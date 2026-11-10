"""Check stock mode-key mapping, real XTest events and menu in a private ARM VM."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect

ROOT=Path(__file__).resolve().parents[1]
DRIVER=r'''
#include <X11/Xlib.h>
#include <X11/Xutil.h>
extern int XTestFakeKeyEvent(Display *,unsigned int,Bool,unsigned long);
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char **argv) {
    if(argc<2)return 64;
    Display *d=XOpenDisplay(NULL);if(!d)return 69;
    if(!strcmp(argv[1],"setup")) {
        for(int i=0;i<10;i++){KeySym s=0xffbe + i;XChangeKeyboardMapping(d,67+i,1,&s,1);}
        int codes[]={185,186,113,114,80,88,104,135};
        const char *names[]={"XF86ScrollUp","XF86ScrollDown","Left","Right","KP_Up","KP_Down","KP_Enter","Menu"};
        for(int i=0;i<8;i++){KeySym s=XStringToKeysym(names[i]);XChangeKeyboardMapping(d,codes[i],1,&s,1);}
        XSync(d,False);XCloseDisplay(d);return 0;
    }
    FILE *out=argc==3?fopen(argv[2],"w"):NULL;if(!out)return 65;
    if(!strcmp(argv[1],"dump")) {
        int first,last,columns;XDisplayKeycodes(d,&first,&last);
        KeySym *keys=XGetKeyboardMapping(d,first,last-first+1,&columns);if(!keys)return 70;
        for(int code=first;code<=last;code++){
            fprintf(out,"%d",code);for(int col=0;col<columns;col++)fprintf(out," %lu",keys[(code-first)*columns+col]);fputc('\n',out);
        }XFree(keys);
    } else if(!strcmp(argv[1],"events")) {
        Window w=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,100,100,0,0,0);
        XSelectInput(d,w,KeyPressMask|KeyReleaseMask);XMapRaised(d,w);XSetInputFocus(d,w,RevertToParent,CurrentTime);XSync(d,False);
        int codes[]={67,68,69,70,71,72,73,74,75,76,185,186,113,114,80,88,104,135};
        for(int i=0;i<18;i++) {
            XTestFakeKeyEvent(d,codes[i],True,CurrentTime);XTestFakeKeyEvent(d,codes[i],False,CurrentTime);XSync(d,False);
            while(XPending(d)) {
                XEvent e;XNextEvent(d,&e);
                if(e.type==MappingNotify)XRefreshKeyboardMapping(&e.xmapping);
                if(e.type==KeyPress)fprintf(out,"%u %lu\n",e.xkey.keycode,XLookupKeysym(&e.xkey,0));
            }
        }
        XDestroyWindow(d,w);XSync(d,False);
    } else return 64;
    fclose(out);XCloseDisplay(d);return 0;
}
'''

PROBE=r'''#!/bin/sh
set -eux
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX300M_MOD_SOURCE_ROOT=/perf/mod
attempt=0
until /perf/close.arm ping; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/map.arm setup
/perf/close.arm stock-parent >/perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
helper=/perf/mod/bin/mode_dial_x11.arm
[ "$("$helper" status)" = 0 ]
/perf/map.arm dump /perf/before.map
/perf/map.arm events /perf/before.events
[ "$("$helper" on)" = 1 ]
[ "$("$helper" on)" = 1 ]
[ "$("$helper" status)" = 1 ]
/perf/map.arm dump /perf/locked.map
/perf/map.arm events /perf/locked.events
[ "$("$helper" off)" = 0 ]
[ "$("$helper" off)" = 0 ]
/perf/map.arm dump /perf/restored.map
/perf/map.arm events /perf/restored.events
mkdir -p /perf/mod/.state
NX_STATE_DIR=/tmp/mode-ui /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
 --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
 --font-metadata /perf/mod/fonts/mfw-font.charset >/perf/menu.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/menu.log; do
    kill -0 "$panel";attempt=$((attempt+1));[ "$attempt" -lt 150 ];/rescue/busybox usleep 100000
done
key(){ /perf/close.arm key "$1"; /rescue/busybox usleep 250000; }
for name in KP_Right KP_Right KP_Right KP_Right KP_Enter;do key "$name";done
key KP_Enter
attempt=0
until grep -q 'X11读回 1' /perf/menu.log;do
    kill -0 "$panel";attempt=$((attempt+1));[ "$attempt" -lt 100 ];/rescue/busybox usleep 100000
done
[ "$(cat /perf/mod/.state/fw-mode-lock)" = 1 ]
[ "$("$helper" status)" = 1 ]
key KP_Enter
attempt=0
until grep -q 'X11读回 0' /perf/menu.log;do
    kill -0 "$panel";attempt=$((attempt+1));[ "$attempt" -lt 100 ];/rescue/busybox usleep 100000
done
[ "$(cat /perf/mod/.state/fw-mode-lock)" = 0 ]
[ "$("$helper" status)" = 0 ]
key Menu;key Menu
wait "$panel"
printf '1\n' >/perf/mod/.state/fw-mode-lock
/bin/sh /perf/mod/mode-dial-restore.sh >/perf/restore.log
[ "$("$helper" status)" = 1 ]
printf '0\n' >/perf/mod/.state/fw-mode-lock
/bin/sh /perf/mod/mode-dial-restore.sh >>/perf/restore.log
[ "$("$helper" status)" = 0 ]
/perf/map.arm dump /perf/final.map
echo MODE_DIAL_X11_VM_PASSED
'''

def read_map(path):
    result={}
    for line in path.read_text().splitlines():
        code,*symbols=map(int,line.split())
        while symbols and symbols[-1]==0:symbols.pop()
        result[code]=symbols
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    out=parser.parse_args().output_dir.resolve()
    out.mkdir(parents=True,exist_ok=True)
    if (out/'report.json').exists():raise ValueError('Preserve earlier VM evidence')
    (out/'map.c').write_text(DRIVER,encoding='utf-8')
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -O2 -Wall -Wextra \\
 -I"$tc/include" -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(vm.linux_path(out/'map.c'))} \\
 {shlex.quote(vm.linux_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/usr/lib/libXtst.so.6" "$rf/usr/lib/libX11.so.6" "$rf/lib/libc-2.13.so" -lgcc \\
 -o {shlex.quote(vm.linux_path(out/'map.arm'))}
'''
    built=subprocess.run(['wsl.exe','-d','Debian','-e','sh','-s'],input=script.encode(),capture_output=True)
    (out/'map-build.log').write_bytes(built.stdout+built.stderr)
    if built.returncode:raise RuntimeError(built.stderr.decode('utf-8','replace'))
    vm.OUT=out
    vm.PROBE=PROBE
    vm.DRIVER=vm.DRIVER.replace('XStringToKeysym("Menu")','XStringToKeysym(argc==3?argv[2]:"Menu")')
    vm.EXTRA_FILES={
        'perf/map.arm':(out/'map.arm').read_bytes(),
        'perf/mod/bin/mode_dial_x11.arm':(ROOT/'outputs/controls/mode-dial-x11/mode_dial_x11.arm').read_bytes(),
        'perf/mod/mode-dial-restore.sh':(ROOT/'mod/deployment/mode-dial-restore.sh').read_bytes(),
    }
    vm.prepare()
    report={'passed':False,'physical_camera_verified':False,
            'scope':'Native ARM helper and nxui + ARM Xvfb; real XTest keycodes; no camera or exposure fixture',
            'menu_sha256':hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest(),
            'helper_sha256':hashlib.sha256(vm.EXTRA_FILES['perf/mod/bin/mode_dial_x11.arm']).hexdigest()}
    started=False
    try:
        vm.call('start');started=True
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):break
            time.sleep(.5)
        else:raise TimeoutError('Private VM startup')
        execution=vm.call('exec','/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json',execution)
        for name in ('before.map','locked.map','restored.map','final.map','before.events','locked.events','restored.events','menu.log','restore.log','xvfb.log'):
            collect(out,name)
        if execution['exit_code']:raise RuntimeError('Mapping probe failed: '+execution['output'][-3500:])
        before=read_map(out/'before.map');locked=read_map(out/'locked.map')
        changed=[code for code in before if before[code]!=locked[code]]
        report['changed_keycodes']=changed
        report['only_mode_keys_changed']=changed==list(range(67,77))
        report['locked_mode_symbols_empty']=all(not locked[code] for code in range(67,77))
        report['all_keymap_restored']=before==read_map(out/'restored.map')==read_map(out/'final.map')
        events={name:{int(row.split()[0]):int(row.split()[1]) for row in (out/(name+'.events')).read_text().splitlines()}
                for name in ('before','locked','restored')}
        report['raw_mode_events_blocked']=all(events['locked'].get(code)==0 for code in range(67,77))
        report['other_raw_events_unchanged']=all(events['locked'].get(code)==symbol for code,symbol in events['before'].items() if code not in range(67,77))
        report['raw_events_restored']=events['before']==events['restored']
        log=(out/'menu.log').read_text(encoding='utf-8',errors='replace')
        report['menu_on_off_and_saved']='X11读回 1' in log and 'X11读回 0' in log
        report['source_preferences_restored']='request=on actual=1' in (out/'restore.log').read_text() and 'request=off actual=0' in (out/'restore.log').read_text()
        report['passed']=all(value for key,value in report.items() if key in (
            'only_mode_keys_changed','locked_mode_symbols_empty','all_keymap_restored','raw_mode_events_blocked',
            'other_raw_events_unchanged','raw_events_restored','menu_on_off_and_saved','source_preferences_restored'))
        if not report['passed']:raise RuntimeError('Mapping assertions failed')
    finally:
        if started:report['stop']=vm.call('stop');report['private_vm_stopped']=True
        arm_vm.write_json(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
