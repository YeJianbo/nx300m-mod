#!/bin/sh
# Finite X11-only restore; no exposure API, camera restart or firmware writes.
set -u
root=${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}
data=${NX300M_MOD_SOURCE_ROOT:-$root}
case $root:$data in /*:/*) ;; *) exit 64 ;; esac
if [ -n "${NX300M_MOD_SOURCE_ID:-}" ]; then
    [ "$(stat -c '%d:%i' "$data" 2>/dev/null)" = "$NX300M_MOD_SOURCE_ID" ] || exit 69
fi
helper=$root/bin/mode_dial_x11.arm
[ -f "$helper" ] && [ ! -L "$helper" ] && [ -x "$helper" ] || exit 69
pref=$data/.state/fw-mode-lock
request=status
if [ -f "$pref" ] && [ ! -L "$pref" ]; then
    IFS= read -r value < "$pref" || exit 64
    case $value in 0) request=off ;; 1) request=on ;; *) exit 64 ;; esac
fi
i=0
while [ "$i" -lt 5 ]; do
    if actual=$("$helper" "$request"); then
        printf 'MODE_DIAL_X11 restored request=%s actual=%s\n' "$request" "$actual"
        exit 0
    fi
    i=$((i+1)); sleep 0.2
done
printf 'MODE_DIAL_X11 restore failed; camera and AE unchanged\n' >&2
exit 69
