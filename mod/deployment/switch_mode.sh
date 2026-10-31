#!/bin/sh
exec /bin/sh "$(dirname "$0")/camera_control.sh" mode "${1:-P}"
