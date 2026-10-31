#!/bin/sh
# Manual, temporary startup candidate. Never stops a running camera app.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() {
    printf 'NX300M exposure: %s\n' "$*" >&2
    if [ -n "${runtime:-}" ]; then
        printf 'ERROR: %s\n' "$*" >> "$runtime/launch.log" || :
        printf 'Logs retained: %s/launch.log and %s/camera.log\n' "$runtime" "$runtime" >&2
    fi
    exit 1
}
report() {
    printf '%s\n' "$*"
    printf '%s\n' "$*" >> "$runtime/launch.log"
}
[ "$#" -le 1 ] || fail 'use inspect or launch without additional arguments'
operation=${1:-inspect}
case $operation in inspect|launch) ;; *) fail 'use inspect or launch' ;; esac
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
MODULE=$HERE/lib/exposure_status.so
APP=/usr/bin/di-camera-app-nx300

camera_processes() {
    for entry in /proc/[0-9]*/exe; do
        target=$(readlink "$entry" 2>/dev/null) || continue
        case $target in
            */di-camera-app-nx300|*/di-camera-app-nx300\ \(deleted\)|*/di-camera-app|*/di-camera-app\ \(deleted\))
                pid=${entry#/proc/}; pid=${pid%/exe}
                printf '%s\n' "$pid"
                ;;
        esac
    done
}

process_start() {
    IFS= read -r process_stat 2>/dev/null < "/proc/$1/stat" || return 1
    process_fields=${process_stat##*) }
    set -- $process_fields
    [ "$#" -ge 20 ] && [ "$1" != Z ] || return 1
    shift 19
    case $1 in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$1"
}
uptime_seconds() {
    IFS=' ' read -r uptime_value uptime_rest < /proc/uptime || return 1
    uptime_value=${uptime_value%%.*}
    case $uptime_value in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$uptime_value"
}
verify_camera() {
    [ "$(camera_processes)" = "$camera_pid" ] || fail 'camera PID changed, disappeared or became multiple instances; no signal was sent'
    [ "$(readlink "/proc/$camera_pid/exe" 2>/dev/null)" = "$APP" ] || fail 'camera executable path changed'
    observed_inode=$(stat -L -c '%d:%i' "/proc/$camera_pid/exe" 2>/dev/null) || fail 'cannot inspect camera executable identity'
    [ "$observed_inode" = "$app_inode" ] || fail 'camera executable inode differs from the original program'
    observed_start=$(process_start "$camera_pid") || fail 'camera process exited or became unreadable'
    [ "$observed_start" = "$camera_start" ] || fail 'camera PID was reused or its start time changed'
    # Several PT_LOAD mappings of the same SO are normal. Every matching
    # library mapping must refer to this one tmpfs copy and its inode.
    awk -v expected="$runtime/exposure_status.so" -v expected_inode="$module_inode" '
        $6 ~ /\/exposure_status\.so$/ {
            if ($6 != expected || $5 != expected_inode || NF != 6) bad=1
            else found=1
        }
        END { exit (bad || !found) }
    ' "/proc/$camera_pid/maps" || fail 'original app has not mapped the unique temporary read-only module'
}
marker_matches() {
    marker_value= marker_extra=
    [ -r "$1" ] || return 1
    IFS=' ' read -r marker_value marker_extra < "$1" || return 1
    [ "$marker_value" = "$camera_pid" ] && [ -z "$marker_extra" ]
}

version= model=
if [ -r /etc/version.info ]; then
    { IFS= read -r version && IFS= read -r model; } < /etc/version.info || true
fi
running=$(camera_processes)
printf 'model=%s firmware=%s\n' "$model" "$version"
printf 'camera_pids=%s\n' "${running:-none}"
printf 'module=%s\n' "$MODULE"
if [ -s "$MODULE" ]; then printf 'module_present=yes\n'; else printf 'module_present=no\n'; fi
if [ -n "$running" ]; then printf 'launch_allowed=no (original app is running)\n'; fi
[ "$operation" = launch ] || exit 0

[ "$(id -u)" = 0 ] || fail 'root required'
[ "$model" = NX300M ] && [ "$version" = 1.15 ] || fail 'only NX300M 1.15 is supported'
case $(uname -m) in armv7l) ;; *) fail 'native ARMv7 camera required' ;; esac
[ -x "$APP" ] && [ ! -L "$APP" ] || fail 'original camera executable unavailable'
[ -s "$MODULE" ] && [ ! -L "$MODULE" ] || fail 'read-only module unavailable'
[ ! -s /etc/ld.so.preload ] || fail 'system-wide preloads must be reviewed first'
[ -z "$running" ] || fail 'refusing a second camera instance; no signal was sent'
awk '$2=="/tmp" && $3=="tmpfs" && $4 !~ /(^|,)noexec(,|$)/ {ok=1} END {exit !ok}' /proc/mounts || fail '/tmp must be executable tmpfs'

exec 8>/tmp/nx300m-exposure-launch.lock
flock -n 8 || fail 'another diagnostic launch owns the lock'
[ -z "$(camera_processes)" ] || fail 'camera app appeared; launch cancelled'
runtime=$(mktemp -d /tmp/nx300m-exposure.XXXXXX) || fail 'cannot allocate temporary directory'
: > "$runtime/launch.log"
cp "$MODULE" "$runtime/exposure_status.so" || fail 'cannot copy module'
cmp -s "$MODULE" "$runtime/exposure_status.so" || fail 'temporary module differs'
chmod 400 "$runtime/exposure_status.so"
app_inode=$(stat -L -c '%d:%i' "$APP") || fail 'cannot record original executable identity'
module_inode=$(stat -c '%i' "$runtime/exposure_status.so") || fail 'cannot record temporary module identity'
# The read-only hook atomically replaces this marker. A fresh inode excludes
# an old marker whose numeric PID happens to have been reused this boot.
previous_readonly_inode=absent
if [ -e /var/run/nx300m-exposure-readonly ]; then
    [ -f /var/run/nx300m-exposure-readonly ] || fail 'existing read-only marker is not a regular file'
    # Keep the old inode allocated even when the hook unlinks its old marker.
    exec 9< /var/run/nx300m-exposure-readonly
    previous_readonly_inode=$(stat -L -c '%d:%i' "/proc/$$/fd/9") || fail 'cannot inspect existing read-only marker'
fi
[ -z "$(camera_processes)" ] || fail 'camera app restarted; launch cancelled'
report "temporary_runtime=$runtime"
report 'Launching original app; waiting up to 20 seconds for read-only readiness.'
started_at=$(uptime_seconds) || fail 'monotonic uptime is unavailable'
deadline=$((started_at+20))
(
    # The monitor retains the lock; the daemon must not inherit it. libhib
    # checks getenv for NULL, so HIB="" would still enter hibernation.
    exec 8>&-
    exec 9<&-
    unset HIB LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
    export DISPLAY=${DISPLAY:-:0}
    export LD_PRELOAD="$runtime/exposure_status.so"
    exec "$APP" </dev/null >"$runtime/camera.log" 2>&1
) &
launcher_pid=$!
launcher_done=0
camera_pid= camera_start=
while :; do
    now=$(uptime_seconds) || fail 'cannot read monotonic uptime while monitoring'
    [ "$now" -ge "$started_at" ] || fail 'monotonic uptime moved backwards'
    [ "$now" -lt "$deadline" ] || fail "read-only readiness timed out (camera_pid=${camera_pid:-none}); app may still be running and was not stopped"
    if [ "$launcher_done" -eq 0 ]; then
        # $! identifies only the starter whose exit status is collected. The
        # daemon PID is discovered independently after daemon(1,1) returns.
        if ! process_start "$launcher_pid" >/dev/null; then
            if wait "$launcher_pid"; then launcher_result=0; else launcher_result=$?; fi
            launcher_done=1
            report "launcher_exit=$launcher_result (not readiness)"
            [ "$launcher_result" -eq 0 ] || fail 'original app starter failed; inspect camera.log'
        fi
    fi
    if [ "$launcher_done" -eq 1 ]; then
        candidates=$(camera_processes)
        if [ -z "$camera_pid" ]; then
            if [ -n "$candidates" ]; then
                set -- $candidates
                [ "$#" -eq 1 ] || fail 'multiple original camera instances appeared; no signal was sent'
                camera_pid=$1
                camera_start=$(process_start "$camera_pid") || fail 'discovered camera already exited'
                report "camera_pid=$camera_pid starttime=$camera_start executable_identity=$app_inode"
            fi
        fi
        if [ -n "$camera_pid" ]; then
            verify_camera
            if marker_matches /var/run/nx300m-exposure-ready && marker_matches /var/run/nx300m-exposure-readonly; then
                readonly_inode=$(stat -L -c '%d:%i' /var/run/nx300m-exposure-readonly 2>/dev/null) || fail 'cannot inspect current read-only marker'
                if [ "$readonly_inode" != "$previous_readonly_inode" ]; then
                    # Recheck identity and mappings after reading both markers.
                    verify_camera
                    report "read_only_ready=yes camera_pid=$camera_pid module=$runtime/exposure_status.so"
                    report "camera_log=$runtime/camera.log"
                    exit 0
                fi
            fi
        fi
    fi
    sleep 1
done
