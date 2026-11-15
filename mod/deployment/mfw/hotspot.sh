#!/bin/sh
# MOD independent Wi-Fi hotspot control (2026-11-11).
#
# Reuses the camera's own driver loading, hostapd and udhcpd from the stock
# 1.15 wlan.sh toolchain, but as a small owner-checked control instead of the
# script's broad killall-based lifecycle. The phone then joins the SSID and
# reaches the existing HTTP service on 8781.
#
# Design rules (task docs/OVERNIGHT_2026-11-11-wireless-telnet.md):
#  - default off; no boot-time scanning, polling or auto-restore
#  - states: off / starting:<stage> / on / stopping / failed:<stage>
#  - only processes and resources this feature created are stopped; PID
#    identity is checked via /proc/<pid>/exe before any signal
#  - if an unknown program already owns the radio (RVF or anything else),
#    report the conflict and leave it alone
#  - no config is written to the original firmware; runtime configs live in
#    a private /var/run directory; the password is never logged
#  - concurrent requests serialize on a flock: a second start is refused,
#    a stop waits for the in-flight start and then tears everything down
#
# Isolated tests drive this exact script inside a private mount/PID/NET
# namespace with bind-mounted stub binaries; there is no fixture mode here.
#
# Usage: hotspot.sh start|stop|status|state|ssid|credentials
set -u
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
umask 077
fail() { printf 'MOD hotspot: %s\n' "$*" >&2; exit 1; }

command=${1:-status}
[ "$#" -le 2 ] || fail 'unexpected arguments'
case $command in
    start|stop|status|state|ssid|credentials) ;;
    *) fail 'use start, stop, status, state, ssid or credentials' ;;
esac

ROOT=${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}
RUN=/var/run/nx300m-hotspot
RUNDIR=$RUN/runtime
STATE_FILE=$RUN/state
OWNER_FILE=$RUN/owner
ADDRESS_FILE=$RUN/address
SSID_FILE=$RUN/ssid
LOCK_FILE=$RUN/lock
# wlan0 unless the harness stubs a different interface.
INTERFACE=${NX_HOTSPOT_INTERFACE:-wlan0}
AP_ADDRESS=${NX_HOTSPOT_ADDRESS:-192.168.0.1}
AP_NETMASK=255.255.255.0
NET_PREFIX=${NX_HOTSPOT_PREFIX:-192.168.0}

# The stock wlan.sh generation path. We do NOT call wlan.sh directly: its
# clean_* helpers kill every hostapd/udhcpd on the system by name. The module
# arguments mirror driver_load() with the camera's default regdom (00).
HOSTAPD=${NX_HOTSPOT_HOSTAPD:-/usr/sbin/hostapd}
UDHCPD=${NX_HOTSPOT_UDHCPD:-/usr/sbin/udhcpd}
DRIVER1=/lib/modules/cfg80211.ko
DRIVER2=/lib/modules/ath6kl_sdio.ko

password_from_config() {
    # A generated key keeps the first version simple and auditable: 16 hex
    # chars from 8 random bytes, seeded once and stored 0600 next to the
    # other MOD state on the SD card.
    state_dir=$ROOT/.state
    mkdir -p "$state_dir" 2>/dev/null || :
    seed_file=$state_dir/hotspot-seed
    if [ ! -s "$seed_file" ]; then
        # od+tr only; no randomness daemon dependency on the camera.
        umask 077
        od -An -N8 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n' > "$seed_file" || :
        chmod 600 "$seed_file" 2>/dev/null || :
    fi
    if [ -s "$seed_file" ]; then
        cat "$seed_file"; return
    fi
    # Deterministic fallback when /dev/urandom is unavailable.
    printf '%s' "$ROOT$(date +%s)" | cksum | tr -d ' ' | cut -c1-16
}

current_ssid() {
    if [ -s "$SSID_FILE" ]; then
        IFS= read -r value < "$SSID_FILE" && printf '%s' "$value" && return
    fi
    printf 'NX300M-%s' "$(basename "$ROOT")"
}

# --- state helpers ---------------------------------------------------------
write_state() {  # state [stage]
    mkdir -p "$RUN" 2>/dev/null || :
    printf '%s\n' "${1}${2:+:$2}" > "$STATE_FILE.tmp" && mv -f "$STATE_FILE.tmp" "$STATE_FILE" 2>/dev/null || :
}
read_state() {
    [ -r "$STATE_FILE" ] || { printf 'off\n'; return; }
    IFS= read -r value < "$STATE_FILE" || value=off
    printf '%s\n' "$value"
}
write_owner() {  # kind pid  -- upsert one line per kind, keep the other
    [ -n "${2:-}" ] || return 1
    case $1 in hostapd|udhcpd) ;; *) return 1 ;; esac
    { [ ! -r "$OWNER_FILE" ] || grep -v "^$1 " "$OWNER_FILE" 2>/dev/null; \
      printf '%s %s\n' "$1" "$2"; } > "$OWNER_FILE.tmp" && \
      mv -f "$OWNER_FILE.tmp" "$OWNER_FILE"
}
# owner_pid <kind>: print the recorded pid only while it is still ours, i.e.
# /proc/<pid>/exe still resolves to that kind's binary. A recycled PID whose
# exe now points somewhere else is NOT ours to signal.
owner_pid() {  # kind expected-exe
    [ -r "$OWNER_FILE" ] || return 1
    pid=$(awk -v k="$1" '$1==k {print $2}' "$OWNER_FILE" 2>/dev/null)
    case $pid in ''|*[!0-9]*) return 1 ;; esac
    [ -d "/proc/$pid" ] || return 1
    [ "$(readlink "/proc/$pid/exe" 2>/dev/null)" = "$2" ] || return 1
    printf '%s' "$pid"
}
interface_exists() {
    [ -d "/sys/class/net/$INTERFACE" ]
}
interface_address() {
    ip -4 addr show dev "$INTERFACE" 2>/dev/null | awk '/inet / {sub(/\/.*/,"",$2); print $2; exit}'
}
has_our_ip() {
    [ "$(interface_address)" = "$AP_ADDRESS" ]
}
address_added_by_us() {
    [ -f "$RUNDIR/address-added" ]
}

# --- started-daemon tracking -----------------------------------------------
# A daemon started by this run must be reclaimable even when it never writes
# (or delays writing) its PID file, and a PID recycled into another program
# must never be signalled. Identification is by executable identity PLUS the
# exact private configuration path of this runtime dir -- never by program
# name alone. The runtime dir is flock-serialized, so during our start only
# the daemon we just spawned can carry this configuration argument.
pid_is_exe() {  # <pid> <exe>
    [ -n "$1" ] && [ -d "/proc/$1" ] && \
        [ "$(readlink "/proc/$1/exe" 2>/dev/null)" = "$2" ]
}
config_cmdline_pid() {  # <exe> <config-path>
    for pid_dir in /proc/[0-9]*; do
        scan_pid=${pid_dir#/proc/}
        [ "$(readlink "$pid_dir/exe" 2>/dev/null)" = "$1" ] || continue
        cmd=$(tr '\0' ' ' < "$pid_dir/cmdline" 2>/dev/null)
        case $cmd in *" $2 "*) printf '%s' "$scan_pid"; return 0 ;; esac
    done
    return 1
}
wait_tracked_pid() {  # <exe> <pidfile> <config-path> <attempts>
    waited=0
    while [ "$waited" -lt "$4" ]; do
        pid=$(cat "$2" 2>/dev/null) || pid=
        case $pid in ''|*[!0-9]*) pid= ;; esac
        if pid_is_exe "$pid" "$1"; then printf '%s' "$pid"; return 0; fi
        pid=$(config_cmdline_pid "$1" "$3" 2>/dev/null) || pid=
        if [ -n "$pid" ]; then printf '%s' "$pid"; return 0; fi
        sleep 0.1
        waited=$((waited+1))
    done
    return 1
}

# --- conflict detection ---------------------------------------------------
# The radio may already be in use by the stock RVF or another service. We
# detect: an interface with a foreign address, or a running wpa_supplicant /
# hostapd / udhcpd that we do not own. In those cases the start is refused
# and the situation is reported; nothing is killed.
foreign_radio_user() {
    if interface_exists; then
        address=$(interface_address)
        if [ -n "$address" ] && [ "$address" != "$AP_ADDRESS" ]; then
            printf 'interface %s already has address %s' "$INTERFACE" "$address"
            return 0
        fi
    fi
    ap_pid=$(owner_pid hostapd "$HOSTAPD" 2>/dev/null) || ap_pid=
    dh_pid=$(owner_pid udhcpd "$UDHCPD" 2>/dev/null) || dh_pid=
    # A daemon carrying OUR private config path is ours even when its owner
    # record was lost or tampered (same identity rule as the start path).
    [ -n "$ap_pid" ] || ap_pid=$(config_cmdline_pid "$HOSTAPD" "$RUNDIR/hostapd.conf" 2>/dev/null) || ap_pid=
    [ -n "$dh_pid" ] || dh_pid=$(config_cmdline_pid "$UDHCPD" "$RUNDIR/dhcpd.conf" 2>/dev/null) || dh_pid=
    for name in hostapd udhcpd wpa_supplicant p2p_supplicant; do
        for pid_dir in /proc/[0-9]*; do
            scan_pid=${pid_dir#/proc/}
            exe=$(readlink "$pid_dir/exe" 2>/dev/null) || continue
            base=${exe##*/}
            [ "$base" = "$name" ] || continue
            # Our own recorded owners are fine; anything else owns the radio.
            case $name:$scan_pid in
                hostapd:"$ap_pid"|udhcpd:"$dh_pid") continue ;;
            esac
            printf '%s (pid %s)' "$base" "$scan_pid"
            return 0
        done
    done
    return 1
}

# --- stop ------------------------------------------------------------------
stop_service() {
    # Stop in reverse start order. Only recorded owners are signalled, each
    # verified by /proc exe identity immediately before the signal. The
    # address is withdrawn only if we set it; the driver stays loaded either
    # way (unloading ath6kl_sdio would take the radio from other subsystems).
    write_state stopping
    stopped=
    for victim in udhcpd hostapd; do
        case $victim in
            udhcpd) exe=$UDHCPD ;;
            hostapd) exe=$HOSTAPD ;;
        esac
        pid=$(owner_pid "$victim" "$exe") || pid=
        [ -n "$pid" ] || continue
        if kill -TERM "$pid" 2>/dev/null; then
            stopped="$stopped $victim"
            waited=0
            while [ -d "/proc/$pid" ] && [ "$waited" -lt 20 ]; do
                sleep 0.1
                waited=$((waited+1))
            done
            # The KILL decision needs the same identity the TERM got: the
            # process can exit and its slot be taken (or its image replaced)
            # during the wait, so /proc/<pid> alone never justifies KILL.
            if [ -d "/proc/$pid" ] && pid_is_exe "$pid" "$exe"; then
                kill -KILL "$pid" 2>/dev/null || :
            fi
        fi
    done
    # Fallback sweep: a daemon whose owner record was lost or tampered is
    # still reclaimable by the same identity the start path uses (exe plus
    # our private config path). Foreign instances carry other configs and
    # never match, so they stay untouched. Runs under the runtime flock, so
    # no concurrent start can be spawning daemons with this config right now.
    for daemon in hostapd udhcpd; do
        case $daemon in
            hostapd) exe=$HOSTAPD; conf=$RUNDIR/hostapd.conf ;;
            udhcpd)  exe=$UDHCPD;  conf=$RUNDIR/dhcpd.conf ;;
        esac
        [ -f "$conf" ] || continue
        pid=$(config_cmdline_pid "$exe" "$conf" 2>/dev/null) || pid=
        [ -n "$pid" ] || continue
        if kill -TERM "$pid" 2>/dev/null; then
            stopped="$stopped $daemon"
            waited=0
            while [ -d "/proc/$pid" ] && [ "$waited" -lt 20 ]; do
                sleep 0.1
                waited=$((waited+1))
            done
            # Same identity rule as above: exe plus our private config path,
            # re-verified immediately before the KILL.
            if [ -d "/proc/$pid" ] && \
               [ "$(config_cmdline_pid "$exe" "$conf" 2>/dev/null)" = "$pid" ]; then
                kill -KILL "$pid" 2>/dev/null || :
            fi
        fi
    done
    # DHCP lease file and PID files belong to this feature only.
    rm -f "$RUNDIR/udhcpd.pid" "$RUNDIR/dhcpd.leases" \
          "$RUNDIR/hostapd.pid" "$RUNDIR/hostapd.conf" "$RUNDIR/dhcpd.conf" 2>/dev/null || :
    if address_added_by_us && has_our_ip; then
        ip addr del "$AP_ADDRESS/$AP_NETMASK" dev "$INTERFACE" 2>/dev/null && \
            stopped="$stopped address" || :
    fi
    rm -f "$RUNDIR/address-added" "$OWNER_FILE" "$SSID_FILE" "$ADDRESS_FILE" 2>/dev/null || :
    write_state off
    printf '%s\n' "${stopped# }"
}

# --- status ----------------------------------------------------------------
report_status() {
    state=$(read_state)
    ssid=$(current_ssid)
    address=$(interface_address)
    # A state file saying "on" is only believed while both recorded workers
    # are still alive and the interface carries our address. Anything else is
    # reported (and persisted) as a concrete failure so the menu shows it and
    # a start can retry; the query itself stays on-demand and bounded.
    if [ "$state" = on ]; then
        ap_alive=$(owner_pid hostapd "$HOSTAPD" 2>/dev/null) || ap_alive=
        dh_alive=$(owner_pid udhcpd "$UDHCPD" 2>/dev/null) || dh_alive=
        missing=
        [ -n "$ap_alive" ] || missing=hostapd
        [ -n "$dh_alive" ] || missing="${missing:+$missing }udhcpd"
        if [ -n "$missing" ]; then
            state="failed:worker-dead:$missing"
            write_state "failed" "worker-dead:$missing"
        elif ! has_our_ip; then
            state="failed:address-lost"
            write_state "failed" "address-lost"
        elif ! interface_exists; then
            state="failed:no-interface"
            write_state "failed" "no-interface"
        fi
    fi
    printf 'HOTSPOT state=%s interface=%s address=%s ssid=%s\n' \
        "$state" "$INTERFACE" "${address:-none}" "$ssid"
    case $state in
        on|starting:*)
            printf 'HOTSPOT_DETAIL ssid=%s\n' "$ssid"
            printf 'HOTSPOT_DETAIL listen=%s:8781\n' "$AP_ADDRESS"
            ;;
    esac
    if [ "$command" = credentials ] && [ "$state" = on ]; then
        # Printed for the UI only; never written to a log file by this script.
        printf 'HOTSPOT_CREDENTIALS ssid=%s password=%s\n' "$ssid" "$(password_from_config)"
    fi
    case $state in
        off|on|starting:*|stopping) return 0 ;;
        *) return 1 ;;
    esac
}

# --- start -----------------------------------------------------------------
start_service() {
    current=$(read_state)
    case $current in
        starting:*|stopping)
            fail "another request is in progress ($current)" ;;
    esac
    # Regardless of the recorded state, anything still alive from a previous
    # run is torn down first: state=on with dead workers or a lost address,
    # and failed:<stage> states written by the status query. Only recorded
    # owners are signalled; foreign processes and addresses stay untouched.
    ap_alive=$(owner_pid hostapd "$HOSTAPD" 2>/dev/null) || ap_alive=
    dh_alive=$(owner_pid udhcpd "$UDHCPD" 2>/dev/null) || dh_alive=
    if [ "$current" = on ] && [ -n "$ap_alive" ] && [ -n "$dh_alive" ] && has_our_ip; then
        printf 'HOTSPOT_START already=on ssid=%s\n' "$(current_ssid)"
        return 0
    fi
    stop_service >/dev/null
    # Missing pieces are reported, never guessed.
    [ -x "$HOSTAPD" ] || { write_state failed no-hostapd; fail "hostapd missing: $HOSTAPD"; }
    [ -x "$UDHCPD" ] || { write_state failed no-udhcpd; fail "udhcpd missing: $UDHCPD"; }
    [ -f "$DRIVER1" ] && [ -f "$DRIVER2" ] || { write_state failed no-driver; fail "Wi-Fi driver modules missing"; }

    conflict=$(foreign_radio_user) && { write_state failed "conflict:$conflict"; fail "radio already in use: $conflict"; }

    mkdir -p "$RUN" "$RUNDIR" || { write_state failed runtime-dir; fail 'cannot create runtime dir'; }
    chmod 700 "$RUNDIR" 2>/dev/null || :
    # One writer at a time. A second start is refused outright; a stop that
    # arrives while we hold the lock waits and then tears down our result.
    exec 9>>"$LOCK_FILE" || { write_state failed lock; fail 'cannot open lock file'; }
    flock -n 9 || { write_state failed busy; fail 'another request is running'; }

    write_state starting load-driver
    if ! grep -q ath6kl_sdio /proc/modules 2>/dev/null; then
        insmod "$DRIVER1" cfg80211_emerg_printk=0 2>>"$RUNDIR/start.log" || {
            write_state failed load-driver; fail "cfg80211 load failed"; }
        insmod "$DRIVER2" ath6kl_emerg_printk=0 2>>"$RUNDIR/start.log" || {
            write_state failed load-driver; fail "ath6kl_sdio load failed"; }
        # The stock script waits for the interface to appear.
        waited=0
        while ! interface_exists && [ "$waited" -lt 100 ]; do
            sleep 0.1; waited=$((waited+1))
        done
    fi
    interface_exists || { write_state failed no-interface; fail "interface $INTERFACE did not appear"; }

    write_state starting link-up
    ip link set "$INTERFACE" up 2>>"$RUNDIR/start.log" || {
        write_state failed link-up; fail "cannot bring $INTERFACE up"; }

    write_state starting address
    if ! has_our_ip; then
        ip addr add "$AP_ADDRESS/$AP_NETMASK" dev "$INTERFACE" 2>>"$RUNDIR/start.log" || {
            write_state failed address; fail "cannot set $AP_ADDRESS on $INTERFACE"; }
        : > "$RUNDIR/address-added"
    fi

    write_state starting hostapd
    ssid=$(current_ssid)
    printf '%s\n' "$ssid" > "$SSID_FILE.tmp" && mv -f "$SSID_FILE.tmp" "$SSID_FILE"
    password=$(password_from_config)
    if [ "${#password}" -lt 8 ]; then
        stop_service >/dev/null
        write_state failed password-length
        fail "generated password too short (${#password}); regenerate .state/hotspot-seed"
    fi
    {
        printf 'logger_stdout=-1\n'
        printf 'logger_stdout_level=2\n'
        printf 'ctrl_interface=%s/hostapd\n' "$RUNDIR"
        printf 'interface=%s\n' "$INTERFACE"
        printf 'driver=nl80211\n'
        printf 'hw_mode=g\n'
        printf 'ieee80211n=1\n'
        printf 'ht_capab=[SHORT-GI-20]\n'
        # Channel 6 avoids the channel 1 the stock softap profile uses, so a
        # coexisting stock profile (if RVF ever starts one) keeps its plan.
        printf 'channel=6\n'
        printf 'ssid=%s\n' "$ssid"
        printf 'auth_algs=1\n'
        printf 'wpa=2\n'
        printf 'wpa_passphrase=%s\n' "$password"
        printf 'wpa_key_mgmt=WPA-PSK\n'
        printf 'rsn_pairwise=CCMP\n'
    } > "$RUNDIR/hostapd.conf"
    chmod 600 "$RUNDIR/hostapd.conf"

    # The daemons are spawned from a subshell that closes the lock fd first:
    # they outlive this script and must not keep the hotspot lock held (a
    # later stop would block forever on our own workers).
    ( exec 9>&-
      "$HOSTAPD" "$RUNDIR/hostapd.conf" -B -P "$RUNDIR/hostapd.pid" 2>>"$RUNDIR/start.log" ) || {
        stop_service >/dev/null
        write_state failed hostapd
        fail "hostapd failed to start (see RAM start.log)"; }
    # Wait a bounded time for the PID file; fall back to the config-path
    # identity scan when it is missing or delayed, and verify the recorded
    # PID still points at our hostapd binary (a recycled PID is refused).
    ap_pid=$(wait_tracked_pid "$HOSTAPD" "$RUNDIR/hostapd.pid" "$RUNDIR/hostapd.conf" 30) || {
        # Nothing we started is alive and identifiable; release the resources
        # this run already took (address, config) without touching others.
        stop_service >/dev/null
        write_state failed hostapd-pid
        fail "hostapd pid missing after start"; }
    write_owner hostapd "$ap_pid"

    write_state starting udhcpd
    {
        printf 'start %s\n' "$NET_PREFIX.30"
        printf 'end %s\n' "$NET_PREFIX.40"
        printf 'interface %s\n' "$INTERFACE"
        printf 'max_leases 10\n'
        printf 'option subnet %s\n' "$AP_NETMASK"
        printf 'option router %s\n' "$AP_ADDRESS"
        printf 'option dns 0.0.0.0\n'
        printf 'pidfile %s/udhcpd.pid\n' "$RUNDIR"
        printf 'lease_file %s/dhcpd.leases\n' "$RUNDIR"
    } > "$RUNDIR/dhcpd.conf"
    chmod 600 "$RUNDIR/dhcpd.conf"
    touch "$RUNDIR/dhcpd.leases"
    ( exec 9>&-
      "$UDHCPD" "$RUNDIR/dhcpd.conf" 2>>"$RUNDIR/start.log" ) || {
        # hostapd is already ours: stop exactly what we started.
        stop_service >/dev/null
        write_state failed udhcpd
        fail "udhcpd failed to start (see RAM start.log)"; }
    ud_pid=$(wait_tracked_pid "$UDHCPD" "$RUNDIR/udhcpd.pid" "$RUNDIR/dhcpd.conf" 30) || {
        stop_service >/dev/null
        write_state failed udhcpd-pid
        fail "udhcpd pid missing after start"; }
    write_owner udhcpd "$ud_pid"

    printf '%s\n' "$AP_ADDRESS" > "$ADDRESS_FILE.tmp" && mv -f "$ADDRESS_FILE.tmp" "$ADDRESS_FILE"
    write_state on
    printf 'HOTSPOT_START ssid=%s address=%s\n' "$ssid" "$AP_ADDRESS"
    printf 'HOTSPOT_DETAIL ssid=%s\n' "$ssid"
    printf 'HOTSPOT_DETAIL listen=%s:8781\n' "$AP_ADDRESS"
    return 0
}

case $command in
    start)   start_service ;;
    stop)
        # Closing the hotspot also closes the Telnet service that rode on it;
        # a Telnet on some other network is untouched (owner check).
        if [ -x "$ROOT/telnet_toggle.sh" ] && [ ! -L "$ROOT/telnet_toggle.sh" ]; then
            /bin/sh "$ROOT/telnet_toggle.sh" disable >/dev/null 2>&1 || :
        fi
        mkdir -p "$RUN" 2>/dev/null || :
        exec 9>>"$LOCK_FILE" || fail 'cannot open lock file'
        # A stop waits for any in-flight start to finish, then tears the
        # whole thing down; the request itself cannot be left half-started.
        flock 9 || fail 'cannot acquire hotspot lock'
        printf 'HOTSPOT_STOP removed=%s\n' "$(stop_service)" ;;
    status|state|ssid|credentials) report_status ;;
esac
