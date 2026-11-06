"""Reproduce early-menu/boot completion in a private ARM kernel + Xvfb VM.

Production boot, LUT panel and action scripts; engine and hardware loader are
explicit command doubles. No physical camera/card or shared WSL mounts.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from wsl_paths import wsl_path

ROOT=Path(__file__).resolve().parents[1]
SOCKET='/tmp/nx300m-lut-cold-boot-private'

ENGINE='''#!/bin/sh
set -eu
case $1 in
 boot)
  touch /perf/engine-started
  while [ ! -f /perf/release-boot ]; do sleep 1; done
  [ ! -e /perf/fail-boot ] || exit 17
  printf '7 /perf/looks/nx300-fuji-test/table.d4lut 100\\n' > /var/run/nx300m-lut-selection
  printf '/perf\\n' > /var/run/nx300m-lut-selection-session
  ;;
 *) exit 64 ;;
esac
'''
PREVIEW='''#!/bin/sh
set -eu
case $1 in capabilities) echo continuous-s2; exit 0;; esac
echo "$1" >> /perf/commands.log
case $1 in
 enable-s2) echo smartfilter > /var/run/nx300m-preview-backend;;
 stop|restore) rm -f /var/run/nx300m-preview-backend;;
 *) exit 64;;
esac
'''
PROBE=r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX_SF_PROBE_FIXTURE=1 NX300M_MOD_SOURCE_ROOT=/perf
export NX300M_MFW_RAM_BASE=/tmp/cold-boot NX_STATE_DIR=/tmp/cold-boot/state
export NX300M_MOD_ROOT=/tmp/cold-boot/runtime NX_MOD_ROOT=/tmp/cold-boot/runtime
mkdir -p "$NX_STATE_DIR" "$NX300M_MOD_ROOT/bin" /mnt/mmc/bin /perf/.state /perf/logs
cp /perf/lut-boot.sh /perf/lut-panel.sh /perf/mod/common.sh "$NX300M_MOD_ROOT/"
cp /perf/mod/mfw-panel-actions.sh /perf/smartfilter-preview.sh "$NX300M_MOD_ROOT/"
cp /perf/mod/bin/nxui.arm "$NX300M_MOD_ROOT/bin/"
cp /perf/lut_engine.arm "$NX300M_MOD_ROOT/bin/"
mkdir -p "$NX300M_MOD_ROOT/fonts"
cp /perf/mod/fonts/mfw-font.charset "$NX300M_MOD_ROOT/fonts/"
chmod 500 "$NX300M_MOD_ROOT/"*.sh "$NX300M_MOD_ROOT/bin/"*.arm
printf 'nx300-fuji-test|Fuji|0|0|0|0|0|0|0
' > /perf/looks.txt
printf '1 /perf/looks/nx300-fuji-test/table.d4lut 100
' > /perf/.state/lut-choice
printf '1
' > /perf/.state/lut-preview
: > /perf/logs/fn-key.log
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf)
cp /perf/close.arm /mnt/mmc/bin/smartfilter_fixture.arm
/rescue/busybox dd if=/dev/zero of=/perf/libiq-fw-slpcam.so bs=4096 count=1
cp /perf/libiq-fw-slpcam.so /perf/libudd.so.3.1
/mnt/mmc/bin/smartfilter_fixture.arm camera > /perf/camera.log 2>&1 &
camera=$!
trap 'kill "$camera" ${stock:-} ${panel:-} ${boot:-} 2>/dev/null || :' EXIT
wait_file(){ n=0; until [ -e "$1" ]; do n=$((n+1)); [ "$n" -lt 80 ]; /rescue/busybox usleep 100000; done; }
wait_log(){ n=0; until grep -q "$2" "$1"; do n=$((n+1)); [ "$n" -lt 120 ] || { cat "$1"; exit 1; }; /rescue/busybox usleep 100000; done; }
wait_log /perf/camera.log '^CAMERA_READY'
n=0; until /perf/close.arm ping; do n=$((n+1)); [ "$n" -lt 60 ]; sleep 1; done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
wait_file /perf/stock-parent.ready
/bin/sh "$NX300M_MOD_ROOT/lut-boot.sh" > /perf/boot.log 2>&1 &
boot=$!
wait_file /perf/engine-started
grep -q '^restoring$' "$NX_STATE_DIR/lut-boot.status"
# Real lut-panel.sh replaces NX_STATE_DIR with a private panel directory.
# The observer must still follow the staging base's service state/boot lock.
/bin/sh "$NX300M_MOD_ROOT/lut-panel.sh" > /perf/panel.log 2>&1 &
panel=$!
wait_log /perf/panel.log '^READY '
wait_log /perf/panel.log '^LUT_PREVIEW_STATE state=3 '
# Both physical-key activation and direct shell entry reject a running boot.
/perf/close.arm key Up Up Return
/rescue/busybox usleep 200000
[ ! -e /perf/commands.log ]
rc=0
/bin/sh "$NX300M_MOD_ROOT/mfw-panel-actions.sh" preview-stop > /perf/busy-action.log 2>&1 || rc=$?
[ "$rc" -ne 0 ]
grep -q '^1$' /perf/.state/lut-preview
touch /perf/release-boot
wait "$boot"; boot=
grep -q '^ready$' "$NX_STATE_DIR/lut-boot.status"
# No key/touch/expose is sent between releasing boot and this check.
wait_log /perf/panel.log '^LUT_PREVIEW_STATE state=1 '
/perf/close.arm key Menu
wait "$panel"; panel=
grep -q '^enable-s2$' /perf/commands.log
! grep -q '^stop$' /perf/commands.log
/bin/sh "$NX300M_MOD_ROOT/mfw-panel-actions.sh" preview-stop > /perf/stop.log 2>&1
[ ! -e /var/run/nx300m-preview-backend ]
touch /perf/fail-boot
rc=0
/bin/sh "$NX300M_MOD_ROOT/lut-boot.sh" > /perf/failed-boot.log 2>&1 || rc=$?
[ "$rc" -eq 17 ]
grep -q '^failed$' "$NX_STATE_DIR/lut-boot.status"
grep -q '^1$' /perf/.state/lut-preview
/bin/sh "$NX300M_MOD_ROOT/lut-panel.sh" > /perf/failed-panel.log 2>&1 &
panel=$!
wait_log /perf/failed-panel.log '^READY '
wait_log /perf/failed-panel.log '^LUT_PREVIEW_STATE state=4 '
/perf/close.arm key Up Up Return
wait_log /perf/failed-panel.log '^BACKEND kind=5 ok=1 '
/perf/close.arm key Menu
wait "$panel"; panel=
grep -q '^smartfilter$' /var/run/nx300m-preview-backend
grep -q 'LUT_BOOT restored preview=1' /perf/logs/fn-key.log
grep -q 'LUT_BOOT failed rc=17; preference retained' /perf/logs/fn-key.log
printf 'COLD_BOOT_UI_PASSED
'
'''


def configure(out):
    vm.OUT=out
    vm.SOCKET=SOCKET


def call(out,action,command=''):
    p=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',wsl_path(Path(__file__)),
                      '--linux','--output-dir',wsl_path(out),action,base64.b64encode(command.encode()).decode()],
                     capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=135)
    if p.returncode: raise RuntimeError(p.stderr or p.stdout)
    return json.loads(p.stdout)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--linux',action='store_true')
    parser.add_argument('action',nargs='?',default='run')
    parser.add_argument('payload',nargs='?',default='')
    args=parser.parse_args();out=args.output_dir.resolve();configure(out)
    if args.linux:
        print(json.dumps(vm.linux_worker(args.action,args.payload)));return
    if (out/'report.json').exists():raise RuntimeError('Preserve the previous result')
    vm.DRIVER=vm.DRIVER.replace('#include <unistd.h>','#include <unistd.h>\n#include <fcntl.h>\n#include <sys/mman.h>')
    vm.DRIVER=vm.DRIVER.replace('    Display *d=XOpenDisplay(NULL);',r'''
    if(argc==2&&!strcmp(argv[1],"camera")){
        const char *names[]={"/perf/libiq-fw-slpcam.so","/perf/libudd.so.3.1"};
        for(int i=0;i<2;i++){int fd=open(names[i],O_RDONLY);if(fd<0||mmap(0,4096,PROT_READ,MAP_PRIVATE,fd,0)==MAP_FAILED)return 30;close(fd);}
        puts("CAMERA_READY");fflush(stdout);for(;;)pause();
    }
    Display *d=XOpenDisplay(NULL);''')
    vm.DRIVER=vm.DRIVER.replace('    if(argc==2) {',r'''
    if(argc>=3&&!strcmp(argv[1],"key")){
        for(int i=2;i<argc;i++){XEvent e={0};e.xkey.display=d;e.xkey.window=w;
            e.xkey.keycode=XKeysymToKeycode(d,XStringToKeysym(argv[i]));if(!e.xkey.keycode)return 22;
            e.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&e);
            e.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&e);}
        XSync(d,False);XCloseDisplay(d);return 0;
    }
    if(argc==2) {''')
    vm.PROBE=PROBE
    sources={
        'perf/lut-boot.sh':'mod/deployment/mfw/lut-boot.sh',
        'perf/lut-panel.sh':'mod/deployment/mfw/lut-panel.sh',
        'perf/mod/mfw-panel-actions.sh':'mod/deployment/mfw/panel-actions.sh',
    }
    vm.EXTRA_FILES={name:(ROOT/path).read_bytes() for name,path in sources.items()}
    vm.EXTRA_FILES.update({'perf/lut_engine.arm':ENGINE.encode(),'perf/smartfilter-preview.sh':PREVIEW.encode()})
    vm.prepare()
    report=dict(passed=False,scope='Production ARM nxui/boot/panel/action scripts; engine and hardware command doubles',
                camera_hardware_verified=False,physical_camera_or_card_accessed=False,
                sources={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in
                         [*sources.values(),'mod/metol/nxui.c','mod/metol/nxui_preview_state.h','mod/metol/nxui_lut_panel.h']})
    started=False
    try:
        call(out,'start');started=True
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):break
            time.sleep(.5)
        else:raise TimeoutError('Private ARM VM boot')
        execution=call(out,'exec','/rescue/busybox timeout 85 /bin/sh -x /perf/probe.sh > /perf/probe.stdout 2> /perf/probe.stderr')
        arm_vm.write_json(out/'execution.json',execution)
        vm.call=lambda action,command='':call(out,action,command)
        for name in ['probe.stdout','probe.stderr','panel.log','failed-panel.log','boot.log','failed-boot.log','busy-action.log','commands.log','logs/fn-key.log']:
            (out/name).parent.mkdir(parents=True,exist_ok=True)
            try:collect(out,name)
            except Exception as exc:report.setdefault('collection_errors',{})[name]=str(exc)
        if execution['exit_code'] or 'COLD_BOOT_UI_PASSED' not in (out/'probe.stdout').read_text():
            raise RuntimeError('Cold boot UI probe failed; inspect probe.stderr')
        report.update(passed=True,early_menu_observes_service_lock=True,
                      completion_repaints_without_input=True,busy_key_and_shell_do_not_disable=True,
                      failed_boot_retains_preference=True,failed_boot_visible_and_explicit_retry=True,
                      finite_control_logs_survive_on_source=True)
    except Exception as exc:
        report['error']=str(exc);raise
    finally:
        if started:report['private_vm_stop']=call(out,'stop')
        arm_vm.write_json(out/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
