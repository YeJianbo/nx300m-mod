# 实时 LUT 预览：当前实现与续接点

## 当前实现

当前主线是一个原厂SmartFilter槽位与IPCS YCC→YCC三维预览。所有LUT使用MOD独立选择，不各占原厂槽位。已实现原厂API适配器、单槽RAM模块、五个PLT槽的加载／恢复、拍照等待和MOD风格／强度同步。宿主为原厂Miniature，菜单文字尚未改名；原厂SmartFilter的RAW菜单限制仍待处理。构建与两类ARM探针见 [单槽模块及验证结果](SMARTFILTER_LUT_PREVIEW.md)。

已补全ADE包装器的六个PhotoEditor调用来源、普通取景的节点插入条件、缓冲区和生命周期。Still准备回调已解析为`CIpcProcess::SetIpcsIQParamCallBackFunction`，会调用`IQ_IPC_Initialize(0)`和`IQ_IPC_Process(0)`。是否完整重写预览改变的色彩状态仍待确认。隔离ARM探针执行原厂SmartFilter和ADE参数交接；像素采用软件参考后端，真实IPCS输出、共享资源仲裁和拍照恢复尚未验证。

[IPCM 原生风格预览](IPCM_PREVIEW.md)保留为已有候选，本轮暂停继续优化CCM／Tone／YCC近似拟合。之前生成的参数、全库误差及视频对照保存在 `outputs/deployment/ipcm-preview/`，其模拟结果不代表当前SmartFilter路线已在硬件生效。

`runtime/lut_preview_*` 继续保留为独立的显示节点观察工具。它完整调用原厂函数并保留返回值；按需记录 RAM 计数和帧元数据，不改变像素。下文记录该观察工具与原厂预览入口的证据。成片 LUT 的 `lut_ram_blob.c`、`lut_live_loader.c` 及196字节配置保持原样。

已有IPCM候选、显示观察模块和SmartFilter候选均为显式手动入口。SmartFilter候选由`smartfilter_ram.bin`、`smartfilter_loader.arm`及`smartfilter-preview.sh`组成；共享库继续用于边界验证。本轮没有安装到实体机身或改启动包。

## 已核实的调用位置

从本地原厂 NX300M 1.15 ELF 重新核对：

- 库：`libcapture-fw-slpcam-nx300.so`。
- `CLiveviewNodeDisplayAdjust::Execute(CCapImage *, void *, unsigned)`：`0x278fc0`，224字节。
- 虚表 `_ZTV26CLiveviewNodeDisplayAdjust`：`0x39c890`；目标入口在`0x39c8ac`，对应指向 Execute 的 `R_ARM_ABS32` 重定位。
- 原厂函数调用 `LiveviewCommon::GetInstance/LiveviewPrint`，然后设置图像宽高、偏移和长宽比例，返回1。反汇编见 [nodes.asm](../scratch/nodes.asm)。
- 原厂函数使用的节点参数在 `self+0x38…0x4c`。观察模块只读取图像头部 `0…0x28`，不访问 `CCapBuffer` 或图像像素。

当前挂接流程：

```text
原厂 DisplayAdjust 虚表入口
  → RAM nx_preview_execute(self, image, data, callback)
      → 原厂 Execute，参数原样传入
      → 可选 RAM 元数据快照
      → 返回原厂结果
```

逐帧路径没有 Shell、文件读写、内存分配、固定等待或 ISP 调用。观察时用一次非阻塞原子竞争保护样本；竞争失败直接跳过记录。`status` 由独立命令读取 RAM，默认只读、不暂停目标线程。

## Claude 已收集的材料及证据边界

以下材料已保存在工作区，后续直接复用，不需每次续接重新扫描：

| 材料 | 内容 |
|---|---|
| `scratch/lvpath.asm`、`scratch/nodes.asm` | 取景路径、显示节点和 SmartFilter 节点 |
| `scratch/smartfilter.asm`、`scratch/suchae.asm` | 原厂效果处理的候选参考路径 |
| `scratch/ade_init.asm`、`scratch/ade_ycc.asm` | ADE 的 IPCS 初始化与 YCC 处理 |
| `scratch/capimage.asm` | 图像、缓冲区字段访问 |
| `scratch/ipcs_open.asm` | IPCS 打开、关闭与引用管理 |
| `scratch/rsz_open.asm`、`scratch/wrap_ctor.asm` | Resize/B2Y 包装器及信号量 |

普通取景已找到的参数链属于 IPCM，成片3D LUT 的已知写表入口属于 IPCS。在已检查接口中没有发现可直接复用的普通取景3D LUT入口；这不能单凭接口缺失扩大为全部硬件能力结论。

“显示前增加IPCS YCC→YCC LUT处理”已有RAM实现。适配器加入B2Y单例的四个信号量，拍照Prepare／Finish挂钩额外阻止新预览并等待在途操作。其他IPCS客户是否参与、实际DMA完成和完整色彩恢复仍须硬件确认。

进一步核对反汇编：`CLiveviewNodeSmartFilter::smartFilter`在`0x27d8fc`构造`ADE_Image`，在`0x27d914`注册flush，在`0x27d924`调用`ADE_SmartFilter_YccProcess`；`ADE_Ipcs_Ycc_correct_Param_SET`在`0x4f20`调用UDD YCC操作。原厂静态调用图未直接连接两段代码；新增RAM挂钩负责桥接。缓存设备行为和IPCS其他客户的协调仍待实机验证。

## 景深／光学预览按键的调用链

核对原厂 1.15 的结果见 [depth_preview.asm](../scratch/depth_preview.asm)。这里的光学预览指电子镜头收缩到设定光圈的景深预览，不能根据画面接近成片就判断使用了成片处理管线。

- GUI 的 `UI_Operate_Optical_Preview`（相机 ELF `0x1af03c`）在两种按键状态下分别向 `UI_Set_Camera_Ioctl` 发出命令 4、布尔参数 1／0，并关闭／恢复镜头 MF permission。GUI ioctl 到捕获接口的中间分发表未在本次逐项追踪。
- 捕获侧 `CCapCmdIf::SetDepthPreview`（`0x1e2248`）调用 `CMessageAdapter::DofPreview(bool)`（`0x1edfb8`），发送开启／取消消息 26／27。`CCmdDepthPreview::handlerBody`（`0x23b9fc`）按消息分别调用 start／cancel，再调用当前状态处理器的虚表 `+0x18`／`+0x1c`。
- `CCaptureStateIdle` 对应两个虚表槽的重定位，明确指向 `CCaptureState::StartPreview`（`0x25fdb4`）和 `CancelPreview`（`0x25fe20`）。前者在闪光灯不充电时调用 `CIrisOperation::StartPreview(false)`；充电时先记录请求。
- `CIrisOperation::StartPreview`（`0x261cf0`）通过条件检查后调用 `CLiveview::DisableImageUpdate`、等待、`CAEproxy::PreviewAe(1)`、`CAWBproxy::Suspend` 和 `C3AController::SetState(3)`。取消分支发出 `PreviewAe(0)`。`onPreviewCompletion`（`0x261f58`）重新启用画面更新；取消完成分支恢复 AWB，并在相应条件下复位 3A 状态和对焦模式。
- `CAEproxy::PreviewAe`（`0x2251f8`）的非测试桩分支调用 `IQ_AE_PreviewAe`（IQ 库 `0x3244e0`）；后者调用 `CAE::PreviewAe`（`0x3054f8`），由该函数发送 AE 消息 `0x50107`。另已核对 `CAE::AeEvent_MSG_Preview` 直接跳转 `CAE::aePreview`（`0x306cbc`），但 AE 消息分发表没有在本次逐项追踪。
- `CAE::aePreview` 设置 PreviewOn／PrevDriveOn；开启时调用 `aeLock`，等待 AE 锁定及系统字段条件，再发送预览完成消息。取消时调用 `aeS1Rel`。`aeLock`（`0x306b54`）更新系统信息、计算捕获曝光参数并进入 AE Lock 状态。

这些已检查的函数体没有直接发起 IPCS 图像操作、切换取景节点路径或调用 3D LUT 写表。代码支持景深预览的曝光与镜头控制解释，尚无证据将其作为原厂 IPCS LUT 取景入口。AE 锁定的全部间接调用和真实运行时管线仍未穷尽，不能据此宣称 IPCS 在此模式下绝不参与。此入口改变 AE／AWB 状态，不能直接将其常驻当作纯 LUT 开关。

### 其他名称相近的预览选项

本地原厂 `usr/share/locale/en/LC_MESSAGES/di-camera-app-nx300.mo` 和 `zh_CN/LC_MESSAGES/di-camera-app-nx300.mo` 中可直接核对以下菜单文案：

| 资源键 | 原厂名称及含义 |
|---|---|
| `MENU_CUSTOM_KEYMAPPING_PREVIEW_OPTICAL` | 光学预览／Optical Preview；对应上面的景深预览控制链 |
| `CAP_FN_FRAMEXPOS` | 构图模式／Framing Mode |
| `MSG_CM_FRAMING_MODE_OFF` | “时时查看的亮度与实际要拍摄的照片的亮度之间保持一致。” |
| `MSG_CM_FRAMING_MODE_ON` | “不管曝光过度或曝光不足保持适当的实时查看亮度。” |
| `MSG_SETTING_FILE_QUICK_VIEW` | “设置拍摄后预览时间。”；另有关闭、1／3／5秒文案 |
| `MENU_CUSTOM_KEYMAPPING_PREVIEW` | 自定义键菜单中的“预览”名称；同组还列有光学预览、一触 WB、一触 RAW+，不能将组名视作独立图像管线 |

因此，用户说的“真实取景”也可能指关闭构图模式后的曝光亮度预览，需要按实际功能区分。上面的景深预览审计不覆盖构图模式。IQ 库已找到 `CAeInfoIF::getAeSysInfoFramingMode`（`0x2f8c34`）和 `CIQDataRepeater::GetFramingMode`（`0x318728`）；本次尚未追踪其全部消费者，菜单说明本身也不能证明切换到 IPCS。

## 构建与离线验证

```powershell
python -X utf8 tools/build_lut_preview.py
python -X utf8 tools/probe_lut_preview_vm.py
```

产物在 `outputs/deployment/lut-preview/`。ARM VM 使用当前构建，不从历史 ZIP 抽取二进制；叠加已核验的实机 MFW `libmisc.so`。使用 Debian ARM 内核、原厂捕获库及显式命名的测试进程，不连接相机或实体卡。

测试执行原厂 DisplayAdjust 函数和图像 setter；仅将 `LiveviewCommon` 的取实例/日志函数替换成测试桩，避免初始化整个相机环境。测试夹具主动把目标虚表页设为只读，检验单槽写入和恢复时保持页权限。模拟帧不是来自传感器。

检查包括：未安装基线、默认透传、观察开关、恢复、复用同一映射重新安装，以及目标进程/线程保持、解除调试附着、帧数据与元数据逐字节一致。实际结果读取 `arm-vm/report.json`；`execution.json`、`serial.log` 和 `guest/` 保留失败现场和测量依据。

当前 ARM VM 的 `report.json` 显示上述透传、恢复和重新安装检查通过，帧及元数据逐字节相同，两个夹具线程均保留，目标虚表页仍为只读。该结果验证用户态挂接与恢复，不验证机身内核、真实帧、ISP 或实时 LUT 色彩。

本轮 ARM 构建和 VM 执行均退出 0，[验证报告](../outputs/deployment/lut-preview/arm-vm/report.json)为 `passed:true`。默认透传、观察、暂停、恢复和重新安装均通过；帧元数据与模拟像素逐字节一致，只读虚表页权限及两个目标线程保持不变，结束时无调试器附着，独立 VM 已停止。本轮未连接相机或实体卡，尚未验证传感器帧、ISP DMA 或实时 LUT 像素处理。

## 命令与恢复

以下是目标 ARM 系统中的命令格式。当前不自动部署，不挂到 MFW autoexec 或按键入口：

```sh
lut_preview_loader.arm status  --pid PID --blob lut_preview_blob.bin
lut_preview_loader.arm apply   --pid PID --blob lut_preview_blob.bin
lut_preview_loader.arm observe --pid PID --blob lut_preview_blob.bin
lut_preview_loader.arm pause   --pid PID --blob lut_preview_blob.bin
lut_preview_loader.arm restore --pid PID --blob lut_preview_blob.bin
```

`apply` 安装透传；`observe` 开启元数据观察；`pause` 关闭观察但保留透传入口；`restore` 关闭观察并恢复原厂虚表项。恢复后代码和状态留在 RAM，避免在途调用访问已释放内存。同一进程和相同构建可以再次安装；不匹配的旧进程、代码或状态会报错退出。`--fixture` 只用于显式测试进程，默认必须是原厂相机 ELF。

加载器复用现有 `live_loader_core.h` 的线程识别和纯系统调用机制；不调用远程 dlopen，不修改原厂指令。虚表项通过 ptrace 的私有页写入，不更改原厂页权限。该机制仍需在机身旧内核验证。

## 下一个验证检查点

按 [SmartFilter验证结果与续接步骤](SMARTFILTER_LUT_PREVIEW.md)先解决关闭原厂滤镜时的节点、工作区及回调，再确认IPCS资源交接和Still参数恢复。上述条件具备后，才在真实IPCS顺序验证直通、单位表和黑白表。

显示观察工具可辅助记录真实取景帧格式与调用频率。SmartFilter／IPCS二次处理现继续沿原厂工作线程、ADE_Image和完成回调调查，采用原厂操作参数控制，不启用旧bit13实验。IPCM全库拟合与该方向的对照依据见 [颜色模型审计](IPCM_COLOR_AUDIT.md)。
