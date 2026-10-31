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
exec 8>"$ram_tmp/nx300m-panel-launch.lock"
flock -n 8 || fail 'a native panel is already open'
runtime=$(mktemp -d "$ram_tmp/nx300m-lut-panel.XXXXXX") || fail 'cannot allocate RAM panel directory'
for asset in nxui.arm lut_engine.arm; do
    cp "$CODE/bin/$asset" "$runtime/$asset" && cmp -s "$CODE/bin/$asset" "$runtime/$asset" || fail "RAM copy differs: $asset"
    chmod 500 "$runtime/$asset"
done
cp "$CODE/fonts/mfw-font.charset" "$runtime/mfw-font.charset" && \
    cmp -s "$CODE/fonts/mfw-font.charset" "$runtime/mfw-font.charset" || fail 'RAM font coverage copy differs'
export DISPLAY=${DISPLAY:-:0}
export NX300M_MOD_ROOT="$ROOT" NX300M_MOD_SOURCE_ROOT="$ROOT" NX300M_MOD_SOURCE_ID="$source_id"
export NX_STATE_DIR="$runtime"
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
live=0
if [ "$force_dry" -eq 0 ] && "$runtime/lut_engine.arm" ready; then
    live=1
elif [ "$force_dry" -eq 0 ]; then
    # The adapter is not resident yet. It is a reversible RAM patch into the
    # running camera (restore undoes it; power loss clears it), and the panel
    # is useless in browse mode, so load it here instead of failing silently.
    printf '%s lut panel: adapter not resident, applying\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
        >>"${NX_FN_KEY_LOG:-/dev/null}" 2>/dev/null || :
    # lut-load.sh lives on the SD data root; it stages its own RAM copies.
    # The first attempt can fail while the camera still holds an orphaned
    # installation whose slots the stock firmware resets on its next reload,
    # so retry instead of dropping the user into browse mode.
    adapter_applied=0
    for attempt in 1 2 3; do
        if [ -r "$ROOT/lut-load.sh" ] && /bin/sh "$ROOT/lut-load.sh" apply; then
            adapter_applied=1
            break
        fi
        printf '%s lut panel: adapter apply attempt %d failed\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$attempt" \
            >>"${NX_FN_KEY_LOG:-/dev/null}" 2>/dev/null || :
        sleep 2
    done
    if [ "$adapter_applied" = 1 ]; then
        printf '%s lut panel: adapter applied\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
            >>"${NX_FN_KEY_LOG:-/dev/null}" 2>/dev/null || :
        if "$runtime/lut_engine.arm" ready; then live=1; fi
    else
        printf '%s lut panel: adapter apply failed\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
            >>"${NX_FN_KEY_LOG:-/dev/null}" 2>/dev/null || :
    fi
fi
if [ "$live" -eq 1 ]; then
    # A reboot wipes the RAM selection; bring back the persisted look before
    # the backup snapshot, so the panel reopens on the previous film.
    if [ ! -r /var/run/nx300m-lut-selection ] && [ -r "$ROOT/.state/lut-selection" ]; then
        cp "$ROOT/.state/lut-selection" /var/run/nx300m-lut-selection 2>/dev/null || :
    fi
    "$runtime/lut_engine.arm" begin || fail 'cannot back up the entry selection'
    set -- --live
else
    printf '当前面板为浏览模式；原厂相机尚未加载匹配的 LUT 适配器。\n' >&2
    set -- --dry-run
fi
[ "$(stat -c '%d:%i' "$ROOT")" = "$source_id" ] || fail 'LUT source changed during panel preparation'
printf 'panel_runtime=%s\nlut_data_root=%s\n' "$runtime" "$ROOT"
cd "$runtime"
set -- "$@" --lut --root "$ROOT" --recipes "$ROOT/looks.txt" \
    --engine "$runtime/lut_engine.arm" --font-file "$stock_font" --font-metadata "$runtime/mfw-font.charset"
[ -z "$lifetime" ] || set -- "$@" --lifetime-ms "$lifetime"
exec "$runtime/nxui.arm" "$@"
