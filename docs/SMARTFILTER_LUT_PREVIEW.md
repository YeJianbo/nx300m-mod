# 单槽SmartFilter三维LUT预览：实现与调用链

当前实现只使用原厂Miniature对应的一个SmartFilter槽位，具体LUT和强度由MOD独立切换。已实现原厂API适配器、独立RAM模块、五个调用槽的事务加载／恢复，以及现有LUT面板、Custom轮换和遥控选择的同步入口。ARM构建已通过；模拟结果见下方报告。

增量包的安装、顺序像素检查和恢复方法见 [候选包说明](SMARTFILTER_CANDIDATE.md)。机身通过 Custom 长按进入“取景预览”子菜单，提供启用原样、单位表、黑白、跟随 LUT、状态和恢复；成功变更后关闭面板并返回取景。打包目录为 `outputs/deployment/smartfilter-preview/sd/`，ZIP为同目录上级的 `nx300m-smartfilter-preview.zip`；保留卡上的现有LUT和开机入口。

机身日志定位到一处面板焦点故障：短按 Custom 的闪现提示仍在显示时打开正式菜单，菜单记录了提示窗口作为返回目标；提示消失后，关闭菜单将焦点退到 `PointerRoot`，后续长按虽然已启动 nxui，却因原实现只查找焦点祖先而退出。现改为返回原厂窗口；焦点缺失时从已显示的原厂窗口树重新查找父窗口。面板仍创建为原厂子窗口，已有其他应用对话框持有焦点时不接管。对应模拟场景并入 `tools/probe_smartfilter_controls_vm.py`。

原厂有独立的SmartFilter取景节点、处理线程、缓冲区状态和完成回调。ADE的YCC色彩处理包装器有 `RGB3D_LUT` 开关，并最终传给IPCS三维表硬件开关。隔离环境执行原厂节点、ADE包装器、初始化与IQ缓存复位；设备像素和cache交接边界使用测试替代，真实IPCS输出仍需实机确认。

证据来自本地NX300M 1.15 ELF、SDK头文件和重新生成的调用图。滤镜算法提取器：[inspect_smartfilter_lut.py](../tools/inspect_smartfilter_lut.py)；扩展调用来源及节点生命周期：[inspect_smartfilter_pipeline.py](../tools/inspect_smartfilter_pipeline.py)、[结果JSON](../outputs/metol/smartfilter-pipeline.json)、[反汇编](../outputs/metol/smartfilter-pipeline.asm)。地址为所属ELF偏移。

## 能否放进原厂滤镜

RAM模块在SmartFilter效果处理处接入YCC→IPCS→YCC桥接，复用原厂工作区和完成回调。加载器只替换Capture库的五个PLT数据槽，不修改原厂指令。候选包已部署到SD卡，真实像素及拍照资源交接仍待实机验证。

GR Neg、GR Pos、富士CC／Astia／Eterna／NC／NN及黑白继续使用现有MOD风格目录，不各占一个原厂编号。启用单槽模块后，原厂选择Miniature，MOD面板或Custom轮换负责切换具体风格和强度。菜单文案目前仍是原厂Miniature，尚未改成“LUT”。

原厂节点当前分派0…14，关闭值为15。RAM候选固定使用liveview模式0，对应ADE效果10、成片内部效果枚举1。`GetEffectMode`实际读取对象`+0x48`，不是UserData编码`0x390001`。其他槽位仍交给原厂。

## 已实现的单槽控制模块

源码：[smartfilter_lut_bridge.h](../runtime/smartfilter_lut_bridge.h)、[smartfilter_lut_bridge.c](../runtime/smartfilter_lut_bridge.c)。构建产物：[libnx-smartfilter-lut.so](../outputs/deployment/smartfilter-preview/libnx-smartfilter-lut.so)。它没有自动安装构造器，也没有改动生产加载器。

```text
一个固定原厂取景模式
  → nx_sf_preview：建立本线程的预览上下文
  → 原厂smartFilter：构造ADE_Image、注册flush
  → nx_sf_effect：读取已暂存的LUT
      → 后端取得资源 → 处理 → 恢复参数 → 释放资源
  → 原厂工作线程释放帧标志、执行完成回调

MOD独立选择风格／强度
  → nx_sf_select：复制、混合到RAM，更新selected_generation
  → 下一次成功处理后更新applied_generation
```

- 一份RAM配置包含风格ID、强度、代次及`3×730×uint16`预览表；每通道729节点加零padding，节点范围0…1023。帧路径不分配内存、不访问文件、不启动Shell、不拟合颜色。
- 0%直接透传帧，不进入IPCS后端。当前非零强度以编译器格式的单位表混合；原生预览颜色域和强度关系仍待硬件验证。
- 只在选定槽位及其线程上下文内改派效果调用。其他槽位、上下文外调用和同线程嵌套原厂调用使用原委托。
- 后端busy或处理失败时，先恢复参数、释放事务，再填充有效的透传输出。恢复失败会暂停后端并报告需要恢复，不把资源交还拍照视作成功。
- 拍照请求先阻止新预览进入；在途帧返回后协调者才能取得拍照门，拍照结束后释放。暂停也会阻止新处理，允许原厂帧继续完成。

原厂API适配器位于`runtime/smartfilter_ipcs.c`：按ID升序非阻塞取得四个B2Y信号量，调用原厂ADE初始化、三通道写表、输入cache提交、YCC处理、输出cache交接，再请求原厂IQ复位／重载，恢复ADE开关、close并逆序释放。4:2:0枚举使用原厂`GetSamType()==0`。这套API调用已经实现，真实DMA错误排空和完整色彩恢复仍需硬件确认。

## RAM入口与MOD同步

源码：`runtime/smartfilter_ram.c`、`runtime/smartfilter_loader.c`；构建：`tools/build_smartfilter_preview.py`。模块为独立ABI，不包含成片196字节配置。

| Capture PLT槽 | 作用 |
|---|---|
| `0x490330` | 原厂smartFilter调用：只在liveview模式0建立LUT上下文 |
| `0x48ed44` | ADE效果调用：在该上下文执行所选9³表 |
| `0x49104c` | B2Y Prepare：先禁止新预览操作，等待在途操作归还 |
| `0x48e824` | B2Y Finish：原厂完成资源释放后允许预览继续 |
| `0x494488` | GetEffectMode：模块启用时把宿主成片效果1映射为0，避免再次套原厂Miniature |

拍照等待使用RAM计数和futex通知，没有固定sleep。预览竞争不到资源时直接完成透传。Prepare失败也保留让路状态，直到真实Finish归还，不能按返回值猜测锁已经释放。

`smartfilter-preview.sh enable`按当前成片选择载入直接`table.d4lut`，并设置RAM后端标志。此后`lut_engine_shared.h`在成功选择、强度变化或恢复原样后调用一次`sync`；面板、Custom和HTTP选择共用此处。帧路径不运行Shell、不读取SD、不拟合。`status`区分requested与applied代次；`restore`恢复五个原厂槽，保留匿名代码映射以容纳已取出的旧函数指针。

加载器沿用已有线程停止／上下文恢复机制，只借用mmap、cacheflush、mprotect等系统调用。原厂文件哈希在相机线程继续运行时检查；暂停线程后重新核对进程生命周期、文件身份与时间、映射、原厂代码和五个调用槽，随后才写入。驻留模块同样核对，没有远程dlopen。切换等待预算从首次暂停预览入场后计算，控制失败会解除临时入场暂停。探针记录实际停线程窗口。所有控制文件都在RAM，不修改开机入口。

### 原厂入口与拍照效果的分离

`CLiveviewCommon::SetSmartFilterMode`（`0x26f108`）在`0x26f13c`写入全局`SetUserData(0x39, ...)`，不是局部取景开关。拍照侧另有`CEffectProcess::processSmartFilter`（`0x2a7e10`），在`0x2a8150`读取`GetEffectMode`，随后分派到原厂ADE滤镜。

RAM模块通过上面的GetEffectMode挂钩分离宿主与Still效果。原厂选择SmartFilter时的内存映射和RAW菜单限制仍存在：本轮没有伪造全局UserData、强开RAW选项或解决关闭滤镜时独立插节点的问题。当前候选适用于原厂宿主槽位路径；不能据此认为普通关闭滤镜的P/A/S/M和RAW菜单已全部接通。

## 普通取景的插入位置

`CLiveviewPathListLiveview::Create`（Capture `0x292be4`）在B2Y和可选LDC之后检查滤镜模式：`0x292ddc`与14比较，模式0…14在`0x292df4`插入SmartFilter；15跳过，并可进入FocusPeak分支。普通关闭滤镜路径需要独立插入节点，不能通过全局伪造滤镜设置影响JPEG。

已由虚表重定位和函数体核对的插入链：

```text
setLiveviewNormalPathSmartFilter [0x293738]
  → vptr+0x14：Base::setLiveviewPathSmartFilter [0x2914b8]
      → vptr+0x18：Base::insertNode [0x2915cc]
          → 原厂PathTable → CorePathCtrl::insertNode → 设置地址
      → Callback::RegisterNode(8, SmartFilter instance) [调用0x291568]
```

`GetSmartFilterMode`（`0x26f67c`）读取`GetUserData(0x39)`，并非简单返回某个可替换缓存字段。`Prepare`将stream mode存到`node+0x68`，将工作地址存到`node+0x6c`；`getWorkAddress`（`0x27e0b8`）请求`GetVirtAddress(0x1000f,0)`。关闭滤镜时这块工作区是否可用尚未证明。内存管理器的`onItemChange_SmartFilterType`（`0x1c6e24`）会根据滤镜及其他设置切换内存映射，不能只插节点后假设工作区已分配。

## 原厂取景滤镜怎样运行

```text
原厂取景路径构建
  → 注册SmartFilter节点（ID 8）
  → CLiveviewNodeSmartFilter::Execute
      → 向原厂消息队列提交图像、节点数据、完成回调
  → SmartFilter::t_Main接收消息
      → 标记原厂视频缓冲区占用、节点busy
      → execute读取原厂滤镜模式
      → smartFilter / selectiveColor
          → 构造原厂ADE_Image
          → 注册原厂CacheFlush回调
          → ADE_SmartFilter_YccProcess
          → 具体原厂滤镜算法
      → 解除缓冲区占用、节点free
      → 成功时执行消息携带的完成回调
  → 后续原厂取景处理
```

| 原厂位置 | 具体证据 |
|---|---|
| Capture `0x2914b8` | `setLiveviewPathSmartFilter`，`0x291568`注册节点ID 8 |
| Capture `0x27cacc` | `Execute`，`0x27cbdc`发送消息 `0xDDD30001` |
| Capture `0x27cd60` | `t_Main`工作线程，`0x27cdb0`接收消息 |
| Capture `0x27ce48`／`0x27ce9c` | 标记`CVideoBuffLock`、`CVideoNodeStatus`占用 |
| Capture `0x27cee4` | 调用节点`execute` |
| Capture `0x27cf28`／`0x27cf7c` | 解除缓冲区标记、节点free |
| Capture `0x27cfd0` | 成功分支调用消息中的完成回调 |
| Capture `0x27d010` | `execute`根据原厂模式分派普通滤镜和selectiveColor |
| Capture `0x27d8fc`／`0x27d914` | 构造`ADE_Image`、注册flush回调 |
| Capture `0x27d924` | 调用`ADE_SmartFilter_YccProcess` |
| Capture `0x27e1d0` | flush回调转交`CLiveviewBuffer::CacheFlush` |

这套机制值得复用。`CVideoBuffLock::setBufferLockFlag`（`0x17ebbc`）和unlock（`0x17ec04`）在检查magic后只写一个字节，没有获得IPCS互斥锁。

### 原厂失败语义

- `Execute`缺少工作区或`node+0x70`为busy时返回true，不排队，也不发完成回调；true不能表示帧已处理。
- `t_Main`复制0x140字节节点数据，先设buffer lock、node busy，再调用`execute`；结束时释放标志，仅在`execute`返回true时发回调。
- 节点数据中，完成回调位于`+0x128`，node status指针位于`+0x130`，buffer lock指针位于`+0x134`。两种标志对象在`+4`检查`0x1234eeff`。
- 原厂`execute`在滤镜关闭值15下返回false，工作线程不发回调。独立预览节点需要自己的启用判定和回调策略。
- 原厂`smartFilter`在`0x27d934`检查ADE结果，非零会在`0x27d960`调用`__assert_fail`。隔离ARM子进程注入UDD错误−17后，实际收到SIGABRT。正式桥接不能将处理失败直接返回这个原厂分支，必须先提供有效的原厂透传输出。
- `TearDown`只清空工作地址。SmartFilter的BringDown虚表槽指向通用`CLiveviewNode::BringDown`（`0x271dac`）；该函数只记日志并返回true，不等待worker。模拟器在busy=1时执行它，busy仍为1。

## 原厂滤镜中的LUT是什么

从 `libdi-alg-smartfilter.so` 的 `ADE_SmartFilter_YccProcess`（`0x2a8c`）扫描到77个可能到达的原厂函数：

- 入口按效果枚举分派 `Miniature`、`Uwha`、`Suchae`、`Damchae`、`Softfocus`、`Defog`等。
- `Uwha`、`Suchae`会打开IPCS并调用 `ADE_Ipcs_Resize_Param_SET`，最终走 `ipcs_one_resize_operation`。
- `Defog`、`Softfocus`调用 `ADE_ProcessLut`（IP platform `0x5cc8`）。该函数先由曲线结点生成256项映射，再按输入字节查表，属于软件一维映射，不是9³ RGB三维表。
- 图中还包含原厂MnF／EP处理。原厂实时效果由多种处理组合而成。

本次直接调用图没有连到 `ADE_Ipcs_Ycc_correct_Param_SET`、`ipcs_ycc_correction_to_ycc_operation`或 `ipcs_3d_lut`。图中的间接虚调用和未加载库另列在JSON中，因此此结果只描述已检查的静态直接调用范围。

## 新确认的完整三维硬件接点

`libdi-alg-ipplatform.so` 已经实现以下包装器：

```text
ADE_Ipcs_Ycc_correct_Param_SET(ADE_Image*)  [0x4bd0]
  → 从ADE_Image取得尺寸、步长、输入／输出YCC地址
  → 读取ADE_IP_switch_RGB3D_LUT
  → 填入ipcs_ycc_correction_to_ycc_param.RGB3D_LUT_SW
  → ipcs_ycc_correction_to_ycc_operation    [UDD 0x4eb38]
      → ipcs_opinfo_set_for_ycc_correction_to_ycc [0x50684]
          → ipcs_hl_3d_lut_onoff            [0x61bd0]
      → 按tile配置并启动YCC输入
      → 等待tile完成
      → 结束配置、关闭DMA及中断
```

不是根据函数名推测，以下绑定已经核对：

| 证据 | 位置 |
|---|---|
| 原厂一字节开关对象 | IP platform `ADE_IP_switch_RGB3D_LUT`，`0x16298` |
| 该对象的GOT重定位 | `0x16288`，`R_ARM_GLOB_DAT` |
| 读取该开关并转成布尔值 | `0x4eac–0x4ecc` |
| 包装器局部操作结构体及字段 | 基址`fp−0x234`，字段`fp−0x94`，相差`0x1a0` |
| UDD读取同一字段 | `0x50810`读取`info+0x1a0` |
| UDD将其传给3D LUT开关函数 | `0x50818`调用 `ipcs_hl_3d_lut_onoff` |
| SDK字段 | `ipcs_type.h`的`ipcs_ycc_correction_to_ycc_param.RGB3D_LUT_SW` |

这里是原厂公开操作参数驱动原厂UDD设置硬件，不需要在Mod里猜寄存器地址或直接拨bit13。`ipcs_3d_lut`仍是写表接口，开关本身不提供表内容。

一个重要细节：`ADE_Ipcs_Open`先调用 `ADE_Ipcs_switch_init`，后者把包括RGB3D LUT在内的六个ADE开关清零；所以不能只在打开IPCS之前把这个变量设成1。原厂初始化还会写入线性Tone和转换矩阵，复用时必须把这部分状态变化也算进去。

## ADE YCC包装器的实际调用来源

已扫描原厂`usr/lib`下非符号链接ELF及相机主程序，共506个ELF，解析错误0。动态符号引用指向`libdi-alg-photoeditor.so`，在该库确认六个直接调用者：

| 调用者 | 函数地址 | 调用ADE YCC的位置 |
|---|---:|---:|
| ADE_Exposure | `0x3430` | `0x349c` |
| ADE_Temperature | `0x4cdc` | `0x4de8` |
| ADE_AdjustRGB | `0x1e1c` | `0x1ec0` |
| ADE_HUE_Control_PCSE | `0x391c` | `0x3a44` |
| ADE_HUE_Control_GSHC | `0x3a5c` | `0x3ac0` |
| ADE_Foggy_Playback_YccProcess | `0x1ee0` | `0x248c` |

另外，IQ库的`CPlaybackEffect::IPCS_Ycc2Ycc`（`0x33b2a8`）在`0x33b594`直接调用UDD的同一个YCC操作。该函数自行open/init并配置参数，忽略操作返回值，末尾返回0，不能用它的返回值认定像素处理成功。

这些来源证明原厂确实使用YCC色彩处理操作；普通SmartFilter到ADE YCC包装器的静态直接路径仍未找到。本次ARM探针中的桥接是测试代码提供的，不是新发现的原厂调用。

## 缓存交接与拍照资源

SmartFilter构造ADE_Image后注册CacheFlush回调，原厂flush最终将地址和长度按页对齐后交给`CMemoryManager_Nx::CacheFlush`。完整检查ADE YCC包装器后，未发现它调用`FlushDCache`；ARM探针也确认，主动flush一次后，包装器没有再次调用注册回调。接入时须明确输入提交和输出完成的cache交接，回调已注册不等于缓存同步已完成。

`ipcs_open`（UDD `0x4bb78`）和`ipcs_close`（`0x4bdb8`）各自短暂持有内部mutex，返回前释放。重复open也可返回0，所以open成功不是覆盖整个操作的独占租约。

Capture包装器另有可复用的同步机制：`CDDWrapperIPCS_B2Y::GetSemaphore`（`0x14b8a0`）提供同一实例中的四个信号量，ID为0、1、2、3，地址分别为实例`+0x00/+0x10/+0x20/+0x30`。B2Y的`PrepareOperation`按启用字段依次取得0…3；Resize的`PrepareOperation`从B2Y单例取得1…3；各自的`FinishOperation`释放相应信号量。这些操作使用共享提供者，不能概括成“两套互不相干的局部锁”。

这仍不等于全局IPCS仲裁：ADE YCC包装器没有加入上述协议，其他IPCS调用者还包括IQ、FD、mm-photo、mmfhal和sif；启用字段也决定是否取锁。各ID保护的具体硬件范围、所有调用者的参与情况，以及Still优先级尚未确认。新增预览不得自行阻塞等待这些信号量后声称已经解决拍照抢占。

### 停流完成通知实际覆盖什么

继续向下追踪后的静态链为：

```text
CDriveBase::stopLiveview [0x248a14]
  → CLiveview::Stop(true) [调用0x248ac4]
  → stopProcess：BringDownNode → videoStreamOff
  → CFrontEndInterface::videoStreamOff [0x1a5918]，提交消息2
  → CFrontEndMsgHandler::messageHandler [0x19ee88]
      → videoStreamOff(bool) [调用0x19f0c4]
          → CFrontEndPP::stopVideoStream [调用0x19fd0c]
          → CVideoEventHandler::stopVideo [调用0x19fd1c]
              → 发事件0x800000，等待gSemVideoStopWait
              → 视频事件线程分派stopSubBlock [调用0x178974]
                  → IPCm::stopVideoStream → IPCm::closeIPCm → LDCm::stopLDCm
                  → 释放gSemVideoStopWait [调用0x17d50c]
      → 消息携带的FrontEndCallback(0,0) [调用0x19f148]
  → CLiveviewCallback::FrontEndCallback [0x27b564]
      → CLiveviewNotification::sendNotify，事件0x14
  → CLiveview::onNotifyCompletion [0x28712c]
      → notifyFromFrontEnd [调用0x287290]
      → 按当前状态进入stopCallbackProcess等分支
```

GOT重定位确认：取景传入的完成函数是`CLiveviewCallback::FrontEndCallback`；`stopVideo`等待和`stopSubBlock`释放的是同一`gSemVideoStopWait`。回调的通知虚表槽、取景接收者的事件0x14分支，以及构造／初始化中的通知注册位置均已保存到调用链产物。

这条确认信号针对上述前端停流动作。`stopSubBlock`没有调用SmartFilter的busy检查、worker等待或IPCS完成等待；SmartFilter的BringDown在隔离ARM验证中也没有排空worker。因此新增IPCS预览仍需独立阻止新帧提交，并确认在途操作结束后再交还Still。

另一个不能忽略的分支：`stopVideo`通过`_fx_semaphore_trytimeout`等待，传入超时参数`0x1388`，失败返回−1；上层`videoStreamOff`在`0x19fd1c`调用后覆盖返回值，未检查该错误，消息处理器仍可发送`(0,0)`完成通知。仅收到原厂完成通知不能证明所有处理已经成功停止。

### Still参数回调的实际来源

`CCpoIpc::IPCS_rawToMain`（`0x190878`）从操作参数`+0x3c`取函数指针，在`0x190ad8`传入B2Y的`PrepareOperation`。后者先按启用字段取上述信号量，再在`0x14e98c`调用这个非空回调，然后配置Bayer→YCC tile操作。这提供了一个“获得原厂操作资源后、开始tile前”的候选位置。

`CIpcProcess::changeParameter2Send`（`0x2a4d40`）在`0x2a4f2c`把`SetIpcsIQParamCallBackFunction`写入上述`+0x3c`。指针由GOT槽`0x4945d0`的`R_ARM_GLOB_DAT`重定位确认；提取器同时校验写入指令。

```text
CIpcProcess::changeParameter2Send
  → stIPCS_Param+0x3c = CIpcProcess::SetIpcsIQParamCallBackFunction
  → CCpoIpc::IPCS_rawToMain
  → B2Y::PrepareOperation：条件取信号量后调用参数回调
      → CapIfDspParamSetIPCs（多项）
      → IQ_IPC_Initialize(0) [调用0x2a2aa4]
          → CIpcProcessor::Initialize [0x347728]
          → [this+4]对象的vptr+4
      → IQ_IPC_Process(0) [调用0x2a2aac]
          → CIpcProcessor::Process [0x3477e4]
          → PreProcess、[this+4]对象的vptr+0、PostProcess
```

已解析`CIpcProcessor`构造器：`this+4`为`CIpcStillParamD4`。其虚表`0x3bfee0`的address point `+4`指向`CIpcParameterList::InitializeParameter`（`0x3473d8`），遍历头指针`+4`和next `+0x10`，调用节点Reset。`CIpcParameterNode::Reset`（`0x350e0c`）将节点缓存索引`+4`置`0xffffffff`。随后Still Update会遍历节点重新更新参数。该遍历及缓存失效已在ARM执行；全部拍摄模式、真实Tone／矩阵／3D表恢复仍需实机确认。

## 按这个思路接入我们的LUT

拟采用的处理结构是：

```text
原厂IPCM生成取景YCC
  → 原厂SmartFilter帧处理框架
  → 原厂IPCS YCC色彩处理，启用3D LUT
  → 交回原厂回调继续显示
```

IPCM继续产生帧，拟由IPCS执行完整三维变换。本轮仅在隔离探针的UDD边界提供了软件9³参考实现，用于核对帧交接和测试表，没有加入机身运行时。

当前接入流程：

1. 显式安装RAM模块，原厂进入Miniature宿主槽位，由原厂分配节点和工作区。
2. MOD选择同步至RAM，ADE操作使用直接9³表，不重复套用成片stock3d组合表。
3. B2Y Prepare阻止新预览并等待在途操作，Finish后恢复预览。
4. 关闭模块时恢复五个原厂调用槽；正常风格切换只更新配置。

已有JPEG LUT仍走原来的IPCS挂钩，RAW路径不变。预览输入已经经过一次成像，不能未经核对就重复套用包含原厂映射的 `table-stock3d.d4lut`；需在已知RGB／YCC转换与Tone条件下选择或编译预览表。

## 隔离ARM验证结果

[probe_smartfilter_vm.py](../tools/probe_smartfilter_vm.py)运行现有私有QEMU ARM环境：原厂1.15用户态库，叠加已核对的MFW`libmisc.so`。内核和硬件仍是模拟环境，不模拟DRIMe IV ISP。探针源码：[smartfilter_fixture.c](../runtime/smartfilter_fixture.c)；[实际报告](../outputs/deployment/smartfilter-preview/arm-vm/report.json)。

实际执行原厂的Prepare、Execute、t_Main、execute、smartFilter、CCapImage getter、ADE_Image构造器及ADE YCC包装器。单槽检查链接本轮构建的控制模块。队列用单消息替代，环境和工作区由夹具提供；IPCS事务、cache记录回调、UDD像素后端及其他原厂滤镜的路由委托由夹具提供。并发用例在pthread中执行原厂worker，让主线程在处理途中提出拍照及切换请求；没有模拟原厂完整多节点调度。

| 检查 | 实际结果 |
|---|---|
| 缺工作区、busy时的Execute | 返回true且未排队 |
| busy时BringDown | 返回true，busy仍为1 |
| 排队后的工作线程 | 处理时两种标志及node busy均为1；回调时均已释放 |
| ADE参数交接 | 宽高、stride、输入输出地址和`+0x1a0`开关符合检查 |
| 软件参考直通 | 64×48合成YCC420帧逐字节一致 |
| 软件参考单位9³表 | 同一合成帧的8-bit YCC最大误差0，输入未改动 |
| 软件参考黑白9³表 | Cb、Cr均为128，亮度误差不超过1，输入未改动 |
| 滤镜关闭值15 | 没有像素操作和完成回调 |
| UDD返回−17 | 原厂滤镜断言失败，隔离子进程SIGABRT |
| 单槽切换与代次 | 单位表→黑白→50%→0%→单位表；切回单位表逐字节相同，0%不调用像素后端 |
| 其他槽位及非预览调用 | 原参数交给原委托，不使用所选LUT |
| 后端busy／出错 | 输出有效透传帧，处理错误后恢复并释放；ADE把UDD的−17归一为−1 |
| 在途帧与拍照请求 | 请求立即阻止新处理，在途帧结束后才能取得拍照门；拍照期间仍完成透传帧 |
| 取得资源途中收到拍照请求 | 跳过新增像素操作、恢复资源；暂停／恢复可用 |
| 参数恢复失败 | 后端暂停，后续帧透传，拍照取得请求返回需要恢复 |

边界探针加入原厂API上传、IQ缓存复位、锁冲突和错误恢复检查，共15项。软件参考假设BT.601全范围、CbCr打包420、RGB三线性9³。实际结果分别保存在[边界报告](../outputs/deployment/smartfilter-preview/arm-vm/report.json)和[RAM装入报告](../outputs/deployment/smartfilter-preview/ram-vm/report.json)。后者使用当前构建的加载器和blob，验证五个PLT槽的装入、切换、成片宿主效果抑制及恢复；UDD像素、cache设备边界和IQ色彩节点Update是明确的测试替代，不代表硬件色彩已验证。

2026-11-01最终运行中，两份报告均为`passed:true`，私有VM已停止。RAM装入检查覆盖在途帧切换、100%／50%／原样、拍照等待与恢复、错误透传、恢复五槽及重新启用；单位表软件参考误差为0，黑白色度为128，重新选择单位表逐字节相同。结束时目标进程`TracerPid=0`，临时入场暂停已解除。模拟停线程窗口：首次装入2867ms，普通配置更新约521–606ms，恢复640–654ms；这些是QEMU测量，不能换算成机身耗时。

## 下一步与当前交付范围

1. 实机顺序验证原样、单位表、黑白表，确认真实颜色域、cache交接和DMA结束。
2. 核对其他IPCS客户、真实Still参数恢复及拍照优先，再评估连续取景和拍照。
3. 继续解决原厂SmartFilter的RAW菜单限制、关闭滤镜时的工作区和独立节点插入；随后改原厂菜单文案。

当前交付包括原厂API适配器、单槽RAM模块、加载／恢复工具、MOD同步脚本、机身控制菜单、调用链及ARM探针。生产JPEG Hook、196字节配置、RAW数据及机身启动入口均未改动。已完成候选包写卡和文件回读，尚未完成实机像素验证。

## 复现

```powershell
python -X utf8 tools/inspect_smartfilter_lut.py
python -X utf8 tools/inspect_smartfilter_pipeline.py
python -X utf8 tools/build_smartfilter_lut_bridge.py
python -X utf8 tools/build_smartfilter_preview.py
python -X utf8 tools/probe_smartfilter_vm.py
python -X utf8 tools/probe_smartfilter_ram_vm.py
python -X utf8 tools/package_smartfilter_preview.py
```

第一份提取器输出77个可达函数及三维开关路径；第二份补全调用来源、虚表和生命周期。原厂颜色数学及20款误差对照见 [IPCM审计](IPCM_COLOR_AUDIT.md)，该拟合路线现暂停。
