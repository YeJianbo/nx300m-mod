"""Compare two Still-LUT hook images on one private ARM VM boot.

The measured interval is exactly the stock `CIpcParameterNode::Update` call the
capture path makes (three channel writers, stock Reset before every update,
tracing disabled). Candidate and baseline images are applied to the same
fixture process in the same boot, so the only difference is the hook image.

This is parameter-submission cost only. It cannot time sensor exposure, ISP,
JPEG encode or SD write, and it does not pretend a per-update saving is a
per-photograph saving.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import arm_vm
import probe_lut_capture_cost_vm as cost
import probe_lut_live_loader_vm as live
import statistics
import struct

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE = ROOT / 'outputs/review/2026-11-15-pi-capture-performance/candidate'
DEFAULT_BASELINE = ROOT / 'outputs/review/2026-11-15-pi-capture-performance/baseline'

# Same scenarios as probe_lut_capture_cost_vm, plus a status snapshot after
# every phase so the published counters can be read without a second run.
PROBE = cost.PROBE.replace(
    '    for sample in 1 2 3 4 5 6 7 8 9; do rpc "$label-$sample"; done\n}',
    '    for sample in 1 2 3 4 5 6 7 8 9; do rpc "$label-$sample"; done\n'
    '    [ ! -r /var/run/nx300m-lut-status.json ] || '
    'cp /var/run/nx300m-lut-status.json "$OUT/$label.status.json"\n}',
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def publish_evidence(guest):
    """Read the hook's own published counters for every phase."""
    rows = {}
    for path in sorted(guest.glob('*.status.json')):
        label = path.name[:-len('.status.json')]
        try:
            data = json.loads(path.read_text(encoding='utf-8', errors='replace'))
        except json.JSONDecodeError:
            rows[label] = {'error': 'unparsable status'}
            continue
        stats = data.get('stats') or {}
        rows[label] = {'stage': data.get('stage'), 'composition': data.get('composition'),
                       'active': data.get('active'), 'generation': data.get('generation'),
                       'strength': data.get('strength'),
                       'updates': stats.get('updates'), 'ipcs_completes': stats.get('ipcs_completes'),
                       'publishes': stats.get('publishes'),
                       'publish_skips': stats.get('publish_skips')}
    # The counters are cumulative per guest process: each phase is the delta to
    # the phase before it, in the order the probe runs them.
    order = ['stock-before', 'hook-stock', 'lut-zero', 'lut-full', 'lut-half',
             'previous-full', 'previous-half']
    phases, previous = {}, dict(updates=0, publishes=0, publish_skips=0)
    for label in order:
        row = rows.get(label)
        if not row or 'error' in row:
            continue
        delta = {}
        for key in ('updates', 'publishes', 'publish_skips'):
            value, base = row.get(key), previous.get(key, 0)
            if not isinstance(value, int):
                delta[key] = None
            elif isinstance(base, int) and value >= base:
                delta[key] = value - base
            else:
                # Applying another image restarts that image's own counters.
                delta[key] = value
        row['delta'] = delta
        phases[label] = row
        previous = {key: row.get(key) or 0 for key in ('updates', 'publishes', 'publish_skips')}
    evidence = {'phases': phases, 'rows': rows,
                'counters_are_cumulative_per_guest_process': True}
    for label in ('lut-full', 'previous-full'):
        delta = (phases.get(label) or {}).get('delta') or {}
        if isinstance(delta.get('updates'), int) and isinstance(delta.get('publishes'), int) \
                and delta['publishes']:
            evidence[label + '_updates_per_publish'] = delta['updates'] / delta['publishes']
    return evidence


def analyze(out):
    """Same verification as probe_lut_capture_cost_vm.analyze, tolerant of the
    loader's informational stdout lines that precede its JSON result line."""
    execution = json.loads((out / 'execution.json').read_text())
    if execution['exit_code']:
        raise RuntimeError('Capture publish cost guest exit ' + str(execution['exit_code']))
    guest = out / 'guest/mfw-results'
    words = lambda path: list(struct.unpack('<2190H', path.read_bytes()))
    variants = ['stock-before', 'hook-stock', 'lut-zero', 'lut-full', 'lut-half', 'stock-after']
    if (guest / 'previous-full-1.fixture.json').exists():
        variants += ['previous-full', 'previous-half']
    samples, medians = {}, {}
    for variant in variants:
        samples[variant] = []
        for sample in range(1, 10):
            data = json.loads((guest / f'{variant}-{sample}.fixture.json').read_text())
            assert data['reset_each'] and data['repeats'] == 256, data
            samples[variant].append({key: data[f'update_{key}_ns'] / 256 / 1e6 for key in ('wall', 'cpu')})
        medians[variant] = {key: statistics.median(row[key] for row in samples[variant])
                            for key in ('wall', 'cpu')}
    stock = words(guest / 'stock-before-1.bin')
    target = words(out / 'source-files/nx300m-mod/looks/ricoh-negative-exact/table-stock3d.d4lut')
    for variant in variants:
        expected = target if variant in ('lut-full', 'previous-full') else (
            [(a + b + 1) // 2 for a, b in zip(stock, target)] if variant in ('lut-half', 'previous-half')
            else stock)
        for sample in range(1, 10):
            assert words(guest / f'{variant}-{sample}.bin') == expected, variant
    restore = last_json_line(guest / 'restore.json')
    if restore.get('restored') is not True:
        raise RuntimeError('Stock slots were not restored: ' + repr(restore))
    baseline = statistics.mean(medians[name]['wall'] for name in ('stock-before', 'stock-after'))
    report = dict(passed=True, samples_ms_per_update=samples, median_ms_per_update=medians,
                  verified_stock_reset_and_all_three_table_channels=True,
                  stock_zero_half_full_values_checked=True, stock_slots_restored=True,
                  stock_slots_restored_result=restore,
                  lut_full_added_ms_per_update=medians['lut-full']['wall'] - baseline,
                  lut_first_update_ms=json.loads((guest / 'lut-first.fixture.json').read_text())['update_wall_ns'] / 1e6)
    if 'previous-full' in medians:
        report['current_over_previous'] = {key: medians['lut-' + key]['wall'] / medians['previous-' + key]['wall']
                                          for key in ('full', 'half')}
    return report


def last_json_line(path):
    for line in reversed(path.read_text(errors='replace').splitlines()):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise RuntimeError('No JSON result line in ' + path.name)


def comparison_of(medians):
    comparison = {}
    for key in ('full', 'half'):
        if 'previous-' + key in medians:
            comparison[key] = {'baseline': medians['previous-' + key]['wall'],
                               'candidate': medians['lut-' + key]['wall'],
                               'delta': medians['lut-' + key]['wall'] - medians['previous-' + key]['wall'],
                               'ratio': medians['lut-' + key]['wall'] / medians['previous-' + key]['wall']}
    comparison['zero'] = {'candidate': medians['lut-zero']['wall']}
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument('--baseline-dir', type=Path, default=DEFAULT_BASELINE)
    parser.add_argument('--analyze-only', action='store_true',
                        help='Read retained VM evidence without another run')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    candidate = args.candidate_dir.resolve()
    baseline = args.baseline_dir.resolve()
    if args.analyze_only:
        report = json.loads((out / 'report.json').read_text())
        report.update(analyze(out))
        report['passed'] = True
        report['publish'] = publish_evidence(out / 'guest/mfw-results')
        report.pop('error', None)
        report['comparison_ms_per_update'] = comparison_of(report['median_ms_per_update'])
        arm_vm.write_json(out / 'report.json', report)
        print(json.dumps({k: report[k] for k in ('median_ms_per_update', 'comparison_ms_per_update')},
                         ensure_ascii=False, indent=2))
        return
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')

    live.OUT = out
    live.BUILD = candidate
    live.BASELINE = baseline
    live.PROBE = PROBE
    live.configure(factory_only=True)
    live.prepare()

    report = dict(passed=False, physical_capture_timing=False,
                  scope=('Genuine ARM CIpcs3DLutD4::Update plus UDD RAM writer on one private VM boot; '
                         'stock Reset before every update, tracing disabled; no sensor, JPEG or SD timing'),
                  candidate={name: digest(candidate / name) for name in ('lut_ram_blob.bin', 'lut_live_loader.arm')},
                  baseline={name: digest(baseline / name) for name in ('lut_ram_blob.bin', 'lut_live_loader.arm')},
                  source_identity={'runtime/lut_ram_blob.c': digest(ROOT / 'runtime/lut_ram_blob.c'),
                                   'tools/probe_capture_publish_cost_vm.py': digest(Path(__file__))})
    started = False
    try:
        live.call('start')
        started = True
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            serial = out / 'serial.log'
            if serial.exists() and 'LUT_LIVE_PRIVATE_VM_SHELL_READY' in serial.read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Capture publish cost VM boot')
        execution = live.call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        guest = live.collect()
        if execution['exit_code'] or 'LUT_CAPTURE_COST_PASSED' not in execution['output']:
            raise RuntimeError('Capture publish cost guest exit ' + str(execution['exit_code']))
        # Verified here rather than through probe_lut_capture_cost_vm.analyze:
        # the loader now prints two informational lines before its JSON result.
        report.update(analyze(out))
        report['publish'] = publish_evidence(guest)
        report['comparison_ms_per_update'] = comparison_of(report['median_ms_per_update'])
    finally:
        if started:
            report['stop'] = live.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
            if report['private_vm_stopped']:
                (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps({k: report[k] for k in ('passed', 'median_ms_per_update', 'comparison_ms_per_update')
                      if k in report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
