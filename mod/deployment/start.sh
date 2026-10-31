#!/bin/sh
. "${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}/common.sh"
operation_lock || fail 'another start/stop operation is still active'

# Mount notifications cover the whole namespace. Automatic reconciliation is
# idempotent and must not undo an explicit stop on an unrelated mount change.
if [ "${NX300M_AUTO_START:-0}" = 1 ] && [ -r "$STOP_REQUEST" ]; then
    manual_boot=
    IFS= read -r manual_boot < "$STOP_REQUEST" || :
    if [ "$manual_boot" = "$BOOT_ID" ]; then
        printf 'NX300M automatic start skipped: manually stopped this boot\n'
        exit 0
    fi
fi
if [ -e "$STOP_REQUEST" ]; then
    rm -f "$STOP_REQUEST" || fail 'cannot clear manual stop request'
fi

fn_note "start begin root=$ROOT auto=${NX300M_AUTO_START:-0}"
if record_alive; then
    fn_note "start exit: already running pid=$REC_PID"
    printf 'NX300M remote already running pid=%s\n' "$REC_PID"
    exit 0
fi
# An obsolete record is metadata only. No process is signalled during recovery.
rm -f "$PIDFILE" "$PHASE" || fail 'cannot clear obsolete state'
[ -x "$REMOTE" ] || fail 'remote binary is missing or SD execution is unavailable'
[ -x "$QUICKSHARE" ] || fail 'Quick Share binary is missing or SD execution is unavailable'
[ -r "$ROOT/web_root/index.html" ] || fail 'web assets are missing'
[ -r "$ROOT/service.sh" ] || fail 'service supervisor is missing'
exec 8>"$STATE/service.lock" || fail 'cannot open service lock'
lease_wait=0
while ! flock -n 8; do
    lease_wait=$((lease_wait+1))
    [ "$lease_wait" -lt 5 ] || fail 'previous supervisor has not exited'
    sleep 1
done
exec 8>&-

export NX300M_MOD_ROOT="$ROOT" NX_MOD_ROOT="$ROOT" NX_STATE_DIR="$STATE"
/bin/sh "$ROOT/service.sh" </dev/null >>"$STATE/supervisor.log" 2>&1 8>&- 9>&- &
supervisor_pid=$!
started_wait=0
while [ "$started_wait" -lt "$START_TIMEOUT" ]; do
    if [ -r "$PHASE" ]; then
        IFS= read -r phase < "$PHASE" || phase=
        if [ "$phase" = ready ] && record_alive; then
            fn_note "start: daemon ready pid=$REC_PID"
            printf 'NX300M remote HTTP ready pid=%s listen=%s\n' "$REC_PID" "${NX_REMOTE_LISTEN:-127.0.0.1:8781}"
            exit 0
        fi
        [ "$phase" != failed ] || fail "remote startup failed; see $LOG"
    fi
    kill -0 "$supervisor_pid" 2>/dev/null || fail "supervisor exited; see $STATE/supervisor.log"
    sleep 1
    started_wait=$((started_wait+1))
done
fail "startup deadline reached; inspect $LOG and run stop.sh before retrying"
