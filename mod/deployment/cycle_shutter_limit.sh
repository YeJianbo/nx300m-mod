#!/bin/sh
set -eu
helper="$(dirname "$0")/camera_control.sh"
line=$(/bin/sh "$helper" shutter-read)
set -- $line
case "$3" in
  0) next=3 ;; 3) next=4 ;; 4) next=5 ;; 5) next=6 ;;
  6) next=7 ;; 7) next=9 ;; *) next=0 ;;
esac
exec /bin/sh "$helper" shutter "$next"
