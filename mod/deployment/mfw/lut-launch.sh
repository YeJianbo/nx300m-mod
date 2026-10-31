#!/bin/sh
# A new app exec can preload this adapter; the MFW child shell cannot inject it.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() {
    printf 'NX300M LUT: %s\n' "$*" >&2
    if [ -n "${runtime:-}" ]; then
        printf 'ERROR: %s\n' "$*" >> "$runtime/launch.log" || :
        printf 'Logs retained: %s\n' "$runtime" >&2
    fi
    exit 1
}
report() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$runtime/launch.log"; }
[ "$#" -le 1 ] || fail 'use inspect or launch'
operation=${1:-inspect}
case $operation in inspect|launch) ;; *) fail 'use inspect or launch' ;; esac
ROOT=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
MODULE=$ROOT/lib/lut_port.so
ENGINE=$ROOT/bin/lut_engine.arm
APP=/usr/bin/di-camera-app-nx300

camera_processes() {
    for entry in /proc/[0-9]*/exe; do
        target=$(readlink "$entry" 2>/dev/null) || continue
        case $target in
            */di-camera-app-nx300|*/di-camera-app-nx300\ \(deleted\)|*/di-camera-app|*/di-camera-app\ \(deleted\))
                pid=${entry#/proc/}; printf '%s\n' "${pid%/exe}" ;;
        esac
    done
}
process_start() {
    IFS= read -r value 2>/dev/null < "/proc/$1/stat" || return 1
    fields=${value##*) }; set -- $fields
    [ "$#" -ge 20 ] && [ "$1" != Z ] || return 1
    shift 19
    case $1 in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$1"
}
uptime_seconds() {
    IFS=' ' read -r value rest < /proc/uptime || return 1
    value=${value%%.*}
    case $value in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$value"
}
verify_camera() {
    [ "$(camera_processes)" = "$camera_pid" ] || fail 'camera PID changed or became multiple instances; no signal was sent'
    [ "$(readlink "/proc/$camera_pid/exe")" = "$APP" ] || fail 'camera executable changed'
    [ "$(stat -L -c '%d:%i' "/proc/$camera_pid/exe")" = "$app_inode" ] || fail 'camera executable inode changed'
    [ "$(process_start "$camera_pid")" = "$camera_start" ] || fail 'camera exited or PID was reused'
    awk -v expected="$runtime/lut_port.so" -v inode="$module_inode" '
        $6 ~ /\/lut_port\.so$/ {
            if ($6 != expected || $5 != inode || NF != 6) bad=1
            else found=1
        }
        END {exit (bad || !found)}
    ' "/proc/$camera_pid/maps" || fail 'camera has not mapped the unique RAM adapter'
}
marker_matches() {
    marker_pid= marker_root= marker_extra=
    [ -r /var/run/nx300m-lut-hook ] || return 1
    IFS=' ' read -r marker_pid marker_root marker_extra < /var/run/nx300m-lut-hook || return 1
    [ "$marker_pid" = "$camera_pid" ] && [ "$marker_root" = "$ROOT" ] && [ -z "$marker_extra" ]
}

version= model=
if [ -r /etc/version.info ]; then
    { IFS= read -r version && IFS= read -r model; } < /etc/version.info || :
fi
running=$(camera_processes)
printf 'model=%s firmware=%s\ncamera_pids=%s\nlut_data_root=%s\n' "$model" "$version" "${running:-none}" "$ROOT"
if [ -s "$MODULE" ]; then printf 'module_present=yes\n'; else printf 'module_present=no\n'; fi
if [ -s "$ENGINE" ]; then printf 'engine_present=yes\n'; else printf 'engine_present=no\n'; fi
for pid in $running; do
    if [ ! -r "/proc/$pid/maps" ]; then
        printf 'camera_%s_lut_mapped=unknown\n' "$pid"
    elif awk '$6 ~ /\/lut_port\.so$/ {found=1} END {exit !found}' "/proc/$pid/maps"; then
        printf 'camera_%s_lut_mapped=yes\n' "$pid"
    else
        printf 'camera_%s_lut_mapped=no\n' "$pid"
    fi
done
if [ -n "$running" ]; then printf 'launch_allowed=no (original app is running)\n'; fi
[ "$operation" = launch ] || exit 0

[ "$(id -u)" = 0 ] || fail 'root required'
[ "$model" = NX300M ] && [ "$version" = 1.15 ] || fail 'only NX300M 1.15 is supported'
case $(uname -m) in armv7l) ;; *) fail 'native ARMv7 required' ;; esac
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'data root uses unsupported characters' ;; esac
[ "${#ROOT}" -le 160 ] || fail 'data root exceeds 160 bytes'
[ -x "$APP" ] && [ ! -L "$APP" ] || fail 'original camera executable unavailable'
[ -s "$MODULE" ] && [ ! -L "$MODULE" ] || fail 'adapter unavailable'
[ -s "$ENGINE" ] && [ ! -L "$ENGINE" ] || fail 'engine unavailable'
[ -s "$ROOT/looks.txt" ] && [ -d "$ROOT/looks" ] || fail 'LUT catalogue unavailable'
[ ! -s /etc/ld.so.preload ] || fail 'system-wide preloads must be reviewed first'
[ -z "$running" ] || fail 'refusing a second camera instance; no signal was sent'
ram_tmp=$(CDPATH= cd -P /tmp && pwd -P) || fail '/tmp unavailable'
awk -v path="$ram_tmp" '
    $2=="/" || path==$2 || index(path,$2 "/")==1 {
        if (length($2)>best) {best=length($2); type=$3; options=$4}
    }
    END {exit !(type=="tmpfs" && options ~ /(^|,)rw(,|$)/ && options !~ /(^|,)noexec(,|$)/)}
' /proc/mounts || fail '/tmp must resolve to writable executable tmpfs'
exec 8>"$ram_tmp/nx300m-lut-launch.lock"
flock -n 8 || fail 'another LUT launcher owns the lock'
[ -z "$(camera_processes)" ] || fail 'camera appeared; launch cancelled'
runtime=$(mktemp -d "$ram_tmp/nx300m-lut.XXXXXX") || fail 'cannot allocate RAM directory'
: > "$runtime/launch.log"
cp "$MODULE" "$runtime/lut_port.so" && cmp -s "$MODULE" "$runtime/lut_port.so" || fail 'RAM adapter copy differs'
cp "$ENGINE" "$runtime/lut_engine.arm" && cmp -s "$ENGINE" "$runtime/lut_engine.arm" || fail 'RAM engine copy differs'
chmod 400 "$runtime/lut_port.so"
chmod 500 "$runtime/lut_engine.arm"
app_inode=$(stat -L -c '%d:%i' "$APP") || fail 'cannot inspect original executable'
module_inode=$(stat -c '%i' "$runtime/lut_port.so") || fail 'cannot inspect RAM adapter'
[ -z "$(camera_processes)" ] || fail 'camera appeared; launch cancelled'
report "temporary_runtime=$runtime"
report "lut_data_root=$ROOT"
report 'Launching one new original app; existing selection remains subject to the stock callback.'
started_at=$(uptime_seconds) || fail 'monotonic uptime unavailable'
deadline=$((started_at+20))
(
    exec 8>&-
    unset HIB LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID
    export DISPLAY=${DISPLAY:-:0} NX300M_MOD_ROOT="$ROOT" LD_PRELOAD="$runtime/lut_port.so"
    cd "$runtime" || exit 1
    exec "$APP" </dev/null >"$runtime/camera.log" 2>&1
) &
launcher_pid=$!
launcher_done=0
camera_pid= camera_start=
while :; do
    now=$(uptime_seconds) || fail 'cannot read monotonic uptime'
    [ "$now" -ge "$started_at" ] || fail 'monotonic uptime moved backwards'
    [ "$now" -lt "$deadline" ] || fail "adapter readiness timed out; app may still be running and was not stopped (camera_pid=${camera_pid:-none})"
    if [ "$launcher_done" -eq 0 ] && ! process_start "$launcher_pid" >/dev/null; then
        if wait "$launcher_pid"; then result=0; else result=$?; fi
        launcher_done=1
        report "launcher_exit=$result (not readiness)"
        [ "$result" -eq 0 ] || fail 'original app starter failed; inspect camera.log'
    fi
    if [ "$launcher_done" -eq 1 ]; then
        candidates=$(camera_processes)
        if [ -z "$camera_pid" ] && [ -n "$candidates" ]; then
            set -- $candidates
            [ "$#" -eq 1 ] || fail 'multiple camera instances appeared; no signal was sent'
            camera_pid=$1
            camera_start=$(process_start "$camera_pid") || fail 'camera already exited'
        fi
        if [ -n "$camera_pid" ] && marker_matches; then
            verify_camera
            report "adapter_ready=yes camera_pid=$camera_pid starttime=$camera_start"
            report 'This confirms loading, not LUT image processing.'
            exit 0
        fi
    fi
    sleep 1
done
