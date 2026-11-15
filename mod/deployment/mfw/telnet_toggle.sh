#!/bin/sh
# MOD Telnet debug switch (2026-11-11).
#
# Server: busybox-static 1.37.0 (GPL-2.0), shipped as bin/busybox.arm; only
# the telnetd/telnet/mkpasswd path is used here (the stock camera busybox has
# no telnetd). Authentication runs through bin/telnet_login.arm, which
# verifies a sha512-crypt hash from the MOD state directory directly -- never
# the camera's passwordless /etc/shadow root entry, and never "-l /bin/sh".
#
# Rules (task docs/OVERNIGHT_2026-11-11-wireless-telnet.md):
#  - uses an existing network (e.g. the MOD hotspot); never creates one
#  - default off; opening the hotspot does not open Telnet
#  - binds only the camera's own address, never a wildcard
#  - one service at most; stopping is owner-checked via /proc/<pid>/exe
#  - when the MOD hotspot stops, its Telnet is closed too; unknown services
#    on other networks are untouched
#  - the password is generated once, stored 0600, shown in the UI on request,
#    and never written to logs
#
# Usage: telnet_toggle.sh enable|disable|toggle|status|state|password
set -u
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
umask 077
fail() { printf 'MOD telnet: %s\n' "$*" >&2; exit 1; }

action=${1:-status}
[ "$#" -le 1 ] || fail 'unexpected arguments'
case $action in
    enable|disable|toggle|on|off|status|state|password) ;;
    *) fail 'use enable, disable, toggle, status, state or password' ;;
esac
case $action in on) action=enable ;; off) action=disable ;; esac

ROOT=${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}
RUN=/var/run/nx300m-telnet
STATE_FILE=$RUN/state
OWNER_FILE=$RUN/owner
LISTEN_FILE=$RUN/listen
LOCK_FILE=$RUN/lock
PORT=23
# The camera's own data-network interface. wlan0 is the radio (hotspot or
# station); mlan0 is the historical SoftAP name on other Samsung models and
# is only used as a fallback when wlan0 has no address.
BIND_INTERFACE=${NX_TELNET_INTERFACE:-wlan0}
BUSYBOX=${NX_TELNET_BUSYBOX:-$ROOT/bin/busybox.arm}
LOGIN_BIN=$ROOT/bin/telnet_login.arm
# Identity checks compare /proc/<pid>/exe with these paths; canonicalize both
# so a symlinked runtime directory (for example /var/run -> /run) cannot
# break the match. On the camera $ROOT is a plain SD directory and this is a
# no-op.
BUSYBOX=$(readlink -f "$BUSYBOX" 2>/dev/null) || :
LOGIN_BIN=$(readlink -f "$LOGIN_BIN" 2>/dev/null) || :

interface_address() {
    ip -4 addr show dev "$BIND_INTERFACE" 2>/dev/null | awk '/inet / {sub(/\/.*/,"",$2); print $2; exit}'
}

# The owner file records our telnetd PID and /proc start time; it is accepted while the
# process still resolves to our busybox binary AND its command line is still
# a telnetd. A recycled PID running some other busybox applet (for example a
# mkpasswd call) is not ours to signal.
telnet_owner_pid() {
    [ -r "$OWNER_FILE" ] || return 1
    IFS=' ' read -r pid recorded_start extra < "$OWNER_FILE" || return 1
    case $pid in ''|*[!0-9]*) return 1 ;; esac
    case $recorded_start in ''|*[!0-9]*) return 1 ;; esac
    [ -z "$extra" ] && process_stat "$pid" && [ "$process_start" = "$recorded_start" ] || return 1
    [ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$BUSYBOX" ] || return 1
    cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
    case $cmd in *"telnetd"*) ;; *) return 1 ;; esac
    printf '%s' "$pid"
}

listening_on() {
    # Check /proc/net/tcp for a LISTEN (0A) socket on our port bound to the
    # given address. The kernel prints addresses as uppercase hex in
    # little-endian byte order, so 127.0.0.1 becomes 0100007F.
    hex=$(printf '%02X%02X%02X%02X' $(echo "$1" | tr '.' ' ' | awk '{print $4" "$3" "$2" "$1}'))
    awk -v a="$hex:0017" 'BEGIN{ok=0} $2==a && $4=="0A" {ok=1} END{exit !ok}' /proc/net/tcp 2>/dev/null
}

write_state() { mkdir -p "$RUN" 2>/dev/null || :; printf '%s\n' "${1}${2:+:$2}" > "$STATE_FILE.tmp" && mv -f "$STATE_FILE.tmp" "$STATE_FILE" 2>/dev/null || :; }
read_state() { [ -r "$STATE_FILE" ] || { printf 'off\n'; return; }; IFS= read -r v < "$STATE_FILE" || v=off; printf '%s\n' "$v"; }

password_file=$ROOT/.state/telnet-password
hash_file=$ROOT/.state/telnet-hash

generated_password() {
    # cat instead of read: the file is written without a trailing newline,
    # which makes `read` return nonzero even with content in hand.
    if [ -s "$password_file" ]; then
        v=$(cat "$password_file" 2>/dev/null) && [ -n "$v" ] && { printf '%s' "$v"; return; }
    fi
    od -An -N6 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n'
}

# --- credentials -----------------------------------------------------------
ensure_credentials() {
    # Create the password and its hash on first enable. The hash lives in a
    # MOD-owned state file (0600); the camera's /etc/shadow is never read,
    # modified or parsed. The hash is regenerated whenever the password file
    # is newer, so a rotated password never authenticates with a stale hash.
    mkdir -p "$ROOT/.state" 2>/dev/null || :
    if [ ! -s "$password_file" ]; then
        umask 077
        generated_password > "$password_file" || :
    fi
    [ -s "$password_file" ] || fail 'cannot create telnet password'
    chmod 600 "$password_file" 2>/dev/null || :
    password=$(cat "$password_file" 2>/dev/null)
    [ -n "$password" ] || fail 'cannot read telnet password'
    case ${#password} in
        0|1|2|3|4|5|6|7) fail 'stored telnet password too short; delete .state/telnet-password' ;;
    esac
    if [ -s "$hash_file" ] && [ -n "$(find "$password_file" -newer "$hash_file" 2>/dev/null)" ]; then
        rm -f "$hash_file"
    fi
    if [ ! -s "$hash_file" ]; then
        # -P 0 reads the password from stdin: the plaintext never appears in
        # the transient mkpasswd argv, only in this pipe and the 0600 file.
        cat "$password_file" | "$BUSYBOX" mkpasswd -m sha512 -P 0 > "$hash_file" 2>/dev/null \
            || fail 'mkpasswd unavailable'
        chmod 600 "$hash_file" 2>/dev/null || :
    fi
    [ -s "$hash_file" ] || fail 'cannot create telnet password hash'
    hash_head=$(head -c 3 "$hash_file" 2>/dev/null)
    [ "$hash_head" = '$6$' ] || fail 'stored hash is not sha512-crypt'
}

# --- stop ------------------------------------------------------------------
# Splitting after the last parenthesis handles comm strings with spaces or
# parentheses. Field 22 (word 20 here) binds a PID to one process lifetime.
process_stat() {
    IFS= read -r process_line 2>/dev/null < "/proc/$1/stat" || return 1
    set -- ${process_line##*) }
    [ "$#" -ge 20 ] || return 1
    process_state=$1 process_parent=$2
    shift 19
    process_start=$1
}
process_matches() { # pid:start
    process_stat "${1%:*}" && [ "$process_start" = "${1#*:}" ] &&
        [ "$process_state" != Z ] && [ "$process_state" != X ]
}
resume_stop_snapshot() {
    for victim in $stop_victims; do
        process_matches "$victim" && kill -CONT "${victim%:*}" 2>/dev/null || :
    done
}
stop_service() {
    pid=$(telnet_owner_pid) || pid=
    removed=
    if [ -n "$pid" ]; then
        process_stat "$pid" || return 1
        stop_victims=
        pending="$pid:$process_start"
        # Freeze only this owned tree before walking it: the listener cannot
        # accept another client and a captured session cannot fork between
        # enumeration and signalling. Sessions may already be login/sh or
        # other executables, so ownership follows ancestry, not exe names.
        # Interrupted cleanup resumes every still-matching frozen process.
        trap 'resume_stop_snapshot' 0
        trap 'exit 1' HUP INT TERM
        while [ -n "$pending" ]; do
            set -- $pending
            current=$1; shift; pending="$*"
            process_matches "$current" || continue
            stop_victims="$current $stop_victims"
            kill -STOP "${current%:*}" 2>/dev/null || continue
            waited=0
            while process_matches "$current" && [ "$process_state" != T ] && [ "$process_state" != t ]; do
                if [ "$waited" -ge 20 ]; then
                    resume_stop_snapshot
                    trap - 0 HUP INT TERM
                    write_state failed stop-timeout
                    return 1
                fi
                sleep 0.01; waited=$((waited+1))
            done
            for pid_dir in /proc/[0-9]*; do
                sweep_pid=${pid_dir#/proc/}
                process_stat "$sweep_pid" || continue
                [ "$process_parent" = "${current%:*}" ] || continue
                pending="$pending $sweep_pid:$process_start"
            done
        done
        # Children precede their parent in this snapshot. Lifetime tokens
        # remain valid across exec and reparenting but reject PID reuse.
        for victim in $stop_victims; do
            process_matches "$victim" || continue
            kill -TERM "${victim%:*}" 2>/dev/null || :
            process_matches "$victim" && kill -CONT "${victim%:*}" 2>/dev/null || :
        done
        waited=0
        while [ "$waited" -lt 20 ]; do
            remaining=
            for victim in $stop_victims; do
                process_matches "$victim" && remaining="$remaining $victim" || :
            done
            [ -n "$remaining" ] || break
            sleep 0.1; waited=$((waited+1))
        done
        for victim in $stop_victims; do
            process_matches "$victim" && kill -KILL "${victim%:*}" 2>/dev/null || :
        done
        resume_stop_snapshot
        trap - 0 HUP INT TERM
        removed=telnetd+sessions
    fi
    rm -f "$OWNER_FILE" "$LISTEN_FILE" 2>/dev/null || :
    write_state off
    printf '%s' "${removed:-none}"
}

# --- status ----------------------------------------------------------------
report_status() {
    state=$(read_state)
    address=$(interface_address)
    pid=$(telnet_owner_pid) || pid=
    # The real verdict is the socket, not the state file.
    if [ -n "$pid" ] && [ -n "$address" ] && listening_on "$address"; then
        effective=on
    elif [ -z "$pid" ]; then
        effective=off
    else
        effective=degraded
    fi
    printf 'TELNET state=%s effective=%s interface=%s address=%s port=%s\n' \
        "$state" "$effective" "$BIND_INTERFACE" "${address:-none}" "$PORT"
    if [ "$action" = password ] && [ "$effective" = on ]; then
        # UI-only output; this script never logs the password itself.
        ensure_credentials
        printf 'TELNET_CREDENTIALS user=root password=%s port=%s\n' "$(generated_password)" "$PORT"
    fi
    [ "$effective" = on ] || return 1
    return 0
}

# --- enable ----------------------------------------------------------------
enable_service() {
    [ -x "$BUSYBOX" ] || { write_state failed no-busybox; fail "busybox binary missing: $BUSYBOX"; }
    [ -x "$LOGIN_BIN" ] || { write_state failed no-login-tool; fail "telnet login tool missing: $LOGIN_BIN"; }
    if pid=$(telnet_owner_pid) && [ -n "$pid" ]; then
        printf 'TELNET_ENABLE already=on pid=%s\n' "$pid"
        return 0
    fi
    # A foreign telnet listener on our port: never kill it, just report.
    address=$(interface_address)
    if [ -z "$address" ]; then
        # wlan0 without an address may mean the SoftAP name differs; try mlan0.
        BIND_INTERFACE=mlan0
        address=$(interface_address)
    fi
    [ -n "$address" ] || { write_state failed no-network; fail "no address on $BIND_INTERFACE; enable Wi-Fi or the MOD hotspot first"; }
    if awk 'BEGIN{ok=0} $2 ~ /:0017$/ && $4=="0A" {ok=1} END{exit !ok}' /proc/net/tcp 2>/dev/null; then
        write_state failed port-in-use
        fail "port $PORT already has a listener from another service"
    fi
    if awk 'BEGIN{ok=0} $2 ~ /:0017$/ && $4=="0A" {ok=1} END{exit !ok}' /proc/net/tcp6 2>/dev/null; then
        write_state failed port-in-use
        fail "port $PORT already has an IPv6 listener from another service"
    fi
    ensure_credentials

    mkdir -p "$RUN" || { write_state failed runtime-dir; fail 'cannot create runtime dir'; }
    exec 9>>"$LOCK_FILE" || { write_state failed lock; fail 'cannot open lock file'; }
    flock -n 9 || { write_state failed busy; fail 'another telnet request is running'; }
    write_state starting
    # busybox login would consult the camera's /etc/shadow, where root has an
    # EMPTY password field - the exact configuration this switch must not
    # expose, and "-l /bin/sh" would skip authentication entirely.
    # Instead telnetd runs our dedicated login tool, which reads the sha512
    # hash from the 0600 state file via the environment: the hash never
    # appears in argv, and the camera's shadow is untouched. -K closes the
    # connection as soon as the login tool exits (three failures or logout).
    # The daemon is spawned from a subshell that closes the lock fd first:
    # telnetd outlives this script and must not keep the telnet lock held
    # (a later disable would block forever on our own service).
    ( exec 9>&-
      NX_TELNET_HASH_FILE="$hash_file" \
        "$BUSYBOX" telnetd -p "$PORT" -b "$address" -K -l "$LOGIN_BIN" 2>"$RUN/start.log" ) || {
        write_state failed telnetd
        fail "telnetd failed to start (see RAM start.log)"
    }
    # telnetd daemonizes itself; find our listener's pid by exe identity and
    # its telnetd command line.
    owner=
    for pid_dir in /proc/[0-9]*; do
        pid=${pid_dir#/proc/}
        [ "$(readlink "$pid_dir/exe" 2>/dev/null)" = "$BUSYBOX" ] || continue
        cmd=$(tr '\0' ' ' < "$pid_dir/cmdline" 2>/dev/null)
        case $cmd in
            *"telnetd"*" -p $PORT "*"$address"*) owner=$pid; break ;;
        esac
    done
    [ -n "$owner" ] || { stop_service >/dev/null; write_state failed no-pid; fail 'cannot identify started telnetd'; }
    process_stat "$owner" || { write_state failed owner-exited; fail 'started telnetd exited'; }
    printf '%s %s\n' "$owner" "$process_start" > "$OWNER_FILE.tmp" && mv -f "$OWNER_FILE.tmp" "$OWNER_FILE"
    listening_on "$address" || { stop_service >/dev/null; write_state failed no-listener; fail 'listener did not appear'; }
    printf '%s %s\n' "$address" "$PORT" > "$LISTEN_FILE.tmp" && mv -f "$LISTEN_FILE.tmp" "$LISTEN_FILE"
    write_state on
    printf 'TELNET_ENABLE address=%s port=%s pid=%s\n' "$address" "$PORT" "$owner"
    return 0
}

# --- toggle ----------------------------------------------------------------
do_toggle() {
    pid=$(telnet_owner_pid) || pid=
    if [ -n "$pid" ]; then
        removed=$(stop_service) || fail 'cannot stop owned telnet sessions; retry disable'
        printf 'TELNET_DISABLE removed=%s\n' "$removed"
    else
        enable_service
    fi
}

case $action in
    enable)  enable_service ;;
    disable)
        mkdir -p "$RUN" 2>/dev/null || :
        exec 9>>"$LOCK_FILE" || fail 'cannot open lock file'
        flock 9 || fail 'cannot acquire telnet lock'
        # Verify the recorded listener really disappeared after our service
        # is gone; anything still on that port is not ours and is only
        # reported, never killed.
        listen_record=
        if [ -r "$LISTEN_FILE" ]; then IFS= read -r listen_record < "$LISTEN_FILE" || listen_record=; fi
        listen_address=${listen_record% *}
        removed=$(stop_service) || fail 'cannot stop owned telnet sessions; retry disable'
        listener=none
        case $listen_address in
            [0-9]*.[0-9]*.[0-9]*.[0-9]*)
                if listening_on "$listen_address"; then listener="foreign:$listen_address"; fi ;;
        esac
        printf 'TELNET_DISABLE removed=%s listener=%s\n' "$removed" "$listener" ;;
    toggle)
        mkdir -p "$RUN" 2>/dev/null || :
        exec 9>>"$LOCK_FILE" || fail 'cannot open lock file'
        flock 9 || fail 'cannot acquire telnet lock'
        do_toggle ;;
    status|state|password) report_status ;;
esac
