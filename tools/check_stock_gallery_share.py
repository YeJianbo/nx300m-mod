"""Operate an isolated stock-camera gallery smoke session without the main card.

This helper supplies no gallery callbacks or fake selection state. Use real X11
input and retain screenshots/logs to distinguish a playback failure from a
successful gallery-to-panel handoff. Build production artifacts separately.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import time

from PIL import Image
from run_stock import GUEST, PROJECT, wsl_path

OUTPUT = PROJECT / 'outputs/quickshare/gallery/stock-session'
CARD = '/opt/nx300m-lab/media/nx300m-gallery-share-card.img'
PYTHON = sys.executable

# Keep stock ash and all its applets. The host dispatcher only supplies the
# explicit ARM interpreter that the QEMU-user lab lacks; it changes no applet.
BUSYBOX_RUNNER = r'''
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc,char **argv) {
 const char *name=strrchr(argv[0],'/');name=name?name+1:argv[0];
 char **args=calloc((size_t)argc+10,sizeof(char*));if(!args)return 125;
 args[0]="/qemu-arm-static";args[1]="-cpu";args[2]="cortex-a9";
 args[3]="-L";args[4]="/";args[5]="-0";args[6]="busybox";args[7]="/tmp/stock-busybox.arm";
 int n=8;if(strcmp(name,"busybox"))args[n++]=(char*)name;
 for(int i=1;i<argc;i++)args[n++]=argv[i];
 setenv("NX300M_ARM_RUNNER","/qemu-arm-static",1);
 execv(args[0],args);return 126;
}
'''


def environment():
    return dict(os.environ, NX300M_SESSION_FILE=str(OUTPUT/'session.json'))


def state():
    return json.loads((OUTPUT/'session.json').read_text(encoding='utf-8'))


def namespace(script, timeout=30):
    pid = state()['namespace_pid']
    result = subprocess.run(['wsl.exe', '-u', 'root', '-e', 'nsenter', '-t', str(pid),
                             '--mount', '--net', '--ipc', '--pid', 'sh', '-s'], input=script.encode(),
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'namespace command exit={result.returncode}: '+result.stderr.decode('utf-8','replace'))
    return result.stdout


def start(software_playback=False, generated_photo=None, qemu=None, capture_observer=False):
    if (OUTPUT/'session.json').exists():
        alive=subprocess.run(['wsl.exe','-u','root','-e','test','-d',f"/proc/{state()['namespace_pid']}"],capture_output=True,timeout=10)
        if alive.returncode==0:
            raise RuntimeError('The isolated stock gallery session is already running')
        (OUTPUT/'session.json').unlink()
    args = [PYTHON, str(PROJECT/'tools/run_stock.py'), '--program', 'camera', '--display', '--preferences',
            '--gpio-preview', '--device-preview', '--shutter-preview', '--shutter-park', '10', '--isp-preview',
            '--cpu-ycc-resize', '--spi-mmio', '--sensor-registers', '--d4c-registers', '--capture-boot',
            '--mmap-script-fix', '--ui-mod', '--gallery-share', '--keep-awake', '--no-strace', '--seconds', '0',
            '--output-dir', str(OUTPUT), '--sdcard', '--sdcard-image', CARD, '--tag', 'stock-gallery']
    if software_playback: args += ['--cpu-jpeg-decode', '--cpu-playback']
    if capture_observer: args += ['--capture-observer']
    if generated_photo is not None: args += ['--gallery-photo',str(generated_photo)]
    if qemu is not None: args += ['--qemu',str(qemu)]
    packet = PROJECT/'outputs/firmware-runtime/ipcm-replay-input.json'
    if packet.is_file() and packet.with_suffix('.yuv').is_file():
        args += ['--replay-packet', str(packet), '--replay-continuous']
    subprocess.run(args, env=environment(), check=True, timeout=40)
    deadline = time.monotonic()+20
    while time.monotonic()<deadline:
        if (OUTPUT/'session.json').exists():
            namespace('test -f '+shlex.quote(GUEST+'/var/run/nx300m-sdcard.json'))
            return
        time.sleep(.2)
    raise RuntimeError('Private session metadata was not created')


def deploy(photo):
    bundle = PROJECT/'outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz'
    if not bundle.is_file() or not photo.is_file():
        raise RuntimeError('Production SD archive and a local JPEG are required')
    bridge()
    script = f'''set -eu
rf={shlex.quote(GUEST)}
expected={shlex.quote(CARD)}
python3 - "$rf" "$expected" <<'PY'
import json,pathlib,sys
actual=json.loads((pathlib.Path(sys.argv[1])/'var/run/nx300m-sdcard.json').read_text())
assert actual['image']==sys.argv[2],actual
PY
mkdir -p "$rf/mnt/mmc/DCIM/100PHOTO"
cp {shlex.quote(wsl_path(photo))} "$rf/mnt/mmc/DCIM/100PHOTO/SAM_4262.JPG"
tar --no-same-owner --no-same-permissions -xzf {shlex.quote(wsl_path(bundle))} -C "$rf/mnt/mmc"
cp {shlex.quote(wsl_path(PROJECT/'mod/deployment/quickshare-panel.sh'))} "$rf/mnt/mmc/nx300m-mod/quickshare-panel.sh"
cp {shlex.quote(wsl_path(PROJECT/'outputs/quickshare/gallery/quickshare-panel.arm'))} "$rf/mnt/mmc/nx300m-mod/bin/quickshare-panel.arm"
cp {shlex.quote(wsl_path(PROJECT/'outputs/quickshare/gallery/nx-remote-controller.arm'))} "$rf/mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm"
ip link set lo up
python3 - <<'PY'
import os,subprocess
with open({wsl_path(OUTPUT/'service.stdout.log')!r},'ab') as stdout,open({wsl_path(OUTPUT/'service.stderr.log')!r},'ab') as stderr:
 subprocess.Popen(['chroot','--userspec=65534:65534',{GUEST!r},'/qemu-arm-static','-cpu','cortex-a9','-L','/',
  '/mnt/mmc/nx300m-mod/bin/nx-remote-controller.arm','--listen','127.0.0.1:8781',
  '--web-root','/mnt/mmc/nx300m-mod/web_root','--media-root','/mnt/mmc/DCIM',
  '--quickshare-binary','/mnt/mmc/nx300m-mod/bin/nx-quickshare-armv7','--quickshare-runner','/qemu-arm-static'],
  stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,env=dict(os.environ,DISPLAY=':99'))
PY
'''
    namespace(script)
    (OUTPUT/'deployment.json').write_text(json.dumps({'card':CARD,'source_photo':str(photo),'gallery_file':'/mnt/mmc/DCIM/100PHOTO/SAM_4262.JPG',
        'archive':str(bundle),'service_launch':'explicit QEMU of production ARM service; SD supervisor not exercised here',
        'binfmt_registered':False,'gallery_callback_fixture':False},indent=2)+'\n',encoding='utf-8')


def bridge():
    (OUTPUT/'busybox-runner.c').write_text(BUSYBOX_RUNNER,encoding='utf-8',newline='\n')
    namespace(f'''set -eu
rf={shlex.quote(GUEST)}
[ -f "$rf/tmp/stock-busybox.arm" ] || cp "$rf/bin/busybox" "$rf/tmp/stock-busybox.arm"
gcc -static {shlex.quote(wsl_path(OUTPUT/'busybox-runner.c'))} -o "$rf/tmp/busybox-runner.next"
chmod 755 "$rf/tmp/stock-busybox.arm" "$rf/tmp/busybox-runner.next"
mount --bind "$rf/tmp/busybox-runner.next" "$rf/bin/busybox"
''')


def input_action(action, values):
    return subprocess.run([PYTHON,str(PROJECT/'tools/input_stock.py'),action,*values],env=environment(),capture_output=True,check=True,timeout=15).stdout


def frame(name):
    data=input_action('frame',[])
    width,height,stride=struct.unpack_from('<III',data)
    Image.frombytes('RGB',(width,height),data[12:],'raw','BGRX',stride).transpose(Image.Transpose.ROTATE_90).save(OUTPUT/(name+'.png'))


def collect():
    result=namespace('''python3 - <<'PY'
import json,pathlib
out=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  if (p/'ns/mnt').readlink()!=pathlib.Path('/proc/self/ns/mnt').readlink():continue
  args=(p/'cmdline').read_bytes().split(b'\\0')
  if any(a.rsplit(b'/',1)[-1] in (b'di-camera-app-nx300',b'quickshare-panel.arm',b'nx-remote-controller.arm') for a in args):
   out.append({'pid':int(p.name),'argv':[a.decode(errors='replace') for a in args if a]})
 except OSError:pass
print(json.dumps(out,indent=2))
PY
''')
    (OUTPUT/'processes.json').write_bytes(result)
    metadata=state()
    stderr=(OUTPUT/metadata['stderr_log']).read_text(encoding='utf-8',errors='replace')
    (OUTPUT/'gallery-handoff.log').write_text('\n'.join(line for line in stderr.splitlines() if '[NX QS]' in line)+'\n',encoding='utf-8')
    print(result.decode())


def stop():
    # Namespace PID is PID 1 of this helper's own camera tree. The session path,
    # card metadata and PID namespace identity must all agree before signalling.
    code=r'''
import json,os,pathlib,signal,sys
p=pathlib.Path('/proc')/sys.argv[1]
card=json.loads((p/'root'/sys.argv[2].lstrip('/')/'var/run/nx300m-sdcard.json').read_text())
assert card['image']==sys.argv[3],card
assert (p/'ns/pid').readlink()!=(pathlib.Path('/proc/1/ns/pid')).readlink()
assert (p/'status').read_text().split('NSpid:')[1].splitlines()[0].split()[-1]=='1'
os.kill(int(sys.argv[1]),signal.SIGKILL)
'''
    subprocess.run(['wsl.exe','-u','root','-e','python3','-c',code,str(state()['namespace_pid']),GUEST,CARD],check=True,timeout=10)
    (OUTPUT/'session.json').unlink()


def main():
    global OUTPUT, CARD
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','deploy','bridge','key','click','frame','collect','stop'))
    parser.add_argument('values',nargs='*')
    parser.add_argument('--photo',type=Path,default=PROJECT/'outputs/looks/photo-reference.jpg')
    parser.add_argument('--software-playback',action='store_true',help='Use actual CPU JPEG decode and playback pixel operations')
    parser.add_argument('--capture-observer',action='store_true',help='Enable passive capture observation and Ecore status requests')
    parser.add_argument('--output-dir',type=Path,default=OUTPUT)
    parser.add_argument('--card-image',default=CARD,help='Dedicated lab card, separate from the main viewer card')
    args=parser.parse_args()
    OUTPUT=args.output_dir.resolve();CARD=args.card_image
    OUTPUT.mkdir(parents=True,exist_ok=True)
    if args.action=='start':start(args.software_playback,capture_observer=args.capture_observer)
    elif args.action=='deploy':deploy(args.photo.resolve())
    elif args.action=='bridge':bridge()
    elif args.action=='frame':frame(args.values[0] if args.values else 'screen')
    elif args.action in ('key','click'):print(input_action(args.action,args.values).decode())
    elif args.action=='collect':collect()
    elif args.action=='stop':stop()


if __name__=='__main__':main()
