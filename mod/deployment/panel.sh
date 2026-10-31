#!/bin/sh
# On-demand native UI; service.sh remains the remote service supervisor.
set -eu
set -f
umask 077
fail() { printf 'NX300M panel: %s\n' "$*" >&2; exit 1; }
mode=${1:-settings}
case $mode in recipes|lut|settings|menu) ;; *) fail 'usage: panel.sh [settings|recipes|lut] [--dry-run]' ;; esac
[ "$#" -le 2 ] || fail 'usage: panel.sh [settings|recipes|lut] [--dry-run]'
force_dry=0
case ${2:-} in '') ;; --dry-run) force_dry=1 ;; *) fail 'expected --dry-run' ;; esac
configured_root=${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}
ROOT=$(CDPATH= cd -P "$configured_root" && pwd -P) || exit 1
STATE=${NX_STATE_DIR:-$ROOT/.state}
case $STATE in /*) ;; *) fail 'state directory must be absolute' ;; esac
mkdir -p "$STATE" || fail 'state directory is not writable'
STATE=$(CDPATH= cd -P "$STATE" && pwd -P) || exit 1
# These paths are embedded in fontconfig XML and passed to native mod paths.
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'mod root uses unsupported path characters' ;; esac
case $STATE in *[!A-Za-z0-9_./-]*) fail 'state directory uses unsupported path characters' ;; esac
[ "${#ROOT}" -le 160 ] || fail 'mod root exceeds the native 160-byte limit'
UI=$ROOT/bin/nxui.arm
ENGINE=$ROOT/bin/recipe_engine.arm
[ "$mode" != lut ] || ENGINE=$ROOT/bin/lut_engine.arm
[ -x "$UI" ] && [ -x "$ENGINE" ] || fail 'native panel or engine is missing or not executable'
[ -r "$ROOT/gui_ini.NX300M" ] || fail 'panel menu is missing'
[ -s "$ROOT/fonts/SDIC_GP_US_20120720.ttf" ] && [ -r "$ROOT/fonts/fonts.conf.in" ] || fail 'panel font/configuration is missing'

# Hold the launcher lock across exec: a second panel must not replace the LUT
# entry backup before discovering that the first panel owns the NXUI lock.
exec 8> /tmp/nx300m-panel-launch.lock
flock -n 8 || fail 'a native panel is already open'
exec 9<> /tmp/nxwa-nxui.lock
flock -n 9 || fail 'NXUI is already open'
mkdir -p "$STATE/font-cache"
sed -e "s|@MOD_ROOT@|$ROOT|g" -e "s|@FONT_CACHE@|$STATE/font-cache|g" \
    "$ROOT/fonts/fonts.conf.in" > "$STATE/panel-fonts.conf.tmp"
mv "$STATE/panel-fonts.conf.tmp" "$STATE/panel-fonts.conf"
export FONTCONFIG_FILE="$STATE/panel-fonts.conf" DISPLAY=${DISPLAY:-:0}
export NX300M_MOD_ROOT="$ROOT"

camera_pid() {
    case $1 in ''|*[!0-9]*) return 1 ;; esac
    [ "$1" -gt 1 ] && kill -0 "$1" 2>/dev/null || return 1
    executable=$(readlink "/proc/$1/exe" 2>/dev/null) || return 1
    case $executable in */di-camera-app|*/di-camera-app-nx300) return 0 ;; *) return 1 ;; esac
}
live=0
catalogue=$ROOT/recipes.txt
if [ "$mode" = lut ]; then
    LUT_ROOT=$ROOT
    # Only LUT data follows the verified source card. UI, engine, fonts, state
    # and cwd remain in the staged RAM runtime.
    if [ "${NX300M_MOD_SOURCE_ROOT+x}${NX300M_MOD_SOURCE_ID+x}" != '' ]; then
        [ -n "${NX300M_MOD_SOURCE_ROOT:-}" ] && [ -n "${NX300M_MOD_SOURCE_ID:-}" ] || fail 'LUT source root and identity must be provided together'
        case $NX300M_MOD_SOURCE_ROOT in /*) ;; *) fail 'LUT source root must be absolute' ;; esac
        case $NX300M_MOD_SOURCE_ROOT in *[!A-Za-z0-9_./-]*) fail 'LUT source root uses unsupported path characters' ;; esac
        [ "${#NX300M_MOD_SOURCE_ROOT}" -le 160 ] || fail 'LUT source root exceeds the native 160-byte limit'
        LUT_ROOT=$(CDPATH= cd -P "$NX300M_MOD_SOURCE_ROOT" && pwd -P) || fail 'LUT source directory is unavailable'
        [ "$LUT_ROOT" = "$NX300M_MOD_SOURCE_ROOT" ] || fail 'LUT source root must be canonical'
        [ "$(stat -c '%d:%i' "$LUT_ROOT" 2>/dev/null)" = "$NX300M_MOD_SOURCE_ID" ] || fail 'LUT source directory identity changed'
    fi
    catalogue=$LUT_ROOT/looks.txt
    [ -s "$catalogue" ] || fail 'LUT catalogue is missing'
    hook=$(cat /var/run/nx300m-lut-hook 2>/dev/null || :)
    set -- $hook
    if [ "$force_dry" -eq 0 ] && [ "$#" -eq 2 ] && camera_pid "$1" && [ "$2" = "$LUT_ROOT" ]; then
        "$ENGINE" begin || fail 'cannot capture the LUT entry selection'
        live=1
    fi
else
    [ -s "$catalogue" ] || fail 'recipe catalogue is missing'
    ready=$(cat /var/run/nx300m-recipe-ready 2>/dev/null || :)
    if [ "$force_dry" -eq 0 ] && camera_pid "$ready"; then live=1; fi
fi
if [ "$live" -eq 1 ]; then
    set -- --live
else
    printf 'NX300M panel: 当前面板为浏览模式，不应用机身设置。\n' >&2
    set -- --dry-run
fi
set -- "$@" --root "$ROOT" --recipes "$catalogue" --engine "$ENGINE" --font 'SDIC_GP_US:pixelsize=19'
[ "$mode" != lut ] || set -- "$@" --lut
if [ "$mode" = settings ] || [ "$mode" = menu ]; then
    set -- "$@" --menu gui_ini.NX300M
fi
flock -u 9
exec 9>&-
# Native hardware executes ARM directly. No emulator or second supervisor.
cd "$ROOT" || fail 'runtime directory is unavailable'
exec "$UI" "$@"
