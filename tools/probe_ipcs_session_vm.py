"""ABI7 session control in the private ARM/MFW VM; physical IPCS is a named double."""
import base64
import json
from pathlib import Path
import shlex
import subprocess

import arm_vm
import probe_smartfilter_vm as boundary
import probe_smartfilter_ram_vm as ram
from build_smartfilter_lut_bridge import LIBRARY as BRIDGE
from mfw_runtime import firmware_profile

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'outputs/deployment/smartfilter-session'
OUT=BUILD/'arm-vm'
PROBE=ram.PROBE[:ram.PROBE.index('request original-mode 2')]+r'''
set -x
operation binding-libc-apply apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request automatic-insert 121
operation automatic-insert-status status
grep -q '"s2_path":1' automatic-insert-status.json
request automatic-repeat 120
operation automatic-repeat-status status
operation automatic-switch apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/monochrome.d4lut --look monochrome --strength 100
request automatic-switch 120
operation automatic-switch-status status
grep -q '"refresh_requests":1' automatic-switch-status.json
request binding-libc-call 110
operation binding-libc-status status
operation binding-libc-reapply apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
operation binding-libc-stop restore
request automatic-remove 120
operation automatic-remove-status status
grep -q '"s2_path":0' automatic-remove-status.json
operation automatic-full-queue-apply apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request automatic-full-queue 122
operation automatic-full-queue-status status
operation automatic-retry-apply apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request automatic-retry 120
operation automatic-retry-status status
grep -q '"s2_path":1' automatic-retry-status.json
operation automatic-final-stop restore
request automatic-final-remove 120
operation automatic-memory-apply apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request automatic-memory 123
operation automatic-memory-status status
operation automatic-memory-stop restore
request automatic-memory-remove 120
request binding-libc-restored 110
kill -TERM "$pid"; wait "$pid" || :
cp /tmp/smartfilter-ram.stdout binding-libc.stdout
set +x
rm -f /tmp/smartfilter-ram.ready /tmp/smartfilter-ram.request /tmp/smartfilter-ram.response
NX_SFR_FIXTURE_OPEN_BINDING=pthread /mnt/mmc/bin/smartfilter_fixture.arm > /tmp/smartfilter-ram.stdout 2> /tmp/smartfilter-ram.stderr &
pid=$!
tries=0
while [ ! -s /tmp/smartfilter-ram.ready ]; do
    tries=$((tries+1)); [ "$tries" -lt 100 ]; /rescue/busybox usleep 100000
done
read ready_pid < /tmp/smartfilter-ram.ready
[ "$ready_pid" = "$pid" ]
cat /proc/$pid/maps > before.maps
seq=0
operation baseline apply --s2 --session 0 --calltrace 1 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request binding-pthread-call 110
operation binding-pthread-status status
request baseline 104
operation baseline-trace open-export
operation candidate apply --s2 --session 1 --calltrace 1 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request candidate 100
request stream-stop-resume 109
request capture 101
request after-capture 100
request idle 102
request contention 103
request after-contention 100
operation candidate-trace open-export
operation return-baseline apply --s2 --session 0 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request return-baseline 104
operation returned-trace open-export
operation soft-stop restore
request binding-pthread-restored 110
request references 105
operation stopped-trace open-export
NX300M_MFW_RAM_BASE=/tmp/nx300m-open-diagnostic
RUNTIME=$NX300M_MFW_RAM_BASE/runtime
STATE=$NX300M_MFW_RAM_BASE/state
mkdir -p "$RUNTIME/bin" "$RUNTIME/lib" "$RUNTIME/ipcs-diagnostic/bin" "$RUNTIME/ipcs-diagnostic/lib" "$RUNTIME/preview-tests" "$STATE" /perf
cp /mnt/mmc/smartfilter-live-preview.sh "$RUNTIME/smartfilter-preview.sh"
cp /mnt/mmc/common.sh "$RUNTIME/common.sh"
cp /mnt/mmc/panel-actions.sh "$RUNTIME/mfw-panel-actions.sh"
cp /mnt/mmc/fn-panel-actions.sh "$RUNTIME/fn-panel-actions.sh"
cp /mnt/mmc/gui_preview_quick_ipcs.NX300M "$RUNTIME/gui_preview.NX300M"
cp /mnt/mmc/lut-load.sh "$RUNTIME/lut-load.sh"
/bin/sh -n /mnt/mmc/lut-load.sh
/bin/sh -n /mnt/mmc/lut-panel.sh
/bin/sh -n /mnt/mmc/lut-cycle.sh
/bin/sh -n /mnt/mmc/fn-panel.sh
! grep -q 'preview-softstop.sh.*--auto' /mnt/mmc/fn-panel.sh
cp /mnt/mmc/smartfilter-probe-collector.sh "$RUNTIME/smartfilter-probe-collector.sh"
cp /mnt/mmc/ipcs-kdd-collector.sh "$RUNTIME/ipcs-kdd-collector.sh"
cp /mnt/mmc/bin/smartfilter_loader.arm "$RUNTIME/ipcs-diagnostic/bin/"
cp /mnt/mmc/bin/smartfilter_probe_launcher.arm "$RUNTIME/ipcs-diagnostic/bin/"
cp /mnt/mmc/bin/lut_engine.arm "$RUNTIME/bin/"
cp /mnt/mmc/bin/lut_engine_switch_fixture.arm "$RUNTIME/bin/lut_engine.arm"
cp /mnt/mmc/bin/lut_live_loader.arm "$RUNTIME/bin/"
cp /mnt/mmc/lut_ram_blob.bin "$RUNTIME/lib/lut_ram_blob.bin"
cp /mnt/mmc/smartfilter_ram.bin "$RUNTIME/ipcs-diagnostic/lib/"
cp /mnt/mmc/monochrome.d4lut "$RUNTIME/preview-tests/monochrome.d4lut"
printf '1\n' > "$RUNTIME/IPCS_OPEN_DIAGNOSTIC"
printf '1\n' > "$RUNTIME/IPCS_SESSION_PREVIEW"
source_id=$(stat -c '%d:%i' /perf)
export NX300M_MFW_RAM_BASE NX300M_MOD_ROOT="$RUNTIME" NX_STATE_DIR="$STATE" NX_SF_PROBE_FIXTURE=1
export NX300M_MOD_SOURCE_ROOT=/perf NX300M_MOD_SOURCE_ID="$source_id" NX_FN_KEY_LOG=/perf/fn-key.log
export NX_FN_SERVICE_ROOT="$RUNTIME"
STILL=/mnt/mmc/bin/lut_live_loader.arm
ENGINE=/mnt/mmc/bin/lut_engine_switch_fixture.arm
export NX_LUT_VM_PID="$pid"
# The read-only capability query must not grant continuous actions to a probe
# wrapper, even when it is shown with the current reduced menu.
cp /mnt/mmc/smartfilter-preview.sh "$RUNTIME/smartfilter-preview.sh"
set +e
/bin/sh "$RUNTIME/fn-panel-actions.sh" preview-sync > probe-menu-sync.txt 2> probe-menu-sync.stderr
probe_menu_rc=$?
set -e
[ "$probe_menu_rc" != 0 ]
grep -q '探针包拒绝' probe-menu-sync.stderr
cp /mnt/mmc/smartfilter-live-preview.sh "$RUNTIME/smartfilter-preview.sh"
wait_kdd_arm() {
    kdd_job=$(sed -n 's/^accepted .* ram=//p' "$1")
    [ -d "$kdd_job" ]
    tries=0
    while ! grep -q '"status":"waiting"' "$kdd_job/kdd.json" 2>/dev/null; do
        tries=$((tries+1)); [ "$tries" -lt 150 ]; /rescue/busybox usleep 100000
    done
}
check_waiter_has_no_leases() {
    (
        exec 8>/tmp/nx300m-smartfilter-preview.lock; flock -n 8
        exec 7>"/var/run/nx300m-smartfilter-control-$pid.lock"; flock -n 7
        exec 5>"$STATE/media-io.lock"; flock -x -n 5
    )
}
# The first menu action must install Still before enabling live preview. Only
# the fixture identity gate differs from the production engine action code.
mkdir -p /perf/.state /perf/looks/current-mono /perf/looks/current-color /tmp/lut-panel-state
cp /mnt/mmc/monochrome.d4lut /perf/looks/current-mono/table.d4lut
cp /mnt/mmc/identity.d4lut /perf/looks/current-color/table.d4lut
printf 'current-mono|Mono|0|0|0|0|0|0|0\ncurrent-color|Color|0|0|0|0|0|0|0\n' > /perf/looks.txt
printf '1 /perf/looks/current-mono/table.d4lut 100\n' > /perf/.state/lut-selection
rm -f /var/run/nx300m-lut-selection /var/run/nx300m-lut-selection-session
/bin/sh "$RUNTIME/fn-panel-actions.sh" preview-s2-enable > wrapper-fast-arm.json 2> wrapper-fast-arm.stderr
cp /var/run/nx300m-preview-backend wrapper-fast-backend.txt
request wrapper-fast-config 112
NX300M_MOD_ROOT=/perf "$ENGINE" ready > first-preview-still-ready.json
request first-preview-still 113
cp /tmp/lut-switch-still.bin first-preview.still.bin
request wrapper-fast-render 100
operation wrapper-fast-trace open-export
# The Still callbacks and the Session backend now coexist in this one explicit
# process. The device writer is RAM MMIO; both native LUT branches are real.
for look in nx300-hard-mono ricoh-positive-exact; do
    mkdir -p "/perf/looks/$look"
    cp /mnt/mmc/looks/$look/* "/perf/looks/$look/"
    printf '%s|%s|0|0|0|0|0|0|0\n' "$look" "$look" >> /perf/looks.txt
done
NX300M_MOD_ROOT=/perf "$ENGINE" ready > still-engine-ready.json 2> still-engine-ready.stderr
switch=0
for look in nx300-hard-mono ricoh-positive-exact nx300-hard-mono; do
    switch=$((switch+1)); tag=switch-$switch
    request "$tag.stream-start" 116
    NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" apply "$look" 100 > "$tag.apply.json" 2> "$tag.apply.stderr"
    request "$tag.stream-capture" 118
    cp /tmp/lut-switch-still.bin "$tag.stream-capture.bin"
    request "$tag.stream-stop" 117
    cp /var/run/nx300m-lut-selection "$tag.selection"
    stat -c '%d:%i' /perf/.state/lut-choice > "$tag.saved-inode"
    request "$tag.still" 113
    cp /tmp/lut-switch-still.bin "$tag.still.bin"
    request "$tag.preview" 100
    request "$tag.after-preview" 115
    cp /tmp/lut-switch-still.bin "$tag.after-preview.bin"
    operation "$tag.status" status
    request "$tag.capture" 101
    request "$tag.after-capture" 115
    cp /tmp/lut-switch-still.bin "$tag.after-capture.bin"
done
# Stop only the preview through the actual menu route. Keep the current
# selection, saved choice and Still hooks; changing a look must stay photo-only.
cp /var/run/nx300m-lut-selection preview-only.selection
cp /perf/.state/lut-choice preview-only.saved
/bin/sh "$RUNTIME/fn-panel-actions.sh" preview-stop > preview-only-stop.json 2> preview-only-stop.stderr
[ ! -e /var/run/nx300m-preview-backend ]
cmp -s preview-only.selection /var/run/nx300m-lut-selection
cmp -s preview-only.saved /perf/.state/lut-choice
NX300M_MOD_ROOT=/perf "$ENGINE" ready > preview-only-ready.json
request preview-only-still 113
cp /tmp/lut-switch-still.bin preview-only.still.bin
operation preview-only-stopped status
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" apply ricoh-positive-exact 100 > preview-only-switch.json 2> preview-only-switch.stderr
[ ! -e /var/run/nx300m-preview-backend ]
operation preview-only-after-switch status
request preview-only-color-still 113
cp /tmp/lut-switch-still.bin preview-only-color.still.bin
/bin/sh "$RUNTIME/fn-panel-actions.sh" preview-s2-enable > preview-only-reenable.json 2> preview-only-reenable.stderr
request preview-only-reenabled 100
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" stock > switch-stock.json 2> switch-stock.stderr
request switch-stock-still 113
cp /tmp/lut-switch-still.bin switch-stock.still.bin
request switch-stock-preview 10
cp /tmp/smartfilter-ram.frame switch-stock.preview.yuv
# The explicit Off choice releases preview references and restores both Still
# pointers. Subsequent frames and captures use the original table, with no IPCS
# preview work. Selecting another look must lazily re-enable the same image.
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" apply nx300-hard-mono 100 > switch-before-off.json 2> switch-before-off.stderr
request switch-before-off-preview 100
request switch-off-stream-start 116
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" disable > switch-off.json 2> switch-off.stderr
request switch-off-stream-stop 117
[ ! -e /var/run/nx300m-preview-backend ]
[ ! -e /var/run/nx300m-lut-selection ]
cp /perf/.state/lut-choice switch-off.saved
set +e
"$STILL" status --pid "$pid" > switch-off-native.json
off_status_rc=$?
set -e
# Read-only status reports the real fixture executable (exit 2), while still
# measuring the exact original pointers. Do not relax production identity.
[ "$off_status_rc" = 2 ]
operation switch-off-before status
request switch-off-preview 10
cp /tmp/smartfilter-ram.frame switch-off-preview.yuv
operation switch-off-after status
operation switch-off-trace open-export
request switch-off-still 113
cp /tmp/lut-switch-still.bin switch-off.still.bin
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" apply ricoh-positive-exact 100 > switch-reenable.json 2> switch-reenable.stderr
NX300M_MOD_ROOT=/perf "$ENGINE" ready > switch-reenabled-native.json
request switch-reenable-still 113
cp /tmp/lut-switch-still.bin switch-reenable.still.bin
[ ! -e /var/run/nx300m-preview-backend ]
cp /mnt/mmc/gui_preview_ipcs_diagnostic.NX300M "$RUNTIME/gui_preview.NX300M"
/bin/sh "$RUNTIME/fn-panel-actions.sh" preview-s2-enable > menu-reenable.json 2> menu-reenable.stderr
request menu-reenable-render 100
operation menu-reenable-status status
# A sync failure is not a successful preview switch. Still retains the user's
# new choice; retry uses it rather than silently restoring the previous look.
mv "$RUNTIME/smartfilter-preview.sh" "$RUNTIME/preview-saved.sh"
printf '#!/bin/sh\nexit 9\n' > "$RUNTIME/smartfilter-preview.sh"
set +e
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" apply nx300-hard-mono 100 > partial-switch.json 2> partial-switch.stderr
partial_rc=$?
set -e
[ "$partial_rc" = 6 ]
cp /var/run/nx300m-lut-selection partial-switch.selection
mv "$RUNTIME/preview-saved.sh" "$RUNTIME/smartfilter-preview.sh"
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$ENGINE" apply nx300-hard-mono 100 > partial-retry.json 2> partial-retry.stderr
request partial-retry-render 100
operation partial-retry-status status
# Simulate the supervisor's handled cancellation after admission is paused.
# The in-flight frame stays held until recovery reopens admission. No SIGKILL.
request cancelled-control-frame 27
"$LOADER" apply --pid "$pid" --blob "$BLOB" --fixture --s2 --session 1 --calltrace 0 --timing 0 \
    --table /mnt/mmc/identity.d4lut --look cancelled --strength 100 > cancelled-control.json 2> cancelled-control.stderr &
control=$!
tries=0
while ! grep -q 'Native frame/capture in flight' "/var/run/nx300m-smartfilter-worker-$control.log" 2>/dev/null; do
    kill -0 "$control"
    tries=$((tries+1)); [ "$tries" -lt 1000 ]; /rescue/busybox usleep 10000
done
set -- $(cat "/proc/$control/task/$control/children")
[ "$#" = 1 ]; worker=$1
kill -USR1 "$worker"
set +e
wait "$control"
cancel_rc=$?
set -e
[ "$cancel_rc" != 0 ] && [ "$cancel_rc" != 5 ]
grep -q 'preview admission restored=true' cancelled-control.stderr
request cancelled-control-release 28
request cancelled-control-render 100
operation cancelled-control-status status
grep '^TracerPid:' "/proc/$pid/status" > cancelled-control-tracer.txt
request switch-stop-still-fixture 114
"$STILL" restore --pid "$pid" --fixture > still-restore.json 2> still-restore.stderr
unset NX_LUT_VM_PID
# Match the panel's SD root/private UI state. The existing RAM-base binding
# must route synchronization to the actual service without reentering engine.
rm -f /var/run/nx300m-lut-selection /var/run/nx300m-lut-selection-session
printf '2 /perf/looks/current-color/table.d4lut 37\n' > /perf/.state/lut-choice
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$RUNTIME/bin/lut_engine.arm" restore-selection > wrapper-selection-sync.json 2> wrapper-selection-sync.stderr
operation wrapper-selection-status status
request wrapper-selection-render 100
request wrapper-fast-capture 101
request wrapper-fast-resume 100
NX300M_MOD_ROOT=/perf NX_STATE_DIR=/tmp/lut-panel-state "$RUNTIME/bin/lut_engine.arm" stock > wrapper-selection-stock.json 2> wrapper-selection-stock.stderr
# An explicit stock choice in this session must not revive an older saved LUT.
printf '3 /perf/looks/current-mono/table.d4lut 100\n' > /perf/.state/lut-choice
set +e
/bin/sh "$RUNTIME/smartfilter-preview.sh" enable-s2 > wrapper-selection-stock-refused.txt 2> wrapper-selection-stock-enable.stderr
stock_enable_rc=$?
set -e
[ "$stock_enable_rc" != 0 ]
operation wrapper-selection-stock-enable status
/bin/sh "$RUNTIME/smartfilter-preview.sh" restore > wrapper-fast-stop.json 2> wrapper-fast-stop.stderr
operation wrapper-fast-status status
/bin/sh "$RUNTIME/smartfilter-preview.sh" test-monochrome-timing > wrapper-arm.json 2> wrapper-arm.stderr
wait_kdd_arm wrapper-arm.json
check_waiter_has_no_leases
# A physical menu/playback return can take longer than the old three-second
# deadline. Keep the real ARM process idle, then produce its first frame.
/rescue/busybox usleep 4500000
! grep -q '"type":"ipcs_kdd_ready"' "$kdd_job/kdd.json"
check_waiter_has_no_leases
request wrapper-render 104
request wrapper-kdd-syscalls 111
tries=0
while ! grep -q '"type":"ipcs_kdd_window"' /perf/fn-key.log; do
    tries=$((tries+1)); [ "$tries" -lt 150 ]; /rescue/busybox usleep 100000
done
/bin/sh "$RUNTIME/smartfilter-preview.sh" restore > wrapper-stop.json 2> wrapper-stop.stderr
cp /perf/fn-key.log wrapper-persisted.ndjson
# Soft stop must complete before any first frame, cancel the pending generation
# and leave no tracer/uncertainty guard behind.
/bin/sh "$RUNTIME/smartfilter-preview.sh" test-monochrome-timing > wrapper-cancel-arm.json 2> wrapper-cancel-arm.stderr
wait_kdd_arm wrapper-cancel-arm.json
check_waiter_has_no_leases
/bin/sh "$RUNTIME/smartfilter-preview.sh" restore > wrapper-cancel-stop.json 2> wrapper-cancel-stop.stderr
tries=0
while [ ! -s "$kdd_job/kdd.result" ]; do
    tries=$((tries+1)); [ "$tries" -lt 150 ]; /rescue/busybox usleep 100000
done
cp "$kdd_job/kdd.json" wrapper-cancel-wait.json
[ ! -e /tmp/nx300m-smartfilter-preview.uncertain ]
operation wrapper-cancel-status status
grep '^TracerPid:' /proc/$pid/status > wrapper-cancel-tracer.txt
# Replay the physical pre-attachment timeout: unknown guards must stay, while
# the exact expired read-only worker receipt may unblock an ordinary restore.
guard=/tmp/nx300m-smartfilter-preview.uncertain
[ ! -d /proc/9999999 ]
umask 077
printf 'timeout child=9999999 start=1; no process killed; reboot before another preview operation\n' > "$guard"
set +e
/bin/sh "$RUNTIME/smartfilter-preview.sh" restore > wrapper-unknown-guard.json 2> wrapper-unknown-guard.stderr
guard_rc=$?
set -e
[ "$guard_rc" != 0 ] && [ -f "$guard" ]
IFS= read -r fields < "/proc/$pid/stat"
fields=${fields##*) }; set -- $fields; shift 19
camera_start=$1
printf '{"type":"ipcs_kdd_window","measurement_source":"arm_fixture","pid":%s,"tid":%s,"detached":true,"error":4,"opener_errno":0,"steps":0,"stage":"observation_cancelled","start":%s,"timing_perturbed":false,"begin":[0,0],"end":[0,0]}\n' "$pid" "$pid" "$camera_start" > /var/run/nx300m-smartfilter-worker-9999999.log
cp /var/run/nx300m-smartfilter-worker-9999999.log wrapper-legacy-worker-proof.txt
ls -ln "$guard" /var/run/nx300m-smartfilter-worker-9999999.log > wrapper-legacy-proof-modes.txt
/bin/sh "$RUNTIME/smartfilter-preview.sh" restore > wrapper-recovered-guard.json 2> wrapper-recovered-guard.stderr
[ ! -e "$guard" ] && [ -s /tmp/nx300m-kdd-recovered-9999999-1.guard ]
# Reproduce a loader rejection with the real ARM binary. Corrupt only this
# fixture's private source copy; the resident target and frozen build stay intact.
cp "$RUNTIME/ipcs-diagnostic/lib/smartfilter_ram.bin" "$RUNTIME/blob.good"
printf '\000\000\000\000' | /rescue/busybox dd of="$RUNTIME/ipcs-diagnostic/lib/smartfilter_ram.bin" bs=1 count=4 conv=notrunc 2>/dev/null
set +e
/bin/sh "$RUNTIME/smartfilter-preview.sh" test-monochrome-timing > wrapper-failure.json 2> wrapper-failure.stderr
failure_rc=$?
set -e
[ "$failure_rc" != 0 ]
grep -q 'Invalid native module/table' wrapper-failure.stderr
grep -q 'NX_PREVIEW_FAILURE command=test-monochrome-timing rc=3' /perf/fn-key.log
grep -q 'Invalid native module/table' /perf/fn-key.log
mv "$RUNTIME/blob.good" "$RUNTIME/ipcs-diagnostic/lib/smartfilter_ram.bin"
# A stale ABI descriptor must report its actual identity and leave the
# recovery descriptor in place. This mutation/restoration is VM-only.
state_file=/var/run/nx300m-smartfilter-$pid.state
cp "$state_file" "$RUNTIME/state.good"
printf '\004\000\000\000' | /rescue/busybox dd of="$state_file" bs=1 seek=4 count=4 conv=notrunc 2>/dev/null
set +e
/bin/sh "$RUNTIME/smartfilter-preview.sh" test-monochrome-timing > wrapper-abi-failure.json 2> wrapper-abi-failure.stderr
abi_rc=$?
set -e
[ "$abi_rc" != 0 ]
grep -q 'stored_abi=4 expected_abi=7' wrapper-abi-failure.stderr
grep -q 'stored_abi=4 expected_abi=7' /perf/fn-key.log
[ -s "$state_file" ]
cp "$RUNTIME/state.good" "$state_file"
cp /perf/fn-key.log wrapper-failures-persisted.log
unset NX300M_MFW_RAM_BASE NX_STATE_DIR NX_SF_PROBE_FIXTURE NX300M_MOD_ROOT NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID NX_FN_KEY_LOG
operation failure-candidate apply --s2 --session 1 --calltrace 0 --table /mnt/mmc/identity.d4lut --look identity --strength 100
request before-failure 100
request rejected-open-close 106
request fault-passthrough 107
request rejected-native-stop 108
set +e
"$LOADER" restore --pid "$pid" --blob "$BLOB" --fixture --s2 > uncertain-stop.json 2> uncertain-stop.stderr
uncertain=$?
set -e
[ "$uncertain" = 3 ]
operation uncertain-trace open-export
operation uncertain-status status
request final-references 105
'''+ram.PROBE[ram.PROBE.index('cat /proc/$pid/maps > after.maps'):]
PROBE=PROBE.replace('case $cmd in 1|5|7|9)', 'case $cmd in 1|5|7|9|100|104)')
PROBE=PROBE.replace('code=$?; trap - EXIT; set +e',
    'code=$?; trap - EXIT; set +e\n'
    '    mkdir -p kdd-debug\n'
    '    for job in /tmp/nx300m-sf-job.*; do for file in collector.log kdd.json kdd.stderr kdd.result kdd-wait.json kdd-wait.stderr; do\n'
    '        [ ! -f "$job/$file" ] || cp "$job/$file" "kdd-debug/${job##*/}.$file"\n'
    '    done; done\n'
    '    if [ "$code" != 0 ]; then for file in *.stderr; do printf "FILE %s\\n" "$file"; cat "$file"; done; fi')
PROBE=PROBE.replace('echo SMARTFILTER_ARM_BOUNDARY_PASSED',
    '/mnt/mmc/bin/ipcs_session_failure_fixture.arm > session-failures.ndjson 2> session-failures.stderr\n'
    '/mnt/mmc/bin/ipcs_calltrace_fixture.arm > udd-calltrace.ndjson 2> udd-calltrace.stderr\n'
    'echo SMARTFILTER_ARM_BOUNDARY_PASSED')


def call(action,command=''):
    r=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','python3',arm_vm.wsl_path(__file__),
                      '--linux',action,base64.b64encode(command.encode()).decode()],
                     capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=375)
    if r.returncode: raise RuntimeError(r.stderr or r.stdout)
    return json.loads(r.stdout)


def prepare():
    from build_smartfilter_preview import build
    import io
    from elftools.elf.elffile import ELFFile
    report=build(session=True)
    boundary.prepare(initramfs=False)
    fixture=OUT/'smartfilter_fixture.arm'
    failure=OUT/'ipcs_session_failure_fixture.arm'
    trace=OUT/'ipcs_calltrace_fixture.arm'
    rf=Path(json.loads((ROOT/'project.local.json').read_text(encoding='utf-8-sig'))['rootfs'])
    elf=ELFFile(io.BytesIO((rf/'usr/lib/libudd.so.3.1').read_bytes()))
    rels=elf.get_section_by_name('.rel.plt'); dyn=elf.get_section(rels['sh_link'])
    slots={dyn.get_symbol(r['r_info_sym']).name:r['r_offset'] for r in rels.iter_relocations()}
    (BUILD/'ipcs_trace_fixture_stock.h').write_text(
        f'#define FIXTURE_CLOSE_REG_SLOT {slots["ipcs_close_reg_base"]}U\n'
        f'#define FIXTURE_CLOSE_IRQ_SLOT {slots["ipcs_close_irq"]}U\n',encoding='ascii')
    script=f'''set -eu
tc=/opt/nx300m-toolchain/usr
rf=/opt/nx300m-lab/stock-1.15
export LD_LIBRARY_PATH="$tc/lib/x86_64-linux-gnu"
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O1 -Wall -Wextra -Werror -Wno-unused-function -Wno-unused-const-variable \\
 -march=armv7-a -mfloat-abi=soft -DNX_SFR_SESSION=1 -DNX_LUT_SWITCH_FIXTURE=1 -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 \\
 -Wl,--export-dynamic -Wl,--allow-shlib-undefined -Wl,-rpath-link,"$rf/usr/lib" -Wl,-rpath-link,"$rf/lib" \\
 -I{shlex.quote(arm_vm.wsl_path(BUILD))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/smartfilter_ram_fixture.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 {shlex.quote(arm_vm.wsl_path(BRIDGE))} \\
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o {shlex.quote(arm_vm.wsl_path(fixture))}
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -march=armv7-a -mfloat-abi=soft \\
 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-z,defs \\
 -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/ipcs_session_failure_fixture.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/ipcs_session.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(arm_vm.wsl_path(failure))}
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O2 -Wall -Wextra -Werror -Wno-unused-const-variable -march=armv7-a -mfloat-abi=soft \\
 -DNX_SFR_SESSION=1 -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-pic -no-pie -fno-stack-protector \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-z,defs \\
 -I{shlex.quote(arm_vm.wsl_path(BUILD))} -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/ipcs_calltrace_fixture.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/ipcs_calltrace.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libpthread.so.0" "$rf/lib/libc-2.13.so" "$rf/lib/libdl.so.2" -lgcc -o {shlex.quote(arm_vm.wsl_path(trace))}
'''
    still_fixture=OUT/'lut_engine_switch_fixture.arm'
    script+=f'''
"$tc/bin/arm-linux-gnueabi-gcc" -B"$tc/lib/gcc-cross/arm-linux-gnueabi/14/" \\
 -std=gnu99 -O1 -Wall -Wextra -Werror -Wno-unused-function -march=armv7-a -mfloat-abi=soft \\
 -D_GNU_SOURCE -D_TIME_BITS=32 -D_FILE_OFFSET_BITS=64 -fno-pic -no-pie -fno-stack-protector \\
 -include {shlex.quote(arm_vm.wsl_path(ROOT/'mod/metol/remote/compat.h'))} \\
 -nostdlib -nostartfiles -Wl,-e,_start -Wl,--dynamic-linker=/lib/ld-linux.so.3 -Wl,-rpath-link,"$rf/lib" \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'runtime/lut_engine_switch_fixture.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'mod/metol/remote/compat.c'))} \\
 {shlex.quote(arm_vm.wsl_path(ROOT/'work/reference/metol/test_server/filmlab/src/crt0_arm.S'))} \\
 "$rf/lib/libc-2.13.so" -lgcc -o {shlex.quote(arm_vm.wsl_path(still_fixture))}
'''
    r=subprocess.run(['wsl.exe','-d','Debian','-u','root','-e','sh','-s'],input=script.encode(),capture_output=True)
    (OUT/'session-fixture-build.stderr.log').write_bytes(r.stderr)
    if r.returncode: raise RuntimeError(f'Session fixture build exit {r.returncode}: '+r.stderr.decode(errors='replace'))
    stage=OUT/'source-files'
    for name,source in [('bin/smartfilter_fixture.arm',fixture),('bin/ipcs_session_failure_fixture.arm',failure),('bin/smartfilter_loader.arm',BUILD/'smartfilter_loader.arm'),
                        ('bin/lut_engine_switch_fixture.arm',still_fixture),
                        ('bin/lut_live_loader.arm',ROOT/'outputs/deployment/lut-live-loader/lut_live_loader.arm'),
                        ('lut_ram_blob.bin',ROOT/'outputs/deployment/lut-live-loader/lut_ram_blob.bin'),
                        ('bin/ipcs_calltrace_fixture.arm',trace),
                        ('bin/smartfilter_probe_launcher.arm',BUILD/'smartfilter_probe_launcher.arm'),
                        ('bin/lut_engine.arm',ROOT/'outputs/metol/lut_engine.arm'),
                        ('smartfilter-live-preview.sh',ROOT/'mod/deployment/mfw/smartfilter-live-preview.sh'),
                        ('smartfilter-preview.sh',ROOT/'mod/deployment/mfw/smartfilter-preview.sh'),
                        ('common.sh',ROOT/'mod/deployment/common.sh'),
                        ('panel-actions.sh',ROOT/'mod/deployment/mfw/panel-actions.sh'),
                        ('fn-panel-actions.sh',ROOT/'mod/deployment/mfw/fn-panel-actions.sh'),
                        ('gui_preview_quick_ipcs.NX300M',ROOT/'mod/deployment/mfw/gui_preview_quick_ipcs.NX300M'),
                        ('gui_preview_ipcs_diagnostic.NX300M',ROOT/'mod/deployment/mfw/gui_preview_ipcs_diagnostic.NX300M'),
                        ('gui_mfw.NX300M',ROOT/'mod/deployment/mfw/gui_mfw.NX300M'),
                        ('fn-gui_mfw.NX300M',ROOT/'mod/deployment/mfw/fn-gui_mfw.NX300M'),
                        ('gui_debug.NX300M',ROOT/'mod/deployment/mfw/gui_debug.NX300M'),
                        ('lut-load.sh',ROOT/'mod/deployment/mfw/lut-load.sh'),
                        ('lut-panel.sh',ROOT/'mod/deployment/mfw/lut-panel.sh'),
                        ('lut-cycle.sh',ROOT/'mod/deployment/mfw/lut-cycle.sh'),
                        ('fn-panel.sh',ROOT/'mod/deployment/mfw/fn-panel.sh'),
                        ('smartfilter-probe-collector.sh',ROOT/'mod/deployment/mfw/smartfilter-probe-collector.sh'),
                        ('ipcs-kdd-collector.sh',ROOT/'mod/deployment/mfw/ipcs-kdd-collector.sh'),
                        ('smartfilter_ram.bin',BUILD/'smartfilter_ram.bin')]:
        p=stage/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(source.read_bytes())
    for look in ('nx300-hard-mono','ricoh-positive-exact'):
        for name in ('table.d4lut','table-stock3d.d4lut'):
            target=stage/'looks'/look/name; target.parent.mkdir(parents=True,exist_ok=True)
            source=ROOT/'looks'/look/name
            if source.exists(): target.write_bytes(source.read_bytes())
    candidate=json.loads((OUT/'candidate.json').read_text())
    candidate.update(ram_build=report,session_experiment=True,
                     session_hardware_boundary='UDD reference operations simulated with paired per-TID references; no physical PMU',
                     measured_hardware_performance=False)
    arm_vm.write_json(OUT/'candidate.json',candidate)
    boundary.vm.initramfs()


def verify(guest):
    def rows(name): return [json.loads(v) for v in (guest/(name+'.json')).read_text().splitlines() if v.startswith('{')]
    def session(name): return next(r for r in rows(name) if r.get('type')=='ipcs_session')
    inserted=rows('automatic-insert-status')[0]
    repeated=rows('automatic-repeat-status')[0]
    switched=rows('automatic-switch-status')[0]
    removed=rows('automatic-remove-status')[0]
    full=rows('automatic-full-queue-status')[0]
    retry=rows('automatic-retry-status')[0]
    assert inserted['s2_path']==1 and inserted['refresh_requests']==inserted['refresh_dispatches']==1,inserted
    assert repeated['s2_path']==1 and repeated['refresh_requests']==1,repeated
    assert switched['look_id']=='monochrome' and switched['s2_path']==1 and switched['refresh_requests']==1,switched
    assert removed['s2_path']==0 and removed['refresh_requests']==removed['refresh_dispatches']==2,removed
    assert full['s2_path']==0 and full['refresh_requests']==3 and full['refresh_error']!=0,full
    assert retry['s2_path']==1 and retry['refresh_requests']==4 and retry['refresh_dispatches']==3 and retry['refresh_error']==0,retry
    baseline=(guest/'baseline.yuv').read_bytes()
    assert baseline==(guest/'candidate.yuv').read_bytes()==(guest/'return-baseline.yuv').read_bytes()
    for label in ['baseline-trace','returned-trace','stopped-trace']:
        r=session(label); assert r['held']==r['active']==0 and r['error']==0,r
    assert session('candidate-trace')['opens']>0
    fault=session('uncertain-trace')
    assert fault['state']==4 and fault['held']==2 and fault['error']==-78,fault
    status=rows('uncertain-status')[0]; assert status['control_paused']==1,status
    refs=[json.loads(v) for v in (guest/'smartfilter-ram.stdout').read_text().splitlines() if v.startswith('{"type":"fixture_refs"')][-1]
    assert refs['held']==0 and refs['opens']==refs['closes'] and refs['device_opens']==refs['device_closes'],refs
    assert (guest/'mfw-library.sha256').read_text().split()[0]==firmware_profile('mfw')['libmisc_sha256']
    failures=[json.loads(v) for v in (guest/'session-failures.ndjson').read_text().splitlines()]
    assert len(failures)==7 and all(r['passed'] is True for r in failures),failures
    from analyze_ipcs_calls import analyze
    udd=analyze([json.loads(v) for v in (guest/'udd-calltrace.ndjson').read_text().splitlines() if v.startswith('{')])
    assert not udd['excluded'] and [r['udd_branch'] for r in udd['calls']]==[
        'first_process_reference','existing_process_reference','first_process_reference'],udd
    arm_vm.write_json(OUT/'udd-calltrace-report.json',udd)
    fast_config=next(json.loads(v) for v in (guest/'smartfilter-ram.stdout').read_text().splitlines()
                     if v.startswith('{"type":"fixture_fast_preview"'))
    assert fast_config['session']==1 and fast_config['timing']==fast_config['calltrace']==0,fast_config
    initial=next(r for r in rows('wrapper-fast-arm') if 'look_id' in r)
    assert initial['look_id']=='current-mono' and initial['strength']==100,initial
    assert (guest/'wrapper-fast-backend.txt').read_text()=='smartfilter\n'
    assert rows('first-preview-still-ready')[0]['ready'] is True
    assert (guest/'first-preview.still.bin').read_bytes()==(OUT/'source-files/monochrome.d4lut').read_bytes()
    assert (guest/'wrapper-fast-render.yuv').read_bytes()==(guest/'wrapper-render.yuv').read_bytes()
    synced=rows('wrapper-selection-status')[0]
    assert synced['look_id']=='current-color' and synced['strength']==37,synced
    assert synced['requested_generation']>initial['requested_generation'],synced
    assert (guest/'wrapper-selection-render.yuv').read_bytes()!=(guest/'wrapper-fast-render.yuv').read_bytes()
    for label in ('wrapper-selection-stock','wrapper-selection-stock-enable'):
        stock=rows(label)[0]
        assert stock['look_id']=='stock' and stock['strength']==0,stock
    assert 'PREVIEW-WARNING' not in (guest/'wrapper-selection-sync.stderr').read_text()
    assert 'PREVIEW-WARNING' not in (guest/'wrapper-selection-stock.stderr').read_text()
    assert rows('still-engine-ready')[0]['ready'] is True
    assert len({(guest/f'switch-{i}.saved-inode').read_text() for i in (1,2,3)})==1
    for i,look in enumerate(('nx300-hard-mono','ricoh-positive-exact','nx300-hard-mono'),1):
        tag=f'switch-{i}'
        table=OUT/'source-files/looks'/look/'table-stock3d.d4lut'
        expected=(table if table.exists() else table.with_name('table.d4lut')).read_bytes()
        assert (guest/(tag+'.still.bin')).read_bytes()==expected,tag
        assert (guest/(tag+'.after-preview.bin')).read_bytes()==expected,tag
        assert (guest/(tag+'.after-capture.bin')).read_bytes()==expected,tag
        assert (guest/(tag+'.stream-capture.bin')).read_bytes()==expected,tag
        selected=(guest/(tag+'.selection')).read_text().split()
        assert selected[1]==f'/perf/looks/{look}/table.d4lut' and selected[2]=='100',selected
        status=rows(tag+'.status')[0]
        assert status['look_id']==look and status['applied_generation']==status['requested_generation'],status
        assert 'PREVIEW-WARNING' not in (guest/(tag+'.apply.stderr')).read_text()
    assert (guest/'switch-1.preview.yuv').read_bytes()==(guest/'switch-3.preview.yuv').read_bytes()
    assert (guest/'switch-1.preview.yuv').read_bytes()!=(guest/'switch-2.preview.yuv').read_bytes()
    assert (guest/'switch-stock.preview.yuv').read_bytes()==(OUT/'source-files/input.yuv').read_bytes()
    from stock_lut import factory_3d_lut
    import struct
    expected_stock=struct.pack('<2190H',*factory_3d_lut())
    assert (guest/'switch-stock.still.bin').read_bytes()==expected_stock
    assert 'LUT_DISABLED' in (guest/'switch-off.json').read_text()
    assert (guest/'switch-off.saved').read_text()=='0\n'
    off_native=rows('switch-off-native')[0]
    assert off_native['rw_slots_original'] and not off_native['ram_hooks_installed'],off_native
    before_off,after_off=rows('switch-off-before')[0],rows('switch-off-after')[0]
    assert not after_off['enabled'] and after_off['rendered']==before_off['rendered'],after_off
    off_session=session('switch-off-trace')
    assert off_session['mode']==off_session['held']==off_session['active']==0,off_session
    assert (guest/'switch-off-preview.yuv').read_bytes()==(OUT/'source-files/input.yuv').read_bytes()
    assert (guest/'switch-off.still.bin').read_bytes()==expected_stock
    streams=[json.loads(v) for v in (guest/'smartfilter-ram.stdout').read_text().splitlines()
             if v.startswith('{"type":"fixture_stream"')]
    assert len(streams)==4 and all(r['frames']>1 and r['processed']>0 and r['control_paused']==0 for r in streams),streams
    assert [r['captures'] for r in streams]==[1,1,1,0],streams
    assert rows('switch-reenabled-native')[0]['ready'] is True
    assert (guest/'switch-reenable.still.bin').read_bytes()==(OUT/'source-files/looks/ricoh-positive-exact/table-stock3d.d4lut').read_bytes()
    assert 'PREVIEW-WARNING' not in (guest/'switch-reenable.stderr').read_text()
    reenabled=rows('menu-reenable-status')[0]
    assert reenabled['look_id']=='ricoh-positive-exact' and reenabled['applied_generation']==reenabled['requested_generation'],reenabled
    assert 'PREVIEW-WARNING' in (guest/'partial-switch.stderr').read_text()
    assert (guest/'partial-switch.selection').read_text().split()[1]=='/perf/looks/nx300-hard-mono/table.d4lut'
    retried=rows('partial-retry-status')[0]
    assert retried['look_id']=='nx300-hard-mono' and retried['applied_generation']==retried['requested_generation'],retried
    cancelled_control=rows('cancelled-control-status')[0]
    assert cancelled_control['control_paused']==0 and cancelled_control['look_id']=='nx300-hard-mono',cancelled_control
    cancelled_log=(guest/'cancelled-control.stderr').read_text()
    assert 'preview admission restored=true' in cancelled_log,cancelled_log
    assert cancelled_log.count('Control admission: full_hash ')==1,cancelled_log
    assert 'Control admission: reused_authenticated_files ' in cancelled_log,cancelled_log
    assert (guest/'cancelled-control-tracer.txt').read_text().split()[-1]=='0'
    fast=session('wrapper-fast-trace')
    assert fast['mode']==1 and fast['trace_next']==0 and fast['error']==0,fast
    assert not any(r.get('type')=='ipcs_open_call' for r in rows('wrapper-fast-trace'))
    fast_stopped=session('wrapper-fast-stop')
    assert fast_stopped['mode']==fast_stopped['held']==fast_stopped['active']==0,fast_stopped
    fast_status=rows('wrapper-fast-status')[0]
    assert not fast_status['enabled'] and fast_status['control_paused']==fast_status['diag_frames']==0,fast_status
    saved=[json.loads(v) for v in (guest/'wrapper-persisted.ndjson').read_text().splitlines() if v.startswith('{')]
    # The normal-mode stop exported its own snapshot before the diagnostic run.
    # Analyze the last complete export, rather than merging separate windows.
    open_rows=[r for r in saved if r.get('type','').startswith('ipcs_open') or r.get('type')=='ipcs_session']
    last_window=max(i for i,r in enumerate(open_rows) if r.get('type')=='ipcs_open_window')
    diagnostic_rows=open_rows[last_window:]
    saved_session=next(r for r in diagnostic_rows if r.get('type')=='ipcs_session')
    assert saved_session['mode']==saved_session['held']==saved_session['active']==0,saved_session
    assert any(r.get('type')=='ipcs_open_call' and r['committed'] for r in saved),saved
    assert any(r.get('command')=='open-export' and r.get('persisted') is True for r in rows('wrapper-stop'))
    wrapper_report=analyze(diagnostic_rows)
    assert wrapper_report['calls'],wrapper_report
    kdd=next(r for r in saved if r.get('type')=='ipcs_kdd_window')
    assert kdd['measurement_source']=='arm_fixture' and kdd['detached'] and kdd['error']==0,kdd
    kdd_calls=[r for r in saved if r.get('type')=='ipcs_kdd_call']
    assert len(kdd_calls)==6 and all(r['completed'] and r['result']>=0 for r in kdd_calls),kdd_calls
    assert [r['syscall'] for r in kdd_calls]==[5,6,5,6,5,6],kdd_calls
    assert kdd['timing_perturbed'] and not kdd['kernel_branch_probed'],kdd
    assert kdd['identity_reused'] and kdd['stage']=='complete',kdd
    ready=next(r for r in saved if r.get('type')=='ipcs_kdd_ready')
    assert ready['status']=='ready' and ready['error']==0 and ready['wait_ms']>=4500,ready
    assert not ready['timing_perturbed'],ready
    cancelled=next(r for r in rows('wrapper-cancel-wait') if r.get('type')=='ipcs_kdd_ready')
    assert cancelled['status']=='not_ready' and cancelled['stage']=='selection_changed',cancelled
    assert cancelled['error']==125 and not cancelled['timing_perturbed'],cancelled
    stopped=rows('wrapper-cancel-status')[0]
    assert not stopped['enabled'] and stopped['control_paused']==0,stopped
    assert (guest/'wrapper-cancel-tracer.txt').read_text().split()[-1]=='0'
    assert any(r.get('command')=='recover-kdd-guard' and r['recovered'] is True
               for r in rows('wrapper-recovered-guard'))
    assert 'unresolved_operation' in (guest/'wrapper-unknown-guard.json').read_text()
    arm_vm.write_json(OUT/'wrapper-calltrace-report.json',wrapper_report)
    failed=(guest/'wrapper-failures-persisted.log').read_text()
    assert 'NX_PREVIEW_FAILURE command=test-monochrome-timing rc=3' in failed
    assert 'Invalid native module/table' in failed
    assert 'stored_abi=4 expected_abi=7' in failed
    assert 'Invalid native module/table' in (guest/'wrapper-failure.stderr').read_text()
    assert 'stored_abi=4 expected_abi=7' in (guest/'wrapper-abi-failure.stderr').read_text()
    for name,owner in [('binding-libc.stdout','libc'),('smartfilter-ram.stdout','libpthread')]:
        bindings=[json.loads(v) for v in (guest/name).read_text().splitlines()
                  if v.startswith('{"type":"fixture_open_binding"')]
        assert len(bindings)==2 and [r['enabled'] for r in bindings]==[1,0],bindings
        assert all(r['owner']==owner and r['original']==r['forwarded_original']
                   and r['native_open_succeeded'] for r in bindings),bindings
    memory=rows('automatic-memory-status')[0]
    assert memory['s2_path']==1 and memory['refresh_pending']==0 and memory['refresh_state']==7,memory
    assert memory['refresh_resumes']>0 and memory['refresh_wakeups']>0,memory
    assert '"native_matcher_and_capture_defer":true' in (guest/'binding-libc.stdout').read_text()
    assert (guest/'preview-only.still.bin').read_bytes()==(guest/'switch-3.still.bin').read_bytes()
    assert (guest/'preview-only-color.still.bin').read_bytes()==(OUT/'source-files/looks/ricoh-positive-exact/table-stock3d.d4lut').read_bytes()
    stopped=rows('preview-only-stopped')[0]; after_switch=rows('preview-only-after-switch')[0]
    assert not stopped['enabled'] and not after_switch['enabled'] and stopped['requested_generation']==after_switch['requested_generation']
    assert '取景预览已关闭；照片 LUT 保留' in (guest/'preview-only-stop.json').read_text()
    return dict(passed=True,abi=7,actual_ram_loader=True,actual_got_hooks=20,mfw_userspace_verified=True,
                asynchronous_stop_callback_wakes_controller=True,
                native_stop_callback_and_memory_matching_executed=True,
                unchanged_map_resumes_without_capture=True,
                unmatched_memory_waits_without_forcing_rebuild=True,
                b2y_open_prepare_window_defers_memory_resume=True,
                preview_stop_keeps_still_selection_and_hooks=True,
                look_switch_while_preview_stopped_does_not_enable_preview=True,
                automatic_topology_insert_without_gallery_or_capture=True,
                automatic_topology_remove_on_soft_stop=True,look_switch_does_not_rebuild_topology=True,
                refresh_runs_on_controller_after_native_message_dispatch=True,capture_defers_topology_refresh=True,
                full_controller_queue_does_not_block_video_callback=True,
                baseline_candidate_baseline_byte_identical=True,owner_thread_reference_pairing=True,
                per_frame_open_close_retained=True,no_cross_frame_channel_locks=True,
                b2y_open_drains_before_lifecycle_lock=True,capture_and_resume=True,
                idle_release=True,channel_contention_nonblocking=True,soft_stop_reference_released=True,
                references=refs,physical_camera_run=False,kdd_first_power_verified=False,
                failure_and_drain_cases=failures,
                actual_stock_udd_branch_forwarding_verified=True,
                rejected_open_close_does_not_overpost_lifecycle=True,
                stop_start_adapters_drain_before_native_teardown=True,
                uncertain_drain_does_not_enter_native_stop=True,
                uncertain_close_stops_admission_without_claiming_release=True,
                button_wrapper_arms_real_session_calltrace=True,
                softstop_persists_full_open_window=True,
                diagnostic_session_reference_disabled=True,
                default_preview_enables_session_with_diagnostics_off=True,
                normal_wrapper_stop_releases_session=True,
                current_preview_installs_still_before_first_frame=True,
                active_stream_switch_capture_and_off_checked=True,
                continuous_stream_samples=streams,
                failed_preview_sync_is_reported_without_reverting_choice=True,
                control_cancel_reopens_admission_and_detaches=True,
                control_drain_reuses_authenticated_files_with_live_rechecks=True,
                off_preview_enable_requires_explicit_look=True,
                actual_fn_menu_action_uses_continuous_backend_capability=True,
                reduced_and_full_menu_route_current_lut=True,
                probe_only_menu_rejects_continuous_actions=True,
                off_restores_native_still_slots_and_stops_preview_processing=True,
                look_after_off_lazily_reinstalls_still_adapter=True,
                panel_data_root_syncs_to_ram_service=True,
                explicit_stock_does_not_revive_saved_lut=True,
                matching_still_engine_ready_in_session_process=True,
                mono_color_mono_selection_preview_still_synchronized=True,
                still_lut_restored_after_preview_and_capture=True,
                saved_choice_updated_without_directory_rename=True,
                menu_does_not_submit_automatic_preview_restore=True,
                native_rejection_stderr_visible_and_persisted=True,
                stale_abi_rejection_visible_without_deleting_state=True,
                libc_and_pthread_open_binding_lifecycle_verified=True,
                read_only_kdd_syscall_observer_detaches_and_persists=True,
                delayed_first_frame_observed_without_control_or_media_leases=True,
                first_frame_wait_cancelled_by_softstop_without_tracer=True,
                kdd_reuses_authenticated_target_without_second_full_hash=True,
                cancelled_readonly_guard_recovered_and_unknown_guard_retained=True,
                lcd_fps_measured=False,hardware_performance_measured=False)


if __name__=='__main__':
    boundary.OUT=OUT; boundary.SOCKET='/tmp/nx300m-ipcs-session-private'
    boundary.MARKER='IPCS_SESSION_PRIVATE_VM_READY'; boundary.PROBE=PROBE
    boundary.prepare_original=boundary.prepare
    # prepare needs the stock setup helper, while main dispatches the candidate.
    stock_prepare=boundary.prepare
    def candidate_prepare():
        boundary.prepare=stock_prepare
        try: prepare()
        finally: boundary.prepare=candidate_prepare
    boundary.prepare=candidate_prepare
    boundary.verify=verify; boundary.call=call
    boundary.main()
