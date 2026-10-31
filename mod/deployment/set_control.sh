#!/bin/sh
set -eu
[ "$#" -eq 2 ] || exit 64
exec /bin/sh "$(dirname "$0")/camera_control.sh" "$1" "$2"
