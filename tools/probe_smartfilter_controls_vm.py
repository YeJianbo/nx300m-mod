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
    enable-s2-stock) printf 'smartfilter\n' > /var/run/nx300m-preview-backend ;;
    probe-once) [ ! -e /perf/reject-preview ] || { printf 'fixture rejected preview\n' >&2; exit 66; } ;;
    diag-export)
        if [ -e /perf/no-diag ]; then printf '{"command":"diag-export","status":"no_data","snapshot_created":false,"persisted":false}\n'; exit 3; fi
        if [ -e /perf/io-error ]; then printf '{"command":"diag-export","status":"export_failed","snapshot_created":true,"persisted":false,"error_stage":"append","error_operation":"write","error_reason":"syscall_failed","error_errno":28,"error_errno_valid":true,"error_errno_text":"No space left on device"}\n'; exit 3; fi
        printf '{"command":"diag-export","status":"persisted","snapshot_created":true,"persisted":true}\n'; exit 0 ;;
    restore)
        if [ -e /perf/hold-softstop ]; then
            : > /perf/softstop-entered
            while [ -e /perf/hold-softstop ]; do usleep 100000; done
        fi
        [ ! -e /perf/fail-softstop ] || exit 5
        rm -f /var/run/nx300m-preview-backend ;;
    status) printf '{"installed":true,"look_id":"identity","strength":100,"requested_generation":1,"applied_generation":1,"rendered":1,"passthrough":0,"last_error":0}\n'; exit 0 ;;
    *) exit 64 ;;
esac
printf '{"fixture":true,"command":"%s"}\n' "$1"
'''

PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod NX_STATE_DIR=/perf/state
export NX300M_MOD_SOURCE_ROOT=/perf/source
mkdir -p /perf/source
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/source)
mkdir -p /perf/state
attempts=0
until /perf/close.arm ping; do
    attempts=$((attempts+1))
    [ "$attempts" -lt 100 ] || { printf 'X display did not become ready\n' >&2; exit 1; }
    usleep 100000
done
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
    wait_log "$label" '^READY '
    /perf/close.arm key Down Return
    wait_log "$label" 'MENU file=.*/gui_preview.NX300M items=7'
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
wait_log "$label" '^READY '
/perf/close.arm /perf/default-menu.ppm
finished
# Old continuous/S1 actions remain rejected even if invoked outside the menu.
for pair in preview-enable:not-enabled preview-monochrome:monochrome preview-monochrome-timing:monochrome-timing preview-sync:sync; do
    set +e
    /bin/sh /perf/mod/mfw-panel-actions.sh "${pair%:*}" > "/perf/${pair#*:}.log" 2>&1
    refused=$?
    set -e
    [ "$refused" != 0 ]
    grep -q '探针包拒绝' "/perf/${pair#*:}.log"
done
open_preview enable
/perf/close.arm snapshot /perf/menu.ppm
choose
finished
grep -q '^smartfilter$' /var/run/nx300m-preview-backend
open_preview identity
choose Right
finished
grep -q '^probe-once$' /perf/commands.log
: > /perf/reject-preview
open_preview rejected
choose Right
wait_log "$label" '^BACKEND kind=5 ok=0'
grep -q 'fixture rejected preview' "/perf/$label.log"
/perf/close.arm key Menu
finished
rm /perf/reject-preview
open_preview status
choose Down
wait_log "$label" 'output=预览模块：已启用'
kill -0 "$panel"
/perf/close.arm key Menu
finished
open_preview restore
choose Down Down
finished
[ ! -e /var/run/nx300m-preview-backend ]
open_preview s2-enable
choose
finished
grep -q '^enable-s2-stock$' /perf/commands.log
open_preview diag-export
choose Down Right
finished
grep -q '^diag-export$' /perf/commands.log
: > /perf/no-diag
open_preview diag-no-data
choose Down Right
finished
grep -q '没有驻留诊断数据' /perf/diag-no-data.log
! grep -q '快照已保留' /perf/diag-no-data.log
rm /perf/no-diag
: > /perf/io-error
open_preview diag-io-error
choose Down Right
wait_log "$label" '^BACKEND kind=5 ok=0'
grep -q '"error_stage":"append"' /perf/diag-io-error.log
grep -q '"error_errno":28' /perf/diag-io-error.log
! grep -q 'S2 诊断已保存' /perf/diag-io-error.log
kill -0 "$panel"
/perf/close.arm key Menu
finished
rm /perf/io-error
open_preview s2-restore
choose Down Down
finished
open_preview back
choose Down Down Right
wait_log "$label" 'MENU file=.*/gui_mfw.NX300M items=8'
/perf/close.arm key Menu
finished
for operation in enable-s2-stock probe-once status restore diag-export; do
    grep -q "^$operation$" /perf/commands.log
done

# Reproduce the real card log: focus was PointerRoot after a notice vanished,
# so the old focus-ancestor-only lookup refused every subsequent menu open.
/perf/close.arm pointer-root
label=root-recovery
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY '
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
wait_log "$label" '^READY '
grep -q 'PANEL_FOCUS_SNAPSHOT .*transient=1' "/perf/$label.log"
wait "$flash"
/perf/close.arm key Menu
finished
for label in reopen-1 reopen-2; do
    /bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
    panel=$!
    wait_log "$label" '^READY '
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
printf '{"diag_export_dispatch":true,"diag_no_data_truthful":true,"diag_io_error_visible":true,"probe_once_dispatch":true,"s2_enable_dispatch":true,"menu_navigation":true,"continuous_actions_rejected":true,"success_returns_to_viewfinder":true,"failure_stays_operable":true,"status_stays_visible":true,"back":true,"stock_focus_restored":true,"orphan_focus_recovery":true,"notice_overlap_returns_stock":true,"reopen_after_notice":true,"foreign_focus_not_stolen":true}\n' > /perf/results.json
kill "$stock"
trap - EXIT
'''


LIVE_ACTIONS = r'''
# S1 stays unavailable; sync must not enable a continuous backend.
for action in preview-enable preview-sync; do
    set +e
    /bin/sh /perf/mod/mfw-panel-actions.sh "$action" > "/perf/$action.log" 2>&1
    refused=$?
    set -e
    [ "$refused" != 0 ]
done
open_preview monochrome
/perf/close.arm snapshot /perf/menu.ppm
choose
finished
grep -q '^test-monochrome$' /perf/commands.log
open_preview monochrome-timing
choose Right
finished
grep -q '^test-monochrome-timing$' /perf/commands.log
open_preview current-lut
choose Down
finished
grep -q '^enable-s2$' /perf/commands.log
open_preview sync
choose Down Right
finished
grep -q '^sync$' /perf/commands.log
: > /perf/reject-preview
open_preview rejected
choose
wait_log "$label" '^BACKEND kind=5 ok=0'
grep -q 'fixture rejected preview' "/perf/$label.log"
/perf/close.arm key Menu
finished
rm /perf/reject-preview
open_preview status
choose Down Down
wait_log "$label" 'output=预览模块：已启用'
kill -0 "$panel"
/perf/close.arm key Menu
finished
open_preview restore
choose Down Down Right
finished
[ ! -e /var/run/nx300m-preview-backend ]
open_preview back
choose Down Down Down
wait_log "$label" 'MENU file=.*/gui_mfw.NX300M items=8'
/perf/close.arm key Menu
finished
for operation in test-monochrome test-monochrome-timing enable-s2 sync status restore; do
    grep -q "^$operation$" /perf/commands.log
done
'''
LIVE_CHECKS = {
    key: True for key in (
        'continuous_monochrome_dispatch', 'continuous_monochrome_timing_dispatch',
        'current_lut_dispatch', 'sync_dispatch',
        's1_rejected', 'sync_does_not_enable', 'menu_navigation',
        'success_returns_to_viewfinder', 'failure_stays_operable', 'status_stays_visible',
        'back', 'stock_focus_restored', 'orphan_focus_recovery',
        'notice_overlap_returns_stock', 'reopen_after_notice', 'foreign_focus_not_stolen')
}

QUICK_PROBE = r'''
# Opening any menu must stay usable without starting a status subprocess.
for name in generic root-recovery; do
    ! grep -q '^BACKEND kind=5' "/perf/$name.log"
done
: > /perf/mod/PREVIEW_QUICK_PANEL
label=quick-full
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
/perf/close.arm snapshot /perf/quick-menu.ppm
choose Down Down
wait_log "$label" 'MENU file=.*/gui_mfw.NX300M items=8'
/perf/close.arm key Menu
finished

label=quick-stop
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
choose
finished
wait_log state/preview-softstop '^NX_PREVIEW_STOP end rc=0 '
grep -q '^restore$' /perf/commands.log

label=quick-enable
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
choose Down
finished
grep -q '^smartfilter$' /var/run/nx300m-preview-backend

# Reproduce the physical log's long restore + rc=5. The menu must open and
# reopen while that real shell job is blocked, without enqueuing more restores.
stop_count=$(grep -c '^restore$' /perf/commands.log)
: > /perf/hold-softstop
: > /perf/fail-softstop
label=slow-stop
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
attempts=0
until [ -e /perf/softstop-entered ]; do
    attempts=$((attempts+1)); [ "$attempts" -lt 100 ]; usleep 100000
done
/perf/close.arm key Menu
finished
label=busy-reopen
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
/perf/close.arm key Menu
finished
[ "$(grep -c '^restore$' /perf/commands.log)" -eq "$((stop_count+1))" ]
rm /perf/hold-softstop
wait_log state/preview-softstop '^NX_PREVIEW_STOP end rc=5 '
label=failed-reopen
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
[ "$(grep -c '^restore$' /perf/commands.log)" -eq "$((stop_count+1))" ]
rm /perf/fail-softstop
choose
finished
attempts=0
while [ -e /var/run/nx300m-preview-backend ]; do
    attempts=$((attempts+1)); [ "$attempts" -lt 100 ]; usleep 100000
done

label=quick-enable-again
/bin/sh /perf/mod/mfw-panel.sh > "/perf/$label.log" 2>&1 &
panel=$!
wait_log "$label" '^READY .*panel=560x240'
choose Down
finished

# Reopening during preview restores before display/font/UI checks, even when
# there is no X server and the UI executable itself is inaccessible.
mv /perf/mod/bin/nxui.arm /perf/mod/bin/nxui.saved
DISPLAY=:199 /bin/sh /perf/mod/mfw-panel.sh > /perf/headless-stop.log 2>&1
mv /perf/mod/bin/nxui.saved /perf/mod/bin/nxui.arm
grep -q '^NX_FN_PANEL background-preview-restore$' /perf/headless-stop.log
! grep -q '^READY ' /perf/headless-stop.log
attempts=0
while [ -e /var/run/nx300m-preview-backend ]; do
    attempts=$((attempts+1)); [ "$attempts" -lt 100 ]; usleep 100000
done
[ ! -e /var/run/nx300m-preview-backend ]
/perf/close.arm focus-stock
# A broken card-log path must not stop the RAM worker from running restore.
printf 'smartfilter\n' > /var/run/nx300m-preview-backend
NX_FN_KEY_LOG=/missing/log/target /bin/sh /perf/mod/preview-softstop.sh request > /perf/no-card-log.log
attempts=0
while [ -e /var/run/nx300m-preview-backend ]; do
    attempts=$((attempts+1)); [ "$attempts" -lt 100 ]; usleep 100000
done
grep -q '^NX_PREVIEW_STOP end rc=0 ' /perf/state/preview-softstop.log
rm /perf/mod/PREVIEW_QUICK_PANEL
'''


def collect_evidence(out):
    """Retain existing logs on failure, not merely the shell's last assertion."""
    command = r'''set -eu
cd /perf
: > controls-evidence-files.txt
for path in *.log *.json *.ppm *.ready; do
    [ ! -f "$path" ] || printf '%s\n' "$path" >> controls-evidence-files.txt
done
cp /perf/mod/smartfilter-preview.sh preview-boundary.captured.sh
cp /perf/mod/mfw-panel-actions.sh panel-actions.captured.sh
cp /perf/actual-smartfilter-preview.sh actual-smartfilter-preview.captured.sh
printf '%s\n' preview-boundary.captured.sh panel-actions.captured.sh actual-smartfilter-preview.captured.sh >> controls-evidence-files.txt
if [ -f /var/run/nx300m-preview-backend ]; then
    cp /var/run/nx300m-preview-backend preview-backend.captured.txt
    printf '%s\n' preview-backend.captured.txt >> controls-evidence-files.txt
fi
env > controls-environment.txt
printf '%s\n' controls-environment.txt >> controls-evidence-files.txt
set --
while IFS= read -r name; do set -- "$@" "$name"; done < controls-evidence-files.txt
/rescue/busybox tar -czf controls-evidence.tar.gz "$@"
'''
    packed = vm.call('exec', command)
    if packed['exit_code']:
        raise RuntimeError('Controls evidence archive exit ' + str(packed['exit_code']))
    collect(out, 'controls-evidence-files.txt')
    names = (out / 'controls-evidence-files.txt').read_text().splitlines()
    if not names or any(Path(name).name != name or name in ('.', '..') for name in names):
        raise ValueError('Invalid controls evidence inventory')
    collect(out, 'controls-evidence.tar.gz')
    with tarfile.open(out / 'controls-evidence.tar.gz') as evidence:
        for name in names:
            member = evidence.getmember(name)
            if not member.isfile():
                raise ValueError('Controls evidence is not a regular file: ' + name)
            (out / name).write_bytes(evidence.extractfile(member).read())
    return names


def main():
    global OUT, PROBE, BOUNDARY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--quick-panel', action='store_true', help='Also check compact layout and headless soft stop')
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    if args.quick_panel and not args.continuous:
        parser.error('--quick-panel requires --continuous')
    if args.quick_panel:
        insertion = PROBE.index('# Stock-tree recovery is for missing focus only.')
        PROBE = PROBE[:insertion] + QUICK_PROBE + '\n' + PROBE[insertion:]
        LIVE_CHECKS.update(no_automatic_status_job=True, compact_navigation_and_full_menu=True,
                           compact_softstop_dispatch=True, compact_timing_dispatch=True,
                           headless_softstop_without_x_or_ui=True, softstop_does_not_hold_menu_pid=True,
                           menu_reopens_during_stop=True, failed_stop_does_not_auto_retry=True,
                           manual_stop_retry_background=True, stop_survives_card_log_open_failure=True)
    if args.continuous:
        FILES.update({
            'nx300m-mod/mfw-panel.sh': ROOT / 'mod/deployment/mfw/fn-panel.sh',
            'nx300m-mod/fn-panel/mfw-panel-actions.sh': ROOT / 'mod/deployment/mfw/fn-panel-actions.sh',
            'nx300m-mod/fn-panel/gui_preview_quick.NX300M': ROOT / 'mod/deployment/mfw/gui_preview_quick.NX300M',
            'nx300m-mod/preview-softstop.sh': ROOT / 'mod/deployment/mfw/preview-softstop.sh',
        })
        FILES['nx300m-mod/smartfilter-preview.sh'] = ROOT / 'mod/deployment/mfw/smartfilter-live-preview.sh'
        for name in ('nx300m-mod/gui_preview.NX300M', 'nx300m-mod/fn-panel/gui_preview.NX300M'):
            FILES[name] = ROOT / 'mod/deployment/mfw/gui_preview_live.NX300M'
        PROBE = PROBE.replace('gui_preview.NX300M items=7', 'gui_preview.NX300M items=8')
        begin = PROBE.index('# Old continuous/S1 actions')
        end = PROBE.index('# Reproduce the real card log:')
        PROBE = PROBE[:begin] + LIVE_ACTIONS + '\n' + PROBE[end:]
        begin = PROBE.index("printf '{\"diag_export_dispatch\"")
        end = PROBE.index(' > /perf/results.json', begin)
        PROBE = PROBE[:begin] + "printf '%s\\n' '" + json.dumps(LIVE_CHECKS) + "'" + PROBE[end:]
        BOUNDARY = BOUNDARY.replace(
            "    enable-s2-stock) printf 'smartfilter\\n' > /var/run/nx300m-preview-backend ;;",
            "    enable-s2|test-monochrome|test-monochrome-timing) [ ! -e /perf/reject-preview ] || { printf 'fixture rejected preview\\n' >&2; exit 66; }; printf 'smartfilter\\n' > /var/run/nx300m-preview-backend ;;\\n"
            "    sync) [ -f /var/run/nx300m-preview-backend ] || exit 65 ;;".replace(';;\\n', ';;\n'))
    vm.OUT = OUT
    vm.SOCKET = '/tmp/nx300m-preview-controls-' + OUT.name
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', KEYS + '\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    Window w; int revert;', FOCUS_DRIVER + '\n    Window w; int revert;', 1)
    # The inherited screenshot helper sends Menu after capture. A screenshot of
    # an actionable menu must not silently close it before choose()/Return.
    vm.DRIVER = vm.DRIVER.replace(
        '    if(argc==2) {\n        XWindowAttributes',
        '    if(argc==2 || (argc==3 && !strcmp(argv[1],"snapshot"))) {\n        XWindowAttributes', 1)
    vm.DRIVER = vm.DRIVER.replace('FILE *out=fopen(argv[1],"wb");',
                                  'FILE *out=fopen(argc==3?argv[2]:argv[1],"wb");', 1)
    vm.DRIVER = vm.DRIVER.replace('        fclose(out);XDestroyImage(image);',
                                  '        fclose(out);XDestroyImage(image);\n        if(argc==3){XCloseDisplay(d);return 0;}', 1)
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
    entries['perf/mod/smartfilter-preview.sh'] = (0o100755, BOUNDARY.encode())
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
    collected = False
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
        # Return control to the guest shell before native_exec's 120s limit,
        # so a blocked UI cannot also consume every evidence-recovery command.
        execution = vm.call('exec', '/rescue/busybox timeout 110 /bin/sh -x /perf/probe.sh 2>/perf/probe-trace.log')
        arm_vm.write_json(OUT / 'execution.json', execution)
        report['evidence_files'] = collect_evidence(OUT)
        collected = True
        if execution['exit_code']:
            raise RuntimeError('Menu controls probe exit ' + str(execution['exit_code']))
        from PIL import Image
        Image.open(OUT / 'menu.ppm').transpose(Image.Transpose.ROTATE_90).save(OUT / 'menu.png')
        Image.open(OUT / 'default-menu.ppm').transpose(Image.Transpose.ROTATE_90).save(OUT / 'default-menu.png')
        if args.quick_panel:
            Image.open(OUT / 'quick-menu.ppm').transpose(Image.Transpose.ROTATE_90).save(OUT / 'quick-menu.png')
        checks = json.loads((OUT / 'results.json').read_text())
        if not checks or any(value is not True for value in checks.values()):
            raise ValueError('Incomplete controls results')
        report.update(passed=True, checks=checks)
    except BaseException as error:
        report['failure'] = str(error)
        if started and not collected:
            try:
                report['evidence_files'] = collect_evidence(OUT)
            except Exception as collection_error:
                report['evidence_failure'] = str(collection_error)
                # Preserve small critical diagnostics even if archive transfer failed.
                for name in ('probe-trace.log', 'enable.log', 'commands.log'):
                    try:
                        collect(OUT, name)
                    except Exception:
                        pass
        raise
    finally:
        if started:
            try:
                report['stop'] = vm.call('stop')
                report['private_vm_stopped'] = 'return' in report['stop']
                if report['private_vm_stopped']:
                    (OUT / 'initramfs.cpio.gz').unlink(missing_ok=True)
            except Exception as stop_error:
                report['private_vm_stopped'] = False
                report['stop_failure'] = str(stop_error)
        arm_vm.write_json(OUT / 'report.json', report)
    pointer = 'controls-live-latest.json' if args.continuous else 'controls-menu-latest.json'
    arm_vm.write_json(ROOT / 'outputs/deployment/smartfilter-preview' / pointer, {'report': str(OUT / 'report.json')})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
