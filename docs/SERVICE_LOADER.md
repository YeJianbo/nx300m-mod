# SD 服务自动触发 SO

`runtime/service_loader.c` 生成独立的内置 `service-loader.so`，连接 app 原生桥、SD 资源与共享 RAM stage。构建器只生成独立产物；持久候选、rootfs 和模拟器的集成状态以对应构建报告为准。

## 当前部署入口（2026-10-24）

持久候选 helper 在启用模块的配置中为原厂 app 设置 `NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod`。新候选默认 stock；分组和停用入口见 [LAUNCH_PROFILES.md](LAUNCH_PROFILES.md)。完整 SD 包提供 `ram-start.sh` 和 `mfw-ram-stage.sh`，分别复用 MFW 的 `autoexec-services.sh` 与 `ram-stage.sh` 源码；本模块执行 `ram-start.sh --service` 并观察真实迁移/启动退出码。共享 stage 复制清单内所有文件、核对字节，确认 tmpfs 可写可执行，拒绝未结束的旧 SD 服务，再调用 RAM `start.sh`。现有 `service.sh` 仍是唯一监管者。无 SD 执行回退。

服务子进程的 root/state 为解析后的 `/tmp/nx300m-mfw/runtime`、`/tmp/nx300m-mfw/state`，常驻脚本、二进制、cwd、锁及日志均使用 RAM。原厂 app 的 ENV 与 LUT 源路径保持原语义；服务迁移不代表原厂 app、已映射模块或原厂 SD 引用已经迁移。loader 的 JSON `root` 仍表示源资源 root。原有 MFW 无参数 autoexec 继续后台启动独立诊断入口；固定 `--service` 模式同步返回完整服务启动结果，不运行 MFW 诊断。

`runtime/gallery_share.c` 不再沿用父 app 的 SD root 派生 Quick Share。它解析固定 RAM 目录，检查覆盖该目录的 tmpfs、ready 状态、唯一 remote 的 boot ID/starttime/executable inode，再仅给面板 child 设置 RAM root/state/cwd，并关闭继承描述符及 SD stdio。ASL 选区枚举及 SD 路径核验先检查 RAM `media-paused`，并持有同一 `media-io.lock` 的共享锁；暂停或锁不可得时保留图库，不执行这些读卡操作。服务身份核验失败保留原厂分享；父 app 的 root/LUT 环境不变，面板与 remote 共用暂停和任务状态。

本轮两个 ARM 模块构建通过，stderr 为空。`outputs/deployment/service-loader/ram-service-vm/report.json` 记录独立完整 ARM Linux VM 的服务模式验证：内部 loader launch helper 使用真实 `ram-start.sh --service` 同步启动，131 项清单逐字节核对；remote 和监管脚本的 executable/cwd/maps/fd 没有 SD 路径，重复入口保持 PID，手动停止不被自动协调撤销。图库 helper 的真实 RAM 身份、child 环境/FD 隔离、共享锁阻止媒体提前释放、暂停时不调用 ASL/stock fallback，以及服务重启后暂停保留均通过。fixture 明确为 source-inclusion，未伪装相机，也未运行原厂 app；下述完整 app/SD VM 记录均为迁移前的历史证据。

此服务生命周期 VM 使用 LUT 源 root 兼容修补前的候选快照；最终完整包包含随后重编的 remote/LUT engine，源 root 兼容专项结果另列，未因此重复整轮生命周期或完整原厂 app VM。

本轮新增：定时器检查内置及SD的 DISABLED，抑制新启动，状态JSON返回 `auto_start_disabled`；不终止已有服务、不卸载模块。原厂 ARM loader/libc/Ecore 的 timer-only 验证通过，覆盖停用时初始启动和资源变化后重启被抑制、移除标记后继续、五次失败上限及子进程回收。其余完整 app VM 记录属于此次策略修改之前，未重复执行。

此前实验室会话的 `start-exited-failure / attempts=5 / last_exit=1` 属于未完整部署：默认 `/mod` 目录由 root 持有且相机 UID 为 65534，`common.sh` 在创建 `/mod/.state` 时收到 `Permission denied`；同时该目录缺少 `bin/` 与 `web_root/`。历史 stderr 被重定向至 `/dev/null`，本结论来自相同 UID／namespace 下仅加载 `common.sh` 的复现，未执行服务启动。只修目录权限仍不能补齐缺失文件。

完整部署集成由 `tools/probe_installed_candidate.py` 在已有独立 ARM VM 验证：候选 wrapper → 未修改原厂 app → service-loader → 完整 SD 服务。探针记录真实 app／remote 的 `/proc/exe`、inode、启动时间、loader 退出码、重复启动与手动停止后的行为；结果以 `outputs/deployment/app-loader/installed-vm/probe-report.json` 为准。它不会通过禁用 loader 或替换成功标记使结果通过，也不改变主模拟器。VM 验证与物理机休眠恢复仍是不同的证据范围。

最终集成已通过：原厂 app PID 330 实际映射九模块，loader 的 root 正确、attempts 为 1、succeeded 为 true、child_pid／last_exit 为 0。自动启动和重复 start 保持同一 remote，手动停止不会被协调逻辑重启，手动重启及停止通过。由于首轮测试客户端 wget 截断二进制 JPEG，后续由 `probe_installed_resume.py` 在相同 app 生命周期内换入最终 remote，只补跑正确二进制上传、还原及未完成的停止／重启；首次失败和后续通过分别保存，没有把采集超时写成成功。

## 入口与行为

SO 没有 constructor。只有真正调用 `ecore_main_loop_begin` 时，才通过 `/proc/self/exe` 的 basename 识别 `di-camera-app` 或 `di-camera-app-nx300`，并注册真实 Ecore 的 0.5 秒 timer 和子进程退出事件观察。任意参数中出现相机名不会通过 guard。

资源 root 复用 `mod/metol/mod_paths.h`：实验默认 `/mod`，生产由加载片段设置 `NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod`。非法 root 不启用。每次 timer 只检查固定 root 和其中的 `ram-start.sh`；root 必须是目录，脚本必须是可读普通文件。没有扫描脚本、挂载 SD 或等待挂载的 shell 循环。

资源可以晚于 app 出现。发现后异步 fork，并执行固定 `/bin/sh <root>/ram-start.sh --service`。父进程不执行阻塞 wait、sleep 或启动脚本。资源身份由 root 与脚本的 `st_dev/st_ino` 组成，并用 `/proc/self/mountinfo` 中覆盖真实 root 的 mount ID 补充：先 `realpath`，再按目录边界取最长挂载路径前缀；支持 mountinfo 的四种转义。最长路径有不同 ID 的同路径堆叠挂载、格式异常、过长／截断行或读取失败时，保留 stat 机制和此前已知 ID；首次读到有效 ID 只补身份，不制造新的恢复轮次。

- 脚本退出 0 后，同一身份且没有新挂载通知时只触发一次；loader 不检查 remote 是否存活。
- 观察到资源消失、root/脚本 inode 或已知 mount ID 改变后，开始新一轮。
- 持续打开 mountinfo（不可打开时尝试 `/proc/mounts`），每 tick 用零超时 `poll(POLLPRI|POLLERR)` 检查 namespace 挂载事件；资源仍在时开始一次幂等启动协调。这能捕获两 tick 之间完成、且 mount ID 被复用的卸载重挂。通知覆盖整个 mount namespace，不能据此判断具体 SD 卡发生变化。
- 自动 child 固定携带 `NX300M_AUTO_START=1`。生产 `start.sh` 对已运行服务直接返回；同一次开机内，显式 `stop.sh` 写下的 `manual-stop` 记录会阻止自动协调拉起服务。手动 `start.sh` 清除记录；旧开机的记录不禁用新开机自动启动。具体脚本行为由 `common.sh/start.sh/stop.sh` 实现。
- 失败最多尝试五次，重试前分别退避 1、2、4、8 秒，再等待新身份或移除重插。定时器量化及子进程退出通知会增加最多约一轮 tick 的延迟。
- 同时只保留一个启动 child。资源变化时也先等它退出，不派生并发启动者。

服务的单实例、ready 检测、启动期限、手动启停继续由既有 `start.sh/common.sh/service.sh` 负责。loader 不监督 remote，也不向 remote 发送信号。若脚本一直不退出，loader 保留该 child 并停止派生新启动者；脚本自己的期限应负责结束启动操作。

## 子进程隔离与回收

父进程在 fork 前准备环境；子进程删除 `LD_PRELOAD`、`LD_AUDIT` 及继承的 `NX300M_AUTO_START`，明确设置选定的 `NX300M_MOD_ROOT` 和 `NX300M_AUTO_START=1`，将 fd 0/1/2 重定向到 `/dev/null`，并用原厂 `fcntl(F_SETFD,0)` 清除三者的 CLOEXEC。后者覆盖相机原先关闭 stdio、open 复用该 fd 且 dup2(fd,fd) 的边界。之后用 Linux `getdents64` 直接枚举 `/proc/self/fd` 并关闭所有 fd >=3，包含 mount watcher 及已高于后来调低的 RLIMIT_NOFILE 的描述符；失败则退出 126，不执行脚本。exec 失败退出 127。fork 后没有 malloc、stdio 或目录库分配。

原厂 Ecore 1.2 本身会 `waitpid(-1, ..., WNOHANG)` 回收全部 child。loader 使用 `ECORE_EXE_EVENT_DEL` 观察自己精确 PID 的退出状态，并把事件继续传给其他处理者；同时保留只针对自己 PID 的 `waitpid(..., WNOHANG)` 兜底。未安装 SIGCHLD handler，也不等待或回收其他 child。Ecore 已回收但事件尚未分派时，允许四个 tick 的通知窗口，之后记录状态不可得并按失败退避。

原厂二进制 `libecore.so.1.2.0` 的实际反汇编在 `outputs/deployment/service-loader/ecore-abi/stock-ecore.asm`：`0x135d0/0x13690` 调用 `waitpid(-1)`；退出事件前缀为 pid +0、exit_code +4、exe +8、exit_signal +12、exited/signalled 位 +16。代码只读取该前缀。目录中的上游 v1.8.0 头文件和 signal 源码仅作辅助对照，实际 ABI 依据是本地原厂二进制。

## 状态诊断

仅在注册、资源变化、fork 和退出时原子替换 `/var/run/nx300m-service-loader-status.json`，不逐 tick 写入。字段包括：

| 字段 | 含义 |
|---|---|
| `owner_pid/root` | 注册 timer 的真实进程及资源 root |
| `present/epoch` | 最近一次检测的资源状态及变更轮次 |
| `attempts/child_pid/succeeded` | 本轮尝试次数、正在等待的启动 child、是否成功一次 |
| `mount_id/mount_known` | 最近有效资源身份中的覆盖挂载 ID；读取异常时不清除旧值 |
| `mount_watch_active/mount_changes` | 持续 poll fd 是否打开、观察到的 namespace 事件批次数；批次不等于挂载操作次数 |
| `last_exit/last_signal/last_errno` | 最近一次启动退出、信号或错误；尚无退出时 `last_exit=-1` |
| `exit_source/reason` | Ecore 事件、waitpid 或状态不可得，以及最近状态变化原因 |

诊断写入失败不影响启动；未通过相机 guard 的 fixture 不创建状态文件。

## 构建与复现

```powershell
python -X utf8 tools\build_service_loader.py
python -X utf8 tools\probe_full_service_ram_vm.py
python -X utf8 tools\probe_service_loader.py
python -X utf8 tools\probe_service_loader_remount.py
```

GNU ARM 工具链位于 WSL `/opt/nx300m-toolchain/usr`。构建为 ARMv7 little-endian `ET_DYN`、softfloat，显式链接原厂 glibc 2.13/libdl/libgcc_s；用 GNU 头文件定义的 `struct stat64` 调原厂 `__xstat64(3,...)`，没有自造 stat 布局。所有导入的 glibc 版本需求均为 `GLIBC_2.4`，`-z defs` 检查未解析链接符号。当前独立 SO 为 69300 字节，动态依赖只有 `libc.so.6`、`libdl.so.2`、`libgcc_s.so.1`。它与下文历史主模拟器中的 68736 字节版本应按对应集成报告区分。

构建与 probe 的原厂依赖从已有 `work/stock-1.15/rootfs.tar` 精确提取到专属 staging，来源记录在 `stock-provenance.json`。未修改主 rootfs、已有 namespace、候选或其他构建目录。

## 已运行结果

`outputs/deployment/service-loader/verification-summary.json`、`guard-report.json`、`timer-report.json` 的最终退出码均为 0，stderr 为空。

| 验证 | 实际结果 |
|---|---|
| 生产 SO + 原厂 loader/Ecore + 诚实 guard fixture | 真实 guard 拒绝 fixture；有 start.sh 也不执行 |
| 明确调用内部 register_timer 的 source-inclusion fixture | 真实 Ecore 注册成功；fixture 保留实际名称，不伪装相机 |
| 无 root、start.sh 为目录、start.sh 不可读 | 均不执行 |
| 资源出现，脚本持续约 1 秒后退出 0 | 只执行一次，后续保持静默；child 已回收，无僵尸 |
| 同 inode 资源移除再移回 | 观察到移除后重新执行一次 |
| 脚本 inode 替换为失败版本 | 五次失败后停止；实际触发间隔 1.499、2.5、4.5、8.5 秒 |
| 耗尽后移除重插 | 重试预算重置，执行成功 |
| child 环境/FD/stdio | 八次脚本均记录 clean_preload、clean_fd、null_stdio |
| 分别预先关闭 fd0、fd1、fd2 | 三个独立 launch-only fixture 全部退出 0；exec 后 0/1/2 均指向 `/dev/null`，记录 clean_stdio |

最后状态记录 owner PID20、child PID0、succeeded=true、last_exit=0、exit_source=ecore-event；对应 `timer-evidence/status.json`。各脚本记录在 `timer-evidence/script-events`。fixture 的 `waitpid(-1,...,WNOHANG)` 仅用于确认结束时无未回收 child；生产 loader 不使用它。

最终完整 timer fixture 主动关闭 fd0，验证修复后的 `/dev/null` 复用路径；另有 `stdio0/1/2-report.json` 与各 `stdio*-evidence/count` 覆盖分别关闭三种 stdio。launch-only fixture 直接调用内部 launch 并阻塞等待自身 child，仅用于这个边界，不运行 Ecore 或声称相机自动入口通过。单独复现这三项可加 `--stdio-only`，保留已经通过的完整 timer/guard 报告。

每个用例运行于新 mount/net/IPC/PID namespace、私有 tmpfs chroot、uid/gid65534 下的 ARM 用户态 QEMU，只绑定 `/dev/null` 和 `/dev/urandom`。原厂 BusyBox shell 通过明确的 QEMU shebang/exec bridge 调度；没有修改全局 binfmt，没有替换 `/proc` 进程身份。

首次只靠 timer waitpid 的运行退出 1：脚本实际成功，但 Ecore 抢先回收，导致错误重试。原始报告和日志保存在 `initial-waitpid-only-*`；加入原厂退出事件后上述最终状态流通过。另一次 fixture 编译曾因现代头文件引入 `__isoc23_fscanf` 而退出 1，改用 fgets 和逐位解析后通过；生产 SO 不依赖该符号。

## 真实 ARM VM 自动链

完整原厂用户态在通用ARM内核中已实际通过app→生产加载器→原样SD服务链。运行记录`outputs/deployment/app-vm/probe-report.json`显示真实app PID325通过guard，加载器由Ecore事件收到启动退出0，attempts=1、child_pid=0、succeeded=true，自动remote为PID439。两进程的真实exe/inode与启动时间均核对通过，四个生产SD脚本逐字节未改；HTTP目录为17项LUT。

重复start保持PID439；手动stop后加载器未重新拉起服务。原样手动重启得到PID559，最终stop后app仍存活，完整检查退出0。该VM使用既有显式硬件替代模块和原厂偏好初始化，没有QEMU-user、身份替代或ready伪造。来源、库隔离修复及原始日志见[ARM_VM.md](ARM_VM.md)。

## 快速重挂载恢复

原厂 GPL 精确提取在 `outputs/deployment/service-loader/gpl/`：`proc_namespace.c:18–35` 的 `mounts_poll` 比较并更新打开文件记录的 `poll_event` 与 namespace 的 `event`，变化时返回 `POLLERR|POLLPRI`；`:273` 在 open 时建立基线，`:319–324` 给 mountinfo 配置该 poll。`namespace.c:557–572` 更新事件号并唤醒 poll，挂载／卸载路径使用该机制。因此生产 loader 保持 fd，只在错误后重开；错误、重开和首次可用均不自行制造 epoch。两个 proc 接口都不可用时回退 stat／可读 mount ID，无法保证发现完全落在两 tick 之间且身份不变的变化。

先前仅增加 mount ID 的候选在真实普通 bind 卸载／重挂中失败：7.286 ms 内 ID `241→241`，root 和脚本 dev/inode 均保持不变；记录 `remount-id-reuse-initial-report.json` 的退出码 1。这一反例保留，当前恢复依据是持续 fd 的 namespace 事件，不能只靠 mount ID。

当前 `remount-report.json` 退出码 0、`passed=true`，stderr 为空。测试使用真实 mountinfo、原厂 ARM loader/Ecore 和 QEMU source-inclusion fixture；host 在 fixture 两次观察之间执行真实挂载操作，没有占用释放 ID 来改变结果。最终实际结果：

| 操作／边界 | 结果 |
|---|---|
| 无关路径 bind 挂载，5.986 ms，root mount ID仍为241 | 一次协调；fixture 的 manual-stop 决策阻止服务重启 |
| 普通 umount + bind，8.045 ms，mount ID `241→241`，root/start.sh dev/inode不变 | epoch 2→3；没有 absent tick，也重新启动一次 |
| 启动 child 尚未退出时 lazy umount + bind，6.137 ms | epoch 3→4；先等旧 child 结束，再启动一次，无重叠 child |
| 首次缺 mountinfo 快照、恢复可读、临时 EIO、watch fd 被 fixture 关闭后重开 | stat 可用；首次读回和暂时失败不引起额外 epoch／启动 |
| 相邻目录前缀、最长目录边界、四种转义、坏行／截断／溢出、堆叠同路径 | 解析或拒绝结果符合预期；symlink root 与真实路径得到同 ID |
| child 自动环境 | fixture 要求 `NX300M_AUTO_START=1` 才能继续，已通过 |

最终 `mount_changes=3`、epoch=4、attempts=1、child_pid=0、succeeded=true；启动脚本调用4次，其中1次手动停止而跳过，实际启动3次。EIO、关闭 watcher 和 manual-stop 决策是明确的 fixture 注入，未声称生产脚本端到端或实机休眠；生产脚本的独立验证见 `manual-stop-report.json`。

只复验原状态机和退避可运行 `tools/probe_service_loader.py --timer-only`，它复用旧 guard 报告，不重跑其余 stdio 用例；`verification-summary.json` 的 `reused_reports` 明示复用项。本次该命令退出 0、stderr 为空，缺失／无效脚本不执行、同身份成功一次、观察到移除后恢复、五次失败耗尽以及重新插入重置预算均通过；五次失败间隔为 1.501、2.500、4.500、8.499 秒。

## 验证范围

timer fixture 直接调用内部函数，只证明原厂 loader/Ecore 下的触发状态机、shell 调度与回收；生产 SO 的 fixture 测试只证明 guard 拒绝。上述 probe 没有运行原厂相机 app、真实 remote 服务、hib2.sh、升级或休眠，没有连接机身。

整合后，主模拟器已加载最终68736字节SO及其余模块。独立记录 `camera-session.json` 显示真实原厂 app 的 guest PID67通过 guard，自动注册定时器；状态 owner_pid=67、current_camera=true、reason=timer-registered。实验室 `/mod` 没有 `start.sh`，因此 present=false、attempts=0、child_pid=0；没有执行脚本。ISO、快门、RGB、PW和LUT桥同时就绪，LCD录制照片回放744帧、失败0。此结果确认真实app入口及无资源行为，不替代SD服务端到端启动、传感器成像或快照恢复。

实际 SD 挂载、更新后快照、恢复以及机身服务自动启动尚未由上述 fixture 覆盖。没有挂载事件的休眠／快照恢复仍未证明；同 inode 原地改写脚本也不会单独重置成功记录。后续实机最小检查是在获准的正常开机后读取诊断，确认 owner PID 对应真实 app、缺卡 `present=false/attempts=0`，插卡后 `succeeded=true/child_pid=0/last_exit=0`，再检查现有脚本记录的 remote ready；同一次开机手动 stop 后，挂载协调也应保持服务停止。
