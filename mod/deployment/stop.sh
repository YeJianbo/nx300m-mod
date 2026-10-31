#!/bin/sh
. "${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}/common.sh"
operation_lock || fail 'another start/stop operation is still active'
# Record intent even if the child already exited. Mount reconciliation respects
# it during this boot; an explicit start or a new boot clears it.
printf '%s\n' "$BOOT_ID" > "$STOP_REQUEST" || fail 'cannot record manual stop request'
if [ ! -f "$PIDFILE" ]; then
    printf 'NX300M remote is stopped\n'
    exit 0
fi
read_record || fail 'invalid PID record; no process was signalled'
if ! identity_alive "$REC_PID" "$REC_START" "$REC_BOOT" "$REC_EXE" "$REC_EXE_ID"; then
    printf 'NX300M stale PID record ignored; no process signalled\n'
    rm -f "$PIDFILE" "$PHASE"
    exit 0
fi
OWN_PID=$REC_PID; OWN_START=$REC_START
stop_identity "$REC_PID" "$REC_START" "$REC_BOOT" "$REC_EXE" "$REC_EXE_ID" || fail 'owned remote did not stop after TERM; PID record retained'
remove_own_record
printf 'NX300M remote stopped pid=%s\n' "$OWN_PID"
