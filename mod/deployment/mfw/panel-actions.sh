#!/bin/sh
# Fixed local actions only; source the existing exact process-identity checks.
set -eu
set -f
[ "$#" -eq 1 ] || exit 64
case $1 in
    status|network-info|disable-autostart|enable-autostart|preview-enable|preview-s2-enable|preview-probe-once|preview-identity|preview-monochrome|preview-monochrome-timing|preview-sync|preview-stop|preview-restore|preview-status|preview-diag-export) action=$1 ;;
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
# The probe UI must not hold a preview/media lease. Only its detached RAM job
# obtains them after READY/ACK; no wait for apply or export belongs to the UI.
continuous=0
case $action in
    preview-s2-enable|preview-enable|preview-monochrome|preview-monochrome-timing|preview-sync)
        # Capability belongs to the installed backend, not a visible test button.
        script=$ROOT/smartfilter-preview.sh
        if [ -f "$script" ] && [ ! -L "$script" ] &&
            [ "$(/bin/sh "$script" capabilities 2>/dev/null)" = continuous-s2 ]; then
            continuous=1
        fi ;;
esac
case $action in
    preview-enable|preview-monochrome|preview-monochrome-timing|preview-sync)
        if [ "$action" = preview-enable ] || [ "$continuous" != 1 ]; then
            printf '探针包拒绝 S1、持续和非零 LUT 入口；请使用独立连续 S2 候选。\n' >&2
            exit 1
        fi ;;
    preview-probe-once|preview-identity)
        script=$ROOT/smartfilter-preview.sh
        [ -r "$script" ] && [ ! -L "$script" ] || { printf 'RAM preview wrapper unavailable\n' >&2; exit 1; }
        exec /bin/sh "$script" probe-once ;;
esac
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
        script=$ROOT/smartfilter-preview.sh
        [ -r "$script" ] && [ ! -L "$script" ] || fail 'preview script unavailable'
        case $action in
            preview-s2-enable)
                operation=enable-s2-stock
                [ "$continuous" != 1 ] || operation=enable-s2 ;;
            preview-monochrome) operation=test-monochrome ;;
            preview-monochrome-timing) operation=test-monochrome-timing ;;
            preview-sync) operation=sync ;;
            preview-stop) operation=stop ;;
            preview-restore) operation=restore ;;
            preview-status) operation=status ;;
            preview-diag-export) operation=diag-export ;;
        esac
        if [ "$operation" = diag-export ]; then
            rc=0
            data=$(/bin/sh "$script" diag-export) || rc=$?
            if printf '%s\n' "$data" | awk '/"status"[ \t]*:[ \t]*"no_data"/ { found=1 } END { exit !found }'; then
                printf '没有驻留诊断数据；未创建 RAM 快照，也没有写卡。\n'
                exit 0
            fi
            if [ "$rc" -ne 0 ]; then
                [ -z "$data" ] || printf '%s\n' "$data" >&2
                printf '诊断导出失败；以回执中的快照、写入及错误字段为准，请保留现场。\n' >&2
                exit 1
            fi
            if printf '%s\n' "$data" | awk '/"persisted"[ \t]*:[ \t]*true/ { found=1 } END { exit !found }'; then
                printf 'S2 诊断已保存；窗口已冻结，可正常关机后读卡。\n'
            elif printf '%s\n' "$data" | awk '/"snapshot_created"[ \t]*:[ \t]*true/ { found=1 } END { exit !found }'; then
                printf '已创建 RAM 快照，尚未写卡；这不是持久存证成功。\n'
            else
                printf '未确认创建诊断快照，也未确认写卡；请保留现场。\n'
            fi
            exit 0
        fi
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
                    if (value["diag_window"]>0) {
                        print "诊断窗口：" value["diag_window"] " · 样本 " value["diag_frames"]
                        print "诊断：" (value["enabled"]!="true" ? "已停止" : value["diag_frozen"]==1 ? "已冻结" : "采集中／容量满即停") " · 丢弃 " value["diag_dropped"]
                    }
                    if (value["profile"]==1) {
                        print "S2：" (value["enabled"]=="true" ? "已武装" : "LUT 已软停") " · 路由驻留"
                        print "S2 接管帧：" value["s2_frames"] " · 安全直通：" value["s2_fallback"]
                        print "取景图：" (value["s2_path"]==1 ? "含 LUT 节点" : "原厂") " · 恢复状态 " value["refresh_state"]
                        if (value["refresh_pending"]==1) print "正在等待原厂取景恢复"
                    }
                }
            '
            exit 0
        fi
        /bin/sh "$script" "$operation"
        case $operation in
            enable-s2-stock) printf 'S2 原样已请求；保持原厂滤镜关闭。单位表处理仅允许独立单帧探针。\n' ;;
            restore) printf '预览已关闭。S2 为软停，完全退出需正常关机重启。\n' ;;
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
