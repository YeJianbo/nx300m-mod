#!/bin/sh
# Keep the original restore supervisor alive independently of the menu window.
set -eu
set -f
umask 077
case ${1:-request}:$# in request:0|request:1|--auto:1) ;; *) exit 64 ;; esac
ROOT=${NX300M_MOD_ROOT:?missing service root}
STATE=${NX_STATE_DIR:?missing RAM state}
case $ROOT:$STATE in /*:/*) ;; *) exit 64 ;; esac
[ -d "$STATE" ] && [ -r "$ROOT/smartfilter-preview.sh" ] || exit 1
lock=$STATE/preview-softstop.lock
request=$STATE/preview-softstop.request
[ ! -L "$lock" ] && [ ! -L "$request" ] || exit 1
exec 8>"$lock"
if ! flock -n 8; then
    printf '关闭预览正在后台进行；菜单可以继续使用。\n'
    exit 0
fi
key=none
if [ -f /var/run/nx300m-preview-backend ]; then
    key=$(stat -c '%d:%i:%Y' /var/run/nx300m-preview-backend)
fi
previous=
if [ -r "$request" ]; then IFS= read -r previous < "$request" || :; fi
if [ "${1:-request}" = --auto ] && [ "$previous" = "$key" ]; then
    printf '本次预览已提交过软停；请查看结果或在菜单中重试。\n'
    exit 0
fi
printf '%s\n' "$key" > "$request"
log=$STATE/preview-softstop.log
(
    trap '' HUP
    printf 'NX_PREVIEW_STOP begin key=%s\n' "$key"
    rc=0
    /bin/sh "$ROOT/smartfilter-preview.sh" restore || rc=$?
    printf '%s %s\n' "$key" "$rc" > "$STATE/preview-softstop.result"
    printf 'NX_PREVIEW_STOP end rc=%s key=%s\n' "$rc" "$key"
    # Losing SD logging must never prevent the restore itself from running.
    # The wrapper persists its timing dump separately; copy control messages
    # only after it has returned, retaining the RAM log if this append fails.
    if [ -n "${NX_FN_KEY_LOG:-}" ]; then
        cat "$log" >>"$NX_FN_KEY_LOG" 2>/dev/null || :
    fi
    exit "$rc"
) </dev/null >"$log" 2>&1 &
printf '关闭预览请求已提交；完成结果写入日志，菜单无需等待。\n'
