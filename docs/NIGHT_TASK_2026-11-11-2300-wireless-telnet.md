# 23:00 夜间任务：独立热点与 Telnet 收尾（全程离线）

> **2026-11-12 晚间续修入口**：[收藏、独立热点与 Telnet 修复任务书](NIGHT_TASK_2026-11-12-wireless-telnet-favorites.md)。接续执行以新任务书及本轮验收结果为准；下文保留上轮任务与执行历史。

> **后续验收：未通过。** 见 [离线验收与接续修复项](ACCEPTANCE_2026-11-12-wireless-telnet.md)。历史正常链路通过记录保留；收藏任务漏做，且新增复现发现菜单、凭据、资源归属及失败回收问题。

> **执行完成（2026-11-12 02:30）**：本任务书已在本会话执行完毕（定时任务因会话限制未创建，经用户"继续"指示直接执行）。结果见 [工作日志](AGENT_WORKLOG_2026-11-11-wireless-telnet.md) 与 [result.json](../outputs/review/2026-11-11-wireless-telnet/result.json)：shell 层隔离验收 133/133、ARM VM 真实链路 32/32、性能四组（虚拟口径）、打包 59 文件。未完成项（菜单三输入 VM 探针、C 探针客户端归因、实体射频/功耗）见工作日志 §5。

今晚 23:00 一次性执行。本文件是续作任务书，接续 `docs/OVERNIGHT_2026-11-11-wireless-telnet.md`（下称“原任务书”）。原任务书第 1、2 节只完成了代码落地，第 3 节（模拟验收与性能）**完全未执行**；且代码存在会让整条链路直接失败的缺陷。本轮目标是把现状变成真正可验收的离线交付，不是重复写一遍实现。

权威目录 `.`。`outputs/github-source/nx300m-mod` 是发布镜像，不是工作副本。保留其他执行者修改。

## 0. 起点现状（20:20 静态核对结论）

已落地但有缺陷：

| 产物 | 状态 |
| --- | --- |
| `mod/deployment/mfw/hotspot.sh` | 已写，**从未成功执行**（见缺陷 1） |
| `mod/deployment/mfw/telnet_toggle.sh` | 已写，`-l` 参数传递方式可疑（缺陷 7） |
| `tools/telnet_login.c` → `outputs/deployment/wireless-telnet/telnet_login.arm` | 已编译（exit 0），未在 Telnet 会话中验证 |
| `outputs/deployment/wireless-telnet/bin/busybox.arm` | 1.37.0 static ARM，已确认含 telnetd/telnet/mkpasswd/login |
| `outputs/deployment/wireless-telnet/BUSYBOX-LICENSE.txt` | 已放 |
| `panel-actions.sh` / `nxui.c` / `nxui_feature_pages.h` / `gui_debug.NX300M` | 已接，菜单可编译（`--menu-only` exit 0） |

未执行：原任务书第 3 节整节——热点正常/异常/所有权隔离验收、Telnet 真实协议测试、菜单三输入方式验证、默认与包内容核对、性能四组对照、交付文档。

### 已确认缺陷（静态核对，先修再验收）

1. **`hotspot.sh:38` 在 `set -u` 下引用未赋值的 `$ROOT`**（赋值在第 43 行）。实测 `sh hotspot.sh status` → `RC=2`，`ROOT: parameter not set`。任何 start/stop/status 都立即退出。
2. **`hotspot.sh:160` 引用不存在的 `$pid_owner`**。`read_owner` 只设置 `kind`/`pid`；系统里一旦有 hostapd/udhcpd/wpa_supplicant，冲突检测分支即崩溃。
3. **`hotspot.sh:169-203` `stop_service` 只关一个进程**。`for victim in udhcpd hostapd` 循环体末尾无条件 `break`，且 owner 文件只保存最后写者（udhcpd 覆盖 hostapd）→ hostapd 永不被停，停机后 AP 仍在广播。改为分别记录 ap/dhcp 两个 pid 并各自做 exe 身份校验。
4. **`hotspot.sh:329` hostapd 已启动但 pid 缺失时直接 `fail`**，未 `stop_service` → 残留 hostapd。所有 hostapd 之后的失败分支都必须回收已取得资源。
5. **无并发串行化**。全程无 `flock`，state 读写非原子；两个并发 start 都会读到 off 并各起一套进程。加锁文件（`flock -n` 失败即报“另一请求进行中”），锁只包状态机，不包长时间等待。
6. **stop 不写 `stopping` 状态**，原任务书要求的“停止中”不可观测。
7. **`telnet_toggle.sh:190` `-l "$login_bin --hash $hash_argument"`**。busybox telnetd 的 `-l` 是程序路径，不经过 shell 拆分；带空格的字符串很可能 exec ENOENT。**23:00 先实测确认**：若失败，改为在 `$RUN` 生成 0700 包装脚本（内含 hash），或让 `telnet_login.arm` 自己从 0600 状态文件读 hash（更优，hash 不再进 argv）。
8. **`telnet_toggle.sh` 未传 `-K`**，login 退出后连接可能挂住不释放。
9. **`gui_debug.NX300M` 的 Telnet 行只能开不能关**：行动作固定 `telnet-toggle`，panel-actions 把它映射到 `enable`，没有 disable 分支。
10. **热点页无密码行**。原任务书要求“显示 SSID、密码和 IP”；脚本有 `credentials` 子命令，但 panel-actions 和 UI 都没接。
11. **Telnet 入口重复**：同时出现在 page 8（由“模块状态”进入）和 `gui_debug.NX300M`，且 page 8 语义不符“设置 → 调试”。
12. **打包缺件**：`tools/build_mfw_services.py` 未包含 hotspot.sh / telnet_toggle.sh / busybox.arm / telnet_login.arm / BUSYBOX-LICENSE.txt / gui_debug.NX300M。
13. **既有探针会失败**：`tools/probe_panel_idle_vm.py:192` 断言 `gui_debug.NX300M items=7`，当前 8 行。
14. **`panel-actions.sh:233` 分派模式漏掉 `hotspot-stop` 与 `telnet-disable`**。顶层 case（第 7 行）接受这两个动作，但第二个 case 的模式是 `hotspot-status|hotspot-toggle|telnet-status|telnet-toggle`，两个“关闭”动作落入 `esac` 之后继续走媒体源检查，最终无法抵达脚本。结果：UI 上能开不能关。实测确认后把两个动作加入模式串。

## 1. 修复阶段（先做，约 45 分钟）

按缺陷 1→6 顺序修 `hotspot.sh`，每修一处用 `sh -n` 加一次 stub 环境实跑确认，不要攒到最后一起测。

修复后必须成立的不变式：

- 任何子命令在干净环境（无 `/var/run/nx300m-hotspot`、无 hostapd/udhcpd 进程）都返回明确结果，不出现 shell 参数错误。
- owner 记录必须能同时表达 AP 与 DHCP 两个进程，且分别做 `/proc/<pid>/exe` 身份校验。
- 失败路径清理范围 = 已取得资源集合。未取得的不得尝试删除（尤其接口地址）。
- 并发第二个请求明确失败，不排队、不重复起进程。
- 状态集合严格为 `off` / `starting:<stage>` / `on` / `stopping` / `failed:<stage>`。

`telnet_toggle.sh` 修缺陷 7、8：优先改成 login 工具自读 hash 文件，避免 hash 进 argv；`-l` 只传程序路径。补 `-K`。

## 2. 隔离模拟验收（约 60 分钟）

用 `unshare --mount --net --ipc --pid --fork --mount-proc` 独立 namespace，**真实生产脚本** + 记录型命令替身（`ip`/`iwconfig`/`hostapd`/`udhcpd`/`insmod` 一律替身为写入 `calls.log` 的脚本），替身可按环境变量注入分阶段失败。不复刻脚本逻辑。

| 范围 | 必须验证 | 落点 |
| --- | --- | --- |
| 热点正常 | 配置生成内容与调用顺序；start→status→stop；重复 start 返回 `already=on` 且进程数不变；stop 后 address 被撤、hostapd+udhcpd 都消失 | `hotspot-normal/` |
| 热点异常 | hostapd 失败、udhcpd 失败、缺 hostapd/udhcpd/驱动/wlan.sh、无网卡、密码过短、启动中第二个请求、start 中途 stop | `hotspot-failure/` |
| 所有权冲突 | 外来地址、外来 hostapd、外来 udhcpd、外来 wpa_supplicant 各自拒绝且 `calls.log` 无 kill；PID 身份变化（`/proc/pid/exe` 指向别处）不误杀；已有地址/路由保留 | `hotspot-ownership/` |
| 并发 | 两个 start 同时启动，只有一个进入状态机，另一个明确报“进行中”，最终进程数 1 套 | `hotspot-concurrency/` |
| Telnet 协议 | 私有 ARM VM/隔离网络内启动真实 busybox telnetd + telnet_login.arm；客户端完成 IAC 协商、错误密码被拒、正确密码进入 shell 并执行一条受限测试命令、断开后重连、disable 后端口不可达 | `telnet-vm/` |
| 菜单 | 键盘、转盘、触控三条路径进入同一页面且动作一致；状态来自脚本输出；失败原因可见；MENU 退出；后台操作期间 UI 不冻结（用既有私有 VM 探针口径） | `menu-vm/` |
| 默认与包 | 冷启动无 hostapd/udhcpd/telnetd 进程；缺 busybox.arm 时 `failed no-busybox`；缺 login 工具时 `failed no-login-tool`；包清单含全部脚本/二进制/许可证；升级不覆盖 `.state/` 用户设置 | `package/` |

每条用例记录：命令、退出码、关键输出行、结论。失败就是失败，不把“脚本没崩”算通过。

## 3. 菜单与打包（约 30 分钟）

- page 8 与 `gui_debug.NX300M` 的 Telnet 入口去重，只保留“设置 → 调试”一处；`gui_debug` 行改成状态相关动作（关时 `telnet-toggle`，开时 `telnet-disable`），或拆成开/关两行。
- 热点页补密码行：经 `hotspot.sh credentials` 取，仅屏显，不写日志、不写截图。
- `tools/build_mfw_services.py` 补 6 个缺件，`runtime-files.list` 与 inventory 同步；busybox.arm 走 `arm_elf` 依赖扫描时若报错，单独在报告中说明并保留文件字节校验。
- 更新 `tools/probe_panel_idle_vm.py` 的 `items=7` 断言（改 8 或按实际行数）。
- 构建：`tools/build_metol.py --menu-only`，记录 exit code 与产物 sha256。

## 4. 性能四组对照（约 30 分钟）

同一个私有 VM，相同输入与计时口径，四组：

1. 新功能均关闭
2. 热点控制空闲（替身，不起真实进程）
3. Telnet 空闲（telnetd 监听、无连接）
4. Telnet 有连接（一个客户端保持会话）

每组记录：进程数、RSS、CPU 累计时间（`/proc/<pid>/stat` utime+stime）、一次控制动作的响应时间（脚本 start/status 到首行输出）。可加测隔离 HTTP 传输，但必须标注“虚拟网络吞吐”。

另外核对：关闭状态下开机路径不新增 worker/轮询——对照 `autoexec-connected.sh`/`services.sh` 的进程与 sleep 循环数，确认没有为新功能加常驻。

## 5. 交付（约 20 分钟）

- `docs/AGENT_WORKLOG_2026-11-11-wireless-telnet.md`
- `outputs/review/2026-11-11-wireless-telnet/result.json`
- 手机终端与电脑 PuTTY 连接说明（写入 worklog 或 `docs/NXCONNECT.md` 增补节）：运行时显示的相机 IP、23 端口、用户名、密码获取方式。
- 明确三分法：`implemented` / `offline_verified` / 硬件未覆盖。
- 未完成项写原因与最小上机步骤。

## 硬约束（照原任务书，不得放宽）

全程离线：不连接实体相机或手机、不写卡、不安装、不刷固件、不推送或发布。网络服务测试仅在隔离环境内运行；不在 Windows/共享 WSL 的实际网络上开启热点、DHCP、Telnet，也不加载无线驱动、改路由或执行原厂清理命令。涉及 mount 的探针使用独立 mount/PID namespace；Shell 与绘制私有 VM 串行，完成后停止自己的 VM。

保持 ABI7/build 3392396409；不修改原厂 Reset、Still IQ、IPCS Session、拍照仲裁、RAW、LCD DMA，不恢复菜单实时取景或此前撤回的防息屏。Fn 保存不应用不退出、OK 保存应用后回取景、MENU 返回等现有语义保留。

凭据不硬编码、不写进日志或截图。不以 `-l /bin/sh` 或空密码 root 作为默认远程入口。只停止本功能拥有的进程/资源，保留真实进程身份检查，不按名字 `killall`。不往原厂固件写入配置；临时配置和敏感信息使用适当权限，日志隐去密码。

Python 统一 `D:/conda/envs/nx300m-mod/python.exe -X utf8`。菜单修改先用 `tools/build_metol.py --menu-only`；新增服务按实际依赖构建。保留小型原始日志与失败退出码，清理自己生成的大型可再生 VM 镜像。

不将模拟开热点等同于实体已可用。无需等待上机，不自动部署或发布。

## 明确不做

- 不恢复 RVF 遥控视频/拍摄会话，不触发 RVF 授权。
- 不附带 FTP。
- 不实现 Wi-Fi 客户端（station）连接流程。
- 不改原厂 `/etc/shadow`、不调用 `wlan.sh` 的 killall 清理函数。
- 不为跑满时间做无关重构。

## 失败处理

单个 VM/工具失败先看原始错误，限定重试一次；仍不可用则记录未验证原因并转做剩余可跑项。实体射频、真实功耗、真实蓝牙/Wi-Fi 吞吐列“硬件未覆盖”，不用模拟结果顶替。

## 调度信息（供新会话创建定时任务时粘贴）

会话限制：当前对话本身由定时任务触发，ZCode 不允许在其中再创建定时任务（`Cannot create a scheduled task inside a session that already belongs to a scheduled task`）。请在普通新会话中执行，或把下面整段作为定时任务 prompt 提交。

- 时间：2026-11-11 23:00（Asia/Kuala_Lumpur，UTC+8），一次性
- 任务书 sha256：`54f8d5efdc9af08814ed604bc7a59971e1d5fa7951caf8e63982ff8d06782428`
- 模型：沿用 `5.3 idle` 偏好，实际标识以执行端回执为准

prompt 正文：

```
# 2026-11-11 23:00 夜间执行：独立热点与 Telnet 收尾（全程离线）

现在开始执行 ./docs/NIGHT_TASK_2026-11-11-2300-wireless-telnet.md。
这是续作任务书，接续 docs/OVERNIGHT_2026-11-11-wireless-telnet.md。先完整读这两份文件与
docs/ACCEPTANCE_2026-11-11-idle.md，再读实际代码。不要重做已完成的实现。

## 起点：实现已落地但从未成功执行过，以下缺陷先自行复现再修

1. hotspot.sh:38 在 set -u 下引用未赋值的 $ROOT（赋值在第 43 行）→ 实测
   `sh hotspot.sh status` 返回 rc=2 "ROOT: parameter not set"，任何子命令立即退出。
2. hotspot.sh:160 引用不存在的 $pid_owner；read_owner 只设 kind/pid。系统里存在
   hostapd/udhcpd/wpa_supplicant 时冲突检测分支崩溃。
3. hotspot.sh:169-203 stop_service 循环体末尾无条件 break，且 owner 文件只存最后写者
   → 只停 udhcpd，hostapd 永不停，停机后 AP 仍在广播。
4. hotspot.sh:329 hostapd pid 缺失时直接 fail，未回收已启动的 hostapd。
5. 全程无 flock，并发两个 start 会各起一套进程；stop 不写 stopping 状态。
6. telnet_toggle.sh:190 用 -l "$login_bin --hash $hash_argument" 传带空格字符串给
   busybox telnetd（telnetd 不经过 shell 拆分），很可能 exec ENOENT；且未传 -K。
7. panel-actions.sh:233 分派模式漏掉 hotspot-stop 与 telnet-disable，UI 上能开不能关。
8. gui_debug.NX300M 的 Telnet 行只能在关时开；热点页没有密码行；Telnet 入口在
   page 8 与 gui_debug 重复。
9. tools/build_mfw_services.py 未打包 hotspot.sh / telnet_toggle.sh / busybox.arm /
   telnet_login.arm / BUSYBOX-LICENSE.txt / gui_debug.NX300M。
10. tools/probe_panel_idle_vm.py:192 断言 gui_debug.NX300M items=7，当前 8 行。

## 执行顺序

1. 复现缺陷 1-4（stub PATH + 隔离 namespace），逐处修复，每修一处立刻 sh -n + 实跑确认。
2. 修 Telnet 的 -l 传递：优先让 telnet_login.arm 自己从 0600 状态文件读 hash（hash 不进程
   argv），-l 只传程序路径，补 -K。改后重新构建该二进制，记录命令与退出码。
3. 隔离模拟验收：unshare --mount --net --ipc --pid --fork --mount-proc，真实生产脚本 +
   记录型命令替身（ip/iwconfig/hostapd/udhcpd/insmod 写入 calls.log，可按环境变量注入
   分阶段失败）。覆盖任务书第 2 节表格全部行。
4. Telnet 真实协议：私有 ARM VM/隔离网络内跑真实 busybox telnetd + telnet_login.arm，
   客户端完成 IAC、错误密码被拒、正确密码进 shell 执行一条受限测试命令、断开重连、
   disable 后端口不可达。
5. 菜单：键盘/转盘/触控三路一致；状态来自脚本；失败可见；MENU 可退；后台操作不冻结 UI。
   去重 Telnet 入口到"设置→调试"，热点页补密码行（仅屏显、不写日志截图）。
6. 打包补 6 个缺件，更新 runtime-files.list 与 inventory；修 items=7 断言。
   构建 tools/build_metol.py --menu-only，记录 exit code 与 sha256。
7. 性能四组：同一私有 VM，新功能全关 / 热点控制空闲（替身）/ Telnet 空闲 / Telnet 有连接，
   记录进程数、RSS、CPU 累计时间、控制响应时间；隔离 HTTP 吞吐标注为虚拟。
   核对关闭状态开机路径未新增 worker/轮询。
8. 交付 docs/AGENT_WORKLOG_2026-11-11-wireless-telnet.md 与
   outputs/review/2026-11-11-wireless-telnet/result.json，含修改文件、构建命令/退出码、
   通过与失败用例、原始证据路径、服务来源/版本/许可证、性能口径、未完成项与原因。
   区分 implemented / offline_verified / 硬件未覆盖。
   附手机终端与 PuTTY 连接说明（运行时相机 IP、23 端口、用户名、密码获取方式）。
   不把模拟开热点说成实体可用。

## 硬约束

全程离线：不连接实体相机或手机、不写卡、不安装、不刷固件、不推送或发布。网络服务测试
只在隔离环境内；不在 Windows/共享 WSL 实际网络上开热点、DHCP、Telnet，不加载无线驱动、
不改路由、不执行原厂清理命令。涉及 mount 的探针用独立 mount/PID namespace；Shell 与绘制
私有 VM 串行，完成后停止自己的 VM。保持 ABI7/build 3392396409；不改原厂 Reset、Still IQ、
IPCS Session、拍照仲裁、RAW、LCD DMA，不恢复菜单实时取景或已撤回的防息屏。Fn 保存不应用
不退出、OK 保存应用后回取景、MENU 返回语义保留。凭据不硬编码、不进日志或截图；不以
-l /bin/sh 或空密码 root 作为默认远程入口；只停本功能拥有的进程/资源，保留 /proc/<pid>/exe
身份检查，不按名字 killall；不往原厂固件写配置，临时配置与敏感信息用适当权限。
Python 统一 D:/conda/envs/nx300m-mod/python.exe -X utf8。保留小型原始日志与失败退出码，
清理自己生成的大型可再生 VM 镜像。

## 失败处理

单个 VM/工具失败先看原始错误，限定重试一次；仍不可用则记录未验证原因并转做剩余可跑项。
实体射频、真实功耗、真实无线吞吐列"硬件未覆盖"，不用模拟结果顶替。
完成有价值的可验收工作后停止，不自行创建续行任务。
```

