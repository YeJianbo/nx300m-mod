#!/bin/sh
# Probe package wrapper: no S1 or continuous/nonzero apply entry point.
set -eu
set -f
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'SmartFilter preview: %s\n' "$*" >&2; exit 1; }
command=${1:-status}
[ "$#" -le 1 ] || fail 'unexpected arguments'
case $command in
    probe-once|test-identity) command=probe-once ;;
    enable-s2-stock|stock|status|restore|diag-export) ;;
    enable|enable-stock|enable-s2|sync|test-monochrome|apply)
        fail '探针包拒绝 S1、持续或非零 LUT 入口；仅允许 S2 原样与单位表单帧探针' ;;
    *) fail 'use enable-s2-stock, probe-once, stock, status, restore or diag-export' ;;
esac
[ "$(id -u)" = 0 ] || fail 'root required'
[ "$(uname -m)" = armv7l ] || fail 'native ARMv7 required'
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH ENV BASH_ENV
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
case $HERE in *[!A-Za-z0-9_./-]*) fail 'unsupported runtime path' ;; esac
# Source-card identity is captured by RAM staging, not guessed from a possibly
# replaced mount. UI does not read the card or inherit its media lock for jobs.
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
[ ! -e "$guard" ] && [ ! -L "$guard" ] || fail '上次操作超时，未终止 loader/tracer；请保留 RAM 现场并正常关机重启，禁止重武装'
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
# Private job directories are siblings of RAM generations; stage/previous
# replacement cannot unlink resources needed by a detached collector/tracer.
job=$(mktemp -d /tmp/nx300m-sf-job.XXXXXX) || fail 'cannot allocate private RAM job'
case $(stat -f -c '%T' "$job") in tmpfs|ramfs) ;; *) rmdir "$job"; fail 'job must be RAM backed' ;; esac
for pair in bin/smartfilter_probe_launcher.arm:launcher bin/smartfilter_loader.arm:loader.arm lib/smartfilter_ram.bin:blob.bin smartfilter-probe-collector.sh:collector.sh; do
    resource=$HERE/${pair%:*}; destination=$job/${pair#*:}
    [ -f "$resource" ] && [ ! -L "$resource" ] || fail "missing resource: $resource (RAM retained: $job)"
    cp "$resource" "$destination" && cmp -s "$resource" "$destination" || fail "private RAM copy differs ($job)"
done
chmod 500 "$job/launcher" "$job/loader.arm" "$job/collector.sh"
chmod 400 "$job/blob.bin"
if [ "$command" = probe-once ]; then
    nonce=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
    [ "${#nonce}" -eq 32 ] || fail 'cannot obtain probe nonce'
    # No preview/media lock is opened by the UI path. This returns only after
    # nonce/PID/starttime and a different session/group have been verified.
    "$job/launcher" --launch "$job" "$nonce" "$source_root" "$source_id" "$state" "$camera_pid" "$camera_start" || fail "collector not acknowledged; no retry; RAM=$job"
    printf '单帧探针已受理，不表示处理或存卡成功。保持灯灭，勿拍照或切换介质；RAM 状态：%s/result\n' "$job"
    exit 0
fi
# Synchronous read/stock/soft-stop controls have a finite wait too. UI-owned
# descriptors are not inherited. Detached collectors instead preserve verified
# job-owned leases across uncertain writer/tracer recovery.
[ ! -L /tmp/nx300m-smartfilter-preview.lock ] || fail 'preview lock is a symbolic link'
exec 9>/tmp/nx300m-smartfilter-preview.lock
flock -n 9 || fail 'another preview operation is running'
[ ! -e "$guard" ] && [ ! -L "$guard" ] || fail 'previous operation is unresolved'
operation=$command
case $command in
    enable-s2-stock|stock)
        operation=stock
        [ ! -e "/var/run/nx300m-ipcm-preview-$camera_pid.state" ] || fail 'restore IPCM and restart camera before changing backend' ;;
esac
set -- "$operation" --pid "$camera_pid" --blob "$job/blob.bin"
case $command in enable-s2-stock|stock) set -- "$@" --s2 ;; esac
[ "$fixture" != 1 ] || set -- "$@" --fixture
# Ordinary manual export preserves the loader's stricter persistence gate.
if [ "$command" = diag-export ] && [ -n "${NX_FN_KEY_LOG:-}" ]; then
    [ "$NX_FN_KEY_LOG" = "$source_root/logs/fn-key.log" ] || fail 'diagnostic log belongs to another source'
    set -- "$@" --diag-log "$NX_FN_KEY_LOG"
fi
rc=0
"$job/launcher" --run 30 "$job/control.json" "$job/control.stderr" "$guard" "$job/loader.arm" "$@" || rc=$?
[ ! -f "$job/control.json" ] || while IFS= read -r line; do printf '%s\n' "$line"; done < "$job/control.json"
[ "$rc" -eq 0 ] || fail "操作失败或超时 rc=$rc；未自动重武装，RAM=$job"
case $command in
    enable-s2-stock|stock) printf 'S2 仅原样通路；没有提交非零或持续 LUT。\n' ;;
    restore) printf 'S2 已请求软停；必要路由和恢复 tracer 不强拆，完全退出需正常关机重启。\n' ;;
esac
