#!/bin/sh
# Acknowledge changes through the running camera bridge, never a marker file.
set -eu
command=${1:-}
value=${2:-0}
operation=1
case "$command" in
  mode)
    bridge=firmware-mod
    case "$value" in
      SMART|SMARTAUTO|AUTO) value=1 ;; P) value=2 ;; A) value=3 ;;
      S) value=4 ;; M) value=5 ;; BULB) value=6 ;; IFN|I) value=7 ;;
      SMARTPRO) value=8 ;; WIFI) value=30 ;; *) exit 64 ;;
    esac
    expected=$value
    payload="1 $value" ;;
  metering)
    bridge=firmware-mod
    # Keep an explicit disable path for an older, already-running module.
    case "$value" in 0) ;; *) echo "Experimental metering was removed; stock metering is used" >&2; exit 64 ;; esac
    expected=$value
    payload="2 $value" ;;
  mode-lock|animation|custom|iso-lock)
    bridge=firmware-mod
    expected=$value
    case "$command:$value" in
      mode-lock:0|mode-lock:1) payload="4 $value" ;;
      animation:0|animation:1|animation:2) payload="5 $value" ;;
      custom:0|custom:1) payload="6 $value" ;;
      iso-lock:0|iso-lock:6400) payload="7 $value" ;;
      *) exit 64 ;;
    esac ;;
  auto-iso)
    bridge=exposure
    case "$value" in
      3200) expected=003b000e ;; 6400) expected=003b000f ;;
      12800) expected=003b0010 ;; 25600) expected=003b0011 ;; *) exit 64 ;;
    esac
    payload="1 0000003b $expected" ;;
  shutter|shutter-read)
    bridge=shutter-limit
    case "$value" in 0|1|2|3|4|5|6|7|8|9) ;; *) exit 64 ;; esac
    expected=$value
    if [ "$command" = shutter-read ]; then operation=0; fi
    payload="$operation $value" ;;
  *) exit 64 ;;
esac
cd /var/run
prefix=nx300m-$bridge
test -f "$prefix-ready" || { echo "$bridge bridge is not ready" >&2; exit 69; }
read -r owner < "$prefix-ready"
case "$owner" in ''|*[!0-9]*) exit 70 ;; esac
test "$owner" -gt 1
kill -0 "$owner"
case "$bridge" in exposure) lock=nx300m-exposure-lock ;; *) lock=$prefix-lock ;; esac
exec 9> "$lock"
lock_attempt=0
while ! flock -n 9; do
  lock_attempt=$((lock_attempt + 1))
  [ "$lock_attempt" -lt 40 ] || { echo "$bridge control is busy" >&2; exit 75; }
  sleep 0.05
done
now=$(date +%s)
id=$(( (now + $$) % 2147483646 + 1 ))
expires=$((now + 6))
printf '%s %s %s\n' "$id" "$expires" "$payload" > "$prefix-request.tmp"
mv "$prefix-request.tmp" "$prefix-request"
attempt=0
while [ "$attempt" -lt 180 ]; do
  if [ -f "$prefix-response" ]; then
    line=$(cat "$prefix-response")
    set -- $line
    if [ "${1:-}" = "$id" ]; then
      [ "${2:-}" = 0 ] || { echo "$bridge returned ${2:-invalid}" >&2; exit 1; }
      case "$command" in
        mode) [ "$#" -eq 26 ] && [ "$3" = "$owner" ] && [ "$4" = "$expected" ] ;;
        metering) [ "$#" -eq 26 ] && [ "$3" = "$owner" ] && [ "$5" = "$expected" ] ;;
        mode-lock|animation|custom|iso-lock)
          [ "$#" -eq 26 ] && [ "$3" = "$owner" ] || exit 70
          shift 13
          case "$command" in
            mode-lock) [ "$1" = "$expected" ] ;;
            animation) [ "$2" = "$expected" ] ;;
            custom) [ "$3" = "$expected" ] ;;
            iso-lock) [ "$4" = "$expected" ] ;;
          esac ;;
        auto-iso) { [ "$#" -eq 6 ] || [ "$#" -eq 12 ] || [ "$#" -eq 14 ] || [ "$#" -eq 15 ]; } && [ "$4" = "$expected" ] ;;
        shutter) [ "$#" -eq 10 ] && [ "$3" = "$expected" ] ;;
        shutter-read) [ "$#" -eq 10 ] ;;
      esac || { echo "$bridge readback mismatch" >&2; exit 70; }
      printf '%s\n' "$line"
      exit 0
    fi
  fi
  kill -0 "$owner"
  sleep 0.05
  attempt=$((attempt + 1))
done
echo "$bridge response timed out" >&2
exit 124
