"""Same-VM LUT panel latency comparison over an identical wheel burst.

Runs several nxui binaries in one guest, each with the same 813-press wheel
burst, the same 13-step reverse tail and the same trailing discrete key, and
reports per-stage paint timings plus the paint-schedule decisions.

Per variant: painted frames, inter-paint P50/P95, longest interval without a
paint, last-input-to-final-paint latency, consumed inputs, final tune value,
CPU/wakeups and the median of each measured phase (event drain, reference
rebuild, draw, damage scan, raster, rotated XImage upload).

Software rasterization in an ARM VM only: no physical FPS, LCD or camera claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import time

import arm_vm
import probe_menu_startup_vm as vm
import probe_menu_coalesce_vm as co
from probe_menu_entry_vm import collect
from probe_lut_selection_vm import TAP_DRIVER

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''#!/bin/sh
set -eu
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
last_choice() { grep 'LUT_CHOICE event=select' "$1" | tail -n 1; }
run_variant() { # label binary
    rm -f /var/run/nx300m-lut-selection /var/run/nx300m-lut-selection-session
    rm -rf /perf/mod/.state; mkdir -p /perf/mod/.state
    NX_PANEL_TRACE=1 NX_STATE_DIR=/tmp/coalesce-$1 /rescue/busybox timeout 25 "$2" --live --lut \
        --root /perf/mod --recipes /perf/mod/looks.txt --engine /perf/recorder.sh \
        --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
        --font-metadata /perf/mod/fonts/mfw-font.charset --lifetime-ms 9000 > /perf/coalesce-$1.log 2>&1 &
    panel=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' /perf/coalesce-$1.log; do
        kill -0 "$panel"; attempt=$((attempt+1)); [ "$attempt" -lt 150 ]; /rescue/busybox usleep 100000
    done
    attempt=0
    until last_choice /perf/coalesce-$1.log | grep -q "key=nx300-hard-mono"; do
        /perf/close.arm key KP_Down
        attempt=$((attempt+1)); [ "$attempt" -lt 30 ]; /rescue/busybox usleep 80000
    done
    /perf/close.arm key XF86HomePage
    /rescue/busybox usleep 300000
    /perf/close.arm key @BURST_ARGS@
    /rescue/busybox usleep 100000
    @TAIL@
    /perf/close.arm key KP_Down
    /rescue/busybox usleep 250000
    wait "$panel" || :
}
@VARIANTS@
kill "$stock"
trap - EXIT
'''

PHASES = ('ref_ms', 'evt_ms', 'draw_ms', 'damage_us', 'raster_us', 'upload_us',
          'raster_px', 'upload_px')

# The saturated burst ends pinned at the parameter limit, so most of its paints
# carry no changed pixels. The sweep workload oscillates the value in small
# groups and never reaches a limit, so every step really repaints content.
WORKLOADS = {
    'burst': {
        'burst': ' '.join(['XF86ScrollDown'] * 800),
        'tail': '/perf/close.arm key ' + ' '.join(['XF86ScrollUp'] * 13),
        'inputs': 813,
    },
    # Small groups with a pause between them: one huge key volley loses events at
    # the X client queue when each paint costs ~50 ms, which would be a fixture
    # artefact rather than a panel result.
    # A triangle wave: each group moves the value in one direction only, so the
    # painted frame always differs from the previous one. Small groups with a
    # pause keep the X client queue from dropping keys while a paint costs ~50 ms.
    'sweep': {
        'burst': ('i=0; while [ $i -lt 30 ]; do '
                  'if [ $((i%2)) -eq 0 ]; then '
                  '/perf/close.arm key XF86ScrollDown XF86ScrollDown XF86ScrollDown; else '
                  '/perf/close.arm key XF86ScrollUp XF86ScrollUp XF86ScrollUp; fi; '
                  '/rescue/busybox usleep 80000; i=$((i+1)); done'),
        'tail': '/perf/close.arm key XF86ScrollUp XF86ScrollUp XF86ScrollUp',
        'inputs': 93,
    },
}


def analyze(text, upload_active_only=False):
    frames = [dict(dequeued=int(m[0]), submitted=int(m[1]), painted=int(m[2]))
              for m in re.findall(r'PANEL_FRAME pid=\d+ dequeued_ms=(-?\d+) submitted_ms=(-?\d+) events=\d+ painted=(\d+) exposed=\d+', text)]
    painted = [f for f in frames if f['painted'] == 1]
    draws = [{k: int(v) for k, v in zip(PHASES, m)} for m in
             re.findall(r'PANEL_DRAW pid=\d+ ref_ms=(-?\d+) evt_ms=(-?\d+) draw_ms=(-?\d+) '
                        r'damage_us=(\d+) raster_us=(\d+) upload_us=(\d+) raster_px=(\d+) upload_px=(\d+)', text)]
    skips = re.findall(r'PANEL_SKIP pid=\d+ tick=(-?\d+) next_frame=(-?\d+) hold=(-?\d+) continuous=(\d+) discrete=(\d+)', text)
    out = dict(frames=len(frames), painted=len(painted), draw_lines=len(draws),
               skip_lines=len(skips))
    # PANEL_DRAW is emitted once per painted frame in the same order, so paints
    # that really changed pixels can be separated from empty redraws.
    series = list(zip(painted, draws))
    active = [(f, d) for f, d in series if d['upload_px'] > 0]
    out['painted_with_upload'] = len(active)
    out['upload_pixels_total'] = sum(d['upload_px'] for _, d in series)
    subset = [f for f, _ in active] if upload_active_only else painted
    if len(subset) < 2:
        out.update(interpaint_p50_ms=None, interpaint_p95_ms=None,
                   longest_paint_gap_ms=None, final_latency_ms=None)
    else:
        gaps = sorted(b['submitted'] - a['submitted'] for a, b in zip(subset, subset[1:]))
        out.update(interpaint_p50_ms=gaps[len(gaps) // 2],
                   interpaint_p95_ms=gaps[int((len(gaps) - 1) * 0.95)],
                   longest_paint_gap_ms=gaps[-1])
        last_dequeued = max((f['dequeued'] for f in frames if f['dequeued'] >= 0), default=-1)
        out['final_latency_ms'] = painted[-1]['submitted'] - last_dequeued if last_dequeued >= 0 else None
    if draws:
        out['phase_median'] = {p: statistics.median(d[p] for d in draws) for p in PHASES}
        out['phase_max'] = {p: max(d[p] for d in draws) for p in PHASES}
    if skips:
        # A skipped dirty frame is only interesting when the hold (not the frame
        # deadline) was the gate; report both counts separately.
        out['skips_hold_gated'] = sum(1 for s in skips if int(s[2]) > int(s[0]))
        out['skips_deadline_gated'] = len(skips) - out['skips_hold_gated']
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--variant', action='append', default=[],
                        metavar='LABEL=PATH',
                        help='binary to run; repeat for several variants in one VM')
    parser.add_argument('--workload', choices=sorted(WORKLOADS), default='burst',
                        help='burst = one saturated wheel run; sweep = unsaturated oscillation')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve earlier evidence; choose a new output directory')
    variants = []
    for item in args.variant:
        label, _, path = item.partition('=')
        if not label or not path:
            raise SystemExit('--variant needs LABEL=PATH')
        if not re.fullmatch(r'[a-z0-9-]+', label):
            raise SystemExit('variant labels are [a-z0-9-]')
        variants.append((label, Path(path)))
    if not variants:
        raise SystemExit('at least one --variant is required')
    from zipfile import ZipFile
    archive = ROOT / 'outputs/looks/lightroom-import/on-camera-test/nx300m-lut-color-test.zip'
    with ZipFile(archive) as package:
        catalogue = package.read('nx300m-mod/looks.txt').decode().rstrip()
        looks = {name: package.read(name) for name in package.namelist()
                 if '/looks/' in name and name.endswith('.d4lut')}
    binaries = {}
    for label, path in variants:
        if not path.exists():
            raise SystemExit(f'missing variant binary {path}')
        binaries[label] = path.read_bytes()
    vm.OUT = out
    workload = WORKLOADS[args.workload]
    # burst = one volley of keys; sweep = a short shell snippet that paces them.
    if args.workload == 'sweep':
        burst_line = workload['burst']
    else:
        burst_line = '/perf/close.arm key ' + workload['burst']
    vm.PROBE = (PROBE.replace('@CATALOGUE@', catalogue)
                .replace('/perf/close.arm key @BURST_ARGS@', burst_line)
                .replace('    @TAIL@', '    ' + workload['tail'])
                .replace('@VARIANTS@', '\n'.join(f'run_variant {l} /perf/{l}.arm' for l, _ in variants)))
    vm.DRIVER = vm.DRIVER.replace('    if(argc==2) {', TAP_DRIVER+co.BURST_DRIVER+'\n    if(argc==2) {', 1)
    vm.DRIVER = vm.DRIVER.replace('    XEvent event={0};', '    if(capture_only){XCloseDisplay(d);return 0;}\n    XEvent event={0};', 1)
    vm.DRIVER = vm.DRIVER.replace('XStringToKeysym("Menu")', 'XStringToKeysym(argc==3 ? argv[2] : "Menu")')
    vm.EXTRA_FILES = {
        'perf/recorder.sh': b'#!/bin/sh\nprintf \'%s\\n\' "$*" >> /perf/actions.txt\nprintf \'STAGED %s\\n\' "${2:-off}"\n',
        'perf/mod/fonts/mfw-font.charset': (ROOT / 'outputs/metol/mfw-font.charset').read_bytes(),
    }
    vm.EXTRA_FILES.update({f'perf/{label}.arm': data for label, data in binaries.items()})
    vm.EXTRA_FILES.update({'perf/mod/' + name.removeprefix('nx300m-mod/'): data for name, data in looks.items()})
    vm.prepare()
    report = dict(passed=False, hardware_fps=False,
                  scope='Same VM, same wheel workload, per-stage paint timings; software rasterization only',
                  workload=args.workload,
                  input_sequence=('saturated 800-press run, 13 reverse keys, one KP_Down' if args.workload == 'burst'
                                  else '30 x 3 keys alternating direction (triangle wave), 3 reverse keys, one KP_Down'),
                  binaries={label: hashlib.sha256(data).hexdigest() for label, data in binaries.items()},
                  variants={})
    started = False
    try:
        vm.call('start'); started = True
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and 'MENU_PERF_VM_READY' in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('panel latency VM startup')
        execution = vm.call('exec', '/bin/sh /perf/probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        for label, _ in variants:
            collect(out, f'coalesce-{label}.log')
        if execution['exit_code']:
            raise RuntimeError('panel latency probe failed: ' + execution['output'][-3000:])
        for label, _ in variants:
            text = (out / f'coalesce-{label}.log').read_text(errors='replace')
            detail = co.details(text)
            report['variants'][label] = dict(
                timing=analyze(text, upload_active_only=(args.workload == 'sweep')),
                final=detail['final'],
                cpu_after_first_frame_ms=detail['cpu_after_first_frame_ms'],
                wakeups=detail['wakeups'],
                full_batches=detail['full_batches'],
                painted_during_full_batches=detail['painted_during_full_batches'],
                wheel_presses=detail['wheel_presses'],
            )
            report['variants'][label]['checks'] = dict(
                all_input_consumed=detail['wheel_presses'] == workload['inputs'],
                final_value=(detail['final'] is not None and detail['final']['field'] == 1
                             and detail['final']['tune'] == [3, 0, 0, 0, 0, 0, 0]),
                final_latency=(report['variants'][label]['timing']['final_latency_ms'] or 10 ** 9) <= 150,
            )
            if args.workload == 'sweep':
                # The point of the sweep is that the picture keeps changing: most
                # painted frames must have uploaded real pixels.
                timing = report['variants'][label]['timing']
                report['variants'][label]['checks']['pixels_changed'] = (
                    timing['painted'] >= 8 and timing['painted_with_upload'] >= timing['painted'] * 0.6)
            else:
                report['variants'][label]['checks']['mid_burst_paints'] = (
                    detail['full_batches'] >= 20 and detail['painted_during_full_batches'] >= 5)
        report['passed'] = all(all(v['checks'].values()) for v in report['variants'].values())
    except BaseException as error:
        report['failure'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = vm.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps({k: report[k] for k in ('passed', 'variants')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
