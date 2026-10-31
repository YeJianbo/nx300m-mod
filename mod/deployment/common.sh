#!/bin/sh
# BusyBox ash/flock/readlink/stat are present in the verified stock TAR.
set -u
umask 077

# One-line evidence mirror to the card. The RAM state directory holds every
# other log only until power-off, so boot-chain failures must be echoed here.
fn_note() {
    [ -n "${NX_FN_KEY_LOG:-}" ] || return 0
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$NX_FN_KEY_LOG" 2>/dev/null || :
}
fail() { fn_note "fail: $*"; printf '%s\n' "NX300M: $*" >&2; exit 1; }
configured_root=${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}
case $configured_root in /*) ;; *) fail 'NX300M_MOD_ROOT must be absolute' ;; esac
ROOT=$(CDPATH= cd -P "$configured_root" && pwd -P) || exit 1
STATE=${NX_STATE_DIR:-$ROOT/.state}
case $STATE in /*) ;; *) fail 'NX_STATE_DIR must be absolute' ;; esac
mkdir -p "$STATE" || fail 'state directory is not writable'
STATE=$(CDPATH= cd -P "$STATE" && pwd -P) || exit 1
REMOTE=$ROOT/bin/nx-remote-controller.arm
QUICKSHARE=$ROOT/bin/nx-quickshare-armv7
PIDFILE=$STATE/remote.pid
PHASE=$STATE/service.state
STOP_REQUEST=$STATE/manual-stop
LOG=$STATE/remote.log
IFS= read -r BOOT_ID < /proc/sys/kernel/random/boot_id || fail 'kernel boot identity unavailable'
[ -n "$BOOT_ID" ] || fail 'kernel boot identity empty'
START_TIMEOUT=${NX_START_TIMEOUT:-15}
STOP_TIMEOUT=${NX_STOP_TIMEOUT:-6}
case $START_TIMEOUT:$STOP_TIMEOUT in *[!0-9:]*|:*|*:) fail 'timeouts must be integer seconds' ;; esac
[ "$START_TIMEOUT" -ge 1 ] && [ "$START_TIMEOUT" -le 60 ] || fail 'start timeout must be 1..60'
[ "$STOP_TIMEOUT" -ge 1 ] && [ "$STOP_TIMEOUT" -le 30 ] || fail 'stop timeout must be 1..30'

operation_lock() {
    exec 9>"$STATE/operation.lock" || return 1
    attempt=0
    while ! flock -n 9; do
        attempt=$((attempt+1))
        [ "$attempt" -lt 10 ] || return 1
        sleep 1
    done
}

process_start() {
    case $1 in ''|*[!0-9]*) return 1 ;; esac
    [ "$1" -gt 1 ] || return 1
    IFS= read -r process_stat 2>/dev/null < "/proc/$1/stat" || return 1
    process_fields=${process_stat##*) }
    # Fields after comm start at state (3); starttime is field 22.
    set -- $process_fields
    [ "$#" -ge 20 ] && [ "$1" != Z ] || return 1
    shift 19
    case $1 in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$1"
}

read_record() {
    [ -f "$PIDFILE" ] || return 1
    { IFS= read -r REC_PID && IFS= read -r REC_START && IFS= read -r REC_BOOT && IFS= read -r REC_EXE && IFS= read -r REC_EXE_ID; } < "$PIDFILE" || return 1
    case $REC_PID:$REC_START in *[!0-9:]*|:*|*:) return 1 ;; esac
    case $REC_EXE_ID in *[!0-9:]*|:*|*:|'') return 1 ;; esac
    [ "$REC_PID" -gt 1 ] && [ "$REC_EXE" = "$REMOTE" ]
}

identity_alive() {
    # The executable inode survives directory rename/unmount. It is captured
    # from the exact binary path before fork, then compared with the live exe.
    [ "$3" = "$BOOT_ID" ] || return 1
    observed_start=$(process_start "$1") || return 1
    [ "$observed_start" = "$2" ] || return 1
    observed_exe=$(readlink "/proc/$1/exe" 2>/dev/null) || return 1
    if [ -n "${5:-}" ]; then
        observed_exe_id=$(stat -L -c '%d:%i' "/proc/$1/exe" 2>/dev/null) || return 1
        [ "$observed_exe_id" = "$5" ] || return 1
    else
        case $observed_exe in "$4"|"$4 (deleted)") ;; *) return 1 ;; esac
    fi
    kill -0 "$1" 2>/dev/null
}

record_alive() {
    read_record && identity_alive "$REC_PID" "$REC_START" "$REC_BOOT" "$REC_EXE" "$REC_EXE_ID"
}

remove_own_record() {
    if read_record && [ "$REC_PID" = "$OWN_PID" ] && [ "$REC_START" = "$OWN_START" ] && [ "$REC_BOOT" = "$BOOT_ID" ]; then
        rm -f "$PIDFILE" "$PHASE"
    fi
}

root_present() {
    current_root=$(stat -c '%d:%i' "$ROOT" 2>/dev/null) || return 1
    [ "$current_root" = "$root_identity" ] && [ -r "$ROOT/start.sh" ]
}

stop_identity() {
    stop_pid=$1; stop_start=$2; stop_boot=$3; stop_exe=$4; stop_exe_id=$5
    identity_alive "$stop_pid" "$stop_start" "$stop_boot" "$stop_exe" "$stop_exe_id" || return 0
    kill -TERM "$stop_pid" 2>/dev/null || return 1
    stopped_wait=0
    while identity_alive "$stop_pid" "$stop_start" "$stop_boot" "$stop_exe" "$stop_exe_id"; do
        [ "$stopped_wait" -lt "$STOP_TIMEOUT" ] || return 1
        sleep 1
        stopped_wait=$((stopped_wait+1))
    done
}
