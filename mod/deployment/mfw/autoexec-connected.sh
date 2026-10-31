#!/bin/sh
# Keep lens admission and NXConnect services in the same MFW boot entry.
PATH=/bin:/usr/bin:/sbin:/usr/sbin
export PATH
[ "$#" -eq 0 ] || exit 1
service=/mnt/mmc/nx300m-mod
lens=/mnt/mmc/nx300m-lens-debug
[ ! -e "$service/DISABLED" ] || exit 0
unset LD_PRELOAD LD_AUDIT LD_LIBRARY_PATH
# Wi-Fi may not have an address at boot. The daemon listens on all interfaces
# and refreshes discovery as the stock Remote Viewfinder brings Wi-Fi up.
export NX_REMOTE_LISTEN=0.0.0.0:8781
export NX_REMOTE_CUSTOM_KEY=0 NX_REMOTE_PANEL_SHORTCUT=0
# Physical entry for the panel and zero-UI look cycling. The Custom button
# carries the mod gestures (tap = next look, hold = panel); Fn stays stock.
export NX_REMOTE_PANEL_KEY=custom
# Fn-entry evidence must survive power-off: the daemon's stderr lands in the
# RAM state directory, so the trigger chain logs to this card file instead.
mkdir -p "$service/logs" 2>/dev/null || :
export NX_FN_KEY_LOG="$service/logs/fn-key.log"
printf '%s autoexec start panel=%s menu=%s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${NX_REMOTE_PANEL_SHORTCUT:-0}" "${NX_REMOTE_MENU_KEY:-0}" \
    >>"$NX_FN_KEY_LOG" 2>/dev/null || :
wifi_exit=0
if [ -r "$lens/autoexec-wifi.sh" ]; then
    /bin/sh "$lens/autoexec-wifi.sh" || wifi_exit=$?
else
    wifi_exit=1
fi
# Both original bootstraps return after launching a low-priority worker. A
# missing camera process in the Wi-Fi tool must not prevent HTTP startup.
service_exit=1
if [ -r "$service/mfw-service-entry.sh" ]; then
    NX300M_MOD_ROOT="$service" NX_MOD_ROOT="$service" \
        /bin/sh "$service/mfw-service-entry.sh"
    service_exit=$?
fi
[ "$wifi_exit" -eq 0 ] && [ "$service_exit" -eq 0 ]
printf '%s autoexec exit wifi=%s service=%s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$wifi_exit" "$service_exit" \
    >>"$NX_FN_KEY_LOG" 2>/dev/null || :
