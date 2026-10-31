#!/bin/sh
# Existing MFW entry. Collect diagnostics on SD; do not chain another Mod.
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
root=/mnt/mmc/nx300m-diagnostics

[ -d "$root" ] || exit 0
[ -e "$root/DISABLED" ] && exit 0
[ -e "$root/complete.ok" ] && exit 0
[ -f "$root/preflight.sh" ] || exit 0
umask 077
mkdir "$root/running" 2>/dev/null || exit 0
trap 'rmdir "$root/running" 2>/dev/null' 0

{
    printf 'NX300M existing MFW autoexec diagnostic v1\n'
    date
    id
    /bin/sh "$root/preflight.sh"
    result=$?
    printf '\npreflight_exit=%s\n' "$result"
    [ "$result" -eq 0 ] || exit "$result"
    printf 'probe complete\n'
} > "$root/preflight.log" 2>&1 || exit 1
printf 'diagnostic-only; no service or hook started\n' > "$root/complete.ok"
