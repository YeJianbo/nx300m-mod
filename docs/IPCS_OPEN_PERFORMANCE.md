# NX300M 1.15 IPCS Open 性能审计

更新：2026-11-03。

**已在 1.15 固件提取的实际内核中确认：IPCS 首次上电执行两次各约 5 ms 的 DMA 复位忙等。** 首次上电分支有明确的约 10 ms 固定成本，与 `ipcs_open` 约 11.7 ms 的中位耗时吻合。最新逐调用实机记录已补齐 Open 内部分段、线程 CPU 时间和 UDD 分支，64/64 都是本进程首引用；实际 KDD 总引用变化仍未采到，尚不能证明每次都进入内核首次上电分支，也不能把此前 76 ms 的长尾归给两次固定等待。

静态调用链、实机逐调用诊断和独立 ABI5 Session 候选已完成。最近成功取得的 Open 窗口来自11月3日 11:27：Session=0，64 次有效调用，64/64 走 UDD 首引用分支；Open P50/P95/最大值为 11.709/11.971/14.830 ms，设备 Open P50 为 10.630 ms，外层 mutex 最大值仅 2 µs。前一窗口出现过 27/31 µs 的已有引用快路径及 12.431 ms mutex 等待，证据分窗保留。详见 [IPCS_SESSION.md](IPCS_SESSION.md) 及 `outputs/deployment/smartfilter-session/camera-open-20261103/latest-open-report.json`。

12:01 的新日志已等到首帧（11.420 秒），但第二次整库认证触发旧 15 秒 watchdog，KDD 观察在 attach 前取消，并留下阻止软停的标记，仍未取得逐调用 KDD 数据。维护采样现已改为单进程认证一次，首帧后只复查身份和代码；仅对有 attach 前取消证明的旧标记恢复软停。

**当前上机包默认 Session=1，正常预览关闭计时和调用采样。** 固定所有者用原厂 Open／Close 维持额外引用，每帧仍执行原厂 Open／Close／Reset 和像素处理。原版与复用版的切换、故障注入留在 ARM／MFW 模拟器；机身直接使用复用版本。新版本实体 Open 耗时、LCD FPS 和功耗尚未取得。

## 证据与复现

输入：

| 文件 | 身份 |
|---|---|
| 原厂 `usr/lib/libudd.so.3.1` | 1,018,360 字节；SHA256 `4c2d05bae62f0f4e7399ae97687c7b7c1a025213b4a000ba14003aaa31eb4b26` |
| `outputs/quickshare/stock-1.15-kernel-50ac20.bin` | 6,890,116 字节；SHA256 `ca832c10448db086d73fe9eddda21c1c88f4ff5be10b2fcb41b296518f099cf5` |
| 内核版本字符串 | Linux 3.5.0，PREEMPT，2014-12-08 |
| 实机采样 | `outputs/deployment/smartfilter-preview/camera-sampled-20261102/latest-timing.ndjson` |

`project.local.json` 指定原厂 rootfs。源码对照位于 `work/vendor-source/TIZEN/packages_nx300m/linux/`，它早于上述内核；两次 5 ms 等待、PMU、Clock、设备回调和计数均保留了实际二进制依据。当前实体相机正在运行的内核版本仍需只读核对。

```powershell
python -X utf8 tools/inspect_ipcs_lifecycle.py
python -X utf8 tools/build_ipcs_observer.py
```

结果原位保存在 `outputs/metol/ipcs-lifecycle/`：

- `report.json`：输入身份、调用点、两层计数和当前证据范围。
- `udd.asm`／`udd-calls.json`：用户态生命周期函数及两个无操作的内部日志函数。
- `kernel.asm`／`kernel-exports.json`：实际内核反汇编、导出名称和源代码对应的内部函数。
- `client-calls.asm`／`client-calls.json`：七个已知 IPCS 调用库的 31 个直接调用点，包含匿名代码扫描和条件分支。
- `timing-evidence.json`：经现有严格日志解析器校验后的 P50／P95／P99、最大值和异常帧。
- `ipcs_observe.arm`：短时只读观察器，链接原厂 glibc 2.13；构建日志及身份见 `observer-build.json`。

内核内部函数名通过导出调用目标、`d4_ipcs` miscdevice、file_operations、Clock 字符串、PMU 参数与指令共同定位。导出名称与源码对应的内部名称在报告中分别标注。Clock 及体系结构屏障中的间接回调尚未全部解析。

## 1. 用户态 Open／Close 与初始化

```text
ipcs_open [UDD 0x4bb78]
  pthread_mutex_lock(0x1012ec)                  调用 0x4bba0
  d4_udd_open("/dev/d4_ipcs", O_RDWR, 6, &fd)   调用 0x4bbc0
    lock(进程内 UDD mutex[6])
    count <= 0：libc open -> count=1 -> 返回0
    count > 0 ：count++，记录 pthread_self -> 返回1
    count >= 50：返回2
    unlock
  返回0时：
    ipcs_open_reg_base(fd)                     调用 0x4bbf4
      ioctl(PHYSICAL_REG_INFO=0xc0087303)
      mmap(原厂设备寄存器范围)
    ipcs_open_irq()                            调用 0x4bc38
      ioctl(OPEN_IRQ=0x80047301)
    ipcs_init()                                调用 0x4bc74
  返回1时：跳过上述映射、IRQ 和初始化
  成功日志函数 0x4bb5c：只保存／恢复寄存器，无输出或等待
  unlock -> 返回0
```

**外层 `ipcs_open` 的成功返回值均为 0，不能用它区分首开和复用。** `d4_udd_open` 的内部返回值才区分两条分支。计数在各进程自己的 UDD 数据区，不等于内核设备总引用数。

Open 外层 mutex 覆盖整个首开，包括设备上电期间；同进程其他线程可能在此等待。`d4_udd_open` 内部还有每设备 mutex，设备 Open 返回以前也一直持有。源码数据流对应的地址（均需加 UDD 映射基址）：

| 数据 | ELF 相对地址 |
|---|---:|
| IPCS 进程内 count | `0x126744` |
| IPCS 外层 mutex | `0x1012ec` |
| UDD mutex[6] | `0x12714c` |
| IPCS 全局 fd | `0xfca34` |

`ipcs_init [0x52910]` 的成功链为：

```text
ipcs_reset [0x603f8]
  -> ipcs_sw_dma_reset [0x60470]
  -> ipcs_default_regset_load [0x526d8]
  -> ipcs_cl_smdma_set [0x57f70]
```

这部分写入原厂复位与默认参数，未发现用户态固定 sleep。两次 5 ms 忙等位于设备 Open 的内核上电路径，与这里的用户态 Reset 不同。现有预览还会通过 ADE init 再调用一次 `ipcs_init`，处理结束时再 Reset、重载 Still IQ；本轮未删减这些恢复步骤。

`ipcs_close [0x4bdb8]` 同样先取得外层 mutex，随后 `d4_udd_close(6)`：

- 进程内还有引用：减计数，移除匹配 `pthread_self()` 的记录，不关闭设备。
- 最后一个进程内引用：计数置零、清线程记录，依次 `ipcs_close_reg_base`、`ipcs_close_irq`、libc `close(fd)`，fd 置 −1。

因此线程跟踪也约束了未来的会话管理：额外引用应由同一所有者线程成对打开和关闭。不能在拍照线程随意关闭另一个线程持有的引用，再假定线程记录完全一致。

错误清理并不对称：Open 的映射失败分支只减 UDD count，没有完整关闭刚打开的设备；IRQ／init 失败分支也没有完整回滚。Close 的映射或 IRQ 清理失败可能在真正关闭 fd 前返回。发生这些错误后，仅看 count 或 rc 不能证明硬件及句柄已恢复，实验后端不能自动循环重试或补改计数。

## 2. 实际内核的电源、Clock 与等待

设备锚点：`d4_ipcs` 的 miscdevice 在 `0xc069166c`，file_operations 在 `0xc05375ac`，其 Open／Release 分别指向 `0xc02a9a8c`／`0xc02a9a10`。这些是二进制中的真实函数指针。

```text
libc open("/dev/d4_ipcs")
  -> ipcs_k_open [0xc02a9a8c]
     -> d4_kdd_open(6) [0xc02b070c]
        内核全局 IPCS count：0 -> 1 时返回 KDD_OPEN_FIRST
        解开 count mutex
     -> 仅首次：ipcs_k_pmu_on_off(0) [0xc02a9418]
        d4_rmu_request                         分配 RMU 描述对象
        d4_pmu_scpre_set(3,0)
        d4_pmu_scall_set(3,0)
        wait_for_stable(3) [0xc019592c]          共用 pmu_lock
        clk_get(dev,"ipcs")
        d4_sw_isp_reset(RST_IPCS=7)
        udelay(1)
        clk_enable
        clk_set_rate(260000000)
        d4_pmu_isoen_set(3,0)
        udelay(1) -> reset release
        d4_pmu_bus_reset(3)
        ipcs_k_dma_rmu_bus [0xc02a935c]          调用点 0xc02a94d4
        reset -> udelay(1) -> reset release
        d4_pmu_bus_reset(3)
        ipcs_k_dma_rmu_bus [0xc02a935c]          调用点 0xc02a9504
        DMA sw reset -> bus reset -> RMU release
```

`ipcs_k_dma_rmu_bus` 写入原厂 DMA 复位序列，然后执行 100 次 `udelay(50)`。实际编译器把循环展开为每轮两次延时、计数加 2，直至 `0x64`。两条调用指令为 `0xc02a93ac`、`0xc02a93b8`，参数均为 `0x00a3d6f8`；内核 `__udelay` 的比例常数为 `0x346dc`，前者正好是后者的 50 倍。

由此确认 **每次该函数名义忙等 5,000 µs，首次上电共 10,000 µs**，另有几个 1 µs 延时。实际 wall 时间受延时校准、IRQ 和抢占影响。这是消耗 CPU 的延时循环，不是可直接删除的 Shell sleep。原厂复位时序的必要性没有被本次分析否定。

其他可能贡献：

| 阶段 | 已确认行为 | 尚缺的动态证据 |
|---|---|---|
| 外层 IPCS／内层 UDD mutex | 首开期间持有；竞争时可进入 futex 等待 | 76 ms 帧实际等了哪把锁 |
| 内核 KDD count mutex | 保护总计数和线程表，随后释放 | 是否与其他进程竞争 |
| `wait_for_stable` | 持共享 `pmu_lock`，循环 `udelay(2)`，名义超时约 100 ms | 每次等待次数与锁等待时间 |
| `pmu_wait_for_ack` | 关电路径持相同锁，循环 `udelay(10)`，名义超时约 400 ms | Close 长尾的具体来源 |
| RMU request | `kmem_cache_alloc` 分配描述对象 | 分配／调度贡献 |
| `clk_get_sys` | Clock 查找中的 mutex | 锁等待时间 |
| `clk_enable`／`clk_set_rate` | 递归父 Clock、原生回调；部分区段关 IRQ／抢占，退出可调度 | 实际回调与耗时 |
| IRQ 初始化 | `request_threaded_irq`，初始化全局 16 项 callback 表 | IRQ 分配和锁等待时间 |
| mmap | 驱动 `remap_pfn_range` | 分配和缺页相关时间 |

PMU 枚举名称需要反向理解：`ipcs_k_pmu_on_off(0)` 取消电源门控、上电；参数 1 进入关电分支。静态还确认 `ipcs_k_open`／Release 没有向调用者完整传播 PMU 失败；设备 Open 成功本身不证明每个电源阶段均成功。

内核 KDD count 与 UDD count 的组合：

| 进程内 UDD count | 内核总 count | 当前 Open 行为 |
|---:|---:|---|
| 0 | 0 | 打开设备、上电、两次 5 ms 等待、映射、IRQ、init |
| 0 | >0 | 新开设备引用，跳过首次上电；仍做本进程映射、IRQ、init |
| >0 | 已有设备引用 | 仅 UDD 增计数和线程记录；不再进入设备 Open |

**内核 count 只管理电源生命周期。** count mutex 在 PMU 操作前已经释放，不覆盖整次硬件初始化，更不覆盖后续像素处理。另一个进程首开时仍会走自己的 IRQ 初始化；该初始化把 `request_threaded_irq` 的 −EBUSY 当作可继续，并重置全局 callback 表。不能据此假设跨进程设备引用已经提供完整共享协议。

最终内核引用关闭时：

```text
ipcs_k_release
  -> d4_kdd_close(6) [0xc02b060c]
  -> 仅 KDD_CLOSE_FINAL：ipcs_k_pmu_on_off(1)
     PMU check -> DMA/PMU ack
     -> isolation ON -> ISP reset
     -> clk_disable [调用点 0xc02a959c]
     -> clk_put -> SCPRE/SCALL 门控 -> RMU release
```

只有 UDD 和内核两层都到最后引用，才能由 Close 触发这条关电路径。其他进程还持有引用时，不应关闭其电源；UDD 内部 mutex 也不是跨进程硬件锁。

## 3. 四个 B2Y 信号量的覆盖范围

四个 ID 的 `GetSemaphore(ID)` 位于 Capture `0x14b8a0`，返回实例 `+0x00/+0x10/+0x20/+0x30`。B2Y Prepare 根据启用字段取得对应信号量；Resize 使用同一提供者的 1…3。当前预览按 ID 升序非阻塞取得四个，完成恢复后逆序释放。

**除此之外，原厂还有实例 `+0x44` 的生命周期信号量：**

- 构造函数 `0x14f1d4` 在 `0x14f21c` 创建它，初值 1。
- B2Y Open 在 `0x14bafc` 取得它，再于 `0x14bb00` 调用 `ipcs_open`。
- B2Y Close 在 `0x14bb54` 调用 `ipcs_close`，之后 `0x14bbc0` 释放它。
- 无参数重载 `GetSemaphore()`（`0x14bbdc`）返回 `this+0x44`；它与 ID 0…3 的接口不同。

原 ABI4 预览没有参加这个 Open／Close 信号量协议。ABI5 Session 候选已按生命周期锁在前、通道 0…3 在后的顺序协调，帧间不持锁；不能持有四个通道锁，再阻塞等待一个已经被原厂 Open 持有的生命周期锁。实体其他客户的覆盖仍未确认。

七个库中的直接调用者还包括 ADE Resize／Ipcs Open、FD Resize、PlaybackEffect、mm-photo playback/editor、mmfhal Resize、sif resize。它们没有共同通过上述 B2Y 单例取得所有资源。查到的直接调用覆盖范围见 `client-calls.json`；动态 `dlsym`、其他设备直接 Open／ioctl 及间接调用仍不在完整性证明范围内。

结论：**四个信号量不足以证明全部 IPCS 状态受保护。** 已发现额外原厂锁，也发现独立调用者。原厂 Reset、ADE 全局开关、IRQ callback、IQ 参数缓存及跨进程电源计数不能统一视作四个通道的数据资源。

## 4. 已有 119 帧的耗时证据

取自同一个实机 dump `265-2022-3024681160-3`，黑白风格，720×480、stride 800，TID 1614。原始记录经现有解析器验证完整性，再排除 passthrough、控制污染及无效记录。分位数使用 nearest rank。

| 耗时（ms） | P50 | P95 | P99 | 最大值 |
|---|---:|---:|---:|---:|
| preview 段 | 19.583 | 21.723 | 28.640 | 84.010 |
| `ipcs_open` | 11.659 | 11.932 | 15.027 | 76.163 |
| `ipcs_close` | 0.136 | 0.191 | 0.287 | 9.351 |

按同一帧的 Open／preview 比例统计，中位占比 59.08%。最大 Open 出现在 seq=9：Open 76.163 ms、preview 84.010 ms，该帧 Close 只有约 0.008 ms。现有记录不能确定其计数分支；相邻 busy 回退也不能代替函数内部计时。

这些数据说明瓶颈确实位于 Open，而静态上电序列提供了约 10 ms 固定成本的具体来源。它们尚未区分：

1. 等 UDD／PMU／Clock 锁的离 CPU 时间。
2. `udelay` 或硬件就绪循环的 CPU 时间。
3. 抢占、IRQ 或其他调度延后。
4. mmap／IRQ 初始化等余下阶段。

已有窗口报告 dropped=23968，分位数只代表保留的 119 帧。8 帧 capture／busy passthrough 也不能充当纯原厂基线。没有显示呈现事件计数，实际 LCD 有效 FPS 仍未测得。

## 5. 短时只读观察器

`tools/ipcs_observe.c` 的本地 ARM 产物已通过原厂 glibc 链接和 QEMU `--help` 启动检查；目标采样循环尚未在实体相机上执行。它只读取 procfs：

- 启动时核对原厂相机进程、UDD 映射及三处代码指纹；进程／映射不符即退出。
- 只以 `O_RDONLY` 打开 `/proc/<pid>/mem`，读已确认的 UDD count、fd 及两把 mutex 的原始字。
- 读取指定预览线程的 `stat`、`schedstat`、`wchan`、`syscall`，同时记录采样边界和实际内核版本。
- 识别 ARM EABI 的 IPCS 设备 Open、IPCS fd ioctl／Close，以及等待两把 UDD mutex 的 futex；它们是离散时刻观察，没有函数调用 ID。
- 默认采样 3 秒、间隔 10 ms，最长 5 秒；结果暂存在观察器自己的 stdout 缓冲，结束时输出。
- 不调用 IPCS，不打开任何设备文件，不写目标内存，不使用 ptrace／信号／注入，不改变预览、拍摄模式或内核 tracing 设置。

以下是开发端调用说明，不要求用户在相机上输入命令。需要先取得当前运行的 PID／预览 TID，不能照抄旧记录的 265／1614：

```sh
ipcs_observe.arm --pid <当前相机PID> --tid <当前预览TID> --seconds 3 --interval-ms 10
```

`schedstat` 的 runtime 和 runqueue 时间若实际更新，可区分**采样窗口内**的运行和可运行但未调度时间；`stat` CPU 时间按内核 tick 计数。未调度的剩余 wall 时间还可能混合睡眠、IRQ、采样误差等，不能自动算成某把锁的等待。

此探针不会读取 KDD 内核 count 或 MMIO。无权限读取进程内存时直接退出。即使成功采样，离散快照仍不能精确拆分单次 76 ms Open；需要实际内核支持的只读 trace/perf 事件，或另行评估诊断版的逐调用 wall＋thread CPU 计时。后者涉及改变诊断运行时，本轮没有注入。

当前相机服务可达性记录在 `camera-availability.json`。服务 GET 超时，观察器尚未在实体 NX300M 上执行。构建和本地 ABI 检查不能补足这一动态证据。

## 6. 最小优化方案与启动条件

应先验证两个问题：119 帧对应的 Open 是否实际反复触发 KDD 0→1；76 ms 长尾主要在运行、锁等待还是调度。否则会话复用可能只改善固定成本，遗漏另一条故障链。

可研究的最小方案是**原厂生命周期内的非独占引用复用**：

1. 保留当前后端作为 baseline。
2. 优先借用已经获得合法资源的原厂 LiveView IPCS 生命周期；若必须增加真实 session 引用，使用原厂 Open／Close 成对管理，遵守线程记录与生命周期锁。
3. 每帧仍取得操作资源、执行原厂 Open／Close／Reset 和 Still IQ 恢复；若已有合法 session 引用，逐帧 Open 自然进入原厂非首开分支。会话引用只维持生命周期，不改变 count；四个通道锁和 `+0x44` 生命周期信号量均不得由预览跨帧独占。
4. 拍照 B2Y Open **之前**首先禁止新预览，排空在途事务，再由合法所有者归还 session；只有归还完成才进入原厂拍照。Prepare 已在生命周期锁之后，不能到这里才等待归还。
5. 退出预览、模式切换、停流、休眠、异常和卸载同样归还引用。失败后停止新实验事务；只有原厂状态恢复确认后才回退，不能因 UI 状态关闭就当作已释放。
6. 不缓存一份硬件寄存器快照跨越原厂拍照／IQ 更新；RAM LUT 数据可持久，但硬件有效性需随原厂 Reset 失效。

**该方案已经实现，普通上机入口默认开启。** 固定所有者成对引用、拍照 Open 前排空、五锁帧间释放、正常软停及不确定失败处理见 Session 报告。ARM 整合检查还执行实际部署脚本，直接读取驻留配置，确认 Session=1、timing=0、calltrace=0。全部独立调用者覆盖、KDD 首开分支和休眠顺序仍缺实机证明。若每帧均从内核 count=0 开始，两次约 5 ms 等待会继续发生；引用复用针对这项重复初始化成本。

## 7. 实际使用与剩余测量

机身直接选择「开启流畅预览 · 当前 LUT」或「流畅预览 · 黑白」。原版切换和故障回归在模拟器完成，机身不增加 A/B/A 操作。实体剩余测量如下：

| 项目 | 必须记录的证据 | 当前结果 |
|---|---|---|
| Open 首开／已有引用 | UDD 分支、设备 Open 与 KDD／PMU 事件；每阶段 wall＋CPU | 64 次原版首引用分段；新版本未测 |
| 稳态与长尾 | 帧处理 P50／P95／P99、最大值、样本数与丢失数 | 仅有上表 baseline |
| LCD 有效 FPS | 实际显示呈现和 buffer／内容更新记录 | 未测 |
| 连拍、RAW+JPEG | Prepare 等待、资源归还、拍照成功与恢复时间、文件完整性 | 资源排空通过模拟，实体未测 |
| 休眠／恢复与模式切换 | 引用生命周期、IRQ／参数恢复、下一次拍照 | 停流／恢复通过模拟，实体未测 |
| 失败与回退 | Session 归还、无在途事务、正常拍照恢复 | 故障／软停通过模拟，实体未测 |

普通预览使用 Session=1，不运行观察器；维护观察器退出时自行关闭 fd。软停先阻止新帧、排空，由原所有者 Close 后归还引用。Session 不修改原厂计数或复位时序，不直接写未知 ISP 寄存器或 NAND；最新构建、模拟和部署状态见 [IPCS_SESSION.md](IPCS_SESSION.md)。
