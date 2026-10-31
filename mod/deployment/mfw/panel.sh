#!/bin/sh
# Independent MFW UI: no recipe engine or in-process camera bridge.
set -eu
set -f
umask 077
IFS=' ' read -r NX_PANEL_SCRIPT_UPTIME ignored < /proc/uptime
export NX_PANEL_SCRIPT_UPTIME
printf 'PANEL_SCRIPT trigger_ms=%s fork_ms=%s script_uptime=%s pid=%s\n' \
    "${NX_PANEL_TRIGGER_MS:-unknown}" "${NX_PANEL_FORK_MS:-unknown}" "$NX_PANEL_SCRIPT_UPTIME" "$$" >&2
panel_stage() {
    IFS=' ' read -r stage_uptime ignored < /proc/uptime
    printf 'PANEL_STAGE pid=%s name=%s uptime=%s\n' "$$" "$1" "$stage_uptime" >&2
}
fail() { printf 'NX300M panel: %s\n' "$*" >&2; exit 1; }
[ "$#" -eq 0 ] || fail 'this panel takes no arguments'
configured_root=${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}
ROOT=$(CDPATH= cd -P "$configured_root" && pwd -P) || exit 1
STATE=${NX_STATE_DIR:-$ROOT/.state}
case $STATE in /*) ;; *) fail 'state directory must be absolute' ;; esac
[ -d "$STATE" ] || mkdir -p "$STATE"
STATE=$(CDPATH= cd -P "$STATE" && pwd -P) || exit 1
case $ROOT:$STATE in *[!A-Za-z0-9_./:-]*) fail 'unsupported resource path characters' ;; esac
[ "${#ROOT}" -le 160 ] || fail 'mod root exceeds the native path limit'
UI=$ROOT/bin/nxui.arm
[ -x "$UI" ] && [ -r "$ROOT/mfw-panel-actions.sh" ] && [ -s "$ROOT/gui_mfw.NX300M" ] || fail 'panel files are missing'
stock_font=/usr/share/fonts/SDIC_GP_US_20120720.ttf
[ -s "$stock_font" ] && [ -s "$ROOT/fonts/mfw-font.charset" ] || fail 'stock panel font/coverage is missing'
# nxui acquires the launcher and UI locks before opening X11. No preparation
# writes require a separate shell lock now that the font is opened by file.
panel_stage resources
export NX300M_MOD_ROOT="$ROOT" NX_STATE_DIR="$STATE" DISPLAY=${DISPLAY:-:0}
unset LD_PRELOAD LD_AUDIT
IFS=' ' read -r NX_PANEL_PREP_UPTIME ignored < /proc/uptime
export NX_PANEL_PREP_UPTIME
exec "$UI" --mfw-services --root "$ROOT" --font-file "$stock_font" --font-metadata "$ROOT/fonts/mfw-font.charset"
