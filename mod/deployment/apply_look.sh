#!/bin/sh
set -eu
root=${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}
case $root in /*) ;; *) printf 'LUT root must be absolute\n' >&2; exit 2 ;; esac
engine=$root/bin/lut_engine.arm
[ -x "$engine" ] || { printf 'LUT engine unavailable\n' >&2; exit 3; }
export NX300M_MOD_ROOT="$root"
target=${1:-reset}
[ "$#" -le 1 ] || exit 2
# The native engine validates the catalogue and shares the panel/API lock and
# selection generation. Its success means staged, awaiting the stock callback.
if [ "$target" = reset ]; then
    exec "$engine" stock
fi
exec "$engine" apply "$target"
