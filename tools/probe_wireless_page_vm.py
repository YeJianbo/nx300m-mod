"""CLOSE-MENU wireless page + BOOT-KEY dynamic regression in the private ARM VM.

Reuses the proven probe_playback_custom_vm.py framework: production evdev/panel
transport, production nxui (menu), real production hotspot.sh driven through
mfw-panel-actions.sh, ARM multicall stub daemons (real processes, real pid
files), stub ip/insmod on PATH and a fake wlan0 sysfs — the 2026-11-12 shell
isolation pattern, inside the ARM VM.

Covers:
  - page 7 entry runs the bounded async hotspot-status query and shows the
    state the script wrote (on/off/failed:<stage>);
  - toggle stop -> start through the real menu rows, page rebuild after each
    completion, focus clamped;
  - missing-daemon failure shows failed:<stage> and the start row stays
    retryable; recovery returns to on;
  - password entry: HOTSPOT_CREDENTIALS reached, BACKEND log masks password=***
    and the real secret never appears in the log;
  - keyboard + dial (XF86Scroll stand-ins) + touch (log-evidence accepted)
    including an injection during an in-flight slow start; MENU exit and stock
    focus restoration;
  - BOOT-KEY: menu window deliberately late — boot-not-ready retries stay at
    retry_ms=1000, the shortcut becomes ready shortly after the window appears;
    sustained fast retries while the window never appears (bounded observation).
"""
import json
import shlex
import subprocess
import time
from pathlib import Path

import arm_vm
import probe_menu_startup_vm as vm
from probe_playback_custom_vm import build_remote
from probe_lut_selection_vm import TAP_DRIVER
from probe_menu_entry_vm import collect
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]
import os as _os
OUT = ROOT / ('outputs/review/2026-11-13-performance-25hz/menu-vm/' +
              (_os.environ.get('NX_WIRELESS_PROBE_DIR') or 'wireless-page'))

CAMERA_SETUP = r'''
        void *code=mmap((void *)0x80000,0x150000,PROT_READ|PROT_WRITE,
                       MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0);
        void *data=mmap((void *)0x4c0000,0x30000,PROT_READ|PROT_WRITE,
                       MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED,-1,0);
        if(code==MAP_FAILED || data==MAP_FAILED)return 30;
        for(unsigned i=0;i<sizeof custom_key_stock/sizeof custom_key_stock[0];i++)
            memcpy((void *)(uintptr_t)custom_key_stock[i].address,custom_key_stock[i].bytes,custom_key_stock[i].size);
        *(volatile unsigned *)0x4c9c5c=0x24c0b8;
        *(volatile unsigned *)0x4c9f04=0x24c838;
        *(volatile unsigned *)0x4c9c64=0x4c9f04;
        *(volatile unsigned *)0x4de4d8=0;
'''
CAMERA_LOOP = r'''
        for(;;) {
            FILE *mode=fopen("/perf/camera-mode","r");int c=mode?fgetc(mode):'V';if(mode)fclose(mode);
            *(volatile unsigned *)0x4c9c64=c=='P'?0x4ca010:0x4c9f04;
            *(volatile unsigned *)0x4de4d8=c=='L'?0x200:0;
            *(volatile unsigned char *)custom_key_stock[0].address=c=='B'?0:custom_key_stock[0].bytes[0];
            FILE *ack=fopen("/perf/camera-mode-ready","w");if(!ack)return 31;fputc(c,ack);fclose(ack);
            while(XPending(d)){XEvent e;XNextEvent(d,&e);}
            usleep(20000);
        }
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97
now_ms() { awk '{printf "%d", $1*1000}' /proc/uptime; }
attempt=0
until [ -S /tmp/.X11-unix/X97 ]; do attempt=$((attempt+1)); [ "$attempt" -lt 60 ]; sleep 1; done
/perf/close.arm ping

# --- wireless runtime isolation (2026-11-12 shell-suite pattern) ---
mount -t tmpfs none /var/run
mount -t tmpfs none /lib/modules
: > /lib/modules/cfg80211.ko; : > /lib/modules/ath6kl_sdio.ko
mount -t tmpfs none /sys/class/net
mkdir /sys/class/net/wlan0
mkdir -p /perf/stub-state /perf/state /perf/mod/.state
echo 192.168.0.1 > /perf/stub-state/wlan0.addr
export PATH=/perf/stubs:$PATH
export NX_HOTSPOT_HOSTAPD=/perf/stubs/hostapd
export NX_HOTSPOT_UDHCPD=/perf/stubs/udhcpd
export STUB_STATE=/perf/stub-state STUB_LOG=/perf/stub-calls.log
: > /perf/stub-calls.log
export NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
chmod 755 /perf/stubs/hostapd /perf/stubs/udhcpd /perf/stubs/insmod /perf/stubs/ip
# hotspot.sh hardens PATH=/bin:/usr/bin:/sbin:/usr/sbin. The VM rootfs has
# a REAL /bin directory whose applet symlinks point at busybox, e.g.
# /bin/ip -> busybox, while /usr/bin/ip is a separate file. Replacing only
# /usr/bin/ip left /bin/ip (first in PATH) as the real busybox applet, which
# failed against the fake /sys/class/net/wlan0 and exited 1 without reaching
# the stub. Replace every PATH candidate with a plain regular-file copy so
# no busybox symlink can shadow the double. Fixture-only; production PATH
# handling is untouched.
for candidate in /bin/ip /usr/bin/ip /sbin/ip /usr/sbin/ip; do
    [ -d "$(dirname "$candidate")" ] || continue
    rm -f "$candidate" && cp /perf/stubs/ip "$candidate" && chmod 755 "$candidate"
done
rm -f /sbin/insmod && cp /perf/stubs/insmod /sbin/insmod && chmod 755 /sbin/insmod
HOTSPOT="/bin/sh /perf/mod/hotspot.sh"

snapshot() { printf '%s\n' "$(cat /var/run/nx300m-hotspot/state 2>/dev/null || echo absent)" > "/perf/state-$1.txt"; }
status_field() { wget -T 2 -Y off -q -O- http://127.0.0.1:8781/api/v1/camera/info | sed -n "s/.*\"$1\":\([0-9a-z_-]*\).*/\1/p" | head -1; }
custom_state() { wget -T 2 -Y off -q -O- http://127.0.0.1:8781/api/v1/camera/info | sed -n 's/.*"custom_key":{"enabled":[a-z]*,"mode":"async","state":"\([a-z-]*\)".*/\1/p' | head -1; }

# ======== Phase A: BOOT-KEY with a deliberately late menu window ========
/usr/bin/di-camera-app-nx300 stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill ${remote:-} "$stock" "${panel:-}" 2>/dev/null || :' EXIT
# Context identity is valid as soon as the process exists (alive marker);
# the window itself stays absent until /perf/create-window is touched.
n=0; until [ -s /perf/stock-parent.alive ]; do n=$((n+1)); [ "$n" -lt 30 ]; sleep 1; done
: >/perf/fn-input
NX_REMOTE_CUSTOM_KEY=1 NX_REMOTE_PANEL_SHORTCUT=1 NX_REMOTE_PANEL_KEY=custom NX_REMOTE_MENU_KEY=0 \
    NX_FN_KEY_LOG=/perf/fn-key.log /perf/remote-fixed.arm --listen 127.0.0.1:8781 --web-root /perf/mod --media-root /perf/photos \
    >/perf/remote-boot.log 2>&1 &
remote=$!
n=0; until grep -q NX_CUSTOM_KEY_READY /perf/remote-boot.log 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
# Three boot-not-ready retries take about 8s (1.5s helper window wait + 1s gap).
n=0; until [ "$(status_field boot_retries)" -ge 3 ] 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 350 ]; /rescue/busybox usleep 100000; done
wget -T 2 -Y off -q -O /perf/bootkey-before.json http://127.0.0.1:8781/api/v1/camera/info
touch /perf/create-window
n=0; until [ -s /perf/stock-parent.ready ]; do n=$((n+1)); [ "$n" -lt 30 ]; sleep 1; done
# The stock window's own creation timestamp, taken inside the driver that maps
# it, separates "window present" from "helper ready" from "touch to ready".
window_ms=$(cat /perf/stock-parent.ready-ms 2>/dev/null || echo 0)
n=0
while [ "$n" -lt 150 ]; do
    if [ "$(custom_state)" = ready ]; then break; fi
    n=$((n+1)); /rescue/busybox usleep 100000
done
ready_t1=$(now_ms)
if [ "$window_ms" -gt 0 ]; then
    printf 'window_ms=%s ready_ms=%s window_to_ready_ms=%s\n' \
        "$window_ms" "$ready_t1" "$((ready_t1-window_ms))" > /perf/bootkey-ready-interval.txt
else
    printf 'window_ms=unknown ready_ms=%s window_to_ready_ms=unknown\n' \
        "$ready_t1" > /perf/bootkey-ready-interval.txt
fi
wget -T 2 -Y off -q -O /perf/bootkey-after.json http://127.0.0.1:8781/api/v1/camera/info
[ "$(custom_state)" = ready ] || {
    echo BOOTKEY_NOT_READY_AFTER_WINDOW >&2; exit 51; }
# The code-controlled part alone: helper poll 250 ms + keymap/grab work, then
# the parent's next 100 ms poll. Touch-to-ready also carries the injection and
# driver scheduling and is reported, not bounded.
w2r=$(sed -n 's/.*window_to_ready_ms=//p' /perf/bootkey-ready-interval.txt)
case $w2r in ''|*[!0-9]*)
    echo BOOTKEY_WINDOW_TIMESTAMP_MISSING >&2; exit 55 ;; esac
[ "$w2r" -le 2000 ] || { echo BOOTKEY_WINDOW_TO_READY_OVER_2S >&2; exit 55; }
kill "$remote"; wait "$remote" || :; remote=
/rescue/busybox usleep 300000

# ======== Phase B: wireless page over the real production scripts ========
$HOTSPOT start >/perf/hotspot-prestart.log 2>&1 || :
cp /var/run/nx300m-hotspot/start.log /perf/hotspot-start.log 2>/dev/null || :
snapshot prestart
[ "$(cat /perf/state-prestart.txt)" = on ] || { cat /perf/hotspot-prestart.log; exit 52; }


/perf/close.arm launch /bin/sh /perf/mod/mfw-panel.sh >/perf/panel.log 2>&1 &
panel=$!
n=0; until grep -q '^FIRST_FRAME ' /perf/panel.log 2>/dev/null; do
    kill -0 "$panel" || { cat /perf/panel.log; exit 53; }
    n=$((n+1)); [ "$n" -lt 60 ]; sleep 1; done

# Enter the wireless page: the dial steps the root list without activating
# (a KP_Down at the root activates the current row in this state machine).
/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000
/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000
/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000
grep -q 'MFW_SELECT page=0 index=3 action=connection' /perf/panel.log || {
    echo ROOT_SELECTION_NOT_CONNECTION >&2; exit 59; }
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
# Page entry starts the bounded async hotspot-status; wait for the readonly
# child completion marker, then capture the rebuilt page.
n=0; until grep -q 'BACKEND kind=5 ok=1' /perf/panel.log 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
snapshot page-entry
[ "$(cat /perf/state-page-entry.txt)" = on ] || { echo PAGE_ENTRY_STATE_NOT_ON >&2; exit 54; }
/perf/close.arm crop /perf/w1-initial.ppm 0 0 48 800

# Dial stand-ins while idle: consumed by the navigation state machine.
/perf/close.arm key XF86ScrollDown; /rescue/busybox usleep 220000
/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000

# Toggle stop (row 0): completion rebuilds the page with state off.
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until [ "$(cat /var/run/nx300m-hotspot/state 2>/dev/null)" = off ]; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
snapshot after-stop
/perf/close.arm crop /perf/w2-off.ppm 0 0 48 800

# Toggle start with a dial injected mid-flight (event loop stays alive).
# No artificial daemon delay here: the production panel kills a non-preview
# backend after 12 s, and a slowed start would be testing that cap instead of
# the in-flight input handling.
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
/rescue/busybox usleep 150000
/perf/close.arm crop /perf/w3-during-start.ppm 0 0 48 800
/perf/close.arm key XF86ScrollDown; /rescue/busybox usleep 220000
n=0; until [ "$(cat /var/run/nx300m-hotspot/state 2>/dev/null)" = on ]; do
    n=$((n+1)); [ "$n" -lt 400 ]; /rescue/busybox usleep 100000; done
snapshot after-start
/perf/close.arm crop /perf/w4-on.ppm 0 0 48 800

# Failure path: daemon binary unexecutable -> failed:<stage> shown, start row
# retryable; restoring it recovers through the same row. While the service is
# ON the toggle row stops it, so stop first and then start with the broken
# binary; otherwise the press would just toggle off and never reach the
# failing start branch.
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until [ "$(cat /var/run/nx300m-hotspot/state 2>/dev/null)" = off ]; do
    n=$((n+1)); [ "$n" -lt 400 ]; /rescue/busybox usleep 100000; done
snapshot before-failure
chmod 000 /perf/stubs/hostapd
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until grep -q failed: /var/run/nx300m-hotspot/state 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
snapshot failure
cp /var/run/nx300m-hotspot/state /perf/state-failure-content.txt
/perf/close.arm crop /perf/w5-failed.ppm 0 0 48 800
chmod 755 /perf/stubs/hostapd
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until [ "$(cat /var/run/nx300m-hotspot/state 2>/dev/null)" = on ]; do
    n=$((n+1)); [ "$n" -lt 400 ]; /rescue/busybox usleep 100000; done
snapshot recovery

# Password row: after the last rebuild the kept focus is the toggle row (0).
/perf/close.arm key KP_Down; /rescue/busybox usleep 220000
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until grep -q HOTSPOT_CREDENTIALS /perf/panel.log 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
cp /var/run/nx300m-hotspot/runtime/hostapd.conf /perf/hotspot-conf.txt 2>/dev/null || :
# Credential proof: the generated secret is real (it reached hostapd.conf and
# the on-screen HOTSPOT_CREDENTIALS line) yet the panel log carries only the
# mask. Only the length is written to the evidence file.
pw=$(sed -n 's/^wpa_passphrase=//p' /perf/hotspot-conf.txt 2>/dev/null | head -1)
[ -n "$pw" ] || { echo CREDENTIAL_MISSING_IN_CONF >&2; exit 60; }
leak=0; if grep -q "$pw" /perf/panel.log 2>/dev/null; then leak=1; fi
masked=0; if grep -q 'password=\*\*\*' /perf/panel.log 2>/dev/null; then masked=1; fi
shown=0; if grep -q 'HOTSPOT_CREDENTIALS' /perf/panel.log 2>/dev/null; then shown=1; fi
printf 'password_len=%s secret_in_log=%s masked_in_log=%s credentials_row_reached=%s\n' \
    "${#pw}" "$leak" "$masked" "$shown" > /perf/credential-check.txt
[ "$leak" = 0 ] || { echo CREDENTIAL_LEAKED_IN_LOG >&2; exit 61; }
[ "$masked" = 1 ] || { echo CREDENTIAL_NOT_MASKED_IN_LOG >&2; exit 62; }
[ "$shown" = 1 ] || { echo CREDENTIALS_ROW_NOT_REACHED >&2; exit 63; }
/perf/close.arm crop /perf/w6-credentials.ppm 0 0 48 800

# Touch: candidate coordinates on the panel (log evidence accepted, recorded).
/perf/close.arm drag 40 400 40 400 || :
/rescue/busybox usleep 400000

# MENU exit and stock focus restoration.
/perf/close.arm key Menu; /rescue/busybox usleep 220000
n=0; until grep -q '^EXIT ' /perf/panel.log 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
wait "$panel" || :
panel=
/perf/close.arm focus > /perf/focus-after-menu.txt
printf '%s\n' "$(cat /perf/stock-parent.ready)" > /perf/focus-expected.txt

# ======== Phase C: sustained boot fast retries with no window at all ========
kill "$stock" 2>/dev/null || :; stock=
/rescue/busybox usleep 500000
: >/perf/fn-input
NX_REMOTE_CUSTOM_KEY=1 NX_REMOTE_PANEL_SHORTCUT=1 NX_REMOTE_PANEL_KEY=custom NX_REMOTE_MENU_KEY=0 \
    NX_FN_KEY_LOG=/perf/fn-key.log /perf/remote-fixed.arm --listen 127.0.0.1:8781 --web-root /perf/mod --media-root /perf/photos \
    >/perf/remote-noWindow.log 2>&1 &
remote=$!
n=0; until grep -q NX_CUSTOM_KEY_READY /perf/remote-noWindow.log 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
# Sustained absence: 24 fast retries (1 s) must be observed, then the ladder
# must escalate instead of retrying at 1 s forever. One attempt is about
# 1.5 s window wait + helper startup + 1 s gap, so allow ~2 minutes.
n=0
while [ "$n" -lt 1500 ]; do
    br=$(status_field boot_retries)
    case $br in ''|*[!0-9]*) br=0 ;; esac
    [ "$br" -ge 25 ] && break
    n=$((n+1)); /rescue/busybox usleep 100000
done
fast=$(grep -c 'retry_ms=1000' /perf/remote-noWindow.log 2>/dev/null || echo 0)
slow=$(grep -cE 'retry_ms=(2000|4000|8000|16000|32000|60000)' /perf/remote-noWindow.log 2>/dev/null || echo 0)
wget -T 2 -Y off -q -O /perf/bootkey-nowindow.json http://127.0.0.1:8781/api/v1/camera/info
{
    echo "boot_retries=$br fast_retries=$fast escalated_retries=$slow"
} > /perf/bootkey-sustained.txt
[ "${br:-0}" -ge 25 ] || { echo BOOTKEY_SUSTAINED_NOT_OBSERVED >&2; exit 56; }
[ "$fast" -ge 24 ] || { echo BOOTKEY_FAST_RETRY_COUNT_SHORT >&2; exit 57; }
[ "$slow" -ge 1 ] || { echo BOOTKEY_NO_ESCALATION >&2; exit 58; }
echo WIRELESS_PAGE_VM_PASS
'''


def main():
    out = OUT
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    out.mkdir(parents=True, exist_ok=True)

    stub_build = subprocess.run(['C:/Windows/System32/wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c',
        'set -eu; cd ' + shlex.quote(wsl_path(ROOT)) + '; tc=/opt/nx300m-toolchain/usr; '
        'export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"; rf=/opt/nx300m-lab/stock-1.15; '
        'mkdir -p ' + shlex.quote(wsl_path(out / 'stubs')) + '; '
        '"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -O2 -std=gnu99 '
        '-DSTUB_NO_GNU_SOURCE '
        '-march=armv7-a -mtune=cortex-a9 -mfpu=vfpv3-d16 -mfloat-abi=softfp -fno-pic -no-pie -fno-stack-protector '
        '-nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 '
        '-Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" '
        '-o ' + shlex.quote(wsl_path(out / 'stubs/wireless_stub')) + ' tools/wireless_stub.c '
        'work/reference/metol/test_server/filmlab/src/crt0_arm.S "$rf/lib/libc-2.13.so" -lgcc && '
        'cd ' + shlex.quote(wsl_path(out / 'stubs')) + ' && '
        'for n in hostapd udhcpd insmod ip; do ln -f wireless_stub "$n"; done && echo STUBS_BUILT'],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    (out / 'stub-build.log').write_text(stub_build.stdout + stub_build.stderr, encoding='utf-8')
    stub_build.check_returncode()
    if 'STUBS_BUILT' not in stub_build.stdout:
        raise RuntimeError('ARM stub build failed')

    vm.OUT, vm.PROBE = out, PROBE
    # Playback-probe driver adaptation (keysym argv, tap/drag, focus) plus the
    # camera accessor bytes for the Custom-key context reader, and a delayed
    # menu window for the BOOT-KEY boot-order scenario.
    driver = vm.DRIVER.replace('#include <stdio.h>', '#include <stdio.h>\n#include <stdint.h>\n#include <sys/mman.h>\n#include <unistd.h>\n#include "' + wsl_path(ROOT / 'mod/metol/remote/custom_key_context.h') + '"')
    driver = driver.replace('Window stock=XCreateSimpleWindow', CAMERA_SETUP + '\n'
        '        {FILE *alive=fopen("/perf/stock-parent.alive","w");if(alive){fprintf(alive,"%ld\\n",(long)getpid());fclose(alive);}}\n'
        '        {int winwait=0;while(access("/perf/create-window",F_OK)&&winwait<1200){usleep(100000);winwait++;}}\n'
        '        Window stock=XCreateSimpleWindow')
    driver = driver.replace('for(;;){XEvent event;XNextEvent(d,&event);}', CAMERA_LOOP)
    driver = driver.replace('    if(argc==2) {', TAP_DRIVER + '\n    if(argc==2 && !strcmp(argv[1],"focus")){printf("%lu\\n",w);XCloseDisplay(d);return 0;}\n    if(argc==2) {', 1)
    # Cropped capture used for the page screenshots: a full frame is ~633 KB
    # of PPM and does not survive the serial base64 transfer window, while the
    # bottom strip carries the state/failure line the assertions are about.
    driver = driver.replace('    if(argc==2) {\n        XWindowAttributes a;',
        '    if(argc==7 && !strcmp(argv[1],"crop")) {\n'
        '        int cx=atoi(argv[3]),cy=atoi(argv[4]),cw=atoi(argv[5]),ch=atoi(argv[6]);\n'
        '        XWindowAttributes a;if(!XGetWindowAttributes(d,w,&a))return 6;\n'
        '        if(cx<0||cy<0||cw<=0||ch<=0||cx+cw>a.width||cy+ch>a.height)return 9;\n'
        '        XImage *image=XGetImage(d,w,cx,cy,cw,ch,AllPlanes,ZPixmap);\n'
        '        FILE *out=fopen(argv[2],"wb");if(!image||!out)return 7;\n'
        '        fprintf(out,"P6\\n%d %d\\n255\\n",cw,ch);\n'
        '        unsigned long masks[3]={image->red_mask,image->green_mask,image->blue_mask};\n'
        '        for(int y=0;y<ch;y++)for(int x=0;x<cw;x++){unsigned long pixel=XGetPixel(image,x,y);\n'
        '            for(int c=0;c<3;c++){unsigned long mask=masks[c],value=pixel&mask;if(!mask)return 8;\n'
        '                while(!(mask&1)){mask>>=1;value>>=1;}\n'
        '                fputc((int)(value*255/mask),out);}}\n'
        '        fclose(out);XDestroyImage(image);XCloseDisplay(d);return 0;\n'
        '    }\n'
        '    if(argc==2) {\n        XWindowAttributes a;')
    # A snapshot call must not fall through into key injection.
    driver = driver.replace('        fclose(out);XDestroyImage(image);',
                            '        fclose(out);XDestroyImage(image);XCloseDisplay(d);return 0;')
    # Stamp the window's own creation time (guest uptime ms) so the probe can
    # separate "window present" from "shortcut ready" instead of timing the
    # whole injection path.
    driver = driver.replace('        fprintf(ready,"%lu\\n",stock);fclose(ready);',
        '        fprintf(ready,"%lu\\n",stock);fclose(ready);\n'
        '        {FILE *stamp=fopen("/perf/stock-parent.ready-ms","w");\n'
        '         if(stamp){FILE *up=fopen("/proc/uptime","r");char line[64]={0};\n'
        '           if(up){if(fgets(line,sizeof line,up)){double s=atof(line);\n'
        '             fprintf(stamp,"%d\\n",(int)(s*1000));}fclose(up);}fclose(stamp);}}')
    driver = driver.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.DRIVER = driver

    build_remote(out, 'fixed')

    vm.EXTRA_FILES = {
        'perf/remote-fixed.arm': (out / 'remote-fixed.arm').read_bytes(),
        'perf/reaper.arm': (ROOT / 'outputs/review/2026-11-13-performance-25hz/menu-vm/arm_reaper.arm').read_bytes(),
        'perf/mod/hotspot.sh': (ROOT / 'mod/deployment/mfw/hotspot.sh').read_bytes(),
        'perf/mod/telnet_toggle.sh': (ROOT / 'mod/deployment/mfw/telnet_toggle.sh').read_bytes(),
        'perf/stubs/wireless_stub': (out / 'stubs/wireless_stub').read_bytes(),
        'perf/stubs/hostapd': (out / 'stubs/hostapd').read_bytes(),
        'perf/stubs/udhcpd': (out / 'stubs/udhcpd').read_bytes(),
        'perf/stubs/insmod': (out / 'stubs/insmod').read_bytes(),
        'perf/stubs/ip': (out / 'stubs/ip').read_bytes(),
    }
    # The context reader identifies the camera process by the stock executable
    # name; expose the driver binary under that name (playback-probe pattern).
    original_cpio_add = arm_vm.cpio_add

    def add(stream, name, mode, data=b''):
        original_cpio_add(stream, name, mode, data)
        if name == 'perf/close.arm':
            original_cpio_add(stream, 'usr/bin/di-camera-app-nx300', 0o100755, data)
    arm_vm.cpio_add = add
    try:
        vm.prepare()
    finally:
        arm_vm.cpio_add = original_cpio_add

    started = False
    report = dict(passed=False, scope='wireless page + BOOT-KEY dynamic; synthetic camera window; real scripts, ARM stub daemons',
                  camera_hardware_verified=False, card_written=False)
    try:
        vm.call('start'); started = True
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Private ARM VM startup')
        execution = vm.call('exec', '/perf/reaper.arm /bin/sh -x /perf/probe.sh 2>/perf/trace.log', timeout=300)
        arm_vm.write_json(out / 'execution.json', execution)
        for name in ('trace.log', 'panel.log', 'remote-boot.log', 'remote-noWindow.log', 'stock-parent.log',
                     'hotspot-prestart.log', 'hotspot-start.log', 'stub-calls.log', 'hotspot-conf.txt', 'focus-after-menu.txt', 'focus-expected.txt',
                     'bootkey-before.json', 'bootkey-after.json', 'bootkey-nowindow.json',
                     'bootkey-ready-interval.txt', 'bootkey-sustained.txt', 'credential-check.txt',
                     'state-prestart.txt', 'state-page-entry.txt', 'state-after-stop.txt', 'state-after-start.txt',
                     'state-failure.txt', 'state-failure-content.txt', 'state-recovery.txt',
                     'w2-off.ppm', 'w4-on.ppm', 'w5-failed.ppm'):
            try:
                collect(out, name)
            except RuntimeError:
                pass
        report.update(guest_exit=execution['exit_code'], output=execution['output'])
        if execution['exit_code'] or 'WIRELESS_PAGE_VM_PASS' not in execution['output']:
            raise RuntimeError('Wireless page regression failed; see execution.json and trace.log')
        report['passed'] = True
    finally:
        if started:
            report['stop'] = vm.call('stop')
            if 'return' in report['stop']:
                (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

