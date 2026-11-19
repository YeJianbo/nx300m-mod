"""Playback long-press Custom -> service panel -> LUT -> OK in an isolated ARM VM.

Exercises the exact 2026-11-18 P1 control flow: the production evdev loop,
context reader and remote dispatch start the REAL deployment panel.sh
(--mfw-services, live=0) during playback, the panel's LUT row opens the LUT
page in the same process (open_mfw_lut), and OK applies through the recording
engine and returns stock focus. Playback keeps videosrc=0 the whole time.
No camera hardware, card or main emulator is touched; the engine is a command
double, so the deferral timing itself is covered by
probe_lut_playback_defer_vm.py at the engine level.
"""
import argparse
import difflib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from zipfile import ZipFile

import arm_vm
import probe_menu_startup_vm as vm
from probe_fn_panel_vm import KEY_DRIVER
from probe_lut_selection_vm import TAP_DRIVER
from probe_menu_entry_vm import collect
from wsl_paths import wsl_path

ROOT = Path(__file__).resolve().parents[1]

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
            *(volatile unsigned *)0x4c9c64=c=='P'?0x4da0f0:c=='M'?0x4da0dc:0x4c9f04;
            *(volatile unsigned *)0x4de4d8=c=='L'?0x200:0;
            *(volatile unsigned char *)custom_key_stock[0].address=c=='B'?0:custom_key_stock[0].bytes[0];
            struct custom_key_context context;
            int opened=custom_key_context_open(&context);
            int eligible=opened&&custom_key_context_ready(&context,0);
            int long_eligible=opened&&custom_key_context_ready(&context,1);
            custom_key_context_close(&context);
            FILE *eligibility=fopen("/perf/context-eligible","w");if(!eligibility)return 32;
            fprintf(eligibility,"%c %d %d\n",c,eligible,long_eligible);fclose(eligibility);
            FILE *ack=fopen("/perf/camera-mode-ready","w");if(!ack)return 31;fputc(c,ack);fclose(ack);
            while(XPending(d)){XEvent e;XNextEvent(d,&e);}
            usleep(20000);
        }
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_MENU_KEY=0 NX_REMOTE_PANEL_SHORTCUT=1 NX_REMOTE_PANEL_KEY=custom
export NX_FN_KEY_LOG=/perf/fn-key.log NX300M_MOD_SOURCE_ROOT=/perf/mod
mkdir -p /perf/state /perf/photos /perf/mod/.state
mkfifo /perf/fn-input
chmod +x /usr/bin/di-camera-app-nx300 /perf/mod/bin/lut_engine.arm
until /perf/close.arm ping; do /rescue/busybox usleep 100000; done
/usr/bin/di-camera-app-nx300 stock-parent >/perf/stock.log 2>&1 &
stock=$!
remote=
trap 'kill ${remote:-} "$stock" 2>/dev/null || :' EXIT
wait_marker() {
    n=0
    until grep -q "$2" "$1" 2>/dev/null; do
        n=$((n+1)); [ "$n" -lt 150 ]; /rescue/busybox usleep 100000
    done
}
state() { printf %s "$1" >/perf/camera-mode; wait_marker /perf/camera-mode-ready "$1"; }
start_remote() {
    : >/perf/fn-key.log; : >/perf/actions.txt
    /perf/remote-$1.arm --listen 127.0.0.1:8781 --web-root /perf/mod --media-root /perf/photos >/perf/remote-$1.log 2>&1 &
    remote=$!
    wait_marker /perf/remote-$1.log NX_CUSTOM_KEY_READY
    /rescue/busybox usleep 300000
    wget -T 2 -Y off -q -O /perf/entry.json http://127.0.0.1:8781/api/v1/panel/entry
    code=$(sed -n 's/.*"linux_keycode":\([0-9][0-9]*\).*/\1/p' /perf/entry.json)
    [ -n "$code" ]
}
key() { /perf/close.arm key "$1"; /rescue/busybox usleep 220000; }
wait_exit() { wait_marker /perf/fn-key.log 'panel exited pid='; /rescue/busybox usleep 200000; }
open_panel() { : >/perf/fn-key.log; : >/perf/actions.txt; /perf/fn.arm "$code" 1350; wait_marker /perf/fn-key.log '^FIRST_FRAME '; }
open_lut_page() {
    # A rare focus-settle window can swallow the first OK; retry is bounded
    # and stops as soon as the page marker appears, so no extra OK can run.
    n=0
    until grep -q '^LUT_PAGE ' /perf/fn-key.log; do
        n=$((n+1)); [ "$n" -lt 12 ] || { cat /perf/fn-key.log; exit 1; }
        key KP_Enter
        /rescue/busybox usleep 300000
    done
}
save_case() { cp /perf/fn-key.log /perf/$1.log; cp /perf/actions.txt /perf/$1-actions.txt; }
stop_remote() { kill "$remote"; wait "$remote" || :; remote=; /rescue/busybox usleep 300000; }
focus_returned() {
    /perf/close.arm focus > /perf/focus.txt
    cmp /perf/focus.txt /perf/stock-parent.ready
}
start_remote fixed
# The long-press entry stays eligible in playback and the tap stays stock.
state P
: >/perf/fn-key.log
/perf/fn.arm "$code" 150
wait_marker /perf/fn-key.log 'custom tap kept stock outside preview'
! grep -q '^FIRST_FRAME ' /perf/fn-key.log
cp /perf/fn-key.log /perf/P-short.log

# Route 1 (playback OK): service panel opens during playback, the LUT row
# opens the LUT page in the same process, OK applies the first look through
# the engine double and the panel exits so stock focus returns.
open_panel
open_lut_page
key KP_Enter
wait_exit
grep -q '^apply ' /perf/actions.txt
grep -q '^BACKEND kind=1 ok=1 ' /perf/fn-key.log
focus_returned
save_case P-ok
save_case P-ok-actions

# Route 2 (playback Fn): select a look, Fn opens tuning, wheel adjusts, Fn
# saves the current look only and stays; MENU returns to the service panel;
# nothing is applied to the camera.
open_panel
open_lut_page
key KP_Down
key XF86HomePage
key XF86ScrollUp
key XF86HomePage
wait_marker /perf/fn-key.log '^LUT_PRESET_SAVED .* applied=0'
! grep -q '^EXIT ' /perf/fn-key.log
[ ! -s /perf/actions.txt ]
key Menu
key Menu
wait_exit
[ ! -s /perf/actions.txt ]
focus_returned
save_case P-fn
save_case P-fn-actions

# Route 3 (playback MENU): browsing without applying, MENU returns to the
# service panel (no edits, so no leave dialog), a second MENU closes.
open_panel
open_lut_page
key KP_Down
key Menu
key Menu
wait_exit
[ ! -s /perf/actions.txt ]
focus_returned
save_case P-menu
save_case P-menu-actions

# Route 4 (liveview V): the same service panel -> LUT -> OK flow with the
# viewfinder running; OK applies and focus returns to the live stock page.
state V
open_panel
open_lut_page
key KP_Enter
wait_exit
grep -q '^apply ' /perf/actions.txt
focus_returned
save_case V-ok
save_case V-ok-actions

echo PLAYBACK_MFW_LUT_VM_PASS
stop_remote
kill "$stock"; wait "$stock" || :
trap - EXIT
echo PLAYBACK_MFW_LUT_VM_PASS_FINAL
'''


def build_remote(out, variant):
    source = out / variant / 'source/metol/remote'
    shutil.copytree(ROOT / 'mod/metol/remote', source, dirs_exist_ok=True)
    for path in (ROOT / 'mod/metol').glob('*.h'):
        shutil.copyfile(path, source.parent / path.name)
    runtime = out / variant / 'runtime'
    runtime.mkdir(exist_ok=True)
    for path in (ROOT / 'runtime').glob('*.h'):
        shutil.copyfile(path, runtime / path.name)
    header = source / 'menu_key.h'
    original = header.read_text(encoding='utf-8')
    start, end = original.index('static int menu_key_device('), original.index('static void menu_key_child(')
    adapted = original[:start] + '''static int menu_key_device(const char *path,unsigned code,unsigned char *held,size_t size) {
    (void)code;memset(held,0,size);
    if(strcmp(path,"/dev/input/event0"))return -1;
    return open("/perf/fn-input",O_RDWR|O_NONBLOCK|O_CLOEXEC);
}
''' + original[end:]
    header.write_text(adapted, encoding='utf-8', newline='\n')
    (out / variant / 'evdev.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), adapted.splitlines(True))), encoding='utf-8')
    builder = (ROOT / 'tools/build_remote.py').read_text(encoding='utf-8')
    start = builder.index('tc=/opt/nx300m-toolchain/usr')
    command = builder[start:builder.index("\n'''", start)].replace('{shlex.quote(GUEST)}', shlex.quote('/opt/nx300m-lab/stock-1.15'))
    command = command.replace('mod/metol/remote/', shlex.quote(wsl_path(source)) + '/')
    command = command.replace('outputs/metol/remote/nx-remote-controller.arm', shlex.quote(wsl_path(out / f'remote-{variant}.arm')))
    command = command.replace('outputs/metol/remote/versions.txt', shlex.quote(wsl_path(out / variant / 'versions.txt')))
    result = subprocess.run(['C:/Windows/System32/wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c',
                             'set -eu\ncd ' + shlex.quote(wsl_path(ROOT)) + '\n' + command], capture_output=True)
    (out / variant / 'build.log').write_bytes(result.stdout + result.stderr)
    result.check_returncode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    vm.OUT, vm.PROBE = out, PROBE
    driver = vm.DRIVER.replace('#include <stdio.h>', '#include <stdio.h>\n#include <stdint.h>\n#include <sys/mman.h>\n#include "' + wsl_path(ROOT / 'mod/metol/remote/custom_key_context.h') + '"')
    driver = ('#undef _TIME_BITS\n#define _TIME_BITS 32\n'
              '#undef _FILE_OFFSET_BITS\n#define _FILE_OFFSET_BITS 64\n'
              '#include "' + wsl_path(ROOT / 'mod/metol/remote/compat.c') + '"\n' + driver)
    driver = driver.replace('Window stock=XCreateSimpleWindow', CAMERA_SETUP + '\n        Window stock=XCreateSimpleWindow')
    driver = driver.replace('for(;;){XEvent event;XNextEvent(d,&event);}', CAMERA_LOOP)
    driver = driver.replace('    if(argc==2) {', TAP_DRIVER + '\n    if(argc==2 && !strcmp(argv[1],"focus")){printf("%lu\\n",w);XCloseDisplay(d);return 0;}\n    if(argc==2) {', 1)
    driver = driver.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    driver = driver.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.DRIVER = driver
    for variant in ('fixed',):
        build_remote(out, variant)
    (out / 'fn.c').write_text(KEY_DRIVER, encoding='utf-8')
    command = f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" -O2 -I"$tc/include" -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/lib" {shlex.quote(wsl_path(out / 'fn.c'))} {shlex.quote(wsl_path(ROOT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(wsl_path(out / 'fn.arm'))}
'''
    built = subprocess.run(['C:/Windows/System32/wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-c', command], capture_output=True)
    (out / 'fn-build.log').write_bytes(built.stdout + built.stderr)
    built.check_returncode()
    vm.EXTRA_FILES = {
        'perf/remote-fixed.arm': (out / 'remote-fixed.arm').read_bytes(),
        'perf/fn.arm': (out / 'fn.arm').read_bytes(),
        # The service panel wrapper captures the REAL deployment launcher's
        # stderr into the evidence log; panel.sh itself is unchanged.
        'perf/mod/mfw-panel.sh': b'#!/bin/sh\nexec /bin/sh /perf/mod/panel-real.sh >>/perf/fn-key.log 2>&1\n',
        'perf/mod/panel-real.sh': (ROOT / 'mod/deployment/mfw/panel.sh').read_bytes(),
        'perf/mod/bin/lut_engine.arm': b'#!/bin/sh\nprintf "%s\\n" "$*" >>/perf/actions.txt\nexit 0\n',
        'perf/mod/lut-panel.sh': b'#!/bin/sh\nexec /perf/mod/bin/nxui.arm --live --lut --root /perf/mod --engine /perf/mod/bin/lut_engine.arm --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf >>/perf/fn-key.log 2>&1\n',
        'perf/mod/looks.txt': b'nx300-fuji-classic-chrome|Classic Chrome|0|0|0|0|0|0|0\nnx300-hard-mono|Hard Mono|0|0|0|0|0|0|0\n',
    }
    archive = ROOT / 'outputs/looks/lightroom-import/on-camera-test/nx300m-lut-color-test.zip'
    with ZipFile(archive) as package:
        for look in ('nx300-fuji-classic-chrome', 'nx300-hard-mono'):
            path = f'nx300m-mod/looks/{look}/table.d4lut'
            vm.EXTRA_FILES['perf/mod/' + path.removeprefix('nx300m-mod/')] = package.read(path)
    # prepare compiles the driver before constructing the overlay.
    original_prepare = arm_vm.cpio_add
    def add(stream, name, mode, data=b''):
        original_prepare(stream, name, mode, data)
        if name == 'perf/close.arm':
            original_prepare(stream, 'usr/bin/di-camera-app-nx300', 0o100755, data)
    arm_vm.cpio_add = add
    try:
        vm.prepare()
    finally:
        arm_vm.cpio_add = original_prepare
    started = False
    report = dict(passed=False, scope='ARM service-panel -> LUT page -> OK/Fn/MENU control flow in playback with videosrc=0; '
                                     'real deployment panel.sh, synthetic camera state, engine recorder',
                  camera_hardware_verified=False, card_written=False)
    try:
        vm.call('start'); started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Private ARM VM startup')
        execution = vm.call('exec', '/bin/sh -x /perf/probe.sh 2>/perf/trace.log')
        arm_vm.write_json(out / 'execution.json', execution)
        for name in ('trace.log', 'stock.log', 'remote-fixed.log', 'fn-key.log', 'actions.txt', 'P-short.log',
                     'P-ok.log', 'P-ok-actions.txt', 'P-fn.log', 'P-fn-actions.txt',
                     'P-menu.log', 'P-menu-actions.txt', 'V-ok.log', 'V-ok-actions.txt'):
            try:
                collect(out, name)
            except RuntimeError:
                pass  # Preserve earlier failure evidence when a later case was not reached.
        report.update(guest_exit=execution['exit_code'], output=execution['output'])
        if execution['exit_code'] or 'PLAYBACK_MFW_LUT_VM_PASS' not in execution['output']:
            raise RuntimeError('Playback service-panel LUT regression failed; see execution.json and trace.log')
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
