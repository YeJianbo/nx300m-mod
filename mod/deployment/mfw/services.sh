#!/bin/sh
# Independent services only: no camera injection, restart, or Wi-Fi setup.
. "${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}/common.sh"
[ -e "$ROOT/DISABLED" ] && exit 0
exec 7>"$STATE/mfw-entry.lock" || exit 1
flock -n 7 || { fn_note 'services exit: entry lock busy'; exit 0; }
diag_base=$STATE/diagnostics-$BOOT_ID-$$
DIAG=$diag_base
diag_suffix=0
# Snapshot restore can reuse both boot ID and PID while SD logs survive.
while [ -e "$DIAG" ]; do
    diag_suffix=$((diag_suffix+1))
    DIAG=$diag_base-$diag_suffix
done
mkdir "$DIAG" || { fn_note 'services exit: diagnostic dir mkdir failed'; exit 1; }
printf '%s\n' "$DIAG" > "$STATE/latest-diagnostic"
printf '\nNX300M MFW independent services v1\n'
date
id
printf 'DISPLAY=%s listen=%s\n' "${DISPLAY:-:0}" "${NX_REMOTE_LISTEN:-127.0.0.1:8781}"
fn_note 'services begin'
full_diagnostics=0
if [ -e "$ROOT/TRACE_HTTP" ] || [ "${NX_MFW_FULL_DIAGNOSTICS:-0}" = 1 ]; then
    full_diagnostics=1
    printf 'requested\n' > "$DIAG/full-diagnostics.state"
else
    printf 'skipped\n' > "$DIAG/full-diagnostics.state"
    for skipped in camera-info camera-status mods-status network-status gallery-root camera-loading; do
        printf 'skipped\n' > "$DIAG/$skipped.state"
    done
fi

network_report() {
    printf '\n== Network addresses (read only) ==\n'
    if command -v ip >/dev/null 2>&1; then
        ip addr show
    elif command -v ifconfig >/dev/null 2>&1; then
        ifconfig -a
    fi
    [ ! -r /proc/net/route ] || cat /proc/net/route
}
[ "$full_diagnostics" = 0 ] || network_report

# A deployment marker requests replacement of this service only, including
# an old daemon restored by the camera's snapshot. stop.sh checks its identity.
restart_service=0
if [ -e "$ROOT/RESTART_SERVICE" ]; then
    fn_note 'services: RESTART_SERVICE requested, stopping owned daemon'
    /bin/sh "$ROOT/stop.sh" 7>&- || { fn_note 'services: stop for restart failed'; exit 1; }
    rm -f "$ROOT/RESTART_SERVICE" || { fn_note 'services: restart marker removal failed'; exit 1; }
    fn_note 'services: restart marker cleared'
    restart_service=1
fi
unset NX_REMOTE_TRACE_HTTP http_proxy HTTP_PROXY https_proxy HTTPS_PROXY all_proxy ALL_PROXY
if [ -e "$ROOT/TRACE_HTTP" ]; then
    export NX_REMOTE_TRACE_HTTP=1
fi
# The startup script has its own bounded wait and exact-process ownership.
# Close this worker's entry lock in its descendants.
if [ "$restart_service" = 1 ]; then
    NX300M_AUTO_START=0 /bin/sh "$ROOT/start.sh" 7>&-
else
    /bin/sh "$ROOT/start.sh" 7>&-
fi
result=$?
fn_note "services: start exit=$result"
printf 'start_exit=%s\n' "$result"
if [ "$result" -ne 0 ]; then
    printf 'Startup failed; inspect remote.log and supervisor.log. No camera restart attempted.\n'
    exit "$result"
fi

inspect_remote() {
    record_alive || return 0
    printf '\n== Owned remote process %s ==\n' "$REC_PID"
    for item in status wchan syscall stack; do
        printf '\n-- %s --\n' "$item"
        [ ! -r "/proc/$REC_PID/$item" ] || cat "/proc/$REC_PID/$item"
    done
    ls -l "/proc/$REC_PID/fd"
    printf '\n-- TCP sockets --\n'
    cat /proc/net/tcp
}

trace_pid=
if [ -e "$ROOT/TRACE_HTTP" ]; then
    inspect_remote > "$DIAG/http-process-before.log" 2>&1
    if [ -x /usr/bin/strace ] && command -v timeout >/dev/null 2>&1 && record_alive; then
        # Stock BusyBox 1.20.2 requires -t; bare 8 is treated as a program.
        timeout -t 8 -s INT /usr/bin/strace -tt -T -s 128 -p "$REC_PID" -o "$DIAG/http-syscalls.log" 7>&- &
        trace_pid=$!
    else
        printf 'System-call trace unavailable; process snapshots still collected.\n'
    fi
fi
http_failed=0
if command -v wget >/dev/null 2>&1; then
    probes=health
    if [ "$full_diagnostics" = 1 ]; then probes="$probes camera/info camera/status mods/status network/status"; fi
    for probe in $probes; do
        case "$probe" in
            health) output=health.json ;;
            camera/info) output=camera-info.json ;;
            camera/status) output=camera-status.json ;;
            mods/status) output=mods-status.json ;;
            network/status) output=network-status.json ;;
        esac
        wget -T 3 -Y off -O "$DIAG/$output.pending" "http://127.0.0.1:8781/api/v1/$probe" 7>&- 2>"$DIAG/$output.client.log"
        probe_result=$?
        printf 'http_probe=%s exit=%s\n' "$probe" "$probe_result"
        if [ "$probe_result" = 0 ]; then
            mv "$DIAG/$output.pending" "$DIAG/$output"
        else
            http_failed=1
        fi
    done
    if [ "$full_diagnostics" = 1 ]; then
        wget -T 3 -Y off -O "$DIAG/gallery-root.json.pending" 'http://127.0.0.1:8781/cgi-bin/dirlist' 7>&- 2>"$DIAG/gallery-root.json.client.log"
        probe_result=$?
        printf 'http_probe=gallery-root exit=%s\n' "$probe_result"
        if [ "$probe_result" = 0 ]; then
            mv "$DIAG/gallery-root.json.pending" "$DIAG/gallery-root.json"
        else
            http_failed=1
        fi
    fi
    if [ "$http_failed" = 0 ]; then
        printf 'http-probes-passed\n' > "$DIAG/http-probes.state"
    else
        printf 'http-probes-failed\n' > "$DIAG/http-probes.state"
    fi
else
    printf 'HTTP probes skipped: wget unavailable.\n'
    printf 'http-probes-unavailable\n' > "$DIAG/http-probes.state"
fi
if [ -n "$trace_pid" ]; then
    wait "$trace_pid"
    printf 'trace_exit=%s (inspect trace output; nonzero is not a pass)\n' "$?"
fi
if [ -e "$ROOT/TRACE_HTTP" ]; then
    inspect_remote > "$DIAG/http-process-after.log" 2>&1
    rm -f "$ROOT/TRACE_HTTP"
fi
cp "$LOG" "$DIAG/remote.log"
camera_loading_report() (
    # Capture the installed MFW identity and the actual app's mappings. These
    # small files let offline analysis use this camera's library, not an old mod.
    set -eu
    loading=$DIAG/camera-loading
    mkdir "$loading"
    cp /etc/version.info "$loading/version.info"
    cp /usr/lib/libmisc.so "$loading/libmisc.so"
    cmp -s /usr/lib/libmisc.so "$loading/libmisc.so"
    readlink -f /usr/lib/libmisc.so > "$loading/libmisc.path"
    stat -L -c '%d:%i %s %n' /usr/lib/libmisc.so > "$loading/libmisc.stat"
    cat /proc/uptime > "$loading/uptime"
    count=0
    camera=
    for entry in /proc/[0-9]*/exe; do
        target=$(readlink "$entry" 2>/dev/null) || continue
        case $target in
            /usr/bin/di-camera-app-nx300|/usr/bin/di-camera-app)
                camera=${entry#/proc/}; camera=${camera%/exe}
                count=$((count+1)) ;;
        esac
    done
    printf 'camera_count=%s\n' "$count"
    [ "$count" -eq 1 ] || exit 1
    printf '%s\n' "$camera" > "$loading/camera.pid"
    before=$(cat "/proc/$camera/stat")
    for item in status cmdline stat maps; do
        cat "/proc/$camera/$item" > "$loading/camera.$item"
    done
    readlink "/proc/$camera/exe" > "$loading/camera.exe"
    stat -L -c '%d:%i %s %n' "/proc/$camera/exe" > "$loading/camera-exe.stat"
    after=$(cat "/proc/$camera/stat")
    # Compare starttime only: CPU times and other stat fields change normally.
    before=${before##*) }; after=${after##*) }
    set -- $before; shift 19; before_start=$1
    set -- $after; shift 19
    [ "$before_start" = "$1" ] || exit 1
    printf 'camera_pid=%s starttime=%s\n' "$camera" "$before_start"
    printf 'copied_installed_libmisc=yes camera_commands_sent=no\n'
)
if [ "$full_diagnostics" = 1 ]; then
    camera_loading_report > "$DIAG/camera-loading.log" 2>&1
    loading_result=$?
    if [ "$loading_result" -eq 0 ]; then
        printf 'camera-loading-readonly-captured\n' > "$DIAG/camera-loading.state"
    else
        printf 'camera-loading-capture-failed\n' > "$DIAG/camera-loading.state"
    fi
fi
printf 'Independent service entry finished. This does not verify phone connectivity or imaging.\n'
