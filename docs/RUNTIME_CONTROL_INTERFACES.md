# NX300M 1.15 免刷机控制接口逆向

更新时间：2026-11-08。范围为原厂 ELF 静态分析；本轮没有执行相机代码、写卡或修改固件。这里的“已定位”指调用与数据流已有指令证据，不代表已完成菜单接入或实体功能验收。

## 本轮结论

| 功能 | 已确认的入口或机制 | 尚缺的部分 |
|---|---|---|
| 正常线程执行 | Ecore `select` 回调槽、主循环间接调用、进程内异步调度 API | 当前 MFW 会话的一次性安全引导、唤醒及退出恢复 |
| 自定义键／图库误触 | 正常取景按键表中的 Custom 表项；与回放派发分开 | 新入口的 RAM 安装和运行验证 |
| 菜单超时息屏 | 原厂停止／恢复电源定时器 API，确认布尔方向及两种回调身份 | 菜单活动与原厂线程的接线、异常退出后的恢复 |
| Auto ISO 12800／25600 | IQ 参数 37 写入及真实 AE 上限消费者 | 免刷机执行入口与实体 AE 验证 |
| A/P 快门区间 | 程序线端点、两种求解调用、后续 Auto ISO 偏移及模式路由 | 完整的范围求解策略与模式门禁；目前没有独立最慢快门 setter |
| 模式切换动画 | 原厂 Draw／Start／Stop／Frame 接口；已有独立 RAM 候选 | 新候选的运行验证 |
| 手动镜头泛绿 | 原厂 CS 初始化、更新、等待完成路径 | PP 串行化、镜头事件时序及实体颜色验证 |
| 镜头 EXIF 关联 | 本轮未新增 EXIF 写入接口证据 | 拍摄时快照与成片元数据关联仍需单独逆向 |

## 来源与复现

原厂文件来自 `project.local.json` 指定的 NX300M 1.15 rootfs。本轮报告记录六个 ELF 的大小、SHA-256、类型、函数范围与代码摘要：

- [完整符号、候选调用关系与文件身份](../outputs/controls/runtime-interfaces/interfaces.json)
- [已核对的间接派发及接口契约](../outputs/controls/runtime-interfaces/contracts.json)
- [只读提取工具](../tools/inspect_runtime_interfaces.py)

复现命令：

```powershell
python -X utf8 tools/inspect_runtime_interfaces.py
```

工具读取 ELF 并生成报告，不加载目标库。直接调用扫描包含 BL、BLX 和跨函数的无条件 B；函数指针构造扫描仅为候选，并非完整调用图。literal pool 可能在线性反汇编中呈现为指令，本文结论另结合调用点和数据表核对。

以下均为 **ELF 虚拟地址**。共享库运行时地址须加实际 load bias；不能把某次模拟器映射地址移用到实体会话。主程序为 ET_EXEC，也仍应核对文件身份与实际映射。

## 1. 正常 Ecore 线程入口

库：`usr/lib/libecore.so.1.2.0`。

| 接口／位置 | 地址 | 调用约定或作用 |
|---|---:|---|
| `ecore_main_loop_select_func_set` | `0xdb30` | r0 为新的 select 回调指针 |
| `ecore_main_loop_select_func_get` | `0xdb4c` | r0 返回当前回调指针 |
| 回调指针槽 | `0x21bb4` | 可写 PT_LOAD 内的 32 位槽 |
| 内部等待函数 | `0xc88c` | 构造 FD 集合和超时后调用该槽 |
| 实际间接调用 | `0xca8c` | `blx r5` |
| `ecore_main_loop_thread_safe_call_async` | `0x525c` | r0=callback，r1=data；进程内 API |
| `ecore_main_fd_handler_add` | `0xdb68` | fd、flags、callback、data，后续参数走栈 |

三处独立地址计算一致：

```text
setter: 0xdb40 + word(0xdb44) + word(0xdb48) = 0x21bb4
getter: 0xdb5c + word(0xdb60) + word(0xdb64) = 0x21bb4
wait:   0xc8b0 + word(0xcf68) + word(0xcf78) = 0x21bb4
```

槽在磁盘 ELF 中为零，动态重定位为 `R_ARM_ABS32(select)`。因此运行时原值必须实际读取，不能把磁盘零值当作原回调。`ecore_main_loop_glib_integrate` 还会使用 getter/setter，原值也可能已经是 glib wrapper。

内部循环 `0xd33c` 在 `0xd5c4`、`0xd648`、`0xd6cc`、`0xd728` 调用等待函数 `0xc88c`；它在 `0xca7c` 从上述槽取回调，按 select ABI 传入 nfds、三个 fd_set 指针和栈上的 timeout，再于 `0xca8c` 调用。应完整保留参数、返回值、errno 与当前回调行为。

这提供了一个明确的研究方向：一次性 RAM wrapper 在原厂自然循环线程中完成后续注册，再转交原 select。**尚未实现或验证这一引导。** 如果线程已经阻塞在旧 select 中，替换槽本身不会唤醒它；还须处理唤醒、重复安装、glib 改写和错误恢复。

异步 API 在调用者已经是 Ecore 主线程时直接调用 callback，否则分配队列项。它不是跨进程 RPC，也不能用来证明从 ptrace 任意暂停线程调用原厂函数是安全的。`main_loop_begin` 直接进入内部循环，不能假设 Hook 公共 `iterate` 会覆盖它。

证据：[Ecore 槽与调用点](../outputs/controls/runtime-interfaces/ecore-select-contract.asm)、[内部循环](../outputs/controls/runtime-interfaces/ecore-internal-loop.asm)、[导出接口](../outputs/controls/runtime-interfaces/libecore.so.1.2.0.asm)。

## 2. 正常取景 Custom 键的真实派发

主程序：`usr/bin/di-camera-app-nx300`。

原厂处理链：

```text
UI_Event_Key_Manager(event) @0x1a5384
  → 正常派发分支读取 UI_Get_State(2)
  → state_table[状态]，表基址 0x26a6e0
  → 正常取景 state=1：UI_Key_Capture_Liveview @0x17bc98
  → 先调用原厂按键过滤器 @0x17d128
  → key_table[event->key]，表基址 0x261590
  → key=12：槽 0x2615c0 → 实际 Custom handler @0x17c748
```

事件指针为 r0；原厂读取 key=`[event+4]`、action=`[event+8]`。这两个字段只代表已经定位的部分，不应据此假定整个事件结构大小。

导出的 `gUI_Key_Liveview_Custom(event)` 位于 `0x17d468`，只包装调用 `0x17c748`。**正常取景表直接指向内部 handler，绕过这个包装函数。** 仅 Hook 同名导出符号不能据此认定真实按键已接入。

原厂状态表另有：

| 状态值 | 派发函数 | 地址 |
|---:|---|---:|
| 1 | Capture Liveview | `0x17bc98` |
| 9 | Capture Lock3a | `0x18ac10` |
| 18 | Pb Single | `0x190288` |
| 19 | Pb Thumbnail | `0x182008` |
| 20 | Pb Dzoom | `0x18e4f0` |
| 21 | Pb Menu | `0x192370` |

后续自定义键应从取景专属表项入手，保留前置过滤、action 语义及其他状态的原厂处理。该表位于 `.rodata`，不能因为是数据指针就假定无需内存写权限处理。拍照、Lock3A、回放状态不在本次候选接管范围；图库删除键无需全局拦截。

这解释了旧 Custom 符号为何有函数却没有正常调用边。它提供了修复误触的接口依据，尚不等于已经修复当前被动监听实现。

证据：[状态与取景按键派发](../outputs/controls/runtime-interfaces/key-context-contract.asm)、[完整取景派发函数](../outputs/controls/runtime-interfaces/key-liveview-dispatch.asm)、`contracts.json` 中的 `key_dispatch`。

## 3. 菜单息屏与电源计时

`UI_Set_Power_Timer(bool)` 位于主程序 `0x1d0f88`：

- **true：停止 timer ID `0x28`、`0x29`、`0x2c`。**
- **false：按原厂偏好及当前门禁重新设置计时。** 不是无条件唤醒屏幕。

原厂触摸 down 在 `0x1a3148` 设置 r0=1，`0x1a314c` 调用；触摸 up 在 `0x1a3434` 设置 r0=0，`0x1a3438` 调用。此前把 true 理解为“刷新计时”会弄反方向。

| 项目 | 地址／值 |
|---|---|
| `UI_Timer_Start(id, double)` | `0x17468c`；先 Stop 再 `ecore_timer_add` |
| `UI_Timer_Stop(id)` | `0x1746e8`；删除 timer 并清句柄 |
| callback 表 | `0x2604f4 + 4*id` |
| 当前句柄表 | `0x4dbb94 + 4*id` |
| timer `0x28` callback | `0x1772d8`，原厂日志名 `UI_Manage_Timer_Display_Save` |
| timer `0x29` callback | `0x1775a4`，原厂日志名 `UI_Manage_Timer_Power_Save` |
| timer `0x2c` callback | `0x177e6c`，本轮未完整确认业务含义 |

UI 偏好 `0x390` 映射 30/60/180/300/600 秒；`0x391` 映射 30/60/180/300/600/1800 秒。实际启动还受原厂状态约束，不宜绕过这些门禁直接操作句柄。

`UI_Set_Power_Manager_Time_Info(bool)` 位于 `0x1d1784`；true 还会调用 `pm_lock_state(0,0,0)`、`pm_set_trigger(0)`。目前只见特定开机回放路径直接调用，不能直接当作通用菜单活动通知。

`libpmapi.so.1.2` 的 `pm_lock_state`=`0x864`、`pm_set_trigger`=`0x8e4`，内部向 **`/tmp/pm_sock`** 发送 20 字节 AF_UNIX/SOCK_DGRAM 消息。返回成功表示 sendto 成功，没有相机业务 ack；它也不会自动重置 app 自己的 Ecore timers。后续优先复用原厂 API，不能仅发 PM 消息便宣称菜单超时问题已解决。

接线应在原厂线程处理真实菜单活动，退出或异常结束后恢复正常自动息屏；本轮未新增永久电源锁或模拟按键。

证据：[电源与 Custom excerpt](../outputs/controls/runtime-interfaces/power-callbacks-and-custom.asm)、[触摸 down](../outputs/controls/runtime-interfaces/touch-power-caller.asm)、[触摸 up](../outputs/controls/runtime-interfaces/touch-up-power-caller.asm)、[PM 发送实现](../outputs/controls/runtime-interfaces/pmapi-transport.asm)。

## 4. Auto ISO 扩展

库：`libiq-fw-slpcam.so`。

| 接口 | 地址 | ABI／数据流 |
|---|---:|---|
| `CIQDataRepeater::SetParameter(int,int)` | `0x318bd0` | r0=this，r1=index，r2=value；index≤`0x72` 时写入参数数组，返回 1，否则 -1 |
| `GetISOMaxSetting()` | `0x318334` | `[this+8]` 指向数组，读取偏移 `0x94`，即参数 37 |
| IQ singleton 指针位置 | `0x4616b8` | 需要实际对象已初始化，不能把槽地址当 this |
| `CAeInfoIF::getAeSysInfoISOMaxSetting()` | `0x2f8b50` | 消费 IQ 上限、转换为 Sv，并结合 AE 模式限制 |

消费者读取 `CAeInfoIF+0x6c`，值大于 3 时强制 Sv128。因此“参数写成 25600”与“当前模式允许 AE 使用 25600”必须分别核对。

原厂 UserData ID `0x3b` 仅有 16 档，6400 为 `0x003b000f`；扩展 12800/25600 不能直接写入伪造的原厂索引。已有 SO 桥采用原厂有效偏好＋IQ 参数 37 扩展；本轮没有重写 AE 或 Lock3A。

数据管理服务 `GetInstance`=`0x21af5c`、`SetUserData`=`0x21b388`、`GetUserData`=`0x21b674`。GetInstance 可能创建对象，不是无副作用的只读 getter。运行时操作仍须解决执行线程与对象生命周期。

既有 [真实原厂 AE 消费者 fixture](../outputs/controls/auto-iso-policy-probe.json) 验证过桥接读回与模式限制；它不能替代实体增益、暗场曝光或当前 MFW 加载验证。完整既有协议见 [CONTROLS.md](CONTROLS.md)。

## 5. A/P 自动快门区间：实际是程序线求解

库：`libiq-fw-slpcam.so`。没有找到可以直接调用的独立 `set_min_shutter()`。

### 刷新与字段

`CAeInfoIF::AeUpdateSystemInfo()` 在 `0x2fa97c` 以 **B 尾调用**刷新 `AeUpdateProgramLineInfo()`=`0x2fa4a8`，并非 BL。

- 最快边界来自 `calcTvMaxCapture()`=`0x2f8398`，写入 `CAeInfoIF+0x48`、`SAePLInfo+4`。
- 最慢 capture 边界在 `0x2fa54c` **直接生成 signed -80**，随后写入 `CAeInfoIF+0x54`、`SAePLInfo+0x10`。
- `AeProgramLineInfo()`=`0x302c5c` 返回 `CAeProgramLine+4`。
- `captureProgramLine_CommonCalculation1()`=`0x2ffc44` 将对象 `+8`、`+0x14` 转入计算区 `+0xf64`、`+0xf66`。
- `+0xf5c` 为 Auto ISO 开关；**`+0xf5e` 是 Sv 基准，不是最慢快门阈值。**

### 求解 ABI

```c
// 根据变参读取与调用点恢复的参数语义；不是可直接外部调用的控制 API。
AeMathCalcProgramLine(int bv16, tagSAeParam *out, int point_count, ...);
// 每个端点传入四个 promoted int：Av16, Tv16, Sv16, exposure_bias16。
```

函数地址 `0x2ce900`，按端点的 `Bv16 = Av16 + Tv16 + 80 - Sv16 - bias16` 分段插值。第三参是端点数量，不是约束类型。

| 路径 | 函数 | math 调用点 | 端点数 |
|---|---:|---:|---:|
| A | `0x301dfc` | `0x301f04` | 2 |
| P | `0x302790` | `0x302980` | 5 |

A 两点为固定 Av、Sv 基准下从 Tv=-256 到 TvMax 的程序线；P 五点跨越 AvMin／中间 Av／AvMax 与不同 Tv 节点。它们不是四个可随意改写的 min/max box。

输出结构已定位的部分：signed short Tv@`+0`、Av@`+2`、Sv@`+4`，边界标志 `+6/+7`，残差 `+0x14`。

**求解后还有一段 Auto ISO 调整。** A 在 `0x301f4c`、P 在 `0x3029cc` 调用 `calcSvshiftForIsoAuto(short×6)`=`0x3018a0`，随后同时偏移 Tv 和 Sv，再使用 `+0xf66` 处理慢边界。只改最终 Tv 会破坏曝光配平；只改端点也不能证明后续 ISO 偏移不会越界。

### 模式门禁

`AeProgramLineForCapture()`=`0x30303c` 按 `this+0x36` 路由。三个域不同：

| 域 | P | A | S | M |
|---|---:|---:|---:|---:|
| UI mode | 2 | 3 | 4 | 5 |
| IQ DIALMODE | 1 | 2 | 3 | 4 |
| AE capture | 0 | 1 | 2 | 3 |

A 求解函数也被 `calcProgramLine_Capture_Pro_Macro`=`0x30200c` 复用；特殊场景还可能回落到 AE0。后续必须结合原厂模式域、视频／3D／场景状态门禁，不能按函数名或单个 AE 枚举接管所有调用。

IQ 侧 getter：GetShootingMode=`0x31873c`、GetCaptureMode=`0x31814c`、GetMovieAE=`0x318890`、Get3DMode=`0x318b78`。本轮尚未证明它们组合后覆盖全部特殊模式。

现阶段可设计“限定 A/P 的程序线与 ISO 联合约束”，尚不能发布完整快门区间。现有最快上限 SO 的能力边界仍以 [SHUTTER_LIMIT.md](SHUTTER_LIMIT.md) 为准。

证据：[IQ 全量相关函数](../outputs/controls/runtime-interfaces/libiq-fw-slpcam.so.asm)、[capture 路由](../outputs/controls/runtime-interfaces/ae-capture-dispatch.asm)、[模式 getter](../outputs/controls/runtime-interfaces/iq-mode-getters.asm)。

## 6. 动画与手动镜头接口

模式动画已定位主程序：DrawModeDial=`0x9d36c`、ani_timer_start=`0x9e328`、ani_timer_stop=`0x9e37c`、draw_modedial_ani=`0x9e3f0`、update_center_mode_icon=`0x9e550`、ClearModeDial=`0x9db90`。调用前者为 r0=this；Start 含 double 参数，须遵循目标 ARM 浮点 ABI，不能统一套用纯整数调用模板。

现有独立 RAM 候选仅完成构建与反汇编检查；本轮没有运行它，也没有恢复旧 V0。软件切换模式相关入口 `UI_Manager_ModeDial_Event(eUI_MODE)`=`0x1a72f8` 已定位，但并未完成独立外部调用条件验证。实体拨盘锁继续沿用已部署的独立 X11 路径。

CS 接口位于 `libiq-fw-slpcam.so`：

| 接口 | 地址 | 已确认调用关系 |
|---|---:|---|
| `CPreProcessorD4::InitCsParam()` | `0x34316c` | Still_M2M／Still／Draft 初始化路径 |
| `UpdateCsParam(bool)` | `0x343e74` | UpdatePPCoreForDraft 传 true |
| `WaitUpdateCsParam()` | `0x343de4` | Update 内两处路径调用 |

Wait 检查 `[self+0x18]&3==3` 后调用 `pp_core_wait_lut_update(1)`，再清除 mask `2` 对应位；负返回记录错误。父包装函数未见显式锁，不证明更上层没有锁。**已有等待函数不等于从任意线程发起初始化就是安全的。** 仍需明确 PP 提交串行化和镜头 attach/detach 时序。

原厂缺镜头默认初始化路径已存在，但本轮不据此认定泛绿根因唯一或修复成功。继续保留 [CS RAM 调查](MANUAL_LENS_CS_RAM.md) 中的生产加载限制。

证据：[CS 上层路径](../outputs/controls/runtime-interfaces/cs-parent-paths.asm)、[模式／电源导出函数](../outputs/controls/runtime-interfaces/di-camera-app-nx300.asm)。

## 后续实现顺序

1. 在隔离 ARM 环境验证正常线程的最小引导，只注册一个无硬件副作用的回调，验证恢复；保留原 select 行为。
2. 接取景专属 Custom 入口和菜单活动通知，分别解决图库误触与菜单息屏。保留 MENU、OK、Fn 的现有语义。
3. 利用正常线程入口接 Auto ISO 现有参数路径，分别核对配置读回、AE 生效上限和实体曝光。
4. A/P 范围先完成模式门禁和联合求解方案；CS 先补 PP 串行化证据，再进入实现。

本轮仅完成接口逆向与可复现证据整理；原厂 Reset、Still IQ、Session、拍照仲裁、RAW 和 LCD DMA 路径均未修改。
