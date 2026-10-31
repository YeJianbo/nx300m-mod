#!/bin/sh
. "${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}/common.sh"
exec 8>"$STATE/service.lock" || exit 1
flock -n 8 || exit 1
fn_note "supervisor begin root=$ROOT"
root_identity=$(stat -c '%d:%i' "$ROOT") || exit 1
# Capture the precise file before fork, so a root rename during exec does not
# prevent identifying the child that actually loaded this inode.
OWN_EXE_ID=$(stat -L -c '%d:%i' "$REMOTE") || exit 1
OWN_PID=0; OWN_START=0
cleanup() {
    trap - EXIT HUP INT TERM
    if [ "$OWN_PID" -gt 1 ]; then
        if ! stop_identity "$OWN_PID" "$OWN_START" "$BOOT_ID" "$REMOTE" "$OWN_EXE_ID"; then
            printf 'NX300M: owned remote did not stop after TERM pid=%s\n' "$OWN_PID" >&2
            return 1
        fi
        # A child with an unrecognised executable must not be signalled or waited
        # on indefinitely. Normally it has exited or stop_identity reaped it.
        if process_start "$OWN_PID" >/dev/null 2>&1; then
            printf 'NX300M: child identity changed; PID record retained pid=%s\n' "$OWN_PID" >&2
            return 1
        fi
        wait "$OWN_PID" 2>/dev/null || :
        remove_own_record
    fi
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM
printf 'starting\n' > "$PHASE" || exit 1
export DISPLAY=${DISPLAY:-:0}
fn_note "supervisor: spawning daemon exe=$REMOTE display=${DISPLAY:-:0}"
"$REMOTE" --listen "${NX_REMOTE_LISTEN:-127.0.0.1:8781}" \
    --web-root "$ROOT/web_root" --media-root "${NX_MEDIA_ROOT:-/mnt/mmc/DCIM}" \
    --quickshare-binary "$QUICKSHARE" </dev/null >"$LOG" 2>&1 8>&- 9>&- &
OWN_PID=$!
fn_note "supervisor: daemon forked pid=$OWN_PID"
identity_wait=0
while [ "$identity_wait" -lt "$START_TIMEOUT" ]; do
    OWN_START=$(process_start "$OWN_PID") || break
    if identity_alive "$OWN_PID" "$OWN_START" "$BOOT_ID" "$REMOTE" "$OWN_EXE_ID"; then
        printf '%s\n%s\n%s\n%s\n%s\n' "$OWN_PID" "$OWN_START" "$BOOT_ID" "$REMOTE" "$OWN_EXE_ID" > "$PIDFILE.tmp" || exit 1
        mv "$PIDFILE.tmp" "$PIDFILE" || exit 1
        break
    fi
    sleep 1
    identity_wait=$((identity_wait+1))
done
if [ -z "$OWN_EXE_ID" ] || ! identity_alive "$OWN_PID" "$OWN_START" "$BOOT_ID" "$REMOTE" "$OWN_EXE_ID"; then
    fn_note "supervisor: daemon died before identity record pid=$OWN_PID"
    printf 'failed\n' > "$PHASE"
    exit 1
fi
ready_wait=0
while identity_alive "$OWN_PID" "$OWN_START" "$BOOT_ID" "$REMOTE" "$OWN_EXE_ID"; do
    root_present || break
    if grep -q '^NX_REMOTE_READY ' "$LOG"; then
        fn_note "supervisor: daemon ready pid=$OWN_PID"
        printf 'ready\n' > "$PHASE" || exit 1
        break
    fi
    ready_wait=$((ready_wait+1))
    if [ "$ready_wait" -ge "$START_TIMEOUT" ]; then
        fn_note "supervisor: daemon not ready within ${START_TIMEOUT}s pid=$OWN_PID"
        printf 'failed\n' > "$PHASE"
        exit 1
    fi
    sleep 1
done
while identity_alive "$OWN_PID" "$OWN_START" "$BOOT_ID" "$REMOTE" "$OWN_EXE_ID"; do
    root_present || break
    sleep 1
done
