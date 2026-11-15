# 工作日志：验收收尾与性能首项（2026-11-14，全程离线）

执行 `docs/NIGHT_TASK_2026-11-13-performance-25hz.md`（含 2026-11-14 派发补充）。模型为 Start Plan 额度上的 GLM-5.3-Flash（`account:bigmodel-start-plan`，系统注入身份），未切换账号或付费模型。全程离线：未连接实体相机/手机、未读写真实卡、未安装/刷机、未推送 GitHub/发布；网络与进程测试仅运行在私有 namespace（unshare mount/pid/net/ipc）内，无共享 `/var/run` 写入，测试进程全部随 namespace 退出回收。

## 0. 接续核对（先读）

- 派发前先读了 `outputs/review/2026-11-13-playback-custom/root-cause/result.json` 及其 `fixes/result.json`、`docs/ACCEPTANCE_2026-11-13-wireless-telnet-favorites.md`、`docs/AGENT_WORKLOG_2026-11-12-wireless-telnet-favorites.md`。
- **源码身份核对：15 个关键文件 SHA-256 全部与两份现有证据一致**（SmartFilter/session/fixture/探针/双产物对齐 root-cause 最新值；favorites/remote/telnet_toggle 对齐 fixes 值；`nxui.arm`、`nx-remote-controller.arm` 以较新的 root-cause 值为准）。相册换 LUT 根因修正的三份源码（`runtime/smartfilter_ram.c` `4895d0c1…`、`runtime/smartfilter_session.inc` `c545e33c…`、Session ABI7 build `1647620021` 的两个部署产物）本轮**逐字节未动**，本轮结束时的哈希复验一致。
- 前一轮已修的三个核心缺陷（C reader 双槽候选、上传重复准入、Telnet 会话回收）**未重做**；本轮只做各自剩余验收（CLOSE-FAVORITES/UPLOAD/TELNET）与其余清单项。
- 旧文案中的 build `3392396409` 不再出现；本轮未触碰 `runtime/lut_ram_blob_build.h`（仍为 `885093066U` 世代 `002514a0…`），未触碰 Reset、Still IQ、IPCS Session、拍照仲裁、RAW、LCD DMA；未恢复菜单实时取景、防息屏或图像向导路径。

## 1. 本轮实际改动（生产源码 6 文件 + 随包文本 1 文件）

| 文件 | 内容 |
| --- | --- |
| `tools/nx300m_installer.py` | CLOSE-FAVORITES：新增 `favorites_write_slot_direct()`（`O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_BINARY` 直写固定槽 + fsync + 逐段写完）与 `favorites_fsync_directory()`（POSIX 目录 fsync；Windows 无 `O_DIRECTORY` 时跳过）。`favorites_write_body()` 改为直写目标槽（不再走 `write_checked()` 的临时文件 + `os.replace()`——相机 FAT 驱动对 rename-over-existing 产生重复目录项），回读解析校验后才 unlink 旧镜像；`favorites_restore()`（删除回滚）同样改为逐槽直写 + 回读校验。同值重试语义保持（generation = max+1）。 |
| `mod/metol/nxui.c` | CLOSE-MENU：`hotspot-status`/`telnet-status` 加入只读动作集（PDEATHSIG+nice19+12s 有界取消）；只读/只读取消/失败三类完成路径在 `mfw_preview==7` 时按脚本重写的 state 文件重建第 7 页（保留状态文本、夹取焦点）；开关成功路径同样重建。 |
| `mod/metol/nxui_feature_pages.h` | CLOSE-MENU：进入连接页改为有界异步 `hotspot-status`（原只调 `network-info`）。`hotspot.sh status` 复核两个 owner 存活与地址并把 `failed:worker-dead:*`/`failed:address-lost` 写回 state 文件，完成后页面重建，死 AP/地址丢失不再假报 on，失败行回到可重试的开启入口。 |
| `mod/metol/remote/custom_key.h` | BOOT-KEY：helper 启动期对菜单窗口做**有界轮询等待**（250ms × 上限 6s，alarm 兜底），窗口迟到不再立刻失败；父进程区分"启动未就绪"（reason 1/2/3 且该 helper 从未 READY）与真实错误——未就绪失败在 24 次内固定 1s 快速重试（不再 2→4→8…60s 递增），超限或真实错误保持原退避梯子；helper 到 READY 后恢复原梯子语义。新增 `boot_retries`/`not_ready_streak` 状态字段与 `NX_CUSTOM_RETRY`/`NX_CUSTOM_BOOT` 有界日志行。 |
| `mod/metol/lut_tune_store.h` | SAVE-SAME：`nx_tune_preset_save()` 增加同值免写路径——最新有效槽 payload 与请求完全一致且**本进程曾对该 (root,id,slot,generation) 以 file+dir 双 fsync 确认耐久**、且文件身份（dev/ino/size/mtime+tv_nsec）未变时，跳过重写与 generation 前进；首次同值/耐久未确认（上次 -1）/外部改动/进程重启时执行契约同步（fsync 现有记录+目录，不重写、不删 fsync），失败仍返回 -1 保留草稿与重试。 |
| `mod/deployment/mfw/hotspot.sh` | CLOSE-WORKERS：`stop_service()` 增加"配置路径身份"兜底回收（exe + 本运行目录私有 hostapd.conf/dhcpd.conf 的 cmdline 匹配），owner 记录丢失/被改后自己的守护进程不再残留；`foreign_radio_user()` 同规则补认（我们的私有配置守护不计为外来占用）。外来实例（其他配置路径）永不匹配、永不扩大清理。 |
| `outputs/deployment/wireless-telnet/BUSYBOX-LICENSE.txt` | CLOSE-LICENSE：源码取得指引由错误的 `1:1.37.0-6+b9` 修正为源码版本 `1:1.37.0-6`（附 `.dsc` 200/`+b9` 404 的既有公开核验证据引用）；二进制保留 `+b9` 身份（SHA-256/版本行未动）。 |

本轮**未改动**：`remote.c`（前一轮已修的准入/长度/断连逻辑保持，本轮只补测量）、`telnet_toggle.sh`（同前）、SmartFilter 三源码、菜单 LUT 面板/请求快照/草稿逻辑、`lut_tune_cache.h`。

## 2. 构建与退出码

| 命令 | 退出码 | 证据 |
| --- | --- | --- |
| `tools/build_metol.py --menu-only`（两次：接线改动后、lut_tune_store 改动后） | 0 / 0 | `outputs/review/2026-11-13-performance-25hz/menu-build.log` |
| `nxui.c` 单文件 `-Wall -Wextra` 语法编译（含 freetype/include 路径） | 0 警告 | 会话记录（WSL 交叉 gcc -fsyntax-only） |
| `tools/build_remote.py --backend-only` | 0 | `remote-build.log`；`nx-remote-controller.arm` `2d65c6a9…` |
| ARM 收藏夹具重编译（3 次尝试） | 失败 | 见 §3.1"复用决定" |
| SAVE-SAME 夹具编译（qemu-arm + 原厂 libc-2.13，`-D_DEFAULT_SOURCE -D_LARGEFILE64_SOURCE`，规避交叉 glibc 的 ISOC23 sscanf 重定向） | 0 | `save-same/save_same_test.arm` `820bf89a…` |
| `fd_exec_failure_test.c` 原生编译（WSL x86，含 `runtime/close_inherited_fds.h`） | 0 | 会话记录 |
| `sh -n mod/deployment/mfw/hotspot.sh` | 0 | 会话记录 |
| `tools/build_mfw_services.py` | 0 | `kit-build.log`：59 文件 |
| 安装器收藏测试（真实安装器 + qemu C store） | 0 | `favorites/installer-direct-favorites.log` |

## 3. 定向测试结果（全部真实执行）

### 3.1 CLOSE-FAVORITES — 22/22 PASS（`favorites/installer-direct-favorites.log`）

真实安装器代码对受控目录（NTFS 临时目录）+ 真实生产 C store（qemu-arm + 原厂 libc-2.13，`fixes/favorites_store_test.arm` `f4d10c10…`，为前一轮在 C reader 修复后编译、内嵌头与当前一致）：

- **不同集合 A(5)→B(27)→C(9)→D(1) 交替写**：每次回读一致；每次只有较旧槽被重写且该槽 generation = 前次 max+1、另一槽保持（槽轮换序列含 0/1 交替）；状态目录无 `.nx-update-*` 残留（无临时文件路径）。
- **os.replace/rename 禁用护栏**：全程 monkeypatch `os.replace`/`os.rename` 为抛错，写与回滚均不触发——证明收藏路径不再使用 rename-over。
- 清空（显式空列表合法）→ 重新添加；`set_favorites` 客户端入口增/删（stub volume 身份）。
- 同值重试：同一内容连写两次，generation 前进 2、内容稳定、双槽均有效。
- 最新槽损坏（半条记录）回退另一槽；下一次写入恢复。
- 撕裂记录（尾部截断）拒绝读取。
- 删除回滚 roundtrip：`favorites_snapshot`→改写→`favorites_restore`，双槽字节级还原，仍在禁 rename 护栏下。
- **跨语言且不同集合**：Python 写 5 键→C 读 5 键；C 写 27→Python 读 27；Python 写 9 键→C 读 9；再 C 写 27→Python 读 27。
- **复用决定（如实记录）**：夹具重编译三次失败（交叉 glibc 头将 sscanf 重定向到原厂 libc-2.13 不存在的 `__isoc23_sscanf`；原始 stderr 逐次保留在会话记录），按任务书"已通过且身份一致的证据复用"改用 `fixes/` 夹具；其内嵌 `nx_favorites_store.h` 身份由前轮 result.json 与当前源码哈希一致背书。

### 3.2 CLOSE-UPLOAD — 慢 I/O 两阶段测量完成（`upload-slow/`）

真实重编 ARM daemon（qemu-user）+ 私有 mount/pid/net/ipc namespace；媒体根位于 WSL `/mnt/d` drvfs（真实慢速后端，作相机卡的可控替身；绝对耗时不是相机数字）。20MiB 现有照片 + 1MiB JPEG 以 64KiB/s 上传：

- 上传完整 200，`bytes=1048580` 回读一致；结束后第二个小上传 200（槽已释放）。
- **接收阶段**（客户端仍在发送，≈16s）：并发 `/health` 采样 93 次，**p50=14ms、p95=16ms、max=25ms**——Mongoose 交织接收，无阻塞。
- **写入阶段**（body 发完到响应开始，preserve 20MiB 备份 + replace 1MiB）：**632ms 事件循环阻塞**——期间唯一的 health 采样延迟 635ms（`health-timeline-analysis.txt`）。相机卡（更慢的 FAT）上该阶段会更长。
- 内存上限：qemu-user 环境 VmHWM 2304 kB（两次上传前后不变；qemu 记账非相机数字）；接收上限由 `recv_mbuf_limit=24MiB+512KiB` 与前轮证据（超大请求头部阶段 413、连接 recv_mbuf 峰值 238 字节）共同背书，本轮未改动该路径。
- **结论与 remaining**：接收两阶段中"写卡阶段长阻塞"为实测事实；异步分段写（在 MG_EV_POLL 间分片执行 preserve/replace，连接关闭时清理临时文件）已在任务书内列为候选设计，本轮未实施——涉及刚通过 13/13 边界回归的上传写路径，剩余预算不足以完成"改动+完整重测"，如实留档。

### 3.3 CLOSE-TELNET — 未完成（如实记录）

前一轮已交付：同源本机协议集成（Debian BusyBox 1.37.0 amd64 + 未改的 `telnet_login.c` 原生构建 + 真实生产脚本）登录/有限命令/断连/shell 与 sleep 消失/外来服务保留/启动时间令牌复用；ARM qemu-user 原厂 shell 试跑失败（`/proc/*/exe` 枚举不可用）后按任务书不再重复该路径。本轮剩余为**完整私有 ARM VM 生命周期**验证；本轮预算内未再启动 VM 会话（优先完成了可离线短测与交付文档，见 §5 未完成清单），`telnet_toggle.sh` 本轮未改动，前轮有效证据继续适用，ARM VM 生命周期仍列 remaining。

### 3.4 CLOSE-MENU — 接线完成、动态探针未完成（如实记录）

- 源码接线完成并重编通过（见 §1）；`hotspot.sh status` 的死 AP/地址丢失写回为前轮已验收行为，本轮接线后页面进入/开关完成/查询失败三条路径都会按最新 state 重建第 7 页并保留状态文本、夹取焦点；密码仅屏显与日志脱敏为前轮已落地逻辑，本轮未改动。
- **动态验证（键盘/转盘/触控三输入、失败文案可见、MENU 退出、等待期间响应）未运行**：上一轮同类 VM 探针在私有 ARM VM 内因命令传输层 rc=64 故障未完成，本轮预算不足以同时容纳 VM 排障与交付文档；列为 remaining（最小步骤：先复现一条 `printf|base64` 传输命令定位 rc=64，再跑既有 `probe_wireless_menu_vm.py` 类探针）。

### 3.5 CLOSE-WORKERS — 18/18 PASS（`workers/workers_matrix.sh`、`workers/cases/`）

真实生产 `hotspot.sh` + 既有 multicall 替身（真实进程/真实 PID 文件/exe 身份），私有 namespace：

- udhcpd 死亡（hostapd 存活）：status rc=1、`failed:worker-dead:udhcpd`；start 恢复 state=on、新 udhcpd 存活。
- owner 记录被改为死 PID：status 拒绝并写回 `failed:worker-dead:hostapd`，真实 hostapd 未被误伤；**stop 按配置路径身份兜底回收**（本轮生产修复，回收后无残留）。
- owner 记录指向存活外来进程：status 拒绝且不杀外来进程、不杀真实 hostapd；stop 后外来进程仍存活（无 kill-by-name、无 PID 复用误杀）。
- 附带确认：外来守护先于 start 存在时，start 拒绝（`radio already in use`，正确行为）。

### 3.6 CLOSE-FD — exec 失败探针交付（`fd/fd_exec_failure_test`）

`launch_settings()` 同款子进程路径：fork → stdin 重定向 → `nx_close_inherited_fds()` → **execve 失败**（不存在的 launcher）：子进程 `_exit(127)`、无 exec；父进程 fd 3 与 fd 5000 存活且可读。原生运行 rc=0。低/高 FD、父进程保留、枚举失败路径复用前轮证据（源码未变）。

### 3.7 CLOSE-DIR — 未取得新结果（如实记录）

dirlist 的 realpath 计数（6004→21）与链接边界复用前轮证据（源码未变）。input 上下文同期延迟：与上一轮相同，实验室无原厂相机程序上下文时 `input/inject` 等待 15s 无响应，本轮未再消耗时间重试，逐项记未验证；不阻塞其他项。

### 3.8 SAVE-SAME — 17/17 PASS（`save-same/save_same_test.arm`、`save-same-run.log`）

真实 `lut_tune_store.h`（qemu-arm + 原厂 libc-2.13；write/fsync 经插桩计数，计数在每次保存前清零）：

- 首次保存：write=1、fsync=2（文件+目录）、rc=1。
- 同值立即再存（Fn 后 OK、重复应用）：**write=0、fsync=0**、generation 不变、rc=1（可证明重复）。
- 外部同内容重写（mtime 变化，纳秒粒度身份）：write=0、**fsync=2**（完成契约同步）、generation 不变、rc=1。
- fsync 注入失败：rc=-1、write=0、generation 不变（耐久未确认如实上报）；解除后重试 fsync=2、rc=1。
- 不同内容/仅强度变化：完整写、generation 前进。
- 最新槽撕裂：回退真实写，另一槽内容存活、读取正确。
- 外部更高 generation 同内容更新：write=0、fsync=2、外部 generation 保留。
- 调用方语义核对：`lut_draft_persist_one` 以 rc==1 丢弃草稿、rc==-1 保留 `durability_pending` 草稿——免写与重同步路径都保持该契约；不在卡上新增"已确认"标记。

### 3.9 BOOT-KEY — 实现完成、动态测量未完成（如实记录）

实现见 §1（启动未就绪 1s 快速重试 ×24 + helper 内 6s 有界窗口等待；真实错误/长期缺失保持 ≤60s 退避）。就绪成本分析：启动期最多 24 次 helper 尝试，每次为一个轻量进程（X 树轮询 250ms 间隔），任一尝试在窗口出现后 ≤1s+ε 内可达 READY；就绪到快捷键可用间隔的目标值需要 X11+helper 动态会话实测，本轮未运行（heavy probe 需私有 qemu 会话，预算不足），记 remaining/待实机。

### 3.10 UI-25 / LOOK-CACHE / IDLE-CHECK / HOOK-STATUS — 未开始（如实记录）

预算按任务书顺序先给了正确性收尾（§3.1–3.8），性能剩余项未动工：UI-25 三档对照未跑（不存在任何估算数据，`ui_rate_comparison` 明确写未测）；LOOK-CACHE 未改代码；IDLE-CHECK 未改；HOOK-STATUS 保持现状（其测量结论：离线无原厂自然调用频率来源，不凭合成频率启用热路径改动——按任务书该项允许以测量结论收尾，本轮的结论是"不可测、不改"）。

### 3.11 CLOSE-PACKAGE — kit 重建完成，完整首装/升级候选未生成

- `nx300m-mfw-services.zip` 重建：59 文件、CRC 全过、SHA-256 `66982f20…`；包内 `hotspot.sh` 为本轮修复版（含配置路径兜底回收）、`licenses/busybox-static-GPL-2.0.txt` 为修正后的源码版本指引、菜单/后端为本轮重编产物（`kit-verify.json`）。
- **完整首装/升级候选未生成**：发布链门槛 `panel-control-prep/report.json` 的 source_sha256 与当前产物一致——本轮改了菜单/后端，重跑该多 VM 面板回归套件（含 4 轮冷/热启动计时）超出本轮预算，且本仓库无独立可重跑入口；与上一轮结论相同，如实保留。未写卡、未发布。

## 4. 边界自检

全程离线 ✓；未连接实体相机/手机 ✓；未读写真实卡（测试媒体根为 NTFS/drvfs 临时目录，随脚本清理）✓；未安装到设备、未刷机、未推送 GitHub、未发布 ✓；namespace/VM 全部随退出回收，未在共享 WSL 挂载写 `/var/run`（namespace 内私有 tmpfs）✓；未重启共享 Host、未改全局工具/权限配置 ✓；Python 仅 `D:/conda/envs/nx300m-mod/python.exe -X utf8` ✓；Shell/绘制 VM 未启动（无争用）✓；凭据不进日志（本轮无凭据操作）✓；SmartFilter Session/相册换 LUT 修正源码与产物逐字节未动 ✓；`lut_ram_blob_build.h` 未动 ✓。

## 5. 未完成清单（原因与最小继续步骤）

1. **CLOSE-TELNET 完整 ARM VM 生命周期**：预算给了可短测项与交付文档。最小步骤：私有 ARM VM 内跑真实 `telnet_toggle.sh` enable→登录→命令→disable→会话树消失→外来实例保留，产出进程树前后快照。
2. **CLOSE-MENU 动态三输入/失败页/MENU 退出**：VM 命令传输 rc=64 未排障。最小步骤：先在 VM 内复现一条 `printf|base64` 命令定位 rc=64，再跑 `probe_wireless_menu_vm.py`。
3. **CLOSE-UPLOAD 异步分段写**：实测 632ms 写阶段阻塞后按预算未实施；设计已列 §3.2。实施后需重跑 13/13 HTTP 边界 + 慢 I/O 对照。
4. **CLOSE-DIR input 同期延迟**：需原厂 input 上下文（实验室限制，逐项未验证）。
5. **BOOT-KEY 就绪间隔动态测量**：需 X11 helper 会话（heavy probe）。
6. **UI-25 三档对照、LOOK-CACHE、IDLE-CHECK、HOOK-STATUS**：未动工，无估算数据。
7. **完整首装/升级候选**：需重跑 panel-control-prep 多 VM 回归套件刷新发布链门槛。
8. **实机待验**（只列步骤，不凭 VM 关闭）：相册 Custom 换款→返回取景卡死是否消失；25Hz/20Hz/15Hz 实机手感与功耗；慢卡上收藏/预设写入表现。

## 6. 交付物索引

- 本文件：`docs/AGENT_WORKLOG_2026-11-13-performance-25hz.md`
- 机器可读结果：`outputs/review/2026-11-13-performance-25hz/result.json`
- 收藏：`favorites/check_installer_favorites_direct.py`、`favorites/installer-direct-favorites.log`
- 上传慢 I/O：`upload-slow/remote-slow.sh`、`remote-slow-daemon.log`、`health-timeline.log`、`health-timeline-analysis.txt`、`upload-slow-response.txt`
- worker 矩阵：`workers/workers_matrix.sh`、`workers/report.txt`、`workers/cases/`
- FD exec 失败：`fd/fd_exec_failure_test.c`（原生构建与运行输出在会话记录）
- SAVE-SAME：`save-same/save_same_test.c`、`save_same_test.arm`、`save-same-run.log`
- 构建/kit：`menu-build.log`、`remote-build.log`、`kit-build.log`、`kit-verify.json`


## 7. 第 2 轮（01:46–03:46，UTC+8）：父线程指缺陷修复 + 动态验收推进

父线程读取第 1 轮交付后指出两个具体缺陷并要求完成 VM 动态验收；本轮在其后约 2 小时内执行，状态以本节与 `result.json` 为准（首轮快照：`outputs/review/2026-11-13-performance-25hz/result.round1-snapshot.json`）。

### 7.1 两个具体缺陷（均修复并带反例与灵敏度验证）

1. **lut_tune_store.h 同值同步分支 FD 泄漏**：`fsync(fd)` 失败短路导致 fd 不关闭。拆开同步与关闭（打开成功必关闭），返回保持 -1 可重试，未改持久化协议。夹具新增 `/proc/self/fd` 计数断言：连续 5 次 fsync 注入失败前后 fd 数不变（18/18 PASS）；用旧代码回放验证该断言确实 FAIL（反例有效）。菜单已重编。
2. **hotspot.sh stop 的 KILL 无身份复查**：owner stop 与配置路径兜底回收的 KILL 此前只看 `/proc/PID` 存在。修复：KILL 前分别以 `pid_is_exe(pid,exe)`（owner 路径）与 `config_cmdline_pid(exe,conf)==pid`（兜底路径）复核身份，kill 范围未扩大。确定性反例：测试替身新增 `STUB_EXEC_ON_TERM`（SIGTERM 时同 PID 换镜像为 /usr/bin/sleep，替代无法离线编排的"退出后 PID 复用"竞态）；旧代码会误杀同槽新进程，新代码不杀（25/25 PASS），且 `stop-hang-control` 反例确认身份未变的挂死守护仍会被 KILL 兜底回收。灵敏度：旧代码回放 FAIL。期间发现并修复了测试基础设施问题：stub 重编译曾把硬链接替身改成符号链接导致全部 exe 身份检查失败，已恢复硬链接。

### 7.2 CLOSE-UPLOAD 异步分段写（实现 + 完整回归）

实测写阶段 632ms 事件循环阻塞后，本次实现：请求头从接收缓冲移除（body 位于 offset 0，免拷贝），preserve/replace 以 512KiB 分片在 MG_EV_POLL 间推进，编辑锁全程持有、60s 截止、断连中止（临时文件清理、原片不动、槽释放）、busy 503 语义不变；成功响应的 bytes 在状态重置前捕获（首版报 0 的缺陷已修）。回归：

- 13/13 HTTP 边界（fixes 轮全部用例）+ 分段 1MiB/24MiB 精确上传全部 200 且字节数正确；
- 慢 I/O 对照：接收阶段 health p50=14ms/p95=17ms 不变；**写阶段由单次 632ms 阻塞变为 n=11 p50=29ms/p95=38ms/max=38ms**；上传总时长 16.6s→17.5s（分片开销如实记录）；
- 写阶段中断开：`write stage=aborted reason=disconnect`，原片 20971524 字节完好，下一个上传 200。

### 7.3 VM 动态验收（A：部分通过；B：本轮环境故障）

- **playback-custom 回归重跑通过**（`PLAYBACK_CUSTOM_VM_PASS`，guest exit 0）：用本轮重编菜单（含 lut_tune_store 同值免写路径）复验 OK/Fn/MENU 六路 + 焦点返回——任务书要求的"受影响路径补跑"完成。
- **BOOT-KEY 启动场景动态验证通过**（r5）：窗口迟到场景下 reason=3 未就绪失败以 retry_ms=1000 快速重试（streak 1→3+，不升级），helper 在有界等待内捕获迟到窗口（`NX_CUSTOM_BOOT window_wait_ms=2785`），create-window 后 state=ready 断言通过。期间发现并修复设计缺陷：helper 6s 窗口等待超过 supervisor 2s init 截止，会被 reason=6 超时误杀并升级（8s→16s→32s→60s）——等待上限降为 1.5s（<2s），r5 即为修复后行为。
- **无线页动态验收未完成（环境故障，非未开始）**：r5→r9 依次修复 5 个环境问题（stock-parent 身份/存活标记、状态端点 URI `/api/v1/camera/info`、替身执行位（overlay cpio 0644）、hotspot.sh PATH 硬化→替身 bind-mount 到 /sbin/insmod 与 /usr/bin/ip——生产脚本自身行为正确）；r8/r9 出现传输层 rc=64（所有 exec 包括探针本身均 64、无探针输出，原始串口保留于 `wireless-page-r8|serial.log`），同一小时既有 telnet VM 探针亦传输超时——（第 3 轮勘误：该结论错误。rc64 与超时的根因是本轮自有夹具——基础镜像 boot 时以 `ln -sf /rescue/busybox` 生成 `/sbin/insmod`、`/usr/bin/ip`，`mount --bind` 跟随符号链接把替身绑到了 busybox 本体，所有 guest 命令都执行 stub 退出 64；详见本节第 6 条与探针修复。历史失败原文保留。）全部 VM 已停止（qmp 复核 + qemu 进程数 0）。
- **CLOSE-TELNET**：复用既有 `probe_telnet_vm.py`（其 11-11 轮 32/32 为旧脚本时代证据）对当前脚本重跑，本时段传输超时（原始日志保留）；ARM 命令替身与真实 busybox 身份已重建并在诊断会话内验证可执行（`CALL insmod ...` 日志）。列为传输恢复后的最小重跑项。

### 7.4 E：门槛入口定位与 kit 刷新

- kit 重建（59 文件 CRC 过，SHA-256 `99869a72…`）：含 hotspot 身份复查修复、修正后的 BusyBox 源码版本说明、本轮 nxui/remote 二进制；包内核验脚本化（`kit-verify.json`）。
- 首装/升级候选门槛的既有回归入口**已定位**：`outputs/performance/panel-control-prep/verify_ui_boot.py` → `verify_finish.py`（聚合启动计时、813 次转盘的 coalesce 指标与精确终态断言、native 检查、overlay 与当前仓库文件的字节相等校验后写入 source_sha256）。本轮未重跑：整套多 VM 回归所需时长远超剩余预算且本时段传输已退化；未改 passed/hash 绕过。

### 7.5 第 2 轮未做项（区分状态）

- UI-25 三档对照、LOOK-CACHE、IDLE-CHECK：**未开始**（预算按父线程优先级给了缺陷修复、上传异步化与 VM 动态验收）。
- HOOK-STATUS：未取得自然频率测量（需原厂上下文），保持现状，不声称测量通过。
- 实机待验项同第 1 轮清单。

### 7.6 第 2 轮产物索引

- 缺陷 1：`save-same/save_same_test.c|arm|save-same-run.log`（18/18 + FD 计数）
- 缺陷 2：`workers/workers_matrix.sh|report.txt|cases/`（25/25，含 exec 身份反例与 KILL 兜底对照）
- 上传异步：`upload-async/remote-short.sh`（13/13+24MiB）、`upload-async/remote-disconnect.sh`、`upload-slow/health-timeline-analysis.txt`（async_run 对照行）
- VM：`menu-vm/playback-custom/`（green）、`menu-vm/wireless-page-r5..r9/`（含 raw serial）、`tools/probe_wireless_page_vm.py`（新探针，全部环境修复已内置）
- kit：`kit-build.log`、`kit-verify.json`


## 8. 第 3 轮必要收尾（03:48 起，UTC+8，以本节末尾的实际完成时间为准）：上传写阶段四处缺陷修复与 rc64 根因

父线程审查 remote.c 指出的四处缺陷全部修复，先编译后注入验证：

1. **preserve 的 EOF 与错误混淆**（严重：错误落到完成路径会发布截断 backup 并随后替换原片）：read 非 EINTR 错误与 write<=0（含部分写后失败）现在置 failed 标志并保留 errno，立即 500+清理，EOF 单独判定。注入 read EIO：500、原片 20971524 字节不变、`.BIG_0001.JPG.nxoriginal` 未发布、槽释放（后续上传 200）。
2. **preserve/replace 的 `fsync(fd)||close(fd)` 短路**：fsync 失败不 close 泄漏 FD。拆分并保留 errno（sync_err/close_err 分别捕获）。注入 fsync EIO：500、原片不变，**失败前后 daemon fd 计数 10=10（无泄漏）**。
3. **mongoose mbuf 所有权（读真实源码确认）**：`mg_http_handler2` 在回调返回后无条件 `mbuf_remove(io, hm->message.len)`（mongoose.c:4747）。此前依赖 `mbuf_remove` 的 `n<=mb->len` 钳制才未被破坏——流水线/尾随请求会使条件成立并移走 body，不可靠。改为**显式接管**：处理函数内移除请求头后直接窃取 recv 分配（body 已在 offset 0），连接获得全新空 mbuf，stage reset 时释放；零拷贝、24MiB 单缓冲边界保持。stage-2 从自有缓冲读取。
4. **rename 后目录 fsync 失败的回复语义**：不再回复"original preserved"（此时替换已提交）。replace 阶段 dir fsync 失败 → 200 + `"durability":"unconfirmed"`；preserve 阶段 dir fsync 失败（backup 已提交）→ 记 unconfirmed 并继续，最终响应携带同一标记。

故障注入套件：`upload-async/fault-inject.sh` + ARM `LD_PRELOAD` 替身（read EIO / write 部分写+ENOSPC / fsync EIO，flag 文件触发）：四场景全部符合预期，最终二进制 `5a5c4688…` 上完整 13/13 边界回归与 24MiB 精确上传重跑通过。

**rc64 根因确认（夹具问题，非共享传输退化）**：基础镜像 boot 时以 `ln -sf /rescue/busybox` 生成 `/sbin/insmod`、`/usr/bin/ip`；`mount --bind` 跟随符号链接，实际把替身绑定到了 **busybox 本体**——所有 guest 命令（含 /bin/sh、base64）都执行 stub 而退出 64，与 r8/r9 现象完全一致。夹具已改为解除符号链接后复制普通文件（仅测试侧，不改生产 PATH 处理）。时间窗口内未再启动 VM 复跑；探针修复已就位，恢复后即可直接重跑。

边界：全程离线；SmartFilter/相册 LUT 修复源码与 `lut_ram_blob_build.h` 未动；未连接实体、未写卡、未推送发布。


## 9. 第 3 轮续（03:55–04:05，UTC+8，实际完成时间）：验收硬化与 kit 刷新

1. **故障注入硬化（23/23，脚本失败即非零）**：`fault-inject.sh` 改为硬断言（HTTP 状态、原片 `cmp` 字节级一致、backup 未发布、槽释放、fd 基线）+ 每用例分阶段故障（preserve 首块/中途 read EIO、正数短写后 ENOSPC、preserve 侧与 replace 侧 fsync EIO、恢复上传）。原始汇总落盘 `fault-summary-raw.txt`。过程中发现并修复替身自身的 fd 复用计数缺陷（open/close 重置计数）。仅隔离命名空间执行。
2. **body 所有权同连接短测**：单条 keep-alive 连接先 2MiB 上传再小上传：`200|200`，两文件以正确尺寸与 JPEG 魔数替换——流水线交互下无截断、无提前成功、无双重释放。
3. **kit 刷新**：以最终 remote `5a5c4688` 重建（SHA-256 `8739650d`，59 文件 CRC 过，包内 nx-remote-controller.arm 与 nxui.arm 与当前 outputs **字节相等**，hotspot 身份修复在包）。完整首装/升级候选门槛仍未重跑，明确不生成、不绕过。
4. **报告勘误**：result 的 round-3 remote.c hash 更正为 `43096424…`；worklog 第 3 轮标题改为实际时间；"共享 VM 基础设施退化"结论删除并加勘误（历史原文保留），rc64 根因改为自有夹具 bind-mount 覆盖 busybox 符号链接。
5. **VM 复跑（r10）**：修复后的夹具确认 stub 在 VM 内真实执行（两次 `CALL insmod` 日志），探针推进到 link-up；暴露 ip 复制解析到探针自身路径的 no-op 缺陷，已改为复制到 `/usr/bin/ip`。窗口内未完成整页动态流程（04:12 截止）；探针就绪，下一次会话一条命令即可重跑。

余项（继续明列）：UI-25 三档对照、LOOK-CACHE、IDLE-CHECK 未开始；HOOK-STATUS 无频率测量不改；Telnet/wireless VM 动态流程待下窗口一条命令重跑；实机项待上机。

## 10. 第 4 轮（接续，UTC+8）：同连接 HTTP 顺序修正、无线页/Telnet VM 收口、LOOK-CACHE 与 IDLE-CHECK 落地、首装/升级候选重跑

本节只记录本轮实际执行并可复核的证据；第 1–3 轮结论不改写，旧报告保持原样。

### 10.1 上传异步两阶段故障覆盖（父线程复核项）

- 新增 `outputs/review/2026-11-13-performance-25hz/upload-async/failer_stage.c` + `fault-inject-stage.sh`：替身按 **操作 + fd 路径** 选靶（`.copy-` = preserve 临时件、`.upload-` = replace 临时件、`.JPG` = 原件），不再用裸调用序计数，因此每个注入点确定落在指定阶段。
- 覆盖：preserve/replace 各 3 次 fsync 注入、read EIO 首块/中段、正数短写 + ENOSPC（两阶段各一）、恢复上传。
- 结果：**pass=82 fail=0**（`fault-summary-raw.txt`、`fault-daemon.log`）。daemon 日志出现 `stage=preserve-sync` 与 `stage=replace-sync` 各 3 次，`injected-fsync-targets: 6`，无跨阶段误命中；每次断言 HTTP 状态、原件字节一致、既有 backup 字节一致、无临时残留、fd 基线 5、槽位释放。

### 10.2 同连接流水线与 body 所有权（含父线程指出的顺序问题）

- 首次运行暴露真实缺陷：**upload body 之后同一 TCP 读到达的字节被静默丢弃**（第二个响应 -1）。修复为 `upload_tail` 捕获 + `MG_EV_SEND` 释放槽位后重放（上限 1 MiB，超限 413、忙 409）。
- 父线程指出 `pipeline-late` 原先把 health 响应排在 upload 响应之前，属 HTTP/1.1 乱序。**该判断成立**：原探针把"health 先到"当作通过条件，是断言写错，daemon 也确实会让后到的请求越过未完成的上传响应。
- 本轮的修正：
  1. daemon 侧新增 `defer` 队列：连接仍持有上传槽位（`MG_F_USER_3`）且到达的不是"取得槽位的那个请求"（`MG_F_USER_6` 仅在路由该请求前有效）时，把整条请求按到达顺序入队，等上传响应完全发出（`MG_EV_SEND` 且 send_mbuf 空）后依次重放，重放时一旦又有上传取得槽位就停下，等下一次释放继续。
  2. 队列预算 256 KiB，超预算直接关闭连接（宁可断开也不让后到响应超车）；连接关闭时丢弃该连接的队列。
  3. 重放走 `mbuf_insert` 拷贝。此前的写法把队列节点内部指针交给 `recv_mbuf.buf`，mongoose 释放时 `free()` 非法指针——`MALLOC_CHECK_=3` 下 daemon 直接 abort，已复现并修掉。
  4. 探针断言改为：同连接**必须先 upload 响应、后 health 响应**（用各自唯一字段判别：`media updated` vs `nx300m-remote`）；另加**独立连接**健康检查，要求在上传写入进行中返回 200 且 ≤2000 ms。
- 结果（`pipeline-raw.txt`）：`pipelined 200,200`、`pipeline2 200,200`、`partial 200,200`、`late 200,200` 全部 0 失败；late 同连接顺序为 upload→health（dt≈0–2 ms），独立连接 health **4 ms** 返回；fd 全程 5→5（修复前 late 曾 5→6 且崩溃）；daemon 存活、无 double free。
- 修复后重跑故障套件仍 **82/0**，确认顺序修正未破坏两阶段写。

### 10.3 无线菜单页（CLOSE-MENU）与失败页截图

- `menu-vm/wireless-page-r24`：真实 `hotspot.sh` 在私有 ARM VM 内全流程通过——第 7 页异步进入查询 `elapsed_ms=480`、`HOTSPOT_STOP removed=udhcpd hostapd`、`HOTSPOT_START ... elapsed_ms=14796`（超过旧 12 s 上限，故后端超时提到 30 s）、失败路径 `hostapd missing`、恢复、`HOTSPOT_CREDENTIALS ssid=NX300M-mod password=***`。
- 断言含：口令真值存在且日志仅掩码（`password_len=16 secret_in_log=0 masked_in_log=1`）、`window_to_ready_ms=1100`（≤2000）、`boot_retries=25 fast=24 escalated=1`、状态序 `on/off/on/failed:no-hostapd/on`、焦点回到 `0x200001`。
- 追加 `tools/probe_wireless_failure_shot_vm.py` + `menu-vm/wireless-failure-r2`：失败页整帧采集（两帧 1,152,015 B，cksum 不同），`frame-compare.json` 22,257 像素差异、`frame-compare-structure.json` 685 行有差异、每行游程中位 3（文字笔画，非整块色块），并输出旋转 PNG 与并排图。

### 10.4 Telnet 生命周期（CLOSE-TELNET）

- `tools/probe_telnet_vm.py` 扩到 47 条断言，真实 `telnet_toggle.sh` + `hotspot.sh` 在 stock ash 下运行：启用/登录/命令/断开重连/禁用后端口关闭/开关往返；错误口令三次与锁定横幅；外来**同二进制**实例（另路径另端口）不被他方 disable 杀掉；owner 记录缺失 → 只报 `listener=foreign:` 不杀；start-time token 不匹配 → 不杀；二次 disable `removed=none` 且状态保持 off。
- 结果：`SUMMARY pass=47 fail=0` + `TELNET_CHAIN_DONE`（`telnet-vm/results.txt`、`chain.log`、`stubs-arm/`）。
- 探针自身收尾步骤的 tar 成员路径写错导致打包失败（链路本身已全绿），已修正命令行；本轮 `report.json` 依据同一次运行的真实 `results.txt` 汇编，并注明该步骤问题与未覆盖项（真实固件/射频/实体网卡、真机带机会话中断电）。

### 10.5 LOOK-CACHE

- 新增 `mod/metol/lut_table_cache.h`：4 槽 × 4380 B（≈17 KiB）原始表缓存，按 `dev/ino/size/mtime(sec+nsec)/ctime(sec+nsec)` 判定身份，每次查找重新 `stat` 校验；读取时 `fstat` 复核，防 stat 与 read 之间被换文件；缺失/截断/超长/符号链接一律返回错误，绝不复用旧字节；另提供 `nx_table_cache_invalidate()` 供同 tick 重写后显式失效。
- 接入两处读取点：`lut_tune_cache.h::nx_tune_cache()` 与 `nxui_lut_panel.h` 参考表装载；tune 目录本身按内容 hash 命名，无需额外失效钩子。
- 目标 ABI：相机 glibc 2.13 只导出 `__xstat64/__fxstat64`，头文件按 `__arm__` 走该入口（与 `nxui.c` 既有约定一致）。
- 验证：主机夹具 `look-cache/lut_table_cache_test.c` **18/18**（A→B→A 命中且第二次不再读、同尺寸跨 tick 重写失效、rename 换 inode 失效、同 tick 重写经显式失效生效、缺失/短/超长/符号链接报错、第 5 款淘汰 LRU、缓存不落盘）；ARM 目标 ABI 夹具 `lut_table_cache_arm_test.arm`（相机工具链 + stock libc + qemu-arm）**8/8**。

### 10.6 IDLE-CHECK

- `custom_key.h` / `menu_key.h`：资源检查从"每 50 ms 轮询各查 4/2 个路径"改为 1 s TTL 缓存 + **动作前强制复查**（启动 helper 前、状态查询时），按键与消息仍每轮消费；新增 `resource_checks` / `resource_probes` 计数器。
- 结果（`idle-check/idle-raw.txt`，5 s 窗口）：门控执行 120 次，元数据查询仅 **32** 次（custom）、119 次门控 / **16** 次（menu）；改动前等量为 480 / 238 次。空闲 CPU 4 ticks，fd 5→5。
- 行为：`DISABLED` 建立后 30 ms 内转 unavailable 且 helper 停止、menu 转 paused；移除后重试日志与探针计数恢复（无 X 环境下 helper 本身仍失败，故以"门控恢复 + 新尝试"为判据）；二进制去掉可执行位 → unavailable；重启后计数归零、门控与上限复现。**16/16**。

### 10.7 BOOT-KEY 数据（探针已内置测量）

- `custom_key.h` 追加 `NX_CUSTOM_BOOT window_loop_ms=… since_start_ms=…` 与 `NX_CUSTOM_BOOT_READY helper_ms`，把"等待窗口"与 fork/exec/XOpenDisplay 分开；无线页探针已采集 `bootkey-ready-interval.txt` 并断言 ≤2000 ms（实测 1100 ms）、持续缺席升级为 `retry_ms=2000`（25 次重试、24 次 1000 ms、1 次升级）。

### 10.8 首装/升级候选闸门（真实重跑，未绕过）

- 运行 `outputs/performance/panel-control-prep/verify_ui_boot.py` → **exit 0**，6 条 CHECK 全过（含 unknown/deleted remote 拒启、preview 无输入唤醒）；开机到 daemon 就绪中位数 before 6865 ms → current 5460 ms（本轮两次运行分别 −20.5%/−34.6%，随 VM 负载浮动）。
- 运行 `verify_finish.py` → **exit 0**，`passed=true`、`private_vms_stopped=true`；aggregate 仍以真实日志断言 `wheel_presses==813`、最终 `tune=[3,0,0,0,0,0,0]`、`field==1`、`full_batches>=20` 且绘制≥5、`final_latency<=150 ms`。
- 为让闸门在离线环境真实跑通（无 H: 卡挂载），做了两处**夹具层面**改动，均不触碰被测二进制与哈希断言：
  1. 卡根改由 `NX_VERIFY_CARD_ROOT` 提供（默认仍 `H:/nx300m-mod`），由 `scratch/build-card-root.py` 用已部署树 + 当前构建装配；装配时必须显式补 `bin/ram_asset_verify.arm`——ram-stage 只复制 manifest 内文件、随后要求 stage 内存在该 verifier，原部署 manifest 未列它，缺这一条会在 ready 前失败（该失败已在 VM 与主机 tmpfs 复现定位，非绕过校验）。
  2. 每轮 RAM 运行时在该轮结束后回收。guest `/tmp` tmpfs 约 8.9 MiB，两份 staged 运行时（各 ≈5.5 MiB）放不下，第 2 轮会报 `insufficient RAM filesystem space`；生产同样只保留一份 RAM 运行时，故回收属对齐真实行为，不修改任何校验。
- 旧证据保留：`ui-boot-round1|r2|r3|r4|r5`、`finish-round1`、`finish-preview-round1|r2|r3` 全部原地保留，未被覆盖。

### 10.9 本轮产物与源码身份

| 文件 | SHA-256 |
|---|---|
| `mod/metol/remote/remote.c` | `D21EF7F6F068025F444FCD595AD5AD288A6B08DD0C5CA2A8F76E89F4ACAB9A78` |
| `outputs/metol/remote/nx-remote-controller.arm` | `881EEB42CC64BB129560DF110DCD5BC020448647DF0134E753925806B643ECFA` |
| `mod/metol/remote/custom_key.h` | `9DF574EAAE09E9323F41937FF36792D437DFFD5CF85FFFC5C760597A322F2ACF` |
| `mod/metol/remote/menu_key.h` | `A912B70FCF87FAD7329CADE6DDAA5F26647CD1DCCBE449606B842E412A7548E6` |
| `mod/metol/lut_table_cache.h`（新） | `B1E8B8AEAA003BDFAAEFFD77A74271CD90BAAE2E6FBCFE04C5782717AC709D7E` |
| `mod/metol/lut_tune_cache.h` | `6F3F1984A5FE0E7E5A7BF02E9918D780314EE8D15FE22228EC127D40DED47660` |
| `mod/metol/nxui_lut_panel.h` | `2466BCB198CC1E26D0C2622C7C724A3230A0A8126DB566965D36ACC193C404EA` |
| `mod/metol/nxui.c` | `2D73D3B80D9366A42222BA5D7BEA9B44A2F016DB4983FB8219B80F35E6A41881` |
| `outputs/metol/nxui.arm` | `4252AB7709CA619B021413636C7BDF81A62082FD7E691B2CC099D8449E49647B` |
| `outputs/deployment/mfw-services/nx300m-mfw-services.zip` | `1B4BA448A3D48313F6CA618FCFB9FFDCD22903249092E70F918C06A1A86AA6C9` |

kit：59 文件，`testzip` 无错，包内 `nx-remote-controller.arm`（881EEB42…）与 `nxui.arm`（4252AB77…）与当前 `outputs` **逐字节相同**。

### 10.10 本轮仍未完成

- **UI-25 三档（15/20/25 Hz）同条件对照与调度统一**：未动工。第 2 节要求先把输入/绘制/子进程/参考图到期时间统一计算，再据此做 3 × 条件对照；这是会改动 `nxui.c` 主循环的实质改动，且牵连已通过的菜单 VM 证据，本轮未在剩余预算内安全完成，保持"未开始"，不声称通过。
- **HOOK-STATUS**：仍无离线可得的原厂自然调用频率，保持现状，不凭合成高频测试启用去重。
- 实机项（真机相册→Custom 换款→返回取景卡死、真机 Telnet/Wi-Fi、SD 读写、刷机）依旧未上机，步骤同前。

### 10.11 UI-25：三档同条件对照与统一调度（本轮完成）

实现（`mod/metol/nxui.c`）：

1. **单一时钟**：连续输入的绘制门限不再用独立的"tick + 66"与"tick + 33"两条判据，而是从**上一次绘制实际开始时刻**推进 `NX_UI_TARGET_MS`（默认 40 ms = 25 Hz），非突发帧上限 `NX_UI_FRAME_MS` 与之相同。select 的 `paint_due` 取两者较晚者，避免在 hold 有效时空转。
2. **离散输入优先**：单键、点击、Fn/OK/MENU 仍立即置 `lut_paint_hold=0` 并当帧绘制，不进入连续门限。
3. **数值绘制与参考图重算解耦**：参考图重算带自己的 `lut_reference_due`。单次重算超过 `NX_UI_REFERENCE_BUDGET_MS`（18 ms）时，本次突发内降到 `NX_UI_REFERENCE_SLOW_MS`（66 ms）档，屏幕保留上一张完整图，数值照常按绘制节奏更新；突发结束时置 `lut_reference_final`，立刻补绘最新效果（select 超时也纳入该到期时间，保证补绘真的发生）。
4. 不新增线程/进程池，全部沿用现有单 UI 线程 + dirty/revision 机制。

三档实测（`tools/probe_ui_25_tiers_vm.py`，同一台私有 ARM VM、同一 813 次滚轮序列、同一源码仅改 `NX_UI_TARGET_MS`）：

| 档位 | 已绘帧 | 帧间隔 p50/p95 (ms) | 末帧延迟 (ms) | 输入消费 | 突发中绘制 | 终值 | CPU 段内 (ms) | 唤醒 |
|---|---|---|---|---|---|---|---|---|
| 66 ms | 15 | 135 / 198 | 28 | 813/813 | 12/25 批 | 正确 | 516.3 | 164 |
| 50 ms | 15 | 104 / 194 | 21 | 813/813 | 12/25 批 | 正确 | 488.5 | 164 |
| 40 ms | 16 | **82** / 192 | 24 | 813/813 | 13/25 批 | 正确 | 491.6 | 111 |

- 三档都满足"输入全部消费 / 突发中定期显示中间状态 / 停止后及时显示最终值（≤150 ms）"，故按"最快且不牺牲正确性"选 **40 ms（25 Hz）**。
- 40 ms 档节奏最紧（p50 82 ms，比 66 ms 档快约 39%），CPU 与 50 ms 档基本持平（491.6 vs 488.5 ms），唤醒反而最少（111 vs 164）；没有用掉更多 CPU 换帧率。
- 交付的出厂二进制 `outputs/metol/nxui.arm`（`B2539802…`）另行单跑一档确认同样满足全部判据：p50 82 ms、末帧 21 ms、813 输入、13 次突发中绘制、终值 `field=1 tune=[3,0,0,0,0,0,0]`、CPU 495.4 ms、唤醒 135。证据 `ui-25/run-shipped/report.json`、`ui-25/run1/report.json`、各档 `coalesce-t{66,50,40}.log`。
- 说明：三档二进制由 `scratch/build-ui-25-tiers.sh` 用同一源码 + 显式 `-DNX_UI_TARGET_MS` 编出，仅供对照；出厂构建使用源码默认 40，二者不逐字节相同（链接参数不同），故对出厂二进制单独做了行为确认，而不是拿测量档的结果冒充出厂。
- 边界：软件光栅化的帧间隔与 CPU，不是物理 FPS/LCD/相机时序；实机 25 Hz 手感与功耗仍需上机。

两侧门槛在 UI-25 构建下重跑：`verify_ui_boot.py` exit 0（6/6 CHECK），`verify_finish.py` exit 0（aggregate 在新二进制上重新断言 813 次滚轮、终值 `[3,0,0,0,0,0,0]`、`field=1`、`full_batches>=20`、`final_latency<=150 ms`）。kit 重建为 `B630A34D…`，包内 `nxui.arm`（`B2539802…`）与 `nx-remote-controller.arm`（`881EEB42…`）与 outputs 逐字节相同。

### 10.12 最终二进制上的有效性重跑与 HOOK-STATUS 实测

UI-25 与上传顺序修正都改动了二进制，因此已关闭项在**最终构建**上重新跑过，而不是沿用改动前的证据：

- **无线菜单页**：`menu-vm/wireless-page-r25` → `WIRELESS_PAGE_VM_PASS`，guest exit 0，VM 已停止（`report.json`: `passed=true`、`guest_exit=0`）。
- **Telnet 生命周期**：`telnet-vm-r2`（最终 `nx-remote-controller.arm` + 当前脚本）→ `SUMMARY pass=47 fail=0` + `TELNET_CHAIN_DONE`。该次运行在**链路完成之后**的证据打包步骤（guest 内 gzip+base64 走串口）超时退出，故探针未自行写出 `report.json`；报告按本次真实 `results.txt` 汇编，并明写打包失败。随后把打包步骤改为"失败不致命、只记录原因"，另跑 `telnet-vm-r3` 验证该路径。
- **HOOK-STATUS**：按任务书允许的"交付测量结论"路径处理。用 live-loader 夹具实际记录的 `/var/run/nx300m-lut-status.json` 汇总：39 个场景、`updates=561`、`ipcs_completes=561`、`publishes=561`，每个场景都是"一次更新恰好一次发布"，夹具内**没有**无变化重复发布。结论：在该夹具上做业务状态去重没有收益；原厂相机自然调用频率离线仍不可得，故**不启用**去重，三通道硬件写表保持原样。证据 `hook-status/hook-status.json`（另列 300KB 串口证据未取回的原因）。

### 10.13 Telnet 证据包边界与收尾

- 最终二进制上 Telnet 链一共跑了三次（`telnet-vm-r2`、`telnet-vm-r3`、`telnet-vm-r4`），每次都是 `SUMMARY pass=47 fail=0` + `TELNET_CHAIN_DONE`，guest 侧断言、`results.txt`、`chain.log` 完整。
- **未取回的是 guest 证据打包**：把 `/perf/telnet`、`/var/run/nx300m-telnet`、`/var/run/nx300m-hotspot` 整棵树 gzip+base64 走串口，375 s、300 s、600 s 三次都在预算内搬不完，探针按设计记录 `packaging not bundled` 而不再把链路判失败。已把打包范围缩到小文本（`perf/telnet` + 两个 state 目录），但**该缩减路径未再跑一轮验证**，因此这条便利产物仍是未验证项，不影响 47 条断言与链路结论。
- 这也是本轮唯一一处"机制在位但产物未生成"的项，据实留在结果里。
