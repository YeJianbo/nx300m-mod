"""Real ABI4 collector/launcher/loader integration in an isolated ARM fixture.

No physical camera/card. This runner is invoked by the coordinator in sequence;
importing it does not build or boot anything. Pixel/cache/hardware boundaries
remain the named RAM fixture doubles, not measurements of real camera hardware.
"""
import base64
import hashlib
import json
import re
import subprocess

import arm_vm
import probe_smartfilter_ram_vm as ram
import probe_smartfilter_vm as boundary

OUT = ram.BUILD / 'collector-vm'
PREFIX = ram.PROBE[:ram.PROBE.index('request original-mode 2')]
PREFIX = PREFIX.replace('case $cmd in 1|5|7|9)', 'case $cmd in 1|5|7|9|10)')
PREFIX = PREFIX.replace('    cp /tmp/smartfilter-ram.stdout',
                        '    gather_collector || :\n    cp /tmp/smartfilter-ram.stdout')
PROBE = PREFIX + r'''
# Only this private guest creates /perf; no host/card source is mounted here.
RESULTS=/arm-vm/mfw-results
cp /arm-vm/mfw-probe.sh "$RESULTS/executed-probe.sh"
RUNTIME=/tmp/nx300m-collector-runtime
STATE=/tmp/nx300m-collector-state
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$STATE" /perf/logs jobs probes frozen
chmod 700 "$RUNTIME" "$STATE" /perf /perf/logs
source_id=$(stat -c '%d:%i' /perf)
export NX300M_MOD_ROOT="$RUNTIME" NX_STATE_DIR="$STATE"
export NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID="$source_id"
export NX_SF_PROBE_FIXTURE=1 NX_SF_PROBE_DELAY=10 NX_SF_PROBE_TIMEOUT=30
printf '%s\n' "$source_id" > source.id
printf '%s\n' "$pid" > actual-camera.pid
cat /proc/$pid/stat > actual-camera.stat
for pair in smartfilter-preview.sh:smartfilter-preview.sh smartfilter-probe-collector.sh:smartfilter-probe-collector.sh bin/smartfilter_probe_launcher.arm:bin/smartfilter_probe_launcher.arm bin/smartfilter_loader.arm:bin/smartfilter_loader.arm smartfilter_ram.bin:lib/smartfilter_ram.bin; do
    cp "/mnt/mmc/${pair%:*}" "$RUNTIME/${pair#*:}"
    cmp -s "/mnt/mmc/${pair%:*}" "$RUNTIME/${pair#*:}"
done
chmod 500 "$RUNTIME/bin/"*.arm "$RUNTIME/"*.sh
cat > /tmp/collector-ui.sh <<'UI'
#!/bin/sh
set -eu
label=$1; mode=$2
cd /arm-vm/mfw-results
printf '%s\n' "$$" > "$label.ui.pid"
cat /proc/$$/stat > "$label.ui.stat"
rc=0
/bin/sh "$NX300M_MOD_ROOT/smartfilter-preview.sh" test-identity > "$label.ui.stdout" 2> "$label.ui.stderr" || rc=$?
printf '%s\n' "$rc" > "$label.ui.done"
[ "$rc" = 0 ] || exit "$rc"
if [ "$mode" = hold ]; then
    while :; do sleep 1; done
fi
UI
wait_file() {
    file=$1; count=0
    while [ ! -s "$file" ]; do
        count=$((count+1)); [ "$count" -lt 600 ] || { printf 'missing %s\n' "$file" >&2; return 1; }
        /rescue/busybox usleep 100000
    done
}
start_ui() {
    scenario=$1; ui_mode=$2
    /rescue/busybox setsid /bin/sh /tmp/collector-ui.sh "$scenario" "$ui_mode" > "$scenario.ui-entry.stdout" 2> "$scenario.ui-entry.stderr" &
    ui_wait=$!
    wait_file "$scenario.ui.done"
    [ "$(cat "$scenario.ui.done")" = 0 ]
    job=$(awk '/^accepted / { for(i=1;i<=NF;i++) if($i ~ /^ram=/) { sub(/^ram=/,"",$i); print $i } }' "$scenario.ui.stdout")
    case $job in /tmp/nx300m-sf-job.*) ;; *) return 1 ;; esac
    [ -d "$job" ] && [ ! -L "$job" ]
    printf '%s\n' "$job" > "$scenario.job-path"
    # Token is a collector-owned RAM file written after actual apply completes.
    # Do not poll loader status: that would change control epochs.
    wait_file "$job/token"
    [ -s "$job/apply.json" ]
    read ready_nonce job_pid job_start < "$job/ready"
    cat /proc/$job_pid/stat > "$scenario.job.stat"
    readlink /proc/$job_pid/cwd > "$scenario.job.cwd"
    ls -l /proc/$job_pid/fd > "$scenario.job.fd"
    if [ "$ui_mode" = exit ]; then wait "$ui_wait"; fi
}
wait_job() {
    wait_file "$job/result"
    cat "$job/result" > "$scenario.result"
    cp -a "$job" "jobs/$scenario"
}
phase_of() { awk -F= '$1=="phase" { print $2 }' "$1"; }
target_of() { awk -F= '$1=="probe_file" { print $2 }' "$1"; }
save_probe_evidence() {
    target=$(target_of "$scenario.result")
    case $target in /perf/sf-probe-*.ndjson) ;; *) return 1 ;; esac
    [ -f "$target" ] && [ ! -L "$target" ]
    cp "$target" "$scenario.probe.ndjson"
    stat -c '%d:%i:%s' "$target" > "$scenario.target.stat"
}
gather_collector() {
    # Also retain partial evidence if the guest script fails before wait_job.
    for name in normal pending capture ioerror; do
        if [ -s "$RESULTS/$name.job-path" ]; then
            path=$(cat "$RESULTS/$name.job-path")
            case $path in /tmp/nx300m-sf-job.*)
                if [ -d "$path" ] && [ ! -d "$RESULTS/jobs/$name" ]; then cp -a "$path" "$RESULTS/jobs/$name"; fi ;;
            esac
        fi
    done
    for path in /var/run/nx300m-sf-diag-*.ndjson; do
        [ ! -f "$path" ] || cp "$path" "$RESULTS/frozen/"
    done
    for path in /perf/sf-probe-*.ndjson; do
        [ ! -f "$path" ] || cp "$path" "$RESULTS/probes/"
    done
}

# Establish the real S2 resident and original Create/B2Y/LDC path prefixes.
operation collector-stock stock --s2 --timing 1
request collector-paths 13

# 1. UI returns/exits, one attempted frame, then budget-exhausted passthrough.
start_ui normal exit
request normal-attempt 10
request normal-future 10
request normal-epoch 34
wait_job
[ "$(phase_of normal.result)" = persisted ]
save_probe_evidence

# 2. Block a real fixture YCC call, terminate the UI session/group, and let the
# independent collector freeze and save that still-pending frame exactly once.
operation pending-stock stock --s2 --timing 1
request pending-paths 13
start_ui pending hold
request pending-enter 27
request pending-epoch 34
[ ! -e "$job/diag.json" ] && [ ! -e "$job/save.json" ]
printf 'terminated_before_snapshot\n' > pending.ui-termination-order
ui_pid=$(cat pending.ui.pid)
# setsid makes this the UI process-group id; preserve the pre-kill stat evidence.
read ui_stat < /proc/$ui_pid/stat
ui_fields=${ui_stat##*) }
set -- $ui_fields
[ "$3" = "$ui_pid" ] && [ "$4" = "$ui_pid" ]
# Stock BusyBox ash's kill builtin treats '--' as an invalid PID. The signal
# has already been selected explicitly, so the negative operand is the PGID.
kill -TERM "-$ui_pid"
set +e
wait "$ui_wait"
ui_exit=$?
set -e
printf '%s\n' "$ui_exit" > pending.ui-terminated.exit
[ "$ui_exit" -ne 0 ]
wait_job
[ "$(phase_of pending.result)" = persisted ]
save_probe_evidence
# Release only after evidence has actually been fsynced/read back by probe-save.
request pending-release 28
request pending-future 10
operation pending-after-release status

# 3. A capture lease changes both arm baselines after the one frame. The real
# probe-save must refuse persistence; a frozen RAM snapshot and empty target stay.
operation capture-stock stock --s2 --timing 1
request capture-paths 13
start_ui capture exit
request capture-attempt 10
request capture-prepare 3
request capture-finish 4
wait_job
[ "$(phase_of capture.result)" = cancelled ]
grep -q '^reason=capture_observed$' capture.result
save_probe_evidence
[ ! -s capture.probe.ndjson ]
request capture-future 10

# 4. Change only the precreated target's owner inside this RAM guest. The
# collector's inode/empty-file check still passes; the real loader owner gate
# must reject it and its precise file error must reach the detached RAM result.
start_ui ioerror exit
read io_target io_identity < "$job/prepare.json"
case $io_target in /perf/sf-probe-*.ndjson) ;; *) exit 1 ;; esac
chown 1:1 "$io_target"
request ioerror-attempt 10
request ioerror-future 10
wait_job
[ "$(phase_of ioerror.result)" = failed ]
grep -q '^reason=save_failed_3$' ioerror.result
grep -q '^error_errno=0$' ioerror.result
grep -q '^error_errno_valid=false$' ioerror.result
save_probe_evidence
[ ! -s ioerror.probe.ndjson ]
operation collector-final status

gather_collector
''' + ram.PROBE[ram.PROBE.index('cat /proc/$pid/maps > after.maps'):]


def call(action, command=''):
    result = subprocess.run(
        ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
         arm_vm.wsl_path(__file__), '--linux', action,
         base64.b64encode(command.encode()).decode()],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=375)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    ram.OUT = OUT
    ram.prepare()
    stage = OUT / 'source-files'
    resources = {
        'bin/smartfilter_probe_launcher.arm': ram.BUILD / 'smartfilter_probe_launcher.arm',
        'smartfilter-probe-collector.sh': ram.ROOT / 'mod/deployment/mfw/smartfilter-probe-collector.sh',
        'smartfilter-preview.sh': ram.ROOT / 'mod/deployment/mfw/smartfilter-preview.sh',
    }
    # Preserve ORIGINAL source bytes for the packager's collector byte gate.
    for relative, source in resources.items():
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    candidate = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))
    candidate.update(
        actual_ram_loader=True, actual_plt_hooks=7, profile='S2',
        collector_integration='actual wrapper + native launcher + collector + loader --fixture',
        collector_source_sha256={key: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for key, path in resources.items()},
        inventory={p.relative_to(stage).as_posix(): p.stat().st_size
                   for p in stage.rglob('*') if p.is_file()})
    arm_vm.write_json(OUT / 'candidate.json', candidate)
    # ram.prepare's first initramfs precedes the newly added collector resources.
    boundary.vm.initramfs()


def verify(guest):
    checks = {}

    def check(name, value):
        assert value, name
        checks[name] = True

    def receipt(scenario, filename):
        return json.loads((guest / 'jobs' / scenario / filename).read_text(encoding='utf-8'))

    def fields(path):
        return dict(line.split('=', 1) for line in path.read_text(encoding='utf-8').splitlines())

    def response(name):
        return [int(x) for x in (guest / (name + '.response')).read_text(encoding='utf-8').split()]

    def stat_record(path):
        text = path.read_text(encoding='utf-8')
        pid = int(text.split(' ', 1)[0])
        values = text.rsplit(') ', 1)[1].split()
        return dict(pid=pid, pgrp=int(values[2]), session=int(values[3]), start=int(values[19]))

    def frozen(scenario, persisted):
        from analyze_smartfilter_timing import parse_log
        diag = receipt(scenario, 'diag.json')
        path = guest / 'frozen' / diag['diag_file'].rsplit('/', 1)[1]
        parsed = parse_log(path)
        check(scenario + '_strict_schema2_parser', parsed.ok and len(parsed.dumps) == 1)
        raw = path.read_bytes()
        lines = raw.splitlines(keepends=True)
        objects = [json.loads(line) for line in lines]
        begin, end = objects[0], objects[-1]
        payload = b''.join(lines[1:-1])
        check(scenario + '_frozen_schema2_checksum',
              begin['type'] == 'sf_diag_begin' and begin['schema'] == 2
              and end['type'] == 'sf_diag_end' and end['schema'] == 2
              and end['records'] == len(objects) - 2 and end['bytes'] == len(payload)
              and end['sha256'] == hashlib.sha256(payload).hexdigest())
        check(scenario + '_ram_snapshot_not_generic_persistence',
              diag['snapshot_created'] is True and diag['persisted'] is False
              and diag['status'] == 'ram_only' and diag['reused'] is False)
        check(scenario + '_snapshot_identity',
              [begin[k] for k in ('pid', 'start', 'build', 'window', 'generation')]
              == [int(part) for part in receipt(scenario, 'apply.json')['token'].split(':')])
        if persisted:
            saved = (guest / (scenario + '.probe.ndjson')).read_bytes()
            check(scenario + '_persisted_original_frozen_bytes', saved == raw and len(saved) > 0)
        records = [obj for obj in objects if obj.get('type') == 'sf_diag_record']
        attempts = [obj for obj in records if obj['kind'] == 'frame' and obj['flags'] & 256]
        check(scenario + '_one_attempt_record', len(attempts) == 1)
        return begin, attempts[0], records

    camera = stat_record(guest / 'actual-camera.stat')
    build = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))['ram_build']['build']
    source_id = (guest / 'source.id').read_text(encoding='utf-8').strip()
    check('source_id_recorded', re.fullmatch(r'\d+:\d+', source_id) is not None)
    original = (OUT / 'source-files/input.yuv').read_bytes()
    for scenario in ('normal', 'pending', 'capture', 'ioerror'):
        result = fields(guest / (scenario + '.result'))
        apply = receipt(scenario, 'apply.json')
        token = apply['token']
        check(scenario + '_actual_camera_token',
              re.fullmatch(r'\d+:\d+:\d+:\d+:\d+', token) is not None
              and [int(x) for x in token.split(':')[:3]] == [camera['pid'], camera['start'], build]
              and result['token'] == token)
        ready = (guest / 'jobs' / scenario / 'ready').read_text(encoding='utf-8').split()
        ack = (guest / 'jobs' / scenario / 'ack').read_text(encoding='utf-8').split()
        job_stat = stat_record(guest / (scenario + '.job.stat'))
        ui_stat = stat_record(guest / (scenario + '.ui.stat'))
        check(scenario + '_ready_ack_and_detached_identity',
              ready == ack and len(ready) == 3 and re.fullmatch(r'[0-9a-f]{32}', ready[0]) is not None
              and [int(x) for x in ready[1:]] == [job_stat['pid'], job_stat['start']]
              and job_stat['session'] != ui_stat['session'] and job_stat['pgrp'] != ui_stat['pgrp'])
        job_path = (guest / (scenario + '.job-path')).read_text(encoding='utf-8').strip()
        check(scenario + '_private_stable_ram_resources',
              re.fullmatch(r'/tmp/nx300m-sf-job\.[A-Za-z0-9]+', job_path) is not None
              and (guest / (scenario + '.job.cwd')).read_text(encoding='utf-8').strip() == job_path
              and (guest / 'jobs' / scenario / 'loader.arm').read_bytes()
              == (OUT / 'source-files/bin/smartfilter_loader.arm').read_bytes()
              and (guest / 'jobs' / scenario / 'blob.bin').read_bytes()
              == (OUT / 'source-files/smartfilter_ram.bin').read_bytes()
              and (guest / 'jobs' / scenario / 'launcher').read_bytes()
              == (OUT / 'source-files/bin/smartfilter_probe_launcher.arm').read_bytes()
              and (guest / 'jobs' / scenario / 'collector.sh').read_bytes()
              == (OUT / 'source-files/smartfilter-probe-collector.sh').read_bytes())
        check(scenario + '_no_inherited_card_descriptors',
              '/mnt/mmc/' not in (guest / (scenario + '.job.fd')).read_text(encoding='utf-8')
              and '/perf/' not in (guest / (scenario + '.job.fd')).read_text(encoding='utf-8'))
        check(scenario + '_ui_only_acknowledges_acceptance',
              (guest / (scenario + '.ui.done')).read_text(encoding='utf-8').strip() == '0'
              and 'accepted nonce=' in (guest / (scenario + '.ui.stdout')).read_text(encoding='utf-8'))
        if scenario == 'ioerror':
            save = receipt(scenario, 'save.json')
            check('ioerror_real_loader_rejection_not_persistence',
                  result['phase'] == 'failed' and result['reason'] == 'save_failed_3'
                  and save['status'] == 'persistence_failed' and save['persisted'] is False
                  and save['snapshot_created'] is True and (guest / 'ioerror.probe.ndjson').stat().st_size == 0)
            check('ioerror_exact_fields_reach_detached_result',
                  all(result[key] == str(save[key]).lower() for key in
                      ('error_stage', 'error_operation', 'error_reason', 'error_errno', 'error_errno_valid',
                       'snapshot_created', 'persisted', 'write_attempted', 'destination_may_have_changed',
                       'destination_bytes_verified'))
                  and save['error_errno'] == 0 and save['error_errno_valid'] is False
                  and 'owner' in save['error_reason'] and save['write_attempted'] is False
                  and save['destination_may_have_changed'] is False
                  and save['destination_bytes_verified'] is False)
            check('ioerror_original_receipt_and_stderr_retained',
                  result['error_receipt'] == job_path + '/save.json'
                  and result['error_stderr'] == job_path + '/save.stderr'
                  and save['error_stage'] in (guest / 'jobs/ioerror/save.stderr').read_text(encoding='utf-8'))
        elif scenario != 'capture':
            save = receipt(scenario, 'save.json')
            check(scenario + '_real_probe_save_receipt',
                  result['phase'] == 'persisted' and save['command'] == 'probe-save'
                  and save['status'] == 'persisted' and save['persisted'] is True
                  and save['observed_capture_unchanged'] is True
                  and save['probe_contract'] == 'idle-no-capture' and save['token'] == token
                  and save['probe_file'] == result['probe_file']
                  and re.fullmatch(r'/perf/sf-probe-[0-9a-f]{32}\.ndjson', save['probe_file']) is not None)
        else:
            save = receipt(scenario, 'save.json')
            check('capture_baseline_change_cancels_persistence',
                  result['phase'] == 'cancelled' and result['reason'] == 'capture_observed'
                  and save['status'] == 'capture_observed' and save['persisted'] is False
                  and (guest / 'capture.probe.ndjson').stat().st_size == 0)

    begin, attempt, records = frozen('normal', True)
    check('normal_committed_one_attempt_then_passthrough',
          begin['probe']['mode'] == 1 and begin['probe']['claimed'] is True
          and begin['probe']['returned'] is True and begin['probe']['outcome'] == 1
          and attempt['state'] == 'committed' and attempt['rc'] == 0
          and response('normal-attempt')[1:] == [1, 1]
          and response('normal-future')[1:] == [0, 1]
          and any(r['kind'] == 'frame' and r['reason'] == 11 and not r['flags'] & 256 for r in records))
    begin, attempt, _ = frozen('pending', True)
    check('pending_ycc_persisted_without_false_generic_safe',
          begin['backend_active'] > 0 and begin['probe']['claimed'] is True
          and begin['probe']['returned'] is False and attempt['state'] == 'pending'
          and attempt['current_stage'] == 17
          and receipt('pending', 'save.json')['backend_pending'] is True
          and receipt('pending', 'diag.json')['safe_to_persist'] is False)
    check('pending_job_survives_ui_group_termination',
          int((guest / 'pending.ui-terminated.exit').read_text(encoding='utf-8')) != 0
          and (guest / 'pending.ui-termination-order').read_text(encoding='utf-8').strip() == 'terminated_before_snapshot'
          and fields(guest / 'pending.result')['phase'] == 'persisted')
    after = json.loads((guest / 'pending-after-release.json').read_text(encoding='utf-8'))
    check('pending_release_then_future_passthrough',
          response('pending-future')[1:] == [0, 1]
          and after['backend_active'] == after['inflight'] == after['captures'] == 0)
    frozen('capture', False)
    check('capture_prepare_and_finish_real_return_values',
          response('capture-prepare')[0] == 0 and response('capture-finish')[0] == 1)
    check('capture_cancel_future_remains_passthrough', response('capture-future')[1:] == [0, 1])
    frozen('ioerror', False)
    check('ioerror_future_remains_passthrough', response('ioerror-future')[1:] == [0, 1])
    for name in ('normal-future', 'pending-future', 'capture-future', 'ioerror-future'):
        check(name.replace('-', '_') + '_pixels', (guest / (name + '.yuv')).read_bytes() == original)
    final = json.loads((guest / 'collector-final.json').read_text(encoding='utf-8'))
    check('actual_seven_slot_s2_resident', final['installed'] is True and final['profile'] == 1 and final['abi'] == 4)
    check('exactly_one_new_snapshot_per_job',
          len({receipt(name, 'diag.json')['dump_id'] for name in ('normal', 'pending', 'capture', 'ioerror')}) == 4
          and len(list((guest / 'frozen').glob('*.ndjson'))) == 4)
    check('guest_completed', (guest / 'probe.exit').read_text(encoding='utf-8').strip() == '0')
    check('executed_harness_exact_bytes', (guest / 'executed-probe.sh').read_bytes() == PROBE.encode())
    artifact_names = ('bin/smartfilter_fixture.arm', 'bin/smartfilter_loader.arm',
                      'bin/smartfilter_probe_launcher.arm', 'smartfilter_ram.bin',
                      'smartfilter-preview.sh', 'smartfilter-probe-collector.sh')
    artifacts = {name: dict(bytes=(OUT / 'source-files' / name).stat().st_size,
                            sha256=hashlib.sha256((OUT / 'source-files' / name).read_bytes()).hexdigest())
                 for name in artifact_names}
    provenance_path = OUT / 'reuse-provenance.json'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8')) if provenance_path.is_file() else None
    if provenance is not None:
        check('reused_candidate_complete_byte_identity',
              all(artifacts[name] == provenance['artifacts'][name] for name in artifact_names)
              and provenance['ram_build_source_identity'] == build
              and provenance['arm_build_performed'] is False)
    return dict(passed=True, checks=checks, actual_ram_loader=True, actual_plt_hooks=7,
                artifact_bytes=artifacts, reused_prepared_candidate=provenance is not None,
                reuse_provenance=provenance,
                collector_actual_wrapper_launcher=True, capture_baseline_rejection=True,
                physical_camera_or_card_accessed=False, hardware_isp_pixels_verified=False,
                fixture_scope='Original ARM S2 paths/worker; named UDD, cache and hardware construction doubles')


if __name__ == '__main__':
    boundary.OUT = OUT
    boundary.SOCKET = '/tmp/nx300m-smartfilter-collector-private'
    boundary.MARKER = 'SMARTFILTER_COLLECTOR_PRIVATE_VM_READY'
    boundary.PROBE = PROBE
    boundary.prepare = prepare
    boundary.verify = verify
    boundary.call = call
    boundary.main()
