"""Check the Still-LUT status publication obligations of the shipped image.

Drives the genuine Update callback in a private ARM VM and reads back what the
hook published. Covers only the status file: same state twice, state changes,
external removal, external replacement, a blocked rename and its repair, plus
the stock-slot restore. It times nothing.
"""
import argparse
import json
from pathlib import Path
import time

import arm_vm
import probe_lut_live_loader_vm as live

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE = ROOT / 'outputs/review/2026-11-15-pi-capture-performance/candidate'

PROBE = r'''#!/bin/sh
set -eu
OUT=/arm-vm/mfw-results
LOADER=/mnt/mmc/bin/lut_live_loader.arm
ROOT=/mnt/mmc/nx300m-mod
LOOK=$ROOT/looks/ricoh-negative-exact/table.d4lut
/mnt/mmc/bin/lut_live_fixture.arm > "$OUT/fixture.stdout" 2> "$OUT/fixture.stderr" &
pid=$!
trap 'kill "$pid" 2>/dev/null || :' EXIT
attempt=0
until [ -s "$OUT/fixture.ready" ]; do
    attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
done
rm -f /var/run/nx300m-lut-trace-enabled /var/run/nx300m-lut-status.json
seq=0
rpc() {
    tag=$1; repeats=${2:-1}; seq=$((seq+1))
    printf '%s 1 2 %s %s\n' "$seq" "$tag" "$repeats" > /var/run/lut-live-request.tmp
    mv /var/run/lut-live-request.tmp /var/run/lut-live-request
    attempt=0
    while :; do
        response=0
        [ ! -r /var/run/lut-live-response ] || read response ignored < /var/run/lut-live-response
        [ "$response" = "$seq" ] && break
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ]; /rescue/busybox usleep 50000
    done
}
snap() {
    if [ -f /var/run/nx300m-lut-status.json ]; then
        cp /var/run/nx300m-lut-status.json "$OUT/$1.status.json"
    else
        printf 'absent\n' > "$OUT/$1.absent"
    fi
}
"$LOADER" apply --pid "$pid" --blob /mnt/mmc/lut_ram_blob.bin --root "$ROOT" --fixture > "$OUT/apply.json" 2> "$OUT/apply.stderr"
printf '11 %s 100\n' "$LOOK" > /var/run/nx300m-lut-selection
rpc steady-a 32
snap steady-a
rpc steady-b 32
snap steady-b
rm -f /var/run/nx300m-lut-status.json
rpc after-delete 1
snap after-delete
printf '{"stage":"foreign","active":9}\n' > /var/run/nx300m-lut-status.json
rpc after-foreign 1
snap after-foreign
printf '12 %s 40\n' "$LOOK" > /var/run/nx300m-lut-selection
rpc after-strength 1
snap after-strength
printf '13 %s/looks/cache-probe/table.d4lut 100\n' "$ROOT" > /var/run/nx300m-lut-selection
rpc after-path 1
snap after-path
rm -f /var/run/nx300m-lut-status.json
mkdir /var/run/nx300m-lut-status.json
rpc blocked 4
snap blocked
rmdir /var/run/nx300m-lut-status.json
rpc repaired 1
snap repaired
"$LOADER" restore --pid "$pid" --fixture > "$OUT/restore.json" 2> "$OUT/restore.stderr"
seq=$((seq+1)); printf '%s 2 0 quit\n' "$seq" > /var/run/lut-live-request
wait "$pid"
echo CAPTURE_PUBLISH_OBLIGATIONS_DONE
'''


def last_state(guest, tag):
    path = guest / f'{tag}.status.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(errors='replace'))


def checks_of(states, guest):
    stats = lambda tag: (states[tag] or {}).get('stats') or {}
    a, b = stats('steady-a'), stats('steady-b')
    delta = {key: (b.get(key) or 0) - (a.get(key) or 0) for key in ('updates', 'publishes', 'publish_skips')}
    # Contract: an unchanged state costs at most one publish per refresh period.
    unchanged = (delta['updates'] >= 30 and
                 delta['publish_skips'] >= delta['updates'] - 2 and
                 delta['publishes'] <= delta['updates'] // 32 + 1)
    return {
        'first_state_published': bool(states['steady-a']) and
                                 states['steady-a']['stage'] == 'forwarded-to-stock-api',
        'unchanged_state_not_rewritten': unchanged,
        'deletion_republished': bool(states['after-delete']) and
                                states['after-delete']['stage'] == 'forwarded-to-stock-api',
        'foreign_replacement_overwritten': bool(states['after-foreign']) and
                                           states['after-foreign']['stage'] == 'forwarded-to-stock-api' and
                                           states['after-foreign']['active'] == 1,
        'strength_change_published': (states['after-strength'] or {}).get('strength') == 40,
        'path_change_published': (states['after-path'] or {}).get('selection_path', '').endswith(
            '/looks/cache-probe/table.d4lut'),
        'blocked_rename_hook_alive_and_no_stale_file': (
            (guest / 'blocked.absent').exists() and not (guest / 'blocked.status.json').exists() and
            stats('after-path').get('updates', 0) > 0),
        'repaired_republished': (states['repaired'] or {}).get('stage') == 'forwarded-to-stock-api' and
                                (states['repaired'] or {}).get('strength') == 100,
    }, delta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--candidate-dir', type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument('--analyze-only', action='store_true',
                        help='Read retained VM evidence without another run')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if args.analyze_only:
        report = json.loads((out / 'report.json').read_text())
        states = {tag: last_state(out / 'guest/mfw-results', tag) for tag in
                  ('steady-a', 'steady-b', 'after-delete', 'after-foreign', 'after-strength',
                   'after-path', 'repaired')}
        report['states'] = states
        report['checks'], report['unchanged_state_delta'] = checks_of(states, out / 'guest/mfw-results')
        report['passed'] = all(report['checks'].values())
        report['error'] = None
        arm_vm.write_json(out / 'report.json', report)
        print(json.dumps({k: report[k] for k in ('passed', 'checks', 'unchanged_state_delta')},
                         ensure_ascii=False, indent=2))
        return
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    live.OUT = out
    live.BUILD = args.candidate_dir.resolve()
    live.PROBE = PROBE
    live.configure(factory_only=True)
    live.prepare()
    report = dict(passed=False, physical_camera_verified=False,
                  scope='Genuine stock Update plus UDD RAM writer in one private ARM VM; status file only, no timing')
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
            raise TimeoutError('Publish obligation VM boot')
        execution = live.call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        guest = live.collect()
        if execution['exit_code'] or 'CAPTURE_PUBLISH_OBLIGATIONS_DONE' not in execution['output']:
            raise RuntimeError('Publish obligation guest exit ' + str(execution['exit_code']))
        states = {tag: last_state(guest, tag) for tag in
                  ('steady-a', 'steady-b', 'after-delete', 'after-foreign', 'after-strength',
                   'after-path', 'repaired')}
        report['states'] = states
        report['no_status_file_while_rename_blocked'] = (guest / 'blocked.absent').exists() and \
            not (guest / 'blocked.status.json').exists()
        report['checks'], report['unchanged_state_delta'] = checks_of(states, guest)
        report['passed'] = all(report['checks'].values())
        if not report['passed']:
            raise AssertionError(report['checks'])
    finally:
        if started:
            report['stop'] = live.call('stop')
            report['private_vm_stopped'] = 'return' in report['stop']
        arm_vm.write_json(out / 'report.json', report)
        if report.get('private_vm_stopped'):
            (out / 'initramfs.cpio.gz').unlink(missing_ok=True)
    print(json.dumps({k: report.get(k) for k in ('passed', 'checks')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
