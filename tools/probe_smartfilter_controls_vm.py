"""Exercise the camera menu in ARM Xvfb; replace only the preview command boundary."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import time

import arm_vm
from build_stock_app_loader import read_cpio
from probe_menu_entry_vm import collect
import probe_menu_startup_vm as vm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/deployment/smartfilter-preview/controls-vm'
FILES = {
    'nx300m-mod/bin/nxui.arm': ROOT / 'outputs/metol/nxui.arm',
    'nx300m-mod/mfw-panel-actions.sh': ROOT / 'mod/deployment/mfw/panel-actions.sh',
    'nx300m-mod/gui_mfw.NX300M': ROOT / 'mod/deployment/mfw/gui_mfw.NX300M',
    'nx300m-mod/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview.NX300M',
    'nx300m-mod/fn-panel/gui_mfw.NX300M': ROOT / 'mod/deployment/mfw/fn-gui_mfw.NX300M',
    'nx300m-mod/fn-panel/gui_preview.NX300M': ROOT / 'mod/deployment/mfw/gui_preview.NX300M',
    'nx300m-mod/smartfilter-preview.sh': ROOT / 'mod/deployment/mfw/smartfilter-preview.sh',
}

KEYS = r'''
    if(argc>=2 && !strcmp(argv[1],"focus-stock")) {
        char *name=NULL;XFetchName(d,w,&name);
        int ok=name && !strcmp(name,"NX-TEST-STOCK-PARENT");
        if(name)XFree(name);XCloseDisplay(d);return ok?0:21;
    }
    if(argc>=2 && !strcmp(argv[1],"focus-foreign")) {
        char *name=NULL;XFetchName(d,w,&name);
        int ok=name && !strcmp(name,"NX-TEST-FOREIGN");
        if(name)XFree(name);XCloseDisplay(d);return ok?0:21;
    }
    if(argc>=3 && !strcmp(argv[1],"key")) {
        for(int i=2;i<argc;i++) {
            XEvent e={0};e.xkey.display=d;e.xkey.window=w;
            e.xkey.keycode=XKeysymToKeycode(d,XStringToKeysym(argv[i]));
            if(!e.xkey.keycode)return 22;
            e.type=KeyPress;XSendEvent(d,w,False,KeyPressMask,&e);
            e.type=KeyRelease;XSendEvent(d,w,False,KeyReleaseMask,&e);
        }
        XSync(d,False);XCloseDisplay(d);return 0;
    }
'''

FOCUS_DRIVER = r'''
    if(argc==2 && !strcmp(argv[1],"pointer-root")) {
        XSetInputFocus(d,PointerRoot,RevertToPointerRoot,CurrentTime);
        XSync(d,False);XCloseDisplay(d);return 0;
    }
    if(argc==2 && !strcmp(argv[1],"foreign-parent")) {
        Window foreign=XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,480,800,0,0,0);
        XStoreName(d,foreign,"NX-TEST-FOREIGN");
        XMapRaised(d,foreign);XSetInputFocus(d,foreign,RevertToPointerRoot,CurrentTime);XSync(d,False);
        FILE *ready=fopen("/perf/foreign-parent.ready","w");if(!ready)return 20;
        fprintf(ready,"%lu\n",foreign);fclose(ready);
        for(;;){XEvent event;XNextEvent(d,&event);}
    }
'''

BOUNDARY = r'''#!/bin/sh
set -eu
printf '%s\n' "$1" >> /perf/commands.log
case $1 in
    enable-stock) printf 'smartfilter\n' > /var/run/nx300m-preview-backend ;;
    test-identity) [ ! -e /perf/reject-preview ] || { printf 'fixture rejected preview\n' >&2; exit 66; } ;;
    test-monochrome|sync) : ;;
    restore) rm -f /var/run/nx300m-preview-backend ;;
    status) printf '{"installed":true,"look_id":"identity","strength":100,"requested_generation":1,"applied_generation":1,"rendered":1,"passthrough":0,"last_error":0}\n'; exit 0 ;;
    *) exit 64 ;;
esac
printf '{"fixture":true,"command":"%s"}\n' "$1"
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
export NX300M_MOD_SOURCE_ROOT=/perf/source
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/source)
mkdir -p /perf/state
until /perf/close.arm ping; do sleep 1; done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" 2>/dev/null || :' EXIT
wait_log() {
    attempts=0
    until grep -q "$2" "/perf/$1.log"; do
        attempts=$((attempts+1)); [ "$attempts" -lt 100 ] || { cat "/perf/$1.log"; exit 1; }
        usleep 100000
    done
}
open_preview() {
    label=$1
    /bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
    panel=$!
    wait_log "$label" '^BACKEND kind=5 ok=1'
    /perf/close.arm key Down Return
    wait_log "$label" 'MENU file=.*/gui_preview.NX300M items=8'
}
finished() {
    wait "$panel"
    grep -q '^EXIT ' "/perf/$label.log"
    /perf/close.arm focus-stock
}
choose() { /perf/close.arm key "$@" Return; }
label=generic
/bin/sh /perf/mod/panel-default.sh > /perf/generic.log 2>&1 &
panel=$!
wait_log "$label" '^BACKEND kind=5 ok=1'
/perf/close.arm /perf/default-menu.ppm
finished
open_preview not-enabled
choose Right
wait_log "$label" '^BACKEND kind=5 ok=0'
grep -q '请先选择' "/perf/$label.log"
/perf/close.arm /perf/menu.ppm
finished
open_preview enable
choose
finished
grep -q '^smartfilter$' /var/run/nx300m-preview-backend
open_preview identity
choose Right
finished
open_preview monochrome
choose Down
finished
open_preview sync
choose Down Right
finished
: > /perf/reject-preview
open_preview rejected
choose Right
wait_log "$label" '^BACKEND kind=5 ok=0'
grep -q 'fixture rejected preview' "/perf/$label.log"
/perf/close.arm key Menu
finished
rm /perf/reject-preview
open_preview status
choose Down Down Right
wait_log "$label" 'output=预览模块：已启用'
kill -0 "$panel"
/perf/close.arm key Menu
finished
open_preview restore
choose Down Down
finished
[ ! -e /var/run/nx300m-preview-backend ]
open_preview back
choose Down Down Down
wait_log "$label" 'MENU file=.*/gui_mfw.NX300M items=8'
/perf/close.arm key Menu
finished
for operation in enable-stock test-identity test-monochrome sync status restore; do
    grep -q "^$operation$" /perf/commands.log
done

# Reproduce the real card log: focus was PointerRoot after a notice vanished,
# so the old focus-ancestor-only lookup refused every subsequent menu open.
/perf/close.arm pointer-root
label=root-recovery
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^BACKEND kind=5 ok=1'
grep -q 'PANEL_PARENT_LOOKUP source=stock-stack ' "/perf/$label.log"
grep -q 'PANEL_PARENT mode=stock-child ' "/perf/$label.log"
/perf/close.arm key Menu
finished

# Use the actual nxui notice window and its independent flash lock. The main
# menu must not retain that short-lived window as its return-focus target.
NXUI_LOCK_TAG=-flash NXUI_LUT_NOTICE='LUT notice overlap' /perf/mod/bin/nxui.arm \
    --lut --dry-run --root /perf/mod --lifetime-ms 15000 \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/flash.log 2>&1 &
flash=$!
label=flash
wait_log "$label" '^READY '
label=notice-overlap
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^BACKEND kind=5 ok=1'
grep -q 'PANEL_FOCUS_SNAPSHOT .*transient=1' "/perf/$label.log"
wait "$flash"
/perf/close.arm key Menu
finished
for label in reopen-1 reopen-2; do
    /bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
    panel=$!
    wait_log "$label" '^BACKEND kind=5 ok=1'
    /perf/close.arm key Menu
    finished
done

# Stock-tree recovery is for missing focus only. An active foreign dialog
# still owns focus and must not be displaced by opening a MOD window.
/perf/close.arm foreign-parent > /perf/foreign-parent.log 2>&1 &
foreign=$!
attempts=0
until [ -s /perf/foreign-parent.ready ]; do
    attempts=$((attempts+1)); [ "$attempts" -lt 100 ]; usleep 100000
done
set +e
/bin/sh /perf/mod/mfw-panel.sh > /perf/foreign-rejected.log 2>&1
foreign_status=$?
set -e
[ "$foreign_status" -eq 4 ]
/perf/close.arm focus-foreign
kill "$foreign"
wait "$foreign" || :
for script in /perf/mod/mfw-panel-actions.sh /perf/actual-smartfilter-preview.sh; do /bin/sh -n "$script"; done
printf '{"menu_navigation":true,"not_enabled_rejected":true,"enable_identity_monochrome_sync_restore":true,"success_returns_to_viewfinder":true,"failure_stays_operable":true,"status_stays_visible":true,"back":true,"stock_focus_restored":true,"orphan_focus_recovery":true,"notice_overlap_returns_stock":true,"reopen_after_notice":true,"foreign_focus_not_stolen":true}\n' > /perf/results.json
kill "$stock"
trap - EXIT
'''


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    OUT = parser.parse_args().output_dir.resolve()
    vm.OUT = OUT
    vm.SOCKET = '/tmp/nx300m-preview-controls-' + OUT.name
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', KEYS + '\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    Window w; int revert;', FOCUS_DRIVER + '\n    Window w; int revert;', 1)
    vm.PROBE = PROBE
    vm.prepare()
    entries = read_cpio(gzip.decompress((OUT / 'overlay.cpio.gz').read_bytes()))
    for name, source in FILES.items():
        data = source.read_bytes()
        if name.endswith('.sh'):
            data = data.replace(b'\r\n', b'\n')
        relative = name.removeprefix('nx300m-mod/')
        entries['perf/mod/' + relative] = (0o100755 if name.endswith(('.sh', '.arm')) else 0o100644, data)
    entries['perf/mod/mfw-panel.sh'] = (0o100755, (ROOT / 'mod/deployment/mfw/fn-panel.sh').read_bytes())
    entries['perf/mod/panel-default.sh'] = (0o100755, (ROOT / 'mod/deployment/mfw/panel.sh').read_bytes())
    entries['perf/mod/fn-panel/mfw-panel-actions.sh'] = (0o100755, (ROOT / 'mod/deployment/mfw/fn-panel-actions.sh').read_bytes())
    entries['perf/source/smartfilter-preview.sh'] = (0o100755, BOUNDARY.encode())
    entries['perf/actual-smartfilter-preview.sh'] = (0o100755, FILES['nx300m-mod/smartfilter-preview.sh'].read_bytes())
    directories = {p.as_posix() for name in entries for p in Path(name).parents if str(p) != '.'}
    overlay = io.BytesIO()
    for name in sorted(directories):
        arm_vm.cpio_add(overlay, name, 0o40755)
    for name, (mode, data) in entries.items():
        if name != 'TRAILER!!!' and mode & 0o170000 != 0o040000:
            arm_vm.cpio_add(overlay, name, mode, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    compressed = gzip.compress(overlay.getvalue(), mtime=0)
    (OUT / 'overlay.cpio.gz').write_bytes(compressed)
    with (arm_vm.WORK / 'initramfs.cpio.gz').open('rb') as source, (OUT / 'initramfs.cpio.gz').open('wb') as target:
        shutil.copyfileobj(source, target)
        target.write(compressed)
    report = {'passed': False, 'private_vm_stopped': False,
              'scope': 'Current ARM nxui + real menu/action wrappers; preview loader command boundary is a fixture',
              'physical_display_or_isp_verified': False,
              'candidate_files': {name: hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n') if name.endswith('.sh') else source.read_bytes()).hexdigest() for name, source in FILES.items()}}
    started = False
    try:
        vm.call('start')
        started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if (OUT / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (OUT / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise TimeoutError('Private menu VM startup')
        execution = vm.call('exec', '/bin/sh -x /perf/probe.sh 2>/perf/probe-trace.log')
        arm_vm.write_json(OUT / 'execution.json', execution)
        if execution['exit_code']:
            collect(OUT, 'probe-trace.log')
            raise RuntimeError('Menu controls probe exit ' + str(execution['exit_code']))
        names = ('probe-trace.log', 'commands.log', 'results.json', 'generic.log', 'not-enabled.log', 'enable.log', 'identity.log', 'monochrome.log', 'sync.log', 'rejected.log', 'status.log', 'restore.log', 'back.log', 'root-recovery.log', 'flash.log', 'notice-overlap.log', 'reopen-1.log', 'reopen-2.log', 'foreign-rejected.log', 'menu.ppm', 'default-menu.ppm')
        packed = vm.call('exec', '/rescue/busybox tar -czf /perf/controls-evidence.tar.gz -C /perf ' + ' '.join(names))
        if packed['exit_code']:
            raise RuntimeError('Menu evidence archive exit ' + str(packed['exit_code']))
        collect(OUT, 'controls-evidence.tar.gz')
        with tarfile.open(OUT / 'controls-evidence.tar.gz') as evidence:
            for name in names:
                (OUT / name).write_bytes(evidence.extractfile(name).read())
        from PIL import Image
        Image.open(OUT / 'menu.ppm').transpose(Image.Transpose.ROTATE_90).save(OUT / 'menu.png')
        Image.open(OUT / 'default-menu.ppm').transpose(Image.Transpose.ROTATE_90).save(OUT / 'default-menu.png')
        report.update(passed=True, checks=json.loads((OUT / 'results.json').read_text()))
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (OUT / 'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(OUT / 'report.json', report)
    arm_vm.write_json(ROOT / 'outputs/deployment/smartfilter-preview/controls-menu-latest.json', {'report': str(OUT / 'report.json')})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
