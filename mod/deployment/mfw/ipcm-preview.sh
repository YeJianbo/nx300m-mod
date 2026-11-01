#!/bin/sh
# Explicit IPCM test entry. Selection is read once; callbacks never read SD.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
fail() { printf 'IPCM preview: %s\n' "$*" >&2; exit 1; }
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
ROOT=$(CDPATH= cd -P "${NX300M_MOD_SOURCE_ROOT:-$HERE}" && pwd -P)
case $ROOT in *[!A-Za-z0-9_./-]*) fail 'unsupported source path' ;; esac
operation=${1:-status}
calibration=0
case $operation in
    apply) [ "$#" -ge 2 ] && [ "$#" -le 3 ] || fail 'apply LOOK [0..100]'; look=$2; strength=${3:-100} ;;
    probe) [ "$#" -ge 2 ] && [ "$#" -le 3 ] || fail 'probe NAME [0..100]'; calibration=1; operation=apply; look=$2; strength=${3:-100} ;;
    sync|status|stock|restore) [ "$#" -le 1 ] || fail 'unexpected arguments' ;;
    *) fail 'use apply LOOK [0..100], probe NAME [0..100], sync, status, stock or restore' ;;
esac
if [ "$operation" = sync ]; then
    # No saved/boot selection is guessed here: use the current still selection.
    operation=stock
    if [ -e /var/run/nx300m-lut-selection ]; then
        read -r generation selected strength extra < /var/run/nx300m-lut-selection || fail 'invalid current selection'
        [ -z "${extra:-}" ] || fail 'unexpected selection fields'
        case $generation in ''|*[!0-9]*) fail 'invalid generation' ;; esac
        case $selected in "$ROOT"/looks/*/table.d4lut) ;; *) fail 'selection belongs to another data root' ;; esac
        look=${selected#"$ROOT"/looks/}; look=${look%/table.d4lut}
        strength=${strength:-100}
        operation=apply
    fi
fi
profile=
if [ "$operation" = apply ]; then
    case $look in ''|*[!a-z0-9_-]*) fail 'invalid look ID' ;; esac
    case $strength in ''|*[!0-9]*) fail 'strength must be 0..100' ;; esac
    [ "${#strength}" -le 3 ] && [ "$strength" -le 100 ] || fail 'strength must be 0..100'
    profile=$ROOT/looks/$look/preview-ipcm.bin
    if [ "$calibration" = 1 ]; then profile=$ROOT/calibration/pcse/$look.bin; fi
    if [ ! -f "$profile" ] || [ -L "$profile" ]; then
        # Remove a previously selected preview rather than leaving stale colour.
        printf 'IPCM preview: %s has no compiled profile; requesting stock preview\n' "$look" >&2
        operation=stock
        profile=
    else
        printf 'IPCM preview candidate: %s at %s%%; use ordinary 2D P/A/S/M, restore before changing mode.\n' "$look" "$strength"
    fi
fi
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
exec 9>/tmp/nx300m-ipcm-preview.lock
flock -n 9 || fail 'another preview operation is running'
runtime=$(mktemp -d /tmp/nx300m-ipcm-preview.XXXXXX) || fail 'cannot allocate runtime'
cleanup() {
    rm -f "$runtime/loader.arm" "$runtime/blob.bin" "$runtime/profile.bin"
    rmdir "$runtime" 2>/dev/null || :
}
trap cleanup EXIT
case $(stat -f -c '%T' "$runtime") in tmpfs|ramfs) ;; *) fail 'temporary directory must be RAM backed' ;; esac
for pair in bin/ipcm_preview_loader.arm:loader.arm lib/ipcm_preview_blob.bin:blob.bin; do
    source=$HERE/${pair%:*}; destination=$runtime/${pair#*:}
    [ -f "$source" ] && [ ! -L "$source" ] || fail "missing resource: $source"
    cp "$source" "$destination" && cmp -s "$source" "$destination" || fail 'RAM copy differs'
done
chmod 500 "$runtime/loader.arm"
set -- "$operation" --pid "$camera_pid" --blob "$runtime/blob.bin"
if [ -n "$profile" ]; then
    cp "$profile" "$runtime/profile.bin" && cmp -s "$profile" "$runtime/profile.bin" || fail 'profile copy differs'
    set -- "$@" --profile "$runtime/profile.bin" --strength "$strength"
fi
result=0
"$runtime/loader.arm" "$@" || result=$?
if [ "$result" = 4 ]; then
    printf 'IPCM preview: restore is pending. Return to normal live view for a native update, then retry restore; do not delete loader state.\n' >&2
fi
exit "$result"
