# 手动镜头泛绿：手动 RAM 调查与修复候选

适用对象为 NX300M 1.15／已有 MFW。当前没有实机三状态对照，不能认定泛绿的唯一原因，也不能认定候选已修复颜色。本文将手动触发的候选与旧的启动 SO 分开：不安装自动修复，不改机械检测值，不做 JPEG／SRW 颜色反向补偿，不刷 NAND。

## 调查结论与最小修改点

原厂 `CIQLensInfo::CallbackDetach()` 会清除 IQ ready 与镜头参数。原厂 2D `UpdateCsParam()` 在 IQ ready 为零或 shading 为空时直接返回；这段返回路径不会自行重建默认表。原厂 `InitCsParam()` 则有现成的无镜头默认分支。它们构成一个可检验的状态残留假设：IQ 已清空，但 PP 上次提交的 CS 校正仍在使用。实际事件、符号与分支证据见 [事件调用链](MANUAL_LENS_EVENT_CHAIN.md)。

最小候选不调用全局 Lens Detached，不调用会触及持久镜头数据的 `CLensInfoAppLayer::Reset()`，也不伪造 IQ ready。它在**下一次原厂 `UpdateCsParam()` 调用线程**中消费一次明确的手动请求，满足以下条件才调用原厂 `InitCsParam()`：

- 现有电子镜头服务存在，状态为已核对的 detach 落点 `-1`，原厂 `IsModuleReady()` 未返回 ready；这不等于证明刚发生过拆镜头事件。
- 现有 IQ 对象的 ready 已为零，shading 指针已为空；陈旧非空状态拒绝处理，先继续观察。
- 目标是现有 PP singleton，2D 路径、已初始化、模式为已审查的 0～3，尺寸／缓冲大小在已知边界内。
- PP flags 的 pending 位未置位。pending 时记录 deferred，消费本次请求；不强等 DMA，不自动重试。

默认值由原厂函数生成：mode 0 仍调用原厂 capture gain getter，其余已审查模式采用原厂默认 gain。候选不改 gain、不生成自己的校正表。黑位、坏点、传感器工厂表及其他 ISP 模块未被候选直接修改；这不是“基础校正图像结果不受影响”的实机证明。

候选使用四个 IQ `R_ARM_JUMP_SLOT`，不覆盖原厂函数指令、不改对象 vptr：

| IQ 内偏移 | 原厂目标 | 用途 |
|---|---|---|
| `0x3c1158` | `CPreProcessorD4::UpdateCsParam(bool)` | 在原厂调用线程消费手动请求 |
| `0x3c0718` | `pp_core_common_lut_init` | 记录 CS kind 3 的提交参数与返回码 |
| `0x3c3208` | `pp_core_cs_set_info` | 记录同线程调用顺序 |
| `0x3c1228` | `pp_core_cs_onoff` | 记录同线程调用顺序和开关参数 |

厂商 `work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd/pp_core.h` 明确：`common_lut_init` 返回 int，`cs_set_info` 和 `cs_onoff` 均为 **void**。不能将后两者残留的 r0 当成成功回执。候选的 `submitted` 只表示 common 返回 0 且后继调用顺序符合预期，不代表硬件完成或颜色修复。

## 当前执行边界

`manual_lens_cs_ram.bin` 为 8 KiB 的自包含 RAM 代码／状态，代码 RX、数据 RW，无 libc 或运行时重定位。加载器复用已存在的 ARM ptrace／纯系统调用底座，核对原厂 IQ、UDD、lens、libc 与相机程序身份。没有后台轮询、自动重新应用、目标进程重启或工厂文件写入。

**生产 apply／request 暂不开放。** 把 Init 放到原厂 Update 线程，只能避免从任意外部线程调用，并不能自动证明 PP 对象没有其他并发使用者。上层串行化与硬件完成条件尚未建立，不能为了能执行而绕过。`--fixture` 仅允许明确命名的独立 ARM 夹具；其结果不充当实机验收。正常入口默认只读 status。

旧 `manual-lens-cs.so` 的自动判定及 inline ready gate 不是本轮实体方案，也不应与本候选叠加。当前不修改根 autoexec、休眠快照或实体 SD。

## 已执行的 ARM 验证

独立完整 ARM VM 的最终 [report.json](../outputs/controls/manual-lens/cs-ram/arm-vm/report.json) 为 `passed:true`，guest 脚本退出 0，私有 VM 已关闭。运行真实原厂 IQ／lens／UDD 函数，但 singleton 对象、几何与 CS backing 是夹具提供的 RAM；没有传感器或实体 ISP。

- 原厂 `InitCsParam` 的 mode 1 默认分支实际生成 96 bytes，每个 uint16 均为 `0x2000`（gain `0x100`）；没有用自写生成器代替该函数。
- 真实 UDD 未打开，`common_lut_init` 返回 **-3**。候选记录同一 Update TID、common → info → onoff 顺序，明确报告 `failed`，未误报 `submitted`。两个 void API 的结果为 `null`。原始结果见 [failed.json](../outputs/controls/manual-lens/cs-ram/arm-vm/guest/mfw-results/failed.json)。
- 真实 ptrace 的四槽安装、重复安装不增加映射、撤槽、同映射再次安装通过；无请求不执行 Init，已消费请求不重放。电子 ready、IQ 非空和 DMA pending 的拒绝／暂缓分支通过；目标双线程与启动身份保持，最后无 tracer。
- 单独的 [policy.json](../outputs/controls/manual-lens/cs-ram/arm-vm/guest/mfw-results/policy.json) 使用假回调覆盖“提交成功”的软件分支，明确 `fake_callbacks:true`、`stock_success_verified:false`，不能作为原厂硬件成功证据。

前两次启动因缺少 `CWatchDog` 析构符号退出 127，均未进入目标 Init／Update；证据分别保留在 `first-startup-failure/`、`second-startup-failure/`。补入真实原厂 provider 后，76 个映像的依赖闭包、511 项 GLOB_DAT 引用的必需强符号检查及启动预检通过；允许未定义的弱符号仍单列保留。最终原厂 fixture stderr 有 `open log dev is failed`，其余验证正常完成。

本轮未验证 mode 0 的 AE capture gain、真实 DMA 完成、PP 并发串行化、照片色偏或电子镜头成像兼容性。因此上述结果验证了候选的调用与失败处理，**不改变生产入口关闭的结论**。

## 三状态实机取证

先只运行 `manual-lens-probe.arm`。它不停止进程、不写目标内存、不调用目标方法；电子 ready 根据已核验的原厂 switch/table 静态解码。机械 GPIO 仅是可选诊断。每次保存完整 stdout、stderr 和退出码，并记录镜头、开机方式、拍摄参数与现象。

1. **原厂镜头基准**：NX 镜头正常工作时，保存电子 raw state／ready、IQ ready／shading、PP mode／flags／几何，拍固定场景 SRW＋JPEG。
2. **手动镜头异常**：按能复现问题的关机换镜头流程，保持相同曝光与白平衡，保存相同日志和 SRW＋JPEG。不要为了取得异常而清工厂数据或改未知寄存器。
3. **原厂 detach 后恢复**：只通过相机允许的正常装卸操作取得真实事件，不用软件伪造事件，不要求用户带电拆装。能安全复现时记录操作和恢复图像；若只有冷启动恢复，明确标为冷启动参照，不能冒充 detach 对照。

观测重点为 `lens_operation`、`iq` 和 `pp`。`device_cs_buffer_not_observed` 表示 CS backing 为设备映射，外部未读取到表内容；它不表示表为空或损坏。单次观察未加锁，不保证字段属于同一瞬间。当前已有旧日志仅覆盖 IQ ready=0／shading 空、PP 2D mode 3 等状态，没有上述完整三组。

## 将来开放手动候选后的最小验收

此节是验收协议，当前不是可执行的实机安装步骤。

1. 先取无补丁基准，并确认相机没有曝光、存储传输或镜头动作。首次加载保持无请求，确认原厂镜头工作和取景、拍摄未变。
2. 复现手动镜头异常；提交一次 request-default。若状态不满足应明确 refused／deferred，不能报告已修复。若一直 pending，说明没有进入已覆盖的更新路径，不主动调用未知刷新函数。
3. 核对请求 generation、Update TID、CS 三次调用的顺序、common 返回码、前后 IQ／电子状态和 PP 参数。`submitted` 后还需实际拍摄及原厂硬件完成证据。
4. 同条件拍摄 SRW＋JPEG，比较绿／紫空间色偏、灰阶、中心和边缘，检查是否引入黑位、坏点、曝光或新的色偏变化。RAW 与 JPEG 分别下结论。
5. 正常装回 NX 镜头，等待原厂电子 ready 和 IQ 校正有效。由原厂正常重建，必要时仅提交一次 request-stock，并重复基准拍摄。未通过之前不接自动修复。

## 回滚与恢复

- **取消请求**：只取消尚未消费的 request。已经提交的 CS 表不会因此自动变回。
- **恢复原厂调用槽**：disable 后等待所有候选回调退出，再还原四个 GOT 槽，逐项读回；保留 RAM 映射，避免在途调用访问已释放代码。若仍 busy／inflight，不强行撤槽。
- **恢复成像状态**：撤槽不能撤销已经上传的表。需由原厂正常初始化重载；有有效电子镜头时 request-stock 也只能请求原厂 Init，不能复制旧 PP 对象或回填过期表。必须结合实际图像确认恢复。
- **断电／快照**：不把普通关机、拔电池或设置重置当成保证清除补丁的方法。MFW 可能恢复旧进程；应核对原厂调用槽与进程身份。移除脚本只防后续执行，不撤销已有 RAM 修改。

不提供清工厂标定、写未知 ISP 寄存器、改 NAND 或清休眠分区的恢复步骤。

## 构建

```powershell
python -X utf8 tools/build_manual_lens_probe.py
python -X utf8 tools/build_manual_lens_cs_ram.py
python -X utf8 tools/build_cs_live_loader.py
```

构建与 ARM 验证结果以各自 JSON 原始记录为准；本地编译或夹具提交不替代三状态实机对照。
