#!/bin/sh
# A short observation job, launched after the menu can return to liveview.
set -eu
umask 077
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH ENV BASH_ENV
[ "$#" -eq 6 ] || exit 64
nonce=$1; source_root=$2; source_id=$3; state=$4; camera_pid=$5; camera_start=$6
job=$(pwd -P)
case $job in /tmp/nx300m-sf-job.*) ;; *) exit 64 ;; esac
fixture=${NX_SF_PROBE_FIXTURE:-0}
case $fixture:$source_root in 0:/mnt/mmc/nx300m-mod|1:/perf) ;; *) exit 64 ;; esac
"$job/launcher" --ready "$job" "$nonce"
# The loader retains one authenticated target across first-frame waiting and
# observation. These descriptors stay unlocked until an actual frame exists.
rc=1
trap 'printf "KDD_OBSERVER_EXIT=%s\n" "$rc" > "$job/kdd.result"' EXIT
[ ! -L /tmp/nx300m-smartfilter-preview.lock ] || exit 1
exec 9>/tmp/nx300m-smartfilter-preview.lock
exec 6>"$state/media-io.lock"
[ ! -e "$state/media-paused" ] || exit 1
[ "$(stat -c '%d:%i' "$source_root")" = "$source_id" ] || exit 1
IFS= read -r fields < "/proc/$camera_pid/stat"
fields=${fields##*) }; set -- $fields
[ "$#" -ge 20 ] && [ "$1" != Z ] || exit 1
shift 19
[ "$1" = "$camera_start" ] || exit 1
log=$source_root/logs/fn-key.log
[ "$fixture" != 1 ] || log=/perf/fn-key.log
[ ! -L "$log" ] || exit 1
IFS= read -r token < "$job/kdd.expect"
[ -n "$token" ] || exit 1
set -- kdd-probe --pid "$camera_pid" --blob "$job/blob.bin" --diag-log "$log" --expect "$token"
[ "$fixture" != 1 ] || set -- "$@" --fixture
rc=0
"$job/launcher" --run 90 "$job/kdd.json" "$job/kdd.stderr" /tmp/nx300m-smartfilter-preview.uncertain "$job/loader.arm" "$@" || rc=$?
exit "$rc"
