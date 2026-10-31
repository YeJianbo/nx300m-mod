#!/bin/sh
# Bounded SD bootstrap; all resident code, state and output move to tmpfs.
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
mode=mfw
if [ "${1:-}" = --service ]; then mode=service; shift; fi
[ "$#" -eq 0 ] || exit 1
root=${NX300M_MOD_ROOT:-${NX_MOD_ROOT:-/mnt/mmc/nx300m-mod}}
[ -d "$root" ] || exit 0
[ -e "$root/DISABLED" ] && { [ -z "${NX_FN_KEY_LOG:-}" ] || printf '%s service entry exit: DISABLED marker\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >>"$NX_FN_KEY_LOG" 2>/dev/null; exit 0; }
[ -r "$root/mfw-ram-stage.sh" ] || exit 1
[ -n "${NX_FN_KEY_LOG:-}" ] && printf '%s service entry begin root=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$root" >>"$NX_FN_KEY_LOG" 2>/dev/null
umask 077
ram_tmp=$(CDPATH= cd -P /tmp && pwd -P) || exit 1
# Resolve /tmp first: a symlink to SD is not a RAM filesystem.
awk -v path="$ram_tmp" '
    $2=="/" || path==$2 || index(path,$2 "/")==1 {
        if (length($2)>best) {best=length($2); type=$3; options=$4}
    }
    END {exit !(type=="tmpfs" && options !~ /(^|,)noexec(,|$)/ && options ~ /(^|,)rw(,|$)/)}
' /proc/mounts || exit 1
ram=$ram_tmp/nx300m-mfw
[ ! -L "$ram" ] || exit 1
mkdir -p "$ram" || exit 1
chmod 700 "$ram" || exit 1
cd / || exit 1
unset LD_PRELOAD LD_AUDIT
export DISPLAY="${DISPLAY:-:0}"
# A fixed command string can close inherited camera descriptors before the
# SD helper is opened. Numeric-only descriptor names never contain shell text.
launch='
    # nice uses a relative adjustment; renice fixes the absolute value even
    # when the camera entry thread had inherited a negative nice value.
    renice 19 -p "$$" >/dev/null || exit 1
    for path in /proc/self/fd/*; do
        fd=${path##*/}
        case $fd in ""|*[!0-9]*|0|1|2) continue ;; esac
        eval "exec $fd>&-"
    done
    if [ "$3" = service ]; then
        exec /bin/sh "$1/mfw-ram-stage.sh" --service "$1" "$2"
    fi
    exec /bin/sh "$1/mfw-ram-stage.sh" "$1" "$2"
'
if [ "$mode" = service ]; then
    # The app loader observes the actual RAM staging/start result. MFW's
    # system(autoexec) caller retains its nonblocking entry below.
    exec nice -n 19 /bin/sh -c "$launch" sh "$root" "$ram" "$mode" </dev/null >>"$ram/bootstrap.log" 2>&1
fi
# Copying and comparing resources also competes with the camera; lowering only
# the eventual remote process leaves the entire SD bootstrap at app priority.
nice -n 19 /bin/sh -c "$launch" sh "$root" "$ram" "$mode" </dev/null >>"$ram/bootstrap.log" 2>&1 &
exit 0
