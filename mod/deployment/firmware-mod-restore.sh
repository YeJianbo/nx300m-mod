#!/bin/sh
# One-shot boot restore for firmware control preferences. The app launcher
# starts this just before the camera exec; it waits a bounded time for the
# firmware-mod bridge, re-applies explicitly saved preferences once, then exits.
# Missing preference files are skipped (module power-on defaults: lock off 0,
# animation 1, ISO unlock 0). Explicitly saved values (including animation 0 for
# stock) are restored.
set -u
root=${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}
data=${NX300M_MOD_SOURCE_ROOT:-$root}
case $root:$data in /*:/*) ;; *) exit 64 ;; esac
if [ -n "${NX300M_MOD_SOURCE_ID:-}" ]; then
    [ "$(stat -c '%d:%i' "$data" 2>/dev/null)" = "$NX300M_MOD_SOURCE_ID" ] || exit 69
fi
state=$data/.state
control=$root/camera_control.sh
log=$state/fw-restore.log
note() { printf '%s %s\n' "$(date -u '+%FT%TZ' 2>/dev/null)" "$*" >>"$log" 2>/dev/null || :; }
[ -f "$control" ] && [ ! -L "$control" ] || exit 0
[ -d "$state" ] && [ ! -L "$state" ] || exit 0
# Both the app launcher and the staged service may reach this script. Keep
# the lock in RAM; preferences always come from the source card, never the
# executable staging directory.
run=${NX_STATE_DIR:-/var/run}
[ -d "$run" ] && [ ! -L "$run" ] || exit 69
exec 8>"$run/fw-restore.lock" || exit 69
flock -n 8 || exit 0
wait_bridge() {
    ready=/var/run/nx300m-$1-ready
    i=0
    while [ "$i" -lt 240 ]; do
        [ -f "$ready" ] && break
        i=$((i + 1)); sleep 0.5
    done
    [ -f "$ready" ] || return 1
    read -r owner < "$ready" || return 1
    case $owner in ''|*[!0-9]*) return 1 ;; esac
    [ "$owner" -gt 1 ] && kill -0 "$owner" 2>/dev/null
}
fw_restore() { # preference-name command allowed-values...
    name=$1
    pref=$state/$name
    command=$2
    shift 2
    [ -f "$pref" ] && [ ! -L "$pref" ] || return 0
    IFS= read -r saved < "$pref" || return 0
    ok=0
    for value in "$@"; do
        [ "$saved" = "$value" ] && ok=1
    done
    [ "$ok" = 1 ] || return 0
    if /bin/sh "$control" "$command" "$saved" >/dev/null 2>&1; then
        note "restored $name=$saved"
    else
        note "restore failed $name=$saved"
    fi
}
if [ -f "$state/fw-mode-lock" ] || [ -f "$state/fw-animation" ] || [ -f "$state/fw-auto-iso-lock" ]; then
    if wait_bridge firmware-mod; then
        fw_restore fw-mode-lock mode-lock 0 1
        fw_restore fw-animation animation 0 1 2
        # A saved explicit ceiling supersedes the older 6400 lock. Otherwise
        # replaying both settings silently pins a requested 12800/25600 to
        # 6400 inside the firmware-mod SetUserData hook.
        ceiling=
        if [ -f "$state/fw-auto-iso-max" ] && [ ! -L "$state/fw-auto-iso-max" ]; then
            IFS= read -r ceiling < "$state/fw-auto-iso-max" || ceiling=
        fi
        case $ceiling in
            3200|12800|25600)
                /bin/sh "$control" iso-lock 0 >/dev/null 2>&1 || note 'failed to release conflicting 6400 lock' ;;
            *) fw_restore fw-auto-iso-lock iso-lock 0 6400 ;;
        esac
    else
        note 'firmware-mod bridge never became ready; firmware preferences not restored'
    fi
fi
# The Auto ISO ceiling belongs to the separate exposure bridge; wait for that
# owner instead of firmware-mod before replaying a saved extension.
if [ -f "$state/fw-auto-iso-max" ]; then
    if wait_bridge exposure; then
        fw_restore fw-auto-iso-max auto-iso 3200 6400 12800 25600
    else
        note 'exposure bridge never became ready; fw-auto-iso-max not restored'
    fi
fi
exit 0
