#!/bin/sh
# Zero-UI film cycling: stage the next look after the current selection.
# Triggered by a short press of Custom; the table reaches the ISP on the next
# stock half-press reload, exactly like a panel selection. Closing LUT is a
# separate explicit panel action and does not participate in style cycling.
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
        /bin/sh "$ROOT/lut-panel.sh" --dry-run --lifetime-ms 650; then
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
    case $current in nx-tune-*)
        if [ -f "${path%/table.d4lut}/tune.txt" ]; then
            read -r tune_version base_key tune_values < "${path%/table.d4lut}/tune.txt" || :
            case $tune_version:$base_key in 1:*[!a-z0-9_-]*|1:) ;; 1:*) current=$base_key ;; esac
        fi ;;
    esac
    case $strength in ''|*[!0-9]*) strength=100 ;; esac
    [ "$strength" -ge 0 ] && [ "$strength" -le 100 ] || strength=100
fi
keys=$(mktemp /tmp/nx300m-lut-cycle.XXXXXX)
catalogue=$ROOT/looks.txt
[ ! -r "$ROOT/looks-cycle.txt" ] || catalogue=$ROOT/looks-cycle.txt
favorites=$ROOT/.state/lut-favorites
if [ -s "$favorites" ] && [ ! -L "$favorites" ]; then catalogue=$ROOT/looks.txt; fi
{
    while IFS= read -r line; do
        key=${line%%|*}
        case $key in ''|*[!A-Za-z0-9_-]*) continue ;; esac
        [ -f "$ROOT/looks/$key/table.d4lut" ] && printf '%s\n' "$key"
    done < "$catalogue"
} > "$keys"
if [ -s "$favorites" ] && [ ! -L "$favorites" ]; then
    # Only confirmed catalogue keys are eligible; the bookmark file cannot
    # introduce arbitrary filesystem paths or undeployed looks.
    awk 'NR==FNR { fav[$1]=1; next } fav[$1]' "$favorites" "$keys" > "$keys.favorites"
    if [ -s "$keys.favorites" ]; then mv "$keys.favorites" "$keys"; else rm -f "$keys.favorites"; fi
fi
next=$(awk -v cur="$current" '
    { keys[NR] = $1 }
    END {
        if (NR == 0) exit 1
        for (i = 1; i <= NR; i++) if (keys[i] == cur) { print keys[(i % NR) + 1]; exit }
        print keys[1]
    }' "$keys" 2>/dev/null || :)
rm -f "$keys"
[ -n "$next" ] || { note 'lut cycle: catalogue is empty'; exit 1; }
# The engine loads a real look on demand. Off must never preload the adapter.
note "lut cycle: $current -> $next strength=$strength"
result=0
output=$(NX300M_MOD_ROOT="$ROOT" "$engine" apply "$next" "$strength" 2>&1) || result=$?
printf '%s\n' "$output"
note "lut cycle engine: $output"
note "lut cycle engine exit=$result next=$next"
[ "$result" -eq 0 ] || { feedback 'LUT 切换失败，请重试'; exit "$result"; }
# Show which look is active: flash the panel over the viewfinder briefly.
# It highlights the current selection and closes itself; best effort only.
label=$(awk -F'|' -v k="$next" '$1==k{print $2; exit}' "$ROOT/looks.txt" 2>/dev/null || :)
[ -n "$label" ] || label=$next
note "lut cycle flash: $label"
case $output in
    *PERSIST-WARNING*) feedback "$label · 未保存到卡，仅本次有效" ;;
    *LUT_DISABLED*) feedback 'LUT 已关闭 · 原厂拍摄' ;;
    *) feedback "$label · 已保存，等待原厂 LUT 重载" ;;
esac
exit "$result"
