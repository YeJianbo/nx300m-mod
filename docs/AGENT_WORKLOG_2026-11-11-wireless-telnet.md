# 工作日志：独立热点与 Telnet（2026-11-11 夜间 → 11-12 凌晨，全程离线）

执行 `docs/OVERNIGHT_2026-11-11-wireless-telnet.md` 与续作任务书 `docs/NIGHT_TASK_2026-11-11-2300-wireless-telnet.md`。因调度回执限制（定时任务会话不能再创建定时任务），经用户"继续"指示在本会话直接执行。全程离线：未连接实体相机/手机、未写卡、未安装、未刷机、未推送发布；网络服务全部运行在私有 namespace 或私有 ARM VM 内。

## 0. 关键结论（先读）

- 本轮开始时的实现**从未成功执行过**：`hotspot.sh:38` 在 `set -u` 下先引用后赋值 `$ROOT`，任何子命令 rc=2 退出（实测证据 `outputs/review/2026-11-11-wireless-telnet/shell-vm/` 任务书缺陷清单第 1 条）。静态核对共确认 14 项缺陷（任务书第 0 节），全部修复。
- **热点生命周期：shell 层隔离验收 133/133 通过**（真实生产脚本 + 记录型命令替身，`unshare --mount --pid --net --ipc`）。
- **Telnet 真实协议：私有 ARM VM 32/32 通过**（真 ARM 内核 + 原厂相机 busybox 1.20.2 ash 跑生产脚本 + busybox-static 1.37.0 telnetd + sha512-crypt 登录工具 + stock-busybox nc 客户端的真实 IAC 流量）。
- **性能四组对照**（同一 VM）：`boot_path_extra_workers=0`；热点替身空闲 RSS 2016 kB；telnetd 空闲 752 kB / 有连接 1016 kB；控制响应为虚拟值（详见 §4）。
- **打包补件完成**：`nx300m-mfw-services.zip` 59 文件，含 6 个此前缺件。
- 未完成项：菜单三输入的 VM 级验证、C 探针客户端的 connect 异常归因（不影响生产面）、实机射频/功耗（硬件未覆盖）。

## 1. 修复清单（全部落地并验证）

| 文件 | 修复内容 |
| --- | --- |
| `mod/deployment/mfw/hotspot.sh` | `set -u` 崩溃（`$ROOT` 先引用后赋值）；owner 文件改为双条目（hostapd+udhcpd 各自 `/proc/<pid>/exe` 身份校验）；`foreign_radio_user` 变量串扰与 `$pid_owner` 幽灵引用；失败路径一律 `stop_service` 回收已取得资源（含 `hostapd-pid` 缺失分支）；`flock` 串行（start `-n` 拒绝并发，stop 阻塞等待后整体收场）；`stopping` 状态；退化 restart（state=on 但进程死→先清理再重启）；移除 `ip link down`（只撤回本功能创建的地址）；地址写入 `$RUN/address` 供 UI 读取；守护进程在关闭锁 fd 的子 shell 中启动（否则 daemon 继承锁 → 关热点死锁）；移除内嵌 fixture 模式（改由 namespace 挂载替身，生产代码零测试钩子） |
| `mod/deployment/mfw/telnet_toggle.sh` | 新增 `toggle` 动作（原 UI 只能开不能关）；`-l` 只传裸程序路径（busybox telnetd 不经 shell 拆分参数），hash 改由 `NX_TELNET_HASH_FILE` 环境变量指向 0600 文件（不再进 argv）；补 `-K`；`/proc/net/tcp` 十六进制大小写修复（原小写永不匹配 → enable 必失败）；`BUSYBOX`/`LOGIN_BIN` `readlink -f` 规范化（`/var/run`→`/run` 符号链接会破坏 exe 身份匹配）；PID 复用防护（exe+cmdline 双校验）；disable 时按同一身份清扫会话子进程；`write_state failed:<stage>` 带阶段；无换行密码文件用 `cat` 读取（`read` 遇 EOF 无换行误报失败）；hash 随密码文件轮换重建（`find -newer`） |
| `tools/telnet_login.c` | hash 来源三通道：`--hash-file` / `NX_TELNET_HASH_FILE` / 默认 `$ROOT/.state/telnet-hash`；密码读取改非规范 tty + FIONREAD 处理 CR LF / CR NUL / 裸 CR（原实现裸 CR 会阻塞）；退格编辑；argv[0]="-sh" 登录 shell 约定；清 `NX_TELNET_HASH_FILE` |
| `mod/deployment/mfw/panel-actions.sh` | 分派模式补 `hotspot-stop`、`telnet-disable`（原漏项 → UI 能开不能关）；新增 `hotspot-credentials`、`telnet-password`；`telnet-toggle` 改映射 `toggle` |
| `mod/metol/nxui_feature_pages.h` / `nxui.c` | page 7（手机连接）：热点开关行 + "显示热点密码"行（仅屏显）+ 网络信息；page 8 恢复原版（刷新状态），Telnet 入口去重到 `gui_debug.NX300M`（设置→调试）；`starting:<stage>` 前缀匹配；热点地址来自 address 文件而非硬编码；allowed 列表补 `hotspot-credentials`/`telnet-password` |
| `mod/deployment/mfw/gui_debug.NX300M` | `开启/关闭 Telnet`（现走真 toggle）+ `显示 Telnet 密码` 行 |
| `tools/probe_panel_idle_vm.py` | `gui_debug items=7`→`9` 断言更新（静态更新；该探针本轮未运行，见未完成项） |
| `tools/build_mfw_services.py` | 打包补 6 缺件：hotspot.sh / telnet_toggle.sh / gui_debug.NX300M / bin/busybox.arm / bin/telnet_login.arm / licenses/busybox-static-GPL-2.0.txt |

服务来源与许可证：busybox-static **1.37.0**（Debian armhf 静态，GPL-2.0，仅用 telnetd/telnet/mkpasswd 路径；原厂 busybox 1.20.2 无 telnetd），`BUSYBOX-LICENSE.txt` 随包分发。登录认证为自研 `telnet_login.arm`（sha512-crypt，链接 stock libcrypt.so.1），不触碰相机 `/etc/shadow`，无 `-l /bin/sh`、无空密码 root（VM 验收含 argv 泄漏扫描）。

## 2. 构建命令与退出码

| 命令 | 退出码 |
| --- | --- |
| `tools/build_metol.py --menu-only`（PowerShell PATH 前置 LLVM） | 0（`NX300M ARM menu built; engines unchanged`） |
| `arm-linux-gnueabi-gcc … tools/telnet_login.c …`（nostdlib+crt0+stock libc/libcrypt） | 0；NEEDED=libc.so.6, libcrypt.so.1 |
| `arm-linux-gnueabi-gcc … tools/telnet_probe_client.c …` | 0 |
| `gcc tools/wireless_stub.c`（x86 替身）与 arm 版交叉编译 | 0 |
| `tools/build_mfw_services.py` | 0（59 文件，runtime-files.list 同步） |
| `outputs/review/2026-11-11-wireless-telnet/shell-vm/harness.sh`（unshare 隔离） | 0（133/133） |
| `tools/probe_telnet_vm.py`（qemu-system-arm 私有 VM） | 0（32/32，report.json passed=true） |

产物 sha256（前 16 位）：nxui.arm `effec4b831b2cd1b`；hotspot.sh `ebb930290d9a5c65`；telnet_toggle.sh `9ce291bb4834f41b`；telnet_login.arm `47ae729338492a2d`；busybox.arm `0a58280553462be5`；kit `7c1b71a0e472563a`。

## 3. 验收结果

### 3.1 shell 层隔离验收（133/133，`shell-vm/report.txt`）

真实生产脚本 + C 多调用替身（argv[0] 分派：hostapd/udhcpd/insmod/ip/busybox；可注入分阶段失败/慢启动/挂死）。覆盖：正常流程（调用顺序 insmod→link up→addr add→hostapd→udhcpd、配置 0600、owner 双条目、address 文件、重复 start 幂等、stop 逆序回收）、异常流程（hostapd/udhcpd 失败、缺二进制/驱动/网卡、link-up/addr 失败、短密码、各失败点资源回收）、所有权与冲突（外来地址/外来 hostapd/外来 wpa_supplicant 拒绝且不杀、PID 复用不误杀、已有地址保留）、并发串行（flock，单套进程）、启动中关闭（阻塞 stop 后全清）、Telnet 替身生命周期（真 socket 监听/关闭、port-in-use 拒绝、hash 轮换、密码不落日志）、热点关闭联动关 Telnet。

### 3.2 ARM VM 真实链路（32/32，`telnet-vm/results.txt`）

qemu-system-arm `-M virt -nic none`，真 ARM 内核 6.12 + 原厂相机 busybox 1.20.2 ash 跑两个生产脚本。Telnet 段：enable（绑定 127.0.0.1:23，LISTEN 实证）→ 错误密码 ×3（三次提示、`Login incorrect`、`Too many failures`、服务端关闭、监听存活）→ 正确密码（`Login OK` + shell 执行 `echo MOD_TELNET_OK_7391`）→ 断开重连 → disable 后端口不可达 → toggle 往返。全链 argv 无密码/hash。热点段（ARM 替身，真 fork/真 pidfile/真信号）：启停全周期 + 幂等 + 双 owner。

客户端说明：C 探针客户端在 qemu-user 与真内核两种环境下 connect 均异常（ENETUNREACH，sockaddr 字节正确），同 guest 的 stock busybox nc 一切正常——判定为测试工具自身问题，已换用**脚本化 stock-busybox nc**（真实 IAC 流量）作为验收客户端；C 客户端 anomaly 归因列入未完成项。生产面（telnetd+登录工具）不受影响。

### 3.3 菜单

菜单构建通过；新 UI 元素经字节级核验全部编入 `nxui.arm`（开启/关闭 MOD 热点、显示热点密码、热点切换中、hotspot-credentials、状态文件路径）。**键盘/转盘/触屏三输入的 VM 级探针本轮未运行**（见未完成项）。

## 4. 性能四组（同一私有 ARM VM，`telnet-vm/perf.txt`）

| 组 | worker 数 | RSS 合计 | CPU 累计(tick) | 热点 status 响应 | Telnet status 响应 |
| --- | --- | --- | --- | --- | --- |
| G0 全关 | 0 | 0 kB | 0 | 159 ms | 170 ms |
| G1 热点替身空闲 | 2 | 2016 kB | 0 | 120 ms | 170 ms |
| G2 Telnet 空闲 | 1 | 752 kB | 0 | 140 ms | 329 ms |
| G3 Telnet 有连接 | 1 | 1016 kB | 1 | 140 ms | 340 ms |

`boot_path_extra_workers=0`：冷启动路径未新增任何 worker/轮询（autoexec 链无热点/Telnet 引用，grep 证实）。**口径**：单次采样、TCG 模拟内核，控制响应为虚拟值，不代表实体相机时延；RSS/CPU 为 guest 内核真实值，可作相对量级参考。隔离 HTTP 吞吐未测（VM 无 8781 服务栈），标注为未覆盖。功耗仅来源分析：驱动常驻（insmod 后不卸载）、AP 信标广播、DHCP 定时器与客户端关联为主要开销来源；VM 数据不产生任何电池/续航结论。

## 5. 未完成项与原因

| 项 | 状态 | 原因与最小后续 |
| --- | --- | --- |
| 菜单三输入 VM 探针（page 7 新行 + gui_debug 新行） | 未运行 | 需菜单 VM 基线机制（Xvfb+基线 overlay），本轮预算内未搭建；行渲染与动作分派复用已验证框架路径，构建/字节核验通过。后续：复用 `probe_menu_startup_vm` 框架补键盘/转盘/触屏三路用例 |
| `probe_panel_idle_vm.py` items=9 断言更新 | 静态更新，未运行 | 探针与上轮基线机制绑定（要求保留 baseline-nxui.arm），避免混淆上轮测量 |
| C 探针客户端 connect 异常（ENETUNREACH，sockaddr 正确） | 工具问题，未归因 | 两种内核复现、stock nc 同环境正常；已改用 nc 客户端完成验收。后续可用 qemu -strace + glibc 版本对照归因 |
| 实体射频（SSID 可见/关联/吞吐）、真实 hostapd 参数有效性 | 硬件未覆盖 | 驱动/寄存器层无法离线验证；wlan.sh 的 regdom 参数沿用原厂默认 |
| 实机功耗 | 硬件未覆盖 | 只做了来源分析（§4），无任何电池结论 |
| 隔离 HTTP 吞吐 | 未测 | VM 内无 8781 服务栈；标为虚拟网络口径缺失 |

## 6. 边界自检

全程离线 ✓；未写卡/安装/刷机/发布 ✓；网络服务仅存在于私有 namespace 与 `-nic none` 私有 VM ✓；未在 Windows/共享 WSL 实际网络开启热点/DHCP/Telnet ✓（WSL 侧仅 loopback + namespace 内替身）；只停止本功能进程、保留 `/proc/<pid>/exe`+cmdline 身份检查、无 `killall` ✓；凭据 0600、不硬编码、不入日志（自动链含 argv 泄漏扫描）✓；不改原厂固件/shadow ✓；ABI7/build 3392396409 未动，Reset/Still IQ/Session/拍照仲裁/RAW/LCD DMA 未触碰 ✓；模拟通过不等于实体可用 ✓。清理：initramfs 副本、WSL chroot/sysroot 已删；小型原始日志与失败退出码保留于 `outputs/review/2026-11-11-wireless-telnet/`。

## 7. 手机 / PuTTY 连接说明（供上机后使用）

1. 相机：`手机连接` 页 → `开启 MOD 热点` → 行值显示 `SSID · 192.168.0.1`；`显示热点密码` 获取 WPA2 密码（仅屏显）。
2. 手机（任意支持热点的客户端，如 Termux）：加入该 SSID → `telnet 192.168.0.1 23`，用户名固定 root，密码即热点页显示值；连续 3 次错误将断开，重连即可重试。
3. 电脑（PuTTY）：Host `192.168.0.1`，Port `23`，Protocol `Telnet`；密码同上。Telnet 开关位于 `设置 → 调试与日志`（`开启/关闭 Telnet`、`显示 Telnet 密码`），仅使用相机当前网络（MOD 热点或已连接的 Wi-Fi），默认关闭；热点关闭时 Telnet 一并关闭。
4. 8781 HTTP：手机加入热点后直接访问 `http://192.168.0.1:8781`，沿用现有 NXConnect 服务，无新增入口。
