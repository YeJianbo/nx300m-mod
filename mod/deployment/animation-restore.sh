#!/bin/sh
# Reapply the explicitly saved animation preference once per service start.
set -u
root=${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}
data=${NX300M_MOD_SOURCE_ROOT:-$root}
case $root:$data in /*:/*) ;; *) exit 64 ;; esac
if [ -n "${NX300M_MOD_SOURCE_ID:-}" ]; then
    [ "$(stat -c '%d:%i' "$data" 2>/dev/null)" = "$NX300M_MOD_SOURCE_ID" ] || exit 69
fi
pref=$data/.state/fw-animation
[ -f "$pref" ] && [ ! -L "$pref" ] || exit 0
IFS= read -r mode < "$pref" || exit 64
case $mode in 0|1|2) ;; *) exit 64 ;; esac
helper=$root/bin/settings_animation.arm
[ -f "$helper" ] && [ ! -L "$helper" ] && [ -x "$helper" ] || exit 69
# Only retry transient busy/startup refusal. Never launch a second transaction
# while a recovery worker can still own the helper's process-wide lock.
i=0
while [ "$i" -lt 5 ]; do
    "$helper" "$mode" && exit 0
    rc=$?
    [ "$rc" = 4 ] || [ "$rc" = 69 ] || exit "$rc"
    i=$((i+1)); sleep 1
done
exit 69
