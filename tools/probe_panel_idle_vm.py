"""Check the hidden-panel backend wait in the existing ARM/Xvfb environment."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import time

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/performance/menu-event-loop/lut-control-repair'
PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_ROOT=/perf/mod
attempt=0
until /perf/close.arm ping; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'result=$?; if [ "$result" != 0 ] && [ -f /perf/normal-menu.log ]; then cat /perf/normal-menu.log; fi; kill "$stock" 2>/dev/null || :' EXIT
attempt=0
until [ -s /perf/stock-parent.ready ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
printf 'test-mono|Mono|0|0|0|0|0|0|0\n' > /perf/mod/looks.txt
printf '1 /perf/mod/looks/test-mono/table.d4lut 100\n' > /var/run/nx300m-lut-selection
cat > /perf/engine.sh <<'ENGINE'
#!/bin/sh
if [ "$1" = apply ]; then
    printf '%s %s\n' "$1" "$2" >> /perf/actions.txt
    printf '%s\n' "$2" > /perf/backend-started.txt
    trap 'printf terminated > /perf/backend-terminated.txt; exit 88' TERM INT
    sleep "${NX_TEST_CONTROL_DELAY:-2}"
    [ "${NX_TEST_FAIL:-0}" != 1 ] || exit 9
    printf '%s\n' "$2" > /perf/chosen.txt
    printf 'STAGED test selection\n'
elif [ "$1" = off ]; then
    printf 'off\n' >> /perf/actions.txt
elif [ "$1" = disable ]; then
    printf 'LUT_DISABLED native_still_restored=true preview_processing=false\n'
    printf 'disable\n' > /perf/off-action.txt
fi
ENGINE
chmod 700 /perf/engine.sh
for variant in baseline current dry; do
    binary=/perf/mod/bin/nxui.arm
    [ "$variant" != baseline ] || binary=/perf/baseline/bin/nxui.arm
    flag=--live
    [ "$variant" != dry ] || flag=--dry-run
    NX_STATE_DIR=/tmp/panel-idle-$variant /rescue/busybox timeout 12 "$binary" "$flag" --lut \
        --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/$variant.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/$variant.log; do
        kill -0 "$panel" || { cat /perf/$variant.log; exit 1; }
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    /perf/close.arm key Return
    wait "$panel"
    grep '^EXIT ' /perf/$variant.log
done
# Apply-to-viewfinder must keep its cover until the child succeeds. On an
# explicit backend failure it must retain the draft and offer a retry.
for variant in apply-viewfinder apply-failure; do
    rm -f /perf/backend-started.txt
    fail=0; [ "$variant" != apply-failure ] || fail=1
    NX_TEST_FAIL=$fail NX_STATE_DIR=/tmp/$variant /perf/mod/bin/nxui.arm --live --lut \
        --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/$variant.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/$variant.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    if [ "$fail" = 1 ]; then
        for key in XF86HomePage Right; do /perf/close.arm key "$key"; done
    fi
    /perf/close.arm tap 740 26
    attempt=0
    until [ -s /perf/backend-started.txt ]; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
    done
    /rescue/busybox usleep 350000
    kill -0 "$panel"
    ! grep -q '^EXIT ' /perf/$variant.log
    /perf/close.arm snapshot /perf/$variant.ppm
    if [ "$fail" = 1 ]; then
        attempt=0
        until grep -q '^BACKEND kind=1 ok=0' /perf/$variant.log; do
            kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
        done
        kill -0 "$panel"
        /perf/close.arm key Menu
        /perf/close.arm key Left
        /perf/close.arm key Return
    fi
    wait "$panel"
    gzip /perf/$variant.ppm
done
rm -f /var/run/nx300m-lut-selection
NX_STATE_DIR=/tmp/panel-off /rescue/busybox timeout 12 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/off.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/off.log; do
    kill -0 "$panel" || { cat /perf/off.log; exit 1; }
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm tap 160 26
wait "$panel"
grep -q '^disable$' /perf/off-action.txt
grep -q 'LUT_DISABLED' /perf/off.log
# Browse from the entry mono to color, then use MENU while apply is in flight.
# The panel must yield focus and retain color instead of launching legacy off.
printf 'test-mono|Mono|0|0|0|0|0|0|0\ntest-color|Color|0|0|0|0|0|0|0\n' > /perf/mod/looks.txt
printf '1 /perf/mod/looks/test-mono/table.d4lut 100\n' > /var/run/nx300m-lut-selection
: > /perf/actions.txt
rm -f /perf/backend-started.txt
NX_STATE_DIR=/tmp/panel-retain /rescue/busybox timeout 20 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/retain.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/retain.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm key Right
/rescue/busybox usleep 400000
[ ! -s /perf/actions.txt ]
/perf/close.arm key Return
/perf/close.arm key Menu
wait "$panel"
[ "$(cat /perf/chosen.txt)" = test-color ]
cp /perf/actions.txt /perf/retain-actions.txt
# A valid control longer than the former 12 s UI limit must finish normally.
rm -f /perf/backend-started.txt /perf/backend-terminated.txt
NX_TEST_CONTROL_DELAY=13 NX_STATE_DIR=/tmp/panel-long /rescue/busybox timeout 25 /perf/mod/bin/nxui.arm --live --lut \
    --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/engine.sh \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/long.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/long.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm key Return
/perf/close.arm key Menu
wait "$panel"
[ ! -e /perf/backend-terminated.txt ]
# Exercise the restored main menu and diagnostic page with native ARM input.
cat > /perf/mod/mfw-panel-actions.sh <<'ACTIONS'
#!/bin/sh
printf '%s\n' "$1" >> /perf/menu-actions.txt
printf '取景预览已关闭；照片 LUT 保留。\n'
ACTIONS
cp /perf/engine.sh /perf/mod/bin/lut_engine.arm
NX_STATE_DIR=/tmp/panel-menu /perf/mod/bin/nxui.arm --mfw-services --root /perf/mod \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata /perf/mod/fonts/mfw-font.charset > /perf/normal-menu.log 2>&1 &
panel=$!
attempt=0
until grep -q '^FIRST_FRAME ' /perf/normal-menu.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
[ ! -e /perf/menu-actions.txt ]
# A dial detent only changes focus. Check both root and feature-list pages.
for key in XF86ScrollUp Return XF86ScrollUp XF86ScrollDown Menu; do /perf/close.arm key "$key"; done
attempt=0
until grep -q 'MFW_SELECT page=3 index=0 action=lens-info' /perf/normal-menu.log &&
      [ "$(grep -c 'MENU file=.*/gui_mfw.NX300M items=5' /perf/normal-menu.log)" -eq 2 ]; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
done
grep -q 'MFW_SELECT page=0 index=1 action=lenses' /perf/normal-menu.log
grep -q 'MFW_SELECT page=3 index=1 action=lens-select' /perf/normal-menu.log
grep -q 'MFW_SELECT page=3 index=0 action=lens-info' /perf/normal-menu.log
[ ! -e /perf/menu-actions.txt ]
for key in XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp Return XF86ScrollUp XF86ScrollUp XF86ScrollUp XF86ScrollUp Return; do /perf/close.arm key "$key"; done
attempt=0
until grep -q 'MENU file=.*/gui_debug.NX300M items=7' /perf/normal-menu.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
for key in Menu Menu; do /perf/close.arm key "$key"; done
attempt=0
until [ "$(grep -c 'MENU file=.*/gui_mfw.NX300M items=5' /perf/normal-menu.log)" -eq 3 ]; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
for key in Return; do /perf/close.arm key "$key"; done
attempt=0
until grep -q '^LUT_PAGE ' /perf/normal-menu.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
/perf/close.arm tap 310 26
attempt=0
until grep -q '^BACKEND kind=5 ok=1' /perf/normal-menu.log; do
    kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 100000
done
for key in Menu Menu; do /perf/close.arm key "$key"; done
wait "$panel"
[ "$(cat /perf/menu-actions.txt)" = preview-toggle ]
kill "$stock"
trap - EXIT
'''


def fields(line):
    return {key: int(value) for key, value in re.findall(r'(\w+)=(-?\d+)', line)}


def verify(out):
    execution = json.loads((out/'execution.json').read_text())
    assert execution['exit_code'] == 0, execution
    samples = {}
    for variant in ('baseline', 'current', 'dry'):
        log = (out/(variant+'.log')).read_text()
        ended = re.search(r'^EXIT (.+)$', log, re.M)
        resources = re.findall(r'^PANEL_RESOURCE (.+)$', log, re.M)
        assert ended and len(resources)==2, log
        sample = fields(ended[1])
        before, after = map(fields, resources)
        sample['wait_cpu_us'] = after['user_us']+after['system_us']-before['user_us']-before['system_us']
        samples[variant] = sample
    assert samples['baseline']['wakeups']>1000, samples
    assert samples['current']['wakeups']<100, samples
    assert samples['dry']['elapsed_ms']<2000, samples
    off = (out/'off.log').read_text()
    assert 'BACKEND kind=1 ok=1' in off and 'LUT_DISABLED' in off, off
    retain = (out/'retain.log').read_text()
    assert 'BACKEND kind=1 ok=1' in retain, retain
    assert (out/'retain-actions.txt').read_text()=='apply test-color\n'
    slow = (out/'long.log').read_text()
    assert 'BACKEND waiting_for_control ' in slow and 'BACKEND kind=1 ok=1' in slow,slow
    elapsed=int(re.search(r'BACKEND kind=1 ok=1 elapsed_ms=(\d+)',slow)[1])
    assert elapsed>=13000 and '后端超时' not in slow,slow
    return dict(passed=True, samples=samples, hidden_backend_wait_does_not_spin=True,
                dry_commit_exits_without_wait=True, lut_off_invokes_disable=True,
                menu_close_retains_new_choice_without_legacy_restore=True,
                long_lut_control_finishes_without_group_kill=True,long_control_elapsed_ms=elapsed,
                normal_menu_debug_navigation_and_preview_stop=True,
                root_and_feature_lists_accept_native_wheel_without_auto_action=True)


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    OUT = parser.parse_args().output_dir.resolve()
    if (OUT/'report.json').exists():
        raise RuntimeError('Preserve the existing menu wait measurement')
    baseline = ROOT/'outputs/performance/menu-event-loop/baseline-nxui.arm'
    if not baseline.is_file():
        raise RuntimeError('Preserve the pre-fix nxui.arm before building the candidate')
    current = ROOT/'outputs/metol/nxui.arm'
    OUT.mkdir(parents=True, exist_ok=True)
    overlay = io.BytesIO()
    for directory in ('perf', 'perf/mod', 'perf/mod/bin'):
        arm_vm.cpio_add(overlay, directory, 0o40755)
    arm_vm.cpio_add(overlay, 'perf/mod/bin/nxui.arm', 0o100755, baseline.read_bytes())
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    recorded = OUT/'baseline-overlay.cpio.gz'
    recorded.write_bytes(gzip.compress(overlay.getvalue(), mtime=0))
    vm.OUT = OUT
    vm.BASELINE_OVERLAY = recorded
    vm.PROBE = PROBE
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")',
                                'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.prepare()
    report = dict(passed=False, physical_camera_tested=False,
                  scope='Real ARM nxui/X11; two-second backend is a shell fixture',
                  baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
                  candidate_sha256=hashlib.sha256(current.read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start')
        started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if (OUT/'serial.log').exists() and 'MENU_PERF_VM_READY' in (OUT/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Menu VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(OUT/'execution.json', execution)
        if execution['exit_code']:
            raise RuntimeError('Panel wait probe exit '+str(execution['exit_code'])+': '+execution['output'])
        for variant in ('baseline', 'current', 'dry', 'off', 'retain', 'long', 'normal-menu', 'apply-viewfinder', 'apply-failure'):
            collect(OUT, variant+'.log')
        for variant in ('apply-viewfinder', 'apply-failure'):
            collect(OUT, variant+'.ppm.gz')
        collect(OUT, 'off-action.txt')
        collect(OUT, 'retain-actions.txt')
        collect(OUT, 'menu-actions.txt')
        report.update(verify(OUT))
        report.update(apply_viewfinder_waits_for_backend=True, failed_apply_retains_panel=True,
                      unchanged_saved_look_explicitly_submitted=True)
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (OUT/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(OUT/'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
