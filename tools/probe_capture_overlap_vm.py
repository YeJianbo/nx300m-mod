"""Replay overlapping capture and delayed preview handoff with installed ARM hooks."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import arm_vm
import probe_ipcs_session_vm as session
import probe_smartfilter_vm as boundary
import probe_smartfilter_ram_vm as ram

PROBE = ram.PROBE[:ram.PROBE.index('request original-mode 2')] + r'''
operation session-apply apply --s2 --session 1 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request session-frames 100
request overlapping-captures 129
request overlap-resumed 100
request delayed-handoff 130
request delayed-resumed 100
request delayed-reset 131
request reset-resumed 100
request delayed-iq 132
request iq-resumed 100
request delayed-unlock 133
request unlock-resumed 100
request retained-reference 134
request retained-resumed 100
operation session-stop restore
request session-refs 105
/mnt/mmc/bin/ipcs_session_failure_fixture.arm > session-failures.ndjson 2> session-failures.stderr
operation failure-apply apply --s2 --session 1 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request before-failure 100
request rejected-open-close 106
request fault-passthrough 107
request rejected-native-stop 108
request final-refs 105
seq=$((seq+1)); printf '%s 0\n' "$seq" >/tmp/smartfilter-ram.request
wait "$pid"; pid=
echo CAPTURE_OVERLAP_PROBE_DONE
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    session.OUT = boundary.OUT = out
    boundary.SOCKET = '/tmp/nx300m-capture-overlap-private'
    boundary.MARKER = 'CAPTURE_OVERLAP_PRIVATE_VM_READY'
    boundary.PROBE = PROBE
    boundary.call = session.call
    boundary.configure()
    session.prepare()
    report = dict(passed=False, physical_camera_verified=False,
                  scope='Installed ARM hooks and stock B2Y/SmartFilter; device/IRQ and SD are fixture boundaries')
    root = Path(__file__).resolve().parents[1]
    report['source_identity'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                               for name in ('runtime/smartfilter_session.inc', 'runtime/smartfilter_ram.c',
                                            'runtime/smartfilter_ram_fixture.c')}
    started = False
    try:
        boundary.call('start'); started = True
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and boundary.MARKER in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Capture private ARM VM startup')
        execution = boundary.call('exec', '/bin/sh /arm-vm/mfw-probe.sh', vm_timeout=90)
        arm_vm.write_json(out / 'execution.json', execution)
        guest = boundary.collect()
        if execution['exit_code'] or 'CAPTURE_OVERLAP_PROBE_DONE' not in execution['output']:
            raise RuntimeError('Capture fixture failed; see guest/mfw-results/smartfilter-ram.stderr')
        rows = [json.loads(line) for line in (guest / 'smartfilter-ram.stdout').read_text().splitlines()
                if line.startswith('{')]
        by_kind = {row['type']: row for row in rows}
        refs = by_kind['fixture_refs']
        delayed = [row for row in rows if row['type'] == 'fixture_capture_slow_handoff']
        report['checks'] = dict(
            overlapping_capture=by_kind['fixture_capture_overlap']['balanced'],
            delayed_handoff={row['stage'] for row in delayed} == {0, 1, 2, 3} and
                            all(row['native_open_preserved'] and row['balanced'] for row in delayed),
            retained_reference=by_kind['fixture_capture_retained_reference']['native_prepare_preserved'],
            references_released=not refs['held'] and refs['opens'] == refs['closes'],
            uncertain_release_guards=(guest / 'rejected-open-close.response').exists() and
                                     (guest / 'rejected-native-stop.response').exists(),
        )
        report['passed'] = all(report['checks'].values())
        if not report['passed']:
            raise AssertionError(report['checks'])
    finally:
        if started:
            report['stop'] = boundary.call('stop')
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
