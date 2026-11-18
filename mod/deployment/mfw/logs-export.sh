#!/bin/sh
# One-shot quick log export to the SD card for offline troubleshooting.
# Collects the fixed RAM diagnostic files (the same set the remote web UI
# bundles) into a single text file on the card. User-invoked only; nothing
# here runs on a timer and the capture-path hook never writes to the card.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'NX300M log export: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail 'root required'
case $(uname -m) in armv7l) ;; *) fail 'native ARMv7 required' ;; esac
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
CODE_ROOT=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
ROOT=${NX300M_MOD_ROOT:-$CODE_ROOT}
STATE=${NX_STATE_DIR:-}
if [ -z "$STATE" ]; then
    # A bare-root shell (telnet) inherits no service environment; try the
    # known RAM-stage location before falling back to the SD state directory.
    if [ -d /tmp/nx300m-mfw/state ]; then
        STATE=/tmp/nx300m-mfw/state
    else
        STATE=$ROOT/.state
        [ -d "$STATE" ] || fail 'no RAM state directory; services not running'
    fi
fi
card=${1:-/mnt/mmc}
case $card in *[!A-Za-z0-9_./-]*) fail 'unsupported card path' ;; esac
[ -d "$card" ] || fail 'card directory unavailable'
[ -w "$card" ] || fail 'card directory is not writable'
timestamp=$(date -u '+%Y%m%dT%H%M%SZ')
target=$card/nx300m-logs-$timestamp.txt
[ -e "$target" ] || [ -L "$target" ] && fail 'target file already exists'
umask 077
: > "$target"
append_file() {
    name=$1; path=$2; limit=${3:-65536}
    printf '===== %s (%s) =====\n' "$name" "$([ -f "$path" ] && stat -c %s "$path" 2>/dev/null || echo unavailable)" >>"$target"
    if [ -f "$path" ] && [ ! -L "$path" ]; then
        size=$(stat -c %s "$path" 2>/dev/null || echo 0)
        if [ "$size" -gt "$limit" ]; then
            tail -c "$limit" "$path" >>"$target" || printf '(read failed)\n' >>"$target"
            printf '(trimmed to newest %s of %s bytes)\n' "$limit" "$size" >>"$target"
        else
            cat "$path" >>"$target" 2>/dev/null || printf '(read failed)\n' >>"$target"
        fi
    else
        printf '(unavailable)\n' >>"$target"
    fi
    printf '\n' >>"$target"
}
printf 'nx300m log bundle %s\n' "$timestamp" >>"$target"
printf 'uptime: ' >>"$target"
cat /proc/uptime >>"$target" 2>/dev/null || printf 'unavailable\n' >>"$target"
for name in mfw-entry.log remote.log supervisor.log service.state remote.pid \
            latest-diagnostic logs/fn-key.log bootstrap.log; do
    append_file "$name" "$STATE/$name"
done
append_file 'lut-journal.log' /var/run/nx300m-lut-journal.log
append_file 'lut-status.json' /var/run/nx300m-lut-status.json
sync
printf 'NX300M log export: %s\n' "$target"
