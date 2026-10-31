#!/bin/sh
# Six-item service box on long-press Fn, drawn by the maintained nxui.
# The 2026-10-24 binary hung on the camera before its first log line; the
# prime suspect is its fontconfig cache build for the 10.7 MB stock font.
# This launcher keeps the same menu and action files but runs the current
# nxui, which opens the font by file (no fontconfig) and logs every stage.
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
PANEL=$ROOT/fn-panel
[ -x "$ROOT/bin/nxui.arm" ] || fail "maintained executable missing: $ROOT/bin/nxui.arm"
[ -s "$PANEL/gui_mfw.NX300M" ] || fail "original menu missing: $PANEL/gui_mfw.NX300M"
[ -r "$PANEL/mfw-panel-actions.sh" ] || fail 'action script missing'
[ -s /usr/share/fonts/SDIC_GP_US_20120720.ttf ] || fail 'original firmware font missing'
[ -s "$ROOT/fonts/mfw-font.charset" ] || fail 'font coverage metadata missing'
# The action script expects the service root even though the menu root is the
# panel directory; keep both exported for the six-item historical menu.
export NX_FN_SERVICE_ROOT="$ROOT" NX300M_MOD_ROOT="$PANEL" NX_MOD_ROOT="$PANEL"
export NX_STATE_DIR="$STATE" DISPLAY=${DISPLAY:-:0}
unset LD_PRELOAD LD_AUDIT
printf 'NX_FN_PANEL maintained-nxui bytes=%s root=%s\n' \
    "$(wc -c <"$ROOT/bin/nxui.arm")" "$PANEL"
exec "$ROOT/bin/nxui.arm" --mfw-services --root "$PANEL" \
    --font-file /usr/share/fonts/SDIC_GP_US_20120720.ttf \
    --font-metadata "$ROOT/fonts/mfw-font.charset"
