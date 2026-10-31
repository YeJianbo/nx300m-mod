# 第二段 PP memory pass

`pp-memory-pass-bank.bin` 是第一次 CSM 录制 Bayer 读出之后，原厂
`cf_pp_capture_frame_read_write_start → pp_rw_dma_start` 调用后保存的完整 PP
寄存器。它是原始证据，解码脚本不会覆盖它。

```powershell
python -X utf8 tools\pp_memory_decode.py outputs\firmware-runtime\pp-memory-pass-bank.bin --output outputs\firmware-runtime\pp-memory-pass-decoded.json
```

## 已确认的配置

三星 GPL `d4_pp_core_regs.h` 的处理子模块偏移需加 `0x4000`。原厂
`libudd.so.3.1` 的 `pp_core_dd_dpc_onoff`、`pp_core_dd_cs_onoff` 和
`pp_core_dd_damp_onoff` 分别访问 `0x4000`、`0x42d0`、`0x4310`，与该基址一致。
不能把 bank 开头的公共／3A 配置当成这些模块。

| 项目 | 真实寄存器／解释 |
|---|---|
| 控制模式 | `0x0008 = 0x22000003`；memory RW mode 2，输入／输出各 4 channel |
| Bayer／位宽 | `0x0000 = 0x01020100`；GRBG，12 bit，IPCM 输出使能 |
| RDMA | `0x3000 = 0x663`，两端口 enable；格式码 6；SECC 4-channel 模式 `0x3038[6:4] = 7` |
| 输入尺寸 | `0x3004 = 0x057e057e`，每个 width 字段 1406；原厂 `PP_CH_4` 分支写入 `image_width >> 2`，完整宽 5624；`0x3008[31:16] = 3714` |
| 每色平面 | 2812 × 1857，有效 packed-12 行 4218 bytes，stride 4224 |
| WDMA | `0x3314 = 0x10000421`；SECC，12 bit，4 channel |
| DPC | `0x4000 = 5`：动态坏点校正开启，静态坏点校正关闭 |
| CS | `0x42d0 = 0x10001`：色彩阴影校正开启；LUT 地址是 PP bank 之外的 `0x90000000` |
| DAMP | `0x4310 = 1`：开启；四路 offset 为 0，gain 为 256 |
| RDMA bit shift | `0x4550 = 0x3001`：开启；所有梯度 64、offset 为 0 |
| 其他处理 | XYS、Smear、HBR、VBR、BG、HRN、VFPN、OBD1–3、OOC1–2、OBR1–2、KNEE 的 enable 均关闭 |

四个平面的寄存器对应来自原厂 SECC setter 和 `pp_secc_addr_set` 中的
`(x parity, y parity)` 定义：

| 平面 | RDMA 地址寄存器 | WDMA 地址寄存器 |
|---|---|---|
| 00 | `0x300c` | `0x3304` |
| 01 | `0x3018` | `0x3324` |
| 10 | `0x3010` | `0x3308` |
| 11 | `0x301c` | `0x3328` |

**这段配置不是 bypass。** HBR、VBR 关闭不能说明整条 PP 都是搬运。
仅复制四个平面会跳过动态 DPC、CS 和位宽处理。

## 已实现的数值子阶段

`runtime/pp_memory_pixels.c` 提供独立函数 `nx300m_pp_expand12_to14`，把
连续 packed-12 样本解包到 16-bit 容器并左移 2 位。它只接受本次原厂
`pp_dd_rdma_bit_shift_on` 对应的线性寄存器配置，拒绝其他边界／梯度／offset、
错误长度和重叠内存。

这是一项基于原厂 bit-shift API 和线性参数的数值模型。现已通过同一实现
接入设备模块的显式输入阶段 API（见下节），但不会自动替代整条 PP，
也不会发送中断。物理硬件的位精确输出尚未核对；不能把该 helper 的构建
通过表述为原厂拍摄成功。

独立 ARMv7 soft-float shared object 已通过 `-Wall -Wextra -Werror` 构建：
`outputs/firmware-runtime/pp-memory-pixels.so`。完整 PP 仍须补齐下列内容后才能
将子阶段用于真实 DMA 输出。

### 四平面实际输入阶段（2026-10-22）

`nx300m_pp_memory_input14(bank, 0xb000, intermediate, samples)` 已作为设备
模块 API 实现。它使用真实 bank 解出四平面地址、行距和尺寸，核对所有
输入映射，再调用 `nx300m_pp_expand12_planes14` 逐行展开。结果按
00、01、10、11 顺序保存为四个连续 `uint16` 平面；每像素数值为原
packed-12 样本左移两位。该中间缓冲位于校正处理之前，不是 WDMA 输出。
原先一维 helper 与新 API 共用 `pp_memory_pixels_impl.h`，没有复制两份算法。

源的 allocation／mapping 生命周期须由调用者在整个调用期间保持稳定。
返回的物理映射是裸指针；该接口不提供并发 unmap／释放保护。目的缓冲须
有独立底层存储，不能通过其他虚拟映射与输入物理内存形成别名。

独立 `tools/probe_pp_memory_task.py` 已完成 ARMv7 soft-float、
`-Wall -Wextra -Werror` 构建和 QEMU 实际执行，退出码 0：

- 从已有 geometry 观测读取四平面 `after_FinishOperation` 的有效末行，
  每行原始 4224 bytes；排除 6-byte 行尾 padding，对 **11248 个真实已保存
  样本**执行新 API，并与 Python 独立 24-bit 解包参考逐样本比较，差异为 0。
  这些字节是已有开发环境软件 MnF split 的实际输出采样，不是物理硬件输出，
  也不是完整真实 frame。
- 用独立合成输入覆盖原厂 5624×3714 几何，经物理映射入口检查全部
  **20887536 个中间样本**。读取全部已映射行，包括已有的预留行字节，
  没有补零、复制边界或把预留行解释为 OB。
- 第四平面未映射、非线性 gradient、目的与输入重叠分别拒绝；前两项核对
  整个中间缓冲未部分写入。原 PP 目的缓冲未变化，IRQ 队列保持为空。

记录在 `outputs/firmware-runtime/pp-memory-task/probe.json`，实际中间结果为
同目录 `expanded-rows.bin`。只构建独立 `pp-memory-device.so`，没有覆盖
生产默认模块、重启主模拟器或修改机身。真实原厂配置仍启用动态 DPC、CS、
DAMP，完整任务继续返回 unsupported；输入阶段可执行不代表这些校正已完成。

### 第二段输出位宽与取位边界（2026-10-22）

已沿 GPL 寄存器／类型定义和原厂 `pp_dd_wdma_set_info`、
`pp_dd_wdma_set_secc_info`、`pp_core_dd_knee_set_info`／manual／onoff
核对输出配置，没有从 DMA 格式码推测像素公式：

| 字段 | 本次值与已确认含义 |
|---|---|
| `0x3314[6:4]` | 2，对应 `enum pp_bit_width::PP_BIT_12`，规定 WDMA 存储格式 |
| `0x3314[0]`／`[10:8]` | SECC 开启／输入 4 channel；不是移位参数 |
| `0x3300`、`0x3320` | 均为 24；原厂 SECC setter 在 `PP_CH_4` 分支将每像素 12 bit 乘 2，写每时钟数据位数，不是右移 24 位 |
| `0x4540[9:8]` | KNEE 输出位宽码 2；该独立码表是 0→12、1→10、2→8 bit，**不等同于** `pp_bit_width` 枚举 |
| `0x4540[0]` | 0，KNEE 关闭；现有代码没有规定关闭时 `[9:8]` 是否仍影响输出 |

KNEE 码表由 preset 分支 `0x2caf0..0x2cb58` 直接确定：selection 0
（14→12）清除 `[9:8]`；selection 1／3（→10）写 1；selection 2／4／5
（→8）写 2。`pp_core_dd_knee_onoff` 仅在 `0x2d368..0x2d384` 改 bit0，
保留输出码；它不执行 CPU 取位、舍入或像素转换。KNEE preset/manual
写入曲线参数也不构成曲线禁用后的硬件取位规范。

在这些已核对的相关定义／setter 中，没有找到独立 WDMA shift、truncation、
rounding 字段。当前仍缺“内部 14-bit 保留哪 12 位”、是否饱和／舍入，以及
KNEE 关闭后的输出规则。不能据此实现 `>>2`、`&4095` 或饱和裁剪中的任一
种，更不能将 12→14→12 视为已证恒等。原始指令保存为
`outputs/firmware-runtime/pp-memory-output-width.asm`；现有解码器加入
`output_quantization`，区分存储位宽和未确认的像素算术，没有新增输出算法。

## 尚缺的处理与输入

- 动态 DPC：GPL 公开热像素阈值、区间、gain 和邻域模式，却没有像素判别、
  替换及固定点取整公式。不能按通用中值滤波擅自替代。当前动态 DPC 的
  gain 参数全为零，也不能据此断言它关闭；独立 enable 明确为 1。
- CS：需保留实际上传的 LUT，并确定采样位置、边缘、插值和定点运算。
  bank 中有 LUT 的 DMA 地址，bank 本身没有 LUT 数据。四路 gain=8192
  不能证明整张空间 LUT 为单位映射。
- DAMP：offset=0／gain=256 呈现单位增益配置，但当前没有像素算术的物理核对。
- MnF 本次配置只拆分 5624 × 3696；下一段 PP 却读 3714 行。每个平面最后
  9 行没有被此次 MnF 写入。已有隔离 probe 以 sentinel 核验这些行保持未写，
  它们的实际生产者和光学黑区语义尚未确认，不能擅自填零或重复最后一行。

`pp-memory-pass-decoded.json` 同时记录 `complete_pp_pixels_implemented=false`
和 `completion_irq_generated=false`。原厂第二次 readout 等待仍不应成功。

原厂配置反汇编保存在：

- `outputs/firmware-runtime/pp-memory-dma-config.asm`
- `outputs/firmware-runtime/pp-memory-corrections-config.asm`

## CS 上传 ABI 与采集位置

以下函数签名来自原厂 `pp_core.h`／`pp_core_type.h`，原厂 ARM 调用约定为
32-bit 指针和 32-bit enum：

```c
/* PP_LUT_CS == 3; sizeof(struct pp_core_lut_load_info) == 12 */
struct pp_core_lut_load_info {
    unsigned int lut_start_addr; /* +0: source virtual address */
    unsigned int lut_size;       /* +4: bytes */
    unsigned int load_timeout;   /* +8 */
};
int pp_core_common_lut_init(int selection, struct pp_core_lut_load_info *);
int pp_core_common_lut_update(int selection, struct pp_core_lut_load_info *);
int pp_core_start_lut_update(int selection, struct pp_core_lut_load_info *);

/* sizeof(struct pp_core_cs_interpolation_lut_info) == 16 */
struct pp_core_cs_interpolation_lut_info {
    unsigned int cs_lut_1_addr;      /* +0: source virtual address */
    unsigned int cs_lut_2_addr;      /* +4: source virtual address */
    unsigned int cs_lut_size;        /* +8: bytes per source */
    unsigned char interpolation_ratio; /* +12: second source weight, 0..100 */
};
void pp_core_cs_interpolation_lut_update(struct pp_core_cs_interpolation_lut_info *);
```

后三个 padding 字节不属于插值参数。保存结构体元数据时，不应把 padding
解释成参数。保存上传数据的准确时点是调用原厂函数之前；保留原始 source
虚拟地址、字节长度和调用类型，不能靠调用返回后的 BMA 地址推断身份。

`libiq-fw-slpcam.so` 的 `CPreProcessorD4::UpdateCsParam` 在 `0x343f88`、
`0x344060` 调用 **`pp_core_start_lut_update(3, info)`**，随后用
`pp_core_wait_lut_update` 异步等待。仅观察 common init/update 会漏掉此更新。

原厂 CS 上传的内部指令链为：

- `pp_core_dd_common_lut_init/update` 的 selection 3 分支把
  `lut_size >> 4` 存入 `0x42e0[20:8]`。`start_lut_update` 复用已有长度，
  不在自己的 selection 3 分支重新写该字段；初始长度为零时不能直接
  使用异步 start 代替原厂先前的 CS 初始化。
- `pp_core_set_mdma_info` 通过 `pp_change_physical_address → bma_virt_to_phys`
  转换 source 虚拟地址，将结果写到 `0x4608`。CS 没有独立 end 寄存器。
- 双源插值入口先把第二源物理地址写到 `0x461c`，把
  `floor(ratio_percent * 255 / 100)` 写到 `0x42e0[7:0]`，再把第一源、长度组成
 临时 load-info，**直接调用 `pp_core_dd_common_lut_update`**。因此双源入口
 还要单独观察，不能假定它会经过公共 common update 转发钩子。
- 两源插值在加载期间设置 `0x42d0[12]`，调用后清除。该 API 没有 CPU 指令
  回写源数据；片上加载／插值结果与源缓冲必须区分。

本次 bank `0x42e0 = 0x000a6e00` 的长度字段是 2670 个 16-byte 单位，
即 **42720 bytes (`0xa6e0`)**。寄存器丢失 API size 的最低 4 位；本次网格
尺寸可独立确认它恰好按 16 bytes 对齐。`0x4608 = 0x90000000` 的当前内存
可以保存为“CS DMA source snapshot”，但缓冲在上传后可能复用，不能据此
确认它就是当前片上 LUT。最新 bank `0x461c = 0`，比例字段为 0。

## CS 源数据布局证据

`libcapture-fw-slpcam-nx300.so::cf_pp_video_cs_lut_size_calculation` 和
`libiq-fw-slpcam.so::CPreProcessorD4::InitCsParam` 的原厂指令共同给出：

```text
columns = ceil(input_width / sample_pixels) + 1
rows = ceil(input_height / sample_pixels) + 1
rows = round_up_to_even(rows)
source_bytes = columns * rows * 8
```

本次 sample=64，columns=89，rows=60，行长=712 bytes，总长=42720 bytes。
每节点包含四个 little-endian `uint16` 系数；`InitCsParam` 在 `0x3432e4`
设置 `0x2000`，然后用四次 `strh` 写入节点 offset 0、2、4、6。这里有
原厂初始化及生成代码依据，不能把数据误读成 packed-12 Bayer 或 RGB LUT。

逻辑网格行 y 对应的物理行索引为：

```text
physical_row = y / 2                  (y is even)
physical_row = rows / 2 + y / 2       (y is odd)
node_offset = physical_row * columns * 8 + x * 8
```

这由 `InitCsParam` 的 `tst y,#1`、偶数分支 `asr y,#1`、奇数分支
`rows/2 + y/2` 和 row-stride 乘法直接确定。系数顺序保留 `lane0..lane3`；
`ApplyCrosstalkComp` 对 offset 0、4 与 offset 2、6 作不同处理，当前没有
足够证据把四个 lane 命名为某个具体颜色顺序。`0x42f0` 的 Bayer=GRBG 也
不能单独证明 LUT 内存 lane 就是 Bayer parity 顺序。

原厂生产者使用 `0x2000` 作为单位系数。这里没有据此宣称硬件插值／乘法
舍入的位精确规则已经实现。

补充证据：

- `outputs/firmware-runtime/pp-memory-cs-upload.asm`
- `outputs/firmware-runtime/pp-memory-cs-generation.asm`

解码脚本支持 `--cs-source <原始上传字节文件>`，先核对精确尺寸，再输出
每 lane 的范围、去重计数、单位系数计数；该命令不会生成或应用颜色结果。

## 实际 CS 上传源与新旧 bank 对照

已读取 `ep-cs-grids-nx300m-cs-upload-lut1-42720.bin`，完整 42720 bytes，
对应 5340 个节点、21360 个 u16。逐 lane 的实际统计是：

| lane | 系数个数 | 最小值 | 最大值 | 不同值个数 | 等于单位系数 8192 |
|---|---:|---:|---:|---:|---:|
| 0 | 5340 | 8192 | 8192 | 1 | 5340 |
| 1 | 5340 | 8192 | 8192 | 1 | 5340 |
| 2 | 5340 | 8192 | 8192 | 1 | 5340 |
| 3 | 5340 | 8192 | 8192 | 1 | 5340 |

这是实际调用前保存的**全单位系数源数据**。由于所有值相同，该数据本身
不能区分颜色 lane 顺序或验证行重排；四 lane 和偶／奇行布局仍来自上述
原厂生产者指令。上传前源快照也不能独自证明片上加载已经完成。

本次 `ep-cs-grids-nx300m-memory-pass-pp.bin` 与原始
`pp-memory-pass-bank.bin` 相比，45056 bytes 中只有四个 u32 不同：

| bank 偏移 | 旧值 | 新值 |
|---|---|---|
| `0x03b4` | `0x05c3d0ff` | `0x05c3d17f` |
| `0x03b8` | `0x05e001aa` | `0x060001aa` |
| `0x03bc` | `0x0224d002` | `0x0224d102` |
| `0x03c0` | `0x0351ffcc` | `0x0352ffcc` |

这些是公共／3A bank 中的字，不是加 `0x4000` 后的 PP 校正配置。当前未为
它们分配未经核对的统计字段名。整个处理页、RDMA／WDMA 配置、common
control 均逐字一致。

`deknee_ctrl` 并非本次新增状态：两份 bank 的 `0x4550` 都是 `0x3001`。
GPL 没有这个寄存器的 enable 字段宏，先前按 bit0 统一筛选不够明确；
解码器现在为每个模块列出 enable 字段的独立依据。这里的 bit0 有实际
原厂函数证据：`pp_dd_rdma_bit_shift_on` 的 `0x26310` 指令设置 bit0，
`pp_dd_rdma_bit_shift_off` 的 `0x26360` 指令清除 bit0。所以它确实是开关，
但操作名称应为原厂 **RDMA bit shift**，不能仅靠 GPL 寄存器名宣称已确认
某种一般 DeKnee 像素曲线。

可复现本次结果：

```powershell
python -X utf8 tools\pp_memory_decode.py outputs\firmware-runtime\ep-cs-grids-nx300m-memory-pass-pp.bin --cs-source outputs\firmware-runtime\ep-cs-grids-nx300m-cs-upload-lut1-42720.bin --compare outputs\firmware-runtime\pp-memory-pass-bank.bin --output outputs\firmware-runtime\pp-memory-ep-cs-grids-decoded.json
```

## 下一段可实现的最小边界

1. **静态 DPC 关闭路径。** 两次 `0x4000=5` 都明确 `static_on=0`、
   `dynamic_on=1`。静态 DPC 子阶段可按真实关闭状态原样通过，但不能将整个
   DPC 模块视为关闭。动态控制 `0x4040=0x01013fff` 还表明阈值为 16383、
   correction mode=1、except-static=1；未知比较和替换公式时，不能从
   “最大阈值／gain 全零”推出动态 DPC 必定恒等。
2. **CS 上传、缓存和系数读取。** 已有证据足以保存本次 source 的精确 bytes、
   根据原厂字段建立 89×60 网格，并按偶／奇行规则读取四 lane 的实际 u16。
   这一步不需要猜测空间插值。普通非均匀 LUT 的像素插值、边界处理和固定点
   舍入仍缺硬件公式。
3. **本次全单位 CS 的受限分支。** 在确认该源成功加载、期间未被后续上传
   替换，且实际 gain／VS／start 配置仍保持本次值之后，可设计全单位系数的
   恒等子路径。仅由这份源数据不能直接发出整条 PP 完成通知；动态 DPC、
   RDMA bit-shift 与其他输入条件仍须分别满足。

本次没有修改运行会话、共享 shim，也没有补写平面末尾 9 行。

## 3714／3696 的原厂来源与 EP 边界复核

这两个高度来自不同原厂接口，已经定位到实际指令：

| 路径 | 实际来源 |
|---|---|
| sensor metadata | `CBayerImageInformationStill` 在 `0x30586c` 读取 mode 3 的 global height，写 `CCapImage+4`；在 `0x3058ac` 读取 effective height，写 `CCapImage+0xc`；在 `0x3058ec` 读取 offset Y，写 `CCapImage+0x14` |
| MnF 几何 | `GetCapImageToMnfImage` 的 `0x152a34` 调用 `getHeight()`（对象 `+0xc`），写 `MnF_image.height`；宽度却在 `0x152a4c` 使用 `getGWidth()` |
| 第二 PP RDMA | `cf_pp_capture_set_ppcore` 在 `0x1aef94` 直接调用 `cf_pp_sensor_get_global_height`，将返回值传给 `cf_pp_ppcore_set_rdma_info` |

现有 mode 3 实际观测是 global height=3714、effective height=3696、offset Y=18。
CSM arm 快照的 SSIF 也明确：OB vertical=(offset 0,size 18)，active
vertical=(offset 18,size 3696)。因此传感器坐标中的 18 行是**顶部 OB 区**。
这不能直接给下游未写内存命名：MnF 输出末尾 18 行不是已证实的顶部 OB 拷贝。

另一个重要事实是 `GetAddrFromCapImage`：入口虽计算了 offset，raw
interleaved address-type 1 的 `0x157368` 分支直接用 buffer base 和一个
stride 得到 even／odd 地址；SECC address-type 2 的 `0x157324` 分支直接
返回四个 buffer 地址。它们没有应用 offset X／Y；应用 offset 的是其他
YCC 分支。因此不能看到 metadata offset Y=18 就擅自让 MnF 从第 18 行开始。
`CFrontEndEventHandler::stillDone` 的 `0x1a4bcc..0x1a4be0` 还会在 path!=3 时
把对象 offset 清零，但不改 height。当前没有记录该瞬间完整对象，不能把
这一分支的执行或上游元数据变换当成已观察事实。

### 检查硬件是否另写 halo／padding

不能仅用软件 helper 的 3696 行循环证明硬件也只写这些行。本次重新核对了
48-word 原厂 setup 参数、EP bank、原厂 setup/DMA setter 与 GPL 结构说明：

| 原厂配置 | 值与依据 |
|---|---|
| overlap 参数 | `MnF_parameter+0x18` 为 0 |
| core 输入／输出 tile 宽 | `EP+0x4034 = 0x00800080`，两者均 128；原厂 `MnF_set_image_information` 仅在 overlap 为 2／4 时扩展输入 tile 宽 |
| core image size | `EP+0x4038 = 0x0e7015f8`，宽 5624，高 3696；原厂 setter 从 input1.width／height 直接写入 |
| tile 计数 | `EP+0x417c = 44`，原厂公式 `ceil(width / tile_size)`；这是横向 strip 数，不是纵向 tile 数 |
| RDMA 高度 | `EP+0x8048 = 0x0e700000`，3696；`MnF_set_bayer_spliter` 把 input.height 原样传给 `ep_dma_set_rdma` |
| RDMA auto geometry | `EP+0x8064 = 0x10000`；GPL `auto_sdw` 只替换 Start X／Width，没有自动 height 扩展字段 |
| WDMA 垂直重复 | `EP+0x8140 = 0x220`，`vrepeat[19:16]=0`；GPL 规定每输入 HSYNC 写 `vrepeat+1` 次，当前是 1 次 |
| 运算 | mode 20 被原厂显式转换为 bypass 14 + bit31 split；没有调用 convolution Bayer 配置路径 |

这里没有发现请求纵向 9+9 行 halo、bottom padding 或写高 3714 的配置。
横向最后一个 strip 的不足 128 像素，与这 18 行差异无关。现有程序证据
支持按请求的 3696 行拆分；它仍不等于已经测量物理硬件的实际 DMA 写范围。
`ep-mnf-config-bank.bin` 是已有退出后非原子快照，最新会话 48-word 参数再次
给出 height=3696、tile=128、overlap=0；不能把旧 bank 当作最新会话原子快照。

在已检查的 `CRawProcess::frameSubProcess` 分支中，原厂取得新 SECC buffer，
设置地址后经 `CCpoMnF` vtable 调用 Bayer split。`CCpoMnF` 的 ELF relocation
确认 vptr+0x48 对应 `bayerSplit`。未发现该调用前有填充末尾 18 行的 CPU
像素 copy。该局部负面结果不能排除尚未记录的别处生产者或硬件行为。

### 下一步应采集什么

下一次开发拍摄只需增加观测，不改原厂参数：在 Bayer split 前后记录输入／
输出 `CCapImage` 的 GWidth、GHeight、Width、Height、OffsetX、OffsetY 和
四个 buffer base，并保存输出每平面的首／末边界行及其原有字节。这样可
区分对象有效区域、全局预留区域，以及 split 前已有的数据。若要证明硬件
实际是否多写，仍需真实硬件 DMA 输出或可靠硬件规范；单独把软件输出与
相同拆分模型比较无法给出这个证据。在此之前不改变 MnF 输出高度、不跳
18 行、不补零、不把已有未知字节称为 OB 数据。

证据文件为 `pp-memory-height-{chain,producers,sensor}.asm`、
`pp-memory-image-fields.asm`、`pp-memory-raw-{subprocess,image-adjust}.asm`、
`pp-memory-after-pp.asm`，以及原有 `ep-mnf-{setup,split,dma}.asm`。

### 已构建的实际 split 边界观测（2026-10-20）

`runtime/capture_geometry_trace.h` 接入 `startup_trace.c` 的专属编译开关；
`tools/build_capture_geometry_trace.py` 仅构建
`outputs/firmware-runtime/startup_trace_geometry.so`，不会覆盖生产
`startup_trace.so`、部署到原厂 rootfs 或操作运行中的 namespace。

真实 ELF 的 `CCpoMnF::bayerSplit` 从 `0x18d67c` 开始，内部在
`0x18d790` 经 PLT 调用 `CDDWrapperMnF::SetOpParam`，在 `0x18d7cc`
经 PLT 调用 `FinishOperation`。前者调用前的 4-word 参数记录中，mode=17、
input 位于 `+4`、output 位于 `+0xc`。因此无需改写 vtable 或代码页：
即使上层 `vptr+0x48` 直接调用原厂函数，这两个内部 PLT 边仍能被观察。
hook 同时核对调用者库名和相对库加载基址的返回地址
`0x18d794`／`0x18d7d0`，只处理这个真实 split。后快照位于原厂 `FinishOperation` 返回后、`Close`
释放 wrapper 信号量之前。它不将 `FinishOperation` 返回视作硬件写入成功。

启用变量为 `NX300M_CAPTURE_GEOMETRY=1`。每次操作使用线程本地关联，
在 `/var/run/nx300m-geometry-<pid>-<sequence>-*.txt` 保存前后元数据：
六个几何字段、raw format/pack/stride/address type，以及物理／虚拟各四个
buffer base。二进制文件只使用已核验的虚拟地址，保存首行、对象有效末行、
全局末行和对象有效区之后的预留尾区；输出还另存按**本次输入 Height**
定位的 `split_effective_last`／`split_reserved_tail`。这能区分输出描述符
有效高度与本次实际请求的 split 高度。SECC 的实际 plane 行距为对象
`RawStride/2`：原厂 `calcImageOffset` 在 `0x172150..0x172168` 以
`RawStride*GHeight/4` 计算各 slot 的预留大小。本次对象 RawStride=8448
对应 plane 行距 4224。行数据包含该行距中的 padding。
raw type 1 按单个 interleaved mosaic buffer 采样，type 2 按四个 SECC
slot 采样；不把 metadata offset 加到它们的地址上。

元数据逐项记录文件地址、row、stride、rows、bytes 和读取状态。读取前检查
`/proc/self/maps` 的可读 RAM 映射；原厂可读写栈允许同时带执行标志，
只读可执行代码页及除 SMA/BMA/zero 之外的设备映射仍被排除；
随后以不超过 1024 bytes 的私有 pipe 复制处理失效指针，不直接解引用
相机对象或像素指针。未知格式、过大／越界范围和未映射内存记录跳过，
不猜物理地址别名。原调用、原始 r0 和 errno 保持透传。

已用以下命令完成 ARMv7 soft-float 构建（`-Wall -Wextra -Werror`），并核验
真实调用指令、getter 字段偏移与导出 hook：

```powershell
python -X utf8 tools\build_capture_geometry_trace.py
```

ABI 证据为 `capture-geometry-trace-abi.asm` 和
`capture-geometry-trace-build.json`。首次独立开发拍摄已到达 MnF split，
但旧版未输出 geometry 文件，也没有入口诊断，故无法区分未进入 hook、
调用者符号 gate 未匹配或安全读取失败。修订 2 增加一次加载诊断及两个
hook 各前 16 次 gate 诊断，列出 enabled、调用者库／offset／最近符号、
参数读取状态和 mode；映射读取失败也输出原因。调用者 gate 改为上述
精确库内指令地址，不依赖 `dladdr` 最近符号的命名。

修订 2 的实际独立拍摄日志已定位两个拒绝条件：loader 报告的库名为
`/usr/lib/libcapture-fw-slpcam.so`，而原参数所在 `0x2effa98c` 线程栈
带可读写执行权限。修订 3 的构建工具通过 WSL `readlink -f` 核验通用库名
与 `libcapture-fw-slpcam-nx300.so` 均指向同一真实 ELF，hook 接受这两个
准确 basename；映射检查允许可读写 RAM 同时带执行标志，仍保留设备
映射拒绝、只读代码页拒绝和 pipe 安全复制。修订 3 已在独立开发拍摄中
实际触发，结果见下节。继续采集时需取回同一 sequence 的 before／after
文件；预期每个 sample 为 `status=saved`。若状态是
`unmapped_or_excluded`，先核对该运行环境实际映射，不能把缺失文件当作
未写证据。即使前后尾区逐字一致，也仅说明这次开发运行观察到的 CPU
可读内存没有变化，不能据此替代物理 MnF DMA 的写范围证据。

### 修订 3 实际对象与边界字节（2026-10-20）

本次原始证据为 `outputs/firmware-runtime/capture-geometry/` 下
`nx300m-geometry-63-1-*` 的两份 txt、56 份 bin，以及保留的
`*geometry-r3-20261020T035327.972391Z.strace.log`。日志确认 mode 17
参数读取成功，精确调用点 `caller_ok=1`，前后快照关联同一 wrapper；
两快照之间明确执行 `EP MnF software Bayer split`。此后仍触发原有
JPEG `mybuf.size > 0` 断言，不能把观测成功称为整条拍摄／JPEG 已完成。
前两次无样本运行和修订 2 的拒绝诊断日志均保留。

`tools/analyze_capture_geometry.py` 已读取全部 56 个 `status=saved` 文件，
核对实际长度、地址与行距计算，比较 28 对边界，并检查各重叠样本逐字一致。
结果保存在同目录 `capture-geometry-analysis.json`，可复现：

```powershell
python -X utf8 tools\analyze_capture_geometry.py
```

两份对象均在 split 前后保持以下实际字段，不能用 sensor 初始 metadata
替代这个调用瞬间的值：

| 对象 | GWidth | GHeight | Width | Height | OffsetX | OffsetY | RawStride | AddrType |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| input | 5624 | 3714 | 5546 | 3696 | 0 | 0 | 8448 | 1 |
| output | 5624 | 3714 | 5546 | 3696 | 78 | 18 | 8448 | 2 |

两对象 `ImageFormat=1`、`RawPackBit=2`，四组物理／虚拟 base 也前后一致：

| slot | input physical | input virtual | output physical | output virtual |
|---|---|---|---|---|
| 0 | `d1fb0000` | `1520f000` | `d3db9000` | `17018000` |
| 1 | `d272b080` | `1598a080` | `d4534080` | `17793080` |
| 2 | `d2ea6100` | `16105100` | `d4caf100` | `17f0e100` |
| 3 | `d3621180` | `16880180` | `d542a180` | `18689180` |

input 仍按 raw type 1 的 interleaved mosaic 从 slot 0 采样；这里记录
四个 getter 对应字段，不将其余 slot 猜成四个实际输入平面。

输出每平面行距 4224，0-based 有效末行是 1847，全局末行是 1856，
预留尾区为行 1848–1856，共 9 行、38016 bytes。实际逐字比较：

| output plane | 有效末行变更 bytes | 其中 padding 变更 | 9 行尾区变更 bytes | 尾区调用前／后 |
|---|---:|---:|---:|---|
| 0 | 4160 | 0 | 0 | 全零／全零 |
| 1 | 4075 | 0 | 0 | 全零／全零 |
| 2 | 4157 | 0 | 0 | 全零／全零 |
| 3 | 4149 | 0 | 0 | 全零／全零 |

这些有效末行在调用前也是全零；调用后变化均位于每行 4218-byte packed-12
payload 内，余下 6-byte padding 没有变化。输出首行、全局末行在前后也均为
全零且相同。输入首／有效末／全局末／尾区四份采样前后全部相同；输入尾区
18 行 152064 bytes 中有 149602 个非零 bytes，因此本次输出全零尾区也
不是把这份输入尾区按字节复制过去的结果。

本次证据确定了实际对象几何、原有尾字节和有效末行发生变化的位置。
**输出尾区在调用前已为零，故相等无法区分“未写入”和“写入相同的零”。**
这次执行的是开发环境中的软件 split；既不能据此证明物理 MnF DMA 的写范围，
也没有确认原厂尾区生产者或将它命名为 OB 数据。没有补写尾行、修改高度或
替换动态 DPC 算法。

## 动态 DPC 的 CPU 参考实现检索

新增 `tools/pp_memory_find_cpu_dpc.py`，只读扫描原厂顶层 `usr/lib/*.so*`
和相机主程序，检查了 506 个 ARM ELF 的动态符号。结果保存在
`pp-memory-dpc-symbol-inventory.json`；检索不覆盖不同命名、剥离符号的函数
或嵌套插件，不能据此证明固件任何地方都不存在参考实现。

找到并核对的相关候选：

- `CPreProcessorD4::SetDynamicDpc(bool)`：原厂根据模式、AE 和 dark ratio
  选择／缩放 16 项参数数组，固定设置 correction mode=1、except-static=1、
  threshold=16383，最后调用 `pp_core_dpc_dynamic_set_info`。这是**硬件参数
  生成器**，没有读取和替换图像邻域像素。
- `libudd::pp_core_dd_dpc_dynamic_set_info`：把结构参数写入 HP／range／gain
  寄存器，不能提供硬件内部比较、插值、邻域替换或取整公式。
- `CIpcAdjust::FindDefectPixelStill`：确实有 CPU 读取图像、均值／方差和
  坏点统计代码，但用于工厂缺陷检测／生成校正数据，并调用
  `IQ_WriteDataToFlash`。它不是运行中的 PP 动态坏点替换实现。
- `libd4c` 的 `d4c_dpc_*`：是另一个 D4C 硬件模块的寄存器配置接口，
  `d4c_dpc_ctrl_set` 调用 `d4c_register_write`，不能混用为 PP CPU 算法。
- `libRedEye::DoDefectCorrection`：是真实 CPU 像素算法，但唯一检出的
  直接调用来自 `GeCorrectRegions`，并使用 `GeImageCrop` 等红眼区域处理。
  没有证据表明它对应 PP 动态 DPC，不能因函数名相似而移植。

现有原厂代码足以复现参数生成及真实寄存器上传，**不足以恢复动态 DPC
像素算术**。继续真实像素路径所缺的是该 PP 模块的算法规范、原厂硬件
输入／输出对照，或一个被确认调用于同一 PP 阶段的 CPU 参考函数。
没有这些证据时，保持动态 DPC 未实现，不用通用中值滤波替换。

对应反汇编：`pp-memory-dpc-param.asm`、`pp-memory-dpc-register-setter.asm`、
`pp-memory-d4c-defect.asm`、`pp-memory-redeye-defect.asm` 与
`pp-memory-redeye-defect-callers.txt`。

## CS LUT MDMA 的统一启动 ABI

### 原厂拍摄集成运行（2026-10-21）

进一步在 `nx300m_pp_memory_task` 开始时保存独立缓存，单文件头和payload
在缓存锁内取自同一generation；采集器在停止会话前保留该文件与任务描述。
`capture-color/cs-memory-loaded/cs-cache.json` 实际读回 generation3、42720 bytes、
89×60节点，与PP任务源地址及尺寸相符；四路全部为8192。后续generation4
复用同一源地址，但不会覆盖这个执行时快照。新增
`tools/inspect_pp_cs_cache.py` 按原厂偶/奇行布局提供节点读取，并检查头、
payload和任务尺寸；它不赋予四路未经确认的颜色名称，也不执行像素校正。

此次设备构建和原厂采集均退出0，仍在JPEG空缓冲断言结束，没有新照片。
先前 `capture-color/cs-memory` 是声明顺序编译失败后误启动的旧设备运行，
不能作为新快照验证；其说明与原始记录保留。修正后有效记录为
`capture-color/cs-memory-loaded`，主会话身份保持不变。

`tools/probe_capture_color.py --output-dir outputs/firmware-runtime/capture-color/cs-dma --capture-seconds 60`
实际启动独立 namespace `31066`，执行原厂拍摄流程。日志依次记录源物理地址
`0x90000000` 上的 4800、42240、42720、5456 字节装载，generation 为1至4；
每次成功都来自原厂调用，未注入 LUT 完成事件。EP MnF 软件拆分随后执行，
第二段 PP 仍报告 `pixels unsupported`（阶段掩码 `0x5e`），最终触发原厂
`DscJfif::EncodeJfif: mybuf.size > 0` 断言，没有新 JPEG。

本次采到了原厂 Gamma、RGB2YCC、CCM 三类参数，未启用自定义风格。
工具退出0表示完成观察与清理，不表示拍摄成功。记录及原始日志在上述目录，
`capture.json` 确认独立会话已停止、主会话 `28897` 身份未变。

已通过隔离验证的设备库同步为常规 `outputs/firmware-runtime/device_preview.so`，后续新建会话会加载此实现。探针与常规构建均保留 `-Wl,-z,nocombreloc`，适配原厂 glibc 2.13 的重定位处理。当前主会话 `28897` 仍使用其启动时的私有库，未为这次设备更新重启。

原厂 `libudd.so.3.1` 的 `pp_core_lut_load_enable`（`0x279b0`）接收
`r0=load_mask`、`r1=on/off`。它先执行 `ioctl(0x7413)` 清除 LUT completion，
再读 common `+0x10`；on=1 时先清 mask 写回、再置位写回，构成启动沿。
on=0 也会清 completion，然后清启动位。common init、common update 和
start update 都经 PLT 调用这一入口，可统一观察真实启动，不能在顶层
common update 返回后才补完成（它内部已经等待过）。

CS 分支使用 load_mask=`0x1000`、IRQ=`0x40`。source 物理地址来自
`+0x4608`，传输字节数来自 `+0x42e0[20:8] * 16`；
`+0x42d0[12]` 表示双源插值装载。原厂 `pp_core_dd_wait_lut_update`
（`0x244e4`）等待后调用原 disable、清 IRQ 与 CS update bit，需保留这些
指令及返回值。GPL `d4_pp_core_dd.h` 明确 CS load-done 为 bit6，
`d4_pp_core_dd.c` 的 ISR 将它投递到独立 LUT completion，不产生 WDMA、
RDMA 或像素完成通知。

`runtime/pp_cs_lut.h` 已在该统一入口执行单源 CS 字节传输。先透传原函数，
保留其 raw r0 和 errno，再在原调用者进入 wait 之前检查编程后的源地址、
长度和单源状态。仅接受仍存活的 BMA allocation 内完整、已映射范围；
持内存生命周期锁用私有 pipe 分块读取，所有字节先进入 staging，完整
成功后才提交独立缓存并通过已有 `pp_dispatch(0x40)` 投递 LUT completion。
未直接改写 wait 返回值，没有投递 RDMA／WDMA 完成或帧队列消息。

`nx300m_pp_cs_lut_read` 按 `runtime/pp_cs_lut_state.h` 的 ABI 提供缓存副本及
source、bytes、generation、valid、error；返回的是已装载字节，不是源缓冲
指针。原 disable、源缓冲改写或释放不改变已有缓存；新一次不支持或失败
的装载使当前缓存失效，避免后续消费者误用上次内容。双源插值、组合
load mask、零长度及无效／已释放源不产生完成。缓存容量按原长度字段
上限为 131056 bytes；实际传输使用寄存器中按 16 bytes 量化的长度。

缓存与现有 PP register bank 同为进程内设备状态。关闭一个设备 fd 或
关闭 IRQ 不等于硬件掉电，因此没有因这些操作清缓存；新进程从无有效
缓存开始。本次没有实现或验证物理 PP 掉电／复位时 LUT SRAM 的保留规则。
此装载没有计算双源系数插值、空间插值或像素乘法。

### 已验证的 CS 装载生命周期

```powershell
python -X utf8 tools\probe_pp_cs_lut.py
```

该工具仅构建 `outputs/firmware-runtime/pp-cs-lut/device.so` 和隔离 probe，
在独立 mount／network／IPC namespace 中调用未修改的原厂 libudd；
不覆盖生产 `device_preview.so`，不启动相机或操作主会话。ARMv7 soft-float
设备与 probe 均通过 `-Wall -Wextra -Werror`。`pp-cs-lut/probe.json` 的
实际退出码为 0，记录：

- 实际 `ep-cs-grids-nx300m-cs-upload-lut1-42720.bin` 的全部 42720 bytes
  复制相同；异步 start 之前先调用原厂 common init 设置长度。
- 原厂 common init、common update、start／wait 均走统一入口；非均匀
  重用字节也逐字相同。源在 start 后被改写、以及后续释放后，已装载缓存
  均保持原值。
- 原厂 wait 消费一次完成；再次等待超时。原 disable 清启动／update 位，
  保留已装载字节。帧事件队列始终为空。
- 双源装载返回未支持状态，已释放但仍有映射的 BMA 源返回无效地址；
  两者都使缓存失效、原厂 wait 超时且没有虚构完成。

首次 probe 曾在未初始化长度时直接 async start，因 `0x42e0=0` 正确拒绝
装载、退出码为 1；随后按原厂先 init 再 start 的顺序修正 probe，并修正
本文此前把 async start 也列为长度写入者的描述。最终成功仅证明软件
CS 字节装载和原厂 completion 生命周期；没有执行 PP 像素处理，没有
解除动态 DPC、输出量化及后续 IPCS 的缺口，也没有生成新拍摄 JPEG。

### CS 网格与参数算术的证据边界

本节只核对 CS。已有头文件、寄存器 setter 和 CPU 参数生成器足以恢复
节点读取与参数编码，尚不足以新增 CS 逐像素执行分支。

`libiq-fw-slpcam.so::CPreProcessorD4::InitCsParam`（`0x34316c`）提供以下
精确布局证据，指令见 `pp-memory-cs-generation.asm`：

- `0x3431b8..0x343224` 计算 `columns=ceil(width/64)+1`、
  `rows=round_up_even(ceil(height/64)+1)`，每节点 8 bytes。
- `0x3432c8..0x3432fc` 根据逻辑行奇偶分配物理行，并在节点 offset
  0、2、4、6 写入四个 little-endian u16。对于合法的节点索引，读取地址为
  `((gy&1) ? rows/2+gy/2 : gy/2)*columns*8 + gx*8 + lane*2`。
- 标准径向系数生成分支的 `0x343784`、`0x343794` 分别使用 `gy<<6`、
  `gx<<6` 再减去图像半高／半宽，证明其节点逻辑位置按 64 个 mosaic
  像素递增。该节点位置不等于已确认硬件采样相位或输入像素到节点的公式。
- 本次原始 bank 对应 89×60 节点、42720 bytes。`pp_core_type.h` 的
  `SAMPLE_POINT_64` 也明确间隔单位为像素；没有证据把这里的64改成
  每个 SECC 色平面上的64个样本。

`libudd.so.3.1::pp_core_dd_cs_set_info`（`0x2b628`）的参数编码可直接
恢复，指令见 `pp-memory-corrections-config.asm`：

| 编码 | 精确证据 |
|---|---|
| 输入区域终点 | `0x2b6c4..0x2b71c` 将 `h_start+h_size`、`v_start+v_size` 写入 `0x42d8` 的两个13-bit字段 |
| 区域起点 | `0x2b730..0x2b768` 将 `h_start`、`v_start` 写入 `0x42dc` 的两个13-bit字段 |
| gain 百分比 | `0x2b7d4..0x2b7f0` 读取 `pp_core_cs_info.gain_ratio`，计算 `floor(percent*8192/100)`；随后写入四个14-bit gain字段 |
| VS 参数 | `0x2b7ac..0x2b7d0` 将 H／V 两字段均设置为4096 |

`pp_core_type.h::pp_core_cs_info` 规定 gain_ratio 为0–100%；GPL 宏将
`0x42e8` 两字段命名为 R／Gr gain，将 `0x42ec` 两字段命名为 Gb／B gain。
原 `ep-cs-grids` bank 的 `0x42dc=0`、`0x42e4=0x10001000`，四个 gain
均为8192。以上能确认寄存器参数编码，**不能确认硬件如何把这些参数与
LUT、像素相乘或混合**，也不能从寄存器颜色名称推出上传 lane 的颜色顺序。

原厂 CPU 代码还提供两类容易与硬件像素算术混淆的证据：

- `InitCsParam` 的 `0x3437c0..0x3437d4` 在生成上传节点时减去单位值
  `0x2000`，进行乘法和逻辑右移13位，再加回单位值；之后乘 capture gain，
  以 `ubfx #8,#16` 取出上传系数。这些是现有 CPU 生产者的32-bit整数
  运算，不是硬件对 Bayer 像素执行的公式。
- 本轮只读核对实际 ELF 的 `CIpcMath::GetInterpolatedValue2D(const
  unsigned short*,int,int,int,int)`（`0x349f48..0x34a00c`）：有效非负
  坐标使用4位小数、四邻点权重、权重和256及整数除法；它的 int 重载
  位于 `0x349e94`。`ApplyCrosstalkComp` 在 `0x342974`、`0x342990`
  调用 u16 版本处理31×21校准表；`InitCsParam` 在 `0x34393c`、
  `0x343a00` 调用 int 版本处理13×13表。这些调用生成或修改上传节点，
  不能用来证明 PP 对89×60网格采用相同插值精度、顺序或边界处理。

`ApplyCrosstalkComp` 的 `0x342994..0x342a6c` 对节点 lane0、lane2
应用不同校准表，lane1、lane3使用共同基值；当前没有足够的颜色标识或
调用证据把它们分别命名为 R／Gr／Gb／B。实际全单位上传数据也不能
区分这些排列。

继续实现 CS 像素输出仍缺四项：上传 lane 到 Bayer 颜色／坐标奇偶的映射；
输入像素坐标与 start／VS 参数到网格采样位置的关系；硬件空间插值的
权重精度、边界和舍入；LUT 与 gain 作用于像素的运算顺序、截断和饱和。
可以依据上述证据读取已装载节点或描述参数；成功装载全单位 LUT 不能
代替这些缺失的硬件算术证据。

## 修订 4：缓冲池交付至第二 PP 的来源观测

`capture_geometry_trace.h` 增加两个限定原厂调用点，仍只读取内存并透传原函数：

- `CRawProcess::frameSubProcess` 在 `0x2ae178` 经 PLT 调用
  `CCapImage::setVirtualAddress`，返回点 `0x2ae17c`。原 setter 只计算并设置
  虚拟地址和缓冲大小；在其返回后保存 output 对象及边界字节，文件 phase 为
  `after_pool_assignment`。这比已有 split 前快照更早，能观察池缓冲刚交给
  output 时的实际数据。
- `CFrontEndPP::stillBayer` 在 `0x1a9028` 经 PLT 调用
  `cf_pp_capture_frame_read_write_start`，返回点 `0x1a902c`。在透传该启动函数
  之前，读取已配置的 PP RDMA 地址、尺寸、格式和 stride，并与最近完成 split
  的 output 四路物理地址逐项匹配；匹配后保存 `before_second_PP`。真实源字节
  通过已有物理映射查询读取，同时保存原 output 对象字段。该关联可跨线程，
  不能把相同地址自行解释为硬件已经写入。

此前候选 `cf_pp_capture_set_rdma_info` 在 `0x1ae040` 只保存全局描述符，
没有写最终 RDMA bank，因此不以它返回时的旧寄存器作为启动配置。
`libudd::read_pp_core_register` 的 `0x27590..0x27598` 直接读取
`PP bank base + caller byte offset`，不额外加 `0x4000`。builder 核对这条
寻址、两个 PLT 调用、pool 参数传递、已有 split/getter ABI 与导出符号。
修订 4 的 ARMv7 soft-float 构建已通过 `-Wall -Wextra -Werror`；专属产物
仍为 `startup_trace_geometry.so`，不覆盖生产 `startup_trace.so`。

`probe_capture_color.py --capture-geometry` 复用独立拍摄环境并收回私有
geometry 文件；`--strace` 可为有界启动故障诊断保留 guest syscall。
工具在运行中保存相机线程 stat／wchan／syscall／kernel stack 与实际 maps。
分析器同时识别修订 3／4 日志；比较要求实际地址、行号、stride、长度一致，
实际 PP 源比较另核对物理地址。字节相同仍不能区分未写与写入相同值。

最初两次实际运行保留在 `capture-geometry-r4`（namespace 20390）与
`capture-geometry-r4-startup`（24729）。第二次 syscall 日志实际出现 18855
次 `sched_yield`，两次均未到达 geometry 观测点。随后
`capture-geometry-r4-lockdiag`（26034）的有界诊断确认同一 tid `0x48`
持有 memory lock（site `0x119`）又等待此锁（site `0x1ba`）。这三份失败
运行的原始日志、可取得的线程记录与 maps 保留，不计入来源采集成功。

PWM 初始化锁修复后，`capture-geometry-r4-lockfix` 的独立 namespace 27092
在原定 60 秒拍摄窗口内实际完成全部四阶段采集。中途原厂自行等待曝光，
外部下降沿触发有限 PWM 计数后继续；没有临时改计时、补发完成事件或延长
窗口。原始 PWM 状态为该目录的 `nx300m-pwm-state.bin`。最终仍是原厂
JPEG `mybuf.size > 0` 断言，没有新照片。上述各 `capture.json` 均确认
独立会话停止、主 namespace 28897 身份未变。

实际 sequence 为 `nx300m-geometry-72-1`，四份 txt 和 **112 份 bin** 全部
读回，分析保存在 `capture-geometry-r4-lockfix/geometry/capture-geometry-analysis.json`。
分析器首次遇到原厂 `%p` 的 `(nil)` 表示导致解析退出 1，现已正确识别为
零；修复后直接读取同一批保存字节、退出 0，没有重新采集。

| 实际阶段 | bin 数 | 四路每路尾区（plane row 1848–1856，38016 bytes） |
|---|---:|---|
| 池指针刚交给 output | 16 | 全零 |
| `SetOpParam` 前 | 28 | 全零，与交付时同一虚拟范围逐字一致 |
| `FinishOperation` 后 | 28 | 全零，与 split 前逐字一致 |
| 第二 PP 启动前 | 40 | output 与真实 RDMA source 两种读取均全零，与 split 后逐字一致 |

四阶段 output 的真实对象均为 `0x00649b80`；GWidth/GHeight 为5624/3714，
Width/Height 为5546/3696，OffsetX/Y 为78/18，RawStride 为8448，AddrType=2。
刚交付时对象的物理地址字段仍为0；该时点仅确认四个真实虚拟范围，不把
后续物理地址倒填成当时字段。原厂随后设置物理地址；split 前后与第二 PP
启动前的四路物理地址分别为 `d3db9000`、`d4534080`、`d4caf100`、`d542a180`。
第二 PP 真实 RDMA 地址逐项相同（`matched=1`），stride 均为4224，尺寸
仍为5624×3714；按同一物理范围、行号和字节长度比较，四路尾区变化均为0。
split 有效末行每路分别变化4160、4075、4157、4149 bytes，变化都在
4218-byte packed-12 payload 内，padding 不变。

这次新增证据把“尾区在 split 前已为零”提前到了“池指针刚交给 output
时已为零”，并确认第二 PP 将读取的确实是相同四路尾区。它仍没有确认更早
是谁把池内存变成零，也不能从字节相同排除期间写入了相同值；物理 MnF
DMA 写范围及这些尾行的原厂像素语义仍未证明。没有据此修改 split 高度、
跳过18行、填零或把尾区称为 OB 数据；动态 DPC、CS 像素算术及第二 PP
输出量化仍是独立缺口。
