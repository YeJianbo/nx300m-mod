# 工作日志：收藏、热点/Telnet 边界与远程服务性能（2026-11-13 凌晨，全程离线）

执行 `docs/NIGHT_TASK_2026-11-12-wireless-telnet-favorites.md`（P1-1 至 P2-5、PERF-1/2/3，共 13 项必修；FONT-1 为可选项）。本会话为用户授权的普通直接会话（绕过故障的原生定时调度），非 Workflow/统一子代理任务；模型为 Start Plan 额度上的 GLM-5.3-Flash。全程离线：未连接实体相机/手机、未读写卡、未安装/刷机/推送发布；网络与远程服务测试仅运行在私有 namespace 或网络隔离的私有 VM 内。

## 0. 会话接续检查（先读）

- 启动时核对同范围会话：工作区最近 10 小时内仅任务书自身有改动（11-12 23:42）；在运行的 codex/ZCode 进程均为网关/桥接基础设施，无进程持有本工作区。本轮为唯一写者，未覆盖任何其他执行者文件。
- 模型与权益：会话身份为 `account:bigmodel-start-plan / GLM-5.3-Flash`（系统注入），mode=yolo；未切换账号或付费模型。权益 23 点刷新记录无法从会话内直接查询账户后台，以系统注入的模型身份与实际执行为准；如后台记录与本文不符，以真实执行日志为准。
- 源码起点与验收记录逐字节一致：hotspot.sh `ebb93029…`、telnet_toggle.sh `9ce291bb…`、nxui.c `2ee4f30…`、nxui_feature_pages.h `c8812f92…`、nxui_lut_panel.h `9da189fc…`、installer `f5d84b9f…`，与 `outputs/review/2026-11-12-wireless-telnet-acceptance/source-identity.json` 完全匹配，无漂移。

## 1. 修复清单（源码，全部已落地）

| 项 | 文件 | 内容 |
| --- | --- | --- |
| P1-1 | `mod/metol/nx_favorites_store.h`（新增）、`nxui_lut_panel.h`、`nxui.c`、`tools/nx300m_installer.py`、`tools/deploy_local_lut_library.py` | 新建与 LUT choice store 同构的双槽收藏存储（`NXFAV1 <slot-gen> <FNV-1a> <body>`，槽 `.state/lut-favorites.0/1`，flock 串行、先写后校验、旧单文件仅在验证后移除）；菜单读写、安装器 `favorite_keys/set_favorites`、look 删除回滚（快照/恢复）、部署工具备份全部统一到同一记录格式。空列表为合法显式状态；同值重试推进代数。 |
| P1-2 | `nxui_feature_pages.h:183` | `%s` 的实参由 `*feature_hotspot_state`（int）改为 `feature_hotspot_state`。菜单重编零警告（见构建记录）。 |
| P1-3 | `nxui.c`（poll_child）、`telnet_toggle.sh`、`tools/wireless_stub.c` | BACKEND 通用日志在打印 output 前把所有 `password=<value>` 值改写为 `***`（屏显路径不变）；mkpasswd 改为 `cat 密码文件 \| mkpasswd -m sha512 -P 0`，明文全程不进 argv（真机 busybox-static 1.37.0 `-P 0` 行为已用 qemu 实测验证）；替身 mkpasswd 同步支持 `-P FD`。 |
| P1-4 | `telnet_toggle.sh` | stop 只回收记录的 owner 及其**直接子进程**（PPid==owner 且 exe==本 busybox 且 cmdline 含 telnetd）；无 owner、身份变化、PID 复用一律不扩大清理。disable 后对记录的监听地址做 LISTEN 复核并输出 `listener=none/foreign:<addr>`。 |
| P1-5 | `hotspot.sh` | 新增 `wait_tracked_pid`/`config_cmdline_pid`：有界等待 PID 文件（延迟 PID），缺失时以 exe+私有配置路径身份扫描识别本次启动的守护进程（无 PID），PID 复用（exe 不符）拒绝；两守护（hostapd/udhcpd）分别覆盖。识别失败才报 `failed:<kind>-pid`，期间 stop_service 回收已取得资源。 |
| P2-1 | `hotspot.sh` | status 在 state=on 时核验两个 owner 存活、接口与地址：worker 死亡写 `failed:worker-dead:<missing>`，地址丢失写 `failed:address-lost`，接口消失写 `failed:no-interface`，rc=1；start 对任何旧状态先做 owner 限定清理（含 address-lost 时仍存活的 worker），再全新拉起，already=on 需 worker+地址齐全。查询保持按需，无新增轮询。 |
| PERF-1 | `mod/metol/remote/remote.c` | 上传接收 24 MiB 上限：`NX_UPLOAD_MAX_BYTES=(24MiB)`，监听器 `recv_mbuf_limit=24MiB+512KiB` 兜底；`upload_gate` 在 MG_EV_RECV 阶段解析到完整头部即判定——过大 Content-Length 立即 413、上传接口 chunked 立即 411、非法 Content-Length 400；同一时刻仅缓冲一个上传（全局槽，503 busy），槽在响应发出或连接断开时释放；能力查询返回 `max_bytes=25165824, chunked_upload=false`。模拟器代理与 App 同步该上限。 |
| PERF-2 | `remote.c::dirlist` | 普通文件/子目录仅一次 lstat（父目录已校验）；仅符号链接保留完整 realpath 边界校验；隐藏项与反斜杠名维持原规则；排序与 JSON/sizes 输出不变。 |
| PERF-3 | `runtime/camera_firmware_mod.c`、`runtime/close_inherited_fds.h` | launch_settings 的 fork 子进程改用 `nx_close_inherited_fds()`（getdents64 直读，无分配器/stdio 锁）；共享头新增 freestanding 模式（该编译单元无 libc 头，open/close 由文件自带 extern，ARM EABI getdents64=217、O_DIRECTORY/O_CLOEXEC 为固定内核 ABI 常量）；枚举失败沿既有调用者模式 `_exit(126)`，不再 exec。 |
| P2-4 | `docs/USER_GUIDE.md` | 新增"MOD 独立热点与 Telnet"章节：独立密码获取方式（Telnet 密码独立生成，与热点密码不同）、手动开关位置（系统设置→调试与日志）、实际监听地址/端口（以调试页显示为准）、只提示 `Password:` 无用户名询问、缺网络明确报错、3 次错误断开、热点关闭联动关 Telnet。上轮 worklog §7 为历史记录未改写，其错误在本轮文档中修正。 |
| P2-5 | `outputs/deployment/wireless-telnet/BUSYBOX-LICENSE.txt` | 1110 字节 Debian notice 替换为 20498 字节完整说明：二进制 SHA-256、版本（BusyBox v1.37.0 Debian 1:1.37.0-6+b9 armhf）、实际使用的 applet、对应源码取得（sources.debian.org/src/busybox/1:1.37.0-6+b9/ 与 deb 链接、deb SHA-256 `8b35210e08d559e…`）、重建参考、完整 GPL-2.0 全文（gnu.org 原文随包嵌入）。kit 内 `licenses/busybox-static-GPL-2.0.txt` 随重建更新。 |

不动项：保持 ABI7/build `3392396409`；未触碰 Reset、Still IQ、IPCS Session、拍照仲裁、RAW、LCD DMA；未恢复菜单实时取景或防息屏；未加入准入缓存扩容；原热点/Telnet 正常链路未重写。

## 2. 构建命令与退出码

| 命令 | 退出码 | 证据 |
| --- | --- | --- |
| `tools/build_metol.py --menu-only`（WSL ARM 交叉编译） | 0 | 菜单产物 `outputs/metol/nxui.arm` `66a59a0d…`；单独 `arm-linux-gnueabi-gcc -c mod/metol/nxui.c`（含 -Wall -Wextra）输出为空，P1-2 的格式警告消失 |
| `tools/build_remote.py --backend-only` | 0 | `outputs/metol/remote/build.log`；remote.c 无 error/warning（mongoose 自身的既有警告与本轮无关） |
| `tools/build_camera_firmware_mod.py`（clang freestanding -Werror） | 0 | `outputs/controls/camera_firmware_mod.so` 20532 字节 |
| `sh -n hotspot.sh && sh -n telnet_toggle.sh` | 0 | 语法检查 |
| `tools/build_mfw_services.py` | 0 | kit 59 文件，CRC 通过 |
| ARM 替身/测试二进制（wireless_stub、favorites_store_test、freestanding_fd_probe、fd_inheritance_test、count_calls、nx-remote-before） | 0 | 各自构建日志/清单见评审目录 |

## 3. 定向测试结果（全部真实执行）

### 3.1 收藏双槽（P1-1）

- **C 夹具（qemu-arm + 原厂 libc-2.13，`favorites_store_test.arm`）18/18 PASS**：旧单文件 5 款读入 → 双槽写入 27 款（耐久=1）→ 旧文件迁移移除 → 同值重试 → 新槽损坏（错误校验和）回退旧槽 → 零长度记录拒绝 → 短写（校验和对但正文无换行）拒绝 → 写失败（槽位为目录）检测且旧集合完好 → 显式空列表两次写入生效。日志 `outputs/review/2026-11-12-wireless-telnet-favorites/favorites-store-test.log`。
- **跨写者一致性 3/3 PASS**（`check_favorites_cross.py`，真实安装器 Python ↔ 真实 C store）：Python 写 27 款+C 读回 27；C 写+Python 读回 27；Python 写的坏校验和记录被拒绝；旧镜像由安装器移除。日志 `favorites-cross.log`。
- **范围限制（如实记录）**：真实卡上 26 条同名目录项的形成条件未复现——夹具在常规 Linux 文件系统上执行，不能表达 FAT/exFAT 同名目录项行为。本轮交付的是读写契约（损坏/短写回退、迁移、一致性）；实卡重复目录项根因仍未定位，不把夹具迁移通过写成实卡根治。

### 3.2 Shell 边界（P1-3/P1-4/P1-5/P2-1，`unshare --mount --pid --net --ipc`，真实生产脚本+记录型替身）

**33/33 PASS**（`checks.sh`，报告 `report.txt`）。关键断言：

- stale-hotspot：杀掉 AP 后 `status` rc=1、状态文件 `failed:worker-dead:hostapd`（不再假报 on）。
- no-pid / delayed-pid / udhcpd-no-pid：start 成功（替身不写/延迟写 PID 文件），owner 均记录，无孤儿进程；stop 后进程消失。
- dead-ap-restart：杀 AP 后再 start，旧资源清理、新 owner 存活、status rc=0。
- address-lost：截空地址后 status 报 `failed:address-lost` rc=1；start 重试后 state=on、地址恢复 192.168.0.1。
- foreign-telnet：本服务与外来 2323 实例并存时 disable——本实例死亡、端口 23 关闭，外来实例存活（foreign_survives=1）。
- hash-argv：enable 全程扫描 /proc/*/cmdline 未发现明文密码（0），hash 为 `$6$` sha512-crypt，disable rc=0。

### 3.3 远程服务（PERF-1/PERF-2，`remote_lab.sh`，私有 mount/PID/NET/IPC namespace，真实 ARM daemon + qemu-arm + Xvfb）

**24/25 PASS**（`remote-lab.log`、`remote-lab-evidence/`）：

- 能力：`max_bytes=25165824`、`chunked_upload=false`。
- 合法上传 200、bytes 回显与文件一致、`.nxoriginal` 备份建立；上传后 daemon VmHWM 11968→12224 kB（约 256 kB 增量，含缓冲与写卡路径）。
- **超大（25 MiB）请求在头部阶段 413 拒绝**：daemon 日志 `reject stage=oversize`；该连接 recv_mbuf 峰值仅 **238 字节**——载荷从未进入内存。
- chunked 上传 411；非法 Content-Length（`abc`）400。
- **并发限制**：23 MiB 合法体以 4 MB/s 持槽接收期间，第二个上传请求被 503 拒绝（`reject stage=busy`）。
- 中途断开（kill 客户端）后：下一个上传 200（槽已释放），原照片与备份完好（仍为 JPEG 魔数）。
- dirlist 边界：普通文件、根内符号链接列出；根外链接、悬空链接、点文件、反斜杠名隐藏；子目录与 sizes 输出正确。
- **性能计数（LD_PRELOAD 计数器，仅测试用，生产二进制不变）**：同一 3000 条目录——新实现一轮大目录+小目录 `realpath=21`；旧实现（本轮源码仅回退 dirlist 循环的测试专用构建 `nx-remote-before.arm`）`realpath=6004`（≈2×3003）。耗时（qemu-TCG 环境，不作实机换算）：新 0.176–0.198 s vs 旧 0.260–0.314 s。大目录列目录期间并发 health 0.17–0.20 s。
- **如实记录的边界**：(a) 缺失 Content-Length 的空 POST 在该 Mongoose 6.5 上不回复（curl 超时 000）——该行为在本轮改动前即存在（旧构建同样无响应），接收无无界分支（recv_mbuf_limit 兜底），不属于本轮修复项，未计入失败;(b) `input/inject` 在无相机程序的实验室里等待原厂应用上下文，15 s 无响应（health 同期 0.17 s 正常）——实验室环境限制，实机路径未覆盖;(c) glibc 2.13 LFS 将 stat/lstat 解析为 *64 符号，计数器未覆盖 *64 拼写——lstat 计数显示 0，但 realpath 计数（6004→21）不受影响且为主要判据;(d) qemu-TCG 的绝对耗时与 RSS（含 qemu 自身）不换算成相机数字。

### 3.4 设置启动 FD 清理（PERF-3）

- ARM freestanding 探针（`freestanding_fd_probe.arm`，qemu-arm + 原厂 libc）：打开 3/4/5 三个探针 → `nx_close_inherited_fds()` rc=0 → 三个探针再 close 全部 -1（确认真的关闭）→ 复开目录可见标准流+目录句柄（qemu 用户态会多见自身运行时描述符，计数仅作记录不作断言）。
- 宿主父子进程测试（`fd_inheritance_test`，x86 WSL）：父进程持有 fd 3 与 fd 5000（>4095），fork 子进程执行同款清理后 exec 检查器——子进程仅见 0/1/2（`child_normal_exec=1`），父进程两个描述符仍可用。枚举失败路径（tmpfs 覆盖 /proc）：子进程不 exec、以非零退出（126），父进程描述符完好。日志 `fd-inheritance.log`、`fd-inheritance-fail.log`。

### 3.5 菜单三路输入 VM 探针（P2-2）——探针已建成，运行未完成（如实记录）

复用既有私有 ARM VM 框架（`probe_menu_startup_vm`/`arm_vm`，网络隔离，真实 ARM nxui + Xvfb + XTest）编写了探针 `probe_wireless_menu_vm.py`：驱动真实菜单与真实热点/Telnet 脚本（ARM 版替身守护进程），覆盖键盘/转盘/触控三路进入手机连接页、真实开关、失败页可见（移除替身守护后 `failed:no-hostapd`，不崩溃、MENU 可退）、密码仅屏显（BACKEND 日志 `password=***` 且无明文）、Telnet 调试行开关与凭据、慢启动期间事件循环不冻结、MENU 退回根菜单。探针脚本与 overlay 均随评审目录交付。

**运行未完成**：多轮执行在 VM 内部以 rc=64 退出且控制台输出丢失——同一命令经 arm_vm 的 base64 分级通道执行时，连 `printf`/`base64` 级别的简单命令也返回 64 且无任何 stdout（串口日志 `wireless-menu-vm/serial.log` 保留原始证据）。早先轮次曾跑到页面导航与真实脚本触发（网络信息回读、热点启动到达 link-up 失败点），说明框架与探针逻辑本身可行；故障集中在 VM 执行传输层，超出本轮"最多一次有依据重试"的预算，未再消耗 VM 轮次。**菜单失败页可打开/可退出、密码仅屏显、三路输入的动态验证因此记为未验证**；P1-2 的修复以零警告编译与源码核查为证据，不声称动态探针通过。全部 VM 均已回收（每轮 `private_vm_stopped=true`）。

## 4. 候选包与发布链（P2-3）

- **独立 kit 已重建并核验**：`nx300m-mfw-services.zip` 59 文件、CRC 通过；包内 `hotspot.sh`、`telnet_toggle.sh`、`gui_debug.NX300M`、`bin/busybox.arm`、`bin/telnet_login.arm`、`bin/nxui.arm`、`licenses/busybox-static-GPL-2.0.txt` 齐全；包内菜单与本轮重编一致（`66a59a0d…`）、脚本与当前源码一致（hotspot `8c6d0184…`）；BusyBox 许可证含完整 GPL-2 文本与对应源码取得资料（20498 字节）。kit SHA-256 `de56fc3ce38dc459…`。
- **完整首装/升级候选：未能重新生成，原因如下（如实记录）**。发布链入口 `release_payload.deployed_overlay()` 要求 `outputs/performance/panel-control-prep/report.json` 的 `source_sha256` 与当前产物一致（"发布文件与本轮 ARM 回归不一致"门槛）。当前 7 项记录中 5 项一致、2 项漂移：`outputs/metol/nxui.arm`（记录 `ed8f32db…` → 现为 `66a59a0d…`）、`outputs/metol/remote/nx-remote-controller.arm`（记录 `28024a24…` → 现为 `b210ef86…`）。该报告由此前性能轮次的多 VM 回归套件生成（含 4 轮冷/热启动计时），本仓库无独立可重跑入口；在本轮预算内重新执行整套 ARM 面板回归不可行。升级保留 `.state/` 的清单机制未改动。**剩余**：重跑面板 ARM 回归套件刷新 `panel-control-prep` 后再生成完整首装/升级候选并核验；本轮未写卡、未发布。

## 5. 未采用项与未覆盖

- **FONT-1（可选）：未采用。** 13 项必修占用全部预算；共享只读字体映射的收益评估（创建失败/释放顺序/两字号一致性/实测内存）未开展，不影响必修验收。
- 真实射频、功耗、SD 卡延迟、实体连接表现：硬件未覆盖，不阻塞离线修复，不计为通过。
- 菜单三输入探针中触控路径采用候选坐标逐个验证（以日志证据接受），坐标约定与实机面板存在换算不确定性；如探针通过则以日志为准，不通过则如实记录。
- `input/inject` 与完整登录会话的实验室表现：见 3.3 边界记录。

## 6. 边界自检

全程离线 ✓；未连接实体相机/手机 ✓；未读写卡、未安装、未刷机、未推送发布 ✓；网络服务仅存在于私有 namespace（网络隔离）与网络隔离私有 VM ✓；WSL 宿主网络未改动、未在宿主开热点/DHCP/Telnet ✓；未重启网关/ZCode/Codex/AgentHost，未中断其他任务 ✓；凭据仅屏显、日志脱敏、mkpasswd 明文不进 argv（qemu 实测）✓；未硬编码密码、未免密绕过认证、未改 `/etc/shadow`、无按名 killall ✓；ABI7/build `3392396409` 未动 ✓；探针 VM/namespace 结束后回收（`private_vm_stopped=true`、namespace 退出）✓；大型可再生镜像（initramfs 副本、overlay）随探针目录清理 ✓。

## 7. 交付物索引

- 本文件：`docs/AGENT_WORKLOG_2026-11-12-wireless-telnet-favorites.md`
- 机器可读结果：`outputs/review/2026-11-12-wireless-telnet-favorites/result.json`
- Shell 定向测试：`outputs/review/2026-11-12-wireless-telnet-favorites/{prepare_checks.py,checks.sh,report.txt,stub.c,stubs/}`
- 收藏夹具：`…/favorites_store_test.{c,arm}`、`favorites-store-test.log`、`check_favorites_cross.py`、`favorites-cross.log`
- FD 探针：`…/freestanding_fd_probe.{c,arm}`、`fd_inheritance_test.c`、`fd-inheritance*.log`
- 远程实验室：`…/remote_lab.sh`、`remote-lab.log`、`remote-lab-evidence/`、`count_calls.{c,arm}`、`nx-remote-before.arm`
- 菜单 VM 探针：`…/probe_wireless_menu_vm.py`、`wireless-menu-vm/`（含 report.json/wireless.log/setup.log）
- 许可证：`outputs/deployment/wireless-telnet/BUSYBOX-LICENSE.txt`
