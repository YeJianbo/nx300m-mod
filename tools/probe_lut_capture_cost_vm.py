"""Measure original versus hooked LUT Update with equal stock Reset/writes.

This is the CPU portion of capture parameter submission, using genuine ARM
IQ/UDD code and RAM MMIO. It cannot time sensor, physical ISP, JPEG or SD.
"""
import argparse
import json
from pathlib import Path
import statistics
import struct
import time

import arm_vm
import probe_lut_live_loader_vm as live

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''#!/bin/sh
set -eu
OUT=/arm-vm/mfw-results
LOADER=/mnt/mmc/bin/lut_live_loader.arm
ROOT=/mnt/mmc/nx300m-mod
/mnt/mmc/bin/lut_live_fixture.arm > "$OUT/fixture.stdout" 2> "$OUT/fixture.stderr" &
pid=$!
trap 'kill "$pid" 2>/dev/null || :' EXIT
attempt=0
until [ -s "$OUT/fixture.ready" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
done
rm /var/run/nx300m-lut-trace-enabled
seq=0
rpc() {
    tag=$1; repeats=${2:-256}; seq=$((seq+1))
    printf '%s 1 2 %s %s\n' "$seq" "$tag" "$repeats" > /var/run/lut-live-request.tmp
    mv /var/run/lut-live-request.tmp /var/run/lut-live-request
    attempt=0
    while :; do
        response=0
        [ ! -r /var/run/lut-live-response ] || read response ignored < /var/run/lut-live-response
        [ "$response" != "$seq" ] || break
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
    done
}
measure() {
    label=$1
    rpc "$label-warm" 1
    for sample in 1 2 3 4 5 6 7 8 9; do rpc "$label-$sample"; done
}
measure stock-before
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/apply.json" 2> "$OUT/apply.stderr"
measure hook-stock
printf '1 %s/looks/ricoh-negative-exact/table.d4lut 0\n' "$ROOT" > /var/run/nx300m-lut-selection
measure lut-zero
printf '2 %s/looks/ricoh-negative-exact/table.d4lut 100\n' "$ROOT" > /var/run/nx300m-lut-selection
rpc lut-first 1
measure lut-full
printf '3 %s/looks/ricoh-negative-exact/table.d4lut 50\n' "$ROOT" > /var/run/nx300m-lut-selection
measure lut-half
rm /var/run/nx300m-lut-selection
"$LOADER" restore --pid "$pid" --fixture > "$OUT/restore.json" 2> "$OUT/restore.stderr"
measure stock-after
if [ -x /mnt/mmc/bin/baseline_loader.arm ]; then
    rm -f "/var/run/nx300m-lut-live-$pid.state"
    /mnt/mmc/bin/baseline_loader.arm apply --pid "$pid" --blob /mnt/mmc/baseline_blob.bin --root "$ROOT" --fixture > "$OUT/previous-apply.json" 2> "$OUT/previous-apply.stderr"
    printf '4 %s/looks/ricoh-negative-exact/table.d4lut 100\n' "$ROOT" > /var/run/nx300m-lut-selection
    measure previous-full
    printf '5 %s/looks/ricoh-negative-exact/table.d4lut 50\n' "$ROOT" > /var/run/nx300m-lut-selection
    measure previous-half
    /mnt/mmc/bin/baseline_loader.arm restore --pid "$pid" --fixture > "$OUT/previous-restore.json" 2> "$OUT/previous-restore.stderr"
fi
kill "$pid"
trap - EXIT
printf 'LUT_CAPTURE_COST_PASSED\n'
'''


def analyze(out):
    execution = json.loads((out/'execution.json').read_text())
    if execution['exit_code']:
        raise RuntimeError('Capture cost guest exit '+str(execution['exit_code']))
    guest = out/'guest/mfw-results'
    words = lambda path: list(struct.unpack('<2190H', path.read_bytes()))
    variants = ('stock-before', 'hook-stock', 'lut-zero', 'lut-full', 'lut-half', 'stock-after')
    if (guest/'previous-full-1.fixture.json').exists():
        variants += ('previous-full', 'previous-half')
    samples, medians = {}, {}
    for variant in variants:
        samples[variant] = []
        for sample in range(1, 10):
            data = json.loads((guest/f'{variant}-{sample}.fixture.json').read_text())
            assert data['reset_each'] and data['repeats']==256, data
            samples[variant].append({key: data[f'update_{key}_ns']/256/1e6 for key in ('wall', 'cpu')})
        medians[variant] = {key: statistics.median(row[key] for row in samples[variant]) for key in ('wall', 'cpu')}
    stock = words(guest/'stock-before-1.bin')
    target = words(out/'source-files/nx300m-mod/looks/ricoh-negative-exact/table-stock3d.d4lut')
    for variant in variants:
        expected = target if variant in ('lut-full', 'previous-full') else (
            [(a+b+1)//2 for a, b in zip(stock, target)] if variant in ('lut-half', 'previous-half') else stock)
        for sample in range(1, 10):
            assert words(guest/f'{variant}-{sample}.bin')==expected, variant
    assert json.loads((guest/'restore.json').read_text()).get('restored') is True
    baseline = statistics.mean(medians[name]['wall'] for name in ('stock-before', 'stock-after'))
    report = dict(passed=True, samples_ms_per_update=samples, median_ms_per_update=medians,
                verified_stock_reset_and_all_three_table_channels=True,
                stock_zero_half_full_values_checked=True, stock_slots_restored=True,
                lut_full_added_ms_per_update=medians['lut-full']['wall']-baseline,
                lut_first_update_ms=json.loads((guest/'lut-first.fixture.json').read_text())['update_wall_ns']/1e6)
    if 'previous-full' in medians:
        report['current_over_previous'] = {key: medians['lut-'+key]['wall']/medians['previous-'+key]['wall'] for key in ('full', 'half')}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/performance/lut-capture-cost/arm-vm')
    parser.add_argument('--analyze-only', action='store_true', help='Read retained VM evidence without another run')
    parser.add_argument('--baseline-dir', type=Path, help='Saved loader/blob for same-VM comparison')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if args.analyze_only:
        report = json.loads((out/'report.json').read_text())
        report.update(analyze(out))
        report.pop('error', None)
        arm_vm.write_json(out/'report.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if (out/'report.json').exists():
        raise RuntimeError('Preserve the recorded capture comparison')
    live.OUT = out
    live.BASELINE = args.baseline_dir.resolve() if args.baseline_dir else None
    live.PROBE = PROBE
    live.configure(factory_only=True)
    live.prepare()
    report = dict(passed=False, physical_capture_timing=False,
                  scope='Genuine ARM CIpcs3DLutD4::Update + UDD RAM writer, equal Reset before every update, tracing off; no JPEG/SD timing')
    started = False
    try:
        live.call('start'); started = True
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            if 'LUT_LIVE_PRIVATE_VM_SHELL_READY' in (out/'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Capture cost VM boot')
        execution = live.call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(out/'execution.json', execution)
        guest = live.collect()
        if execution['exit_code'] or 'LUT_CAPTURE_COST_PASSED' not in execution['output']:
            raise RuntimeError('Capture cost guest exit '+str(execution['exit_code']))
        report.update(analyze(out))
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if started:
            report['stop'] = live.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out/'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out/'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
