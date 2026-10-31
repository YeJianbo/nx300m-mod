# 原厂 CPU JPEG 编码后端

`runtime/jpeg_cpu_adapter.c` 已有独立 ARM 构建和实际编码验证。它使用 NX300M
1.15 固件自身的 `libjpeg.so.8`，消费显式提供的 YCC 任务；当前没有接入模拟器
的拍摄像素生产者，也没有在主相机会话启用。常规路径在 PP／IPCS 没有有效像素
时仍拒绝编码；文末独立诊断模式只编码来源未确认的缓冲字节，以继续观察原厂
保存流程。这项工作不代表完整 Bayer 拍照、理光风格或硬件 ISP 颜色已修复。

## 构建与产物

使用现有 conda 环境：

```powershell
python -X utf8 tools/build_jpeg_encode.py --probe
```

脚本从已核验的原厂 1.15 提取量化表，使用现有 WSL ARM 工具链链接原厂
glibc 2.13 和 libdl。现代工具链的 C23 `strtol` 重定向在构建兼容头
中关闭，使用原厂已有的十进制解析 ABI。不会安装 SD 包、改用户照片或重启主模拟器。

产物位于 `outputs/firmware-runtime/jpeg-encode/`：

- `jpeg-encode.so`：可选编码模块，导出原厂 `JpegEncode(sJpegOp&,bool)` ABI。
- `jpeg-encode-probe.arm`：单独运行的 ARM 合成 YCC 探针。
- `stock_quant_tables.h`、`quant-tables.json`、`quant-table-evidence.asm`：提取表及依据。
- `compress-abi-evidence.asm`：原厂压缩结构和实际使用字段的指令依据。
- `build-report.json`：实际编译、依赖、探针和输出解码结果。

模块和探针都没有 `DT_NEEDED libjpeg.so.8`；由模块自行 `dlopen` 原厂 soname。
构建报告确认 ARM 产物仅依赖 `libdl.so.2`、`libc.so.6`，所需
glibc 符号版本不超过目标固件。

## 原厂量化表与 ABI

`libsif.so::DscJfifHal::Encode` 在 `0x29988..0x299fc` 将 quality enum 0、1、2
分别映射到 index 10、6、2；`0x29a4c..0x29a60` 以 `index*16` 选择表指针行，
表基址为 `0x6c018`。每行前两个指针为 Y/C 表，后两个为零。

| quality enum | index | Y 表地址 | C 表地址 |
|---|---|---|---|
| 0 | 10 | `0x5ed78` | `0x5edb8` |
| 1 | 6 | `0x5ef78` | `0x5efb8` |
| 2 | 2 | `0x5f178` | `0x5f1b8` |

`libudd.so.3.1` 的 `0xd23e0..0xd23ec` 将请求中的表指针交给 `0xd2b0c`；
`0xd2b58..0xd2bb8` 按每张表 16 个原始 u32 复制到量化寄存器。每张表为64字节。

表次序还与真实照片交叉核对：`outputs/looks/photo-reference.json` 指向的原厂
JPEG 实读 EXIF 为 SAMSUNG、NX300M、1.15，它的自然序 DQT 与 index10 的两张表
逐值一致；同一记录的 SRW 内嵌 JPEG 与原始 RAW 指定片段逐字节一致，其自然序
DQT 与 index2 逐值一致。index6 使用同一指针行布局和寄存器复制路径，其顺序
据此推断；当前没有另外取得一张 quality enum1 的原厂 JPEG 逐值对照。

`software_quality=0` 直接使用这些表；1..100 明确选择 IJG 软件质量值，没有将
原厂枚举拟合成百分比。相同量化表不保证 CPU DCT、舍入、边缘处理及熵编码与
硬件完全一致。

压缩结构为 JPEG80、432字节。原厂指令及探针的编译期检查共同核对
`comp_info=0x54`、`raw_data_in=0xd0`、`dct_method=0xe8`、组件结构步长`0x58`。
`do_fancy_downsampling=0xe0` 也与原厂raw编码分支的写入位置一致。
压缩结构可用标准 IJG8 头；此前解码结构所需的16字节厂商扩展不应用于压缩结构。

## 已完成的隔离验证

2026-10-21 构建和探针退出码均为0。探针输入为本地生成的34×18 YCC样本，覆盖
4:2:2／4:2:0、planar／interleaved、连续／even-odd分离、CbCr／CrCb，共16种
组合；同一采样模式的逻辑输入得到相同编码字节。非 MCU 整倍数的尾行和右边缘
实际经过扩展，未靠裁切样本避开边界。

六个原厂量化策略输出在 ARM 进程结束后由独立宿主解码器完整解码，尺寸、采样
和两张 DQT 逐值符合预期；显式 IJG90 也实际编码、解码通过。未知像素授权、
不足输出容量和不足输入映射都被拒绝，失败时 size 清零且整个输出及尾部哨兵
保持不变。报告只证明上述合成输入的 CPU 编码和任务边界。

调试中发现，只有尺寸、DQT、EOI和可解码检查通过仍可能颜色错误：默认打开的
`do_fancy_downsampling` 不适合这里已经下采样的raw色度行，原厂软件raw路径也
明确将其关闭。未关闭时，同输入重复编码曾出现边缘系数差异，真实调用样本的
Cb/Cr直接解码最大误差达85/107。临时保持库引用虽然掩盖重复输出差异，却没有
修复颜色。现在显式关闭该选项，真实调用样本的Y/Cb/Cr最大误差为1/0/0。

进一步用正确的每次编码库生命周期（dlopen→压缩状态destroy→dlclose）临时副本
复跑16种组合全部通过，证据为 `reload-check/report.json`；因此不能将之前差异
归因于库卸载本身。最终实现保持每个编码上下文的库引用到状态销毁之后，再释放
引用，不需要为此永久缓存库或增加互斥。探针没有直接链接或预先加载libjpeg。
最终上下文生命周期版本已重新编译并重跑下节的原厂调用与像素检查；16组合的
正确逐次卸载生命周期证据来自明确标记的临时同构核心副本，未冒充最终构建重跑。

## 尚未接入的边界

2026-10-22 增加诊断用 `nx300m_copy_live_virtual(address,length,destination)`：
仅接受确切虚拟地址，在同一个 memory lock 内核对 READ 映射和当前仍存活的
SMA/BMA 分配，并通过内核 pipe 复制到调用方预先分配的内存。它不返回裸指针，
因此释放、unmap、保护变更不能在核对和该次复制之间插入。只接受一个完整
映射／分配内的范围；跨片段范围保守拒绝。失败可留下目标前缀，调用者必须
丢弃该次结果。该接口没有停止其他像素写入，不提供整帧事务、跨调用代次
承诺、生产者授权或编码提交能力。

`mapping-replace/probe.json` 本轮 ARM/QEMU 实际输出确认：8192 字节两段逐字
匹配，只读输入可读、仅写输入拒绝；已释放但仍有映射的分配拒绝；重叠目标
和坏目标拒绝。原映射／保护／搬移检查同次退出0。此产物用于独立开发会话，
未据此开启主相机 CPU 编码。

独立 CPU 编码诊断另使用 `nx300m_live_virtual_generation(address,length,writable)`
取得当前共享 arena 分配的非零 64-bit 身份（分配代次及确切物理起点），再以
`nx300m_write_live_virtual_if_generation(address,length,source,identity)` 写回。
代次独立存放，不改变原厂 8-byte ioctl 参数；释放再复用同址仍产生新代次，
旧身份返回 `-ESTALE`。身份取得和提交均核对完整映射及读写权限，提交在同一
memory lock 内复核身份和复制。私有映射不能证明写回 arena，故拒绝其身份及
提交。它不证明输入像素正确，也不阻止其他像素写入；调用者仍须持有任务对象。
内核复制失败可能留下输出前缀，失败时必须保持任务 size 为0。

ARM/QEMU 探针实际确认两页写回逐字一致、只读输出／坏来源／释放分配拒绝，
同址重新分配的旧身份拒绝，私有别名拒绝；同次原映射检查退出0。记录见
`mapping-replace/probe.json::live_generation_submit_verified`。

拍摄侧仍缺 `nx300m_jpeg_map_address` 与 `nx300m_jpeg_input_ready` 实现。前者必须
核对完整有效 SMA 分配，后者必须为这个确切任务确认已有生产者像素及色度次序；
当前探针仅为自己的合成分配提供它们。接入时应继续沿用已有分配与生产者记录。

不能直接用 `device_preview.c` 的现有 `nx300m_map_virtual/physical` 包装编码出口：
它们不能同时证明存活分配及生产者授权；释放分配后映射仍可能存在。
映射表已跟踪 `mprotect` 和 Linux 3.5 `mremap` 的区间、权限与失败行为，
但返回裸指针后仍可能并发解除映射，不能据此跨越编码生命周期。
映射表还包含 ISP 3A 区域，不能都当作图像分配。虚拟地址和物理 token 同时命中
不同对象时应拒绝歧义；物理别名只有在确认同一共享后备对象及偏移后才能互换。

普通 `mmap/mmap64` 的 `MAP_FIXED` 覆盖现已按页取整清除或拆分旧映射，保留两侧
正确的物理偏移；成功覆盖也清除重叠的 MMIO 跟踪。与 `munmap` 复用区间处理，
在同一锁内预留记录容量、执行内核调用并更新记录。调用失败保持原记录；表满
时整段替换可复用被覆盖项，需要额外拆分槽但容量不足时在覆盖前返回 `ENOMEM`。
这只修复映射记录失效问题，不提供分配代次、权限或生产者授权。

`tools/probe_mapping_replace.py` 使用独立目录的 shim 和 ARM probe，在隔离挂载、
网络与 IPC namespace 中验证。通过后的同一产物已同步到模拟器默认的
`outputs/firmware-runtime/device_preview.so`；未热更新或重启现有主相机。
2026-10-21 的 `-Wall -Wextra -Werror` 构建及 probe 均退出0，实测匿名／普通
文件覆盖、`mmap64`、BMA 到 SMA 替换、首尾及页边界、失败保留、512项表满的
拒绝拆分与整段槽复用，以及 `munmap` 拆分。结果位于
`outputs/firmware-runtime/mapping-replace/probe.json`。

实际相机启动还暴露了持锁重入：PWM `open` 在 `memory_lock` 内启动原厂
`pthread_create`，线程栈分配再次进入公开 `mmap`。有界诊断实测等待者与持有者
为同一线程，证据保留在 `capture-geometry-r4-lockdiag/`。外设初始化现由专用锁
串行化，初始化成功后才短暂获取 `memory_lock` 发布记录；映射操作仍使用原锁。
更新后的 ARM probe 实际启用 PWM timing，原厂线程启动与上述映射用例全部通过。
同版真实隔离相机会话 `capture-geometry-r4-lockfix/` 已越过该启动阻塞，实际出现
PWM、ISP、CS LUT 字节装载和 LCD 初始化；这不代表拍摄像素或后续几何阶段完成。
这未覆盖 SPI 后处理调用外部函数的所有潜在重入路径。

未来最小接入应复用现有分配、映射表与权限/释放处理，补齐分配代次和
共享别名身份；在短锁内核对确切任务和生产者记录并取得输入快照，锁外
编码，再于短锁内核对输出分配/映射仍为同一代次后提交。仅返回裸指针的 map
回调不能提供这个生命周期保证。当前没有为尚缺像素生产者的拍摄链新增另一套
注册表或出口；解码、缩放完成也不作为拍摄完成依据。

即使显式设置 `NX300M_CPU_JPEG=1`，缺少上述能力也不会产出替代照片。默认关闭时
仍透传原厂函数。原厂 HAL 设备、IRQ、文件保存完成以及实际拍照到相册的链路
仍需分别实现和验证；本次没有对它们作成功声明。

## 原厂 EncodeJfif 实际调用验证

`tools/probe_stock_jpeg_encode.py` 在独立网络、IPC和挂载 namespace 中加载原厂
`libsif.so`，调用真实 `DscJfif::GetInstance()` 构造20字节对象，再运行原厂
`EncodeJfif`（`0x28c50`）。其参数签名为8个u32、pack mode、YCC type、quality
和输出长度引用；已核对参数顺序为输出地址、宽、高、缓冲高、stride、输入基址、
字节偏移、行偏移，随后三个枚举及长度引用。

本次使用显式生成的128×96连续 Y+CbCr 输入、pack mode2、4:2:0。原厂实际构造
`sJpegOp`，经自身 `JpegEncode` PLT调用模块；`libsif` 的 `0x6c934` JUMP_SLOT
实际绑定到 `jpeg-encode.so` 的hook，未改GOT或绕过原厂调用端。隔离回调逐字段
核对该任务后才授权，真实 `GetMaxEncodeSize` 给出12288字节。原厂函数实际返回1，
回填2827字节，EOI检查通过，输出之后的哨兵不变；宿主完整解码为128×96、420，
DQT与原厂 index10一致。直接YCbCr解码相对明确生成的输入，最大误差为1/0/0，
平均误差为0.008138/0/0。没有先转RGB造成的色域裁剪干扰该比较。证据为
`stock-call-report.json` 和同目录实际输出JPEG。

这项结果仅证明上述原厂 mode2 分支到CPU hook的调用和输出交接。原厂 mode1
生成的 planar 地址间距与其 task 中的 chroma delta关系尚未适配，不能由合成
planar约定测试通过推断原厂 planar 已适配。具体证据：在高96、stride128的420
例子中，mode1将Cb设为Y+12288、Cr设为Cb+3072，但task.stride_c仍为128；按48行
色度读取会互相重叠。`JpegEncode`和HAL均原样传递deltaC，`libudd`的FRAME分支
设置 odd=even+deltaC、硬件delta=2*deltaC，未见planar时的软件折半。

定向提取的原厂GPL `jpeg_type.h` 在 `outputs/firmware-runtime/jpeg-encode/gpl/`
下，明确UNPACK=0、PACK=1、FRAME=1、INTERLACE=0和deltaC为C stride，但没有提供
硬件在UNPACK时隐式折半的说明。因此当前不能将task.stride_c擅自除以2。
原厂构造及下层传递的完整指令证据见 `stock-call-evidence.asm`。

原厂hook现仅接受 `task.pack_type=0`；启用后的其他pack明确返回0、size清零，
在调用映射或生产者授权之前拒绝。隔离probe直接调用hook核对了pack1拒绝、
map/ready均未执行、整个输出不变，避免为了测试拒绝而进入原厂上层的size0
断言。独立的 `nx300m_jpeg_cpu_encode` API仍支持调用方明确提供真实色度stride的
planar软件约定；该约定的既有16组合测试不证明原厂mode1 ABI兼容。

拍摄像素生产者、保存链和主相机会话均未参与本次验证。

## 任务布局与原厂透传观测（2026-10-22）

`nx300m_jpeg_describe_task(task, capacity, layout)` 现在提供统一的请求区间描述，
包括输出容量、Y／Cb／Cr 各 even／odd 平面的地址、完整读取跨度和读写方向。
描述器与编码器共用跨度计算，检查几何、stride、格式、32-bit 地址溢出和输出
容量；不修改任务、不映射内存，也不授予生产者资格。独立 API 的 planar
约定仍不代表原厂 mode1 的硬件 stride 已解决。CPU 编码结果另有
`failure_stage`，区分布局、映射、生产者授权和压缩失败。

`run_stock.py --jpeg-task-trace` 和 `probe_capture_color.py --jpeg-task-trace`
加载该模块并设置 `NX300M_JPEG_TASK_TRACE=1`、`NX300M_CPU_JPEG=0`，在原厂
`JpegEncode` 调用前输出一条 `[JPEG TASK]` JSON，随后仍调用原厂编码器。
该记录含原始16字段、精确区间与描述拒绝原因；若设备后端导出
`nx300m_copy_live_virtual`，每个输入区间另外独立受锁读取并记录返回码、零字节数
和最小／最大值：不超过256KiB的区间读取完整范围，更大区间只采样开头64字节，
以 `sample_full_range` 明确覆盖范围。各区间不构成原子联合快照，也不检查像素来源。
采集工具将记录收回到独立目录的 `jpeg-tasks.json` 和 `capture.json`。

同模块的 `EncodeJfif` wrapper 按已核对的原厂签名透传，只在上述观测开关开启时
记录 `[JPEG CALLER]`，包括 caller 返回地址、所属库、返回地址相对库基址的偏移
及原始尺寸／地址／格式参数。ARM A32 调用点是返回偏移减4；记录本身保存返回点，
不把它误标为调用指令。`guest_tid` 用于关联同线程的调用者与任务观测。

最新 ARM 构建及既有16种布局探针均退出0。真实 `EncodeJfif` 合成输入隔离
调用再次退出0，任务描述为输出12288、Y12288、CbCr6144字节，实际输出2827字节，
独立 YCbCr 解码误差仍为1／0／0。该验证只检查描述器与既有编码行为没有分歧，
不证明拍摄生产者、硬件像素或完整成像链完成。

随后实际运行 `probe_capture_color.py --jpeg-task-trace --capture-observer
--startup-seconds 25 --capture-seconds 60`，独立 namespace7064 在原定窗口内
到达原厂 JPEG 空缓冲断言。记录位于
`outputs/firmware-runtime/capture-jpeg-task-trace/jpeg-tasks.json`，实际只有一项
160×120、422、pack0、quality1任务，Y、CbCr请求跨度各19200字节，输出容量
19200字节。这是小尺寸任务，不能视为全尺寸照片编码已经开始。受锁采样成功，
Y开头64字节为0，CbCr开头64字节为128；不能由这两个局部采样推断整张图、
像素生产者或原始拍摄源。原厂编码器没有被替换，未生成新媒体文件；独立会话
已经停止，主 namespace21455 的身份未变，详见该目录 `capture.json`。

第二次独立采集 `outputs/firmware-runtime/capture-jpeg-source-trace/` 明确了
实际调用来源：同一 guest pid75／tid215 的 `EncodeJfif` caller 为
`libsif.so+0x160e8`（返回点），对应 A32 `bl` 指令 `0x160e4`。原厂
`DscExif::EncodeImages` 位于 `0x15ee0`，依次在 `0x160e4`、`0x1625c`、
`0x163d4` 调用编码器；动态命中第一项，尚未进入后两项。该次请求是 EXIF
编码序列最先处理的160×120小图，而非全尺寸照片。参数构造及完整函数指令已保存
于 `outputs/firmware-runtime/jpeg-encode/jpeg-source-evidence.asm`。

这次两个输入区间均完整读取19200字节：Y全部为0，CbCr全部为128，
`sample_error=0`、`sample_full_range=true`。这确认该次请求的小图缓冲为中性黑，
仍未确定它由原厂主动初始化、缩放路径写入，还是缺少上游像素而保留初值，
因此没有为其添加生产者授权或用CPU编码绕过断言。采集退出0、仍停止于原厂
JPEG空缓冲断言、无新媒体文件；独立 namespace7772 已停止，主21455身份未变。

## 独立拍摄诊断编码

`probe_capture_color.py --jpeg-cpu-diagnostic` 可明确选择“编码当前缓冲字节”实验，
不检查或认可像素生产者；默认关闭，未加入SD或rootfs加载配置。它隐含任务观测，
仅使用独立输出目录及专属实验卡
`/opt/nx300m-lab/media/nx300m-jpeg-diagnostic.img`，不能用于主模拟器输出目录。
`run_stock.py` 对该实验分别设置 `NX300M_JPEG_DIAGNOSTIC=1` 和
`NX300M_JPEG_DIAGNOSTIC_ISOLATED=1`；常规 `NX300M_CPU_JPEG` 仍为0。

`runtime/jpeg_diagnostic.h` 将确切请求的每个输入范围经受锁虚拟地址快照复制到
私有缓冲，再由既有CPU编码器处理。独立map/ready回调只认可该次私有任务，
不会注册或改变常规 `nx300m_jpeg_input_ready`；输入假定为CbCr并明确记录
`source_pixels_unverified=true`。原厂planar硬件stride仍拒绝。总私有区间最多
128MiB；输出使用原厂量化表，保留原尺寸与采样。

输出开始时通过 `nx300m_live_virtual_generation` 取得确切可写共享分配的代次和
物理起点token，编码后仅通过 `nx300m_write_live_virtual_if_generation` 受锁写回。
目标释放、同址重分配、权限或共享属性变化均拒绝提交，不接受任意物理别名。
`[JPEG DIAGNOSTIC]` 记录阶段、错误和实际提交字节；成功只意味着该字节实验
越过了编码接口，不代表新照片、传感器像素或颜色验证。采集报告始终保留
`new_capture_jpeg_verified=false` 和 `image_verified=false`。

实际运行一次25秒启动／60秒拍摄窗口，独立 namespace8353 中原厂
`DscExif::EncodeImages` 三项均越过编码接口：

| 原厂 caller 返回偏移 | 尺寸 | 同代次输出实际提交字节 |
|---|---|---:|
| `0x160e8` | 160×120 | 1002 |
| `0x16260` | 5472×3648 | 390516 |
| `0x163d8` | 1616×1080 | 34723 |

随后原厂 `WriteChunks` 新建 `/mnt/mmc/DCIM/100PHOTO/SAM_0001.JPG`，预期和实际
写入均为466803字节，`fflush`、`fclose` 均返回0，独立观察器记录
`write_completion_confirmed=true`。两条 backend-completion 发生在写文件之前，
均未关联请求身份，不能据此宣称已识别最终照片完成回调。

停止独立会话后，只读挂载专属实验卡并收回该文件，在宿主完整解码：主图为
5472×3648、422，直接 YCbCr 三通道均为0，RGB全部为 `(0,135,0)`，是纯绿色；
EXIF缩略图为160×120、1002字节，YCbCr为 `(0,128,128)`，RGB纯黑。输出格式、
原厂EXIF拼接和文件写闭这条流程已实际执行，输入成像像素与颜色尚未完成，
不能把纯绿文件作为照片或LUT正确性结果。

原始证据、完整JPEG、提取缩略图与宿主解码结果位于
`outputs/firmware-runtime/capture-jpeg-cpu-diagnostic/`，核心记录为
`jpeg-tasks.json`、`capture-observer/.../event-3.json`、`capture.json`、
`saved-jpeg-decode.json`。窗口退出0，独立会话已停止、只读挂载已卸载，
主 namespace21455 身份未变；没有额外触发拍摄或替换主会话模块。


## 外部图像样本注入与诊断编码（2026-10-23）

`runtime/jpeg_diagnostic.h` 可在独立诊断任务中读取 `sample_<w>x<h>_y.bin` 和 `sample_<w>x<h>_cbcr.bin`。输入是宿主渲染的样本，压缩由 CPU libjpeg 使用提取的原厂量化表完成；原厂 `EncodeImages` 继续组织任务，EXIF/MPO 封装和 `WriteChunks` 写盘实际执行。该分支不执行原厂硬件 JPEG 编码，也没有补齐 ISP 像素处理。

显式目录参数为 `--diagnostic-sample-dir /mod/samples_ricoh`，须与 `--jpeg-cpu-diagnostic` 及独立输出目录一起使用；旧环境变量 `NX300M_DIAGNOSTIC_SAMPLE_DIR` 仍兼容。目录、文件名、长度、mtime和记录时间写入 launch/session/capture。该元数据不是文件内容身份认证；样本读取期间应保持目录内容稳定。

样本模式只接受已明确的逐行交织 Y/CbCr 布局，不接受尚未定义样本文件格式的 even/odd 交错输入。样本文件是完整平面，不再作为原快照的局部覆盖：打开失败、长度与任务范围不相等、seek/read/ferror、额外尾字节或关闭错误均拒绝，`task.size=0`，不压缩或提交混合输入。日志 `sample_requested` 区分请求，`sample_injected` 仅在两个完整平面实际读入后为true，并记录成功读入的平面数和字节数。

### 早期半高度色度错误

`capture-jpeg-sample-injected-r2` 及早期理光／富士样本使用半高度色度数据，而这些原厂 JPEG 任务 `ycc_type=0`，要求16×8 MCU的4:2:2采样。旧代码忽略 `fread` 返回值，只覆盖文件实际提供的前半个色度平面，尾部保留原快照。此前文档所述“EOF 后自动填零”不符合代码行为；未提供色度且原快照为零的区域在解码后呈绿色。

因此4.93MB等早期文件可证明 CPU 压缩、原厂封装和写盘被执行，但不能证明自然图像颜色正确。原始文件和运行证据保留，不能用缩略图正常替代主图检查。

### 本次任务的 NV16 样本

JPEG任务的采样码来自 libsif；交织 CbCr 顺序由本诊断输入明确约定，不能仅由4:2:2码断言真实硬件一定使用NV16。在本次 progressive 任务中，stride 等于宽度，全高度 Y 和 CbCr 各需要：

| 尺寸 | 每个平面字节 |
|---|---:|
| 5472×3648 | 19,961,856 |
| 1616×1080 | 1,745,280 |
| 160×120 | 19,200 |

现有 `capture-ricoh-positive-nv16/SAM_0006.JPG` 和 `capture-fuji-classic-chrome-nv16/SAM_0007.JPG` 分别为5,189,142和5,166,440字节，均完整解码为5472×3648、JPEG 4:2:2。下半部 RGB 均值分别为 `[141.806,143.312,144.954]`、`[141.592,144.443,144.883]`，不再出现旧文件的大面积绿色下半部。相对于当前注入 Y/CbCr 重建 RGB 的8位通道 RMSE 为0.8375／0.8363；这只检查注入和压缩，不检查目标风格或 ISP。

### 样本读取错误的定向检查

`tools/probe_jpeg_diagnostic_samples.py` 使用生成的16×8平面，在独立 ARM/QEMU 程序中调用同一诊断入口，未启动相机或写真实照片。缺少Y、缺少CbCr、CbCr过短和过长均拒绝，提交次数为0，整个输出保持原值，日志 `sample_injected=false`；完整样本成功提交661字节且日志为true。全部退出0，构建使用 `-Wall -Wextra -Werror`。记录为 `outputs/firmware-runtime/jpeg-encode/diagnostic-samples-probe/report.json`。

这些检查修复样本实验中的混合输入和错误成功记录，不代表传感器成像、动态DPC、CS或完整ISP已经实现。

## 真实录制 Bayer 的软件 ISP 接入

新增 `--software-isp-source` 与 `runtime/software_isp.h`，以录制的 GRBG12 mosaic 为源，在真正的原厂 `JpegEncode` 任务边界做明确的软件 CFA 低通、双线性插值、同 RAW 白平衡、原厂最终 CCM/Gamma/3D LUT/RGB2YCC 和 CPU JPEG 压缩。该入口独立于上述外部 YCC 样本诊断；两种模式不能同时启用。

`prepare_software_isp.py` 已将有效区域全部20,461,056像素与恢复的 `work/reference-media/SAM_4262.SRW` 核对一致。`build_jpeg_encode.py --reference-raw PATH` 允许原始 RAW 恢复到另一位置；仍要求其嵌入 JPEG 字节与保留参考完全相同，并保留原厂 EXIF 与量化表验证。

软件编码必须观察到原厂读出流程实际完成 Bayer 转移的 generation；编码完成后仍核对同一 Bayer generation 与原厂输出 allocation generation，才提交字节。缺参数、未知布局、无真实源或编码失败均返回失败，`task.size=0`。

模型、启动方式、当前几何与硬件差异见 [SOFTWARE_ISP.md](SOFTWARE_ISP.md)。完整 DCP 色彩参考另走 packet→DNG→RawTherapee 路径；此轻量模型尚未证明为完整 DCP 或 DRIMe4 的像素等价实现。

最终独立原厂拍摄验证已完成三项任务：160×120=13,322字节、5472×3648=6,944,642字节、1616×1080=976,557字节，均低于原厂容量且返回成功。原厂 `WriteChunks` 写出 [SAM_0001.JPG](../outputs/firmware-runtime/capture-software-isp-area/media/DCIM/100PHOTO/SAM_0001.JPG)，7,975,085字节；独立宿主完整解码5472×3648、4:2:2，EXIF为NX300M/1.15。实际照片、任务日志、原厂写盘观察和主会话身份检查见 `outputs/firmware-runtime/capture-software-isp-area`。这补上了“真实录制 mosaic→明确软件模型→原厂任务封装/保存”路径，硬件 PP/IPCS/JPEG 和完整色彩等价仍未实现。
