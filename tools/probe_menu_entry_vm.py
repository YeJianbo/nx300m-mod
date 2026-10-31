"""Check the passive MENU entry and real ARM panel in a private ARM Xvfb VM.

Only the evdev open/capability transport is replaced by a FIFO. The production
helper event loop, IPC supervisor, launch path and nxui run unchanged. No camera
program, physical keys, real evdev mapping or LCD driver is verified here.
"""
import argparse
import base64
import difflib
import gzip
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

import arm_vm
import probe_menu_startup_vm as vm
from build_stock_app_loader import read_cpio

PROJECT = Path(__file__).resolve().parents[1]

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0 NX_REMOTE_MENU_KEY=1
mkdir -p /perf/state /perf/mod/web_root /perf/photos
mkfifo /perf/menu-input
attempt=0
until /perf/close.arm ping; do attempt=$((attempt+1)); [ "$attempt" -lt 30 ]; sleep 1; done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock_parent=$!
attempt=0
until [ -s /perf/stock-parent.ready ]; do attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1; done
/perf/remote-fixture.arm --listen 127.0.0.1:8781 --web-root /perf/mod/web_root --media-root /perf/photos > /perf/remote.log 2>&1 &
remote=$!
trap 'kill "$remote" "$stock_parent" 2>/dev/null || :' EXIT
info() { wget -T 2 -Y off -q -O - http://127.0.0.1:8781/api/v1/camera/info; }
menu_status() { info | grep -o '"menu_key":{[^}]*}'; }
attempt=0
until menu_status > /perf/entry.json && grep -q '"state":"ready"' /perf/entry.json; do
    attempt=$((attempt+1)); [ "$attempt" -lt 25 ]; sleep 1
done
code=$(sed -n 's/.*"linux_keycode":\([0-9][0-9]*\).*/\1/p' /perf/entry.json)
[ -n "$code" ]
/perf/close.arm raw "$code" 100
sleep 1
menu_status > /perf/short.json
grep -q '"triggers":0' /perf/short.json
/perf/close.arm raw "$code" 1450
attempt=0
until grep -q '^FIRST_FRAME ' /perf/remote.log; do attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1; done
menu_status > /perf/first.json
grep -q '"triggers":1' /perf/first.json
/perf/close.arm /perf/first.ppm
sleep 1
/perf/close.arm raw "$code" 1450 cancel
sleep 1
menu_status > /perf/cancel.json
grep -q '"triggers":1' /perf/cancel.json
/perf/close.arm raw "$code" 1450
attempt=0
until [ "$(grep -c '^FIRST_FRAME ' /perf/remote.log)" -ge 2 ]; do attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1; done
menu_status > /perf/second.json
grep -q '"triggers":2' /perf/second.json
# Status completion enables the existing menu action; then select LUT via Down.
sleep 3
/perf/close.arm lut
attempt=0
until grep -q 'LUT 适配器' /perf/remote.log && [ "$(grep -c '^FIRST_FRAME ' /perf/remote.log)" -ge 3 ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 20 ]; sleep 1
done
/perf/close.arm /perf/lut.ppm
sleep 1
wget -T 2 -Y off -q -O /perf/health.json http://127.0.0.1:8781/api/v1/health
printf '{"short_hold_no_launch":true,"long_hold_launch":true,"cancel_on_other_key":true,"close_and_reopen":true,"lut_handoff":true,"http_alive":true}\n' > /perf/results.json
kill "$remote"
wait "$remote"
kill "$stock_parent"
trap - EXIT
'''

WRITER = r'''
    if(argc>=4 && !strcmp(argv[1],"raw")) {
        struct input_event e={0};int fd=open("/perf/menu-input",O_WRONLY|O_NONBLOCK);
        if(fd<0)return 10;
        e.type=EV_KEY;e.code=atoi(argv[2]);e.value=1;
        if(write(fd,&e,sizeof e)!=sizeof e)return 11;
        if(argc>4) {
            usleep(100000);e.code=KEY_LEFTMETA;e.value=1;write(fd,&e,sizeof e);
            e.value=0;write(fd,&e,sizeof e);e.code=atoi(argv[2]);
        }
        usleep(atoi(argv[3])*1000);e.value=0;
        if(write(fd,&e,sizeof e)!=sizeof e)return 12;
        close(fd);return 0;
    }
'''
LUT_KEY = r'''
    if(argc==2 && !strcmp(argv[1],"lut")) {
        const char *keys[]={"Down","Return"};
        for(int i=0;i<2;i++) {
            XEvent event={0};event.xkey.display=d;event.xkey.window=w;
            event.xkey.keycode=XKeysymToKeycode(d,XStringToKeysym(keys[i]));
            event.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&event);
            event.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&event);
            XSync(d,False);usleep(250000);
        }
        XCloseDisplay(d);return 0;
    }
'''


def prepare(out):
    vm.OUT = out
    vm.PROBE = PROBE
    vm.DRIVER = vm.DRIVER.replace('#include <unistd.h>', '#include <unistd.h>\n#include <fcntl.h>\n#include <linux/input.h>')
    vm.DRIVER = vm.DRIVER.replace('    Display *d=XOpenDisplay(NULL);', WRITER+'\n    Display *d=XOpenDisplay(NULL);')
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', LUT_KEY+'\n    if(argc==2) {')
    vm.prepare()
    inputs = json.loads((out/'inputs.json').read_text())
    inputs.update(scope='passive MENU helper, production ARM remote/UI, FIFO evdev transport',menu_helper_measured=True,
                  real_evdev_capabilities_verified=False,physical_mapping_verified=False)
    inputs.pop('x11_first_client_warmed_before_both_variants',None)
    arm_vm.write_json(out/'inputs.json',inputs)
    source = out/'source/metol/remote'
    shutil.copytree(PROJECT/'mod/metol/remote',source)
    for name in ('mod_paths.h','lut_engine_shared.h'):
        shutil.copyfile(PROJECT/'mod/metol'/name,source.parent/name)
    (out/'runtime').mkdir()
    shutil.copyfile(PROJECT/'runtime/lut_ram_blob.h',out/'runtime/lut_ram_blob.h')
    header = source/'menu_key.h'
    original = header.read_text()
    start, end = original.index('static int menu_key_device('), original.index('static void menu_key_child(')
    replacement = '''/* VM transport only: feed unchanged ARM input_event records. */
static int menu_key_device(const char *path,unsigned code,unsigned char *held,size_t size) {
    (void)code;memset(held,0,size);
    if(strcmp(path,"/dev/input/event0"))return -1;
    return open("/perf/menu-input",O_RDWR|O_NONBLOCK|O_CLOEXEC);
}
'''
    adapted = original[:start]+replacement+original[end:]
    header.write_text(adapted,newline='\n')
    (out/'evdev-transport.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True),adapted.splitlines(True),fromfile='production',tofile='VM-FIFO')))
    builder = (PROJECT/'tools/build_remote.py').read_text()
    start = builder.index('tc=/opt/nx300m-toolchain/usr')
    end = builder.index("\n'''",start)
    command = builder[start:end].replace('{shlex.quote(GUEST)}',shlex.quote('/opt/nx300m-lab/stock-1.15'))
    command = command.replace('mod/metol/remote/',shlex.quote(vm.linux_path(source))+'/')
    command = command.replace('outputs/metol/remote/nx-remote-controller.arm',shlex.quote(vm.linux_path(out/'remote-fixture.arm')))
    command = command.replace('outputs/metol/remote/versions.txt',shlex.quote(vm.linux_path(out/'versions.txt')))
    built = subprocess.run(['wsl.exe','-u','root','-e','sh','-c','set -eu\ncd '+shlex.quote(vm.linux_path(PROJECT))+'\n'+command],capture_output=True)
    (out/'remote-build.log').write_bytes(built.stdout+built.stderr)
    if built.returncode: raise RuntimeError('Fixture build exit '+str(built.returncode))
    entries = read_cpio(gzip.decompress((out/'overlay.cpio.gz').read_bytes()))
    extra = {
        'perf/remote-fixture.arm':(out/'remote-fixture.arm').read_bytes(),
        'perf/mod/lut-panel.sh':(PROJECT/'mod/deployment/mfw/lut-panel.sh').read_bytes(),
        'perf/mod/bin/lut_engine.arm':(PROJECT/'outputs/metol/lut_engine.arm').read_bytes(),
        'perf/mod/looks.txt':(PROJECT/'outputs/deployment/lut-mod-diagnostics/sd/nx300m-mod/looks.txt').read_bytes(),
    }
    for name,data in extra.items():entries[name]=(0o100755 if name.endswith(('.arm','.sh')) else 0o100644,data)
    overlay = io.BytesIO()
    for name,(mode,data) in entries.items():
        if name!='TRAILER!!!':arm_vm.cpio_add(overlay,name,mode,data)
    arm_vm.cpio_add(overlay,'TRAILER!!!',0)
    compressed=gzip.compress(overlay.getvalue(),mtime=0)
    (out/'overlay.cpio.gz').write_bytes(compressed)
    with (arm_vm.WORK/'initramfs.cpio.gz').open('rb') as src,(out/'initramfs.cpio.gz').open('wb') as target:
        shutil.copyfileobj(src,target);target.write(compressed)


def collect(out,name):
    result=vm.call('exec','printf "DATA_START\\n"; /rescue/busybox base64 /perf/'+name+'; printf "DATA_END\\n"')
    match=re.search(r'\r?\nDATA_START\r?\n([A-Za-z0-9+/=\r\n]*)DATA_END',result['output'])
    if not match:raise RuntimeError('Cannot collect '+name)
    (out/name).write_bytes(base64.b64decode(match[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    out=parser.parse_args().output_dir.resolve()
    if out.exists():raise RuntimeError('Preserve earlier evidence; select a new output directory')
    prepare(out)
    started=False
    report={'passed':False,'camera_program_running':False,'physical_evdev_verified':False,'physical_display_driver_verified':False,'evdev_transport':'FIFO fixture only; see evdev-transport.diff','candidate_deployed':False}
    try:
        vm.call('start');started=True
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):break
            time.sleep(1)
        else:raise TimeoutError('VM startup')
        result=vm.call('exec','/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json',result)
        for name in ('remote.log','entry.json','short.json','first.json','cancel.json','second.json','health.json','results.json'):
            collect(out,name)
        if result['exit_code']:raise RuntimeError('Entry exercise exit '+str(result['exit_code']))
        report['scenarios']=json.loads((out/'results.json').read_text())
        report['passed']=all(report['scenarios'].values())
        report['guest_exit_code']=result['exit_code']
    except BaseException as error:
        report['failure']=str(error);raise
    finally:
        if started:
            report['stop']=vm.call('stop')
            if 'return' in report['stop']:(out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
