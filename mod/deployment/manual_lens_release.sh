#!/bin/sh
# NX300M 1.15 APPPREF_B_ENABLE_NO_LENS_RELEASE. This changes the stock shared
# preference, not a lens model, calibration file, or the shutter/AF algorithms.
set -eu
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
action=${1:-status}
[ "$#" -le 1 ] || exit 64
case $action in status|enable|disable) ;; *) echo 'usage: manual_lens_release.sh [status|enable|disable]' >&2; exit 64 ;; esac
[ -x /usr/bin/prefman ] || { echo 'stock prefman is unavailable' >&2; exit 69; }
command -v timeout >/dev/null 2>&1 || { echo 'stock timeout is unavailable' >&2; exit 69; }

run_prefman() {
    # This stock CLI can exit zero after a shared-memory/semaphore error.
    # Preserve its output and do not interpret an exit code alone as success.
    if output=$(timeout -t 3 /usr/bin/prefman "$@" 2>&1); then
        case $output in *[Ff][Aa][Ii][Ll]*|*[Ee][Rr][Rr][Oo][Rr]*)
            printf '%s\n' "$output" >&2; return 1 ;;
        esac
    else
        printf '%s\n' "$output" >&2
        return 1
    fi
}
read_value() {
    run_prefman get 1 0x30c l || return 1
    # Accept one complete, mutually consistent decimal/hex boolean value.
    value=$(printf '%s\n' "$output" | sed -n \
        -e 's/^[[:space:]]*value[[:space:]]*=[[:space:]]*0[[:space:]]\{1,\}(0x0\{1,\})[[:space:]]*$/0/p' \
        -e 's/^[[:space:]]*value[[:space:]]*=[[:space:]]*1[[:space:]]\{1,\}(0x0*1)[[:space:]]*$/1/p')
    case $value in 0|1) ;; *) printf 'invalid no-lens preference response: %s\n' "$output" >&2; return 1 ;; esac
}
read_value || exit 1
before=$value
case $action in
    status) printf 'no_lens_release=%s\n' "$before"; exit 0 ;;
    enable) requested=1 ;;
    disable) requested=0 ;;
esac
if [ "$before" != "$requested" ]; then
    run_prefman set 1 0x30c l "$requested" || exit 1
fi
read_value || exit 1
[ "$value" = "$requested" ] || { printf 'no-lens preference readback differs: expected=%s actual=%s\n' "$requested" "$value" >&2; exit 1; }
printf 'no_lens_release=%s previous=%s readback=verified\n' "$value" "$before"
