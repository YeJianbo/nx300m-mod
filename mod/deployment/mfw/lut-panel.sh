#!/bin/sh
# Explicit LUT UI only; no key listener, service start or camera launch.
set -eu
set -f
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'NX300M LUT panel: %s\n' "$*" >&2; exit 1; }
force_dry=0
lifetime=
while [ "$#" -gt 0 ]; do
    case $1 in
        --dry-run) force_dry=1 ;;
        --lifetime-ms)
            [ "$#" -ge 2 ] || fail 'lifetime needs a value'
            case $2 in ''|*[!0-9]*) fail 'lifetime must be milliseconds' ;; esac
            lifetime=$2; shift ;;
        *) fail 'expected --dry-run or --lifetime-ms' ;;
    esac
    shift
done
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
SOURCE=${NX300M_MOD_SOURCE_ROOT:-$HERE}
ROOT=$(CDPATH= cd -P "$SOURCE" && pwd -P) || fail 'LUT data directory unavailable'
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'unsupported LUT data path' ;; esac
[ "${#ROOT}" -le 160 ] || fail 'LUT data root exceeds 160 bytes'
source_id=$(stat -c '%d:%i' "$ROOT") || fail 'cannot inspect LUT source identity'
if [ "${NX300M_MOD_SOURCE_ROOT+x}${NX300M_MOD_SOURCE_ID+x}" != '' ]; then
    [ -n "${NX300M_MOD_SOURCE_ROOT:-}" ] && [ -n "${NX300M_MOD_SOURCE_ID:-}" ] || fail 'source root and identity must be provided together'
    [ "$ROOT" = "$SOURCE" ] && [ "$source_id" = "$NX300M_MOD_SOURCE_ID" ] || fail 'LUT source identity changed'
fi
# A staged service may call this script from its verified RAM asset directory.
CODE=$HERE
for asset in bin/nxui.arm bin/lut_engine.arm fonts/mfw-font.charset; do
    [ -s "$CODE/$asset" ] && [ ! -L "$CODE/$asset" ] || fail "panel asset unavailable: $asset"
done
[ -s "$ROOT/looks.txt" ] || fail 'LUT catalogue unavailable'
stock_font=/usr/share/fonts/SDIC_GP_US_20120720.ttf
[ -s "$stock_font" ] || fail 'original firmware font unavailable'
ram_tmp=$(CDPATH= cd -P /tmp && pwd -P) || fail '/tmp unavailable'
awk -v path="$ram_tmp" '
    $2=="/" || path==$2 || index(path,$2 "/")==1 {
        if (length($2)>best) {best=length($2); type=$3; options=$4}
    }
    END {exit !(type=="tmpfs" && options ~ /(^|,)rw(,|$)/ && options !~ /(^|,)noexec(,|$)/)}
' /proc/mounts || fail '/tmp must resolve to writable executable tmpfs'
# The cycle flash runs with NXUI_LOCK_TAG=-flash so it never blocks the real
# panel a user may open while the flash is still on screen.
exec 8>"$ram_tmp/nx300m-panel-launch${NXUI_LOCK_TAG:-}.lock"
flock -n 8 || fail 'a native panel is already open'
runtime=$(mktemp -d "$ram_tmp/nx300m-lut-panel.XXXXXX") || fail 'cannot allocate RAM panel directory'
ram_assets=0
if [ -n "${NX300M_MFW_RAM_BASE:-}" ] && [ "$CODE" = "$NX300M_MFW_RAM_BASE/runtime" ]; then
    case $(stat -f -c '%T' "$CODE") in tmpfs|ramfs) ram_assets=1 ;; esac
fi
for asset in nxui.arm lut_engine.arm; do
    # Staged generations are published read-only and replaced by rename, never
    # edited in place. A private hard link pins the same inode across service
    # replacement, without copying and rereading the executable on every open.
    if [ "$ram_assets" = 1 ] && [ -x "$CODE/bin/$asset" ] && ln "$CODE/bin/$asset" "$runtime/$asset"; then
        :
    else
        cp "$CODE/bin/$asset" "$runtime/$asset" && cmp -s "$CODE/bin/$asset" "$runtime/$asset" || fail "RAM copy differs: $asset"
        chmod 500 "$runtime/$asset"
    fi
done
if [ "$ram_assets" = 1 ] && ln "$CODE/fonts/mfw-font.charset" "$runtime/mfw-font.charset"; then
    :
else
    cp "$CODE/fonts/mfw-font.charset" "$runtime/mfw-font.charset" && \
        cmp -s "$CODE/fonts/mfw-font.charset" "$runtime/mfw-font.charset" || fail 'RAM font coverage copy differs'
fi
export DISPLAY=${DISPLAY:-:0}
export NX300M_MOD_ROOT="$ROOT" NX300M_MOD_SOURCE_ROOT="$ROOT" NX300M_MOD_SOURCE_ID="$source_id"
export NX_STATE_DIR="$runtime"
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
# The engine admits/loads the adapter on Apply. Merely opening the list must
# not pause the camera or synchronize preview before the first UI frame.
if [ "$force_dry" -eq 0 ]; then set -- --live; else set -- --dry-run; fi
[ "$(stat -c '%d:%i' "$ROOT")" = "$source_id" ] || fail 'LUT source changed during panel preparation'
printf 'panel_runtime=%s\nlut_data_root=%s\n' "$runtime" "$ROOT"
cd "$runtime"
set -- "$@" --lut --root "$ROOT" --recipes "$ROOT/looks.txt" \
    --engine "$runtime/lut_engine.arm" --font-file "$stock_font" --font-metadata "$runtime/mfw-font.charset"
[ -z "$lifetime" ] || set -- "$@" --lifetime-ms "$lifetime"
exec "$runtime/nxui.arm" "$@"
