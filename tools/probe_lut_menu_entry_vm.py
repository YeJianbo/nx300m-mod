"""Compare menu-to-LUT latency using real ARM nxui, stock fonts and X11.

The engine records actions without a camera. Baseline adapter preparation is
an immediate fixture, so its timings exclude real ptrace/preview waits.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import time
from zipfile import ZipFile

import arm_vm
import probe_menu_startup_vm as vm
from probe_menu_entry_vm import collect
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''#!/bin/sh
set -eu
export DISPLAY=:97 NX300M_MOD_SOURCE_ROOT=/perf/data
mkdir -p /perf/data/.state
printf '1 /perf/data/looks/nx300-hard-mono/table.d4lut 75\n' > /perf/data/.state/lut-choice
export NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' /perf/data)
until /perf/close.arm ping; do /rescue/busybox usleep 100000; done
/perf/close.arm stock-parent > /perf/stock-parent.log 2>&1 &
stock=$!
trap 'kill "$stock" 2>/dev/null || :' EXIT
until [ -s /perf/stock-parent.ready ]; do /rescue/busybox usleep 100000; done
wait_log() {
    attempt=0
    until grep -q "$1" "$log"; do
        kill -0 "$panel" || { cat "$log"; exit 1; }
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ]
        /rescue/busybox usleep 50000
    done
}
for trial in 1 2 3; do
  for variant in baseline current; do
    service=/perf/mod
    [ "$variant" != baseline ] || service=/perf/baseline
    export NX_FN_SERVICE_ROOT=$service NX300M_MOD_ROOT=$service
    log=/perf/$variant-$trial.log
    : > /perf/actions.txt
    "$service/bin/nxui.arm" --mfw-services --root "$service" \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata "$service/fonts/mfw-font.charset" > "$log" 2>&1 &
    panel=$!
    wait_log '^FIRST_FRAME '
    /perf/close.arm key KP_Enter > /perf/$variant-$trial-input.txt
    if [ "$variant" = baseline ]; then
        attempt=0
        until [ "$(grep -c '^FIRST_FRAME ' "$log")" -eq 2 ]; do
            kill -0 "$panel" || { cat "$log"; exit 1; }
            attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
        done
    else
        wait_log '^LUT_PAGE '
        [ ! -s /perf/actions.txt ]
        grep -q 'LUT_ENTRY .*key=nx300-hard-mono strength=75 saved=1' "$log"
        if [ "$trial" = 1 ]; then
            /perf/close.arm snapshot /perf/lut-page.ppm
            gzip /perf/lut-page.ppm
            # Return discards local browsing; the same window can reopen LUT.
            /perf/close.arm key KP_Right >/dev/null
            /perf/close.arm tap 520 22
            /rescue/busybox usleep 200000
            /perf/close.arm key KP_Enter >/dev/null
            attempt=0
            until [ "$(grep -c '^LUT_PAGE ' "$log")" -eq 2 ]; do
                kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
            done
            [ ! -s /perf/actions.txt ]
            /perf/close.arm key KP_Enter >/dev/null
        else
            /perf/close.arm key Menu >/dev/null
        fi
    fi
    [ "$variant" != baseline ] || /perf/close.arm key Menu >/dev/null
    wait "$panel"
    cp /perf/actions.txt /perf/$variant-$trial-actions.txt
  done
done
kill "$stock"
trap - EXIT
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/performance/lut-menu-entry/arm-vm')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out/'report.json').exists():
        raise RuntimeError('Preserve the recorded menu entry comparison')
    before = ROOT/'outputs/performance/lut-menu-entry/before'
    current = ROOT/'outputs/metol/nxui.arm'
    with ZipFile(ROOT/'outputs/deployment/smartfilter-session/diagnostic-card/nx300m-ipcs-open-diagnostic.zip') as package:
        catalogue = package.read('nx300m-mod/looks.txt')
    engine = b'''#!/bin/sh
printf '%s %s root=%s\n' "$1" "${2:-}" "$NX300M_MOD_ROOT" >> /perf/actions.txt
printf 'STAGED fixture\n'
'''
    # Preserve copy size without invoking a physical camera loader.
    engine += b'#'+b' ' * max(0, (ROOT/'outputs/metol/lut_engine.arm').stat().st_size-len(engine)-2)+b'\n'
    files = {'perf/data/looks.txt': catalogue,
             'perf/mod/bin/nxui.arm': (before/'nxui.arm').read_bytes(),
             'perf/mod/lut-panel.sh': (before/'lut-panel.sh').read_bytes(),
             'perf/mod/bin/lut_engine.arm': engine,
             'perf/mod/fonts/mfw-font.charset': (ROOT/'outputs/metol/mfw-font.charset').read_bytes()}
    for name in ('gui_mfw.NX300M', 'gui_debug.NX300M'):
        files['perf/mod/'+name] = (ROOT/'mod/deployment/mfw'/name).read_bytes()
    overlay = io.BytesIO()
    directories = {p.as_posix() for name in files for p in Path(name).parents if str(p) != '.'}
    for name in sorted(directories):
        arm_vm.cpio_add(overlay, name, 0o40755)
    for name, data in files.items():
        arm_vm.cpio_add(overlay, name, 0o100755 if name.endswith(('.arm', '.sh')) else 0o100644, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    out.mkdir(parents=True, exist_ok=True)
    vm.BASELINE_OVERLAY = out/'baseline-overlay.cpio.gz'
    vm.BASELINE_OVERLAY.write_bytes(gzip.compress(overlay.getvalue(), mtime=0))
    vm.OUT, vm.PROBE = out, PROBE
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '''    unsigned t[2]; if(syscall(263,1,t))return 21;
    printf("INPUT sent_ms=%llu window=%lu\\n",(unsigned long long)t[0]*1000+t[1]/1000000,w);
    XEvent event={0};''', 1)
    vm.EXTRA_FILES = {'perf/data/looks.txt': catalogue, 'perf/mod/bin/lut_engine.arm': engine}
    vm.prepare()
    report = dict(passed=False, physical_camera_measured=False,
                  scope='ARM X11 menu-to-LUT; adapter fixture has no artificial delay, no physical ptrace or preview sync',
                  baseline_sha256=hashlib.sha256((before/'nxui.arm').read_bytes()).hexdigest(),
                  candidate_sha256=hashlib.sha256(current.read_bytes()).hexdigest())
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if 'MENU_PERF_VM_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Menu entry VM boot')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        for variant in ('baseline', 'current'):
            for trial in range(1, 4):
                for suffix in ('.log', '-input.txt', '-actions.txt'):
                    collect(out, f'{variant}-{trial}{suffix}')
        if execution['exit_code']:
            raise RuntimeError('Menu entry probe exit '+str(execution['exit_code'])+': '+execution['output'])
        collect(out, 'lut-page.ppm.gz')
        from PIL import Image
        Image.open(io.BytesIO(gzip.decompress((out/'lut-page.ppm.gz').read_bytes()))).save(out/'lut-page.png')
        samples = {}
        for variant in ('baseline', 'current'):
            samples[variant] = []
            for trial in range(1, 4):
                log = (out/f'{variant}-{trial}.log').read_text()
                sent = int(re.search(r'sent_ms=(\d+)', (out/f'{variant}-{trial}-input.txt').read_text())[1])
                if variant == 'baseline':
                    shown = int(re.findall(r'name=focus-ready at_ms=(\d+)', log)[1])
                else:
                    shown = int(re.search(r'^LUT_PAGE .*shown_ms=(\d+)', log, re.M)[1])
                    assert len(re.findall(r'^FIRST_FRAME ', log, re.M)) == 1, log
                    assert (out/f'{variant}-{trial}-actions.txt').read_text() == (
                        'apply nx300-hard-mono root=/perf/data\n' if trial == 1 else ''), log
                samples[variant].append(shown-sent)
        medians = {key: statistics.median(value) for key, value in samples.items()}
        report.update(passed=True, samples_click_to_list_ms=samples, median_click_to_list_ms=medians,
                      current_over_baseline=medians['current']/medians['baseline'],
                      same_process_window_and_font_reused=True, browse_does_not_call_engine=True,
                      saved_choice_displayed_without_restoring_camera=True,
                      return_and_reopen_without_apply=True, confirmation_uses_data_root=True)
    except Exception as error:
        report['error'] = str(error)
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
