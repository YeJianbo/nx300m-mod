"""Run the REAL telnet + hotspot control chain in a private ARM Linux VM.

The guest runs the production control scripts under the stock camera busybox
1.20.2 ash (extracted from the 1.15 rootfs), with the shipped busybox-static
telnetd, the sha512-crypt login tool and the RFC854 probe client on a real
kernel: /proc/<pid>/exe identity checks, forked daemons with real PID files
and real loopback TCP all behave exactly like the camera. The hotspot path
uses the cross-compiled ARM command doubles for hostapd/udhcpd/insmod/ip.

Boots only with -nic none: everything runs on the guest loopback.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time

import arm_vm
import probe_mfw_ram_vm

PROJECT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get('NX_TELNET_PROBE_DIR') or
           (PROJECT / 'outputs/review/2026-11-11-wireless-telnet/telnet-vm'))
SOCKET = os.environ.get('NX_TELNET_PROBE_SOCKET') or '/tmp/nx300m-telnet-private'

CHAIN = r'''#!/bin/sh
# Guest-side acceptance: production scripts under the camera's own busybox
# ash, real ARM kernel, real telnetd + login tool, ARM command doubles for
# the hotspot daemons. Results land in /perf/telnet/results.txt.
ROOT=/perf/telnet/mod
NXSH="/nx-stock/bin/busybox sh"
R=/perf/telnet/results.txt
export NX300M_MOD_ROOT=$ROOT NX_TELNET_INTERFACE=lo
PASS=0; FAIL=0
chk() { if [ "$3" = "$4" ]; then PASS=$((PASS+1)); echo "PASS $1: $2" >> "$R";
    else FAIL=$((FAIL+1)); echo "FAIL $1: $2 (want [$3] got [$4])" >> "$R"; fi }
LISTEN_COUNT() { awk '$4=="0A" && $2 ~ /:0017$/' /proc/net/tcp | wc -l; }

: > "$R"
mkdir -p /perf/telnet/stub-state
printf '127.0.0.1\n' > /perf/telnet/stub-state/lo.addr

# The ip double needs its state before any script queries an address.
export STUB_LOG=/perf/telnet/hotspot-calls.log
export STUB_STATE=/perf/telnet/stub-state
: > "$STUB_LOG"

# ---------- telnet: camera ash + real telnetd + real login tool ----------
out=$($NXSH $ROOT/telnet_toggle.sh enable 2>&1); rc=$?
echo "$out" > /perf/telnet/enable.out
chk tel "enable rc=0" 0 "$rc"
grep -q "TELNET_ENABLE address=127.0.0.1 port=23" /perf/telnet/enable.out \
    && chk tel "enable reports address/port" yes yes || chk tel "enable reports address/port" yes no
chk tel "LISTEN socket" yes "$([ "$(LISTEN_COUNT)" -ge 1 ] && echo yes || echo no)"
chk tel "state on" on "$(cat /var/run/nx300m-telnet/state 2>/dev/null)"
pw=$(cat $ROOT/.state/telnet-password 2>/dev/null)
hash=$(cat $ROOT/.state/telnet-hash 2>/dev/null)
leak=0
if [ -n "$pw" ] && [ -n "$hash" ]; then
    for c in /proc/[0-9]*/cmdline; do
        t=$(tr '\0' ' ' < "$c" 2>/dev/null)
        case "$t" in *"$pw"*|*"$hash"*) leak=$((leak+1)) ;; esac
    done
fi
chk tel "no password/hash in argv" 0 "$leak"

NC="/nx-stock/bin/busybox nc"
tcase() { # tcase <name> <payload-file>; payload is sourced with SESSION_PW set
    { SESSION_PW="$pw" . "$2"; } | $NC 127.0.0.1 23 > /perf/telnet/$1.raw 2>&1
    tr -d '\377\376\375\374\373\372' < /perf/telnet/$1.raw > /perf/telnet/$1
}
cat > /perf/telnet/reject.in <<'IN'
sleep 1
printf '%s\r\n' 'TotallyWrong123'
sleep 1
printf '%s\r\n' 'TotallyWrong123'
sleep 1
printf '%s\r\n' 'TotallyWrong123'
sleep 8
IN
cat > /perf/telnet/login.in <<'IN'
sleep 1
printf '%s\r\n' "$SESSION_PW"
sleep 1
printf 'echo %s\r\n' 'MOD_TELNET_OK_7391'
sleep 2
IN

# wrong password three times -> three prompts, failure banner, server close
tcase reject /perf/telnet/reject.in
chk tel "three password prompts" 3 "$(grep -c 'Password:' /perf/telnet/reject)"
grep -q "Login incorrect" /perf/telnet/reject \
    && chk tel "wrong password not accepted" yes yes || chk tel "wrong password not accepted" yes no
grep -q "Too many failures" /perf/telnet/reject \
    && chk tel "lockout banner" yes yes || chk tel "lockout banner" yes no
chk tel "listener survives reject" yes "$([ "$(LISTEN_COUNT)" -ge 1 ] && echo yes || echo no)"

# correct password -> Login OK and the test command output
tcase login /perf/telnet/login.in
grep -q "Login OK" /perf/telnet/login \
    && chk tel "correct password accepted" yes yes || chk tel "correct password accepted" yes no
grep -q "MOD_TELNET_OK_7391" /perf/telnet/login \
    && chk tel "shell executed test command" yes yes || chk tel "shell executed test command" yes no

# reconnect: a second full session succeeds
tcase login2 /perf/telnet/login.in
grep -q "MOD_TELNET_OK_7391" /perf/telnet/login2 \
    && chk tel "reconnect after disconnect" yes yes || chk tel "reconnect after disconnect" yes no

out=$($NXSH $ROOT/telnet_toggle.sh disable 2>&1); rc=$?
echo "$out" > /perf/telnet/disable.out
chk tel "disable rc=0" 0 "$rc"
sleep 1
chk tel "listener gone" 0 "$(LISTEN_COUNT)"
$NC 127.0.0.1 23 < /dev/null > /perf/telnet/refused.out 2>&1; rc=$?
chk tel "port unreachable after disable" no "$([ $rc -eq 0 ] && echo yes || echo no)"
chk tel "state off" off "$(cat /var/run/nx300m-telnet/state 2>/dev/null)"

out=$($NXSH $ROOT/telnet_toggle.sh toggle 2>&1); rc=$?
chk tel "toggle on rc=0" 0 "$rc"
# A freshly daemonized server may close the very first back-to-back session;
# confirm the listener and give the session one retry before judging.
sleep 1
attempt=0
while [ "$(LISTEN_COUNT)" -lt 1 ] && [ "$attempt" -lt 10 ]; do
    sleep 1; attempt=$((attempt+1))
done
SESSION_PW="$pw" tcase login3 /perf/telnet/login.in
if ! grep -q "MOD_TELNET_OK_7391" /perf/telnet/login3; then
    sleep 2
    SESSION_PW="$pw" tcase login3 /perf/telnet/login.in
fi
grep -q "MOD_TELNET_OK_7391" /perf/telnet/login3 \
    && chk tel "session after toggle-on" yes yes || chk tel "session after toggle-on" yes no
out=$($NXSH $ROOT/telnet_toggle.sh toggle 2>&1); rc=$?
chk tel "toggle off rc=0" 0 "$rc"
$NC 127.0.0.1 23 < /dev/null > /dev/null 2>&1; rc=$?
chk tel "port closed after toggle-off" no "$([ $rc -eq 0 ] && echo yes || echo no)"

# ---------- ownership / identity cases ----------
# The listener is identified by (exe, pid start time) recorded in the owner
# file; every rule below must keep foreign instances alive and never signal a
# PID whose identity changed.
FOREIGN_DIR=$ROOT/bin/foreign
FOREIGN=$FOREIGN_DIR/busybox
/nx-stock/bin/busybox mkdir -p "$FOREIGN_DIR" 2>/dev/null || :
cp $ROOT/bin/busybox.arm "$FOREIGN" || :
chmod 755 "$FOREIGN" || :
# A second instance of the SAME binary (byte-identical copy) under another
# path: it must survive every stop path that only owns our recorded listener.
# The file keeps the name "busybox" because the applet dispatch keys on
# argv[0]; the identity check keys on the exe path, so it stays foreign.
"$FOREIGN" telnetd -p 2323 -b 127.0.0.1 -K >/perf/telnet/foreign.log 2>&1 &
foreign_launcher=$!
foreign_pid=
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    for pid_dir in /proc/[0-9]*; do
        p=${pid_dir#/proc/}
        x=$(readlink "$pid_dir/exe" 2>/dev/null)
        [ "$x" = "$FOREIGN" ] || continue
        foreign_pid=$p
    done
    [ -n "$foreign_pid" ] && break
    /nx-stock/bin/busybox sleep 1
done
chk own "foreign same-binary running" yes "$([ -n "$foreign_pid" ] && echo yes || echo no)"

# 1) our service + the foreign instance: disable must remove only ours
$NXSH $ROOT/telnet_toggle.sh enable >/dev/null 2>&1
sleep 1
out=$($NXSH $ROOT/telnet_toggle.sh disable 2>&1); rc=$?
echo "$out" > /perf/telnet/own-disable.out
chk own "disable rc=0 with foreign present" 0 "$rc"
chk own "our listener gone" 0 "$(LISTEN_COUNT)"
chk own "foreign listener preserved" yes "$([ -n "$foreign_pid" ] && [ -d "/proc/$foreign_pid" ] && echo yes || echo no)"

# 2) owner record removed while running: disable must report the leftover
#    listener as foreign instead of killing it by name
$NXSH $ROOT/telnet_toggle.sh enable >/dev/null 2>&1
sleep 1
listener_pid=$(awk '{print $1}' /var/run/nx300m-telnet/owner 2>/dev/null)
rm -f /var/run/nx300m-telnet/owner
out=$($NXSH $ROOT/telnet_toggle.sh disable 2>&1); rc=$?
echo "$out" > /perf/telnet/own-missing-owner.out
chk own "disable with missing owner rc=0" 0 "$rc"
case "$out" in
    *"listener=foreign:"*) chk own "leftover listener reported as foreign" yes yes ;;
    *) chk own "leftover listener reported as foreign" yes no ;;
esac
chk own "state off after missing-owner disable" off "$(cat /var/run/nx300m-telnet/state 2>/dev/null)"
chk own "unowned listener not killed" yes "$([ -n "$listener_pid" ] && [ -d "/proc/$listener_pid" ] && echo yes || echo no)"
kill -KILL "$listener_pid" 2>/dev/null || :
sleep 0.5

# 3) identity change: correct PID, wrong start-time token -> not ours to stop
$NXSH $ROOT/telnet_toggle.sh enable >/dev/null 2>&1
sleep 1
listener_pid=$(awk '{print $1}' /var/run/nx300m-telnet/owner 2>/dev/null)
printf '%s %s\n' "$listener_pid" '1' > /var/run/nx300m-telnet/owner
out=$($NXSH $ROOT/telnet_toggle.sh disable 2>&1); rc=$?
echo "$out" > /perf/telnet/own-identity.out
chk own "stale start-time token rc=0" 0 "$rc"
chk own "identity change not killed" yes "$([ -d "/proc/$listener_pid" ] && echo yes || echo no)"
grep -q 'listener=foreign' /perf/telnet/own-identity.out \
    && chk own "identity change reported as foreign" yes yes \
    || chk own "identity change reported as foreign" yes no
kill -KILL "$listener_pid" 2>/dev/null || :
for pid_dir in /proc/[0-9]*; do
    case $(tr '\0' ' ' < "$pid_dir/cmdline" 2>/dev/null) in
        *"-p 23 -b 127.0.0.1"*) kill -KILL "${pid_dir#/proc/}" 2>/dev/null || : ;; esac
done
sleep 0.5

# 4) disable retry: a second disable is a clean no-op
$NXSH $ROOT/telnet_toggle.sh enable >/dev/null 2>&1
sleep 1
$NXSH $ROOT/telnet_toggle.sh disable >/dev/null 2>&1
out=$($NXSH $ROOT/telnet_toggle.sh disable 2>&1); rc=$?
echo "$out" > /perf/telnet/own-retry.out
chk own "second disable rc=0" 0 "$rc"
case "$out" in
    *"removed=none"*) chk own "second disable removed=none" yes yes ;;
    *) chk own "second disable removed=none" yes no ;;
esac
chk own "state still off after retry" off "$(cat /var/run/nx300m-telnet/state 2>/dev/null)"
kill -KILL "$foreign_pid" $foreign_launcher 2>/dev/null || :
sleep 0.5
chk own "foreign cleaned up after cases" no "$([ -n "$foreign_pid" ] && [ -d "/proc/$foreign_pid" ] && echo yes || echo no)"

# ---------- hotspot: camera ash + ARM command doubles on the real kernel ----------
export NX_HOTSPOT_HOSTAPD=$ROOT/bin/hostapd
export NX_HOTSPOT_UDHCPD=$ROOT/bin/udhcpd
: > "$STUB_LOG"
if ! [ -d /sys/class/net/wlan0 ]; then
    mount -t tmpfs none /sys/class/net 2>/dev/null || :
    mkdir -p /sys/class/net/wlan0
fi
/sbin/insmod /lib/modules/cfg80211.ko cfg80211_emerg_printk=0 > /perf/telnet/diag-insmod.txt 2>&1
echo "rc=$?" >> /perf/telnet/diag-insmod.txt
ls -la /sbin/insmod >> /perf/telnet/diag-insmod.txt 2>&1
out=$($NXSH $ROOT/hotspot.sh start 2>&1); rc=$?
echo "$out" > /perf/telnet/hs-start.out
chk hs "start rc=0" 0 "$rc"
chk hs "state on" on "$(cat /var/run/nx300m-hotspot/state 2>/dev/null)"
chk hs "owner records both daemons" 2 "$(wc -l < /var/run/nx300m-hotspot/owner 2>/dev/null)"
grep -q "CALL insmod insmod /lib/modules/cfg80211.ko" "$STUB_LOG" \
    && chk hs "driver load order" yes yes || chk hs "driver load order" yes no
grep -q "CALL hostapd start" "$STUB_LOG" \
    && chk hs "hostapd invoked" yes yes || chk hs "hostapd invoked" yes no
grep -q "CALL udhcpd start" "$STUB_LOG" \
    && chk hs "udhcpd invoked" yes yes || chk hs "udhcpd invoked" yes no
chk hs "address recorded" "192.168.0.1" "$(cat /var/run/nx300m-hotspot/address 2>/dev/null)"

out=$($NXSH $ROOT/hotspot.sh start 2>&1); rc=$?
echo "$out" > /perf/telnet/hs-start2.out
chk hs "second start already=on" 0 "$rc"
chk hs "no duplicate hostapd" 1 "$(grep -c 'CALL hostapd start' "$STUB_LOG")"

out=$($NXSH $ROOT/hotspot.sh stop 2>&1); rc=$?
echo "$out" > /perf/telnet/hs-stop.out
chk hs "stop rc=0" 0 "$rc"
chk hs "state off" off "$(cat /var/run/nx300m-hotspot/state 2>/dev/null)"
sleep 1
left=0
for e in /proc/[0-9]*/exe; do
    x=$(readlink "$e" 2>/dev/null)
    case $x in $ROOT/bin/hostapd|$ROOT/bin/udhcpd) left=$((left+1)) ;; esac
done
chk hs "both daemons gone" 0 "$left"


# ---------- performance: four groups inside this same VM ----------
# Metrics per group: worker process count, our-services RSS sum (kB), their
# cumulative CPU ticks, and one control round trip per script (ms, GNU date).
PERF=/perf/telnet/perf.txt
: > "$PERF"
our_pids() {
    for e in /proc/[0-9]*/exe; do
        x=$(readlink "$e" 2>/dev/null)
        case $x in
            $ROOT/bin/hostapd|$ROOT/bin/udhcpd|$ROOT/bin/busybox.arm)
                p=${e%/exe}; p=${p#/proc/}; printf '%s\n' "$p" ;;
        esac
    done
}
snapshot() { # snapshot <label>
    workers=0; rss=0; cpu=0
    for pid in $(our_pids); do
        workers=$((workers+1))
        v=$(awk '/^VmRSS:/{print $2}' /proc/$pid/status 2>/dev/null)
        rss=$((rss + ${v:-0}))
        t=$(awk '{print $14 + $15}' /proc/$pid/stat 2>/dev/null)
        cpu=$((cpu + ${t:-0}))
    done
    t0=$(awk '{print $1}' /proc/uptime)
    $NXSH $ROOT/hotspot.sh status > /dev/null 2>&1
    t1=$(awk '{print $1}' /proc/uptime)
    h_ms=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%d", (b-a)*1000}')
    t0=$(awk '{print $1}' /proc/uptime)
    $NXSH $ROOT/telnet_toggle.sh status > /dev/null 2>&1
    t1=$(awk '{print $1}' /proc/uptime)
    t_ms=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%d", (b-a)*1000}')
    printf '%s workers=%d rss_kb=%d cpu_ticks=%d hotspot_status_ms=%d telnet_status_ms=%d\n' \
        "$1" "$workers" "$rss" "$cpu" "$h_ms" "$t_ms" >> "$PERF"
}
background_session() { # keep one telnet session open for group 4
    { sleep 25; } | $NC 127.0.0.1 23 > /dev/null 2>&1 &
}

# G0: everything off (also proves the boot path added no worker)
workers_boot=0
for name in hostapd udhcpd telnetd; do
    for e in /proc/[0-9]*/cmdline; do
        grep -q "$name" "$e" 2>/dev/null && workers_boot=$((workers_boot+1))
    done
done
printf 'boot_path_extra_workers=%d\n' "$workers_boot" >> "$PERF"
snapshot G0-all-off

# G1: hotspot idle with its command doubles
$NXSH $ROOT/hotspot.sh start > /dev/null 2>&1
sleep 1
snapshot G1-hotspot-idle-stub
$NXSH $ROOT/hotspot.sh stop > /dev/null 2>&1
sleep 0.5

# G2: telnetd listening, no connection
$NXSH $ROOT/telnet_toggle.sh enable > /dev/null 2>&1
sleep 1
snapshot G2-telnet-idle

# G3: one connected session held open
background_session
sleep 2
snapshot G3-telnet-connected
wait
$NXSH $ROOT/telnet_toggle.sh disable > /dev/null 2>&1

echo "SUMMARY pass=$PASS fail=$FAIL" >> "$R"
echo "TELNET_CHAIN_DONE" >> "$R"
'''


def configure():
    vm = probe_mfw_ram_vm
    init = vm.INIT
    init = '\n'.join(line for line in init.splitlines()
                     if '/arm-vm/mfw-source' not in line and
                     'remount,bind,ro /mnt/mmc' not in line)
    block = r'''
echo TELNET_STEP_shims
/rescue/busybox mkdir -p /var/run /perf/telnet
[ -x /usr/bin/flock ] || /rescue/busybox ln -sf /rescue/busybox /bin/flock
command -v ip >/dev/null 2>&1 || /rescue/busybox ln -sf /rescue/busybox /usr/sbin/ip
[ -x /usr/bin/od ] || /rescue/busybox ln -sf /rescue/busybox /usr/bin/od
command -v find >/dev/null 2>&1 || /rescue/busybox ln -sf /rescue/busybox /usr/bin/find
echo TELNET_STEP_chain-start
sh /perf/telnet/run-chain.sh > /perf/telnet/chain.log 2>&1 &
echo TELNET_VM_READY
'''
    init = init.replace('echo MFW_PRIVATE_VM_SHELL_READY', block)
    vm.INIT = init
    vm.OUT = OUT
    vm.SOCKET = SOCKET
    vm.PROBE = ':'.join(CHAIN.splitlines()[:1])  # unused marker guard


def prepare():
    vm = probe_mfw_ram_vm
    OUT.mkdir(parents=True, exist_ok=True)
    base = arm_vm.WORK / 'initramfs.cpio.gz'
    if not base.is_file():
        raise ValueError('Cached ARM VM initramfs is required; run tools/arm_vm.py prepare')

    # stock camera userland: the 1.20.2 busybox shell and the armel runtime
    # libraries the production binaries link against
    stock_files = {}
    with tarfile.open(PROJECT / 'work/stock-1.15/rootfs.tar') as stock:
        stock_files['nx-stock/bin/busybox'] = stock.extractfile('bin/busybox').read()
        stock_files['lib/ld-linux.so.3'] = stock.extractfile('lib/ld-2.13.so').read()
        stock_files['lib/libc.so.6'] = stock.extractfile('lib/libc-2.13.so').read()
        stock_files['lib/libcrypt.so.1'] = stock.extractfile('lib/libcrypt-2.13.so').read()

    deploy = PROJECT / 'outputs/deployment/wireless-telnet'
    stubs = OUT / 'stubs-arm'
    files = {
        'init': vm.INIT.encode(),
        'perf/telnet/run-chain.sh': CHAIN.replace('\r\n', '\n').encode(),
        'perf/telnet/mod/telnet_toggle.sh':
            (PROJECT / 'mod/deployment/mfw/telnet_toggle.sh').read_bytes().replace(b'\r\n', b'\n'),
        'perf/telnet/mod/hotspot.sh':
            (PROJECT / 'mod/deployment/mfw/hotspot.sh').read_bytes().replace(b'\r\n', b'\n'),
        'perf/telnet/mod/bin/busybox.arm': (deploy / 'bin/busybox.arm').read_bytes(),        'perf/telnet/mod/bin/telnet_login.arm': (deploy / 'telnet_login.arm').read_bytes(),
        'perf/telnet/mod/bin/telnet_probe_client.arm': (deploy / 'telnet_probe_client.arm').read_bytes(),
        'perf/telnet/mod/bin/hostapd': (stubs / 'hostapd').read_bytes(),
        'perf/telnet/mod/bin/udhcpd': (stubs / 'udhcpd').read_bytes(),
        'bin/ip': (stubs / 'ip').read_bytes(),
        'sbin/insmod': (stubs / 'insmod').read_bytes(),
    }
    files.update(stock_files)

    overlay = io.BytesIO()
    directories = {p.as_posix() for name in files for p in Path(name).parents if str(p) != '.'}
    for name in sorted(directories):
        arm_vm.cpio_add(overlay, name, 0o40755)
    for name, data in files.items():
        if name == 'init' or name.endswith('.sh') or name.endswith('.so.3') \
                or name.startswith(('bin/', 'sbin/')) or '/bin/' in name or '/sbin/' in name:
            mode = 0o100755
        else:
            mode = 0o100644
        arm_vm.cpio_add(overlay, name, mode, data)
    arm_vm.cpio_add(overlay, 'TRAILER!!!', 0)
    image = OUT / 'initramfs.cpio.gz'
    with base.open('rb') as source, image.open('wb') as target:
        shutil.copyfileobj(source, target)
        target.write(gzip.compress(overlay.getvalue(), mtime=0))
    digest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()
              if name.endswith(('.sh', '.arm')) or name.endswith('busybox')}
    arm_vm.write_json(OUT / 'inputs.json', {
        'scope': 'production telnet/hotspot scripts under stock camera ash in a real ARM kernel VM',
        'overlay_files': sorted(files), 'sha256': digest,
        'network': 'guest loopback only (qemu -nic none)',
        'hardware_rf_verified': False})


def call(action, command='', timeout=375):
    # WSL does not forward arbitrary Windows environment variables; the output
    # directory and socket overrides must be listed in WSLENV to reach the
    # Linux half (which then maps the Windows path itself).
    env = dict(os.environ)
    env['WSLENV'] = ':'.join(filter(None, [env.get('WSLENV', ''),
                                           'NX_TELNET_PROBE_DIR', 'NX_TELNET_PROBE_SOCKET']))
    result = subprocess.run(
        ['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
         arm_vm.wsl_path(Path(__file__).resolve()), '--linux', action,
         base64.b64encode(command.encode()).decode()],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def run():
    report_path = OUT / 'report.json'
    report_path.unlink(missing_ok=True)
    call('start')
    try:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if 'TELNET_VM_READY' in (OUT / 'serial.log').read_text(errors='replace'):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Private ARM boot or chain timed out (no TELNET_VM_READY)')
        collected = call('exec', '/rescue/busybox cat /perf/telnet/chain.log 2>/dev/null | /rescue/busybox tail -40')
        (OUT / 'chain.log').write_text(collected['output'], encoding='utf-8')
        # The chain runs in the background; poll its result file instead of
        # holding one long serial command open until the deadline.
        results = ''
        deadline = time.monotonic() + 420
        while time.monotonic() < deadline:
            collected = call('exec', '/rescue/busybox cat /perf/telnet/results.txt 2>/dev/null')
            results = collected['output']
            if 'TELNET_CHAIN_DONE' in results:
                break
            time.sleep(5)
        (OUT / 'results.txt').write_text(results, encoding='utf-8')
        # Evidence bundling is a convenience, not part of the chain: gzipping and
        # base64-ing a few hundred kilobytes through the guest serial console can
        # exceed the call budget. A packaging failure is recorded, never turned
        # into a chain failure.
        packaging = 'bundled'
        try:
            package = call('exec',
                           # Only the small text artefacts: the full tree is too large to move over the
                           # guest serial console within any useful budget (three runs timed out at
                           # 375 s, 300 s and 600 s). The state files bound the chain's own claims.
                           '/rescue/busybox tar -czf /tmp/telnet-evidence.tar.gz -C / perf/telnet '
                           'var/run/nx300m-telnet/state var/run/nx300m-hotspot/state 2>/dev/null || '
                           '/rescue/busybox tar -czf /tmp/telnet-evidence.tar.gz -C / perf/telnet; '
                           '/rescue/busybox base64 /tmp/telnet-evidence.tar.gz', timeout=660)
            encoded = ''.join(line.strip() for line in package['output'].splitlines()
                              if re.fullmatch('[A-Za-z0-9+/=]+', line.strip()))
            raw = base64.b64decode(encoded)
            (OUT / 'guest-evidence.tar.gz').write_bytes(raw)
            import tarfile as _tf
            with _tf.open(fileobj=io.BytesIO(raw)) as archive:
                archive.extractall(OUT / 'guest', filter='data')
        except Exception as error:
            packaging = f'not bundled: {type(error).__name__}'
            (OUT / 'guest-evidence-error.txt').write_text(str(error), encoding='utf-8')
        passes = len(re.findall(r'^PASS ', results, flags=re.M))
        fails = re.findall(r'^FAIL .*', results, flags=re.M)
        chain_done = 'TELNET_CHAIN_DONE' in results
        summary = re.search(r'SUMMARY pass=(\d+) fail=(\d+)', results)
        report = {
            'passed': bool(chain_done and not fails and summary
                           and int(summary.group(2)) == 0),
            'assertions_passed': passes,
            'assertions_failed': fails,
            'chain_completed': chain_done,
            'native_arm_kernel': True,
            'camera_busybox_ash': 'stock 1.20.2 busybox sh from rootfs.tar',
            'service': 'busybox-static 1.37.0 telnetd + telnet_login.arm (sha512-crypt)',
            'rf_verified': False,
            'hardware_verified': False,
            'guest_evidence': (str(OUT / 'guest') if packaging == 'bundled' else packaging),
            'evidence_packaging': packaging,
        }
        arm_vm.write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report['passed']:
            raise RuntimeError('Telnet VM chain reported failures; see results.txt and guest evidence')
    finally:
        try:
            call('stop')
        except Exception as error:  # always stop the private VM
            (OUT / 'stop-error.txt').write_text(str(error), encoding='utf-8')


def linux_worker(action, payload):
    # This half runs inside WSL, where the Windows-style OUT that the host
    # side uses is not a valid path; map it to the /mnt equivalent first.
    global OUT
    from wsl_paths import wsl_path as _wsl_path
    OUT = Path(_wsl_path(OUT))
    arm_vm.SERIAL = SOCKET + '-serial.sock'
    arm_vm.QMP = SOCKET + '-qmp.sock'
    import os
    if action == 'start':
        try:
            arm_vm.qmp('query-status')
            raise RuntimeError('Private VM already running; not replacing it')
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        for name in (arm_vm.SERIAL, arm_vm.QMP):
            Path(name).unlink(missing_ok=True)
        (OUT / 'serial.log').write_text('')
        env = dict(os.environ, LD_LIBRARY_PATH=str(arm_vm.WORK / 'host/usr/lib/x86_64-linux-gnu'))
        command = [str(arm_vm.WORK / 'host/usr/bin/qemu-system-arm'), '-M', 'virt', '-cpu', 'cortex-a15',
                   '-m', '2048M', '-smp', '2', '-kernel', str(arm_vm.WORK / 'downloads/vmlinuz'),
                   '-initrd', str(OUT / 'initramfs.cpio.gz'), '-append', 'console=ttyAMA0 rdinit=/init panic=-1',
                   '-display', 'none', '-monitor', 'none', '-serial',
                   f'unix:{arm_vm.SERIAL},server=on,wait=off,logfile={OUT / "serial.log"}',
                   '-qmp', f'unix:{arm_vm.QMP},server=on,wait=off', '-nic', 'none', '-no-reboot']
        log = (OUT / 'qemu.log').open('ab', buffering=0)
        process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        arm_vm.write_json(OUT / 'handle.json', {'pid': process.pid, 'command': command,
                                                'serial_socket': arm_vm.SERIAL, 'qmp_socket': arm_vm.QMP})
        return {'pid': process.pid}
    if action == 'stop':
        return arm_vm.qmp('quit')
    command = base64.b64decode(payload).decode()
    return arm_vm.native_exec(command, timeout=600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', choices=['start', 'stop', 'exec'])
    parser.add_argument('payload', nargs='?', default='')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    configure()
    if args.linux:
        print(json.dumps(linux_worker(args.linux, args.payload)))
        return
    prepare()
    if not args.prepare_only:
        run()


if __name__ == '__main__':
    main()
