# NX300M 1.15 手动镜头事件与 CS 静态链

本次只读核对原厂 ELF 和厂商 SDK 头文件。结果支持一个具体机理：Detach 会清掉 IQ 镜头 shading 来源，而 2D `UpdateCsParam` 在 ready=0 或 shading=NULL 时直接返回；因此，不能把“已 Detach”当作“旧 PP CS 表已被默认表替换”。静态链尚未证明 PP 对象的共同串行化，生产 apply 应继续禁用，观察记录可以保留。

没有运行实体相机、写卡、刷机或主模拟器，也没有验证实际照片色偏、硬件提交完成或重启后的效果。本文不把静态候选写成已完成修复。

## 证据与复现

原厂源目录：`//wsl.localhost/Debian/opt/nx300m-lab/stock-1.15`。库内地址均为 ELF 虚拟地址，实际进程须按对应 module load bias 重定位。

| 证据 | 内容 |
| --- | --- |
| [extract_static.py](../outputs/controls/manual-lens/event-chain/extract_static.py) | 只读提取脚本，按符号 size 导出 A32 反汇编，另扫描指定目标的直接 B/BL 候选 |
| [symbols.json](../outputs/controls/manual-lens/event-chain/symbols.json) | ELF 文件大小、函数范围、直接分支候选、模块 ready 表、持久文件路径解算 |
| [lens.asm](../outputs/controls/manual-lens/event-chain/lens.asm) | Lens state、事件分发、raw module 状态和 ResetFlashInfo |
| [iq.asm](../outputs/controls/manual-lens/event-chain/iq.asm) | IQ callback、Init/Update CS、PP core 和 VD falling wrapper |
| [capture.asm](../outputs/controls/manual-lens/event-chain/capture.asm) | VD falling event worker、第二处回调入口、PP core 调用者 |
| [app.asm](../outputs/controls/manual-lens/event-chain/app.asm) | UI lens event 到 Attached/Detached/Ready 的入口 |
| [udd.asm](../outputs/controls/manual-lens/event-chain/udd.asm) | CS 低层 wrapper 的返回值处理 |
| [pp_core.h](../work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd/pp_core.h) | SDK 函数声明：common LUT init 返回 int，两个 CS setter 返回 void |

复现命令：

```powershell
python -X utf8 outputs\controls\manual-lens\event-chain\extract_static.py
```

最后一次执行退出码 0，已读取实际导出结果。线性反汇编包含 literal pool；形似指令的数据不作为可达代码证据。脚本的 direct branch candidates 只是扫描结果，下面链条中的分支另经人工核对。

## 事件与电子镜头状态

Lens 侧 `CLensOperation::OnLensCallback` 在 `liblens-fw-prod.so:0x2cd74` 分发事件。主路径如下，不能与函数前面的 notifier 快路混为一谈。

| 原厂事件 | Lens 主路径 | IQ 路径 | App UI 路径 |
| --- | --- | --- | --- |
| `0x2000001e` Attach | `0x2d2cc setModuleState(0,0)`，执行 init sequence，再在 `0x2d2e8` 分发 Attach | `OnCallback` → `CallbackAttach` `0x30c7b8` → AE Attach wrapper | UI event 6 → `UI_Callback_Lens_Attached` |
| `0x2000001f` Detach | 停 polling、清若干表、`0x2d110 setModuleState(-1,0)`，`0x2d224` 分发 Detach，再发 UI event 5 | `CallbackDetach` `0x30c7bc` 清镜头 IQ 来源，见下节 | UI event 5 → `UI_Callback_Lens_Detached(true)` |
| `0x20000020` Ready | `0x2d294` 分发 Ready，再发 UI event 7 | `CallbackReady` `0x30dd90` 计算 distortion/shading 后置 ready=1 | UI event 7 → `UI_Callback_Lens_Ready(true)` |

`CIQLensInfo::Initialize` `0x30c6fc` 向 `CLensInfoControl` 注册这三项 callback；`OnCallback` `0x30ddd8` 对 `event-0x2000001e` 分发。该连接由真实注册和调用指令支持。

`CLensOperation::setModuleState` `0x2a074` 只写 `operation+0x70` 的 int 和 `+0x74` 的 byte。raw=-1 是 Detach 的明确落点，但也见于 `Initialize:0x2a158`、`WarmBootInitialize:0x2457c`。构造函数在 `0x27428` 令 r0=0，并在 `0x274b4` 将其写入 raw state。因此单次读到 -1 不能区分稳定脱镜与初始化窗口。

`IsModuleReady:0x2b250` 通过 `raw+3` 查 `0x6fb54` 的表，实际表已存 JSON：

| raw | 返回事件值 |
| --- | --- |
| -3 | `0x2000001b` |
| -2 | `0x2000001a` |
| -1 | `0x20000017` |
| 12、19 | `0x20000019` |
| 0..11、13..18，以及越界 | `0x20000018` |

机械 detection 是另一条证据。`CLensGateKeeper::IsLensAttached:0x25d48` 通常读 GPIO 并反相，且 adjustment 字段 `+0x2454` 可强制返回 attached。`CLensState::OnDetach:0x43224` 先确认机械非 attached，之后才执行 GateKeeper Detach 并向上回调。电子 module ready 与机械检测不可互换。

## Detach 清 IQ 来源，Update 不补默认表

`CIQLensInfo::CallbackDetach:0x30c7bc` 首先调用 AE Detach wrapper，然后释放并清零 `+0x18/+0x1c/+0x20/+0x24/+0x28` 的数组，清 `+0x8` ready、`+0xc/+0x10/+0x14/+0xf0`，设置 `+0xf4=1`，清 `+0x70` 的 0x80 bytes。该函数本体没有 PP/CS 提交调用。

`CallbackReady:0x30dd90` 调 AE Ready、`CalcLensdistortionValue`、`CalcLensShadingValue`，再于 `0x30ddb4` 将 `+8` 置 1。ready=1 不单独证明 shading pointer 非空，也不证明硬件上传完成。

2D `CPreProcessorD4::UpdateCsParam(bool):0x343e74` 在 `0x344010` 读 IQ ready，ready=0 于 `0x344018` 跳至返回路径 `0x343f98`；ready 非零才在 `0x34401c` 读 shading `+0x20`，NULL 同样返回。它不会在这些状态下调用默认表生成支路。

由此可得：Detach 的来源清理与 Update 的早退能够解释“旧表可能保留”的风险。仍不能据此证明每次脱镜都会残留旧表：AE 内部实现、其他间接模式切换与硬件生命周期未被完整穷尽。

## Init 默认支路与副作用

`CPreProcessorD4::InitCsParam:0x34316c` 中，2D（`pp+0x14==0`）且 IQ ready=0 或 shading=NULL 时进入 `0x3433d0` 默认支路。

- `pp+0x2c` mode 非 0 使用 gain `0x100`；mode 0 经 `0x343bdc IQ_AE_GetCaptureCSGain()` 取得 capture gain。
- gain 左移 5 位并 mask `0xffe0`；循环 `0x34342c..0x343454` 向 `*(pp+0)` backing 按表几何写四路相同值。
- mode -1 或 4 走 CS off；其他默认模式调用 `0x3434b0 pp_core_common_lut_init(3,...)`、`0x3434bc pp_core_cs_set_info(...)`、`0x3434c4 pp_core_cs_onoff(1)`，更新 `pp+0x18` flags。
- common LUT init 返回 0 才 OR 5，但即使它失败，原厂仍继续执行两个 void setter。因此“调用三项”与“成功上传且硬件生效”是不同证据。

这是一条生成 uniform CS 表的分支；该限定支路内没有看到工厂空间校正文件加载或 `ApplyCrosstalkComp` 调用。不能推广为整个 Init 不影响基础校正：它的其他镜头/3D 支路有不同处理。

Init 会写 backing、配置 CS、改变 enable 和 flags，且外部 singleton 获取可能涉及 lazy construction。`IQ_PP_SetCoreParam:0x32636c` 的入口范围更广：Draft `SetPPCoreForDraft:0x347178` 除 Init CS 外还设置 Bayer type、sensor info、OOC、BG、XY shading、DPC、OBD，并清 flags/count。仅为 CS 复位不应重跑完整 core，也不应关闭 XY shading、DPC 等其他基础处理。

## Lens Shading 到 CS，与传感器校正的边界

本节区分的是实际数据来源和 PP block，不把厂商的 Lens Shading、Color Shading 与 XY shading 名称当作同义词。

`CalcLensShadingValue:0x30cee0` 在 `0x30cf08` 调 `CLensInfoControl::GetSpecificRelativeIlluminance`，取得镜头侧 relative illuminance 数据；在 `0x30cf34` 检查旧 `this+0x20`、释放后于 `0x30cf50` 分配 `0x54 * this+0x10` bytes，并在 `0x30cf60` 把新指针写回 `CIQLensInfo+0x20`。后续 `IntSpline`（例如 `0x30d0c4`）直接向由该指针派生的地址填表。不是只由函数名推测这张表的归属。

2D `InitCsParam` 在 `0x343534` 检查该 lens singleton 的 ready，在 `0x343540` 读取同一个 `+0x20`；`UpdateCsParam` 在 `0x34401c` 也读取 `+0x20`。Init 的 GOT 引用 `0x3c3488` 经动态 relocation 明确对应 `CIpcSingleton<CIQLensInfo>::m_pInstance`，计算过程存于 JSON 的 `reviewed_source_relocations`。有镜头分支还使用 LensID、zoom、F-number 和插值数据生成 CS backing。因此 **Lens Shading 的 relative illuminance 来源确实进入 CS 处理链**；不能将它泛化为所有传感器空间校正。

未找到 `InitXyshadingParam` 这一符号，原厂对应入口是 `SetXyShdParam(int):0x344fe8`，其数据与提交接口均独立：

| 项目 | CS | XY shading 原厂路径 |
| --- | --- | --- |
| 输出 backing | `*(pp+0)` | `*(pp+8)`，`0x34504c` 读取 |
| 已核来源 | `CIQLensInfo+0x20`，镜头 relative illuminance 派生 | mode 相关分支在 `0x34528c/0x3452a8` 调 `GetMovieVfpnAddress/GetLiveVfpnAddress`，`0x3452c8` 起复制到 XY backing；mode 0 另有 AE/AWB/gain 生成分支 |
| 硬件接口 | `pp_core_cs_set_info/onoff` | `pp_core_xys_set_info/onoff`，调用点 `0x3451c8/0x3451d0` |
| common LUT selector | 3 | 1，调用点 `0x3451dc` |

`CIpcMemoryManager::LoadVfpnData:0x3483e4` 在 `0x348430` 调 `IQ_ReadDataFromFlash(0x74, live_ptr, 0xed8)`，在 `0x348440` 调 `IQ_ReadDataFromFlash(0x76, movie_ptr, 0x16f8)`，随后做表内归一处理。GOT `0x3c342c/0x3c3b10` 分别 relocation 到 `m_pVfpnLive/m_pVfpnMovie`；两个 getter 返回这些数据地址，地址有效性失败还可能 reallocate。这里证实了 **Flash-read API/VFPN 数据来源**，尚未向下追到具体 flash 分区、文件或每台机器出厂标定身份，不能扩大为已验证某一工厂校准文件。

Draft core 的实际顺序为 `SetXyShdParam(0x22)`（`0x3471bc`）→ `SetDpcParam`（`0x3471c4`）→ `InitCsParam`（`0x3471cc`）→ `ProcessOBDValue(0)`（尾跳 `0x3471dc`）。Still/M2M 同样分别调用 XY、DPC、CS。`SetDpcParam:0x346c70` 具有独立 dynamic DPC API 和 defect-address 分支；`ProcessOBDValue:0x345b4c` 处理独立 OB 状态，process=1 分支在 `0x345c18` 调 `pp_core_obd_get_ob_data`，Draft process=0 则复位其统计状态。不能把 OBD 这一调用本身称为启用/禁用开关。

因此，手动镜头的 CS 候选只应作用于已经限定的 CS 默认支路，保留原厂 XY/VFPN、DPC、OBD 路径。整体关闭 PP 或连带关闭 XY 会改变独立的传感器处理来源，超出镜头 CS 问题。以上仅证明静态结构及作用范围；没有证明改动后照片的亮度、色彩、噪声和坏点处理完全不受影响，也没有消除前述并发缺口。

## VD falling 的执行链与同步缺口

已核对的 Draft Update 调用链：

```text
capture CFrontEndEventHandler::t_Main           0x1a4098
  waitEvent 0x1a3cec → receiveEvent 0x1a3af4 → _fx_eventflag_receive
  eventHandler                                0x1a41a8（调用点 0x1a4184）
    handleVDFallingEvent                       0x1a43cc（调用点 0x1a4204）
      IQ_handleVDFallingIntrrupt               0x322cf4（调用点 0x1a43dc）
        CPreProcessorD4::UpdatePPCoreForDraft   0x346840（尾跳 0x322d18）
          UpdateCsParam                       0x343e74（调用点 0x3468d4）
```

`t_Main` 是用户空间事件等待/分发循环；名字中的 Interrupt 不足以证明处于内核 IRQ 上下文。该队列可顺序处理其收到的事件，但以上函数本体未见覆盖 PP 对象更新的 mutex/semaphore。

还有第二个已核对入口：`CCallback3DReadOutDone::onExecute:0x1d81a0` 在 `0x1d81dc` 直接调用同一个 IQ VD falling wrapper。它增加了需要解释的执行路径，不能仅用常规 event worker 的 TID 证明全对象单 owner。

Init 的其他入口也已存在：capture `videoSetPPCore:0x2c1308` 在 `0x2c13fc` 调 `IQ_PP_SetCoreParam(1)`；`capSetPPCore:0x2c2b3c` 在 `0x2c2c70/0x2c2ca4` 调 SetCoreParam 的其他模式。未证明这些调用者与 VD falling 共用锁或受同一线程约束。

Lens `CallbackDisptch:0x27770` 的确用 `operation+0x1c8` mutex，在 `0x277b4` lock、`0x277e8` unlock；证据只覆盖 lens callback register 分发，未连接到 PP backing 互斥。UDD wrapper 的下层保护即便串行化单次硬件 API，也不足以覆盖 Init 前面的整张 backing 写入。

所以以下条件只是候选前置筛选，尚不足以允许 apply：raw=-1、IQ ready=0、shading=NULL、已有 PP singleton、2D、mode 0..3、pending bit=0。特别是 pending=0 的瞬时检查及 TID 相同都不构成共同互斥证明。

## 低层 API 返回契约

SDK `pp_core.h:42`、`:117`、`:118` 的声明分别为：

```c
int pp_core_common_lut_init(enum pp_core_lut_selection, struct pp_core_lut_load_info *);
void pp_core_cs_set_info(struct pp_core_cs_info *);
void pp_core_cs_onoff(enum pp_onoff);
```

原厂 `libudd.so.3.1` 二进制与此一致：`common_lut_init:0x21f10` 保存下层返回 r0，在最后一个内部 helper 后恢复返回；`cs_set_info:0x22de0` 与 `cs_onoff:0x22e1c` 均为 60 bytes，调用下层后再调内部 helper `0x230e8`，没有保存下层 r0。两个 void wrapper 返回时的 r0 没有成功语义。

观察器可分别记录 common 的 int 结果以及两个 void API 的调用/返回次数、参数和执行顺序；不能把 void 返回残留 r0 统计成成功回执。它们也不提供硬件 readback 证据。

## 排除的触发点与持久副作用

`CLensInfoAppLayer::Reset:0x19f2c` 跳到 `CLensInfo::ResetFlashInfo:0x3e394`，明确执行：清 0x10000 backing/0x804 header、`open(flags=0x42,mode=0755)`、`write(0x10000)`、`fdatasync`、`close`。PC-relative literal 已解算为字符串地址 `0x72e44`，路径 **`/mnt/ubi1/lens_info.bin`**，记录在 JSON。它不是仅 RAM CS 复位入口。

相反，`CLensInfoAppLayer::Read/Write` 本体是 semaphore 加 64-byte 内存复制，不能只凭 Write 名字断言写持久文件。`CLensInfo::detach` 还释放子对象，未穷尽其析构间接副作用。

`CLensGateKeeper::Detach:0x25f78` 实际进行 IRQ、LensPowOff、lens SPI close/disable 和 GPIO 操作；伪造 Attach/Detach 全链会触及这些实体行为，不适合作为局部 CS 重置。

App `UI_Event_Callback_Manager_Lens:0x195c7c` 将 event 5/6/7 分给 Detached/Attached/Ready。Detached `0x211610` 进入无符号 helper `0x213fac`，该 helper 包含取消拍摄、UI状态和 `UI_Start_Modechange_Capture_Mode:0x2142bc` 等分支。没有发现此处直接 CS Init 调用；间接模式切换是否必然重设 PP 尚未连接，不能作为确定的修复触发。

## 当前可采用的边界与剩余项

最小候选是复用原厂 PP 更新回调中已有对象的 CS Init 默认逻辑，并把请求消费限制为一次。当前只支持观察：未证明 Init/backing 与其他 PP 写入共用同步机制，生产 apply 应保持关闭。

要改变这一结论，需要至少补上覆盖整张 backing 写入与提交的真实共同锁/单 owner 证据，或建立受控的原厂调度路径；仅多次看到同 TID、pending=0、没有崩溃或 common 返回 0 均不够。

仍未验证：AE Detach 内部及所有间接 UI/core 路径的完整副作用；相机运行期线程/锁与模式转换时序；硬件上传/readback；手动镜头真实照片色偏是否改善；不同拍摄模式、电子镜头重新接入和 suspend/resume 的兼容性。后续实体确认应先读取 observer 的真实输出，预期区分事件、状态、一次请求消费和三项 API 的调用顺序，再决定是否具备局部 apply 的同步条件。
