#!/bin/sh
# Run only in the private IPC namespace created by run_stock.py.
set -u
umask 000
guest="$1"
mkdir -p "$guest/dev/shm"
mount -t tmpfs -o mode=1777,nosuid,nodev tmpfs "$guest/dev/shm" || exit 4
for directory in pref adj; do
    mkdir -p "$guest/$directory"
    mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs "$guest/$directory" || exit 4
done
# The stock application writes runtime notifications here as its service UID.
mkdir -p "$guest/var/run"
mount -t tmpfs -o mode=0777,nosuid,nodev tmpfs "$guest/var/run" || exit 4
# The stock AdjLog writer omits a usable creation mode in this path. Its
# production root service masks that error; precreate a private writable log.
touch "$guest/adj/AdjLog.txt"
chmod 666 "$guest/adj/AdjLog.txt"
chroot "$guest" /qemu-arm-static -strace /usr/bin/prefman init
printf 'PREFMAN_INIT_EXIT=%s\n' "$?" >&2
reset_result=$(chroot "$guest" /qemu-arm-static -strace /usr/bin/prefman reset)
reset_status=$?
printf '%s\n' "$reset_result"
printf 'PREFMAN_RESET_EXIT=%s\n' "$reset_status" >&2
case "$reset_result" in
    *failed*) printf 'PREFMAN_DEFAULTS_LOADED=0\n' >&2; exit 4 ;;
esac
trace=$(mktemp)
result=$(chroot --userspec=65534:65534 "$guest" /qemu-arm-static -strace /usr/bin/prefman get 1 0xe0 l 2>"$trace")
status=$?
cat "$trace" >&2
printf '%s\n' "$result"
printf 'PREFMAN_CUSTOM1_READ_EXIT=%s\n' "$status" >&2
case "$result" in
    *failed*) printf 'PREFMAN_STATE_VALID=0\n' >&2; exit 4 ;;
esac
[ "$status" = 0 ] || exit 4
if grep -q 'Semaphore open Fail\|pref_get_shm.*failed' "$trace"; then
    printf 'PREFMAN_STATE_VALID=0\n' >&2
    exit 4
fi
# Stock reset restores APPPREF_FW_UPGRADE_CHECK=1 (common-menu id 0xfa).
# This private session did not upgrade firmware; leave the stock hibernation
# and updater paths intact and clear only its in-memory application flag.
result=$(chroot --userspec=65534:65534 "$guest" /qemu-arm-static -strace /usr/bin/prefman set 1 0x384 l 0 2>"$trace")
status=$?
cat "$trace" >&2
printf '%s\n' "$result"
printf 'PREFMAN_FW_UPGRADE_CHECK_SET_EXIT=%s\n' "$status" >&2
case "$result" in
    *failed*) printf 'PREFMAN_FW_UPGRADE_CHECK_VALID=0\n' >&2; exit 4 ;;
esac
if [ "$status" != 0 ] || grep -q 'Semaphore open Fail\|failed' "$trace"; then
    printf 'PREFMAN_FW_UPGRADE_CHECK_VALID=0\n' >&2
    exit 4
fi
result=$(chroot --userspec=65534:65534 "$guest" /qemu-arm-static -strace /usr/bin/prefman get 1 0x384 l 2>"$trace")
status=$?
cat "$trace" >&2
printf '%s\n' "$result"
printf 'PREFMAN_FW_UPGRADE_CHECK_READ_EXIT=%s\n' "$status" >&2
case "$result" in
    *failed*|*'Semaphore open Fail'*) printf 'PREFMAN_FW_UPGRADE_CHECK_VALID=0\n' >&2; exit 4 ;;
esac
if [ "$status" != 0 ] || grep -q 'Semaphore open Fail\|failed' "$trace" ||
   ! printf '%s\n' "$result" | grep -Eq '^[[:space:]]*value[[:space:]]*=[[:space:]]*0[[:space:]]+\(0x0+\)[[:space:]]*$'; then
    printf 'PREFMAN_FW_UPGRADE_CHECK_VALID=0\n' >&2
    exit 4
fi
printf 'PREFMAN_FW_UPGRADE_CHECK=0\n' >&2
printf 'PREFMAN_STORAGE_ACCESSIBLE=1\n' >&2
