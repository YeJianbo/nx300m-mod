"""Continuous S2: real frozen shell wrapper + ARM launcher/loader in private VM.

Only reads cached VM/build inputs; creates fresh evidence under live-vm. No
camera/card passthrough, package build, shared VM preparation or dependency install.
Stock Prepare/Execute/worker and graph prefixes execute; UDD pixels, cache and
hardware construction remain the existing explicitly named fixture doubles.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import time

# Also supports python -I without importing anything from an extracted archive.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arm_vm
import probe_smartfilter_ram_vm as ram
import probe_smartfilter_vm as boundary

ROOT = ram.ROOT
BUILD = ram.BUILD
OUT = BUILD / 'live-vm'
SOCKET = '/tmp/nx300m-smartfilter-live-private'
MARKER = 'SMARTFILTER_LIVE_PRIVATE_VM_READY'
_PREFIX = ram.PROBE[:ram.PROBE.index('request original-mode 2')]
_PREFIX = _PREFIX.replace('case $cmd in 1|5|7|9)', 'case $cmd in 1|5|7|9|10)')
_PREFIX = _PREFIX.replace('    cp /tmp/smartfilter-ram.stdout',
                          '    gather_live || :\n    cp /tmp/smartfilter-ram.stdout')
PROBE = _PREFIX + r'''
umask 077
RESULTS=/arm-vm/mfw-results
cp /arm-vm/mfw-probe.sh executed-probe.sh
RUNTIME=/tmp/nx300m-live-runtime
STATE=/tmp/nx300m-live-state
GUARD=/tmp/nx300m-smartfilter-preview.uncertain
BACKEND=/var/run/nx300m-preview-backend
SELECTION=/var/run/nx300m-lut-selection
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/preview-tests" "$STATE" /perf/looks/identity /perf/looks/monochrome jobs
chmod 700 "$RUNTIME" "$STATE" /perf
source_id=$(stat -c '%d:%i' /perf)
export NX300M_MOD_ROOT="$RUNTIME" NX_STATE_DIR="$STATE"
export NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID="$source_id" NX_SF_PROBE_FIXTURE=1
printf '%s\n' "$source_id" > source.id
cat /proc/$pid/stat > actual-camera.stat
cat /proc/mounts > mounts.txt
for pair in smartfilter-live-preview.sh:smartfilter-live-preview.sh smartfilter-probe-collector.sh:smartfilter-probe-collector.sh bin/smartfilter_probe_launcher.arm:bin/smartfilter_probe_launcher.arm bin/smartfilter_loader.arm:bin/smartfilter_loader.arm smartfilter_ram.bin:lib/smartfilter_ram.bin monochrome.d4lut:preview-tests/monochrome.d4lut; do
    cp "/mnt/mmc/${pair%:*}" "$RUNTIME/${pair#*:}"
    cmp -s "/mnt/mmc/${pair%:*}" "$RUNTIME/${pair#*:}"
done
chmod 500 "$RUNTIME/"*.sh "$RUNTIME/bin/"*.arm
cp /mnt/mmc/identity.d4lut /perf/looks/identity/table.d4lut
cp /mnt/mmc/monochrome.d4lut /perf/looks/monochrome/table.d4lut
printf '41 /perf/looks/identity/table.d4lut 37\n' > "$SELECTION"
cp "$SELECTION" selection.original
stat -f -c '%T' "$RUNTIME" "$STATE" > ram-filesystems.txt

job_list() {
    for path in /tmp/nx300m-sf-job.*; do
        [ ! -d "$path" ] || printf '%s\n' "$path"
    done
}
snapshot_originals() {
    label=$1
    sha256sum "$RUNTIME/smartfilter-live-preview.sh" "$RUNTIME/bin/"*.arm "$RUNTIME/lib/smartfilter_ram.bin" "$RUNTIME/preview-tests/monochrome.d4lut" /perf/looks/*/table.d4lut > "$label.originals.sha256"
}
gather_live() {
    mkdir -p "$RESULTS/jobs"
    for path in /tmp/nx300m-sf-job.*; do
        [ -d "$path" ] || continue
        saved="$RESULTS/jobs/${path##*/}"
        mkdir -p "$saved"
        (cd "$path"; ls -la; sha256sum launcher loader.arm blob.bin collector.sh table.d4lut 2>/dev/null || :) > "$saved/resources.txt"
        for name in control.json control.stderr; do
            [ ! -f "$path/$name" ] || cp "$path/$name" "$saved/$name"
        done
    done
    [ ! -e "$GUARD" ] || cp -P "$GUARD" "$RESULTS/guard.final"
    [ ! -f "$SELECTION" ] || cp "$SELECTION" "$RESULTS/selection.final"
    [ ! -f "$BACKEND" ] || cp "$BACKEND" "$RESULTS/backend.final"
    for path in /var/run/nx300m-smartfilter-*.state; do
        [ ! -f "$path" ] || cp "$path" "$RESULTS/"
    done
    cat /proc/$pid/maps > "$RESULTS/last-camera.maps" 2>/dev/null || :
}
wrap() {
    label=$1; expected=$2; action=$3
    job_list > "$label.jobs.before"
    rc=0
    /bin/sh "$RUNTIME/smartfilter-live-preview.sh" "$action" > "$label.stdout" 2> "$label.stderr" || rc=$?
    printf '%s\n' "$rc" > "$label.exit"
    job_list > "$label.jobs.after"
    if [ "$expected" = ok ]; then [ "$rc" = 0 ]; else [ "$rc" -ne 0 ]; fi
    if [ -f "$BACKEND" ]; then cp "$BACKEND" "$label.backend"; else : > "$label.backend"; fi
    cp "$SELECTION" "$label.selection"
}
# Observe only this fixture's resident config. dd reads /proc, never writes it.
# Header/config sizes come from the exact staged native blob, not a fake loader.
config_snapshot() {
    label=$1
    config=$(od -An -tu4 -j 60 -N 4 "/var/run/nx300m-smartfilter-$pid.state" | tr -d ' ')
    size=$(od -An -tu4 -j 56 -N 4 "$BLOB" | tr -d ' ')
    /rescue/busybox dd if=/proc/$pid/mem of="$label.config.bin" bs=1 skip="$config" count="$size" 2> "$label.config.stderr"
    [ "$(stat -c '%s' "$label.config.bin")" = "$size" ]
}
snapshot_originals before
wrap sync-disabled reject sync
operation initial status

# A single apply must render both frames: no rearm, status or second apply
# between request10 calls. request13 executes original Create/B2Y/LDC prefixes.
wrap mono ok test-monochrome
wrap mono-status ok status
config_snapshot mono
request mono-paths 13
request mono-first 10
request mono-second 10
wrap mono-after ok status
request still-mode 2
cmp "$SELECTION" selection.original

# enable-s2 consumes the current still choice/strength without changing it.
wrap selected ok enable-s2
request selected-first 10
wrap selected-after ok status
printf '42 /perf/looks/monochrome/table.d4lut 50\n' > "$SELECTION"
cp "$SELECTION" selection.synced-original
wrap synced ok sync
request synced-first 10
request synced-second 10
wrap synced-after ok status
cmp "$SELECTION" selection.synced-original

# All preflight rejects must leave the resident generation and originals intact.
# There is one wrapper invocation per case; no retry harness is used.
wrap before-rejects ok status
printf '43 /perf/looks/missing/table.d4lut 80\n' > "$SELECTION"
wrap missing-table reject sync
printf '44 /perf/looks/../identity/table.d4lut 80\n' > "$SELECTION"
wrap invalid-path reject sync
printf '45 /perf/looks/monochrome/table.d4lut 101\n' > "$SELECTION"
wrap invalid-strength reject sync
printf '46 /perf/looks/monochrome/table.d4lut 50\n' > "$SELECTION"
NX300M_MOD_SOURCE_ID=0:0; export NX300M_MOD_SOURCE_ID
wrap source-id reject sync
NX300M_MOD_SOURCE_ID="$source_id"; export NX300M_MOD_SOURCE_ID
# A missing built-in diagnostic table must not fall back to stock or identity.
mv "$RUNTIME/preview-tests/monochrome.d4lut" "$RUNTIME/preview-tests/monochrome.retained"
wrap missing-builtin reject test-monochrome
mv "$RUNTIME/preview-tests/monochrome.retained" "$RUNTIME/preview-tests/monochrome.d4lut"
wrap after-rejects ok status
snapshot_originals after

# Existing uncertainty is never overwritten/removed, for sync and restore alike.
printf 'existing unresolved operation; retain exact evidence\n' > "$GUARD"
cp "$GUARD" guard.original
stat -c '%d:%i:%s:%Y' "$GUARD" > guard.before.stat
wrap uncertain-sync reject sync
wrap uncertain-restore reject restore
cmp "$GUARD" guard.original
stat -c '%d:%i:%s:%Y' "$GUARD" > guard.after.stat
cp "$GUARD" guard.retained
# Only the test coordinator clears its own sentinel to continue the restore case.
rm "$GUARD"
wrap restored ok restore
request restored-frame 10
wrap restored-status ok status
wrap sync-after-restore reject sync

# Timing is opt-in and bounded, not a one-frame pixel probe. Exercise beyond
# capacity without another control transaction; still/capture uses separate slots.
wrap timed ok test-monochrome-timing
config_snapshot timed
index=0
while [ "$index" -lt 132 ]; do
    request "timed-frame-$index" 10
    index=$((index+1))
done
request timed-capture 6
wrap timed-before-stop ok status
export NX_FN_KEY_LOG=/perf/fn-key.log
wrap timed-stopped ok restore
cp /perf/fn-key.log wrapper-kept-persisted.log
unset NX_FN_KEY_LOG
request timed-stopped-frame 10
wrap timed-after-stop ok status

# A failed export must not keep the wrapper backend marker or LUT enabled.
# Corrupt only a fixture-owned deterministic snapshot name before soft stop.
wrap broken-snapshot ok test-monochrome-timing
request broken-snapshot-frame 10
wrap broken-snapshot-status ok status
token=$(awk -F '"token":"' '{split($2,v,"\""); print v[1]}' broken-snapshot-status.stdout)
old_ifs=$IFS; IFS=:; set -- $token; IFS=$old_ifs
[ "$#" = 5 ]
printf 'invalid fixture snapshot\n' > "/var/run/nx300m-sf-diag-$1-$2-$3-$4.ndjson"
wrap broken-snapshot-stop ok restore
wrap broken-snapshot-after ok status
rm "/var/run/nx300m-sf-diag-$1-$2-$3-$4.ndjson"

# Fixture-only faults are never admitted by production CLI. Keep restore's
# result independent of optional allocation/export/persistence outcomes.
for fault in alloc export; do
    operation "keep-$fault-apply" apply --s2 --timing 1 --table /mnt/mmc/monochrome.d4lut --look "keep-$fault"
    request "keep-$fault-frame" 10
    operation "keep-$fault-stop" restore --keep-timing --fixture-keep-failure "$fault"
    operation "keep-$fault-after" status
done
operation keep-persist-apply apply --s2 --timing 1 --table /mnt/mmc/monochrome.d4lut --look keep-persist
request keep-persist-frame 10
operation keep-persist-stop restore --keep-timing --diag-log /perf/fn-key.log
cp /perf/fn-key.log kept-persisted.log

operation keep-io-apply apply --s2 --timing 1 --table /mnt/mmc/monochrome.d4lut --look keep-io
request keep-io-frame 10
mv /perf/fn-key.log /perf/fn-key.retained
mkdir /perf/fn-key.log
operation keep-io-stop restore --keep-timing --diag-log /perf/fn-key.log
operation keep-io-after status
rmdir /perf/fn-key.log
mv /perf/fn-key.retained /perf/fn-key.log

operation keep-drain-apply apply --s2 --timing 1 --table /mnt/mmc/monochrome.d4lut --look keep-drain
request keep-drain-pending 27
(sleep 0.1; request keep-drain-release 28) &
release_pid=$!
operation keep-drain-stop restore --keep-timing
wait "$release_pid"
operation keep-drain-after status

operation keep-rollback-apply apply --s2 --timing 1 --table /mnt/mmc/monochrome.d4lut --look keep-rollback
request keep-rollback-frame 10
rc=0
"$LOADER" restore --keep-timing --fixture-fail-slot 6 --pid "$pid" --blob "$BLOB" --fixture > keep-rollback-stop.stdout 2> keep-rollback-stop.stderr || rc=$?
printf '%s\n' "$rc" > keep-rollback-stop.exit
[ "$rc" = 3 ]
operation keep-rollback-after status
operation keep-rollback-cleanup restore --keep-timing

for name in /var/run/nx300m-sf-diag-*.ndjson; do
    [ ! -f "$name" ] || cp "$name" "$RESULTS/"
done
snapshot_originals final
cp /mnt/mmc/smartfilter-live-preview.sh executed-wrapper.sh
sha256sum /mnt/mmc/bin/smartfilter_fixture.arm /mnt/mmc/bin/smartfilter_loader.arm /mnt/mmc/bin/smartfilter_probe_launcher.arm /mnt/mmc/smartfilter_ram.bin /mnt/mmc/lib/libnx-smartfilter-lut.so > executed-artifacts.sha256
gather_live
''' + ram.PROBE[ram.PROBE.index('cat /proc/$pid/maps > after.maps'):]


def digest(path):
    data = path.read_bytes()
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def configure():
    boundary.OUT = OUT
    boundary.SOCKET = SOCKET
    boundary.MARKER = MARKER
    boundary.PROBE = PROBE
    boundary.configure()


def call(action, command=''):
    result = subprocess.run(
        ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3', '-I',
         arm_vm.wsl_path(__file__), '--linux', action,
         base64.b64encode(command.encode()).decode()],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=390)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def prepare():
    """Reuse only immutable cached inputs; compile fixture into our own directory."""
    cached = BUILD / 'collector-vm/source-files'
    build = json.loads((BUILD / 'ram-build-report.json').read_text(encoding='utf-8'))
    for name, expected in build['control_artifacts'].items():
        if digest(BUILD / name)['sha256'] != expected:
            raise ValueError('Current control artifact differs from build report: ' + name)
    for name, expected in build['control_sources'].items():
        if digest(ROOT / name)['sha256'] != expected:
            raise ValueError('Current control source differs from build report: ' + name)
    blob = (BUILD / 'smartfilter_ram.bin').read_bytes()
    words = struct.unpack_from('<18I', blob)
    if words[0] != 0x5246534e or words[1] != 4 or words[17] != build['build']:
        raise ValueError('Current RAM blob/header does not match ABI4 build report')
    # Recompute the native build ID exactly as the existing builder does.
    stock_header = (BUILD / 'smartfilter_stock.h').read_text(encoding='ascii')
    prefix, suffix = stock_header.rsplit('#define NX_SFR_BUILD ', 1)
    native_sources = ['smartfilter_ram.c', 'smartfilter_ram.h', 'smartfilter_ram.ld',
                      'smartfilter_lut_bridge.c', 'smartfilter_lut_bridge.h',
                      'smartfilter_ipcs.c', 'smartfilter_ipcs.h',
                      'smartfilter_timing.c', 'smartfilter_timing.h']
    identity = hashlib.sha256()
    for name in native_sources:
        identity.update((ROOT / 'runtime' / name).read_bytes())
    identity.update(prefix.rstrip('\n').encode())
    if (int.from_bytes(identity.digest()[:4], 'little') or 1) != build['build']:
        raise ValueError('Current RAM sources differ from native build identity')
    stage = OUT / 'source-files'
    resources = {
        'bin/smartfilter_loader.arm': BUILD / 'smartfilter_loader.arm',
        'bin/smartfilter_probe_launcher.arm': BUILD / 'smartfilter_probe_launcher.arm',
        'smartfilter_ram.bin': BUILD / 'smartfilter_ram.bin',
        'lib/libnx-smartfilter-lut.so': BUILD / 'libnx-smartfilter-lut.so',
        'lib/libmisc.so': cached / 'lib/libmisc.so',
        'input.yuv': cached / 'input.yuv',
        'identity.d4lut': cached / 'identity.d4lut',
        'monochrome.d4lut': cached / 'monochrome.d4lut',
        'smartfilter-live-preview.sh': ROOT / 'mod/deployment/mfw/smartfilter-live-preview.sh',
        'smartfilter-probe-collector.sh': ROOT / 'mod/deployment/mfw/smartfilter-probe-collector.sh',
    }
    for name, source in resources.items():
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
    fixture = stage / 'bin/smartfilter_fixture.arm'
    # Identical compile/link flags and fixed old-glibc stock libraries to ram.prepare;
    # deliberately do not invoke its shared production build/preparation functions.
    script = f'''set -eu
    tc=/opt/nx300m-toolchain/usr
    rf=/opt/nx300m-lab/stock-1.15
    export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
    "$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
      -std=gnu99 -O1 -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-const-variable \\
      -march=armv7-a -mfloat-abi=soft -fno-pic -no-pie -fno-stack-protector \\
      -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
      -Wl,--export-dynamic -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" \\
      -Wl,-rpath-link,"$rf/lib" -I{shlex.quote(arm_vm.wsl_path(BUILD))} \\
      {shlex.quote(arm_vm.wsl_path(ROOT / 'runtime/smartfilter_ram_fixture.c'))} \\
      {shlex.quote(arm_vm.wsl_path(ROOT / 'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
      {shlex.quote(arm_vm.wsl_path(stage / 'lib/libnx-smartfilter-lut.so'))} \\
      "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc \\
      -o {shlex.quote(arm_vm.wsl_path(fixture))}
    '''
    built = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'sh', '-s'],
                           input=script.encode(), capture_output=True, timeout=120)
    (OUT / 'fixture-build.stderr.log').write_bytes(built.stderr)
    (OUT / 'fixture-build.stdout.log').write_bytes(built.stdout)
    if built.returncode:
        raise RuntimeError('Private fixture compilation failed; see fixture-build.stderr.log')
    artifacts = {p.relative_to(stage).as_posix(): digest(p)
                 for p in stage.rglob('*') if p.is_file()}
    candidate = dict(artifacts=artifacts, ram_build=build,
                     inventory={name: meta['bytes'] for name, meta in artifacts.items()},
                     fixture_sources={name: digest(ROOT / name) for name in
                        ('runtime/smartfilter_ram_fixture.c', 'runtime/smartfilter_fixture.c')},
                     cached_inputs=str(cached), shared_preparation_or_build_run=False,
                     base_initramfs=digest(arm_vm.WORK / 'initramfs.cpio.gz'))
    arm_vm.write_json(OUT / 'candidate.json', candidate)
    boundary.vm.initramfs()


def verify(guest):
    checks = {}

    def check(name, value):
        checks[name] = bool(value)
        arm_vm.write_json(OUT / 'checks.json', checks)
        if not value:
            raise AssertionError(name)

    def text(name):
        return (guest / name).read_text(encoding='utf-8')

    def receipt(name):
        return next(json.loads(line) for line in text(name + '.stdout').splitlines()
                    if line.startswith('{'))

    def response(name):
        return [int(v) for v in text(name + '.response').split()]

    def frame(name):
        return (guest / (name + '.yuv')).read_bytes()

    candidate = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))
    check('executed_harness_exact_bytes', (guest / 'executed-probe.sh').read_bytes() == PROBE.encode())
    check('executed_wrapper_exact_bytes', digest(guest / 'executed-wrapper.sh') ==
          candidate['artifacts']['smartfilter-live-preview.sh'])
    check('wrapper_and_state_tmpfs', text('ram-filesystems.txt').split() == ['tmpfs', 'tmpfs'])
    check('actual_source_stat_identity', re.fullmatch(r'\d+:\d+\n', text('source.id')) is not None)
    check('sync_does_not_enable', int(text('sync-disabled.exit')) != 0 and
          'not enabled' in text('sync-disabled.stderr') and
          not json.loads(text('initial.json'))['installed'])
    mono = receipt('mono-status')
    after = receipt('mono-after')
    check('monochrome_actual_s2_status', mono['installed'] and mono['enabled'] and
          mono['abi'] == 4 and mono['profile'] == 1 and mono['look_id'] == 'monochrome' and
          mono['strength'] == 100 and mono['probe_once'] == 0 and mono['probe_state'] == 0)
    cfg = (guest / 'mono.config.bin').read_bytes()
    check('monochrome_timing_disabled_in_actual_resident_config',
          len(cfg) == candidate['ram_build']['config_bytes'] and
          struct.unpack_from('<II', cfg, len(cfg) - 8) == (0, 0) and after['diag_frames'] == 0)
    check('same_apply_two_consecutive_real_worker_frames',
          response('mono-first')[1:] == response('mono-second')[1:] == [1, 1] and
          after['requested_generation'] == mono['requested_generation'] and
          after['rendered'] - mono['rendered'] == 2 and
          after['applied_generation'] == after['requested_generation'] and after['s2_frames'] >= 2)
    check('actual_create_b2y_ldc_prefixes', all(after['mode_forged'][i] > 0 for i in (0, 2, 3)))
    original = (OUT / 'source-files/input.yuv').read_bytes()
    y_bytes = 64 * 48
    for name in ('mono-first', 'mono-second'):
        pixels = frame(name)
        check(name + '_software_reference_monochrome_pixels', len(pixels) == len(original) and
              all(v == 128 for v in pixels[y_bytes:]) and
              max(abs(a - b) for a, b in zip(pixels[:y_bytes], original[:y_bytes])) <= 1)
    check('two_monochrome_frames_identical', frame('mono-first') == frame('mono-second'))
    check('still_selection_bytes_unchanged_by_test_monochrome',
          all((guest / (name + '.selection')).read_bytes() == (guest / 'selection.original').read_bytes()
              for name in ('sync-disabled', 'mono', 'mono-status', 'mono-after', 'selected', 'selected-after')))
    check('still_host_mode_unchanged', response('still-mode')[0] == 2 and after['host_still_mode'] == 2)
    selected, synced = receipt('selected-after'), receipt('synced-after')
    check('enable_s2_reads_current_choice_and_strength', selected['look_id'] == 'identity' and
          selected['strength'] == 37 and selected['probe_once'] == 0 and
          response('selected-first')[1:] == [1, 1])
    check('sync_updates_current_choice_and_strength', synced['look_id'] == 'monochrome' and
          synced['strength'] == 50 and synced['probe_once'] == 0 and
          synced['requested_generation'] > selected['requested_generation'] and
          response('synced-first')[1:] == response('synced-second')[1:] == [1, 1] and
          (guest / 'synced.selection').read_bytes() == (guest / 'selection.synced-original').read_bytes())
    chroma_error = max(abs((a - 128) - (b - 128) * .5)
                       for a, b in zip(frame('synced-first')[y_bytes:], original[y_bytes:]))
    check('half_strength_software_reference_pixels', chroma_error <= 2 and
          frame('synced-first') == frame('synced-second'))
    reject_text = {'missing-table': 'missing or linked', 'invalid-path': 'invalid look ID',
                   'invalid-strength': 'strength must be 0..100', 'source-id': 'source identity changed',
                   'missing-builtin': 'missing or linked', 'uncertain-sync': '禁止重武装',
                   'uncertain-restore': '禁止重武装', 'sync-after-restore': 'not enabled'}
    for name, message in reject_text.items():
        before_jobs, after_jobs = set(text(name + '.jobs.before').splitlines()), set(text(name + '.jobs.after').splitlines())
        new_jobs = after_jobs - before_jobs
        check(name + '_reject_without_retry', int(text(name + '.exit')) != 0 and
              message in text(name + '.stderr') and len(new_jobs) <= 1 and
              all(not (guest / 'jobs' / Path(job).name / 'control.json').exists() for job in new_jobs))
    before, rejected = receipt('before-rejects'), receipt('after-rejects')
    check('preflight_rejects_leave_resident_selection_unchanged',
          all(before[k] == rejected[k] for k in ('requested_generation', 'applied_generation',
              'look_id', 'strength', 'rendered', 'enabled', 'probe_once')))
    check('existing_uncertain_guard_original_bytes_and_inode_retained',
          (guest / 'guard.original').read_bytes() == (guest / 'guard.retained').read_bytes() and
          text('guard.before.stat') == text('guard.after.stat'))
    check('all_original_wrappers_tables_and_native_artifacts_unchanged',
          text('before.originals.sha256') == text('after.originals.sha256') == text('final.originals.sha256'))
    restored = receipt('restored-status')
    check('restore_soft_stops_retains_routes_and_clears_backend',
          restored['installed'] and not restored['enabled'] and not restored['insert_enabled'] and
          restored['routing_retained'] and restored['restart_required_for_full_restore'] and
          text('restored.backend') == '' and response('restored-frame')[1:] == [0, 1] and
          frame('restored-frame') == original)
    for name in ('mono', 'selected', 'synced'):
        check(name + '_backend_marker', text(name + '.backend') == 'smartfilter\n')
    camera = text('actual-camera.stat')
    pid = int(camera.split(' ', 1)[0])
    start = int(camera.rsplit(') ', 1)[1].split()[19])
    check('receipts_bound_to_actual_arm_fixture_process_and_build', all(
          receipt(name)['pid'] == pid and receipt(name)['start'] == start and
          receipt(name)['build'] == candidate['ram_build']['build']
          for name in ('mono-status', 'mono-after', 'selected-after', 'synced-after', 'restored-status')))
    executed = {line.split()[-1]: line.split()[0] for line in text('executed-artifacts.sha256').splitlines()}
    check('executed_native_artifact_digests_match_candidate', all(
          executed['/mnt/mmc/' + name] == candidate['artifacts'][name]['sha256'] for name in
          ('bin/smartfilter_fixture.arm', 'bin/smartfilter_loader.arm',
           'bin/smartfilter_probe_launcher.arm', 'smartfilter_ram.bin', 'lib/libnx-smartfilter-lut.so')))
    # Each successful wrapper operation executes one real launcher/loader job.
    for path in (guest / 'jobs').iterdir():
        resources = (path / 'resources.txt').read_text(encoding='utf-8')
        for name, artifact in (('launcher', 'bin/smartfilter_probe_launcher.arm'),
                               ('loader.arm', 'bin/smartfilter_loader.arm'), ('blob.bin', 'smartfilter_ram.bin')):
            check(path.name + '_' + name + '_byte_identity',
                  candidate['artifacts'][artifact]['sha256'] + '  ' + name in resources)
    check('mfw_library_byte_identity', text('mfw-library.sha256').split()[0] ==
          candidate['artifacts']['lib/libmisc.so']['sha256'])

    def rows(name, suffix='.stdout'):
        return [json.loads(line) for line in text(name + suffix).splitlines() if line.startswith('{')]

    def stop_rows(name, suffix='.stdout'):
        values = rows(name, suffix)
        return (next(v for v in values if v['command'] == 'restore'),
                next(v for v in values if v['command'] == 'restore-timing'))

    timed = receipt('timed-before-stop')
    stopped, saved = stop_rows('timed-stopped')
    timed_cfg = (guest / 'timed.config.bin').read_bytes()
    check('timing_opt_in_continuous_not_probe', struct.unpack_from('<II', timed_cfg, len(timed_cfg)-8) == (1, 0) and
          timed['probe_once'] == 0 and timed['look_id'] == 'monochrome' and timed['strength'] == 100)
    check('timing_capacity_keeps_continuous_lut', timed['diag_frames'] == 128 and timed['diag_dropped'] > 0 and
          response('timed-frame-0')[1:] == response('timed-frame-131')[1:] == [1, 1] and
          frame('timed-frame-0') == frame('timed-frame-131') and timed['rendered'] >= after['rendered']+132)
    check('timed_restore_old_window_new_generation', saved['command'] == 'restore-timing' and
          saved['snapshot_created'] and saved['persisted'] and saved['status'] == 'persisted' and
          saved['token'] == timed['token'] and stopped['requested_generation'] == timed['requested_generation']+1 and
          stopped['diag_window'] == timed['diag_window']+1 and stopped['enabled'] is False)
    check('timed_restore_clears_backend_and_keeps_routes', text('timed-stopped.backend') == '' and
          receipt('timed-after-stop')['routing_retained'] and
          response('timed-stopped-frame')[1:] == [0, 1] and frame('timed-stopped-frame') == original)
    check('untimed_restore_does_not_export', stop_rows('restored')[1]['status'] == 'no_data')
    broken_stop, broken_save = stop_rows('broken-snapshot-stop')
    check('wrapper_export_failure_does_not_prevent_soft_stop', broken_stop['enabled'] is False and
          broken_save['status'] == 'export_failed' and broken_save['snapshot_created'] is False and
          text('broken-snapshot-stop.exit').strip() == '0' and text('broken-snapshot-stop.backend') == '' and
          receipt('broken-snapshot-after')['enabled'] is False)
    for fault, status in (('alloc', 'snapshot_unavailable'), ('export', 'export_failed')):
        stop, diagnostic = stop_rows('keep-' + fault + '-stop', '.json')
        observed = json.loads(text('keep-' + fault + '-after.json'))
        check('keep_' + fault + '_failure_preserves_restore_success', stop['enabled'] is False and
              diagnostic['status'] == status and diagnostic['snapshot_created'] is False and
              observed['enabled'] is False and observed['backend_active'] == observed['captures'] == 0)
    persisted_stop, persisted_save = stop_rows('keep-persist-stop', '.json')
    check('kept_window_uses_existing_log_persistence', persisted_stop['enabled'] is False and
          persisted_save['persisted'] and persisted_save['command'] == 'restore-timing')
    io_stop, io_save = stop_rows('keep-io-stop', '.json')
    check('kept_persistence_failure_leaves_valid_ram_and_soft_stop', io_stop['enabled'] is False and
          io_save['status'] == 'export_failed' and io_save['snapshot_created'] is True and
          io_save['persisted'] is False and json.loads(text('keep-io-after.json'))['enabled'] is False)
    drain_stop, drain_save = stop_rows('keep-drain-stop', '.json')
    check('keep_timing_waits_for_inflight_restore', drain_stop['enabled'] is False and
          drain_stop['backend_active'] == drain_stop['inflight'] == 0 and drain_save['snapshot_created'])
    rollback = json.loads(text('keep-rollback-after.json'))
    check('keep_timing_never_exports_rolled_back_restore', text('keep-rollback-stop.exit').strip() == '3' and
          rollback['enabled'] is True and not any(v['command'] == 'restore-timing' for v in rows('keep-rollback-stop')))
    from analyze_smartfilter_timing import parse_log, analyze
    timed_file = guest / Path(saved['diag_file']).name
    parsed = parse_log(timed_file)
    report = analyze(parsed)
    metadata = next(iter(parsed.dumps.values())).begin
    check('kept_snapshot_has_old_identity_and_real_stages', parsed.ok and
          metadata['generation'] == timed['requested_generation'] and metadata['look_id'] == 'monochrome' and
          metadata['strength'] == 100 and metadata['probe']['mode'] == 0 and
          any({'lut0','lut1','lut2','ycc','reset','iq_init','iq_process','close'} <= set(g['metrics'])
              for g in report['frame_groups'] if g['key']['path'] == 'render'))
    wrapper_log = parse_log(guest / 'wrapper-kept-persisted.log')
    check('wrapper_kept_window_persisted_via_source_log', wrapper_log.ok and
          set(wrapper_log.dumps) == set(parsed.dumps) and
          all(wrapper_log.dumps[key].payload == parsed.dumps[key].payload for key in parsed.dumps))
    check('kept_capture_slots_independent_of_full_frame_storage',
          {'prepare','finish'} <= {g['key']['kind'] for g in report['lifecycle_groups']})
    check('kept_persisted_log_valid', parse_log(guest / 'kept-persisted.log').ok)
    arm_vm.write_json(OUT / 'kept-timing-analysis.json', report)
    check('guest_completed', text('probe.exit').strip() == '0')
    return dict(passed=True, checks=checks, artifacts=candidate['artifacts'],
                candidate_sha256=digest(OUT / 'candidate.json')['sha256'],
                harness_sha256=hashlib.sha256(PROBE.encode()).hexdigest(),
                actual_wrapper_launcher_loader=True, actual_ram_loader=True, actual_plt_hooks=7,
                reference_pixels=dict(monochrome_chroma=128, half_strength_max_chroma_error=chroma_error),
                physical_camera_or_card_accessed=False, original_full_camera_app_run=False,
                hardware_isp_pixels_verified=False, cache_coherency_verified=False,
                hardware_control_locks_or_raw_modified=False, shared_reports_modified=False,
                fixture_scope='Original ARM S2 Prepare/Execute/worker and Create/B2Y/LDC prefixes; '
                              'named software UDD pixel/cache/queue/hardware-construction doubles, not sensor/ISP validation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', choices=('start', 'exec', 'stop'))
    parser.add_argument('payload', nargs='?', default='')
    args = parser.parse_args()
    configure()
    if args.linux:
        print(json.dumps(boundary.vm.linux_worker(args.linux, args.payload)))
        return
    if os.name != 'nt':
        parser.error('Run with the existing project Windows Conda Python')
    # Preserve all previous attempts; old evidence can never satisfy this run.
    if OUT.exists():
        OUT.rename(OUT.with_name('live-vm-before-' + str(time.time_ns())))
    OUT.mkdir(parents=True)
    report = dict(passed=False, phase='prepare', physical_camera_or_card_accessed=False)
    start_attempted = False
    tool_sha256 = digest(Path(__file__))['sha256']
    arm_vm.write_json(OUT / 'report.json', report)
    try:
        prepare()
        report['phase'] = 'boot'
        start_attempted = True
        print(json.dumps(call('start')), flush=True)
        deadline = time.monotonic() + boundary.BOOT_TIMEOUT
        while time.monotonic() < deadline:
            serial = OUT / 'serial.log'
            if serial.exists() and MARKER in serial.read_text(errors='replace'):
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Private continuous S2 VM boot timed out')
        report['phase'] = 'execute'
        try:
            execution = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        except Exception as exc:
            execution = dict(exit_code=124, output=str(exc))
        arm_vm.write_json(OUT / 'execution.json', execution)
        # Always collect even a failed guest. Reuse integrity-checked serial transfer.
        boundary.call = call
        guest = boundary.collect()
        if execution['exit_code'] or 'SMARTFILTER_ARM_BOUNDARY_PASSED' not in execution['output']:
            raise RuntimeError('Continuous S2 guest failed; retained execution.json and guest evidence')
        report['phase'] = 'verify'
        report = verify(guest)
        report['phase'] = 'complete'
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if start_attempted:
            try:
                arm_vm.write_json(OUT / 'shutdown.json', call('stop'))
                report['private_vm_stopped'] = True
            except Exception as exc:
                report['private_vm_stopped'] = False
                report['shutdown_error'] = str(exc)
        if (OUT / 'candidate.json').exists():
            report['candidate_sha256'] = digest(OUT / 'candidate.json')['sha256']
        report['tool_sha256'] = tool_sha256
        stable = digest(Path(__file__))['sha256'] == tool_sha256
        if (OUT / 'candidate.json').exists():
            candidate = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))
            stable = stable and all(digest(ROOT / name)['sha256'] == expected for name, expected in
                                    candidate['ram_build']['control_sources'].items())
        report['source_and_candidate_bytes_stable'] = stable
        if not stable:
            report['passed'] = False
            report['error'] = 'Tool or control sources changed during verification; rebuild and rerun'
        arm_vm.write_json(OUT / 'report.json', report)
    if not report.get('passed'):
        raise RuntimeError(report.get('error', 'Continuous verification did not pass'))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
