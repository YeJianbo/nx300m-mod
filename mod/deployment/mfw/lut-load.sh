#!/bin/sh
# Manual RAM loading into the existing camera; no flash or app restart.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
# Control-path journal: one RAM line per user operation, kept under 32 KiB.
# The capture-path hook never writes here; see lut_engine_shared.h for the
# engine-side writer and /api/v1/diagnostics?bundle=1 for one-tap export.
LUT_JOURNAL=/var/run/nx300m-lut-journal.log
journal() {
    up=0; read -r up rest </proc/uptime 2>/dev/null || up=0
    printf '%s %s\n' "${up:-0}" "$*" >>"$LUT_JOURNAL" 2>/dev/null || :
}
journal_cap() {
    size=$(stat -c %s "$LUT_JOURNAL" 2>/dev/null) || return 0
    [ "$size" -le 32768 ] || {
        tail -c 16384 "$LUT_JOURNAL" >"$LUT_JOURNAL.trim" 2>/dev/null || return 0
        cat "$LUT_JOURNAL.trim" >"$LUT_JOURNAL" 2>/dev/null || :
        rm -f "$LUT_JOURNAL.trim"
    }
    return 0
}
fail() { journal_cap; journal "FAIL $*"; printf 'NX300M LUT: %s\n' "$*" >&2; exit 1; }
[ "$#" -le 1 ] || fail 'use status, apply or restore'
operation=${1:-status}
case $operation in status|apply|restore) ;; *) fail 'use status, apply or restore' ;; esac
CODE_ROOT=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
ROOT=$CODE_ROOT
fixture=${NX_SF_PROBE_FIXTURE:-0}
case $fixture in 0|1) ;; *) fail 'invalid fixture mode' ;; esac
if [ -n "${NX300M_MOD_SOURCE_ROOT:-}${NX300M_MOD_SOURCE_ID:-}" ]; then
    [ -n "${NX300M_MOD_SOURCE_ROOT:-}" ] && [ -n "${NX300M_MOD_SOURCE_ID:-}" ] || fail 'incomplete data source identity'
    ROOT=$(CDPATH= cd -P "$NX300M_MOD_SOURCE_ROOT" && pwd -P) || fail 'data source unavailable'
    [ "$(stat -c '%d:%i' "$ROOT")" = "$NX300M_MOD_SOURCE_ID" ] || fail 'data source identity changed'
fi
[ "$fixture" != 1 ] || [ "$ROOT" = /perf ] || fail 'fixture requires explicit /perf data root'
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'unsupported data root' ;; esac
[ "$(id -u)" = 0 ] || fail 'root required'
case $(uname -m) in armv7l) ;; *) fail 'native ARMv7 required' ;; esac
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID
export NX300M_MOD_ROOT="$ROOT"
# Still upgrades and preview controls both stop the same target threads.
# Serialize them before any ptrace operation; retain unresolved recovery state.
if [ "$operation" != status ]; then
    [ ! -e /tmp/nx300m-smartfilter-preview.uncertain ] || fail 'preview recovery is still active'
    # Release the preview lease across loader exec. The loader's supervisor and
    # recovery workers outlive this script; an inherited fd 9 would keep the
    # lease after this script exits, so the next apply would fail as
    # "preview control is running" while nothing is actually running.
    exec 9>/tmp/nx300m-smartfilter-preview.lock
    flock -n 9 || fail 'preview control is running; no LUT installation attempted'
fi
pids=
for entry in /proc/[0-9]*/exe; do
    target=$(readlink "$entry" 2>/dev/null) || continue
    case $target in
        /usr/bin/di-camera-app-nx300|/usr/bin/di-camera-app)
            value=${entry#/proc/}; pids="$pids ${value%/exe}" ;;
        /mnt/mmc/bin/smartfilter_fixture.arm)
            if [ "$fixture" = 1 ]; then value=${entry#/proc/}; pids="$pids ${value%/exe}"; fi ;;
    esac
done
set -- $pids
[ "$#" -eq 1 ] || fail 'expected exactly one running original camera process'
camera_pid=$1
journal_cap
journal "op=$operation begin camera_pid=$camera_pid root=$ROOT"
runtime=
cleanup() {
    [ -n "$runtime" ] || return 0
    rm -f "$runtime"/compat/*.bin
    rmdir "$runtime/compat" 2>/dev/null || :
    rm -f "$runtime/lut_live_loader.arm" "$runtime/lut_ram_blob.bin" "$runtime/lut_engine.arm"
    rmdir "$runtime" 2>/dev/null || :
}
trap cleanup EXIT
ensure_runtime() {
    [ -z "$runtime" ] || return 0
    runtime=$(mktemp -d /tmp/nx300m-lut-load.XXXXXX) || fail 'cannot create RAM directory'
    kind=$(stat -f -c '%T' "$runtime")
    case $kind in tmpfs|ramfs) ;; *) fail 'temporary directory is not RAM backed' ;; esac
    for name in bin/lut_live_loader.arm lib/lut_ram_blob.bin bin/lut_engine.arm; do
        [ -f "$CODE_ROOT/$name" ] && [ ! -L "$CODE_ROOT/$name" ] || fail "missing regular resource: $name"
        destination=$runtime/${name##*/}
        cp "$CODE_ROOT/$name" "$destination" || fail "copy failed: $name"
        cmp -s "$CODE_ROOT/$name" "$destination" || fail "copy mismatch: $name"
    done
    chmod 700 "$runtime/lut_live_loader.arm" "$runtime/lut_engine.arm"
}
code_kind=$(stat -f -c '%T' "$CODE_ROOT" 2>/dev/null || :)
if { [ "$code_kind" = tmpfs ] || [ "$code_kind" = ramfs ]; } &&
   [ -f "$CODE_ROOT/bin/lut_live_loader.arm" ] && [ ! -L "$CODE_ROOT/bin/lut_live_loader.arm" ] && [ -x "$CODE_ROOT/bin/lut_live_loader.arm" ] &&
   [ -f "$CODE_ROOT/lib/lut_ram_blob.bin" ] && [ ! -L "$CODE_ROOT/lib/lut_ram_blob.bin" ] &&
   [ -f "$CODE_ROOT/bin/lut_engine.arm" ] && [ ! -L "$CODE_ROOT/bin/lut_engine.arm" ] && [ -x "$CODE_ROOT/bin/lut_engine.arm" ]; then
    loader_bin=$CODE_ROOT/bin/lut_live_loader.arm
    blob_file=$CODE_ROOT/lib/lut_ram_blob.bin
    engine_bin=$CODE_ROOT/bin/lut_engine.arm
    printf 'loader_runtime=%s\nlut_data_root=%s\n' "$CODE_ROOT" "$ROOT"
else
    ensure_runtime
    loader_bin=$runtime/lut_live_loader.arm
    blob_file=$runtime/lut_ram_blob.bin
    engine_bin=$runtime/lut_engine.arm
    printf 'loader_runtime=%s\nlut_data_root=%s\n' "$runtime" "$ROOT"
fi
run_loader() {
    set --
    [ "$fixture" != 1 ] || set -- --fixture
    # 9>&- : never hand the preview lease to the loader. Its supervisor and
    # recovery workers outlive this script, and an inherited descriptor would
    # pin the lease after exit so the next apply reports "preview control is
    # running" although nothing is.
    case $operation in
        apply) "$loader_bin" apply --pid "$camera_pid" --blob "$blob_file" --root "$ROOT" "$@" 9>&- ;;
        restore) "$loader_bin" restore --pid "$camera_pid" "$@" 9>&- ;;
        status) "$loader_bin" status --pid "$camera_pid" 9>&- ;;
    esac
}
loader_exit=0
run_loader || loader_exit=$?
journal "loader op=$operation rc=$loader_exit"
# Upgrade a valid old installation, or recognize an orphan only by an exact
# match to a saved immutable code image. Never overwrite unidentified hooks.
if [ "$operation" = apply ] && { [ "$loader_exit" -eq 3 ] || [ "$loader_exit" -eq 7 ]; }; then
    recovered=0
    if [ "$loader_exit" -eq 7 ]; then
        restore_exit=0
        "$loader_bin" restore --pid "$camera_pid" 9>&- || restore_exit=$?
        case $restore_exit in
            0) recovered=1; rm -f "/var/run/nx300m-lut-live-$camera_pid.state" ;;
            4|5) journal "recovery restore rc=$restore_exit"; exit "$restore_exit" ;;
        esac
    fi
    if [ "$recovered" -eq 0 ]; then
        # Saved code images are only consulted during known-hook recovery.
        # A normal status/apply must not copy and reread every legacy image.
        ensure_runtime
        mkdir "$runtime/compat"
        for saved in "$CODE_ROOT"/lib/lut-compat/*.bin; do
            [ -f "$saved" ] && [ ! -L "$saved" ] || continue
            target=$runtime/compat/${saved##*/}
            cp "$saved" "$target" && cmp -s "$saved" "$target" || fail 'compatibility image copy differs'
        done
        for saved in "$runtime/lut_ram_blob.bin" "$runtime"/compat/*.bin; do
            [ -f "$saved" ] || continue
            restore_exit=0
            "$runtime/lut_live_loader.arm" restore-known --pid "$camera_pid" --blob "$saved" --root "$ROOT" || restore_exit=$?
            case $restore_exit in
                0) recovered=1; break ;;
                4|5) exit "$restore_exit" ;;
            esac
        done
    fi
    if [ "$recovered" -eq 1 ]; then
        journal "recovery matched saved image; retrying apply"
        loader_exit=0
        run_loader || loader_exit=$?
        journal "loader op=$operation rc=$loader_exit after-recovery"
    else
        journal "recovery exhausted: resident hooks match no saved image"
        printf 'Resident hooks did not match saved code; read-only status follows.\n' >&2
        "$loader_bin" status --pid "$camera_pid" 9>&- || :
    fi
fi
if [ "$operation" = apply ] && [ "$loader_exit" -eq 0 ]; then
    # Selection restoration may sync preview after the Still transaction.
    # Release its shared control lease before that nested synchronization.
    flock -u 9
    # Restore selection through the same validated backend used by App/panel.
    # An invalid saved look must not turn a successful hook load into a retry.
    if [ "${NX300M_LUT_ENGINE_OWNS_LOCK:-0}" != 1 ]; then
        "$engine_bin" restore-selection 9>&- || \
            { journal "restore-selection failed"; printf 'RESTORE-WARNING saved LUT could not be restored; select a look again.\n' >&2; }
    fi
fi
# Result evidence must survive power-off for the physical camera loop.
[ -n "${NX_FN_KEY_LOG:-}" ] && \
    printf '%s lut-load %s exit=%s camera_pid=%s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$operation" "$loader_exit" "$camera_pid" \
        >>"$NX_FN_KEY_LOG" 2>/dev/null
journal "done op=$operation rc=$loader_exit"
exit $loader_exit
