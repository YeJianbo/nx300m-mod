#!/bin/sh
# The historical UI has a separate menu root; actions use the current service.
set -eu
[ "$#" -eq 1 ] || exit 64
ROOT=${NX_FN_SERVICE_ROOT:?missing service root}
export NX300M_MOD_ROOT="$ROOT" NX_MOD_ROOT="$ROOT"
if [ "$1" = preview-restore ] && [ -f "$ROOT/PREVIEW_QUICK_PANEL" ]; then
    exec /bin/sh "$ROOT/preview-softstop.sh" request
fi
exec /bin/sh "$ROOT/mfw-panel-actions.sh" "$1"
