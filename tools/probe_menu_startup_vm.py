"""Measure the current MFW menu in a separate, networkless native ARM VM.

Uses cached stock libraries and an ARM Xvfb server. Does not start the stock
camera, deploy a package, or touch the main emulator. Run with Conda Python.
"""
import argparse
import base64
import gzip
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import arm_vm
from probe_mfw_ram_vm import INIT as BASE_INIT

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/performance/menu-startup-vm'
SOCKET = '/tmp/nx300m-menu-perf-private'
BASELINE_OVERLAY = None

DRIVER = r'''
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <unistd.h>
int main(int argc,char **argv) {
    if(argc>2 && !strcmp(argv[1],"launch")) {
        if(setpriority(PRIO_PROCESS,0,19))return 4;
        execv(argv[2],argv+2);return 5;
    }
    Display *d=XOpenDisplay(NULL); if(!d)return 2;
    if(argc==2 && !strcmp(argv[1],"ping")){XCloseDisplay(d);return 0;}
    if(argc==2 && !strcmp(argv[1],"stock-parent")) {
        Window stock=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
        XClassHint hint={"di-camera-app-nx300","di-camera-app-nx300"};
        XSetClassHint(d,stock,&hint);XStoreName(d,stock,"NX-TEST-STOCK-PARENT");
        XMapRaised(d,stock);XSetInputFocus(d,stock,RevertToPointerRoot,CurrentTime);XSync(d,False);
        FILE *ready=fopen("/perf/stock-parent.ready","w");if(!ready)return 20;
        fprintf(ready,"%lu\n",stock);fclose(ready);
        for(;;){XEvent event;XNextEvent(d,&event);}
    }
    Window w; int revert; XGetInputFocus(d,&w,&revert);
    if(w==None || w==PointerRoot)return 3;
    if(argc==2) {
        XWindowAttributes a;if(!XGetWindowAttributes(d,w,&a))return 6;
        XImage *image=XGetImage(d,w,0,0,a.width,a.height,AllPlanes,ZPixmap);
        FILE *out=fopen(argv[1],"wb");if(!image || !out)return 7;
        fprintf(out,"P6\n%d %d\n255\n",a.width,a.height);
        unsigned long masks[3]={image->red_mask,image->green_mask,image->blue_mask};
        for(int y=0;y<a.height;y++)for(int x=0;x<a.width;x++) {
            unsigned long pixel=XGetPixel(image,x,y);
            for(int c=0;c<3;c++) {
                unsigned long mask=masks[c],value=pixel&mask;
                if(!mask)return 8;
                while(!(mask&1)){mask>>=1;value>>=1;}
                fputc((int)(value*255/mask),out);
            }
        }
        fclose(out);XDestroyImage(image);
    }
    XEvent event={0}; event.xkey.display=d;event.xkey.window=w;
    event.xkey.keycode=XKeysymToKeycode(d,XStringToKeysym("Menu"));
    event.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&event);
    event.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&event);
    XSync(d,False);XCloseDisplay(d);return 0;
}
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97
attempt=0
until [ -S /tmp/.X11-unix/X97 ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 30 ]; sleep 1
done
# Match an already running camera display: do not charge the X server's very
# first client setup to whichever menu variant happens to run first.
/perf/close.arm ping
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock_parent=$!
trap 'kill "$stock_parent" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do attempt=$((attempt+1)); [ "$attempt" -lt 10 ]; sleep 1; done
variants=current
[ ! -d /perf/baseline ] || variants='baseline current'
for variant in $variants; do
export NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/tmp/menu-perf-$variant
[ "$variant" != baseline ] || NX300M_MOD_ROOT=/perf/baseline
mkdir -p "$NX_STATE_DIR"
for phase in cold warm; do
    label=$phase
    [ "$variants" = current ] || label=$variant-$phase
    /perf/close.arm launch /bin/sh "$NX300M_MOD_ROOT/mfw-panel.sh" > "/perf/$label.log" 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' "/perf/$label.log"; do
        kill -0 "$panel" || { cat "/perf/$label.log"; exit 1; }
        attempt=$((attempt+1)); [ "$attempt" -lt 30 ]; sleep 1
    done
    # Collection and close happen after the measured final first-frame fence.
    sleep 1
    /perf/close.arm "/perf/$label.ppm"
    wait "$panel"
    grep '^EXIT ' "/perf/$label.log"
done
done
kill "$stock_parent"
trap - EXIT
'''


def linux_path(path):
    path = Path(path).resolve()
    return '/mnt/' + path.drive[0].lower() + path.as_posix()[2:]


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'report.json').exists():
        raise RuntimeError('Preserve the existing measurement; choose --output-dir for another run')
    (OUT / 'close.c').write_text(DRIVER, encoding='utf-8')
    output = shlex.quote(linux_path(OUT))
    command = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \
 -O2 -Wall -Wextra -I"$tc/include" -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \
 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \
 {output}/close.c {shlex.quote(linux_path(PROJECT))}/work/reference/metol/test_server/filmlab/src/crt0_arm.S \
 "$rf/usr/lib/libX11.so.6" "$rf/lib/libc-2.13.so" -lgcc -o {output}/close.arm
'''
    build = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', command],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
    (OUT / 'build.log').write_text(build.stdout + build.stderr, encoding='utf-8')
    if build.returncode:
        raise RuntimeError(f'Close driver build exit {build.returncode}: {build.stderr}')
    # Keep the earlier full-package probe and its evidence unchanged. The base
    # contains stock libraries plus Debian Xvfb; only the test init is replaced.
    init = BASE_INIT.replace('/arm-vm/mfw-results', '/perf')
    init = '\n'.join(line for line in init.splitlines()
                     if '/arm-vm/mfw-source' not in line and
                     'remount,bind,ro /mnt/mmc' not in line)
    init = init.replace('MFW_PRIVATE_VM_SHELL_READY', 'MENU_PERF_VM_READY')
    init = init.replace('echo MENU_PERF_VM_READY',
                        'echo 1 > /proc/sys/kernel/printk\necho MENU_PERF_VM_READY')
    files = {
        'init': init.encode(),
        'perf/probe.sh': PROBE.encode(),
        'perf/close.arm': (OUT / 'close.arm').read_bytes(),
        'perf/mod/bin/nxui.arm': (PROJECT / 'outputs/metol/nxui.arm').read_bytes(),
        'perf/mod/mfw-panel.sh': (PROJECT / 'mod/deployment/mfw/panel.sh').read_bytes(),
        'perf/mod/mfw-panel-actions.sh': (PROJECT / 'mod/deployment/mfw/panel-actions.sh').read_bytes(),
        'perf/mod/common.sh': (PROJECT / 'mod/deployment/common.sh').read_bytes(),
        'perf/mod/gui_mfw.NX300M': (PROJECT / 'mod/deployment/mfw/gui_mfw.NX300M').read_bytes(),
        'perf/mod/fonts/fonts.conf.in': (PROJECT / 'mod/deployment/mfw/fonts.conf.in').read_bytes(),
        'perf/mod/fonts/mfw-font.charset': (PROJECT / 'outputs/metol/mfw-font.charset').read_bytes(),
    }
    if BASELINE_OVERLAY:
        from build_stock_app_loader import read_cpio
        baseline = read_cpio(gzip.decompress(BASELINE_OVERLAY.read_bytes()))
        for name, (mode, data) in baseline.items():
            if name.startswith('perf/mod/') and mode & 0o170000 == 0o100000:
                files[name.replace('perf/mod/', 'perf/baseline/', 1)] = data
        if 'perf/baseline/bin/nxui.arm' not in files:
            raise ValueError('Baseline overlay has no recorded panel binary')
    overlay = io.BytesIO()
    directories = {p.as_posix() for name in files for p in Path(name).parents if str(p) != '.'}
    for name in sorted(directories):
        arm_vm.cpio_add(overlay, name, 0o40755)
    for name, data in files.items():
        arm_vm.cpio_add(overlay, name, 0o100755 if name == 'init' or name.endswith(('.sh', '.arm')) else 0o100644, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    compressed = gzip.compress(overlay.getvalue(), mtime=0)
    (OUT / 'overlay.cpio.gz').write_bytes(compressed)
    with (arm_vm.WORK / 'initramfs.cpio.gz').open('rb') as source, (OUT / 'initramfs.cpio.gz').open('wb') as target:
        shutil.copyfileobj(source, target)
        target.write(compressed)
    arm_vm.write_json(OUT / 'inputs.json', {
        'scope': 'standalone menu, stock ARM libraries, native ARM kernel and ARM Xvfb',
        'base_initramfs': str(arm_vm.WORK / 'initramfs.cpio.gz'),
        'base_initramfs_bytes': (arm_vm.WORK / 'initramfs.cpio.gz').stat().st_size,
        'menu_bytes': len(files['perf/mod/bin/nxui.arm']),
        'camera_program_running': False, 'custom_helper_measured': False,
        'launcher_nice': 19,
        'x11_first_client_warmed_before_both_variants': True,
        'baseline_overlay': str(BASELINE_OVERLAY) if BASELINE_OVERLAY else None,
        'main_emulator_touched': False, 'network': 'none',
    })


def linux_worker(action, payload):
    arm_vm.SERIAL, arm_vm.QMP = SOCKET + '-serial.sock', SOCKET + '-qmp.sock'
    if action == 'start':
        try:
            arm_vm.qmp('query-status')
            raise RuntimeError('Private measurement VM already running')
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        for path in (arm_vm.SERIAL, arm_vm.QMP):
            Path(path).unlink(missing_ok=True)
        command = [str(arm_vm.WORK / 'host/usr/bin/qemu-system-arm'), '-M', 'virt', '-cpu', 'cortex-a15',
                   '-m', '2048M', '-smp', '2', '-kernel', str(arm_vm.WORK / 'downloads/vmlinuz'),
                   '-initrd', str(OUT / 'initramfs.cpio.gz'), '-append', 'console=ttyAMA0 rdinit=/init panic=-1',
                   '-display', 'none', '-monitor', 'none', '-serial',
                   f'unix:{arm_vm.SERIAL},server=on,wait=off,logfile={OUT / "serial.log"}',
                   '-qmp', f'unix:{arm_vm.QMP},server=on,wait=off', '-nic', 'none', '-no-reboot']
        env = dict(os.environ, LD_LIBRARY_PATH=str(arm_vm.WORK / 'host/usr/lib/x86_64-linux-gnu'))
        log = (OUT / 'qemu.log').open('ab', buffering=0)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        arm_vm.write_json(OUT / 'handle.json', {'pid': process.pid, 'command': command})
        return {'pid': process.pid}
    if action == 'stop':
        return arm_vm.qmp('quit')
    return arm_vm.native_exec(base64.b64decode(payload).decode(), timeout=120)


def call(action, command=''):
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             linux_path(__file__), '--linux', '--output-dir', linux_path(OUT), action,
                             base64.b64encode(command.encode()).decode()],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=135)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def run():
    prepare()
    started = False
    report = {'passed': False, 'main_emulator_touched': False, 'physical_camera_measured': False}
    try:
        call('start'); started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if (OUT / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (OUT / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise TimeoutError('Private VM did not reach its shell')
        execution = call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(OUT / 'execution.json', execution)
        report['guest_exit_code'] = execution['exit_code']
        # Extract logs only, with an explicit bounded marker independent of terminal echo.
        labels = ('baseline-cold', 'baseline-warm', 'current-cold', 'current-warm') if BASELINE_OVERLAY else ('cold', 'warm')
        for name in (*[label + '.log' for label in labels], 'xvfb.log'):
            collected = call('exec', 'printf "DATA_START\\n"; if [ -f /perf/' + name + ' ]; then /rescue/busybox base64 /perf/' + name + '; fi; printf "DATA_END\\n"')
            match = re.search(r'\r?\nDATA_START\r?\n([A-Za-z0-9+/=\r\n]*)DATA_END', collected['output'])
            if not match:
                arm_vm.write_json(OUT / (name + '.collection-error.json'), collected)
                raise RuntimeError('Cannot extract ' + name)
            (OUT / name).write_bytes(base64.b64decode(match[1]))
        if execution['exit_code']:
            raise RuntimeError(f'Guest menu measurement exit {execution["exit_code"]}')
        for label in labels:
            collected = call('exec', 'printf "DATA_START\\n"; /rescue/busybox gzip -c /perf/' + label + '.ppm | /rescue/busybox base64; printf "DATA_END\\n"')
            match = re.search(r'\r?\nDATA_START\r?\n([A-Za-z0-9+/=\r\n]*)DATA_END', collected['output'])
            if not match:
                raise RuntimeError('Cannot extract pixels: ' + label)
            from PIL import Image
            Image.open(io.BytesIO(gzip.decompress(base64.b64decode(match[1])))).save(OUT / (label + '.png'))
        samples = analyze()
        report.update(passed=True, samples=samples, scope='Isolated menu launches; no physical key, camera app or LCD timing')
    except Exception as exc:
        report['failure'] = str(exc)
        raise
    finally:
        if started:
            report['stop'] = call('stop')
            if 'return' in report['stop'] and (OUT / 'overlay.cpio.gz').is_file():
                # The exact small overlay is retained; the assembled image is
                # just cached base + overlay, and is no longer needed on exit.
                (OUT / 'initramfs.cpio.gz').unlink(missing_ok=True)
                report['assembled_initramfs_removed'] = True
        arm_vm.write_json(OUT / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def analyze():
    """Parse retained logs without booting another VM or changing evidence."""
    execution = json.loads((OUT / 'execution.json').read_text(encoding='utf-8'))
    if execution['exit_code']:
        raise RuntimeError(f'Guest menu measurement exit {execution["exit_code"]}')
    samples = {}
    labels = ('baseline-cold', 'baseline-warm', 'current-cold', 'current-warm') if (OUT / 'baseline-cold.log').exists() else ('cold', 'warm')
    for label in labels:
        text = (OUT / (label + '.log')).read_text(encoding='utf-8')
        frame = re.search(r'^FIRST_FRAME (.+)$', text, re.M)
        init = re.search(r'^PANEL_INIT (.+)$', text, re.M)
        # nxui intentionally restores priority 0; only its launcher and backend
        # inherit/use 19. The previous nice=19 assertion confused these stages.
        if not frame or not init or not re.search(r'^EXIT ', text, re.M) or 'nice=0' not in text:
            raise RuntimeError('Missing startup, priority or exit record: ' + label)
        samples[label] = {
            'first_frame': {k: int(v) for k, v in re.findall(r'(\w+)=(-?\d+)', frame[1])},
            'initialization': {k: int(v) for k, v in re.findall(r'(\w+)=(-?\d+)', init[1])},
            'font_config_reused': 'reused=1' in text,
            'font_mode': 'file' if 'PANEL_FONT mode=file ' in text else 'match',
            'ui_nice': 0,
        }
    return samples


def main():
    global OUT, BASELINE_OVERLAY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', action='store_true')
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('--baseline-overlay', type=Path, help='Recorded earlier menu overlay to run in the same guest')
    parser.add_argument('action', choices=['run', 'start', 'stop', 'exec', 'analyze'], nargs='?', default='run')
    parser.add_argument('payload', nargs='?', default='')
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    BASELINE_OVERLAY = args.baseline_overlay.resolve() if args.baseline_overlay else None
    if args.linux:
        print(json.dumps(linux_worker(args.action, args.payload)))
    elif args.action == 'run':
        run()
    elif args.action == 'analyze':
        print(json.dumps(analyze(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(call(args.action, args.payload)))


if __name__ == '__main__':
    main()
