#!/bin/sh
# Explicit continuous S2 controls; the probe-only wrapper remains separate.
set -eu
set -f
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'SmartFilter preview: %s\n' "$*" >&2; exit 1; }
command=${1:-status}
[ "$#" -le 1 ] || fail 'unexpected arguments'
case $command in
    capabilities) printf 'continuous-s2\n'; exit 0 ;;
    probe-once|test-identity) command=probe-once ;;
    enable-s2|enable-s2-stock|test-monochrome|test-monochrome-timing|sync|stock|status|stop|restore|diag-export) ;;
    *) fail 'use enable-s2, sync, stock, status, stop, restore, diag-export or probe-once' ;;
esac
[ "$(id -u)" = 0 ] || fail 'root required'
[ "$(uname -m)" = armv7l ] || fail 'native ARMv7 required'
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH ENV BASH_ENV
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
case $HERE in *[!A-Za-z0-9_./-]*) fail 'unsupported runtime path' ;; esac
open_diagnostic=0
if [ -e "$HERE/IPCS_OPEN_DIAGNOSTIC" ]; then
    [ -f "$HERE/IPCS_OPEN_DIAGNOSTIC" ] && [ ! -L "$HERE/IPCS_OPEN_DIAGNOSTIC" ] || fail 'invalid IPCS diagnostic marker'
    [ "$(cat "$HERE/IPCS_OPEN_DIAGNOSTIC")" = 1 ] || fail 'invalid IPCS diagnostic version'
    open_diagnostic=1
fi
session_preview=0
if [ -e "$HERE/IPCS_SESSION_PREVIEW" ]; then
    [ "$open_diagnostic" = 1 ] && [ -f "$HERE/IPCS_SESSION_PREVIEW" ] &&
        [ ! -L "$HERE/IPCS_SESSION_PREVIEW" ] && [ "$(cat "$HERE/IPCS_SESSION_PREVIEW")" = 1 ] || fail 'invalid Session preview marker'
    session_preview=1
fi
source_root=${NX300M_MOD_SOURCE_ROOT:-/mnt/mmc/nx300m-mod}
source_id=${NX300M_MOD_SOURCE_ID:-}
state=${NX_STATE_DIR:-/tmp/nx300m-mfw/state}
fixture=${NX_SF_PROBE_FIXTURE:-0}
case $fixture:$source_root in
    0:/mnt/mmc/nx300m-mod|1:/perf) ;;
    *) fail 'fixture requires explicit NX_SF_PROBE_FIXTURE=1 and source=/perf; production rejects fixture mode' ;;
esac
case $source_id in ''|*[!0-9:]*|:*|*:|*:*:*) fail 'verified source identity is required from RAM staging' ;; esac
case $state in /*) ;; *) fail 'state directory must be absolute' ;; esac
[ ! -L "$state" ] && [ -d "$state" ] || fail 'RAM state directory unavailable'
case $(stat -f -c '%T' "$HERE") in tmpfs|ramfs) ;; *) fail 'run the verified RAM-staged preview wrapper, not the SD copy' ;; esac
case $(stat -f -c '%T' "$state") in tmpfs|ramfs) ;; *) fail 'state directory must be RAM backed' ;; esac
guard=/tmp/nx300m-smartfilter-preview.uncertain
backend=/var/run/nx300m-preview-backend
if [ -e "$guard" ] || [ -L "$guard" ]; then
    case $open_diagnostic:$command in 1:restore|1:stop) ;; *) fail '上次操作未解决；保留 RAM 现场，禁止重武装' ;; esac
fi
backend_value() {
    [ ! -L "$backend" ] || fail 'backend marker is a symbolic link'
    value=
    if [ -e "$backend" ]; then
        [ -f "$backend" ] && [ "$(stat -c '%u' "$backend")" = "$(id -u)" ] || fail 'invalid backend marker'
        IFS= read -r value < "$backend" || fail 'invalid backend marker'
    fi
}
backend_value
if [ "$command" = sync ]; then
    [ "$value" = smartfilter ] || fail 'continuous S2 is not enabled; sync does not enable it'
fi
pids=
set +f
for entry in /proc/[0-9]*/exe; do
    target=$(readlink "$entry" 2>/dev/null) || continue
    matched=0
    if [ "$fixture" = 1 ]; then
        case $target in /mnt/mmc/bin/smartfilter_fixture.arm|/perf/smartfilter_fixture.arm) matched=1 ;; esac
    else
        [ "$target" != /usr/bin/di-camera-app-nx300 ] || matched=1
    fi
    if [ "$matched" = 1 ]; then value=${entry#/proc/}; pids="$pids ${value%/exe}"; fi
done
set -f
set -- $pids
[ "$#" -eq 1 ] || fail 'expected one original camera process'
camera_pid=$1
IFS= read -r camera_fields < "/proc/$camera_pid/stat" || fail 'camera identity unavailable'
camera_fields=${camera_fields##*) }
set -- $camera_fields
[ "$#" -ge 20 ] && [ "$1" != Z ] || fail 'camera process is not live'
shift 19
camera_start=$1
job=$(mktemp -d /tmp/nx300m-sf-job.XXXXXX) || fail 'cannot allocate private RAM job'
case $(stat -f -c '%T' "$job") in tmpfs|ramfs) ;; *) rmdir "$job"; fail 'job must be RAM backed' ;; esac
for pair in bin/smartfilter_probe_launcher.arm:launcher bin/smartfilter_loader.arm:loader.arm lib/smartfilter_ram.bin:blob.bin smartfilter-probe-collector.sh:collector.sh; do
    resource=$HERE/${pair%:*}; destination=$job/${pair#*:}
    if [ "$open_diagnostic" = 1 ]; then
        case ${pair%:*} in bin/*|lib/*) resource=$HERE/ipcs-diagnostic/${pair%:*} ;; esac
    fi
    [ -f "$resource" ] && [ ! -L "$resource" ] || fail "missing resource: $resource (RAM retained: $job)"
    cp "$resource" "$destination" && cmp -s "$resource" "$destination" || fail "private RAM copy differs ($job)"
done
chmod 500 "$job/launcher" "$job/loader.arm" "$job/collector.sh"
chmod 400 "$job/blob.bin"
if [ "$command" = probe-once ]; then
    nonce=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    [ "${#nonce}" -eq 32 ] || fail 'cannot obtain probe nonce'
    "$job/launcher" --launch "$job" "$nonce" "$source_root" "$source_id" "$state" "$camera_pid" "$camera_start" || fail "collector not acknowledged; no retry; RAM=$job"
    printf '单帧探针已受理，不表示处理或存卡成功；RAM=%s/result\n' "$job"
    exit 0
fi
# Continuous controls read selection/table only while holding their own lease.
# Caller-owned leases are not assumed, and no engine selection lock is taken.
[ ! -L "$state/media-io.lock" ] || fail 'media lock is a symbolic link'
exec 6>"$state/media-io.lock"
flock -s -n 6 || fail 'media access is paused or draining'
[ ! -e "$state/media-paused" ] || fail 'media access is paused'
[ ! -L "$source_root" ] && [ -d "$source_root" ] &&
    [ "$(stat -c '%d:%i' "$source_root")" = "$source_id" ] || fail 'source identity changed'
# Before the preview lock: the engine owns the selection lock, and its normal
# sync callback acquires the preview lock in that order. Do not recurse here.
if [ "$command" = enable-s2 ]; then
    engine=$HERE/bin/lut_engine.arm
    [ -f "$engine" ] && [ ! -L "$engine" ] && [ -x "$engine" ] || fail 'LUT selection backend unavailable'
    NX300M_LUT_SKIP_PREVIEW_SYNC=1 "$engine" activate || fail 'selected photo LUT could not be activated'
fi
[ ! -L /tmp/nx300m-smartfilter-preview.lock ] || fail 'preview lock is a symbolic link'
exec 9>/tmp/nx300m-smartfilter-preview.lock
flock -n 9 || fail 'another preview operation is running'
if [ -e "$guard" ] || [ -L "$guard" ]; then
    case $open_diagnostic:$command in 1:restore|1:stop) ;; *) fail 'previous operation is unresolved' ;; esac
fi
backend_value
if [ "$command" = sync ]; then
    [ "$value" = smartfilter ] || fail 'continuous S2 was stopped before sync'
fi
operation=$command
[ "$command" != stop ] || operation=restore
[ "$open_diagnostic:$command" != 1:diag-export ] || operation=open-export
table=
look=
strength=100
case $command in
    enable-s2|test-monochrome|test-monochrome-timing|enable-s2-stock|stock|sync)
        [ ! -e "/var/run/nx300m-ipcm-preview-$camera_pid.state" ] || fail 'restore IPCM and restart camera before changing backend'
        [ -z "$value" ] || [ "$value" = smartfilter ] || fail 'another preview backend is selected'
        operation=stock ;;
esac
case $command in
    test-monochrome|test-monochrome-timing)
        table=$HERE/preview-tests/monochrome.d4lut
        look=monochrome
        operation=apply ;;
    enable-s2|sync)
        selection=/var/run/nx300m-lut-selection
        [ ! -L "$selection" ] || fail 'selection is a symbolic link'
        if [ -e "$selection" ]; then
            [ -f "$selection" ] && [ "$(stat -c '%u' "$selection")" = "$(id -u)" ] || fail 'invalid current selection'
            read -r generation selected strength extra < "$selection" || fail 'invalid current selection'
            [ -z "${extra:-}" ] || fail 'unexpected selection fields'
            case $generation in ''|*[!0-9]*) fail 'invalid generation' ;; esac
            case $selected in "$source_root"/looks/*/table.d4lut) ;; *) fail 'selection belongs to another source' ;; esac
            look=${selected#"$source_root"/looks/}; look=${look%/table.d4lut}
            case $look in ''|*[!a-z0-9_-]*) fail 'invalid look ID' ;; esac
            [ "${#look}" -le 63 ] || fail 'look ID too long'
            case $strength in ''|*[!0-9]*) fail 'strength must be 0..100' ;; esac
            [ "${#strength}" -le 3 ] && [ "$strength" -le 100 ] || fail 'strength must be 0..100'
            [ ! -L "$source_root/looks" ] && [ ! -L "$source_root/looks/$look" ] || fail 'look directory is a symbolic link'
            table=$selected
            operation=apply
        fi ;;
esac
if [ -n "$table" ]; then
    [ -f "$table" ] && [ ! -L "$table" ] || fail 'compiled LUT table is missing or linked; preview was not updated'
    before=$(stat -c '%d:%i:%s:%Y' "$table") || fail 'cannot inspect LUT table'
    [ "$(stat -c '%s' "$table")" = 4380 ] || fail 'invalid LUT table size'
    cp "$table" "$job/table.d4lut" && cmp -s "$table" "$job/table.d4lut" || fail 'RAM table copy differs'
    [ "$(stat -c '%d:%i:%s:%Y' "$table")" = "$before" ] &&
        [ "$(stat -c '%d:%i' "$source_root")" = "$source_id" ] || fail 'source changed while copying LUT'
    chmod 400 "$job/table.d4lut"
fi
set -- "$operation" --pid "$camera_pid" --blob "$job/blob.bin"
case $command in
    enable-s2|test-monochrome|enable-s2-stock|stock|sync) set -- "$@" --s2 --timing 0 ;;
    test-monochrome-timing) set -- "$@" --s2 --timing 1 ;;
    restore) set -- "$@" --keep-timing ;;
esac
if [ "$open_diagnostic" = 1 ]; then
    case $command in
        test-monochrome-timing) set -- "$@" --session 0 --calltrace 1 ;;
        enable-s2|test-monochrome|enable-s2-stock|stock|sync) set -- "$@" --session "$session_preview" --calltrace 0 ;;
    esac
fi
[ -z "$table" ] || set -- "$@" --table "$job/table.d4lut" --look "$look" --strength "$strength"
[ "$fixture" != 1 ] || set -- "$@" --fixture
expected_diag_log=$source_root/logs/fn-key.log
# The isolated fixture mounts its log directory directly at /perf.
[ "$fixture" != 1 ] || expected_diag_log=/perf/fn-key.log
if [ "$command" = diag-export ] && [ -n "${NX_FN_KEY_LOG:-}" ]; then
    [ "$NX_FN_KEY_LOG" = "$expected_diag_log" ] || fail 'diagnostic log belongs to another source'
    set -- "$@" --diag-log "$NX_FN_KEY_LOG"
elif [ "$command" = restore ] && [ -n "${NX_FN_KEY_LOG:-}" ]; then
    if [ "$NX_FN_KEY_LOG" = "$expected_diag_log" ]; then
        set -- "$@" --diag-log "$NX_FN_KEY_LOG"
    else
        printf '诊断日志目标不属于当前来源；继续软停，诊断仅保留 RAM。\n' >&2
    fi
fi
rc=0
"$job/launcher" --run 30 "$job/control.json" "$job/control.stderr" "$guard" "$job/loader.arm" "$@" || rc=$?
[ ! -f "$job/control.json" ] || while IFS= read -r line; do printf '%s\n' "$line"; done < "$job/control.json"
if [ "$rc" -ne 0 ]; then
    # The loader's stderr contains the actual rejection. Preserve it before
    # the user powers down; the menu output is bounded and RAM is transient.
    {
        printf 'NX_PREVIEW_FAILURE command=%s rc=%s pid=%s start=%s ram=%s\n' "$command" "$rc" "$camera_pid" "$camera_start" "$job"
        [ ! -f "$job/control.stderr" ] || cat "$job/control.stderr"
        [ ! -f "$job/control.json" ] || cat "$job/control.json"
        printf 'NX_PREVIEW_FAILURE_END\n'
    } > "$job/failure.log"
    cat "$job/failure.log" >&2
    if [ "${NX_FN_KEY_LOG:-}" = "$expected_diag_log" ] && [ ! -L "$expected_diag_log" ]; then
        if cat "$job/failure.log" >> "$expected_diag_log"; then
            printf '完整失败原因已保存到相机卡 logs/fn-key.log。\n' >&2
        else
            printf '失败原因仅保留 RAM=%s/failure.log。\n' "$job" >&2
        fi
    fi
    fail "操作失败或超时 rc=$rc；未自动重试，RAM=$job"
fi
# Include the native admission/stop-window timings on successful controls too.
# They were previously stranded in a private RAM job, hiding the cause of a
# slow switch after power-off. This is control-event output, never frame I/O.
if [ -f "$job/control.stderr" ]; then
    while IFS= read -r line; do
        case $line in 'Control admission:'*|'Target stop window:'*) printf '%s\n' "$line" >&2 ;; esac
    done < "$job/control.stderr"
fi
if [ "$open_diagnostic:$command" = 1:restore ]; then
    # Stop is already committed. A failed export must not undo it or prevent
    # the menu from reopening; retain both outputs in the private RAM job.
    set -- open-export --pid "$camera_pid" --blob "$job/blob.bin"
    [ "$fixture" != 1 ] || set -- "$@" --fixture
    if [ "${NX_FN_KEY_LOG:-}" = "$expected_diag_log" ]; then set -- "$@" --diag-log "$NX_FN_KEY_LOG"; fi
    export_rc=0
    "$job/launcher" --run 30 "$job/open.json" "$job/open.stderr" "$guard" "$job/loader.arm" "$@" || export_rc=$?
    [ ! -f "$job/open.json" ] || cat "$job/open.json"
    if [ "$export_rc" != 0 ] && [ -f "$job/open.stderr" ]; then
        cat "$job/open.stderr" >&2
        if [ "${NX_FN_KEY_LOG:-}" = "$expected_diag_log" ] && [ ! -L "$expected_diag_log" ]; then
            cat "$job/open.stderr" >> "$expected_diag_log" 2>/dev/null || :
        fi
    fi
    if [ "$export_rc" != 0 ]; then printf 'IPCS Open 诊断未能保存 rc=%s；预览已软停，RAM=%s。\n' "$export_rc" "$job"; fi
fi
case $command in
    enable-s2|test-monochrome|test-monochrome-timing)
        marker=$(mktemp /var/run/nx300m-preview-backend.XXXXXX) || fail 'cannot publish backend'
        printf 'smartfilter\n' > "$marker"
        mv -f "$marker" "$backend"
        printf '连续 S2 LUT 请求已提交；请返回取景确认画面，不代表已处理帧。\n' ;;
    restore)
        [ "$value" != smartfilter ] || rm -f "$backend"
        printf 'LUT 预览已软停。\n'
        if grep -q '"command":"restore-timing".*"persisted":true' "$job/control.json"; then
            printf '本次采样已保存到相机卡 logs/fn-key.log。\n'
        elif grep -q '"command":"restore-timing".*"snapshot_created":true' "$job/control.json"; then
            printf '本次采样仅保留 RAM 快照，尚未写入相机卡。\n'
        elif grep -q '"command":"restore-timing".*"status":"no_data"' "$job/control.json"; then
            printf '本次没有可保存的计时样本。\n'
        else
            printf '本次采样未能保存；预览保持关闭，错误详情在 RAM=%s/control.json。\n' "$job"
        fi ;;
    stop)
        [ "$value" != smartfilter ] || rm -f "$backend"
        printf '取景预览已关闭；照片 LUT 保留。\n' ;;
    stock|enable-s2-stock) printf 'S2 原样请求已提交；没有应用非零 LUT。\n' ;;
esac
case $command in
    enable-s2) preference=1 ;;
    stop|restore) preference=0 ;;
    *) preference= ;;
esac
if [ -n "$preference" ]; then
    "$HERE/bin/lut_engine.arm" save-preview "$preference" ||
        printf 'PERSIST-WARNING 预览已切换，但开机偏好未保存。\n' >&2
fi
if [ "$open_diagnostic:$command" = 1:test-monochrome-timing ]; then
    # Publish the backend before transferring the wrapper's preview lease.
    # The observer waits for a committed call after the menu returns.
    observer=$HERE/ipcs-kdd-collector.sh
    if [ -f "$observer" ] && [ ! -L "$observer" ]; then
        cp "$observer" "$job/collector.next" && cmp -s "$observer" "$job/collector.next" &&
            chmod 500 "$job/collector.next" && mv -f "$job/collector.next" "$job/collector.sh" || fail 'KDD collector copy differs'
        nonce=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
        token=$(sed -n 's/^.*"token":"\([0-9:]*\)".*$/\1/p' "$job/control.json")
        [ -n "$token" ] || fail 'KDD observation requires the committed preview identity'
        printf '%s\n' "$token" > "$job/kdd.expect"
        flock -u 9
        if [ "${#nonce}" = 32 ]; then
            "$job/launcher" --launch "$job" "$nonce" "$source_root" "$source_id" "$state" "$camera_pid" "$camera_start" ||
                printf 'KDD 短采样未启动；原 Open 诊断仍然有效。\n' >&2
        fi
    fi
fi
