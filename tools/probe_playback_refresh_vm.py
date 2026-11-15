"""Replay/preview ownership regression using installed ARM hooks and stock IQ code.

Retains playback LUT selection. Tests stale refresh messages, native stop/start,
latest-selection restoration, and normal no-map-change recovery. Physical
streaming and IPCS remain the existing explicitly named fixture boundaries.
"""
import argparse
import json
from pathlib import Path
import time

import arm_vm
import probe_ipcs_session_vm as session
import probe_smartfilter_vm as boundary
import probe_smartfilter_ram_vm as ram

PROBE = ram.PROBE[:ram.PROBE.index('request original-mode 2')] + r'''
operation initial apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request inactive-state 125
request stale-dispatch 126
operation stale-state status
request native-stop 127
# Selection is submitted while stopped; its latest table must survive return.
operation playback-selection apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100
request native-return 128
operation returned-state status
operation ordinary-stop restore
request ordinary-remove 120
operation ordinary-apply apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request ordinary-memory-recovery 123
operation ordinary-final status
operation session-apply apply --s2 --session 1 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request session-frames 100
request session-stop-start 109
request session-resumed 100
request session-capture 101
operation session-stop restore
request session-refs 105
seq=$((seq+1)); printf '%s 0\n' "$seq" >/tmp/smartfilter-ram.request
wait "$pid"; pid=
echo PLAYBACK_REFRESH_PROBE_DONE
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prepared', action='store_true')
    parser.add_argument('--expect-before-failure', action='store_true')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve previous evidence; select a new output directory')
    session.OUT = boundary.OUT = out
    boundary.SOCKET = '/tmp/nx300m-ipcs-session-private'
    boundary.MARKER = 'IPCS_SESSION_PRIVATE_VM_READY'
    boundary.PROBE = PROBE
    if args.expect_before_failure:
        boundary.PROBE = PROBE[:PROBE.index('# Selection is submitted')] + '''
seq=$((seq+1)); printf '%s 0\n' "$seq" >/tmp/smartfilter-ram.request
wait "$pid"; pid=
echo PLAYBACK_REFRESH_PROBE_DONE
'''
    boundary.call = session.call
    boundary.configure()
    if args.prepared:
        boundary.vm.initramfs()
    else:
        session.prepare()
    started = False
    report = dict(passed=False, physical_camera_verified=False,
                  scope='Actual ARM hooks, stock message dispatcher/memory matcher; hardware stream/ISP doubles')
    try:
        boundary.call('start'); started = True
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and boundary.MARKER in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Playback private ARM VM startup')
        execution = boundary.call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        arm_vm.write_json(out / 'execution.json', execution)
        guest = boundary.collect()
        if execution['exit_code'] or 'PLAYBACK_REFRESH_PROBE_DONE' not in execution['output']:
            raise RuntimeError('Playback fixture failed; see execution.json and guest evidence')
        rows = []
        for line in (guest / 'smartfilter-ram.stdout').read_text(encoding='utf-8').splitlines():
            if line.startswith('{'):
                rows.append(json.loads(line))
        def row(kind):
            return next(value for value in rows if value.get('type') == kind)
        inactive = row('fixture_refresh_inactive')
        stale = row('fixture_refresh_stale_dispatch')
        stopped = row('fixture_refresh_stock_stop')
        checks = dict(
            stopped_and_transitioning_frames_do_not_queue=inactive['unsafe_requests'] == 0,
            stale_request_does_not_restart_stopped_liveview=stale['rebuilds'] == 0 and stale['state'] == 20 and not stale['pending'],
            native_stop_cancels_refresh=not stopped['pending'] and not stopped['queued'] and stopped['stopped'] == 1,
        )
        observed = dict(inactive=inactive, stale=stale, stopped=stopped)
        if not args.expect_before_failure:
            returned = row('fixture_refresh_return')
            final = json.loads((guest / 'ordinary-final.json').read_text().splitlines()[0])
            observed['returned'] = returned
            checks.update(
                return_uses_latest_playback_selection=not returned['stopped'] and not returned['pending'] and returned['s2_path'] == 1 and returned['look'] == 'monochrome' and not returned['extra_rebuilds'],
                ordinary_native_memory_resume_preserved=not final['refresh_pending'] and final['s2_path'] == 1,
                session_stop_start_and_capture_preserved=(guest / 'session-stop-start.response').is_file() and (guest / 'session-capture.response').is_file(),
                session_refs_released=row('fixture_refs')['held'] == 0 and row('fixture_refs')['opens'] == row('fixture_refs')['closes'],
            )
        report.update(checks=checks, observed=observed,
                      actionable_passed=all(checks.values()), before_counterexample_confirmed=not all(checks.values()))
        report['passed'] = not all(checks.values()) if args.expect_before_failure else all(checks.values())
        if not report['passed']:
            raise AssertionError(checks)
    finally:
        if started:
            report['stop'] = boundary.call('stop')
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
