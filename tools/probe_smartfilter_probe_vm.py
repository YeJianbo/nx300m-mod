"""Finite ABI4 probe-once integration suite using the existing private ARM runner.

Run separately from probe_smartfilter_ram_vm.py and probe_smartfilter_s2_vm.py.
This script builds/boots only when invoked as a program; importing it does not.
The real loader, RAM bridge and stock worker execute; fixture hardware/cache
boundaries remain simulated. No camera, H: card or production media is used.
"""
import base64
import hashlib
import json
import re
import subprocess

import arm_vm
import probe_smartfilter_ram_vm as ram
import probe_smartfilter_vm as boundary

OUT = ram.BUILD / 'probe-vm'
SOCKET = '/tmp/nx300m-smartfilter-probe-private'
MARKER = 'SMARTFILTER_PROBE_PRIVATE_VM_READY'
RETURNED_CASES = {
    'success': (1, None, None),
    'busy': (2, 'try2', None),
    'render-error': (4, 'ycc', -1),
    'reset-error': (5, 'reset', -77),
    'close-error': (5, 'close', -78),
    'post-error': (6, 'unlock', -79),
}
BLOCKED_CASES = ('trace-full', 'scope-full', 'trace-frozen', 'trace-off')
PENDING_CASES = {
    'pending-ycc': (27, 'ycc', 17),
    'pending-reset': (44, 'reset', 19),
    'pending-iq-process': (45, 'iq_process', 21),
    'pending-unlock': (46, 'unlock', 23),
}
TOKEN_RE = re.compile(r'[1-9][0-9]*:[0-9]+:[1-9][0-9]*:[1-9][0-9]*:[1-9][0-9]*\Z')

# Keep fixture start/request/operation and shutdown identical to the RAM suite.
_PREFIX = ram.PROBE[:ram.PROBE.index('request original-mode 2')]
_PREFIX = _PREFIX.replace('--fixture > "$label.json"', '--fixture --s2 > "$label.json"')
_PREFIX = _PREFIX.replace('case $cmd in 1|5|7|9)', 'case $cmd in 1|5|7|9|10|33)')
PROBE = _PREFIX + r'''
umask 077
mkdir -p /perf
source_id=$(stat -c '%d:%i' /perf)
case_id=0
reject_operation() {
    label=$1; expected_exit=$2; shift 2
    set +e
    "$LOADER" "$@" --pid "$pid" --blob "$BLOB" --fixture --s2 > "$label.json" 2> "$label.stderr"
    actual_exit=$?
    set -e
    printf '%s\n' "$actual_exit" > "$label.exit"
    [ "$actual_exit" = "$expected_exit" ]
}
get_token() {
    token=$(sed -n 's/.*"token":"\([^"]*\)".*/\1/p' "$1.json")
    [ -n "$token" ]
}
start_case() {
    case_name=$1
    request "$case_name-clear" 40
    case_id=$((case_id+1))
    probe_file=$(printf '/perf/sf-probe-%032x.ndjson' "$case_id")
    [ ! -e "$probe_file" ] && [ ! -L "$probe_file" ]
    : > "$probe_file"
    operation "$case_name-arm" apply --probe-once
    get_token "$case_name-arm"
}
export_case() {
    operation "$case_name-export" diag-export --expect "$token"
    dump=$(sed -n 's/.*"diag_file":"\([^"]*\)".*/\1/p' "$case_name-export.json")
    [ -s "$dump" ]
    cp "$dump" "$case_name.raw.ndjson"
}
save_case() {
    operation "$case_name-save" probe-save --expect "$token" --probe-file "$probe_file" \
        --source-id "$source_id" --ack-idle-no-capture
    cmp "$dump" "$probe_file"
    cp "$probe_file" "$case_name.saved.ndjson"
}

# No resident/window must remain no_data, not a successful empty snapshot.
request original-mode 2
operation baseline status
reject_operation no-data 3 diag-export
reject_operation no-data-expect 3 diag-export --expect "$pid:1:1:1:1"
# Initialise S2 topology once in stock mode; every test arm below is explicit.
operation stock-phase stock --timing 1
request stock-paths 13
operation stock-status status

start_case success
request success-first 10
reject_operation not-frozen 3 probe-save --expect "$token" --probe-file "$probe_file" \
    --source-id "$source_id" --ack-idle-no-capture

# A mismatched token must be rejected before epoch/frozen/pause changes. PID
# mismatch is rejected by CLI (64), other fields by stopped identity check (3).
old_ifs=$IFS; IFS=:; set -- $token; IFS=$old_ifs
p_pid=$1; p_start=$2; p_build=$3; p_window=$4; p_generation=$5
request token-pause-set 35
request token-epoch-before 34
reject_operation drift-pid 64 diag-export --expect "$((p_pid+1)):$p_start:$p_build:$p_window:$p_generation"
reject_operation drift-start 3 diag-export --expect "$p_pid:$((p_start+1)):$p_build:$p_window:$p_generation"
reject_operation drift-build 3 diag-export --expect "$p_pid:$p_start:$((p_build+1)):$p_window:$p_generation"
reject_operation drift-window 3 diag-export --expect "$p_pid:$p_start:$p_build:$((p_window+1)):$p_generation"
wrong_generation="$p_pid:$p_start:$p_build:$p_window:$((p_generation+1))"
reject_operation drift-generation 3 diag-export --expect "$wrong_generation"
reject_operation drift-save 3 probe-save --expect "$wrong_generation" --probe-file "$probe_file" \
    --source-id "$source_id" --ack-idle-no-capture
request token-epoch-after 34
request token-pause-observed 36
[ ! -s "$probe_file" ]
request success-later 10
export_case

# Exercise the ordinary export path as well as probe-save. Refuse a symlink
# without appending anywhere, and retain the original valid frozen snapshot.
: > /perf/io-error-sentinel
ln -s /perf/io-error-sentinel /perf/fn-key.log
reject_operation ordinary-destination-symlink 3 diag-export --expect "$token" --diag-log /perf/fn-key.log
[ ! -s /perf/io-error-sentinel ]
[ -L /perf/fn-key.log ]
rm /perf/fn-key.log
cmp "$dump" "$case_name.raw.ndjson"

# Restrict persistence to an acknowledged contract, fixed source identity,
# permitted name and pre-created empty ordinary file; never silently re-encode.
reject_operation missing-ack 64 probe-save --expect "$token" --probe-file "$probe_file" --source-id "$source_id"
reject_operation bad-source 3 probe-save --expect "$token" --probe-file "$probe_file" \
    --source-id 0:0 --ack-idle-no-capture
reject_operation bad-path 3 probe-save --expect "$token" --probe-file /perf/not-a-probe.ndjson \
    --source-id "$source_id" --ack-idle-no-capture
nonempty=/perf/sf-probe-ffffffffffffffffffffffffffffff01.ndjson
symlink=/perf/sf-probe-ffffffffffffffffffffffffffffff02.ndjson
missing=/perf/sf-probe-ffffffffffffffffffffffffffffff03.ndjson
printf 'occupied\n' > "$nonempty"
ln -s "$probe_file" "$symlink"
reject_operation nonempty-destination 3 probe-save --expect "$token" --probe-file "$nonempty" \
    --source-id "$source_id" --ack-idle-no-capture
reject_operation symlink-destination 3 probe-save --expect "$token" --probe-file "$symlink" \
    --source-id "$source_id" --ack-idle-no-capture
reject_operation missing-destination 3 probe-save --expect "$token" --probe-file "$missing" \
    --source-id "$source_id" --ack-idle-no-capture
[ ! -e "$missing" ] && [ ! -s "$probe_file" ]
cp "$nonempty" nonempty-destination.txt
save_case
operation success-save-repeat probe-save --expect "$token" --probe-file "$probe_file" \
    --source-id "$source_id" --ack-idle-no-capture
cmp "$dump" "$probe_file"
# Corrupt only this VM's diagnostic copy; no valid file may be recopied from it.
cp "$dump" /tmp/probe-valid-snapshot.ndjson
printf 'corrupt\n' >> "$dump"
reject_operation corrupt-snapshot 3 probe-save --expect "$token" --probe-file "$probe_file" \
    --source-id "$source_id" --ack-idle-no-capture
cp /tmp/probe-valid-snapshot.ndjson "$dump"
cmp "$dump" "$probe_file"
# Balanced capture changes arm baselines even when captures has returned to 0.
request capture-prepare 3
request capture-finish 4
reject_operation capture-baseline 3 probe-save --expect "$token" --probe-file "$probe_file" \
    --source-id "$source_id" --ack-idle-no-capture
cmp "$dump" "$probe_file"

for name in busy render-error reset-error close-error post-error; do
    start_case "$name"
    frame_command=10
    case $name in
        busy) frame_command=7 ;;
        render-error) frame_command=5 ;;
        reset-error) request "$name-inject" 41 ;;
        close-error) request "$name-inject" 42 ;;
        post-error) request "$name-inject" 43 ;;
    esac
    request "$name-first" "$frame_command"
    request "$name-later" 10
    export_case
    save_case
 done

for name in trace-full scope-full trace-frozen trace-off; do
    start_case "$name"
    case $name in
        trace-full) request "$name-inject" 30; request "$name-first" 10 ;;
        scope-full) request "$name-inject" 31; request "$name-first" 10 ;;
        trace-frozen) request "$name-inject" 32; request "$name-first" 10 ;;
        trace-off) request "$name-first" 33 ;;
    esac
    request "$name-later" 10
    export_case
    save_case
 done

for name in pending-ycc pending-reset pending-iq-process pending-unlock; do
    start_case "$name"
    case $name in
        pending-ycc) pending_command=27 ;;
        pending-reset) pending_command=44 ;;
        pending-iq-process) pending_command=45 ;;
        pending-unlock) pending_command=46 ;;
    esac
    request "$name-start" "$pending_command"
    operation "$name-status" status
    export_case
    save_case
    # Freeze/export/save do not wait for a hardware callback or manufacture END.
    request "$name-release" 28
    operation "$name-returned" status
    request "$name-later" 10
    operation "$name-export-repeat" diag-export --expect "$token"
    cmp "$dump" "$probe_file"
 done
request final-clear 40
operation final-restore restore
operation final status
''' + ram.PROBE[ram.PROBE.index('cat /proc/$pid/maps > after.maps'):]


def call(action, command=''):
    result = subprocess.run(
        ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
         arm_vm.wsl_path(__file__), '--linux', action,
         base64.b64encode(command.encode()).decode()],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    ram.OUT = OUT
    ram.prepare()
    candidate = json.loads((OUT / 'candidate.json').read_text())
    candidate.update(expected_abi=4, actual_plt_hooks=7, profile='S2', probe_once=True,
                     probe_windows=len(RETURNED_CASES) + len(BLOCKED_CASES) + len(PENDING_CASES),
                     path_probe_scope='stock prefixes; not complete hardware topology construction',
                     raw_sensor_or_full_path_construction=False)
    (OUT / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n')


def _read_dump(path):
    """Require strict parser acceptance and independently bind original payload bytes."""
    from analyze_smartfilter_timing import parse_log

    raw = path.read_bytes()
    assert raw and len(raw) <= 1048576 and raw.endswith(b'\n'), path.name
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[0]['type'] == 'sf_diag_begin' and rows[-1]['type'] == 'sf_diag_end', path.name
    assert rows[0]['schema'] == rows[-1]['schema'] == 2, path.name
    assert rows[0]['dump_id'] == rows[-1]['dump_id'], path.name
    payload = raw.splitlines(keepends=True)[1:-1]
    assert len(payload) == rows[-1]['records'], path.name
    assert sum(map(len, payload)) == rows[-1]['bytes'], path.name
    assert hashlib.sha256(b''.join(payload)).hexdigest() == rows[-1]['sha256'], path.name
    assert [r['seq'] for r in rows[1:-1]] == list(range(len(payload))), path.name
    assert all(r['dump_id'] == rows[0]['dump_id'] for r in rows), path.name
    parsed = parse_log(path)
    assert parsed.ok and len(parsed.dumps) == 1, (
        path.name, parsed.errors, [d.errors for d in parsed.dumps.values()])
    return rows[0], [r for r in rows if r['type'] == 'sf_diag_record'], parsed


def verify(guest):
    from analyze_smartfilter_timing import analyze
    from mfw_runtime import firmware_profile

    def record(name):
        return json.loads((guest / (name + '.json')).read_text())

    def response(name):
        values = [int(v) for v in (guest / (name + '.response')).read_text().split()]
        assert len(values) == 3, name
        return values

    def rejected(name, status=None, code=3):
        assert int((guest / (name + '.exit')).read_text()) == code, name
        if status is not None:
            result = record(name)
            assert result['status'] == status and result['persisted'] is False, (name, result)
            return result

    original = (OUT / 'source-files/input.yuv').read_bytes()
    assert original and (guest / 'mfw-library.sha256').read_text().split()[0] == firmware_profile('mfw')['libmisc_sha256']
    assert response('original-mode')[0] == 2
    assert record('baseline')['installed'] is False
    stock = record('stock-status')
    assert stock['abi'] == 4 and stock['profile'] == 1 and stock['installed']
    assert all(stock['mode_forged'][i] > 0 for i in (0, 2, 3)), stock
    for name in ('no-data', 'no-data-expect'):
        result = rejected(name, 'no_data')
        assert result['snapshot_created'] is False
        assert 'Control transaction cancelled' not in (guest / (name + '.stderr')).read_text()
    rejected('not-frozen', 'probe_contract_missing')
    rejected('missing-ack', code=64)
    for name in ('bad-source', 'bad-path'):
        rejected(name, 'media_identity_changed')
    def io_failure(name, status, expected_errno):
        result = rejected(name, status)
        for key in ('error_stage', 'error_operation', 'error_reason'):
            assert isinstance(result[key], str) and re.fullmatch(r'[a-z0-9_]{1,64}', result[key]), (name, key, result)
        assert type(result['error_errno']) is int and result['error_errno'] == expected_errno, (name, result)
        assert result['error_errno_valid'] is (expected_errno != 0), (name, result)
        assert isinstance(result['error_errno_text'], str) and result['error_errno_text'], (name, result)
        assert result['snapshot_created'] is True and result['persisted'] is False, (name, result)
        assert result['write_attempted'] is False and result['destination_may_have_changed'] is False, (name, result)
        assert result['destination_bytes_verified'] is False, (name, result)
        error_text = (guest / (name + '.stderr')).read_text(encoding='utf-8')
        assert result['error_stage'] in error_text and result['error_operation'] in error_text, (name, error_text)
        return result

    io_failure('ordinary-destination-symlink', 'export_failed', 40)
    io_failure('nonempty-destination', 'persistence_failed', 0)
    io_failure('symlink-destination', 'persistence_failed', 40)
    io_failure('missing-destination', 'persistence_failed', 2)
    assert (guest / 'nonempty-destination.txt').read_bytes() == b'occupied\n'
    rejected('corrupt-snapshot', 'no_valid_snapshot')
    rejected('capture-baseline', 'capture_observed')
    assert response('capture-prepare')[0] == 0
    assert response('capture-finish')[0] == 1
    assert record('success-save-repeat')['persisted'] is True

    rejected('drift-pid', code=64)
    for field in ('start', 'build', 'window', 'generation', 'save'):
        result = rejected('drift-' + field, 'identity_mismatch')
        assert result['snapshot_created'] is False
        assert 'Control transaction cancelled' not in (guest / ('drift-' + field + '.stderr')).read_text()
    assert response('token-epoch-before')[0] == response('token-epoch-after')[0]
    assert response('token-pause-observed')[0] == 1

    checks = {
        'abi4_s2_stock_path_prefixes': True,
        'no_data_without_snapshot_or_resume': True,
        'five_part_token_drift_preserves_epoch_and_pause': True,
        'save_requires_freeze_ack_source_and_restricted_destination': True,
        'save_rejects_nonempty_symlink_and_missing_destination': True,
        'ordinary_and_probe_persistence_errors_have_exact_details': True,
        'frozen_checksum_corruption_refused': True,
        'balanced_capture_baseline_change_refuses_save': True,
        'save_exact_bytes_readback_and_idempotence': True,
    }
    analyses = {}
    tokens = set()
    all_cases = (*RETURNED_CASES, *BLOCKED_CASES, *PENDING_CASES)
    for name in all_cases:
        armed = record(name + '-arm')
        token = armed['token']
        assert TOKEN_RE.fullmatch(token), (name, token)
        assert token not in tokens, name
        tokens.add(token)
        parts = [int(part) for part in token.split(':')]
        assert parts[:3] == [armed['pid'], armed['start'], armed['build']], name
        assert parts[4] == armed['requested_generation'], name
        assert armed['abi'] == 4 and armed['profile'] == armed['probe_once'] == 1, name
        assert armed['strength'] == 100 and armed['probe_state'] == 1 and armed['probe_outcome'] == 0, name
        meta, rows, parsed = _read_dump(guest / (name + '.raw.ndjson'))
        assert meta['measurement_source'] == 'vm' and meta['frozen'] is True, name
        assert [meta[k] for k in ('pid', 'start', 'build', 'window', 'generation')] == parts, name
        assert meta['strength'] == 100 and meta['clock_errors'] == 0, name
        assert (guest / (name + '.saved.ndjson')).read_bytes() == (guest / (name + '.raw.ndjson')).read_bytes(), name
        exported, saved = record(name + '-export'), record(name + '-save')
        assert exported['token'] == saved['token'] == token, name
        assert exported['snapshot_created'] is True and exported['persisted'] is False, name
        assert saved['persisted'] is True and saved['snapshot_created'] is True, name
        assert saved['probe_contract'] == 'idle-no-capture' and saved['observed_capture_unchanged'] is True, name
        assert re.fullmatch(r'/perf/sf-probe-[0-9a-f]{32}\.ndjson', saved['probe_file']), name
        probe = meta['probe']
        assert probe['mode'] == 1 and probe['prepare_baseline'] == probe['finish_baseline'], name
        attempts = [row for row in rows if row['flags'] & 256]
        frames = [row for row in rows if row['kind'] == 'frame']
        frame_slots = [row for row in rows if row['kind'] in ('frame', 'initialize')]
        assert len(attempts) <= 1 and all(r['kind'] == 'frame' for r in attempts), name
        later = guest / (name + '-later.yuv')
        assert response(name + '-later')[1:] == [0, 1], name
        assert later.read_bytes() == original, name
        if name in RETURNED_CASES:
            outcome, stage, error = RETURNED_CASES[name]
            assert probe['state'] == 3 and probe['claimed'] is True and probe['returned'] is True, name
            assert probe['outcome'] == outcome and len(attempts) == 1, (name, probe)
            attempt = attempts[0]
            assert attempt['state'] == 'committed' and attempt['rc'] == 0, name
            assert probe['slot'] < len(frame_slots) <= 128 and frame_slots[probe['slot']] == attempt, name
            assert any(r['reason'] == 11 and r['path'] == 'passthrough' for r in frames if r is not attempt), name
            assert response(name + '-first')[1:] == ([0, 1] if name == 'busy' else [1, 1]), name
            first = (guest / (name + '-first.yuv')).read_bytes()
            if name == 'success':
                assert len(first) == len(original) and max(abs(a - b) for a, b in zip(first, original)) <= 1
                assert attempt['path'] == 'render'
                required = {'try0', 'try1', 'try2', 'try3', 'open', 'ade_init', 'lut0', 'lut1', 'lut2',
                            'flush_in', 'ycc', 'flush_out', 'reset', 'iq_init', 'iq_process', 'close', 'unlock'}
                assert required <= attempt['stages'].keys(), name
            else:
                assert first == original, name
                assert stage in attempt['stages'] and attempt['stages'][stage]['rc'] != 0, name
                if error is not None:
                    assert attempt['stages'][stage]['rc'] == error, name
            assert saved['backend_pending'] is False, name
            checks[name + '_one_attempt_then_passthrough'] = True
        elif name in BLOCKED_CASES:
            assert probe['state'] == 4 and probe['outcome'] == 7 and probe['slot'] == 0xffffffff, (name, probe)
            assert probe['claimed'] is False and probe['returned'] is False and not attempts, name
            assert response(name + '-first')[1:] == [0, 1], name
            assert (guest / (name + '-first.yuv')).read_bytes() == original, name
            assert saved['backend_pending'] is False, name
            if name == 'trace-off':
                # The temporary cfg timing toggle is restored before this frame.
                assert any(r['reason'] == 12 and r['path'] == 'passthrough' for r in frames), name
            checks[name + '_blocked_without_hardware_or_retry'] = True
        else:
            _, stage, stage_id = PENDING_CASES[name]
            assert probe['state'] == 2 and probe['outcome'] == 0, (name, probe)
            assert probe['claimed'] is True and probe['returned'] is False and len(attempts) == 1, name
            attempt = attempts[0]
            assert attempt['state'] == 'pending' and attempt['end'] is None, name
            assert attempt['current_stage'] == stage_id and attempt['current_at'] is not None, name
            assert stage not in attempt['stages'], name
            assert meta['backend_active'] == 1 and meta['inflight'] > 0, name
            assert exported['safe_to_persist'] is False and saved['backend_pending'] is True, name
            before, after = record(name + '-status'), record(name + '-returned')
            assert before['probe_state'] == 2 and before['backend_active'] == 1, name
            assert after['probe_state'] == 3 and after['probe_outcome'] == 1 and after['backend_active'] == 0, name
            assert response(name + '-release')[1:] == [1, 1], name
            repeat = record(name + '-export-repeat')
            assert repeat['reused'] is True and repeat['dump_id'] == exported['dump_id'], name
            checks[name + '_pending_frozen_save_release_and_reuse'] = True
        analyses[name] = analyze(parsed)
        assert len(analyses[name]['probe_attempts']) == 1, name
        assert analyses[name]['probe_attempts'][0]['probe'] == probe, name
    final = record('final')
    assert final['installed'] and not final['enabled'] and final['routing_retained']
    assert final['inflight'] == final['backend_active'] == final['captures'] == 0
    checks['final_soft_stop_preserves_routing_without_inflight'] = True
    (OUT / 'probe-timing-analysis.json').write_text(json.dumps(analyses, indent=2) + '\n')
    return dict(passed=True, abi=4, profile='S2', actual_ram_loader=True, actual_plt_hooks=7,
                probe_windows=len(all_cases), checks=checks,
                physical_camera_or_card_accessed=False, hardware_isp_pixels_verified=False,
                raw_sensor_or_full_path_construction=False, cache_coherency_verified=False)


if __name__ == '__main__':
    boundary.OUT = OUT
    boundary.SOCKET = SOCKET
    boundary.MARKER = MARKER
    boundary.PROBE = PROBE
    boundary.prepare = prepare
    boundary.verify = verify
    boundary.call = call
    boundary.main()
