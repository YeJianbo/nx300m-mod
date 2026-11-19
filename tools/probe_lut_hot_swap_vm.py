"""P3 apply-latency work in the private ARM/MFW VM: pause quantification,
same-value confirmation skip, and the stop-free hot table swap.

Scenarios (all against the real loader, real resident module and real stock
worker; physical camera/SD stay named fixture boundaries):
  M1/M2  the unchanged full stop-based apply under a rendering preview: the
         pause-attempt attribution lines, per-phase stop window timing and the
         repeated-confirmation cost being optimized (the "before" behavior).
  H1/H5  same-value confirmation through the hot path: nothing is written and
         no thread stops.
  H2     different table, frames flowing: the request is published stop-free
         into the resident mailbox, consumed at the safe frame boundary and
         verified against the applied generation before reporting success.
  H7     identical look/strength/FPS with different table bytes must not skip.
  H3/H4  no frame callbacks (or a callback held during a capture): the armed
         request reports honest pending rc 76, survives the window and applies
         at the next boundary; the module-side capture gate holds it.
  H6     disabled preview falls back to the full guarded transaction.
  B      the real smartfilter-live-preview.sh sync chain: skip, verified hot
         swap, honest pending marker, and convergence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

import arm_vm
import probe_ipcs_session_vm as session
import probe_smartfilter_vm as boundary
import probe_smartfilter_ram_vm as ram

PREAMBLE = ram.PROBE[:ram.PROBE.index('request original-mode 2')]

APPLY_ID = 'apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100'
APPLY_MONO = 'apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100'
HOT_ID = 'hot-apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100'
HOT_MONO = 'hot-apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100'

PROBE = PREAMBLE + r'''
upt() { IFS=' ' read -r up _ < /proc/uptime; printf '%s' "$up"; }
operation() {
    label=$1; shift
    t0=$(upt)
    "$LOADER" "$@" --pid "$pid" --blob "$BLOB" --fixture > "$label.json" 2> "$label.stderr"
    rc=$?
    t1=$(upt)
    printf '%s %s\n' "$t0" "$t1" > "$label.elapsed"
    return $rc
}
timed() { label=$1; cmd=$2; t0=$(upt); request "$label" "$cmd"; t1=$(upt); printf '%s %s\n' "$t0" "$t1" > "$label.elapsed"; }
# Install the identity S2 preview through the unchanged full-control path
# (first install keeps the stop-based transaction).
operation install '''+APPLY_ID+r'''
install_rc=$?
timed install-frame 154
# Dispatch the topology rebuild the first frame queued, so the resident
# reaches its steady S2 state (refresh_queued=0) before the hot scenarios.
timed install-converge 120
operation install-status status
timed install-state 153
# M1: the current behavior - full stop-based apply against one held frame:
# the first attempt is deterministically busy, the pause is attributed and
# the phases are timed. One frame source at a time: the stock node is not
# reentrant, so the pump never overlaps the held frame or the join frame.
timed m1-busy 163
operation m1-apply '''+APPLY_MONO+r'''
m1_rc=$?
timed m1-release 164
timed m1-frame 154
operation m1-status status
timed m1-state 153
# M1b: the same full apply while frames keep flowing (pump only): the retry
# count under real load, exactly the card-log pattern.
timed m1b-pump-on 155
operation m1b-apply '''+APPLY_MONO+r'''
m1b_rc=$?
timed m1b-pump-off 157
timed m1b-frame 154
operation m1b-status status
timed m1b-state 153
# M2: repeated same-value confirmation through the full path - the cost the
# hot path removes. One held frame keeps the first attempt busy.
timed m2-busy 163
operation m2-apply '''+APPLY_MONO+r'''
m2_rc=$?
timed m2-release 164
timed m2-frame 154
operation m2-status status
timed m2-state 153
# Dispatch any rebuild the pause-window frames queued so the hot path sees
# the steady state (refresh_queued=0).
timed m2-converge 120
timed m2-state2 153
# H1: same-value confirm through the hot path: nothing written, no stop.
operation h1-skip '''+HOT_MONO+r'''
h1_rc=$?
timed h1-state 153
# H2: different table through the hot path while frames flow: published
# stop-free, consumed at the safe frame boundary, verified by generation.
timed h2-pump-on 155
set +e
operation h2-hot '''+HOT_ID+r'''
h2_rc=$?
set -e
timed h2-pump-off 157
timed h2-state 153
timed h2-frame 154
timed h2-state2 153
# H3: no frames - the armed request reports honest pending rc 76, the capture
# gate holds it, then one frame boundary consumes and applies it.
set +e
t0=$(upt)
NX_SF_HOT_POLL_MS=300 "$LOADER" '''+HOT_MONO+r''' --pid "$pid" --blob "$BLOB" --fixture > h3-pending.json 2> h3-pending.stderr
h3_rc=$?
t1=$(upt)
set -e
printf '%s %s\n' "$t0" "$t1" > h3-pending.elapsed
timed h3-gate 160
timed h3-state 153
timed h3-frame 154
timed h3-state2 153
# H4: a running stream without frame callbacks (plus a still capture inside
# the window): the request stays armed and reports pending; one later
# boundary applies it.
timed h4-stream-on 161
timed h4-capture-arm 158
set +e
t0=$(upt)
NX_SF_HOT_POLL_MS=400 "$LOADER" '''+HOT_ID+r''' --pid "$pid" --blob "$BLOB" --fixture > h4-pending.json 2> h4-pending.stderr
h4_rc=$?
t1=$(upt)
set -e
printf '%s %s\n' "$t0" "$t1" > h4-pending.elapsed
timed h4-capture-done 159
timed h4-stream-off 162
timed h4-state 153
timed h4-frame 154
timed h4-state2 153
# H5: after convergence the same request is a skip again.
operation h5-skip '''+HOT_ID+r'''
h5_rc=$?
timed h5-state 153
# H6: disabled preview falls back to the full stop-based transaction.
operation h6-off restore
h6off_rc=$?
timed h6-frame 154
timed h6-converge 120
operation h6-hot '''+HOT_MONO+r'''
h6_rc=$?
timed h6-frame2 154
# Dispatch the rebuild the re-enabled frame queued, returning the resident to
# the steady running state before the chain scenarios.
timed h6-converge2 120
operation h6-status status
timed h6-state 153
# H7: identical look/strength/effective FPS, but a different table file. The
# loader must compare resident table bytes and publish a stop-free hot swap.
if cmp -s /mnt/mmc/identity.d4lut /mnt/mmc/monochrome.d4lut; then exit 1; fi
timed h7-pump-on 155
operation h7-same-id-different-table hot-apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look monochrome --strength 100
h7_rc=$?
timed h7-pump-off 157
timed h7-state 153
timed h7-frame 154
timed h7-state2 153
printf '%s %s %s %s %s %s %s %s %s %s %s\n' "$install_rc" "$m1_rc" "$m1b_rc" "$m2_rc" "$h1_rc" "$h2_rc" "$h3_rc" "$h4_rc" "$h5_rc" "$h6_rc" "$h7_rc" > rc.summary
timed refs 105
cat /proc/$pid/maps > after.maps
# B: the real control script - sync reaches the hot path; repeated sync skips;
# without frames the chain reports the honest pending marker. The fixture
# process stays alive: every control below targets it through the chain.
RUNTIME=/tmp/nx300m-hot-runtime
HOT_STATE=/tmp/nx300m-mfw-state-hot
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/preview-tests" "$HOT_STATE" /perf/.state /perf/looks/identity /perf/looks/monochrome
hot_source_id=$(stat -c '%d:%i' /perf)
export NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID="$hot_source_id" NX_SF_PROBE_FIXTURE=1 NX_STATE_DIR="$HOT_STATE"
cp /mnt/mmc/identity.d4lut /perf/looks/identity/table.d4lut
cp /mnt/mmc/monochrome.d4lut /perf/looks/monochrome/table.d4lut
for pair in /mnt/mmc/bin/smartfilter_probe_launcher.arm:bin/smartfilter_probe_launcher.arm /mnt/mmc/bin/smartfilter_loader.arm:bin/smartfilter_loader.arm /mnt/mmc/bin/ram_asset_verify.arm:bin/ram_asset_verify.arm /mnt/mmc/smartfilter_ram.bin:lib/smartfilter_ram.bin /mnt/mmc/smartfilter-probe-collector.sh:smartfilter-probe-collector.sh; do
    cp "${pair%%:*}" "$RUNTIME/${pair#*:}"
done
cp /mnt/mmc/smartfilter-live-preview.sh "$RUNTIME/smartfilter-live-preview.sh"
chmod 500 "$RUNTIME/"*.sh "$RUNTIME/bin/"*.arm
chmod 400 "$RUNTIME/lib/smartfilter_ram.bin"
printf 'smartfilter\n' > /var/run/nx300m-preview-backend
sync_run() {
    label=$1
    t0=$(upt)
    NX_STATE_DIR="$HOT_STATE" /bin/sh "$RUNTIME/smartfilter-live-preview.sh" sync > "$label.stdout" 2> "$label.stderr"
    rc=$?
    t1=$(upt)
    printf '%s %s\n' "$t0" "$t1" > "$label.elapsed"
    printf '%s\n' "$rc" > "$label.exit"
    [ "$rc" = 0 ]
}
# B1: the resident holds the H7 identity bytes under the monochrome look id,
# so the first chain sync is a real table swap: converge it with frames
# flowing, then the unchanged second sync is the same-value skip.
printf '90 /perf/looks/monochrome/table.d4lut 100\n' > /var/run/nx300m-lut-selection
timed b1-pump-on 155
sync_run b1-converge
timed b1-pump-off 157
sync_run b1-skip
# B2: different table with frames flowing: verified hot swap in the chain.
timed b2-pump-on 155
printf '91 /perf/looks/identity/table.d4lut 100\n' > /var/run/nx300m-lut-selection
sync_run b2-hot
timed b2-pump-off 157
# B3: same values again: skip.
sync_run b3-skip
# B4: table change with no frames: the chain exits 0 with the honest pending
# marker; the armed request stays the latest one.
export NX_SF_HOT_POLL_MS=300
printf '92 /perf/looks/monochrome/table.d4lut 100\n' > /var/run/nx300m-lut-selection
sync_run b4-pending
unset NX_SF_HOT_POLL_MS
# B5: frames flow, the armed request applies, the next sync is a skip again.
timed b5-pump-on 155
timed b5-pump-off 157
sync_run b5-skip
cat /proc/$pid/maps > after2.maps 2>/dev/null || true
seq=$((seq+1)); printf '%s 0\n' "$seq" >/tmp/smartfilter-ram.request
wait "$pid"; pid=
echo LUT_HOT_SWAP_PROBE_DONE
'''


def uptime_ms(path):
    first, second = path.read_text().split()
    return int((float(first) + float(second)) * 1000)


def stop_windows(path):
    return [int(v) for v in re.findall(r'Target stop window: (\d+) ms;', path.read_text(errors='replace'))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if (out / 'report.json').exists():
        raise RuntimeError('Preserve recorded evidence; use a new output directory')
    session.OUT = boundary.OUT = out
    boundary.SOCKET = '/tmp/nx300m-lut-hot-swap-private'
    boundary.MARKER = 'LUT_HOT_SWAP_PRIVATE_VM_READY'
    boundary.PROBE = PROBE
    boundary.call = session.call
    boundary.configure()
    session.prepare()
    root = Path(__file__).resolve().parents[1]
    report = dict(passed=False, physical_camera_verified=False,
                  scope='Actual ARM hooks, resident mailbox consumption at the real frame callback, '
                        'stock worker doubles for hardware; no camera, no card')
    nxui = root / 'outputs/metol/nxui.arm'
    report['panel_busy_pending_marker'] = nxui.exists() and b'LUT_PREVIEW_BUSY_PENDING' in nxui.read_bytes()
    report['source_identity'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                 for name in ('runtime/smartfilter_ram.c', 'runtime/smartfilter_ram.h',
                                              'runtime/smartfilter_ram_fixture.c', 'runtime/smartfilter_loader.c',
                                              'mod/deployment/mfw/smartfilter-live-preview.sh',
                                              'mod/metol/nxui.c')}
    before = out.parent / 'p3-before-bins'
    if before.exists():
        report['before_binaries'] = {line.split()[1]: line.split()[0]
                                     for line in (before / 'SHA256SUMS.txt').read_text().splitlines()}
    started = False
    try:
        boundary.call('start'); started = True
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if (out / 'serial.log').exists() and boundary.MARKER in (out / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Hot swap private ARM VM startup')
        execution = boundary.call('exec', '/bin/sh /arm-vm/mfw-probe.sh', 600)
        arm_vm.write_json(out / 'execution.json', execution)
        guest = boundary.collect()
        if execution['exit_code'] or 'LUT_HOT_SWAP_PROBE_DONE' not in execution['output']:
            raise RuntimeError('Hot swap fixture failed; see execution.json and guest evidence')
        rows = [json.loads(line) for line in (guest / 'smartfilter-ram.stdout').read_text(encoding='utf-8').splitlines()
                if line.startswith('{')]
        hot_rows = [row for row in rows if row['type'] == 'fixture_hot']
        refs = next(row for row in rows if row['type'] == 'fixture_refs')
        checks = {}

        def pick(index):
            return hot_rows[index]

        def stderr(name):
            return (guest / (name + '.stderr')).read_text(errors='replace')

        def json_file(name):
            return json.loads((guest / (name + '.json')).read_text(encoding='utf-8'))

        def elapsed(name):
            first, second = (guest / (name + '.elapsed')).read_text().split()
            return int((float(second) - float(first)) * 1000)

        rc_values = (guest / 'rc.summary').read_text().split()
        rcs = dict(zip(('install', 'm1', 'm1b', 'm2', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'h7'),
                       rc_values))
        checks['all_expected_rcs'] = rcs == dict(install='0', m1='0', m1b='0', m2='0', h1='0', h2='0',
                                                 h3='76', h4='76', h5='0', h6='0', h7='0')
        install, m1, m1b, m2, m2b = pick(0), pick(1), pick(2), pick(3), pick(4)
        h1, h2, h2b, h3, h3b, h4, h4b, h5, h6 = pick(5), pick(6), pick(7), pick(8), pick(9), pick(10), pick(11), pick(12), pick(13)
        # M: the before-behavior baselines keep full control semantics.
        m1_windows, m2_windows = stop_windows(guest / 'm1-apply.stderr'), stop_windows(guest / 'm2-apply.stderr')
        checks['m1_busy_retries_attribution'] = (len(m1_windows) >= 2 and
                                                 'Pause attempt:' in stderr('m1-apply') and
                                                 'Control attempts: count=' in stderr('m1-apply') and
                                                 'attach=' in stderr('m1-apply') and 'commit=' in stderr('m1-apply'))
        checks['m1_converged_by_generation'] = (m1['applied'] == m1['generation'] and
                                                json_file('m1-status')['applied_generation'] ==
                                                json_file('m1-status')['requested_generation'])
        m1b_windows = stop_windows(guest / 'm1b-apply.stderr')
        checks['m1b_full_apply_under_load'] = (rcs['m1b'] == '0' and len(m1b_windows) >= 1 and
                                               m1b['applied'] == m1b['generation'] and
                                               m1b['generation'] > m1['generation'])
        checks['m2_same_value_still_full_control'] = (len(m2_windows) >= 2 and m2['generation'] > m1b['generation'] and
                                                      m2['applied'] == m2['generation'])
        # H1: repeated confirmation touches nothing.
        checks['h1_skip_no_stop_no_generation'] = (not stop_windows(guest / 'h1-skip.stderr') and
                                                   'Hot transaction: skipped' in stderr('h1-skip') and
                                                   h1['generation'] == m2['generation'] and
                                                   h1['applied'] == h1['generation'] and
                                                   not h1['mail_seq'])
        checks['h1_faster_than_full_confirm'] = elapsed('h1-skip') < elapsed('m2-apply')
        # H2: stop-free verified swap under load.
        h2_windows = stop_windows(guest / 'h2-hot.stderr')
        checks['h2_hot_swap_no_stop'] = (not h2_windows and 'Hot transaction: applied' in stderr('h2-hot') and
                                         h2['mail_consumed'] >= 1 and not h2['mail_seq'] and
                                         h2['generation'] > h1['generation'] and h2['applied'] == h2['generation'] and
                                         h2['look'] == 'identity' and h2['control_pause'] == 0)
        checks['h2_faster_than_full_swap'] = elapsed('h2-hot') < elapsed('m1-apply')
        # H3: honest pending, capture gate holds it, one boundary applies it.
        pending3 = json_file('h3-pending')
        checks['h3_pending_reported'] = (pending3.get('status') == 'pending' and
                                         'Hot transaction: pending' in stderr('h3-pending') and
                                         h3['mail_seq'] == pending3['generation'] and
                                         h3['mail_consumed'] == h2['mail_consumed'] and
                                         h3['applied'] == h2['generation'])
        checks['h3_capture_gate_holds_request'] = h3['captures'] == 0 and h3['mail_rejected'] == 0
        checks['h3_boundary_applies_armed_request'] = (h3b['applied'] == h3b['generation'] == pending3['generation'] and
                                                       not h3b['mail_seq'] and h3b['look'] == 'monochrome')
        # H4: the armed request survives a running stream plus a capture window.
        pending4 = json_file('h4-pending')
        checks['h4_pending_survives_stream_and_capture'] = (pending4.get('status') == 'pending' and
                                                            h4['mail_seq'] == pending4['generation'] and
                                                            h4['mail_consumed'] == h3b['mail_consumed'] and
                                                            h4['applied'] == h3b['generation'])
        checks['h4_boundary_applies_after_stream'] = (h4b['applied'] == h4b['generation'] == pending4['generation'] and
                                                      h4b['look'] == 'identity')
        # H5/H6: convergence skips again; disabled preview falls back to the
        # full guarded transaction.
        checks['h5_converged_skip'] = (not stop_windows(guest / 'h5-skip.stderr') and
                                       'Hot transaction: skipped' in stderr('h5-skip') and
                                       h5['generation'] == h4b['generation'] and h5['applied'] == h5['generation'])
        h6_windows = stop_windows(guest / 'h6-hot.stderr')
        checks['h6_disabled_falls_back_to_full_control'] = (len(h6_windows) >= 1 and
                                                            h6['generation'] > h5['generation'] and
                                                            h6['enabled'] == 1 and h6['strength'] == 100 and
                                                            h6['applied'] == h6['generation'] and h6['look'] == 'monochrome')
        status6 = json_file('h6-status')
        checks['h6_status_generation_verified'] = (status6['applied_generation'] == status6['requested_generation'] and
                                                   status6['enabled'] is True and status6['look_id'] == 'monochrome')
        h7 = pick(14)
        checks['h7_same_id_different_table_applied'] = (
            not stop_windows(guest / 'h7-same-id-different-table.stderr') and
            'Hot transaction: applied' in stderr('h7-same-id-different-table') and
            'reason=latest-request-already-applied' not in stderr('h7-same-id-different-table') and
            h7['generation'] > h6['generation'] and h7['mail_consumed'] > h6['mail_consumed'] and
            not h7['mail_seq'] and h7['applied'] == h7['generation'] and h7['look'] == 'monochrome' and
            h7['control_pause'] == 0)
        # Generation monotonicity across the whole run (h1/h5 repeat values
        # without generation bumps only where the request is already applied).
        generations = [install['generation'], m1['generation'], m1b['generation'], m2['generation'], m2b['generation'],
                       h1['generation'], h2['generation'], h2b['generation'], h3['generation'], h3b['generation'],
                       h4['generation'], h4b['generation'], h5['generation'], h6['generation'], h7['generation']]
        # The three skips (m2b dump, h1 confirm, h5 confirm) repeat the
        # previous generation; every other control bumps it.
        # Monotonic; the two pure skips (h1 after the converged M2 state, h5
        # after the applied H4 request) do not bump the resident generation.
        checks['generation_monotonic'] = (generations == sorted(generations) and
                                          generations[5] == generations[3] and
                                          generations[12] == generations[11] and
                                          generations[13] > generations[12])
        checks['refs_balanced'] = not refs['held'] and refs['opens'] == refs['closes']
        # B: the real script chain.
        checks['b1_chain_converge_no_stop'] = ((guest / 'b1-converge.exit').read_text().strip() == '0' and
                                               'Hot transaction: applied' in stderr('b1-converge') and
                                               'LUT_PREVIEW_BUSY_PENDING' not in (guest / 'b1-converge.stdout').read_text(errors='replace') and
                                               not stop_windows(guest / 'b1-converge.stderr'))
        checks['b1_chain_skip_no_stop'] = (not stop_windows(guest / 'b1-skip.stderr') and
                                           'Hot transaction: skipped' in stderr('b1-skip') and
                                           'LUT_PREVIEW_BUSY_PENDING' not in (guest / 'b1-skip.stdout').read_text(errors='replace') and
                                           'Hot transaction:' in stderr('b1-skip'))
        checks['b2_chain_hot_swap_no_stop'] = (not stop_windows(guest / 'b2-hot.stderr') and
                                               'Hot transaction: applied' in stderr('b2-hot'))
        checks['b3_chain_repeat_skips'] = ('Hot transaction: skipped' in stderr('b3-skip') and
                                           not stop_windows(guest / 'b3-skip.stderr'))
        checks['b4_chain_pending_marker'] = ((guest / 'b4-pending.exit').read_text().strip() == '0' and
                                             'LUT_PREVIEW_BUSY_PENDING' in (guest / 'b4-pending.stdout').read_text(errors='replace') and
                                             '已保存；拍摄或场景切换结束后自动生效' in (guest / 'b4-pending.stdout').read_text(errors='replace'))
        checks['b5_chain_converged_skip'] = ('Hot transaction: skipped' in stderr('b5-skip'))
        # The elapsed comparison table for the report.
        report['timing'] = {
            'install_ms': elapsed('install'), 'm1_apply_ms': elapsed('m1-apply'),
            'm1b_apply_ms': elapsed('m1b-apply'), 'm2_apply_ms': elapsed('m2-apply'),
            'h1_skip_ms': elapsed('h1-skip'), 'h2_hot_ms': elapsed('h2-hot'),
            'm1_stop_windows_ms': m1_windows, 'm1_stop_windows_total_ms': sum(m1_windows),
            'm1b_stop_windows_ms': m1b_windows, 'm1b_stop_windows_total_ms': sum(m1b_windows),
            'm2_stop_windows_ms': m2_windows, 'm2_stop_windows_total_ms': sum(m2_windows),
            'h2_stop_windows_ms': h2_windows, 'h6_stop_windows_ms': h6_windows,
            'b1_skip_ms': elapsed('b1-skip'), 'b2_hot_ms': elapsed('b2-hot'),
            'b3_skip_ms': elapsed('b3-skip'), 'b4_pending_ms': elapsed('b4-pending'),
            'm1_pause_attempts': stderr('m1-apply').count('Pause attempt:'),
            'm1_attempts': stderr('m1-apply').split('Control attempts: count=')[1].split('.')[0],
            'm1b_pause_attempts': stderr('m1b-apply').count('Pause attempt:'),
            'm1_stock_path_frames': m1['passthrough'] - install['passthrough'],
            'h2_stock_path_frames': h2['passthrough'] - m2['passthrough'],
        }
        report['observed'] = {name: row for name, row in zip(
            ('install', 'm1', 'm1b', 'm2', 'm2_converged', 'h1', 'h2', 'h2_after', 'h3', 'h3_after',
             'h4', 'h4_after', 'h5', 'h6', 'h7'), hot_rows)}
        report['pending_json'] = {'h3': pending3, 'h4': pending4}
        report['rcs'] = rcs
        report['checks'] = checks
        report['passed'] = all(checks.values())
        if not report['passed']:
            raise AssertionError(checks)
    finally:
        if started:
            report['stop'] = boundary.call('stop')
        arm_vm.write_json(out / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
