"""Isolated full-system ARM check of the unchanged MFW-to-RAM service kit.

Reuses the cached kernel, stock-library initramfs and QEMU. Never connects to
the main emulator, a physical card or camera. Run with the project Conda Python.
"""
import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

import arm_vm

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / 'outputs/deployment/mfw-services/ram-runtime/arm-vm'
SOCKET = '/tmp/nx300m-mfw-ram-private'
BUILD_REPORT = PROJECT / 'outputs/deployment/mfw-services/build-report.json'
SERVICE_REVISION = '20261025-panel-cache'
TMPFS_LIMIT = 16 * 1024 * 1024
SERVICE_MEMORY_LIMIT = 32 * 1024 * 1024

INIT = r'''#!/rescue/busybox sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
/rescue/busybox mount -t devtmpfs devtmpfs /dev
exec </dev/ttyAMA0 >/dev/ttyAMA0 2>&1
/rescue/busybox mount -t proc proc /proc
/rescue/busybox mount -t sysfs sysfs /sys
/rescue/busybox mkdir -p /tmp /dev/pts /mnt/mmc /arm-vm/mfw-results /sys/fs/cgroup
/rescue/busybox mount -t devpts devpts /dev/pts
/rescue/busybox mount -t tmpfs -o rw,exec,mode=1777,size=16m mfw-runtime /tmp
# The large initramfs and X server remain outside this service-only budget.
# Keep the console available on setup failure so the probe can report it.
if /rescue/busybox mount -t cgroup2 none /sys/fs/cgroup &&
   /rescue/busybox grep -qw memory /sys/fs/cgroup/cgroup.controllers &&
   echo +memory > /sys/fs/cgroup/cgroup.subtree_control &&
   /rescue/busybox mkdir /sys/fs/cgroup/mfw-service &&
   echo 33554432 > /sys/fs/cgroup/mfw-service/memory.max &&
   echo 0 > /sys/fs/cgroup/mfw-service/memory.swap.max &&
   [ -r /sys/fs/cgroup/mfw-service/memory.peak ]; then
    echo ready > /arm-vm/mfw-results/cgroup-setup.txt
else
    echo unavailable > /arm-vm/mfw-results/cgroup-setup.txt
fi
/rescue/busybox ifconfig lo 127.0.0.1 up
/rescue/busybox mount --bind /arm-vm/mfw-source /mnt/mmc
/rescue/busybox mount -o remount,bind,ro /mnt/mmc
/rescue/busybox mkdir -p /tmp/.X11-unix
/rescue/busybox chmod 1777 /tmp/.X11-unix
export XKB_CONFIG_ROOT=/usr/share/X11/xkb
/rescue/busybox mv /usr/bin/xkbcomp /arm-vm/xkbcomp.debian
/rescue/busybox printf '%s\n' '#!/rescue/busybox sh' 'exec /usr/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3 --library-path /usr/lib/arm-linux-gnueabihf /arm-vm/xkbcomp.debian "$@"' > /usr/bin/xkbcomp
/rescue/busybox chmod 755 /usr/bin/xkbcomp
# Keep Debian Xvfb on its own libc; production ARM services still use the
# unchanged stock interpreter and original /lib and /usr/lib resources.
/usr/lib/arm-linux-gnueabihf/ld-linux-armhf.so.3 --library-path /usr/lib/arm-linux-gnueabihf /usr/bin/Xvfb :97 -screen 0 480x800x24 -nolisten tcp -ac -noreset -fp built-ins > /arm-vm/mfw-results/xvfb.log 2>&1 &
echo $! > /arm-vm/mfw-results/xvfb.pid
echo MFW_PRIVATE_VM_SHELL_READY
while true; do /rescue/busybox setsid /bin/sh -i < /dev/ttyAMA0 > /dev/ttyAMA0 2>&1; done
'''

PROBE = r'''#!/bin/sh
set -eux
OUT=/arm-vm/mfw-results
RAM=/tmp/nx300m-mfw
SOURCE=/mnt/mmc/nx300m-mod
CG=/sys/fs/cgroup/mfw-service
service_command() {
    # A separate shell joins before exec; the test coordinator stays outside.
    /bin/sh -c 'echo "$$" > /sys/fs/cgroup/mfw-service/cgroup.procs || exit 1; exec "$@"' sh "$@"
}
mfw_entry() {
    # Stock ash retains prefix assignments to shell functions. Set the SD
    # environment only in the external child, preserving the coordinator's
    # exported RAM root for subsequent production stop/restart invocations.
    service_command /rescue/busybox env NX300M_MOD_ROOT="$SOURCE" NX_MOD_ROOT="$SOURCE" \
        /bin/sh /mnt/mmc/autoexec.sh
}
resources() {
    label=$1
    for field in memory.max memory.swap.max memory.current memory.peak memory.events cgroup.procs; do
        [ ! -r "$CG/$field" ] || cat "$CG/$field" > "$OUT/$label.$field"
    done
    df -Pk /tmp > "$OUT/$label.tmpfs-df.txt"
    du -sk "$RAM" > "$OUT/$label.runtime-du.txt" 2>/dev/null || :
}
runtime_snapshot() {
    label=$1
    (cd "$RAM/runtime"; while IFS= read -r relative; do
        stat -c '%d:%i:%s %n' "$relative"
    done < "$SOURCE/runtime-files.list") > "$OUT/$label.runtime-identities"
    : > "$OUT/$label.stage-trees"
    for staged in "$RAM"/stage.*; do
        [ ! -d "$staged" ] || printf '%s\n' "$staged" >> "$OUT/$label.stage-trees"
    done
    [ ! -s "$OUT/$label.stage-trees" ]
    cp "$RAM/bootstrap.log" "$OUT/$label.bootstrap.log"
    resources "$label"
}
gather_logs() {
    [ ! -f "$RAM/bootstrap.log" ] || cp "$RAM/bootstrap.log" "$OUT/bootstrap.log"
    for name in mfw-entry.log remote.log supervisor.log; do
        [ ! -f "$RAM/state/$name" ] || cp "$RAM/state/$name" "$OUT/$name"
    done
    resources final
}
trap gather_logs EXIT
export DISPLAY=:97 NX_START_TIMEOUT=30 NX_STOP_TIMEOUT=10
echo MFW_FULL_ARM_BEGIN
uname -a > "$OUT/uname.txt"
grep ' /mnt/mmc ' /proc/mounts > "$OUT/source-mount.txt"
grep ' /tmp ' /proc/mounts > "$OUT/ram-mount.txt"
grep ' /mnt/mmc .* ro,' /proc/mounts
[ "$(cat "$OUT/cgroup-setup.txt")" = ready ]
[ "$(cat "$CG/memory.max")" = 33554432 ]
[ "$(cat "$CG/memory.swap.max")" = 0 ]
[ -r "$CG/memory.peak" ]
! grep -q ':/mfw-service$' /proc/1/cgroup /proc/$$/cgroup
cat /proc/1/cgroup > "$OUT/init.cgroup"
cat /proc/$$/cgroup > "$OUT/coordinator.cgroup"
while IFS= read -r relative; do
    stat -c '%s %n' "/mnt/mmc/$relative"
done < /arm-vm/package-files.list > "$OUT/package-files.stat"
wait_x=0
while [ ! -S /tmp/.X11-unix/X97 ]; do
    wait_x=$((wait_x+1)); [ "$wait_x" -lt 15 ]; sleep 1
done
xvfb_pid=$(cat "$OUT/xvfb.pid")
tr '\000' '\n' < /proc/$xvfb_pid/cmdline | grep -F -x /usr/bin/Xvfb
cat /proc/$xvfb_pid/cgroup > "$OUT/xvfb.cgroup"
! grep -q ':/mfw-service$' "$OUT/xvfb.cgroup"
mfw_entry
wait_ready() {
    attempt=0
    until [ -f "$RAM/state/remote.pid" ] && grep -q '^ready$' "$RAM/state/service.state"; do
        attempt=$((attempt+1)); [ "$attempt" -lt 45 ] || return 1; sleep 1
    done
}
wait_entry() {
    attempt=0
    while ! flock -n 6; do
        attempt=$((attempt+1)); [ "$attempt" -lt 45 ] || return 1; sleep 1
    done
    exec 6>&-
}
wait_ready || { cat "$RAM/bootstrap.log"; cat "$RAM/state/remote.log"; exit 1; }
exec 6> "$RAM/stage.lock"
wait_entry
export NX300M_MOD_ROOT="$RAM/runtime" NX_STATE_DIR="$RAM/state"
. "$RAM/runtime/common.sh"
record_alive
first=$REC_PID
printf '%s\n' "$first" > "$OUT/first.pid"
capture() {
    label=$1
    record_alive
    [ "$(readlink /proc/$REC_PID/exe)" = "$REMOTE" ]
    [ "$(stat -L -c '%d:%i' /proc/$REC_PID/exe)" = "$(stat -c '%d:%i' "$REMOTE")" ]
    readlink /proc/$REC_PID/exe > "$OUT/$label.exe"
    readlink /proc/$REC_PID/cwd > "$OUT/$label.cwd"
    cat /proc/$REC_PID/maps > "$OUT/$label.maps"
    ls -l /proc/$REC_PID/fd > "$OUT/$label.fd"
    tr '\000' '\n' < /proc/$REC_PID/environ > "$OUT/$label.environment"
    [ "$(cat "$OUT/$label.cwd")" = "$RAM/runtime" ]
    grep -q '^0::/mfw-service$' /proc/$REC_PID/cgroup
    cat /proc/$REC_PID/cgroup > "$OUT/$label.cgroup"
    ! grep -F '/mnt/mmc' "$OUT/$label.maps" "$OUT/$label.fd"
    count=0
    helpers=0
    custom_helpers=0
    usb_helpers=0
    : > "$OUT/$label.helpers"
    : > "$OUT/$label.custom-helpers"
    : > "$OUT/$label.usb-helpers"
    for entry in /proc/[0-9]*/exe; do
        target=$(readlink "$entry" 2>/dev/null) || continue
        case $target in */nx-remote-controller.arm)
            pid=${entry#/proc/}; pid=${pid%/exe}
            if [ "$pid" = "$REC_PID" ]; then count=$((count+1)); continue; fi
            helper_comm=$(cat /proc/$pid/comm)
            case $helper_comm in nx-x11-worker|nx-custom-key|nx-usb-worker) ;; *) exit 1 ;; esac
            IFS= read -r helper_stat < /proc/$pid/stat
            helper_fields=${helper_stat##*) }
            set -- $helper_fields
            [ "$2" = "$REC_PID" ]
            [ "$(stat -L -c '%d:%i' /proc/$pid/exe)" = "$REC_EXE_ID" ]
            [ "$(readlink /proc/$pid/cwd)" = / ]
            grep -q '^0::/mfw-service$' /proc/$pid/cgroup
            ! grep -F /mnt/mmc /proc/$pid/maps
            ! ls -l /proc/$pid/fd | grep -F /mnt/mmc
            if [ "$helper_comm" = nx-custom-key ]; then
                custom_helpers=$((custom_helpers+1))
                printf '%s\n' "$pid" >> "$OUT/$label.custom-helpers"
            elif [ "$helper_comm" = nx-usb-worker ]; then
                usb_helpers=$((usb_helpers+1))
                printf '%s\n' "$pid" >> "$OUT/$label.usb-helpers"
            else
                helpers=$((helpers+1))
                printf '%s\n' "$pid" >> "$OUT/$label.helpers"
            fi ;;
        esac
    done
    [ "$count" -eq 1 ]
    [ "$helpers" -le 1 ]
    [ "$custom_helpers" -le 1 ]
    [ "$usb_helpers" -le 1 ]
    printf '%s\n' "$count" > "$OUT/$label.count"
    # Resident service shells must also use RAM scripts, cwd and descriptors.
    : > "$OUT/$label.shells"
    for entry in /proc/[0-9]*/cmdline; do
        pid=${entry#/proc/}; pid=${pid%/cmdline}
        if tr '\000' '\n' 2>/dev/null < "$entry" | grep -F -x "$RAM/runtime/service.sh" >/dev/null; then
            printf 'pid=%s cwd=%s\n' "$pid" "$(readlink /proc/$pid/cwd)" >> "$OUT/$label.shells"
            ls -l /proc/$pid/fd >> "$OUT/$label.shells"
            [ "$(readlink /proc/$pid/cwd)" = "$RAM/runtime" ]
            grep -q '^0::/mfw-service$' /proc/$pid/cgroup
            ! ls -l /proc/$pid/fd | grep -F '/mnt/mmc'
        fi
    done
    [ -s "$OUT/$label.shells" ]
}
wait_worker() {
    attempt=0
    while :; do
        children=
        IFS= read -r children < /proc/$REC_PID/task/$REC_PID/children || :
        for worker in $children; do
            worker_comm=
            IFS= read -r worker_comm < /proc/$worker/comm || continue
            [ "$worker_comm" = nx-x11-worker ] || continue
            IFS= read -r worker_stat < /proc/$worker/stat
            worker_fields=${worker_stat##*) }
            set -- $worker_fields
            [ "$2" = "$REC_PID" ] && return 0
        done
        attempt=$((attempt+1)); [ "$attempt" -lt 300 ] || return 1; /rescue/busybox usleep 50000
    done
}
wait_worker_idle() {
    attempt=0
    while :; do
        children=; active=0
        IFS= read -r children < /proc/$REC_PID/task/$REC_PID/children || :
        for worker in $children; do
            worker_comm=
            IFS= read -r worker_comm < /proc/$worker/comm || continue
            [ "$worker_comm" != nx-x11-worker ] || active=1
        done
        [ "$active" -eq 0 ] && return 0
        attempt=$((attempt+1)); [ "$attempt" -lt 100 ] || return 1; /rescue/busybox usleep 50000
    done
}
capture first
runtime_snapshot first
[ ! -s "$OUT/first.helpers" ]
diagnostic=$(cat "$RAM/state/latest-diagnostic")
[ "$(cat "$diagnostic/full-diagnostics.state")" = skipped ]
[ -s "$diagnostic/health.json" ]
: > "$OUT/default-diagnostics.txt"
for skipped in camera-info camera-status mods-status network-status gallery-root camera-loading; do
    [ "$(cat "$diagnostic/$skipped.state")" = skipped ]
    [ ! -e "$diagnostic/$skipped.json" ]
    printf '%s=skipped\n' "$skipped" >> "$OUT/default-diagnostics.txt"
done
printf 'health=present full=skipped\n' >> "$OUT/default-diagnostics.txt"
count=0
: > "$OUT/compared-files.list"
while IFS= read -r relative; do
    cmp -s "$SOURCE/$relative" "$RAM/runtime/$relative"
    printf '%s\n' "$relative" >> "$OUT/compared-files.list"
    count=$((count+1))
done < "$SOURCE/runtime-files.list"
printf '%s\n' "$count" > "$OUT/compared-files.txt"
ls -l "$RAM/state" "$RAM/stage.lock" > "$OUT/ram-state.txt"
wget -T 5 -Y off -O "$OUT/health.json" http://127.0.0.1:8781/api/v1/health
wget -T 5 -Y off -O "$OUT/idle-info.json" http://127.0.0.1:8781/api/v1/camera/info
wget -T 5 -Y off -O "$OUT/network-before.json" http://127.0.0.1:8781/api/v1/network/status
/bin/sh "$RAM/runtime/mfw-panel-actions.sh" network-info > "$OUT/panel-network-before.txt"
wget -T 5 -Y off -O "$OUT/diagnostics.json" http://127.0.0.1:8781/api/v1/diagnostics
wget -T 5 -Y off -O "$OUT/bootstrap-http.log" 'http://127.0.0.1:8781/api/v1/diagnostics?file=bootstrap.log'
cmp -s "$RAM/bootstrap.log" "$OUT/bootstrap-http.log"
# Exercise the shipped launcher and UI, rather than only comparing their bytes.
# Menu is injected only into this private X server, with no camera app running.
for label in panel-first panel-second; do
    service_command /rescue/busybox env NX300M_MOD_ROOT="$RAM/runtime" NX_STATE_DIR="$RAM/state" \
        /bin/sh "$RAM/runtime/mfw-panel.sh" > "$OUT/$label.log" 2>&1 &
    panel_pid=$!
    attempt=0
    until grep -q '^FIRST_FRAME ' "$OUT/$label.log"; do
        kill -0 "$panel_pid" || { cat "$OUT/$label.log"; exit 1; }
        attempt=$((attempt+1)); [ "$attempt" -lt 25 ] || exit 1; sleep 1
    done
    if grep -q '^PANEL_FONT mode=file ' "$OUT/$label.log"; then
        stat -c '%d:%i:%Y:%s %n' /usr/share/fonts/SDIC_GP_US_20120720.ttf > "$OUT/$label-font-source.txt"
        [ ! -e "$RAM/state/font-source" ] && [ ! -e "$RAM/state/font-cache" ]
        printf 'direct-file; no menu fontconfig source/cache\n' > "$OUT/$label-font-cache.txt"
    else
        stat -c '%d:%i:%Y:%s %n' "$RAM/state/font-source" > "$OUT/$label-font-source.txt"
        : > "$OUT/$label-font-cache.txt"
        for cached_font in "$RAM/state/font-cache/"*; do
            [ ! -f "$cached_font" ] || stat -c '%d:%i:%Y:%s %n' "$cached_font" >> "$OUT/$label-font-cache.txt"
        done
        [ -s "$OUT/$label-font-cache.txt" ]
    fi
    wget -T 5 -Y off -O "$OUT/$label.osd" http://127.0.0.1:8781/api/v1/osd/get
    wget -T 5 -Y off -O "$OUT/$label-close.json" \
        'http://127.0.0.1:8781/api/v1/input/inject?key=Menu'
    attempt=0
    while kill -0 "$panel_pid" 2>/dev/null; do
        attempt=$((attempt+1)); [ "$attempt" -lt 20 ] || exit 1; sleep 1
    done
    wait "$panel_pid"
    grep '^READY ' "$OUT/$label.log"
    grep '^EXIT ' "$OUT/$label.log"
    (
        exec 8> /tmp/nx300m-panel-launch.lock
        flock -n 8
        exec 9<> /tmp/nxwa-nxui.lock
        flock -n 9
    )
    printf 'launcher=free nxui=free\n' > "$OUT/$label-locks.txt"
done
# Explicit Menu injection uses the on-demand helper. Observe its idle exit
# before checking that duplicate entry creates no background X11 activity.
wait_worker_idle
mfw_entry
sleep 1
exec 6> "$RAM/stage.lock"
wait_entry
record_alive
[ "$REC_PID" = "$first" ]
capture repeat
runtime_snapshot repeat
cmp -s "$OUT/first.runtime-identities" "$OUT/repeat.runtime-identities"
[ ! -s "$OUT/repeat.helpers" ]
# An explicit invalid key creates the on-demand helper without pressing a key.
/rescue/busybox wget -S -T 5 -Y off -O "$OUT/invalid-key.json" \
    'http://127.0.0.1:8781/api/v1/input/inject?key=NoSymbol' 2> "$OUT/invalid-key.http.log" &
input_probe=$!
# Stop only this service's verified helper. Its X wait must not starve HTTP,
# and the stalled OSD request must terminate without touching the X server.
wait_worker
[ "$(readlink /proc/$worker/exe)" = "$REMOTE" ]
[ "$(stat -L -c '%d:%i' /proc/$worker/exe)" = "$REC_EXE_ID" ]
kill -STOP "$worker"
/rescue/busybox wget -S -T 5 -Y off --post-data='' \
    -O "$OUT/stalled-osd.json" http://127.0.0.1:8781/api/v1/osd/get 2> "$OUT/stalled-osd.http.log" &
osd_probe=$!
sleep 1
wget -T 2 -Y off -O "$OUT/stalled-health.json" http://127.0.0.1:8781/api/v1/health
wget -T 2 -Y off -O "$OUT/stalled-info.json" http://127.0.0.1:8781/api/v1/camera/info
if wait "$osd_probe"; then echo 'stalled OSD incorrectly succeeded'; exit 1; fi
grep '503' "$OUT/stalled-osd.http.log"
if wait "$input_probe"; then echo 'invalid key incorrectly succeeded'; exit 1; fi
printf 'helper=%s bounded_osd_503=yes independent_http=yes\n' "$worker" > "$OUT/stalled-worker.txt"
wget -T 5 -Y off -O "$OUT/media-before.json" http://127.0.0.1:8781/api/v1/media/status
wget -T 5 -Y off -O "$OUT/storage-before.json" http://127.0.0.1:8781/api/v1/storage/status
/rescue/busybox wget -S -T 5 -Y off --post-data='{}' --header='Origin: http://127.0.0.1:8781' \
    -O "$OUT/media-pause.json" http://127.0.0.1:8781/api/v1/media/pause 2> "$OUT/media-pause.http.log"
wait_media_released() {
    media_status=$1
    attempt=0
    while :; do
        wget -T 5 -Y off -O "$media_status" http://127.0.0.1:8781/api/v1/media/status
        if grep -q '"paused":true' "$media_status" && grep -q '"service_media_released":true' "$media_status"; then break; fi
        attempt=$((attempt+1)); [ "$attempt" -lt 10 ] || return 1; sleep 1
    done
}
wait_media_released "$OUT/media-paused.json"
[ -f "$RAM/state/media-paused" ]
capture paused
wget -T 5 -Y off -O "$OUT/storage-paused.json" http://127.0.0.1:8781/api/v1/storage/status
wget -T 5 -Y off -O "$OUT/camera-info-paused.json" http://127.0.0.1:8781/api/v1/camera/info
wget -T 5 -Y off -O "$OUT/network-paused.json" http://127.0.0.1:8781/api/v1/network/status
/bin/sh "$RAM/runtime/mfw-panel-actions.sh" network-info > "$OUT/panel-network-paused.txt"
/bin/sh "$RAM/runtime/stop.sh" > "$OUT/stop.log" 2>&1
! record_alive
[ ! -e "/proc/$first" ] || ! kill -0 "$first" 2>/dev/null
# An ordinary mount/entry notification must preserve the explicit stop.
mfw_entry
sleep 1
exec 6> "$RAM/stage.lock"
wait_entry
! record_alive
# A new explicit SD restart request is introduced by the VM test coordinator;
# the production bootstrap itself never writes its source.
mount -o remount,bind,rw /mnt/mmc
printf 'private-vm-restart\n' > "$SOURCE/RESTART_SERVICE"
mount -o remount,bind,ro /mnt/mmc
mfw_entry
wait_ready
exec 6> "$RAM/stage.lock"
wait_entry
record_alive
[ "$REC_PID" != "$first" ]
printf '%s\n' "$REC_PID" > "$OUT/restarted.pid"
capture restarted
[ -e "$SOURCE/RESTART_SERVICE" ]
[ -s "$RAM/state/control-receipts/RESTART_SERVICE.id" ]
wget -T 5 -Y off -O "$OUT/restarted-health.json" http://127.0.0.1:8781/api/v1/health
wait_media_released "$OUT/media-restarted.json"
wget -T 5 -Y off -O "$OUT/storage-restarted.json" http://127.0.0.1:8781/api/v1/storage/status
wget -T 5 -Y off -O "$OUT/camera-info-restarted.json" http://127.0.0.1:8781/api/v1/camera/info
wget -T 5 -Y off -O "$OUT/network-restarted.json" http://127.0.0.1:8781/api/v1/network/status
[ -f "$RAM/state/media-paused" ]
/bin/sh "$RAM/runtime/stop.sh" > "$OUT/final-stop.log" 2>&1
! record_alive
cp "$RAM/bootstrap.log" "$OUT/bootstrap.log"
cp "$RAM/state/mfw-entry.log" "$OUT/mfw-entry.log"
cp "$RAM/state/remote.log" "$OUT/remote.log"
cp "$RAM/state/supervisor.log" "$OUT/supervisor.log"
echo MFW_FULL_ARM_PASSED
'''


def linux_path(path):
    return arm_vm.wsl_path(path)


def build_inventory():
    report = json.loads(BUILD_REPORT.read_text(encoding='utf-8'))
    inventory = {}
    for item in report['files']:
        name, size = item['path'], item['bytes']
        parts = name.split('/')
        if (not name or any(part in ('', '.', '..') for part in parts) or
                '\\' in name or ':' in name or name in inventory or
                not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ValueError('Invalid or duplicate build inventory entry: ' + repr(item))
        inventory[name] = size
    if not inventory or 'nx300m-mod/runtime-files.list' not in inventory:
        raise ValueError('Build inventory lacks the runtime manifest')
    return dict(sorted(inventory.items()))


def archive_inventory(package):
    inventory = {}
    for member in package.infolist():
        if member.is_dir() or member.filename in inventory:
            raise ValueError('Unexpected directory or duplicate ZIP member: ' + member.filename)
        inventory[member.filename] = member.file_size
    return dict(sorted(inventory.items()))


def runtime_files(inventory):
    return sorted(name.removeprefix('nx300m-mod/') for name in inventory
                  if name.startswith('nx300m-mod/'))


def initramfs():
    base = arm_vm.WORK / 'initramfs.cpio.gz'
    stream = io.BytesIO()
    arm_vm.cpio_add(stream, 'arm-vm', 0o40755)
    arm_vm.cpio_add(stream, 'init', 0o100755, INIT.encode(), 2)
    arm_vm.cpio_add(stream, 'arm-vm/mfw-probe.sh', 0o100755, PROBE.encode(), 3)
    candidate = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))
    arm_vm.cpio_add(stream, 'arm-vm/package-files.list', 0o100644,
                    ''.join(name + '\n' for name in candidate['inventory']).encode('ascii'))
    directories = set()
    stage = OUT / 'source-files'
    for path in stage.rglob('*'):
        if path.is_dir(): directories.add('arm-vm/mfw-source/' + path.relative_to(stage).as_posix())
        if path.is_file():
            relative = 'arm-vm/mfw-source/' + path.relative_to(stage).as_posix()
            directories.update(p.as_posix() for p in Path(relative).parents if str(p) != '.')
    for directory in sorted(directories):
        if directory != 'arm-vm': arm_vm.cpio_add(stream, directory, 0o40755)
    for path in stage.rglob('*'):
        if path.is_file():
            mode = 0o100755 if path.suffix == '.sh' or path.parent.name == 'bin' else 0o100644
            arm_vm.cpio_add(stream, 'arm-vm/mfw-source/' + path.relative_to(stage).as_posix(), mode, path.read_bytes())
    arm_vm.cpio_add(stream, 'TRAILER!!!', 0)
    image = OUT / 'initramfs.cpio.gz'
    with base.open('rb') as source, image.open('wb') as target:
        shutil.copyfileobj(source, target)
        target.write(gzip.compress(stream.getvalue(), mtime=0))


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    base = arm_vm.WORK / 'initramfs.cpio.gz'
    if not base.is_file(): raise ValueError('Cached ARM VM initramfs is required')
    archive = PROJECT / 'outputs/deployment/mfw-services/nx300m-mfw-services.zip'
    inventory = build_inventory()
    stage = OUT / 'source-files'
    if stage.exists():
        assert stage.parent.resolve() == OUT.resolve() and not stage.is_symlink()
        shutil.rmtree(stage)
    with zipfile.ZipFile(archive) as package:
        if archive_inventory(package) != inventory:
            raise ValueError('Complete ZIP inventory differs from build-report.json')
        package.extractall(stage)
        for name in inventory:
            if (stage / name).read_bytes() != package.read(name):
                raise ValueError('Staged candidate bytes differ from ZIP: ' + name)
    expected_runtime = runtime_files(inventory)
    actual_runtime = (stage / 'nx300m-mod/runtime-files.list').read_text(encoding='ascii').splitlines()
    if len(actual_runtime) != len(set(actual_runtime)) or sorted(actual_runtime) != expected_runtime:
        raise ValueError('Runtime manifest does not cover exactly the build inventory runtime files')
    arm_vm.write_json(OUT / 'candidate.json', {
        'archive': str(archive), 'archive_modified_epoch': archive.stat().st_mtime,
        'archive_bytes': archive.stat().st_size,
        'remote_bytes': (stage / 'nx300m-mod/bin/nx-remote-controller.arm').stat().st_size,
        'package_files': len(inventory), 'runtime_files': len(expected_runtime),
        'inventory': inventory, 'runtime_file_paths': expected_runtime,
        'snapshot_directory': str(stage)})
    (stage / 'DCIM').mkdir(exist_ok=True)
    # Only init and the test coordinator are overridden. No production asset,
    # process-identity function, ARM binary or ready record is substituted.
    initramfs()
    print('Prepared private ARM initramfs and read-only bind source; cached base unchanged', flush=True)


def linux_worker(action, payload):
    arm_vm.SERIAL = SOCKET + '-serial.sock'
    arm_vm.QMP = SOCKET + '-qmp.sock'
    if action == 'start':
        import os
        try:
            arm_vm.qmp('query-status')
            raise RuntimeError('Private VM already running; not replacing it')
        except (FileNotFoundError, ConnectionRefusedError): pass
        for name in (arm_vm.SERIAL, arm_vm.QMP): Path(name).unlink(missing_ok=True)
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
    if action == 'stop': return arm_vm.qmp('quit')
    command = base64.b64decode(payload).decode()
    return arm_vm.native_exec(command, timeout=360)


def call(action, command=''):
    encoded = base64.b64encode(command.encode()).decode()
    result = subprocess.run(['wsl.exe', '-d', 'Debian', '-u', 'root', '-e', 'python3',
                             linux_path(__file__), '--linux', action, encoded],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=375)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def extract_evidence(collected, require_complete=True):
    import tarfile
    if collected['exit_code']:
        raise RuntimeError('Guest evidence collection failed: ' + collected['output'])
    lines = collected['output'].splitlines()
    begin = next((i for i, line in enumerate(lines) if line.strip() == 'MFW_EVIDENCE_BEGIN'), None)
    end = next((i for i, line in enumerate(lines) if line.strip() == 'MFW_EVIDENCE_END'), None)
    if begin is None or end is None or end <= begin:
        raise ValueError('Missing complete guest evidence framing')
    encoded = [line.strip() for line in lines[begin+1:end] if line.strip()]
    if any(not re.fullmatch('[A-Za-z0-9+/=]+', line) for line in encoded):
        raise ValueError('Non-base64 output interrupted guest evidence; inspect collection.json')
    raw = base64.b64decode(''.join(encoded), validate=True)
    (OUT / 'guest-evidence.tar.gz').write_bytes(raw)
    # tarfile may stop at a zero header before reading gzip's final CRC. Force
    # full decompression before trusting any member or touching old evidence.
    unpacked = gzip.decompress(raw)
    required = {
        'diagnostics.json', 'compared-files.txt', 'compared-files.list', 'package-files.stat',
        'idle-info.json', 'first.pid', 'restarted.pid', 'cgroup-setup.txt',
        'default-diagnostics.txt',
        'init.cgroup', 'coordinator.cgroup', 'xvfb.cgroup', 'ram-mount.txt',
        'stalled-health.json', 'stalled-info.json', 'stalled-osd.http.log', 'invalid-key.http.log',
        'camera-info-paused.json', 'camera-info-restarted.json',
        'panel-network-before.txt', 'panel-network-paused.txt',
        'bootstrap.log', 'remote.log', 'supervisor.log', 'mfw-entry.log', 'final-stop.log',
    }
    for label in ('first', 'repeat', 'paused', 'restarted'):
        required.update(label + '.' + suffix for suffix in
                        ('exe', 'cwd', 'maps', 'fd', 'environment', 'helpers', 'custom-helpers',
                         'usb-helpers', 'count', 'shells', 'cgroup'))
    for label in ('first', 'repeat'):
        required.update(label + '.' + suffix for suffix in
                        ('runtime-identities', 'stage-trees', 'bootstrap.log', 'runtime-du.txt'))
    for label in ('first', 'repeat', 'final'):
        required.update(label + '.' + suffix for suffix in
                        ('memory.max', 'memory.swap.max', 'memory.current', 'memory.peak',
                         'memory.events', 'cgroup.procs', 'tmpfs-df.txt'))
    for label in ('panel-first', 'panel-second'):
        required.update((label + '.log', label + '-close.json', label + '-locks.txt', label + '.osd',
                         label + '-font-source.txt', label + '-font-cache.txt'))
    for label in ('before', 'paused', 'restarted'):
        required.update(prefix + '-' + label + '.json' for prefix in ('media', 'storage', 'network'))
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode='r:') as archive:
        names = archive.getnames()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate guest evidence archive members')
        missing = {'mfw-results/' + name for name in required} - set(names)
        if missing and require_complete:
            raise ValueError('Guest evidence archive is incomplete: ' + ', '.join(sorted(missing)))
        guest = OUT / 'guest'
        if guest.exists():
            assert guest.parent.resolve() == OUT.resolve() and not guest.is_symlink()
            shutil.rmtree(guest)
        archive.extractall(guest, filter='data')
    arm_vm.write_json(OUT / 'evidence-integrity.json', {
        'gzip_crc_valid': True, 'archive_members': len(names),
        'required_evidence_members': len(required), 'missing_evidence_members': sorted(missing),
        'extracted_into_clean_directory': True})


def resource_report(guest):
    observations = {}
    for label in ('first', 'repeat', 'final'):
        values = {field: int((guest / (label + '.' + field)).read_text(encoding='ascii'))
                  for field in ('memory.max', 'memory.swap.max', 'memory.current', 'memory.peak')}
        events = dict(line.split() for line in
                      (guest / (label + '.memory.events')).read_text(encoding='ascii').splitlines())
        events = {key: int(value) for key, value in events.items()}
        if (values['memory.max'] != SERVICE_MEMORY_LIMIT or values['memory.swap.max'] != 0 or
                not 0 < values['memory.peak'] <= SERVICE_MEMORY_LIMIT or
                any(events.get(key, -1) != 0 for key in ('oom', 'oom_kill'))):
            raise ValueError('Service memory budget was unavailable or exceeded: ' + repr((values, events)))
        # POSIX df -Pk reports 1024-byte blocks, independent of stock defaults.
        row = (guest / (label + '.tmpfs-df.txt')).read_text(encoding='ascii').splitlines()[-1].split()
        if len(row) != 6 or row[-1] != '/tmp' or int(row[1]) * 1024 != TMPFS_LIMIT:
            raise ValueError('Private /tmp did not have the required 16 MiB quota: ' + repr(row))
        observations[label] = {
            **values, 'memory.events': events,
            'tmpfs': {'capacity_bytes': int(row[1]) * 1024,
                      'used_bytes': int(row[2]) * 1024, 'available_bytes': int(row[3]) * 1024},
            'remaining_pids': (guest / (label + '.cgroup.procs')).read_text(encoding='ascii').split(),
        }
    return observations


def run():
    (OUT / 'report.json').unlink(missing_ok=True)
    call('start')
    try:
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            text = (OUT / 'serial.log').read_text(encoding='utf-8', errors='replace') if (OUT / 'serial.log').exists() else ''
            if 'MFW_PRIVATE_VM_SHELL_READY' in text: break
            time.sleep(1)
        else: raise RuntimeError('Private ARM boot timed out; inspect serial/qemu logs')
        try:
            result = call('exec', '/bin/sh /arm-vm/mfw-probe.sh')
        except Exception as exc:
            # Keep guest failure evidence even when serial coordination times
            # out under host load; a timeout is never a passing service check.
            result = {'exit_code': 124, 'output': str(exc)}
        arm_vm.write_json(OUT / 'execution.json', result)
        # Always collect current evidence, including failures, through serial.
        # Kernel console diagnostics can otherwise splice into a base64 line.
        # This changes only the private guest's console verbosity.
        collected = call('exec', "set -e; /rescue/busybox dmesg -n 1; /rescue/busybox tar -czf /arm-vm/mfw-evidence.tar.gz -C /arm-vm mfw-results; printf 'MFW_EVIDENCE_BEGIN\\n'; /rescue/busybox base64 /arm-vm/mfw-evidence.tar.gz; printf 'MFW_EVIDENCE_END\\n'")
        arm_vm.write_json(OUT / 'collection.json', collected)
        successful = not result['exit_code'] and 'MFW_FULL_ARM_PASSED' in result['output']
        extract_evidence(collected, require_complete=successful)
        if result['exit_code'] or 'MFW_FULL_ARM_PASSED' not in result['output']:
            raise RuntimeError('Full ARM RAM-service integration failed; inspect execution.json and guest evidence')
        guest = OUT / 'guest/mfw-results'
        candidate = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))
        inventory = candidate['inventory']
        if candidate['package_files'] != len(inventory) or candidate['runtime_file_paths'] != runtime_files(inventory):
            raise ValueError('Candidate inventory and derived runtime paths disagree')
        actual_inventory = {}
        for line in (guest / 'package-files.stat').read_text(encoding='utf-8').splitlines():
            size, name = line.split(' ', 1)
            if not name.startswith('/mnt/mmc/') or name.removeprefix('/mnt/mmc/') in actual_inventory:
                raise ValueError('Invalid actual guest package inventory: ' + line)
            actual_inventory[name.removeprefix('/mnt/mmc/')] = int(size)
        if actual_inventory != inventory:
            raise ValueError('Actual guest complete package differs from candidate build inventory')
        compared = (guest / 'compared-files.list').read_text(encoding='ascii').splitlines()
        if len(compared) != len(set(compared)) or sorted(compared) != candidate['runtime_file_paths']:
            raise ValueError('Actual byte comparisons did not cover exactly the candidate runtime files')
        if (int((guest / 'compared-files.txt').read_text(encoding='utf-8')) != len(compared) or
                candidate['runtime_files'] != len(compared)):
            raise ValueError('Candidate and actual runtime comparison counts disagree')
        resources = resource_report(guest)
        if (guest / 'first.runtime-identities').read_bytes() != (guest / 'repeat.runtime-identities').read_bytes():
            raise ValueError('Duplicate entry replaced a runtime file inode')
        for label, staging_entries, skipped in [('first', 1, 0), ('repeat', 2, 1)]:
            if (guest / (label + '.stage-trees')).read_text(encoding='ascii').strip():
                raise ValueError('Entry retained a second staging or previous runtime tree')
            log = (guest / (label + '.bootstrap.log')).read_text(encoding='utf-8')
            if (len(re.findall(r'^RAM staging source=', log, re.M)) != staging_entries or
                    len(re.findall(r'^RAM memory total=', log, re.M)) != 1 or
                    log.count('RAM runtime bytes unchanged; full staging copy skipped') != skipped):
                raise ValueError('First/repeated entry did not perform one initial copy and skip the repeat')
        diagnostics = json.loads((guest / 'diagnostics.json').read_text(encoding='utf-8'))
        if diagnostics.get('storage') != 'tmpfs': raise ValueError('Diagnostics did not use tmpfs')
        idle_info = json.loads((guest / 'idle-info.json').read_text(encoding='utf-8'))
        if idle_info.get('service_revision') != SERVICE_REVISION:
            raise ValueError('ARM runtime did not execute the startup-fix revision')
        custom = idle_info.get('custom_key', {})
        if custom.get('enabled') is not True or custom.get('state') in ('ready', 'holding', 'waiting-release') or custom.get('triggers') != 0:
            raise ValueError('Custom key must remain unavailable without a stock camera process: ' + repr(custom))
        if idle_info.get('x11') != {'background_enabled': False, 'helper_running': False, 'held_input': False}:
            raise ValueError('Default service unexpectedly retains X11 activity: ' + repr(idle_info.get('x11')))
        if (guest / 'first.helpers').read_text(encoding='utf-8').strip() or (guest / 'repeat.helpers').read_text(encoding='utf-8').strip():
            raise ValueError('Default idle service unexpectedly starts an X11 helper')
        panel_timing = {}
        if ((guest / 'panel-first-font-source.txt').read_bytes() !=
                (guest / 'panel-second-font-source.txt').read_bytes() or
                (guest / 'panel-first-font-cache.txt').read_bytes() !=
                (guest / 'panel-second-font-cache.txt').read_bytes()):
            raise ValueError('Reopening panel changed the font resource or its cache metadata')
        for label in ('panel-first', 'panel-second'):
            panel_log = (guest / (label + '.log')).read_text(encoding='utf-8')
            if 'Fontconfig warning:' in panel_log:
                raise ValueError('Panel font configuration fell back to defaults: ' + label)
            if not all(re.search(r'^' + event + r' ', panel_log, re.M) for event in ('READY', 'FIRST_FRAME', 'EXIT')):
                raise ValueError('Shipped panel did not open, render and close: ' + label)
            timing = re.search(r'^FIRST_FRAME process_ms=(\d+) launch_ms=(\d+)', panel_log, re.M)
            if not timing:
                raise ValueError('Missing measured panel startup timing: ' + label)
            panel_timing[label] = {'process_ms': int(timing[1]), 'launch_ms': int(timing[2])}
            init_timing = re.search(r'^INIT (.+)$', panel_log, re.M)
            if init_timing:
                panel_timing[label]['initialization'] = {
                    key: int(value) for key, value in re.findall(r'(\w+_ms)=(\d+)', init_timing[1])}
            if 'MFW status=' not in panel_log or panel_log.index('FIRST_FRAME ') > panel_log.index('MFW status='):
                raise ValueError('Service status work started before the first panel frame: ' + label)
            # Keep the actual ARM/Xvfb pixels for visual inspection of first
            # paint, independent of the process's FIRST_FRAME log record.
            import struct
            from PIL import Image
            packet = (guest / (label + '.osd')).read_bytes()
            stride = 8 + 80 * 40 * 4
            if (len(packet) != 12 + 120 * stride or
                    struct.unpack_from('>4H', packet) != (800, 480, 80, 40) or packet[-4:] != b'\xff' * 4):
                raise ValueError('Invalid full panel OSD packet: ' + label)
            frame = Image.new('RGB', (800, 480))
            seen = set()
            for offset in range(8, len(packet)-4, stride):
                x, y, _ = struct.unpack_from('>HHI', packet, offset)
                if x % 80 or y % 40 or x >= 800 or y >= 480 or (x, y) in seen:
                    raise ValueError('Invalid panel OSD tile: ' + label)
                seen.add((x, y))
                tile = Image.frombytes('RGBA', (80, 40), packet[offset+8:offset+stride])
                frame.paste(tile.convert('RGB'), (x, y))
            frame.save(OUT / (label + '.png'))
            if json.loads((guest / (label + '-close.json')).read_text(encoding='utf-8')).get('ok') is not True:
                raise ValueError('Private panel Menu input failed: ' + label)
            if (guest / (label + '-locks.txt')).read_text(encoding='utf-8').strip() != 'launcher=free nxui=free':
                raise ValueError('Shipped panel retained a UI lock: ' + label)
        if json.loads((guest / 'stalled-health.json').read_text(encoding='utf-8')).get('ok') is not True:
            raise ValueError('Health failed while X helper was stopped')
        if json.loads((guest / 'stalled-info.json').read_text(encoding='utf-8')).get('model') != 'NX300M':
            raise ValueError('Camera info failed while X helper was stopped')
        if not re.search(r'HTTP/[0-9.]+ 503\b', (guest / 'stalled-osd.http.log').read_text(encoding='utf-8')):
            raise ValueError('Stopped X helper did not cause bounded OSD failure')
        if not re.search(r'HTTP/[0-9.]+ (400|503)\b', (guest / 'invalid-key.http.log').read_text(encoding='utf-8')):
            raise ValueError('Invalid input was accepted or failed without an HTTP response')
        media_before = json.loads((guest / 'media-before.json').read_text(encoding='utf-8'))
        media_paused = json.loads((guest / 'media-paused.json').read_text(encoding='utf-8'))
        media_restarted = json.loads((guest / 'media-restarted.json').read_text(encoding='utf-8'))
        if not media_before.get('available') or media_before.get('paused'):
            raise ValueError('Fresh RAM service did not expose available, active media access')
        if not re.search(r'HTTP/[0-9.]+ 202\b', (guest / 'media-pause.http.log').read_text(encoding='utf-8')):
            raise ValueError('Real HTTP media pause did not return 202')
        for status in (media_paused, media_restarted):
            expected = {'available': True, 'paused': True, 'service_media_released': True,
                        'file_connections': 0, 'quickshare_running': False, 'panel_actions_blocked': True,
                        'pause_persisted': True,
                        'scope': 'remote_service_only', 'stock_usb_handoff_verified': False, 'resume_supported': False}
            if any(status.get(key) != value for key, value in expected.items()):
                raise ValueError('Media release or restart persistence status is inconsistent: ' + repr(status))
        if media_paused.get('pause_reason') != 'manual' or media_restarted.get('pause_reason') != 'restored':
            raise ValueError('Restart did not restore the explicit RAM pause marker')
        storage = {}
        for label, paused in [('before', False), ('paused', True), ('restarted', True)]:
            status = json.loads((guest / ('storage-' + label + '.json')).read_text(encoding='utf-8'))
            expected = {'raw_backing': 'unknown', 'usb_cable': 'unknown', 'usb_mode': None,
                        'lun_nodes_found': 0, 'media_paused': paused, 'observation_only': True,
                        'stock_usb_handoff_verified': False, 'resume_supported': False}
            if any(status.get(key) != value for key, value in expected.items()):
                raise ValueError('Generic VM incorrectly reports camera USB evidence: ' + repr(status))
            storage[label] = status
        for label in ['paused', 'restarted']:
            info = json.loads((guest / ('camera-info-' + label + '.json')).read_text(encoding='utf-8'))
            if info.get('model') != 'NX300M' or info.get('media_paused') is not True:
                raise ValueError('Camera info was not readable while paused: ' + repr(info))
        networks = {}
        for label, paused in [('before', False), ('paused', True), ('restarted', True)]:
            status = json.loads((guest / ('network-' + label + '.json')).read_text(encoding='utf-8'))
            expected = {'http_port': 8781, 'broadcast_port': 5681, 'broadcast_enabled': False,
                        'media_paused': paused, 'interfaces': [], 'mac_address': None, 'instance_id': None}
            if any(status.get(key) != value for key, value in expected.items()):
                raise ValueError('Networkless ARM VM incorrectly reports a camera network: ' + repr(status))
            networks[label] = status
        for label in ['before', 'paused']:
            panel = (guest / ('panel-network-' + label + '.txt')).read_text(encoding='utf-8')
            if '当前没有非回环 IPv4 地址' not in panel:
                raise ValueError('Native panel failed to report absent network while media was ' + label)
        archive = PROJECT / 'outputs/deployment/mfw-services/nx300m-mfw-services.zip'
        archive_current = (archive.stat().st_size == candidate['archive_bytes'] and
                           archive.stat().st_mtime == candidate['archive_modified_epoch'])
        if not archive_current: raise ValueError('Candidate archive metadata changed during the final integration run')
        if build_inventory() != inventory:
            raise ValueError('Current build report differs from the candidate complete inventory')
        with zipfile.ZipFile(archive) as package:
            if archive_inventory(package) != inventory:
                raise ValueError('Current ZIP differs from the candidate complete inventory')
            for name in inventory:
                if (Path(candidate['snapshot_directory']) / name).read_bytes() != package.read(name):
                    raise ValueError('Candidate source bytes differ from the current ZIP: ' + name)
        arm_vm.write_json(OUT / 'report.json', {
            'passed': True, 'scope': 'Unchanged full MFW package -> autoexec -> RAM stage -> actual ARM remote with stock libraries',
            'package_files': len(inventory), 'runtime_files_compared': len(compared),
            'candidate': candidate, 'candidate_archive_metadata_matches_current_build': archive_current,
            'candidate_build_report_zip_and_actual_guest_inventory_match': True,
            'service_memory_budget': {'mechanism': 'cgroup v2', 'memory_max_bytes': SERVICE_MEMORY_LIMIT,
                                      'swap_max_bytes': 0, 'observations': resources,
                                      'scope': 'MFW startup descendants and explicitly launched production panels',
                                      'excluded': ['initramfs baseline', 'init', 'Xvfb', 'test coordinator and HTTP clients'],
                                      'whole_camera_memory_simulation': False},
            'tmpfs_quota_bytes': TMPFS_LIMIT, 'guest_ram_bytes': 2 * 1024**3,
            'guest_ram_purpose': 'hold full stock initramfs; service cgroup budget remains enforced',
            'first_entry_one_published_runtime_and_no_retained_staging_tree': True,
            'repeat_entry_preserved_runtime_inodes_and_skipped_full_copy': True,
            'native_arm_kernel': True, 'identity_checks_unchanged': True,
            'remote_and_supervisor_no_sd_cwd_fd_maps': True, 'duplicate_entry_same_pid': True,
            'default_idle_and_repeat_entry_no_x11_helper': True,
            'usb_worker_verified_separately_from_x11_helper': True,
            'default_startup_self_check': 'health only; other probes remain explicit test-coordinator requests',
            'mfw_panel_launcher_open_menu_close_and_reopen': True,
            'mfw_panel_first_frame_timing_ms': panel_timing,
            'mfw_panel_pixels': ['panel-first.png', 'panel-second.png'],
            'mfw_panel_reopen_preserved_font_resource_metadata': True,
            'mfw_panel_font_mode': 'file' if 'PANEL_FONT mode=file ' in (guest / 'panel-first.log').read_text(encoding='utf-8') else 'match',
            'mfw_panel_both_ui_locks_released_after_each_close': True,
            'service_revision': idle_info['service_revision'],
            'invalid_key_rejected_without_keydown': True,
            'stalled_x11_helper_osd_503_and_http_responsive': True,
            'stop_and_explicit_restart': True, 'manual_stop_respected': True,
            'health_and_ram_diagnostics': True, 'main_emulator_touched': False,
            'real_HTTP_media_pause_status': 202, 'media_pause_persisted_after_restart': True,
            'media_before': media_before, 'media_paused': media_paused, 'media_restarted': media_restarted,
            'storage_observation': storage, 'camera_info_readable_while_paused_and_after_restart': True,
            'network_status': networks, 'network_status_readable_while_paused_and_after_restart': True,
            'native_panel_network_action_before_and_during_media_pause': True,
            'source_fixture': 'read-only bind from private initramfs RAM root; coordinator writes explicit restart marker',
            'not_verified': ['whole-camera memory pressure or available physical camera RAM',
                             'physical camera/SD/block-device/exFAT/FAT/unmount behavior', 'camera application', 'USB/UMS coordination']})
        print('PASS full ARM MFW RAM integration: ' + str(OUT / 'report.json'))
    except Exception as exc:
        failure = {'passed': False, 'failure': str(exc), 'main_emulator_touched': False,
                   'execution': str(OUT / 'execution.json'),
                   'evidence_integrity': str(OUT / 'evidence-integrity.json'),
                   'guest_evidence': str(OUT / 'guest/mfw-results')}
        guest = OUT / 'guest/mfw-results'
        failure['resource_evidence'] = {
            path.name: path.read_text(encoding='utf-8').strip()
            for path in guest.glob('*') if path.is_file() and
            any(path.name.endswith('.' + field) for field in
                ('memory.max', 'memory.swap.max', 'memory.current', 'memory.peak',
                 'memory.events', 'tmpfs-df.txt'))}
        arm_vm.write_json(OUT / 'report.json', failure)
        raise
    finally:
        call('stop')
        report_path = OUT / 'report.json'
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['private_vm_stopped'] = True
            arm_vm.write_json(report_path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--linux', action='store_true')
    parser.add_argument('action', choices=['prepare', 'refresh', 'run', 'start', 'stop', 'exec'])
    parser.add_argument('payload', nargs='?', default='')
    args = parser.parse_args()
    if args.linux:
        print(json.dumps(linux_worker(args.action, args.payload)))
    elif args.action == 'prepare': prepare()
    elif args.action == 'refresh': initramfs()
    elif args.action == 'run': run()
    else: print(json.dumps(call(args.action, args.payload)))


if __name__ == '__main__': main()
