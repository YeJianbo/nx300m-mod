# 实时预览的 IPCS 恢复链审计

2026-11-04。核对提交 `0381fc6d` 对应源码，并重读已有实体采样。本轮改动限于离线计时分析器：JSON 和 Markdown 均增加 nearest-rank P99；相机运行时、Reset、IQ 恢复、写表及资源仲裁未改动。

## 已确认的调用链

`runtime/smartfilter_ipcs.c` 的成功事务依次执行 Open、ADE 初始化、三通道 LUT、输入缓存同步、YCC、输出缓存同步、Reset、IQ Initialize(0)、IQ Process(0)、Close。四个通道信号量按升序尝试获取，末尾逆序释放；Session 引用独立于逐帧事务，不代表跨帧保留色彩参数。实际绑定见 `tools/build_smartfilter_preview.py`，其中 `api.reset` 是原厂 `ipcs_init`。

原厂静态链见 `outputs/metol/iq-process-path.asm`：

```text
IQ_IPC_Initialize(0)
  → CIpcProcessor::Initialize @0x347728
  → Still 参数列表的 InitializeParameter
  → 节点 Reset，将缓存索引置为 ~0

IQ_IPC_Process(0)
  → CIpcProcessor::Process @0x3477e4
  → Still 参数列表的 UpdateParameter
  → 节点虚函数 Update
  → 已注入时进入 nx_lut_ram_update
  → 原厂 Update / SetParam
  → 三通道 ipcs_3d_lut GOT Hook
  → 原厂写表
```

Still 列表构造器在 `0x33f0e0` 创建 `CIpcs3DLutD4`，在 `0x33f0ec` 加入参数列表。这是恢复链能够触发成片 Hook 的静态依据。Process 的 `0x347818..0x34784c` 还会依据 IQDataRepeater 参数 0x45 选择分支；不能仅凭列表中存在节点，就声称所有实体模式的调用次数已测得。

### 现有模拟的边界

`runtime/smartfilter_ram_fixture.c::ram_probe_iq_process` 先执行原厂 Processor 的 Process，再在 `lut_sync_enabled` 时手动调用 `lut_switch_process`。后者独立 Reset 并调用真实 LUT 节点；原来的列表使用替身参数节点。这验证了预览恢复后仍能同步成片表，**没有测出实体完整 Still 列表自然触发 LUT Hook 的次数**。后续自然调用链探针必须去掉这项人为补调用，保留原厂列表遍历，并明确哪些输入或设备仍为替身。

## 已有实体分段结果

来源：`outputs/deployment/smartfilter-preview/camera-sampled-20261102/latest-timing.ndjson`，build `3024681160`，同一黑白窗口，720×480、stride 800。解析器重新校验通过，119 条有效 render 记录。它们来自 Session 复用之前，不能作为 build `3392396409` 的新性能。

单位 ms，分位数均直接从各段样本计算，表中各分位数不相加。

| 阶段 | P50 | P95 | P99 |
|---|---:|---:|---:|
| preview | 19.583 | 21.723 | 28.640 |
| Open | 11.659 | 11.932 | 15.027 |
| ADE 初始化 | 1.475 | 1.861 | 1.961 |
| LUT R | 0.045 | 0.261 | 0.330 |
| LUT G | 0.044 | 0.260 | 0.516 |
| LUT B | 0.044 | 0.265 | 0.335 |
| 输入缓存同步 | 0.534 | 0.822 | 1.044 |
| YCC | 2.347 | 2.731 | 2.863 |
| 输出缓存同步 | 0.492 | 0.595 | 0.930 |
| Reset | 0.843 | 0.903 | 1.083 |
| IQ Initialize | 0.009 | 0.012 | 0.025 |
| IQ Process | 1.714 | 1.867 | 1.998 |
| Close | 0.136 | 0.191 | 0.287 |
| 通道 begin | 0.011 | 0.012 | 0.014 |
| 通道 unlock（每帧四次的累计） | 0.004 | 0.005 | 0.006 |

每条 render 记录都有一次 Open、ADE、各通道写表、YCC、Reset、IQ Init、IQ Process 和 Close；unlock 合计 476 次。这里计数的是阶段入口，不是 IQ Process 内的 LUT Update 次数。数据是墙钟耗时，不包含可用于分离等待和 CPU 执行时间的逐段线程 CPU 记录，也没有 Session 所有者线程的配对计时。

保留窗口另有 capture 透传 2 条、busy 透传 6 条；元数据报告 dropped=23968。119/127 只能描述保留帧的分类，不能当作整个取景过程的 LUT 覆盖率。LCD 有效 FPS 仍缺呈现端事件。重新分析的完整 JSON/Markdown 位于 `outputs/review/preview-restore-audit/historical-timing.*`。

## Still Hook 内还有哪些重复工作

`runtime/lut_ram_blob.c` 已有每个上下文独立的选择解析缓存和 LUT 数据缓存。稳定选择下仍需 stat64 判断文件身份，但不重复打开和解析选择，也不重复读 4380 字节表。以所有缓存命中、强度非零、原厂依次写三通道且诊断关闭为例：

- Update 入口一次 gettid，三个 writer 各一次 gettid。
- 选择和 LUT 各一次 stat64；查找合成表缺失时还会探测直接表。
- 一次 access 检查 trace 开关。
- 三通道完成后，publish 无条件 getpid、open、write、close、rename，更新 `/var/run/nx300m-lut-status.json`。

普通三通道路径中，即使选择和输出状态没变，也至少有上述 12 次系统调用；这是代码计数，不是实体耗时测量。文件在 RAM 文件系统，不能描述为每帧写 SD 卡。

此前 VM 的 1.614 ms 是整个 Hook Update 的均摊墙钟时间，包含原厂写表、身份检查和状态发布，没有分离 publish 的耗时。它不能与实体 IQ Process 的 1.714 ms 相加，也不能证明状态文件占去了其中的大部分时间。

可以先在离线诊断中分离选择/表身份检查、原厂 Update/三通道转发及状态发布。若发布成本可见，再实现只在已完成提交状态变化时发布，并处理并发线程的发布顺序、错误到恢复、状态文件被删除后的重建，以及重新注入。不能只按 generation 跳过，否则同一代次的坏表/恢复或来源分支变化可能被隐藏。原厂实际写表保留。

## 跨帧参数复用的判断

当前证据不足以延后 Still 恢复或省略下一帧写表。Session 引用仅证明资源引用配对，不证明硬件配置在帧间保持原值。原厂 IQ 恢复本身可以重新写 LUT；RAM 表不变不能作为硬件表仍有效的依据。

`docs/IPCS_OPEN_PERFORMANCE.md` 和 `tools/inspect_ipcs_clients.py` 已列出 B2Y 外的 ADE Resize、FD Resize、PlaybackEffect、photo playback/editor、mmfhal/sif resize 等直接调用者。生命周期锁和四个通道锁不构成覆盖所有调用者的证明。原厂回放、模式切换、停流、休眠及异常返回的配置失效边界未闭合前，不采用跨帧硬件参数缓存。

后续工作按以下顺序收敛：

1. 在同一最新版实体窗口关联 Session 引用、逐帧 Open 分支与各段 wall/线程 CPU 时间，确认上电成本是否已移出逐帧路径。
2. 完成不额外手动调用 LUT 节点的原厂 IQ 列表探针；取得每次恢复的 Hook Update、三通道 writer、publish 次数。模拟值与实体值分开报告。
3. 对确认存在的重复状态发布/文件协议开销做局部优化，保留实际参数写入、动态选择和原厂恢复。
4. 只有资源和配置失效图覆盖主要调用者后，再研究跨帧复用参数；不能先以删除 Reset/IQ 的方式试性能。

本轮分析器的 94 项现有测试全部通过，退出码 0。没有重新运行硬件替身 VM 来冒充新实体计时，也没有为分析器改动重写测试卡。
