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
    enable-s2|enable-s2-stock|test-monochrome|test-monochrome-timing|sync|stock|status|stop|restore|diag-export|pause) ;;
    *) fail 'use enable-s2, sync, stock, status, stop, restore, diag-export or probe-once' ;;
esac
uid=$(id -u)
[ "$uid" = 0 ] || fail 'root required'
[ "$(uname -m)" = armv7l ] || fail 'native ARMv7 required'
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH ENV BASH_ENV
# Per-control-event stage timing (identity/job/table/launcher/output) so card
# logs can attribute sync wait. One /proc/uptime read per stage via redirect;
# printf is a builtin, so this never forks.
sf_stage() {
    IFS=' ' read -r sf_uptime sf_rest < /proc/uptime
    printf 'SF_STAGE script=%s stage=%s uptime=%s\n' "${0##*/}" "$1" "$sf_uptime" >&2
}
sf_stage preamble
case $0 in */*) here_dir=${0%/*};; *) here_dir=.;; esac
HERE=$(CDPATH= cd -P "$here_dir" && pwd -P)
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
filesystems=$(stat -f -c '%T' "$HERE" "$state") || fail 'cannot inspect runtime filesystems'
set -- $filesystems
[ "$#" -eq 2 ] || fail 'incomplete runtime filesystems'
for filesystem do
    case $filesystem in tmpfs|ramfs) ;; *) fail 'runtime and state must be RAM backed' ;; esac
done
guard=/tmp/nx300m-smartfilter-preview.uncertain
backend=/var/run/nx300m-preview-backend
if [ -e "$guard" ] || [ -L "$guard" ]; then
    case $open_diagnostic:$command in 1:restore|1:stop) ;; *) fail '上次操作未解决；保留 RAM 现场，禁止重武装' ;; esac
fi
backend_value() {
    [ ! -L "$backend" ] || fail 'backend marker is a symbolic link'
    value=
    if [ -e "$backend" ]; then
        [ -f "$backend" ] && [ "$(stat -c '%u' "$backend")" = "$uid" ] || fail 'invalid backend marker'
        IFS= read -r value < "$backend" || fail 'invalid backend marker'
    fi
}
backend_value
if [ "$command" = sync ]; then
    [ "$value" = smartfilter ] || fail 'continuous S2 is not enabled; sync does not enable it'
fi
identity_launcher=$HERE/bin/smartfilter_probe_launcher.arm
# The read-only scanner is independent of the session ABI. Diagnostic assets
# are staged as 0400 and become executable only in the private job below.
[ -f "$identity_launcher" ] && [ ! -L "$identity_launcher" ] && [ -x "$identity_launcher" ] || fail 'camera identity scanner unavailable'
set -- --camera-identity
[ "$fixture" != 1 ] || set -- "$@" --fixture
camera_identity=$("$identity_launcher" "$@") || fail 'expected one live original camera process'
set -- $camera_identity
[ "$#" -eq 2 ] || fail 'camera identity unavailable'
camera_pid=$1
camera_start=$2
sf_stage identity
job=$(mktemp -d /tmp/nx300m-sf-job.XXXXXX) || fail 'cannot allocate private RAM job'
# The native copy verifier checks this private destination's RAM filesystem
# before it creates any executable; no second stat process is needed here.
copy_tool=$HERE/bin/ram_asset_verify.arm
[ -f "$copy_tool" ] && [ ! -L "$copy_tool" ] && [ -x "$copy_tool" ] || fail 'RAM asset copy verifier unavailable'
set -- --copy "$HERE" "$job"
for pair in bin/smartfilter_probe_launcher.arm:launcher bin/smartfilter_loader.arm:loader.arm lib/smartfilter_ram.bin:blob.bin smartfilter-probe-collector.sh:collector.sh; do
    resource=${pair%:*}; destination=${pair#*:}
    if [ "$open_diagnostic" = 1 ]; then
        case $resource in bin/*|lib/*) resource=ipcs-diagnostic/$resource ;; esac
    fi
    permission=500
    [ "$destination" != blob.bin ] || permission=400
    set -- "$@" "$permission" any "$resource" "$destination"
done
# Staging and /tmp are separate mounts on the camera. One finite native copy
# preserves private job ownership, byte readback and modes across EXDEV,
# without starting cp/cmp/chmod separately for every resource.
"$copy_tool" "$@" || fail "private RAM batch copy differs ($job)"
sf_stage job-ready
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
    case $open_diagnostic:$command in 1:restore|1:stop|1:pause) ;; *) fail 'previous operation is unresolved' ;; esac
fi
backend_value
if [ "$command" = sync ]; then
    [ "$value" = smartfilter ] || fail 'continuous S2 was stopped before sync'
fi
operation=$command
# Both user stop and engine-side pause drain the same native preview session;
# only the preference write differs (handled in the preference case below).
[ "$command" != stop ] || operation=restore
[ "$command" != pause ] || operation=restore
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
            [ -f "$selection" ] && [ "$(stat -c '%u' "$selection")" = "$uid" ] || fail 'invalid current selection'
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
    table_root=$source_root
    case $command in test-monochrome|test-monochrome-timing) table_root=$HERE ;; esac
    "$copy_tool" --copy "$table_root" "$job" 400 4380 "${table#"$table_root"/}" table.d4lut || fail 'RAM table copy differs or LUT size is invalid'
    copied_identity=$(stat -c '%d:%i:%s:%Y' "$table" "$source_root") || fail 'cannot inspect copied LUT source'
    set -- $copied_identity
    [ "$#" -eq 2 ] || fail 'incomplete copied LUT identity'
    copied_source_id=${2%:*}; copied_source_id=${copied_source_id%:*}
    [ "$1" = "$before" ] && [ "$copied_source_id" = "$source_id" ] || fail 'source changed while copying LUT'
    sf_stage table-ready
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
sf_stage launcher
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
sf_stage output
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
    pause)
        # Runtime suspension requested by the LUT engine (LUT Off), not a
        # user preview change: stop the hardware path but keep the saved
        # preference untouched so re-applying a LUT restores it.
        [ "$value" != smartfilter ] || rm -f "$backend"
        printf '预览已随 LUT 关闭暂停；预览偏好保留。\n' ;;
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
