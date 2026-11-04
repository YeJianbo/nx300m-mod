#!/bin/sh
# One finite startup task, using the same validated Still/preview controls.
set -eu
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
source_root=${NX300M_MOD_SOURCE_ROOT:-/mnt/mmc/nx300m-mod}
fixture=${NX_SF_PROBE_FIXTURE:-0}
case $fixture:$source_root in 0:/mnt/mmc/nx300m-mod|1:/perf) ;; *) exit 1 ;; esac
source_id=$(stat -c '%d:%i' "$source_root") || exit 1
export NX300M_MOD_SOURCE_ROOT="$source_root" NX300M_MOD_SOURCE_ID="$source_id"
export NX300M_MOD_ROOT="$HERE" NX_MOD_ROOT="$HERE"
saved=$source_root/.state/lut-choice
[ -e "$saved" ] || saved=$source_root/.state/lut-selection
[ -f "$saved" ] && [ ! -L "$saved" ] || exit 0
case $(stat -f -c '%T' "$HERE") in tmpfs|ramfs) ;; *) exit 1 ;; esac
exec 5>"${NX_STATE_DIR:-/tmp/nx300m-mfw/state}/lut-boot.lock"
flock -n 5 || exit 0
# Library presence is a readiness gate, not proof of image processing. Only
# one admitted installation is attempted; unresolved loader errors are kept.
attempt=0
while [ "$attempt" -lt 30 ]; do
    camera=
    count=0
    for process in /proc/[0-9]*; do
        executable=$(readlink "$process/exe" 2>/dev/null) || continue
        if [ "$fixture" = 1 ]; then
            [ "$executable" = /mnt/mmc/bin/smartfilter_fixture.arm ] || continue
        else
            [ "$executable" = /usr/bin/di-camera-app-nx300 ] || continue
        fi
        count=$((count+1)); camera=$process
    done
    if [ "$count" = 1 ] && grep -q '/libiq-fw-slpcam.so' "$camera/maps" &&
        grep -q '/libudd.so.3.1' "$camera/maps"; then break; fi
    attempt=$((attempt+1)); sleep 1
done
[ "$attempt" -lt 30 ] || { printf 'LUT_BOOT camera readiness timeout\n' >&2; exit 1; }
NX300M_LUT_SKIP_PREVIEW_SYNC=1 "$HERE/bin/lut_engine.arm" boot
preview=0
if [ -f "$source_root/.state/lut-preview" ] && [ ! -L "$source_root/.state/lut-preview" ]; then
    IFS= read -r preview < "$source_root/.state/lut-preview" || preview=0
fi
case $preview in
    1) /bin/sh "$HERE/smartfilter-preview.sh" enable-s2 ;;
    0) : ;;
    *) printf 'LUT_BOOT invalid preview preference; Still retained\n' >&2; exit 1 ;;
esac
printf 'LUT_BOOT restored preview=%s\n' "$preview"
