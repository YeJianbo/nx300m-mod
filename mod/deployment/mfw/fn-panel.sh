#!/bin/sh
# Custom long-press panel, using the stock font directly without fontconfig.
set -eu
set -f
umask 077
fail() { printf 'NX_FN_PANEL: %s\n' "$*" >&2; exit 1; }
[ "$#" -eq 0 ] || exit 64
ROOT=$(CDPATH= cd -P "${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}" && pwd -P)
# The RAM state log dies with the camera; this panel's stderr must survive on
# the card so an unresponsive physical boot can still be diagnosed.
if [ -n "${NX_FN_KEY_LOG:-}" ]; then
    fn_log_dir=$(dirname "$NX_FN_KEY_LOG")
    if [ -d "$fn_log_dir" ] && [ -w "$fn_log_dir" ]; then
        exec >>"$NX_FN_KEY_LOG" 2>&1
        printf '%s fn panel launcher start root=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ROOT"
    fi
fi
STATE=${NX_STATE_DIR:-$ROOT/.state}
case $ROOT:$STATE in *[!A-Za-z0-9_./:-]*) exit 64 ;; esac
mode=--mfw-services
if [ -f "$ROOT/PREVIEW_QUICK_PANEL" ]; then
    # Keep the running graph and its selected LUT while using the menu.
    # An automatic background restore raced the next LUT load and removed
    # the backend marker needed by selection sync. Stop is an explicit action.
    mode=--mfw-quick-preview
fi
PANEL=$ROOT/fn-panel
if [ ! -x "$ROOT/bin/nxui.arm" ]; then
    fail "maintained executable missing: $ROOT/bin/nxui.arm"
fi
[ -s "$PANEL/gui_mfw.NX300M" ] || fail "original menu missing: $PANEL/gui_mfw.NX300M"
if [ "$mode" = --mfw-quick-preview ]; then
    [ -s "$PANEL/gui_preview_quick.NX300M" ] || fail 'quick menu missing'
fi
[ -r "$PANEL/mfw-panel-actions.sh" ] || fail 'action script missing'
[ -s /usr/share/fonts/SDIC_GP_US_20120720.ttf ] || fail 'original firmware font missing'
# Actions use the service root while menu files live in its panel directory.
export NX_FN_SERVICE_ROOT="$ROOT" NX300M_MOD_ROOT="$PANEL" NX_MOD_ROOT="$PANEL"
export NX_STATE_DIR="$STATE" DISPLAY=${DISPLAY:-:0}
unset LD_PRELOAD LD_AUDIT
printf 'NX_FN_PANEL maintained-nxui bytes=%s root=%s\n' \
    "$(stat -c '%s' "$ROOT/bin/nxui.arm")" "$PANEL"
exec "$ROOT/bin/nxui.arm" "$mode" --root "$PANEL" \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf
