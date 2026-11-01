#!/bin/sh
# One original SmartFilter host; MOD selection is loaded only on control events.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'SmartFilter preview: %s\n' "$*" >&2; exit 1; }
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
ROOT=$(CDPATH= cd -P "${NX300M_MOD_SOURCE_ROOT:-$HERE}" && pwd -P)
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'unsupported source path' ;; esac
command=${1:-status}
[ "$#" -le 1 ] || fail 'unexpected arguments'
case $command in enable|enable-stock|sync|status|stock|restore|test-identity|test-monochrome) ;; *) fail 'use enable, enable-stock, sync, status, stock, restore, test-identity or test-monochrome' ;; esac
backend=/var/run/nx300m-preview-backend
if [ "$command" = sync ]; then
    [ -r "$backend" ] || exit 0
    read -r selected_backend < "$backend" || exit 0
    [ "$selected_backend" = smartfilter ] || exit 0
fi
operation=$command
[ "$command" != enable-stock ] || operation=stock
profile=
look=stock
strength=0
if [ "$command" = enable ] || [ "$command" = sync ]; then
    operation=stock
    if [ -e /var/run/nx300m-lut-selection ]; then
        read -r generation selected strength extra < /var/run/nx300m-lut-selection || fail 'invalid current selection'
        [ -z "${extra:-}" ] || fail 'unexpected selection fields'
        case $generation in ''|*[!0-9]*) fail 'invalid generation' ;; esac
        case $selected in "$ROOT"/looks/*/table.d4lut) ;; *) fail 'selection belongs to another source' ;; esac
        look=${selected#"$ROOT"/looks/}; look=${look%/table.d4lut}
        case $look in ''|*[!a-z0-9_-]*) fail 'invalid look ID' ;; esac
        case $strength in ''|*[!0-9]*) fail 'strength must be 0..100' ;; esac
        [ "${#strength}" -le 3 ] && [ "$strength" -le 100 ] || fail 'strength must be 0..100'
        # Already-rendered preview must use the direct table, not stock3d composition.
        profile=$ROOT/looks/$look/table.d4lut
        [ -f "$profile" ] && [ ! -L "$profile" ] || fail 'missing direct preview table'
        operation=apply
    fi
fi
case $command in
    test-identity|test-monochrome)
        look=${command#test-}
        profile=$HERE/preview-tests/$look.d4lut
        [ -f "$profile" ] && [ ! -L "$profile" ] || fail 'missing preview test table'
        operation=apply
        strength=100
        ;;
esac
[ "$(id -u)" = 0 ] || fail 'root required'
[ "$(uname -m)" = armv7l ] || fail 'native ARMv7 required'
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID
pids=
for entry in /proc/[0-9]*/exe; do
    target=$(readlink "$entry" 2>/dev/null) || continue
    case $target in /usr/bin/di-camera-app-nx300) value=${entry#/proc/}; pids="$pids ${value%/exe}" ;; esac
done
set -- $pids
[ "$#" -eq 1 ] || fail 'expected one original camera process'
camera_pid=$1
exec 9>/tmp/nx300m-smartfilter-preview.lock
flock -n 9 || fail 'another preview operation is running'
# IPCM and IPCS preview must not both transform the same viewfinder frame.
case $operation in
    apply|stock)
        [ ! -e "/var/run/nx300m-ipcm-preview-$camera_pid.state" ] || \
            fail 'restore the IPCM candidate and restart the camera process before changing backend'
        ;;
esac
runtime=$(mktemp -d /tmp/nx300m-smartfilter-preview.XXXXXX) || fail 'cannot allocate runtime'
cleanup() {
    rm -f "$runtime/loader.arm" "$runtime/blob.bin" "$runtime/table.d4lut"
    rmdir "$runtime" 2>/dev/null || :
}
trap cleanup EXIT
case $(stat -f -c '%T' "$runtime") in tmpfs|ramfs) ;; *) fail 'temporary directory must be RAM backed' ;; esac
for pair in bin/smartfilter_loader.arm:loader.arm lib/smartfilter_ram.bin:blob.bin; do
    source=$HERE/${pair%:*}; destination=$runtime/${pair#*:}
    [ -f "$source" ] && [ ! -L "$source" ] || fail "missing resource: $source"
    cp "$source" "$destination" && cmp -s "$source" "$destination" || fail 'RAM copy differs'
done
chmod 500 "$runtime/loader.arm"
set -- "$operation" --pid "$camera_pid" --blob "$runtime/blob.bin"
if [ -n "$profile" ]; then
    cp "$profile" "$runtime/table.d4lut" && cmp -s "$profile" "$runtime/table.d4lut" || fail 'table copy differs'
    set -- "$@" --table "$runtime/table.d4lut" --look "$look" --strength "$strength"
fi
result=0
"$runtime/loader.arm" "$@" || result=$?
[ "$result" -eq 0 ] || exit "$result"
if [ "$command" = enable ] || [ "$command" = enable-stock ]; then
    printf 'smartfilter\n' > "$backend.tmp"
    mv "$backend.tmp" "$backend"
    printf 'SmartFilter preview: select the original Miniature slot; change look and strength in MOD.\n'
elif [ "$command" = restore ] && [ -r "$backend" ]; then
    read -r selected_backend < "$backend" || selected_backend=
    [ "$selected_backend" != smartfilter ] || rm -f "$backend"
fi
