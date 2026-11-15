"""Focused CLOSE-MENU failure-copy screenshot in the private ARM VM.

The full wireless probe already passed (wireless-page-r24) but its 48-px
strip crops turned out to be a static area, so they do not show the failure
row. This probe drives only the minimum path needed for the visual claim:
prestart -> enter page 7 -> stop -> start with the hostapd double made
non-executable -> capture the failed page (full frame and a half-scale copy).
"""
import json
import os
import time
from pathlib import Path

import arm_vm
import probe_menu_startup_vm as vm
from probe_playback_custom_vm import build_remote
from probe_menu_entry_vm import collect
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ('outputs/review/2026-11-13-performance-25hz/menu-vm/' +
              (os.environ.get('NX_WIRELESS_PROBE_DIR') or 'wireless-failure-shot'))

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

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97
now_ms() { awk '{printf "%d", $1*1000}' /proc/uptime; }
attempt=0
until [ -S /tmp/.X11-unix/X97 ]; do attempt=$((attempt+1)); [ "$attempt" -lt 60 ]; sleep 1; done
/perf/close.arm ping

mount -t tmpfs none /var/run
mount -t tmpfs none /lib/modules
: > /lib/modules/cfg80211.ko; : > /lib/modules/ath6kl_sdio.ko
mount -t tmpfs none /sys/class/net
mkdir /sys/class/net/wlan0
mkdir -p /perf/stub-state /perf/mod/.state
echo 192.168.0.1 > /perf/stub-state/wlan0.addr
export PATH=/perf/stubs:$PATH
export NX_HOTSPOT_HOSTAPD=/perf/stubs/hostapd NX_HOTSPOT_UDHCPD=/perf/stubs/udhcpd
export STUB_STATE=/perf/stub-state STUB_LOG=/perf/stub-calls.log
: > /perf/stub-calls.log
export NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
chmod 755 /perf/stubs/hostapd /perf/stubs/udhcpd /perf/stubs/insmod /perf/stubs/ip
for c in /bin/ip /usr/bin/ip /sbin/ip /usr/sbin/ip; do
    [ -d "$(dirname "$c")" ] || continue
    rm -f "$c" && cp /perf/stubs/ip "$c" && chmod 755 "$c"
done
rm -f /sbin/insmod && cp /perf/stubs/insmod /sbin/insmod && chmod 755 /sbin/insmod
HOTSPOT="/bin/sh /perf/mod/hotspot.sh"

/usr/bin/di-camera-app-nx300 stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill ${panel:-} "$stock" 2>/dev/null || :' EXIT
n=0; until [ -s /perf/stock-parent.ready ]; do n=$((n+1)); [ "$n" -lt 30 ]; sleep 1; done

$HOTSPOT start > /perf/hotspot-prestart.log 2>&1 || :
[ "$(cat /var/run/nx300m-hotspot/state)" = on ] || { echo PRESTART_NOT_ON >&2; exit 52; }

/perf/close.arm launch /bin/sh /perf/mod/mfw-panel.sh > /perf/panel.log 2>&1 &
panel=$!
n=0; until grep -q '^FIRST_FRAME ' /perf/panel.log 2>/dev/null; do
    kill -0 "$panel" || { cat /perf/panel.log; exit 53; }
    n=$((n+1)); [ "$n" -lt 60 ]; sleep 1; done

/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000
/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000
/perf/close.arm key XF86ScrollUp; /rescue/busybox usleep 220000
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until grep -q 'BACKEND kind=5 ok=1' /perf/panel.log 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 100 ]; /rescue/busybox usleep 100000; done
[ "$(cat /var/run/nx300m-hotspot/state)" = on ] || { echo PAGE_ENTRY_NOT_ON >&2; exit 54; }
/perf/close.arm /perf/f0-on.ppm

# Stop, then start with an unexecutable hostapd double so the failure row is
# the only thing the rebuilt page can show.
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until [ "$(cat /var/run/nx300m-hotspot/state)" = off ]; do
    n=$((n+1)); [ "$n" -lt 400 ]; /rescue/busybox usleep 100000; done
chmod 000 /perf/stubs/hostapd
/perf/close.arm key KP_Enter; /rescue/busybox usleep 220000
n=0; until grep -q 'failed:' /var/run/nx300m-hotspot/state 2>/dev/null; do
    n=$((n+1)); [ "$n" -lt 200 ]; /rescue/busybox usleep 100000; done
snap_state=$(cat /var/run/nx300m-hotspot/state)
printf '%s\n' "$snap_state" > /perf/state-failure.txt
[ "$snap_state" = "failed:no-hostapd" ] || { echo UNEXPECTED_FAILURE_STATE >&2; exit 55; }
/rescue/busybox usleep 400000
/perf/close.arm /perf/f1-failed.ppm
chmod 755 /perf/stubs/hostapd
echo FAILURE_SHOT_DONE
'''


def main():
    out = OUT
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    r24 = ROOT / 'outputs/review/2026-11-13-performance-25hz/menu-vm/wireless-page-r24'
    vm.OUT, vm.PROBE = out, PROBE
    driver = vm.DRIVER.replace('#include <stdio.h>', '#include <stdio.h>\n#include <stdint.h>\n#include <sys/mman.h>\n#include <unistd.h>\n#include "' + wsl_path(ROOT / 'mod/metol/remote/custom_key_context.h') + '"')
    driver = driver.replace('Window stock=XCreateSimpleWindow', CAMERA_SETUP + '\n        Window stock=XCreateSimpleWindow')
    # A snapshot must not fall through into key injection.
    driver = driver.replace('        fclose(out);XDestroyImage(image);',
                            '        fclose(out);XDestroyImage(image);XCloseDisplay(d);return 0;')
    driver = driver.replace('    if(argc==2) {', '''    if(argc==3 && !strcmp(argv[1],"half")) {
        XWindowAttributes a;if(!XGetWindowAttributes(d,w,&a))return 6;
        int cw=a.width/2,ch=a.height/2;
        XImage *image=XGetImage(d,w,0,0,a.width,a.height,AllPlanes,ZPixmap);
        FILE *out=fopen(argv[2],"wb");if(!image||!out)return 7;
        fprintf(out,"P6\\n%d %d\\n255\\n",cw,ch);
        unsigned long masks[3]={image->red_mask,image->green_mask,image->blue_mask};
        for(int y=0;y<ch;y++)for(int x=0;x<cw;x++){unsigned long pixel=XGetPixel(image,x*2,y*2);
            for(int c=0;c<3;c++){unsigned long mask=masks[c],value=pixel&mask;if(!mask)return 8;
                while(!(mask&1)){mask>>=1;value>>=1;}
                fputc((int)(value*255/mask),out);}}
        fclose(out);XDestroyImage(image);XCloseDisplay(d);return 0;
    }
    if(argc==2) {''', 1)
    driver = driver.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.DRIVER = driver
    vm.EXTRA_FILES = {
        'perf/remote-fixed.arm': (ROOT / 'outputs/metol/remote/nx-remote-controller.arm').read_bytes(),
        'perf/reaper.arm': (ROOT / 'outputs/review/2026-11-13-performance-25hz/menu-vm/arm_reaper.arm').read_bytes(),
        'perf/mod/hotspot.sh': (ROOT / 'mod/deployment/mfw/hotspot.sh').read_bytes(),
        'perf/mod/telnet_toggle.sh': (ROOT / 'mod/deployment/mfw/telnet_toggle.sh').read_bytes(),
    }
    for name in ('hostapd', 'udhcpd', 'insmod', 'ip', 'wireless_stub'):
        vm.EXTRA_FILES['perf/stubs/' + name] = (r24 / 'stubs' / name).read_bytes()
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
    report = dict(passed=False, scope='failed-page screenshot only; synthetic camera window, real scripts')
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
        report.update(guest_exit=execution['exit_code'], output=execution['output'])
        if execution['exit_code'] or 'FAILURE_SHOT_DONE' not in execution['output']:
            raise RuntimeError('Failure-shot probe failed; see execution.json and trace.log')
        for name in ('trace.log', 'panel.log', 'stub-calls.log', 'state-failure.txt', 'hotspot-prestart.log'):
            try:
                collect(out, name)
            except RuntimeError as error:
                report.setdefault('collect_errors', []).append(f'{name}: {error}')
        # The full frame is 264x800 RGB; allow the serial transfer a long window.
        for name in ('f1-failed.ppm', 'f0-on.ppm'):
            try:
                vm.collect_full = True
                tmp_out = out
                probe_collect(tmp_out, name)
            except Exception as error:
                report.setdefault('collect_errors', []).append(f'{name}: {error}')
        report['passed'] = True
    finally:
        if started:
            report['stop'] = vm.call('stop')
            if 'return' in report['stop']:
                (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def probe_collect(out, name):
    """Collect a large guest file as gzip+base64.

    A flat UI frame is 633 KB of PPM and the serial console moves only a few
    KB/s, so the raw base64 transfer timed out and then decoded with bad
    padding. Compressing in the guest first keeps the payload small, and the
    regex skips the echoed command line (which also contains the markers).
    """
    import base64 as _b64
    import gzip
    import re
    command = ('printf "BEGIN_MARK\\n"; /rescue/busybox gzip -9 -c /perf/' + name +
               ' | /rescue/busybox base64; printf "END_MARK\\n"')
    result = vm.call('exec', command, timeout=420)
    match = re.search(r'\r?\nBEGIN_MARK\r?\n([A-Za-z0-9+/=\r\n]*)END_MARK', result['output'])
    if not match:
        raise RuntimeError('markers missing for ' + name)
    payload = _b64.b64decode(''.join(match[1].split()))
    (out / (name + '.gz')).write_bytes(payload)
    (out / name).write_bytes(gzip.decompress(payload))


if __name__ == '__main__':
    main()
