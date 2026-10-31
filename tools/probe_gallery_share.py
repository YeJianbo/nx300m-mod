"""Bounded ARM UI/task smoke in a fresh network/mount/PID namespace.

The CLI fixture emits protocol events; it never connects to a receiver. This
checks selected-file handoff and UI completion, not phone interoperability.
"""
from pathlib import Path
import json
import subprocess
import shlex
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/quickshare/gallery'

HELPER=r'''
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static Window find(Display *d) {
 Window root,parent,*kids=NULL,found=0;unsigned n=0;
 XQueryTree(d,DefaultRootWindow(d),&root,&parent,&kids,&n);
 for(unsigned i=0;i<n;i++){char *name=NULL;XFetchName(d,kids[i],&name);
  if(name && !strcmp(name,"NX300M Quick Share")) found=kids[i];if(name)XFree(name);}
 if(kids)XFree(kids);return found;
}
static void shot(Display*d,Window w,const char*path){
 XImage *im=XGetImage(d,w,0,0,440,760,AllPlanes,ZPixmap);if(!im)exit(6);
 FILE*f=fopen(path,"wb");if(!f)exit(7);fprintf(f,"P6\n440 760\n255\n");
 for(int y=0;y<760;y++)for(int x=0;x<440;x++){unsigned long p=XGetPixel(im,x,y);
  fputc((p>>16)&255,f);fputc((p>>8)&255,f);fputc(p&255,f);}fclose(f);XDestroyImage(im);
}
static void key(Display*d,Window w,KeySym symbol){
 XEvent e;memset(&e,0,sizeof e);e.xkey.display=d;e.xkey.window=w;e.xkey.keycode=XKeysymToKeycode(d,symbol);
 e.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&e);XFlush(d);usleep(120000);
 /* Escape closes the window on key-down; do not send to a destroyed XID. */
 if(symbol==0xff1b)return;
 e.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&e);XFlush(d);usleep(120000);
}
int main(int argc,char**argv){
 Display*d=XOpenDisplay(NULL);if(!d)return 2;
 Window parent=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
 XMapWindow(d,parent);XSync(d,False);XSetInputFocus(d,parent,RevertToParent,CurrentTime);XFlush(d);
 FILE*ready=fopen(argv[1],"w");fputs("ready",ready);fclose(ready);
 Window w=0;for(int i=0;i<200&&!w;i++){usleep(100000);w=find(d);}if(!w)return 3;
 sleep(3);
 /* Fifth receiver starts below the viewport: selection must scroll into view. */
 for(int i=0;i<4;i++)key(d,w,0xff54);
 shot(d,w,argv[2]);key(d,w,0xff0d);
 /* The fixture deliberately emits no transfer event for two seconds. */
 usleep(1800000);key(d,w,0xff0d);
 sleep(4);shot(d,w,argv[3]);
 /* Use the real page close handling; fixture parent must regain focus. */
 key(d,w,0xff1b);
 for(int i=0;i<100&&find(d);i++)usleep(100000);
 Window focus;int revert;XGetInputFocus(d,&focus,&revert);
 printf("{\"panel_closed\":%s,\"focus_restored\":%s}\n",find(d)?"false":"true",focus==parent?"true":"false");
 int result=find(d)||focus!=parent;XDestroyWindow(d,parent);XCloseDisplay(d);return result;
}
'''
FIXTURE='''#!/bin/sh
set -eu
printf '%s\\n' "$@" >> "$QS_PROBE/arguments.txt"
if [ "$1" = discover ]; then
 i=0
 while [ "$i" -lt 6 ]; do
  printf '{"event":"discovered","endpoint":{"ip":"127.0.0.1","port":%s,"name":"Receiver%s","present":true}}\\n' "$((9999+i))" "$i"
  i=$((i+1))
 done
 trap 'exit 0' TERM INT
 while :; do sleep 1; done
else
 [ "$1" = send ] && [ "$2" = 127.0.0.1:10003 ] && [ "$3" = Receiver4 ] && [ "$4" = "$QS_PROBE/DCIM/100PHOTO/generated.txt" ] || exit 81
 sleep 2
 printf '%s\\n' '{"event":"transfer","message":{"state":"SendingFiles","meta":{"pin_code":"1234","sent_bytes":30,"total_bytes":30}}}'
 printf '%s\\n' '{"event":"transfer","message":{"state":"AwaitingReceiverAck","meta":{"sent_bytes":30,"total_bytes":30}}}'
 sleep 1
 printf '%s\\n' '{"event":"transfer","message":{"state":"Finished","meta":{"sent_bytes":30,"total_bytes":30,"completion_basis":"safe_disconnect_ack"}}}'
fi
'''

def main():
    OUT.mkdir(exist_ok=True)
    (OUT/'ui-driver.c').write_text(HELPER,encoding='utf-8')
    (OUT/'cli-fixture.sh').write_text(FIXTURE,encoding='utf-8',newline='\n')
    project='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
    folder=project+'/outputs/quickshare/gallery'
    script=f'''set -eu
mount --make-rprivate /
mount -t tmpfs tmpfs /tmp
ip link set lo up
cd {shlex.quote(project)}
out={shlex.quote(folder)}
rf=/opt/nx300m-lab/stock-1.15
probe=$(mktemp -d /tmp/nx-qs.XXXXXX)
export QS_PROBE="$probe" NX_MEDIA_ROOT="$probe/DCIM" DISPLAY=:113 LC_ALL=C
mkdir -p "$probe/DCIM/100PHOTO" "$probe/fonts" "$probe/cache"
printf 'Generated gallery smoke text.\\n' > "$probe/DCIM/100PHOTO/generated.txt"
cp "$rf/usr/share/fonts/SDIC_GP_US_20120720.ttf" "$probe/fonts/"
sed -e "s|@MOD_ROOT@|$probe|g" -e "s|@FONT_CACHE@|$probe/cache|g" mod/deployment/fonts.conf.in > "$probe/fonts.conf"
export FONTCONFIG_FILE="$probe/fonts.conf"
cp "$out/cli-fixture.sh" "$probe/cli";chmod 755 "$probe/cli"
gcc -idirafter /opt/nx300m-toolchain/usr/include "$out/ui-driver.c" /lib/x86_64-linux-gnu/libX11.so.6 -o "$probe/driver"
gcc -std=gnu99 -ffunction-sections -fdata-sections -Wl,--gc-sections \\
 -idirafter /opt/nx300m-toolchain/usr/include -I/opt/nx300m-toolchain/usr/include/freetype2 \\
 -Iwork/reference/metol/test_server/nxui -Iwork/reference/nx-remote-controller/externals/mongoose \\
 tools/quickshare_panel_state_probe.c -lm -o "$probe/state-probe"
"$probe/state-probe" > "$out/state-result.json"
Xvfb :113 -screen 0 480x800x24 -nolisten tcp > "$out/xvfb.log" 2>&1 &
xpid=$!
trap 'kill "$xpid" 2>/dev/null || :' EXIT
sleep 1
"$probe/driver" "$probe/ready" "$out/devices.ppm" "$out/complete.ppm" > "$out/ui-result.json" 2> "$out/ui-driver.stderr.log" &
driver=$!
while [ ! -f "$probe/ready" ]; do sleep 0.1; done
"$rf/qemu-arm-static" -cpu cortex-a9 -L "$rf" "$out/nx-remote-controller.arm" --listen 127.0.0.1:8781 --web-root "$probe" --media-root "$probe/DCIM" --quickshare-binary "$probe/cli" > "$out/service.stdout.log" 2> "$out/service.stderr.log" &
service=$!
sleep 1
"$rf/qemu-arm-static" -cpu cortex-a9 -L "$rf" "$out/quickshare-panel.arm" "$probe/DCIM/100PHOTO/generated.txt" > "$out/panel.stdout.log" 2> "$out/panel.stderr.log" &
panel=$!
result=0
wait "$driver" || result=$?
kill "$panel" "$service" 2>/dev/null || :
wait "$panel" || :
wait "$service" || :
cp "$probe/arguments.txt" "$out/fixture-arguments.txt"
exit "$result"
'''
    result=subprocess.run(['wsl.exe','-u','root','-e','unshare','--mount','--net','--ipc','--pid','--fork','--mount-proc','sh','-s'],input=script.encode(),capture_output=True,timeout=60)
    (OUT/'probe.stdout.log').write_bytes(result.stdout);(OUT/'probe.stderr.log').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError(f'Probe exit {result.returncode}: '+result.stderr.decode('utf-8','replace'))
    for name in ('devices','complete'):
        with Image.open(OUT/(name+'.ppm')) as image:image.transpose(Image.Transpose.ROTATE_90).save(OUT/(name+'.png'))
    args=(OUT/'fixture-arguments.txt').read_text(encoding='utf-8').splitlines()
    assert args.count('send')==1,args
    assert '127.0.0.1:10003' in args and 'Receiver4' in args,args
    state=json.loads((OUT/'state-result.json').read_text())
    assert all(state.values()),state
    ui=json.loads((OUT/'ui-result.json').read_text())
    assert ui['panel_closed'] and ui['focus_restored'],ui
    report={'scope':'ARM native page + real ARM task service, generated file and local fake protocol events',
            'selected_file_forwarded':True,'keyboard_send':True,'single_send_during_initial_phase':True,
            'fifth_receiver_selected':True,'state_regressions':state,**ui,'phone_transfer_tested':False,'stock_gallery_callback_executed':False,
            'main_simulator_restarted':False,'private_network_namespace':True}
    (OUT/'probe-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report))

if __name__=='__main__':main()
