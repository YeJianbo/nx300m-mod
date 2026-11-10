"""End-to-end firmware control wiring on real ARM nxui in a private VM.

A fixture process at /usr/bin/di-camera-app-nx300 satisfies the UI's exact
feature_ready() identity checks (exe path + camera_firmware_mod.so mapping) and
emulates the documented 26-field firmware-mod request/response bridge. The
production panel-actions fw-* actions, camera_control.sh client, preference
records and firmware-mod-restore.sh then run unchanged against it. This proves
UI wiring, request format, read-back display, persistence and boot restore in
software; the real camera module, dial hardware and ISO behaviour stay
unverified here.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]

BRIDGE = r'''
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
/* Fixture: satisfies feature_ready() for both the firmware-mod and exposure
 * modules and emulates the firmware-mod (26 fields) and exposure (15 fields)
 * request/response bridges. */
int main(void) {
    /* The path must contain "/camera_firmware_mod.so": feature_ready greps
     * the maps line for exactly that substring. */
    int fd=open("/perf/camera_firmware_mod.so",O_RDONLY);
    if(fd<0)return 3;
    if(mmap(0,4096,PROT_READ,MAP_PRIVATE,fd,0)==MAP_FAILED)return 4;
    int fd2=open("/perf/exposure_controls.so",O_RDONLY);
    if(fd2>=0&&mmap(0,4096,PROT_READ,MAP_PRIVATE,fd2,0)==MAP_FAILED)return 4;
    FILE *ready=fopen("/var/run/nx300m-firmware-mod-ready.tmp","w");
    if(!ready)return 5;
    fprintf(ready,"%d\n",(int)getpid());
    fclose(ready);
    rename("/var/run/nx300m-firmware-mod-ready.tmp","/var/run/nx300m-firmware-mod-ready");
    ready=fopen("/var/run/nx300m-exposure-ready.tmp","w");
    if(!ready)return 5;
    fprintf(ready,"%d\n",(int)getpid());
    fclose(ready);
    rename("/var/run/nx300m-exposure-ready.tmp","/var/run/nx300m-exposure-ready");
    FILE *log=fopen("/perf/bridge.log","a");
    int locked=0,animation=0,custom=1,iso=0,mode=2;
    unsigned ceiling=0x003b000e;
    unsigned mask=63;
    FILE *out=fopen("/var/run/nx300m-firmware-mod-response.tmp","w");
    if(!out)return 6;
    fprintf(out,"1 0 %d %d 0 0 0 0 0 0 0 0 0 %d %d %d %d %u 0 0 0 0 0 0 0 %d\n",
        (int)getpid(),mode,locked,animation,custom,iso,mask,ceiling);
    fclose(out);
    rename("/var/run/nx300m-firmware-mod-response.tmp","/var/run/nx300m-firmware-mod-response");
    unsigned last=0,last_exposure=0;
    for(;;) {
        usleep(100000);
        FILE *in=fopen("/var/run/nx300m-exposure-request","r");
        if(in){
            unsigned id=0,expires=0,op=0,data_id=0,value=0;
            if(fscanf(in,"%u %u %u %x %x",&id,&expires,&op,&data_id,&value)==5&&id&&id!=last_exposure){
                last_exposure=id;
                if(op==1&&data_id==0x3b&&(value&0xffff0000u)==0x003b0000u)ceiling=value;
                out=fopen("/var/run/nx300m-exposure-response.tmp","w");
                if(out){
                    fprintf(out,"%u 0 %08x %08x 1 1 0 0 0 0 0 0 %08x 1 0\n",
                        id,ceiling,ceiling,ceiling);
                    fclose(out);
                    rename("/var/run/nx300m-exposure-response.tmp","/var/run/nx300m-exposure-response");
                }
                if(log){fprintf(log,"exposure request id=%u op=%u data=%08x value=%08x ceiling=%08x\n",
                    id,op,data_id,value,ceiling);fflush(log);}
            }
            fclose(in);
        }
        in=fopen("/var/run/nx300m-firmware-mod-request","r");
        if(!in)continue;
        unsigned id=0,expires=0,op=0,value=0;
        if(fscanf(in,"%u %u %u %u",&id,&expires,&op,&value)!=4){fclose(in);continue;}
        fclose(in);
        if(!id||id==last)continue;
        last=id;
        int code=0;
        if(op==1){ if(value<1||(value>8&&value!=30))code=-102; else mode=(int)value; }
        else if(op==4){ if(value>1)code=-102; else locked=(int)value; }
        else if(op==5){ if(value>2)code=-102; else animation=(int)value; }
        else if(op==7){ if(value!=0&&value!=6400)code=-102; else iso=(int)value; }
        else code=-101;
        out=fopen("/var/run/nx300m-firmware-mod-response.tmp","w");
        if(!out)continue;
        fprintf(out,"%u %d %d %d 0 0 0 0 0 0 0 0 0 %d %d %d %d %u 0 0 0 0 0 0 0 %d\n",
            id,code,(int)getpid(),mode,locked,animation,custom,iso,mask,ceiling);
        fclose(out);
        rename("/var/run/nx300m-firmware-mod-response.tmp","/var/run/nx300m-firmware-mod-response");
        if(log){fprintf(log,"request id=%u op=%u value=%u code=%d mode=%d locked=%d anim=%d iso=%d\n",
            id,op,value,code,mode,locked,animation,iso);fflush(log);}
    }
}
'''

PROBE = r'''#!/bin/sh
set -eu
set -x
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod
attempt=0
until /perf/close.arm ping; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" "$bridge" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
mkdir -p /perf/mod/.state
cp /perf/fake-module /perf/camera_firmware_mod.so
cp /perf/exposure-module /perf/exposure_controls.so
/usr/bin/di-camera-app-nx300 > /perf/bridge-err.log 2>&1 &
bridge=$!
attempt=0
until [ -f /var/run/nx300m-firmware-mod-ready ]; do
    kill -0 "$bridge"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
export NX300M_MOD_SOURCE_ROOT=/perf/mod
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/mod)
wait_count() { # log expected-FW_MOD-count
    attempt=0
    until [ "$(grep -c '^BACKEND kind=5 ok=1 .*output=FW_MOD' "$1")" -ge "$2" ]; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
    done
}
key() { /perf/close.arm key "$1"; /rescue/busybox usleep 200000; }

# --- Pass 1: settings toggles through the real panel-actions/fw chain.
# Feature pages step one row per Down; a completed fw action rebuilds the page
# with selection back on row 0.
NX_STATE_DIR=/tmp/fw-pass1 /rescue/busybox timeout 90 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/pass1.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/pass1.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
grep -q '^FW_STATE loaded=[1-9][0-9]* current=2 locked=0 animation=0 iso=0$' /perf/pass1.log
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
# Page 5 rows: 0 lock, 1 animation, 2 auto-iso, 3 software mode.
key KP_Enter          # lock on
wait_count /perf/pass1.log 1
key KP_Enter          # lock off
wait_count /perf/pass1.log 2
key KP_Down; key KP_Down
key KP_Enter          # auto-iso on
wait_count /perf/pass1.log 3
key KP_Down; key KP_Down
key KP_Enter          # auto-iso off
wait_count /perf/pass1.log 4
key KP_Down
key KP_Enter          # animation 0 -> 1
wait_count /perf/pass1.log 5
key KP_Down
key KP_Enter          # animation 1 -> 2
wait_count /perf/pass1.log 6
key KP_Down; key KP_Down; key KP_Down
key KP_Enter          # software mode P -> A
wait_count /perf/pass1.log 7
grep -q 'FW_MOD 拨盘锁定已开启 · 原厂读回 P' /perf/pass1.log
grep -q 'FW_MOD Auto ISO 上限已锁定 6400 · 原厂读回 P' /perf/pass1.log
grep -q 'FW_MOD 动画已设为隐藏拨盘 · 原厂读回 P' /perf/pass1.log
grep -q 'FW_MOD 已切到 A 挡 · 原厂读回 A' /perf/pass1.log
[ "$(cat /perf/mod/.state/fw-mode-lock)" = 0 ]
[ "$(cat /perf/mod/.state/fw-animation)" = 2 ]
[ "$(cat /perf/mod/.state/fw-auto-iso-lock)" = 0 ]
key Menu
/rescue/busybox usleep 400000
key Menu
wait "$panel"

# --- Pass 2: state comes back from the persisted response after restart.
NX_STATE_DIR=/tmp/fw-pass2 /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/pass2.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/pass2.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
grep -q '^FW_STATE loaded=[1-9][0-9]* current=3 locked=0 animation=2 iso=0$' /perf/pass2.log
key Menu
wait "$panel"

# --- Pass 3: boot restore re-applies non-default preferences once.
printf '1\n' > /perf/mod/.state/fw-mode-lock
printf '1\n' > /perf/mod/.state/fw-animation
printf '6400\n' > /perf/mod/.state/fw-auto-iso-lock
rm -f /var/run/nx300m-firmware-mod-response
echo MARK_RESTORE >> /perf/bridge.log
NX300M_MOD_ROOT=/perf/mod /bin/sh /perf/mod/firmware-mod-restore.sh
attempt=0
until [ "$(grep -c '^request ' /perf/bridge.log)" -ge 3 ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
grep -q 'op=4 value=1 code=0' /perf/bridge.log
grep -q 'op=5 value=1 code=0' /perf/bridge.log
grep -q 'op=7 value=6400 code=0' /perf/bridge.log
grep -q 'restored fw-mode-lock=1' /perf/mod/.state/fw-restore.log
grep -q 'restored fw-animation=1' /perf/mod/.state/fw-restore.log
grep -q 'restored fw-auto-iso-lock=6400' /perf/mod/.state/fw-restore.log
[ "$(awk '{print $14}' /var/run/nx300m-firmware-mod-response)" = 1 ]
[ "$(awk '{print $15}' /var/run/nx300m-firmware-mod-response)" = 1 ]
[ "$(awk '{print $17}' /var/run/nx300m-firmware-mod-response)" = 6400 ]

# --- Pass 4: explicit zero preferences are replayed, including animation 0.
printf '0\n' > /perf/mod/.state/fw-mode-lock
printf '0\n' > /perf/mod/.state/fw-animation
printf '0\n' > /perf/mod/.state/fw-auto-iso-lock
rm -f /var/run/nx300m-firmware-mod-response
before=$(grep -c '^request ' /perf/bridge.log)
echo MARK_DEFAULT >> /perf/bridge.log
NX300M_MOD_ROOT=/perf/mod /bin/sh /perf/mod/firmware-mod-restore.sh
after=$(grep -c '^request ' /perf/bridge.log)
[ "$after" -eq "$((before+3))" ]
[ "$(awk '{print $15}' /var/run/nx300m-firmware-mod-response)" = 0 ]
grep -q 'restored fw-animation=0' /perf/mod/.state/fw-restore.log
# Missing preferences still send nothing.
for name in fw-mode-lock fw-animation fw-auto-iso-lock; do rm /perf/mod/.state/$name; done
before=$(grep -c '^request ' /perf/bridge.log)
NX300M_MOD_ROOT=/perf/mod /bin/sh /perf/mod/firmware-mod-restore.sh
[ "$(grep -c '^request ' /perf/bridge.log)" -eq "$before" ]

# --- Pass 5: applied-but-unsaved is not toggled back on explicit retry.
NX_STATE_DIR=/tmp/fw-pass5 /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/pass5.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/pass5.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
for key_name in KP_Right KP_Right KP_Right KP_Right KP_Enter; do key "$key_name"; done
mv /perf/mod/.state /perf/mod/.state-saved
printf blocked > /perf/mod/.state
key KP_Enter
attempt=0
until grep -q '已应用、未保存' /perf/pass5.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
grep -q '^FW_STATE loaded=.*locked=1 ' /perf/pass5.log
rm /perf/mod/.state
mv /perf/mod/.state-saved /perf/mod/.state
key KP_Enter
wait_count /perf/pass5.log 1
[ "$(cat /perf/mod/.state/fw-mode-lock)" = 1 ]
[ "$(awk '{print $14}' /var/run/nx300m-firmware-mod-response)" = 1 ]
key Menu
key Menu
wait "$panel"

# --- Pass 6: Auto ISO ceiling cycle through the exposure bridge.
rm -f /perf/mod/.state/fw-auto-iso-max
NX_STATE_DIR=/tmp/fw-pass6 /rescue/busybox timeout 60 /perf/mod/bin/nxui.arm --mfw-services \
    --root /perf/mod --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/pass6.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/pass6.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
# Page 0: lut(0), lenses(1), exposure(2); page 4 row 3 = extension ceiling.
key KP_Right
key KP_Right
key KP_Enter
/rescue/busybox usleep 300000
for ceiling in 12800 25600 3200 6400; do
    key KP_Down; key KP_Down; key KP_Down
    key KP_Enter
    case $ceiling in 12800) completed=1 ;; 25600) completed=2 ;; 3200) completed=3 ;; 6400) completed=4 ;; esac
    wait_count /perf/pass6.log "$completed"
    [ "$(cat /perf/mod/.state/fw-auto-iso-max)" = "$ceiling" ]
done
grep -q '原厂读回 003b0010' /perf/pass6.log
grep -q '原厂读回 003b0011' /perf/pass6.log
grep -q '原厂读回 003b000e' /perf/pass6.log
grep -q 'exposure request .*data=0000003b value=003b0011' /perf/bridge.log
key Menu; key Menu
wait "$panel"
# --- Saved extensions and explicit stock restore all replay through exposure.
for ceiling in 12800 25600 3200 6400; do
printf '%s\n' "$ceiling" > /perf/mod/.state/fw-auto-iso-max
echo MARK_ISO_RESTORE >> /perf/bridge.log
NX300M_MOD_ROOT=/perf/mod /bin/sh /perf/mod/firmware-mod-restore.sh
grep -q "restored fw-auto-iso-max=$ceiling" /perf/mod/.state/fw-restore.log
done
rm -f /perf/mod/.state/fw-auto-iso-max

# Both production persistence branches must leave abnormal targets untouched.
# The ceiling cycle above intentionally ran without firmware-mod. Restore its
# finite fixture owner only for the unrelated mode-lock persistence branch.
cp /var/run/nx300m-exposure-ready /var/run/nx300m-firmware-mod-ready
for pair in fw-mode-lock-on:fw-mode-lock fw-auto-iso-max-12800:fw-auto-iso-max; do
    action=${pair%:*}; pref=${pair#*:}
    for kind in linked dangling directory; do
        data=/perf/refuse-$pref-$kind
        mkdir -p "$data/.state"
        printf 'keep\n' > "$data/victim"
        case $kind in
            linked) ln -s "$data/victim" "$data/.state/$pref" ;;
            dangling) ln -s "$data/absent" "$data/.state/$pref" ;;
            directory) mkdir "$data/.state/$pref" ;;
        esac
        set +e
        NX300M_MOD_SOURCE_ROOT="$data" /bin/sh /perf/mod/mfw-panel-actions.sh "$action" > /perf/refuse.log 2>&1
        rc=$?
        set -e
        printf 'TARGET_RESULT %s %s rc=%s\n' "$pref" "$kind" "$rc"
        [ "$rc" = 75 ]
        [ "$(cat "$data/victim")" = keep ]
        case $kind in directory) [ -d "$data/.state/$pref" ] ;; *) [ -L "$data/.state/$pref" ] ;; esac
        printf 'REFUSED %s %s rc=%s preserved=1\n' "$pref" "$kind" "$rc" >> /perf/target-checks.log
    done
done

kill "$bridge"
kill "$stock"
trap - EXIT
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--auto-iso-only', action='store_true', help='Run only new ceiling cycle/restore and both target checks')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    vm.OUT = out
    vm.PROBE = (PROBE.split('# --- Pass 1:',1)[0]+'# --- Pass 6:'+PROBE.split('# --- Pass 6:',1)[1]) if args.auto_iso_only else PROBE
    if args.auto_iso_only:
        vm.PROBE=vm.PROBE.replace('set -x\n','')
        vm.PROBE=vm.PROBE.replace('# --- Pass 6:',
            'rm -f /var/run/nx300m-firmware-mod-ready\n# --- Pass 6:',1)
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {
        'perf/mod/mfw-panel-actions.sh': (ROOT/'mod/deployment/mfw/panel-actions.sh').read_bytes(),
        'perf/mod/camera_control.sh': (ROOT/'mod/deployment/camera_control.sh').read_bytes(),
        'perf/mod/firmware-mod-restore.sh': (ROOT/'mod/deployment/firmware-mod-restore.sh').read_bytes(),
        'perf/mod/common.sh': (ROOT/'mod/deployment/common.sh').read_bytes(),
        'perf/mod/gui_mfw.NX300M': (ROOT/'mod/deployment/mfw/gui_mfw.NX300M').read_bytes(),
        'perf/mod/fonts/mfw-font.charset': (ROOT/'outputs/metol/mfw-font.charset').read_bytes(),
        'perf/mod/fonts/fonts.conf.in': (ROOT/'mod/deployment/mfw/fonts.conf.in').read_bytes(),
        'perf/fake-module': b'NX300M fixture camera_firmware_mod.so payload\n',
        'perf/exposure-module': b'NX300M fixture exposure_controls.so payload\n',
    }
    vm.prepare()
    # Build the fixture bridge separately and inject it under the exact exe
    # path feature_ready() demands.
    (out/'bridge.c').write_text(BRIDGE, encoding='utf-8')
    shell = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -O2 -Wall \\
 -I"$tc/include" -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 {vm.linux_path(out/'bridge.c')} \\
 {vm.linux_path(ROOT)}/work/reference/metol/test_server/filmlab/src/crt0_arm.S \\
 "$rf/lib/libc-2.13.so" -lgcc -o {vm.linux_path(out/'bridge.arm')}
'''
    built = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', shell],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    (out/'bridge-build.log').write_text(built.stdout+built.stderr, encoding='utf-8')
    if built.returncode:
        raise RuntimeError('Fixture bridge build exit '+str(built.returncode))
    # Add guest files the base prepare() did not know about, then rebuild the
    # assembled image exactly like vm.prepare: base initramfs + appended overlay.
    import gzip
    import io
    import shutil
    from build_stock_app_loader import read_cpio
    entries = read_cpio(gzip.decompress((out/'overlay.cpio.gz').read_bytes()))
    entries['usr/bin/di-camera-app-nx300'] = (0o100755, (out/'bridge.arm').read_bytes())
    overlay = io.BytesIO()
    for name, (mode, data) in entries.items():
        if name != 'TRAILER!!!':
            arm_vm.cpio_add(overlay, name, mode, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    (out/'overlay.cpio.gz').write_bytes(gzip.compress(overlay.getvalue(), mtime=0))
    with (out/'initramfs.cpio.gz').open('wb') as target:
        shutil.copyfileobj((arm_vm.WORK/'initramfs.cpio.gz').open('rb'), target)
        target.write(gzip.compress(overlay.getvalue(), mtime=0))
    report = dict(passed=False, hardware_verified=False,
                  scope='Real ARM nxui settings/exposure pages vs fixture firmware-mod bridge; panel-actions + camera_control + restore unchanged',
                  candidate_sha256=hashlib.sha256((ROOT/'outputs/metol/nxui.arm').read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            if (out/'serial.log').exists() and 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Firmware controls VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        for name in ('pass1.log', 'pass2.log', 'pass5.log', 'pass6.log', 'bridge.log', 'bridge-err.log'):
            try:
                collect(out, name)
            except RuntimeError:
                pass
        if execution['exit_code']:
            raise RuntimeError('Firmware controls probe failed: '+execution['output'][-4000:])
        pass1 = (out/'pass1.log').read_text(errors='replace') if (out/'pass1.log').exists() else ''
        pass2 = (out/'pass2.log').read_text(errors='replace') if (out/'pass2.log').exists() else ''
        bridge = (out/'bridge.log').read_text(errors='replace')
        pass6 = (out/'pass6.log').read_text(errors='replace') if (out/'pass6.log').exists() else ''
        if args.auto_iso_only:
            collect(out,'target-checks.log')
            restore=vm.call('exec','cat /perf/mod/.state/fw-restore.log')['output']
            report.update(scope='Actual ARM UI + stock shell/control client; fixture exposure bridge, no real ISO/hardware',
                ui_auto_iso_cycle=all('原厂读回 '+raw in pass6 for raw in ('003b0010','003b0011','003b000e','003b000f')),
                restore_all_four=all('restored fw-auto-iso-max='+str(n) in restore for n in (12800,25600,3200,6400)),
                abnormal_targets_preserved=(out/'target-checks.log').read_text().count('rc=75 preserved=1')==6)
            report['passed']=report['ui_auto_iso_cycle'] and report['restore_all_four'] and report['abnormal_targets_preserved']
            return
        report.update(
            ui_lock_on_off='FW_MOD 拨盘锁定已开启 · 原厂读回 P' in pass1 and 'FW_MOD 拨盘锁定已关闭' in pass1,
            ui_auto_iso_on_off='FW_MOD Auto ISO 上限已锁定 6400' in pass1 and 'FW_MOD Auto ISO 上限锁已解除' in pass1,
            ui_animation_cycle='FW_MOD 动画已设为瞬切' in pass1 and 'FW_MOD 动画已设为隐藏拨盘' in pass1,
            ui_software_mode_readback='FW_MOD 已切到 A 挡 · 原厂读回 A' in pass1,
            request_op4_seen='op=4 value=1 code=0' in bridge and 'op=4 value=0 code=0' in bridge,
            request_op5_op7_seen='op=5 value=1 code=0' in bridge and 'op=5 value=2 code=0' in bridge and 'op=7 value=6400 code=0' in bridge,
            request_mode_readback='op=1 value=3 code=0 mode=3' in bridge,
            ui_auto_iso_max_25600_readback='FW_MOD Auto ISO 上限已设为 25600 · 原厂读回 003b0011' in pass6,
            request_auto_iso_25600_seen='exposure request' in bridge and 'value=003b0011' in bridge,
        )
        # Preference files were asserted inside the guest; their final values
        # are re-checked here from collected state for the report.
        state = vm.call('exec', 'cat /perf/mod/.state/fw-mode-lock')
        lines = [line for line in state['output'].splitlines() if line.strip() and not line.startswith(('1 ', '__ARM'))]
        values = [line.strip() for line in lines[-1:]]
        report['final_prefs_after_restore_defaults'] = values == ['1']
        report['ui_state_restored_after_restart'] = re.search(
            r'FW_STATE loaded=[1-9][0-9]* current=3 locked=0 animation=2 iso=0', pass2) is not None
        report['restore_reapplies_non_defaults'] = ('restored fw-mode-lock=1' in vm.call(
            'exec', 'cat /perf/mod/.state/fw-restore.log')['output'])
        report['ui_applied_unsaved_retry'] = '已应用、未保存' in (out/'pass5.log').read_text(errors='replace')
        report['restore_explicit_zero_animation'] = 'restored fw-animation=0' in vm.call('exec', 'cat /perf/mod/.state/fw-restore.log')['output']
        report['passed'] = all(value is True for key, value in report.items()
                               if key.startswith(('ui_', 'request_', 'final_', 'restore_')))
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
