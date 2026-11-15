# 2026-11-13 验收：收藏、独立热点、Telnet 与远程服务性能

**验收未通过。** ZCode 已结束执行，但当前代码有三项经短测复现的逻辑缺陷，另有菜单状态接线、源码取得说明和未完成的动态验证需要收尾。`all_actionable_passed=false`。

权威目录为 `.`。本轮只验收昨天的 [13 项任务](NIGHT_TASK_2026-11-12-wireless-telnet-favorites.md)，未执行新写的 25Hz 性能任务。全程离线，未连接设备、读写卡、刷机或发布，未修改生产源码。

## 执行状态与源码身份

- 原生 ZCode 会话：`sess_375cdab5-5009-4171-8e99-b0fc8d0ec3b5`。
- 对应 turn：`turn_f498c9a2-ff26-4440-a79c-5afbcf9eef7e`，数据库状态 `completed`，UTC+8 的 05:40:37 开始、07:46:00 结束。
- 验收前，工作日志记录的 21 项源码及产物 SHA-256 全部匹配当前文件；验收中生产源码没有变化。
- 菜单、远程后端、固件 RAM 模块均重新构建成功；两份生产 Shell 语法检查通过。
- 菜单和后端重编后，只有 ELF `.strtab` 的 6 字节临时文件名变化，其他节与 kit 原件一致；不把这项非运行内容差异判为代码回归。完整安装链的 ARM 回归门槛仍未满足，实际调用 `deployed_overlay()` 已确认拒绝生成。
- 任务书沿用的 build `3392396409` 与起点现状不符：当前 `runtime/lut_ram_blob_build.h` 是 `885093066U`，时间为 11 月 7 日，早于本轮任务。验收未改动它；后续交接应核对正确基线，不按旧文案改回。

机器结果与原始证据：[验收目录](../outputs/review/2026-11-13-wireless-telnet-acceptance/result.json)。

## 必须修复的发现

### P1-1：收藏最新槽在 slot 0 时，C 读取会返回 slot 1 的旧内容

`mod/metol/nx_favorites_store.h::nx_favorites_read_root_full()` 在比较代数之前，就把每个槽读进同一输出 `body`。slot 0 即使代数较新，随后读取 slot 1 仍会覆盖正文，代数判断只更新局部变量，没有恢复正确正文。

用真实安装器连续保存 A、B、C、D，真实 C store 的结果为 **A、B、B、D**；Python 读取一直正确。本轮重新编译 C 夹具后重复复现。菜单 `lut_init_groups()` 使用这个 C 入口，因此会发生收藏回退或点完立即恢复旧集合。

证据：`favorites-rebuilt-alternating.json`。原 18/18 夹具重新运行仍通过，说明它遗漏了两个有效槽内容不同且 slot 0 最新的情形；原跨写者 3/3 也只来回写相同的 27 款集合。

修复：用独立候选正文读取槽，仅在候选代数胜出时复制到结果；覆盖不同集合的交替写入、清空、增删及跨语言读取。安装器 `favorites_write_body()` 仍通过 `write_checked()` 的临时文件加 `os.replace()` 写固定槽；需一并核对它与本项目避免 FAT 覆盖 rename 的存储要求是否一致。

### PERF-1：合法分段上传被自己的并发限制拒绝

`remote.c::upload_gate()` 首段收到请求头后设置 `MG_F_USER_3` 并增加全局活跃数；下一次 `MG_EV_RECV` 又检查同一请求头，看到活跃数为 1，就把当前上传自己拒绝为 503。

私有 mount/PID/net/IPC namespace 内运行本轮重编的真实 ARM 后端：9 字节小 JPEG 上传 200；**合法 1MiB JPEG，限速 64KiB/s 分段发送，收到 503**，目标照片保持 9 字节。daemon 原始记录为 `accept ... active=1` 后立即 `reject stage=busy ... active=1`。上一轮并发测试只断言第二个请求 503，没有断言第一个合法大请求上传完成，漏掉了这个回归。

证据：`remote-short.log`、`remote-short-daemon.log`、`remote-short.sh`。

同一短测还确认缺失 Content-Length、`Content-Length: 10abc` 两种请求均超时无回复。后者被解析成 10，没有拒绝尾部非法字符。任务书明确要求这些边界得到处理，不能因旧版本也有超时而移出验收。

修复：已取得接收槽的连接不重复取得；槽位释放覆盖完成、拒绝、断连。完整校验长度及不支持的传输编码，缺少长度立即明确拒绝。验证合法大 JPEG 的完整写回、备份和响应，再检查真实第二连接 503、断连释放与接收峰值。慢 I/O 下 preserve/replace 阻塞测量仍未做。

### P1-4：Telnet 监听关闭了，会话子进程仍残留

`telnet_toggle.sh::stop_service()` 先终止 owner，再按 `PPid==owner` 扫描子进程。owner 退出后，子进程已被重新收养，扫描漏掉它；实际登录进程还可能已经 exec 为 login 或 shell，单靠 busybox/telnetd 字样也覆盖不了。

复用既有 namespace 夹具及会话替身，运行真实生产脚本：关闭前 owner=51、子进程=52，两个进程；关闭返回 0、输出 `listener=none`，**52 仍活着且 PPid 已变成 1**。这是脚本资源回收缺陷的复现；真实 ARM 已登录 shell 的关闭验证仍待补。

证据：`telnet-short.log`、`telnet-short.sh`。旧 33/33 中 `ours_gone` 只检查监听 owner，没有检查所属会话。

修复：在父进程退出前取得并核验所属会话身份，回收顺序与登录/exec 生命周期协调；保留外来同二进制实例。补真实登录、执行有限命令、disable 后会话断开及进程树消失的验证。

### P2-1：Shell 状态修好了，菜单仍只读旧文件

源码检查：`feature_wireless_snapshot()` 直接读取旧 state；进入连接页只调 `network-info`，该动作不调用热点 `status`。热点开关完成后，`poll_child()` 也没有重建第 7 页。AP 自行死亡时若无人显式执行 `hotspot.sh status`，菜单仍可显示旧 on；开关后的行值与密码入口刷新也没有完整接线。

证据位置：`nxui_feature_pages.h` 的 `feature_wireless_snapshot`、`feature_page(7)`、`feature_action("connection")`；`nxui.c::poll_child()`；`panel-actions.sh` 的 `network-info`。这是源码确认的接线缺口，动态菜单探针本轮仍未完成。

修复：进入页面时做一次有界异步状态查询，完成开关/查询后按实际结果刷新页面与焦点；无需新增常驻轮询。补死 AP、地址丢失、成功开关、失败重试的真实菜单验证。

### P2-5：BusyBox 的二进制版本被当成源码版本

完整 GPL 文本已随包补齐，但随包说明写的是 `apt-get source busybox (=1:1.37.0-6+b9)`，且对应 sources.debian.org 路径也带 `+b9`。`+b9` 是二进制重建版本；Debian 对应 source 版本为 `1:1.37.0-6`。

本轮公开源核验：`busybox_1.37.0-6.dsc` 返回 200，正文声明源码版本；`busybox_1.37.0-6+b9.dsc` 返回 404。sources.debian.org API 因证书过期未成功读取，未绕过 TLS 校验。

证据：`license-source-check.json`。修正源码版本和可下载的 `.dsc`/源码、Debian packaging 资料，保留二进制原本的 `+b9` 身份；重建候选 kit 后再核对随包内容。

## 13 项验收状态

| 项目 | 结果 | 当前有效证据与剩余 |
| --- | --- | --- |
| P1-1 收藏 | 失败 | 已复现不同集合交替写入读回旧槽 |
| P1-2 失败页崩溃 | 部分通过 | 参数已修、重编成功；实际失败页可见与 MENU 退出未验证 |
| P1-3 凭据 | 部分通过 | 日志脱敏和 stdin 哈希源码已落地、旧 argv 短测有效；完整菜单链缺测 |
| P1-4 Telnet 归属回收 | 失败 | 外来实例保留有证据，但本服务会话残留已复现 |
| P1-5 AP/DHCP 启动回收 | 部分通过 | no-pid、hostapd delayed-pid、重启等旧短测通过；任务要求的两类 worker 异常退出和 PID 变化矩阵未完整交付 |
| P2-1 无线状态 | 失败 | Shell 故障检测有进展；菜单读取和刷新接线仍缺 |
| P2-2 菜单三输入 | 未验证 | VM exec 返回 64，`wireless.log`、`setup.log` 均未收集；旧 VM 已回收 |
| P2-3 候选包 | 部分通过 | kit 59 文件 CRC、脚本、菜单与后端运行内容核对通过；完整首装/升级包未生成 |
| P2-4 连接说明 | 通过 | 已补独立密码、手动入口、地址和 Password 提示 |
| P2-5 许可证与源码资料 | 失败 | GPL 全文具备；源码取得的版本指引需修正 |
| PERF-1 上传 | 失败 | 分段合法上传 503；缺少/非法长度超时；慢 I/O 缺测 |
| PERF-2 目录扫描 | 部分通过 | 源码保留链接边界；原始日志 realpath 6004→21、边界用例通过；input 同期延迟测量未取得有效结果 |
| PERF-3 设置 FD 清理 | 通过核心路径 | 共享 getdents64 实现、重编通过；复用低 FD/5000 FD、父进程保留、枚举失败证据；launch_settings 的 exec 失败专门探针未交付 |

可选 FONT-1 未采用。目录性能数字来自 QEMU-TCG；真实射频、功耗、SD 延迟和实体连接仍单列待上机。

## 下一轮最短收尾顺序

1. 修收藏选槽、上传重复准入、Telnet 会话回收，先跑上面三个反例及正常路径。
2. 补菜单状态接线，修复 VM 命令传输后完成键盘/转盘/触控、失败页、凭据仅屏显、MENU 退出。
3. 完成上传长度与并发边界、受控慢 I/O 测量；补缺少的生命周期用例。
4. 修随包源码说明，刷新与本轮产物匹配的 ARM 回归记录，重建并核验完整首装/升级候选，保留 `.state/`。

先完成这些正确性收尾，再执行 25Hz 等新增性能任务。
