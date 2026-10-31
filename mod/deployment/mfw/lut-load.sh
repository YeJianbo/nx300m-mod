#!/bin/sh
# Manual RAM loading into the existing camera; no flash or app restart.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'NX300M LUT: %s\n' "$*" >&2; exit 1; }
[ "$#" -le 1 ] || fail 'use status, apply or restore'
operation=${1:-status}
case $operation in status|apply|restore) ;; *) fail 'use status, apply or restore' ;; esac
CODE_ROOT=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
ROOT=$CODE_ROOT
if [ -n "${NX300M_MOD_SOURCE_ROOT:-}${NX300M_MOD_SOURCE_ID:-}" ]; then
    [ -n "${NX300M_MOD_SOURCE_ROOT:-}" ] && [ -n "${NX300M_MOD_SOURCE_ID:-}" ] || fail 'incomplete data source identity'
    ROOT=$(CDPATH= cd -P "$NX300M_MOD_SOURCE_ROOT" && pwd -P) || fail 'data source unavailable'
    [ "$(stat -c '%d:%i' "$ROOT")" = "$NX300M_MOD_SOURCE_ID" ] || fail 'data source identity changed'
fi
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'unsupported data root' ;; esac
[ "$(id -u)" = 0 ] || fail 'root required'
case $(uname -m) in armv7l) ;; *) fail 'native ARMv7 required' ;; esac
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID
export NX300M_MOD_ROOT="$ROOT"
pids=
for entry in /proc/[0-9]*/exe; do
    target=$(readlink "$entry" 2>/dev/null) || continue
    case $target in
        /usr/bin/di-camera-app-nx300|/usr/bin/di-camera-app)
            value=${entry#/proc/}; pids="$pids ${value%/exe}" ;;
    esac
done
set -- $pids
[ "$#" -eq 1 ] || fail 'expected exactly one running original camera process'
camera_pid=$1
runtime=$(mktemp -d /tmp/nx300m-lut-load.XXXXXX) || fail 'cannot create RAM directory'
kind=$(stat -f -c '%T' "$runtime")
case $kind in tmpfs|ramfs) ;; *) fail 'temporary directory is not RAM backed' ;; esac
cleanup() {
    rm -f "$runtime/lut_live_loader.arm" "$runtime/lut_ram_blob.bin"
    rmdir "$runtime" 2>/dev/null || :
}
trap cleanup EXIT
for name in bin/lut_live_loader.arm lib/lut_ram_blob.bin; do
    [ -f "$CODE_ROOT/$name" ] && [ ! -L "$CODE_ROOT/$name" ] || fail "missing regular resource: $name"
    destination=$runtime/${name##*/}
    cp "$CODE_ROOT/$name" "$destination" || fail "copy failed: $name"
    cmp -s "$CODE_ROOT/$name" "$destination" || fail "copy mismatch: $name"
done
chmod 700 "$runtime/lut_live_loader.arm"
printf 'loader_runtime=%s\nlut_data_root=%s\n' "$runtime" "$ROOT"
run_loader() {
    case $operation in
        apply) "$runtime/lut_live_loader.arm" apply --pid "$camera_pid" --blob "$runtime/lut_ram_blob.bin" --root "$ROOT" ;;
        restore) "$runtime/lut_live_loader.arm" restore --pid "$camera_pid" ;;
        status) "$runtime/lut_live_loader.arm" status --pid "$camera_pid" ;;
    esac
}
loader_exit=0
run_loader || loader_exit=$?
# A refused reapply usually means a stale or half-restored resident blob from
# an earlier build. Restore it, then try once more; power-off is never needed.
if [ "$operation" = apply ] && [ "$loader_exit" -ne 0 ] && [ "$loader_exit" -ne 4 ] && [ "$loader_exit" -ne 5 ]; then
    restore_exit=0
    "$runtime/lut_live_loader.arm" restore --pid "$camera_pid" || restore_exit=$?
    [ -n "${NX_FN_KEY_LOG:-}" ] && \
        printf '%s lut-load recovery restore exit=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$restore_exit" \
            >>"$NX_FN_KEY_LOG" 2>/dev/null
    # With the hooks neutralized the resident mapping is unreachable. Drop the
    # state record so the retry installs fresh instead of adopting a mapping
    # whose layout may belong to an older blob build.
    [ "$restore_exit" -eq 0 ] || exit 1
    rm -f "/var/run/nx300m-lut-live-$camera_pid.state"
    loader_exit=0
    run_loader || loader_exit=$?
fi
# Result evidence must survive power-off for the physical camera loop.
[ -n "${NX_FN_KEY_LOG:-}" ] && \
    printf '%s lut-load %s exit=%s camera_pid=%s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$operation" "$loader_exit" "$camera_pid" \
        >>"$NX_FN_KEY_LOG" 2>/dev/null
exit $loader_exit
