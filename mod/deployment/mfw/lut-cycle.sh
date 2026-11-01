#!/bin/sh
# Zero-UI film cycling: stage the next look after the current selection.
# Triggered by a short press of Custom; the table reaches the ISP on the next
# stock half-press reload, exactly like a panel selection. "stock" participates
# in the cycle so the camera can always be returned to original rendering.
set -eu
umask 077
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
note() {
    [ -n "${NX_FN_KEY_LOG:-}" ] || return 0
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$NX_FN_KEY_LOG" 2>/dev/null || :
}
ROOT=${NX300M_MOD_SOURCE_ROOT:-/mnt/mmc/nx300m-mod}
[ -r "$ROOT/looks.txt" ] && [ -d "$ROOT/looks" ] || { note 'lut cycle: look catalogue unavailable'; exit 1; }
# The daemon's inherited source identity was captured at an earlier mount; on
# FAT the same card gets new identities after every reader round-trip, which
# used to fail every tap with "data source identity changed". Recompute it.
NX300M_MOD_SOURCE_ROOT=$ROOT
NX300M_MOD_SOURCE_ID=$(stat -c '%d:%i' "$ROOT")
export NX300M_MOD_SOURCE_ROOT NX300M_MOD_SOURCE_ID
feedback() {
    text=$1
    note "lut cycle feedback: $text"
    # Feedback is read-only: do not reload the hook or overwrite panel backup.
    if ! NXUI_LOCK_TAG=-flash NXUI_LUT_NOTICE="$text" NX300M_MOD_SOURCE_ROOT="$ROOT" \
        /bin/sh "$ROOT/lut-panel.sh" --dry-run --lifetime-ms 1500; then
        note 'lut cycle: feedback panel unavailable'
        printf 'NX300M LUT: %s (feedback panel unavailable)\n' "$text" >&2
    fi
}
engine=$ROOT/bin/lut_engine.arm
[ -x "$engine" ] || { note 'lut cycle: engine binary missing'; feedback '切换失败：缺少 LUT 后端'; exit 1; }
if ! NX300M_MOD_ROOT="$ROOT" "$engine" restore-selection; then
    note 'lut cycle: saved selection unavailable; continuing with current selection'
fi
strength=100
current=stock
if [ -r /var/run/nx300m-lut-selection ]; then
    read -r gen path strength < /var/run/nx300m-lut-selection 2>/dev/null || :
    case $path in */looks/*/table.d4lut) current=$(basename "$(dirname "$path")") ;; esac
    case $strength in ''|*[!0-9]*) strength=100 ;; esac
    [ "$strength" -ge 0 ] && [ "$strength" -le 100 ] || strength=100
fi
keys=$(mktemp /tmp/nx300m-lut-cycle.XXXXXX)
{
    printf 'stock\n'
    while IFS= read -r line; do
        key=${line%%|*}
        case $key in ''|*[!A-Za-z0-9_-]*) continue ;; esac
        [ -f "$ROOT/looks/$key/table.d4lut" ] && printf '%s\n' "$key"
    done < "$ROOT/looks.txt"
} > "$keys"
next=$(awk -v cur="$current" '
    { keys[NR] = $1 }
    END {
        if (NR == 0) exit 1
        for (i = 1; i <= NR; i++) if (keys[i] == cur) { print keys[(i % NR) + 1]; exit }
        print keys[1]
    }' "$keys" 2>/dev/null || :)
rm -f "$keys"
[ -n "$next" ] || { note 'lut cycle: catalogue is empty'; exit 1; }
# The adapter only stays resident from a previous panel open or apply. When a
# tap arrives first, load it here so cycling works without opening the panel.
if ! NX300M_MOD_ROOT="$ROOT" "$engine" ready >/dev/null 2>&1; then
    note 'lut cycle: adapter not resident, applying'
    status_file=$(mktemp /tmp/nx300m-lut-apply.XXXXXX)
    ( apply_code=0; /bin/sh "$ROOT/lut-load.sh" apply 2>&1 || apply_code=$?; printf '%s\n' "$apply_code" >"$status_file" ) |
        while IFS= read -r line; do note "lut-load: $line"; done
    apply_status=$(cat "$status_file" 2>/dev/null || echo 1)
    rm -f "$status_file"
    [ "$apply_status" = 0 ] || { note 'lut cycle: adapter apply failed'; feedback '切换失败：LUT 模块未就绪'; exit 1; }
fi
note "lut cycle: $current -> $next strength=$strength"
result=0
if [ "$next" = stock ]; then
    output=$(NX300M_MOD_ROOT="$ROOT" "$engine" stock 2>&1) || result=$?
else
    output=$(NX300M_MOD_ROOT="$ROOT" "$engine" apply "$next" "$strength" 2>&1) || result=$?
fi
printf '%s\n' "$output"
note "lut cycle engine: $output"
note "lut cycle engine exit=$result next=$next"
[ "$result" -eq 0 ] || { feedback 'LUT 切换失败，请重试'; exit "$result"; }
# Show which look is active: flash the panel over the viewfinder briefly.
# It highlights the current selection and closes itself; best effort only.
label=$(awk -F'|' -v k="$next" '$1==k{print $2; exit}' "$ROOT/looks.txt" 2>/dev/null || :)
[ -n "$label" ] || label=$next
[ "$next" = stock ] && label='原样'
note "lut cycle flash: $label"
case $output in
    *PERSIST-WARNING*) feedback "$label · 未保存到卡，仅本次有效" ;;
    *) feedback "$label · 已保存，等待原厂 LUT 重载" ;;
esac
exit "$result"
