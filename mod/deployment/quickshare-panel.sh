#!/bin/sh
# Selected paths arrive as arguments; the native panel validates the full batch.
set -eu
umask 077
[ "$#" -ge 1 ] && [ "$#" -le 16 ] || exit 2
ROOT=$(CDPATH= cd -P "${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}" && pwd -P)
STATE=${NX_STATE_DIR:-$ROOT/.state}
case $ROOT:$STATE in *[!A-Za-z0-9_./:-]*) exit 2 ;; esac
[ -x "$ROOT/bin/quickshare-panel.arm" ] || exit 3
exec 8> /tmp/nx300m-panel-launch.lock
flock -n 8 || exit 3
mkdir -p "$STATE/font-cache"
sed -e "s|@MOD_ROOT@|$ROOT|g" -e "s|@FONT_CACHE@|$STATE/font-cache|g" \
    "$ROOT/fonts/fonts.conf.in" > "$STATE/quickshare-fonts.conf"
export FONTCONFIG_FILE="$STATE/quickshare-fonts.conf" DISPLAY=${DISPLAY:-:0} NX_STATE_DIR="$STATE"
if [ -n "${NX300M_ARM_RUNNER:-}" ]; then
    [ "$NX300M_ARM_RUNNER" = /qemu-arm-static ] && [ -x "$NX300M_ARM_RUNNER" ] || exit 2
    exec "$NX300M_ARM_RUNNER" -cpu cortex-a9 -L / "$ROOT/bin/quickshare-panel.arm" "$@"
fi
exec "$ROOT/bin/quickshare-panel.arm" "$@"
