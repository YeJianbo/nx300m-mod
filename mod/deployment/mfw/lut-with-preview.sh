#!/bin/sh
# Optional paired selection for the explicit preview candidate, not a key hook.
set -eu
HERE=$(CDPATH= cd -P "$(dirname "$0")" && pwd -P)
ROOT=$(CDPATH= cd -P "${NX300M_MOD_SOURCE_ROOT:-$HERE}" && pwd -P)
export NX300M_MOD_ROOT="$ROOT"
unset NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID
engine=$ROOT/bin/lut_engine.arm
[ -x "$engine" ] || { printf 'Existing LUT engine is required.\n' >&2; exit 1; }
case ${1:-} in
    stock) [ "$#" -eq 1 ] || exit 2; NX300M_LUT_SKIP_PREVIEW_SYNC=1 "$engine" stock ;;
    apply) [ "$#" -ge 2 ] && [ "$#" -le 3 ] || exit 2; NX300M_LUT_SKIP_PREVIEW_SYNC=1 "$engine" apply "$2" "${3:-100}" ;;
    *) printf 'Use apply LOOK [0..100] or stock.\n' >&2; exit 2 ;;
esac
# The single-slot backend follows the same saved selection as the panel/Custom.
preview=ipcm-preview.sh
if [ -r /var/run/nx300m-preview-backend ]; then
    read -r backend < /var/run/nx300m-preview-backend || backend=
    [ "$backend" != smartfilter ] || preview=smartfilter-preview.sh
fi
# A preview failure must be visible while keeping the actual still selection.
/bin/sh "$HERE/$preview" sync || {
    result=$?
    printf 'Still LUT selection succeeded; preview sync failed (exit %s).\n' "$result" >&2
    exit "$result"
}
