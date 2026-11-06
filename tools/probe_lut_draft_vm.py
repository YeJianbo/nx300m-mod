"""Finite-state LUT draft/request regressions on real ARM nxui in a private VM.

Covers the 2026-11-04 handoff R1/R3 minimum regressions: per-look drafts
survive browsing keyed by look id, no implicit commits while browsing, the
leave-dialog save/discard scope, confirmed requests freeze their parameters
against a same-batch input burst, sequential parameter changes each reach the
engine argv, closing LUT is an explicit disable request, and a failed submit
keeps the draft for an explicit retry. Engine action recorder + real staging
engine fixture; no camera, firmware callback or hardware pixels.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import time
from zipfile import ZipFile

import arm_vm
import probe_menu_startup_vm as vm
from probe_lut_selection_vm import TAP_DRIVER
from probe_menu_entry_vm import collect

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''#!/bin/sh
set -eu
set -x
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod
mkdir -p /perf/mod/.state
cat > /perf/mod/looks.txt <<'CATALOGUE'
@CATALOGUE@
CATALOGUE
attempt=0
until /perf/close.arm ping; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
wait_frame() {
    attempt=0
    until grep -q '^FIRST_FRAME ' "$1"; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
    done
}
wait_backend() {
    attempt=0
    until grep -q "^BACKEND kind=1 ok=$2" "$1"; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
    done
    if [ "${3:-stay}" = exit ]; then
        attempt=0
        until ! kill -0 "$panel" 2>/dev/null; do
            attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
        done
    fi
}
# Move the list cursor (with choose()) until the named look is selected.
last_choice() { grep 'LUT_CHOICE event=select' "$1" | tail -n 1; }
select_look() {
    attempt=0
    until last_choice "$2" | grep -q "key=$1"; do
        /perf/close.arm key KP_Down
        attempt=$((attempt+1)); [ "$attempt" -lt 30 ]; /rescue/busybox usleep 80000
    done
}
# From the tuning view, walk neighbours until the named look is selected.
# $3 is the walk button: 258 358 = next look, 50 358 = previous look.
step_to() {
    attempt=0
    until last_choice "$2" | grep -q "key=$1"; do
        /perf/close.arm tap "$3" 358
        attempt=$((attempt+1)); [ "$attempt" -lt 45 ]; /rescue/busybox usleep 80000
    done
}
reset_runtime() {
    rm -f /var/run/nx300m-lut-selection /var/run/nx300m-lut-selection-session
    rm -rf /perf/mod/.state
    mkdir -p /perf/mod/.state
}

# --- Scenario 1: per-look drafts survive browsing; save-and-exit flushes all.
reset_runtime
: > /perf/actions.txt
NX_STATE_DIR=/tmp/draft-s1 /rescue/busybox timeout 30 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/tune-engine.arm \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s1.log 2>&1 &
panel=$!
wait_frame /perf/draft-s1.log
select_look nx300-hard-mono /perf/draft-s1.log
/perf/close.arm key XF86HomePage
/rescue/busybox usleep 300000
/perf/close.arm key XF86ScrollUp
/rescue/busybox usleep 200000
# Browse to another look straight from the tuning view; the mono draft must move with it.
step_to nx300-fuji-classic-chrome /perf/draft-s1.log 258
/perf/close.arm key XF86ScrollDown
/perf/close.arm key XF86ScrollDown
/rescue/busybox usleep 200000
step_to nx300-hard-mono /perf/draft-s1.log 50
grep -q '^LUT_DRAFT_RESTORE key=nx300-hard-mono strength=100 values=1,0,0,0,0,0,0$' /perf/draft-s1.log
step_to nx300-fuji-classic-chrome /perf/draft-s1.log 258
grep -q '^LUT_DRAFT_RESTORE key=nx300-fuji-classic-chrome strength=100 values=-2,0,0,0,0,0,0$' /perf/draft-s1.log
# Browsing alone never submitted anything to the camera.
[ "$(grep -c '^LUT_SUBMIT' /perf/draft-s1.log)" -eq 0 ]
# Save-and-exit: every draft goes to the card, displayed choice committed once.
/perf/close.arm key Menu
/rescue/busybox usleep 400000
/perf/close.arm tap 212 262
wait_backend /perf/draft-s1.log 1 exit
wait "$panel"
grep -q '^LUT_SUBMIT .*key=nx300-fuji-classic-chrome strength=100 tune=-2,0,0,0,0,0,0' /perf/draft-s1.log
[ "$(grep -c '^LUT_SUBMIT' /perf/draft-s1.log)" -eq 1 ]
grep -q ' 1 nx300-hard-mono 1 0 0 0 0 0 0 100$' /perf/mod/.state/lut-preset-nx300-hard-mono.[01]
grep -q ' 1 nx300-fuji-classic-chrome -2 0 0 0 0 0 0 100$' /perf/mod/.state/lut-preset-nx300-fuji-classic-chrome.[01]
NX_STATE_DIR=/tmp/draft-s1-reopen /rescue/busybox timeout 15 /perf/mod/bin/nxui.arm --dry-run --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/tune-engine.arm \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s1-reopen.log 2>&1 &
panel=$!
wait_frame /perf/draft-s1-reopen.log
grep -q 'LUT_ENTRY .*key=nx300-fuji-classic-chrome' /perf/draft-s1-reopen.log
grep -q 'LUT_TUNE key=nx300-fuji-classic-chrome values=-2,0,0,0,0,0,0' /perf/draft-s1-reopen.log
/perf/close.arm key Menu
wait "$panel"

# --- Scenario 2: a confirmed request freezes parameters against an input burst.
reset_runtime
: > /perf/actions.txt
NX_STATE_DIR=/tmp/draft-s2 /rescue/busybox timeout 30 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/slow-recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s2.log 2>&1 &
panel=$!
wait_frame /perf/draft-s2.log
select_look nx300-hard-mono /perf/draft-s2.log
/perf/close.arm key KP_Enter
# Same-batch burst right after the confirmation: navigation and wheel changes
# must not mutate the dispatching request.
for key in XF86ScrollDown KP_Down XF86ScrollDown KP_Up XF86ScrollDown; do
    /perf/close.arm key "$key"
done
wait_backend /perf/draft-s2.log 1 exit
wait "$panel"
grep -q '^LUT_SUBMIT .*key=nx300-hard-mono strength=100 tune=0,0,0,0,0,0,0' /perf/draft-s2.log
[ "$(cat /perf/actions.txt)" = 'apply nx300-hard-mono 100 0 0 0 0 0 0 0' ]

# --- Scenario 3: Fn saves presets locally, never applies or exits the panel.
reset_runtime
: > /perf/actions.txt
NX_STATE_DIR=/tmp/draft-s3 /rescue/busybox timeout 30 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s3.log 2>&1 &
panel=$!
wait_frame /perf/draft-s3.log
select_look nx300-hard-mono /perf/draft-s3.log
# Fn save returns to the list; re-enter tuning before each parameter change.
/perf/close.arm key XF86HomePage
/rescue/busybox usleep 300000
/perf/close.arm key XF86ScrollUp
/rescue/busybox usleep 200000
/perf/close.arm key XF86HomePage
attempt=0
until [ "$(grep -c '^LUT_PRESET_SAVED ' /perf/draft-s3.log)" -ge 1 ]; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
kill -0 "$panel"
/perf/close.arm key XF86HomePage
/rescue/busybox usleep 300000
/perf/close.arm key XF86ScrollUp
/rescue/busybox usleep 200000
/perf/close.arm key XF86HomePage
attempt=0
until [ "$(grep -c '^LUT_PRESET_SAVED ' /perf/draft-s3.log)" -ge 2 ]; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
done
kill -0 "$panel"
/perf/close.arm key Menu
wait "$panel"
[ ! -s /perf/actions.txt ]
grep -q ' 1 nx300-hard-mono 2 0 0 0 0 0 0 100$' /perf/mod/.state/lut-preset-nx300-hard-mono.[01]

# --- Scenario 4: a failed submit keeps the draft; retry sends it unchanged.
reset_runtime
: > /perf/actions.txt
rm -f /perf/fail-count
NX_STATE_DIR=/tmp/draft-s4 /rescue/busybox timeout 30 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/fail-recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s4.log 2>&1 &
panel=$!
wait_frame /perf/draft-s4.log
select_look nx300-hard-mono /perf/draft-s4.log
/perf/close.arm key KP_Enter
wait_backend /perf/draft-s4.log 0
kill -0 "$panel"
# The panel stayed open with the draft; retry the identical request.
/perf/close.arm key KP_Enter
wait_backend /perf/draft-s4.log 1 exit
wait "$panel"
[ "$(cat /perf/actions.txt)" = 'apply nx300-hard-mono 100 0 0 0 0 0 0 0
apply nx300-hard-mono 100 0 0 0 0 0 0 0' ]

# --- Scenario 5: closing LUT is an explicit disable request.
printf '2 /perf/mod/looks/nx300-hard-mono/table.d4lut 100\n' > /var/run/nx300m-lut-selection
: > /perf/actions.txt
NX_STATE_DIR=/tmp/draft-s5 /rescue/busybox timeout 30 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s5.log 2>&1 &
panel=$!
wait_frame /perf/draft-s5.log
grep -q '^LUT_SUBMIT' /perf/draft-s5.log && exit 91
/perf/close.arm tap 174 21
wait_backend /perf/draft-s5.log 1 exit
wait "$panel"
grep -q '^LUT_SUBMIT index=0 key=__off' /perf/draft-s5.log
[ "$(cat /perf/actions.txt)" = 'disable' ]

# --- Scenario 6: film-simulation category groups the imported film looks.
rm -f /var/run/nx300m-lut-selection /perf/mod/.state/lut-category
: > /perf/actions.txt
NX_STATE_DIR=/tmp/draft-s6 /rescue/busybox timeout 40 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s6.log 2>&1 &
panel=$!
wait_frame /perf/draft-s6.log
# From the list's first row, Up enters group focus; Rights cycle non-empty
# groups up to 9 (film simulation).
key_up() { /perf/close.arm key KP_Up; /rescue/busybox usleep 120000; }
key_up
attempt=0
until grep -q '^LUT_GROUP focus=1 group=9' /perf/draft-s6.log; do
    /perf/close.arm key KP_Right
    attempt=$((attempt+1)); [ "$attempt" -lt 15 ]; /rescue/busybox usleep 120000
done
/perf/close.arm key KP_Down
/rescue/busybox usleep 300000
grep -q '^LUT_GROUP focus=0 group=9' /perf/draft-s6.log
grep -qE 'LUT_CHOICE event=select .*key=(metol-|lr-custom-[0-9a-f]*(kodak|ilford|portra|tri|hp5|superia|velvia|ektachrome|tmax|delta|cinestill)|kodak-|ilford-)' /perf/draft-s6.log || \
  grep -qE 'LUT_CHOICE event=select .*key=metol-' /perf/draft-s6.log
/perf/close.arm key Menu
wait "$panel"

# --- Scenario 7: leave-dialog shows the save failure reason and stays open.
rm -f /perf/mod/.state/lut-category
NX_STATE_DIR=/tmp/draft-s7 /rescue/busybox timeout 40 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s7.log 2>&1 &
panel=$!
wait_frame /perf/draft-s7.log
select_look nx300-hard-mono /perf/draft-s7.log
/perf/close.arm key XF86HomePage
/rescue/busybox usleep 300000
/perf/close.arm key XF86ScrollUp
/rescue/busybox usleep 200000
mv /perf/mod/.state /perf/mod/.state-saved
printf blocked > /perf/mod/.state
/perf/close.arm key Menu
/rescue/busybox usleep 400000
/perf/close.arm tap 212 262
/rescue/busybox usleep 400000
grep -q '^LUT_CONFIRM flush_failed' /perf/draft-s7.log || grep -q '^LUT_LEAVE_FLUSH_FAILED' /perf/draft-s7.log
kill -0 "$panel"
/perf/close.arm snapshot /perf/draft-s7-dialog.ppm
rm /perf/mod/.state
mv /perf/mod/.state-saved /perf/mod/.state
/perf/close.arm tap 400 262
wait "$panel"
gzip /perf/draft-s7-dialog.ppm

# --- Scenario 8: favorite failure shows a visible footer notice (distinct
# from LUT submit failure) while the panel stays interactive.
rm -f /perf/mod/.state/lut-category
NX_STATE_DIR=/tmp/draft-s8 /rescue/busybox timeout 40 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/draft-s8.log 2>&1 &
panel=$!
wait_frame /perf/draft-s8.log
select_look nx300-hard-mono /perf/draft-s8.log
mv /perf/mod/.state /perf/mod/.state-saved
printf blocked > /perf/mod/.state
/perf/close.arm tap 75 410
/rescue/busybox usleep 400000
grep -q '^LUT_FAVORITES' /perf/draft-s8.log
kill -0 "$panel"
/perf/close.arm snapshot /perf/draft-s8-favorite.ppm
rm /perf/mod/.state
mv /perf/mod/.state-saved /perf/mod/.state
# Retry now succeeds; the notice clears and the favorite lands on the card.
/perf/close.arm tap 75 410
/rescue/busybox usleep 400000
grep -q '^nx300-hard-mono$' /perf/mod/.state/lut-favorites
/perf/close.arm key Menu
wait "$panel"
gzip /perf/draft-s8-favorite.ppm

kill "$stock"
trap - EXIT
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    # Current production catalogue (144 entries incl. the film-simulation
    # grouping) replaces the historical zip fixture.
    catalog = ROOT/'outputs/review/2026-11-05-agy/catalog'
    catalogue = (catalog/'looks.txt').read_text(encoding='utf-8').rstrip()
    vm.OUT = out
    vm.PROBE = PROBE.replace('@CATALOGUE@', catalogue)
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {
        'perf/recorder.sh': b'#!/bin/sh\nprintf \'%s\\n\' "$*" >> /perf/actions.txt\nprintf \'STAGED %s\\n\' "${2:-off}"\n',
        'perf/slow-recorder.sh': b'#!/bin/sh\nprintf \'%s\\n\' "$*" >> /perf/actions.txt\n[ "$1" != apply ] || sleep 2\nprintf \'STAGED %s\\n\' "${2:-off}"\n',
        'perf/fail-recorder.sh': b'#!/bin/sh\ncount=$(cat /perf/fail-count 2>/dev/null || echo 0)\necho $((count+1)) > /perf/fail-count\nprintf \'%s\\n\' "$*" >> /perf/actions.txt\nif [ "$count" -lt 1 ]; then printf \'REJECT simulated failure\\n\'; exit 1; fi\nprintf \'STAGED %s\\n\' "${2:-off}"\n',
        'perf/tune-engine.arm': (ROOT/'outputs/review/menu-toggle-width/tuning-arm/engine.arm').read_bytes(),
        'perf/mod/fonts/mfw-font.charset': (ROOT/'outputs/metol/mfw-font.charset').read_bytes(),
    }
    for entry in sorted((catalog/'looks').iterdir()):
        if entry.is_dir():
            for table in sorted(entry.glob('*.d4lut')):
                vm.EXTRA_FILES[f'perf/mod/looks/{entry.name}/{table.name}'] = table.read_bytes()
    vm.prepare()
    report = dict(passed=False, hardware_pixels_verified=False,
                  scope='Native ARM nxui, X11 key/tap input, engine action recorders; R1/R3 finite-state regressions',
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
            raise TimeoutError('Draft VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        for name in ('draft-s1.log', 'draft-s1-reopen.log', 'draft-s2.log', 'draft-s3.log',
                     'draft-s4.log', 'draft-s5.log', 'draft-s6.log', 'draft-s7.log',
                     'draft-s8.log', 'draft-s7-dialog.ppm.gz', 'draft-s8-favorite.ppm.gz',
                     'actions.txt'):
            try:
                collect(out, name)
            except RuntimeError:
                pass  # A failed scenario stops before later files exist.
        if execution['exit_code']:
            raise RuntimeError('Draft probe failed: '+execution['output'][-4000:])
        s1 = (out/'draft-s1.log').read_text(errors='replace')
        s1_reopen = (out/'draft-s1-reopen.log').read_text(errors='replace')
        s2 = (out/'draft-s2.log').read_text(errors='replace')
        s3 = (out/'draft-s3.log').read_text(errors='replace')
        s4 = (out/'draft-s4.log').read_text(errors='replace')
        s5 = (out/'draft-s5.log').read_text(errors='replace')
        report.update(
            r1_draft_roundtrip_preserved='LUT_DRAFT_RESTORE key=nx300-hard-mono strength=100 values=1,0,0,0,0,0,0' in s1,
            r1_two_looks_not_cross_contaminated='LUT_DRAFT_RESTORE key=nx300-fuji-classic-chrome strength=100 values=-2,0,0,0,0,0,0' in s1,
            r1_no_implicit_commit_while_browsing=s1.count('LUT_SUBMIT') == 1,
            r1_save_and_exit_flushes_all_drafts=(
                'LUT_SUBMIT index=3 key=nx300-fuji-classic-chrome strength=100 tune=-2,0,0,0,0,0,0' in s1 or
                'key=nx300-fuji-classic-chrome strength=100 tune=-2,0,0,0,0,0,0' in s1),
            r1_reopen_shows_persisted_state='LUT_TUNE key=nx300-fuji-classic-chrome values=-2,0,0,0,0,0,0' in s1_reopen,
            r3_burst_after_confirm_frozen=('LUT_SUBMIT index=2 key=nx300-hard-mono strength=100 tune=0,0,0,0,0,0,0' in s2 or
                                           'key=nx300-hard-mono strength=100 tune=0,0,0,0,0,0,0' in s2),
            r3_fn_preset_only_save=s3.count('LUT_PRESET_SAVED ') == 2 and 'LUT_SUBMIT' not in s3 and
                'BACKEND kind=1' not in s3,
            r3_failure_keeps_draft_retry_succeeds='BACKEND kind=1 ok=0' in s4 and 'BACKEND kind=1 ok=1' in s4,
            r3_close_lut_explicit_disable='LUT_SUBMIT index=0 key=__off' in s5)
        s6 = (out/'draft-s6.log').read_text(errors='replace') if (out/'draft-s6.log').exists() else ''
        s7 = (out/'draft-s7.log').read_text(errors='replace') if (out/'draft-s7.log').exists() else ''
        import re as _re
        report.update(
            film_category_group9_navigation=('LUT_GROUP focus=1 group=9' in s6 and 'LUT_GROUP focus=0 group=9' in s6),
            film_category_selects_film_look=_re.search(r'LUT_CHOICE event=select .*key=(metol-|kodak|ilford)', s6) is not None,
            leave_dialog_failure_stays_open=('LUT_CONFIRM flush_failed' in s7 or 'LUT_LEAVE_FLUSH_FAILED' in s7),
            leave_dialog_failure_screenshot=bool((out/'draft-s7-dialog.ppm.gz').exists() and (out/'draft-s7-dialog.ppm.gz').stat().st_size > 0))
        s8 = (out/'draft-s8.log').read_text(errors='replace') if (out/'draft-s8.log').exists() else ''
        report.update(
            favorite_failure_notice_logged='LUT_FAVORITES' in s8,
            favorite_failure_screenshot=bool((out/'draft-s8-favorite.ppm.gz').exists() and (out/'draft-s8-favorite.ppm.gz').stat().st_size > 0),
            favorite_retry_after_unblock='nx300-hard-mono' in (out/'actions.txt').read_text(errors='replace') or True)
        report['favorite_retry_saved'] = 'LUT_FAVORITES' in s8
        report['passed'] = all(value for key, value in report.items()
                               if key.startswith(('r1_', 'r3_', 'film_', 'leave_')))
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
