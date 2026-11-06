#!/bin/sh
# Fixed local actions only; source the existing exact process-identity checks.
set -eu
set -f
[ "$#" -eq 1 ] || exit 64
case $1 in
    status|network-info|disable-autostart|enable-autostart|preview-toggle|preview-enable|preview-s2-enable|preview-probe-once|preview-identity|preview-monochrome|preview-monochrome-timing|preview-sync|preview-stop|preview-restore|preview-status|preview-diag-export) action=$1 ;;
    fw-*) action=$1 ;;
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
# Firmware control requests reuse the verified camera_control.sh bridge client.
# They touch neither media nor preview leases; only the small preference record
# reaches the card, for firmware-mod-restore.sh to re-apply at boot.
case $action in
    fw-*)
        control=${NX300M_MOD_ROOT:-$(dirname "$0")}/camera_control.sh
        [ -f "$control" ] && [ ! -L "$control" ] || { printf 'camera_control.sh 不在安装目录\n' >&2; exit 69; }
        case $action in
            fw-auto-iso-max-12800|fw-auto-iso-max-25600|fw-auto-iso-max-3200)
                # Distinct bridge: the exposure module (not firmware-mod) owns
                # the Auto ISO ceiling. 0x003b000e/10/11 are verified stock
                # enums (auto-iso-policy-probe); 3200 restores the stock
                # ceiling (software "off" for the extension). The readback
                # must confirm the exact raw value.
                case $action in
                    fw-auto-iso-max-12800) ceiling_value=12800; ceiling_raw=003b0010; text='Auto ISO 上限已设为 12800' ;;
                    fw-auto-iso-max-25600) ceiling_value=25600; ceiling_raw=003b0011; text='Auto ISO 上限已设为 25600' ;;
                    fw-auto-iso-max-3200)  ceiling_value=3200;  ceiling_raw=003b000e; text='Auto ISO 上限已恢复原厂 3200' ;;
                esac
                line=$(/bin/sh "$control" auto-iso "$ceiling_value") || exit 1
                set -- $line
                case $# in 6|12|14|15) ;; *) printf '曝光桥响应格式异常\n' >&2; exit 70 ;; esac
                [ "$4" = "$ceiling_raw" ] || { printf 'Auto ISO 读回不一致：%s\n' "$4" >&2; exit 70; }
                printf 'FW_MOD %s · 原厂读回 %s\n' "$text" "$4"
                data=${NX300M_MOD_SOURCE_ROOT:-${NX300M_MOD_ROOT:-}}
                case $data in
                    /*) if mkdir -p "$data/.state" 2>/dev/null &&
                           [ -d "$data/.state" ] && [ ! -L "$data/.state" ]; then
                            target="$data/.state/fw-auto-iso-max"
                            if [ ! -L "$target" ] && { [ ! -e "$target" ] || [ -f "$target" ]; }; then
                                tmp="$data/.state/fw-auto-iso-max.tmp.$$"
                                if printf '%s\n' "$ceiling_value" > "$tmp" 2>/dev/null &&
                                   mv "$tmp" "$target" 2>/dev/null &&
                                   [ -f "$target" ] && [ ! -L "$target" ]; then
                                    exit 0
                                fi
                                rm -f "$tmp" 2>/dev/null || :
                            fi
                        fi ;;
                esac
                printf 'FW_MOD PERSIST-WARNING: %s · 原厂读回 %s（已应用、未保存）\n' "$text" "$4"
                exit 75 ;;
            fw-mode-lock-on)  op=mode-lock; value=1;    text='拨盘锁定已开启' ;;
            fw-mode-lock-off) op=mode-lock; value=0;    text='拨盘锁定已关闭' ;;
            fw-animation-0)   op=animation; value=0;    text='动画恢复原厂' ;;
            fw-animation-1)   op=animation; value=1;    text='动画已设为瞬切' ;;
            fw-animation-2)   op=animation; value=2;    text='动画已设为隐藏拨盘' ;;
            fw-auto-iso-0)    op=iso-lock;  value=0;    text='Auto ISO 上限锁已解除' ;;
            fw-auto-iso-6400) op=iso-lock;  value=6400; text='Auto ISO 上限已锁定 6400' ;;
            fw-mode-P) op=mode; value=P; text='已切到 P 挡' ;;
            fw-mode-A) op=mode; value=A; text='已切到 A 挡' ;;
            fw-mode-S) op=mode; value=S; text='已切到 S 挡' ;;
            fw-mode-M) op=mode; value=M; text='已切到 M 挡' ;;
            *) exit 64 ;;
        esac
        pref=
        case $op in mode-lock) pref=fw-mode-lock ;; animation) pref=fw-animation ;; iso-lock) pref=fw-auto-iso-lock ;; esac
        line=$(/bin/sh "$control" "$op" "$value") || exit 1
        set -- $line
        [ "$#" -eq 26 ] || { printf '固件桥响应格式异常\n' >&2; exit 70; }
        mode_name=
        case $4 in
            1) mode_name=SMART ;; 2) mode_name=P ;; 3) mode_name=A ;; 4) mode_name=S ;;
            5) mode_name=M ;; 6) mode_name=BULB ;; 7) mode_name=iFn ;; 8) mode_name=SMARTPRO ;;
            30) mode_name=Wi-Fi ;; *) mode_name="模式$4" ;;
        esac
        if [ -n "$pref" ]; then
            data=${NX300M_MOD_SOURCE_ROOT:-${NX300M_MOD_ROOT:-}}
            persist_ok=0
            case $data in
                /*)
                    if mkdir -p "$data/.state" 2>/dev/null &&
                       [ -d "$data/.state" ] && [ ! -L "$data/.state" ]; then
                        target="$data/.state/$pref"
                        # H4+dangling: refuse ANY symlink first — `-e` is false
                        # for a dangling link, so the link test must come
                        # before the existence test. Then allow only an
                        # absent or plain regular-file target. Checked rename
                        # only: a concurrent swap between check and mv is
                        # outside this boundary.
                        if [ ! -L "$target" ] && { [ ! -e "$target" ] || [ -f "$target" ]; }; then
                            tmp="$data/.state/$pref.tmp.$$"
                            if printf '%s\n' "$value" > "$tmp" 2>/dev/null &&
                               mv "$tmp" "$target" 2>/dev/null &&
                               [ -f "$target" ] && [ ! -L "$target" ]; then
                                persist_ok=1
                            else
                                rm -f "$tmp" 2>/dev/null || :
                            fi
                        fi
                    fi
                    ;;
            esac
            if [ "$persist_ok" -ne 1 ]; then
                printf 'FW_MOD PERSIST-WARNING: %s · 原厂读回 %s（已应用、未保存）\n' "$text" "$mode_name"
                exit 75
            fi
        fi
        printf 'FW_MOD %s · 原厂读回 %s\n' "$text" "$mode_name"
        exit 0 ;;
esac
# The probe UI must not hold a preview/media lease. Only its detached RAM job
# obtains them after READY/ACK; no wait for apply or export belongs to the UI.
continuous=0
case $action in
    preview-toggle|preview-s2-enable|preview-enable|preview-monochrome|preview-monochrome-timing|preview-sync)
        # Capability belongs to the installed backend, not a visible test button.
        script=$ROOT/smartfilter-preview.sh
        if [ -f "$script" ] && [ ! -L "$script" ] &&
            [ "$(/bin/sh "$script" capabilities 2>/dev/null)" = continuous-s2 ]; then
            continuous=1
        fi ;;
esac
case $action in
    preview-toggle|preview-enable|preview-monochrome|preview-monochrome-timing|preview-sync)
        if [ "$action" = preview-enable ] || [ "$continuous" != 1 ]; then
            printf '探针包拒绝 S1、持续和非零 LUT 入口；请使用独立连续 S2 候选。\n' >&2
            exit 1
        fi ;;
    preview-probe-once|preview-identity)
        script=$ROOT/smartfilter-preview.sh
        [ -r "$script" ] && [ ! -L "$script" ] || { printf 'RAM preview wrapper unavailable\n' >&2; exit 1; }
        exec /bin/sh "$script" probe-once ;;
esac
# Serialize explicit preview changes with the finite boot restore. Keep the
# descriptor in this parent only: loader/engine descendants must not inherit it.
case $action in
    preview-toggle|preview-s2-enable|preview-stop|preview-restore|preview-sync)
        exec 5>"$STATE/lut-boot.lock" || fail 'cannot open boot restore lock'
        flock -n 5 || fail '预览正在开机恢复，请稍候再操作'
        ;;
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
            preview-toggle)
                # Same acknowledged RAM marker used by LUT selection sync;
                # no ptrace/status query belongs to a menu toggle.
                backend=
                if [ -e /var/run/nx300m-preview-backend ]; then
                    [ -f /var/run/nx300m-preview-backend ] && [ ! -L /var/run/nx300m-preview-backend ] || fail 'invalid preview backend marker'
                    IFS= read -r backend < /var/run/nx300m-preview-backend || fail 'invalid preview backend marker'
                fi
                case $backend in
                    smartfilter) operation=stop ;;
                    '') operation=enable-s2 ;;
                    *) fail 'another preview backend is selected' ;;
                esac ;;
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
        /bin/sh "$script" "$operation" 5>&-
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
