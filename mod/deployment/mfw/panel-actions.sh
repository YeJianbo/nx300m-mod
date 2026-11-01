#!/bin/sh
# Fixed local actions only; source the existing exact process-identity checks.
set -eu
set -f
[ "$#" -eq 1 ] || exit 64
case $1 in
    status|network-info|disable-autostart|enable-autostart|preview-enable|preview-identity|preview-monochrome|preview-sync|preview-restore|preview-status) action=$1 ;;
    *) exit 64 ;;
esac
. "${NX300M_MOD_ROOT:-/mnt/mmc/nx300m-mod}/common.sh"
if [ "$action" = network-info ]; then
    # Network inspection touches neither the source card nor media. Keep it
    # available while USB/media access is paused, just like the HTTP status.
    printf '手机连接：先在相机开启无线，再允许手机加入相机 WLAN。\n'
    listen=${NX_REMOTE_LISTEN:-0.0.0.0:8781}
    printf '服务配置：%s\n' "$listen"
    addresses=
    if command -v ip >/dev/null 2>&1; then
        addresses=$(ip -4 addr show up 2>/dev/null | awk '
            /^[0-9]+:/ { iface=$2; sub(/:$/, "", iface) }
            $1=="inet" && $2 !~ /^127\./ { split($2, a, "/"); print iface " " a[1] }
        ')
    fi
    if [ -z "$addresses" ] && command -v ifconfig >/dev/null 2>&1; then
        addresses=$(ifconfig 2>/dev/null | awk '
            /^[^ \t]/ { iface=$1; sub(/:$/, "", iface) }
            /inet addr:/ { value=$2; sub(/^addr:/, "", value); if (value !~ /^127\./) print iface " " value; next }
            $1=="inet" && $2 !~ /^127\./ { print iface " " $2 }
        ')
    fi
    if [ -z "$addresses" ]; then
        printf '当前没有非回环 IPv4 地址，请先开启原厂无线连接。\n'
    else
        printf '%s\n' "$addresses" | awk -v listen="$listen" '
            BEGIN { split(listen, endpoint, ":"); port=endpoint[2] }
            NR<=2 { print $1 ": " $2; if (endpoint[1]=="0.0.0.0" || endpoint[1]==$2) print "http://" $2 ":" port "/" }
            END { if (NR>2) print "更多接口请查看 App 连接诊断。" }
        '
    fi
    printf 'NXConnect 点“查找相机”；NFC 需手机读取到兼容标签。\n'
    exit 0
fi
# Coordinate even read-only source status with the remote's media pause. The
# lock is in RAM for the staged runtime, never on the card being handed off.
exec 6>"$STATE/media-io.lock" || fail 'cannot open media access lock'
flock -s -n 6 || fail 'media access is paused or draining'
[ ! -e "$STATE/media-paused" ] || fail 'media access is paused'
SOURCE_ROOT=${NX300M_MOD_SOURCE_ROOT:-$ROOT}
case $SOURCE_ROOT in /*) ;; *) fail 'source root must be absolute' ;; esac
source_present() {
    [ -d "$SOURCE_ROOT" ] || return 1
    [ -z "${NX300M_MOD_SOURCE_ID:-}" ] ||
        [ "$(stat -c '%d:%i' "$SOURCE_ROOT" 2>/dev/null)" = "$NX300M_MOD_SOURCE_ID" ]
}
if [ "$action" != status ]; then
    source_present || fail 'source SD directory is unavailable or has changed; preference was not saved'
    [ ! -L "$SOURCE_ROOT/DISABLED" ] || fail 'source DISABLED is a symbolic link'
    [ ! -L "$ROOT/DISABLED" ] || fail 'runtime DISABLED is a symbolic link'
fi
case $action in
    preview-*)
        script=$SOURCE_ROOT/smartfilter-preview.sh
        [ -r "$script" ] && [ ! -L "$script" ] || fail 'preview script unavailable'
        case $action in
            preview-enable) operation=enable-stock ;;
            preview-identity) operation=test-identity ;;
            preview-monochrome) operation=test-monochrome ;;
            preview-sync) operation=sync ;;
            preview-restore) operation=restore ;;
            preview-status) operation=status ;;
        esac
        case $operation in
            test-*|sync)
                selected_backend=
                [ ! -r /var/run/nx300m-preview-backend ] || read -r selected_backend < /var/run/nx300m-preview-backend || :
                [ "$selected_backend" = smartfilter ] || fail '请先选择“启用预览 · 原样”'
                ;;
        esac
        if [ "$operation" = status ]; then
            data=$(/bin/sh "$script" status)
            if [ -n "${NX_FN_KEY_LOG:-}" ]; then printf 'SmartFilter status=%s\n' "$data" >> "$NX_FN_KEY_LOG" 2>/dev/null || :; fi
            printf '%s\n' "$data" | awk '
                /"installed"/ {
                    gsub(/[{}"]/, ""); n=split($0, fields, ",")
                    for (i=1;i<=n;i++) { split(fields[i], pair, ":"); value[pair[1]]=pair[2] }
                    print "预览模块：" (value["installed"]=="true" ? "已启用" : "未启用")
                    print "风格：" value["look_id"] " · 强度 " value["strength"] "%"
                    print "已请求：" value["requested_generation"] " · 已处理：" value["applied_generation"]
                    print "处理帧：" value["rendered"] " · 原样帧：" value["passthrough"]
                    print "最近错误：" value["last_error"]
                }
            '
            exit 0
        fi
        /bin/sh "$script" "$operation"
        case $operation in
            enable-stock) printf '预览已启用。请在原厂智能滤镜中选择 Miniature。\n' ;;
            test-identity) printf '单位表已提交。请观察取景是否保持原样。\n' ;;
            test-monochrome) printf '黑白表已提交。请观察取景是否变为黑白。\n' ;;
            sync) printf '已提交当前 LUT，请观察取景。\n' ;;
            restore) printf '已恢复原厂调用。请关闭原厂智能滤镜。\n' ;;
        esac
        exit 0
        ;;
esac
case $action in
    disable-autostart)
        if [ ! -e "$SOURCE_ROOT/DISABLED" ]; then : > "$SOURCE_ROOT/DISABLED"; fi
        [ -f "$SOURCE_ROOT/DISABLED" ] || fail 'source DISABLED is not a regular file'
        if [ ! -e "$ROOT/DISABLED" ]; then : > "$ROOT/DISABLED"; fi
        [ -f "$ROOT/DISABLED" ] || fail 'DISABLED is not a regular file'
        ;;
    enable-autostart)
        [ ! -e "$SOURCE_ROOT/DISABLED" ] || { [ -f "$SOURCE_ROOT/DISABLED" ] && rm -f "$SOURCE_ROOT/DISABLED"; }
        [ ! -e "$SOURCE_ROOT/DISABLED" ] || fail 'cannot clear source DISABLED'
        [ ! -e "$ROOT/DISABLED" ] || { [ -f "$ROOT/DISABLED" ] && rm -f "$ROOT/DISABLED"; }
        [ ! -e "$ROOT/DISABLED" ] || fail 'cannot clear DISABLED'
        ;;
esac
if record_alive; then
    phase=
    if [ -r "$PHASE" ]; then IFS= read -r phase < "$PHASE" || :; fi
    case $phase in
        ready) printf '遥控服务：运行中 (PID %s)\n' "$REC_PID" ;;
        *) printf '遥控服务：进程存活，HTTP就绪待确认\n' ;;
    esac
else
    printf '遥控服务：未运行\n'
fi
# Camera mappings are evidence of loading; readiness files alone are not.
camera_count=0
camera_pid=
set +f
for process in /proc/[0-9]*; do
    executable=$(readlink "$process/exe" 2>/dev/null) || continue
    case $executable in
        /usr/bin/di-camera-app|/usr/bin/di-camera-app-nx300)
            camera_count=$((camera_count+1)); camera_pid=${process##*/} ;;
    esac
done
set -f
if [ "$camera_count" -eq 1 ] && [ -r "/proc/$camera_pid/maps" ]; then
    modules=$(awk '
        BEGIN { n=split("lut_port.so ui_mod.so exposure_controls.so shutter_limit.so color-style.so lcd-capture.so service-loader.so gallery-share.so camera_firmware_mod.so", names, " ") }
        { path=$6; for (i=1;i<=n;i++) if (path == names[i] || (length(path)>length(names[i]) && substr(path,length(path)-length(names[i])) == "/" names[i])) found[i]=1 }
        END { count=0; for (i=1;i<=n;i++) count+=found[i]; print count }
    ' "/proc/$camera_pid/maps") || exit 1
    printf '相机扩展模块：%s/9 已加载\n' "$modules"
else
    printf '相机扩展模块：当前进程无法确认\n'
fi
if ! source_present; then
    printf '下次MFW自动启动：来源卡不可用，无法确认\n'
elif [ -e "$SOURCE_ROOT/DISABLED" ]; then
    printf '下次MFW自动启动：已停用（本次服务继续运行）\n'
else
    printf '下次MFW自动启动：已启用\n'
fi
