# NX300M 原厂程序运行与移植现状

更新：2026-10-20。目标是运行 NX300M 原厂程序，以真实程序、接口和输出支持 Mod 开发。

## 输入来源

项目最初按最新版官方固件、未装 MFW 准备。2026-10-24用户更正机内已有MFW，随后实机日志确认本项目 `autoexec.sh` 诊断以root执行成功；准确MFW版本尚未读取。当前沿用已有入口，不为此重复刷机。模拟器分析源仍为核验后的原厂 1.15，不因机内状态更正而变成 MFW 仿真。用户允许使用核验后的本机原厂包，禁止复用有严重 bug 的旧 Mod。

三星香港 NX300M 支持页仍提供 1.15。新获取的官网 ZIP 前 4096 字节与本机原包一致，文件名、压缩前后大小、条目 CRC 一致，完整解压 CRC 通过；SLP 内部标识为 NX300M / 1.15。未使用旧补丁、注入器或旧展开目录。

本地 ZIP 摘要为 `ccd3b357f85b4d50b583ba874504dd896f4fff1d5d1b41e7384ac78f2a4e2df0`，SLP 摘要为 `75ededdc1e191ea3cdf8fb1201faced001d47b164224601a21150f75f8f7922f`；这是本地身份记录，不是三星公布的摘要。

UBI 新提取包含 13,709 个普通文件、1,870 个目录、1,251 个符号链接，报告坏块为 0。执行器为 ARM 用户态 QEMU 7.2.0，CPU 配置 Cortex-A9，运行库来自同一原厂文件系统。

## 当前交互结果

拍摄开发现已用录制 active Bayer 加显式常量边框交付 PP WDMA：全量 SMA 读回与 31,375,872 字节输入相同，原厂 IRQ 回调与等待返回已执行。修复了完成事件早于原厂 CSM 返回后清零的时序问题，首次等待由数据和原厂回调完成；capEnd 后第二次等待仍超时，尚未解决。EP MnF 配置路径也已接通，原先的零分块除零已越过；MnF Bayer 拆分已通过原厂接口的隔离全尺寸验证；完整拍摄联调、第二段 PP 与 IPCS 运算仍未完成，最新开发运行终止于 JPEG 缓冲为空的原厂断言，没有新照片。证据为 `outputs/firmware-runtime/bayer-readout-development.json`，范围与素材局限见硬件文档。

原厂相机程序与菜单已显示并可操作。修复缓存别名映射和私有 `/proc` 后，越过了原厂取景缓冲断言及 `__getmntent_r(NULL)` 崩溃。X11 原始屏幕为 480×800，查看器逆时针旋转为 800×480。正确的菜单按键是 `Menu`、`KP_Up/Down/Left/Right`、`KP_Enter`。

Metol 的 ARM 面板已在相同 X11 上运行：七维配方模式实际加载 68 条上游配方，仍为浏览模式；LUT 库现有 17 项，包括 11 份本机 Lightroom DCP 风格片段与 6 份伴随 filmsim 颜色配方。六份新表已与上游 baker 逐点核对并部署到当前会话；既有面板已验证选择、OK 保留和 MENU 恢复进入前请求。关闭后恢复原厂窗口焦点。界面复用开源源码，未重绘原厂相机菜单。来源及修改见 `mod/metol/PORT.md`；LUT API、来源及适配范围见 `docs/LOOKS.md`。

查看器检查真实相机进程，启动／停止同步等待，显示全黑和截图失败状态。模拟器启动时在私有偏好中设置 `APPPREF_B_DISABLE_DISPLAY_SAVE`（0x300）与 `APPPREF_B_DISABLE_POWER_SAVE`（0x304）。启用后同一原厂进程持续运行超过 20 分钟，仍可显示、操作原厂菜单；这不是完整硬件稳定性验证。

运行器已将 stdout／stderr 改为在 WSL 内直接写入每次启动独立的日志，修复 Windows stdio 中继导致活动日志长期显示零字节的问题。`session.json` 指向本次启动日志。日志现明确暴露 `/dev/allegro`、镜头 GPIO 和 d4c 缺失；半按并不代表成功进入拍摄。

2026-10-20 曾出现新 WSL 命令无法启动，而 Linux 内相机／网关仍响应的情况。进一步缩小范围后，结束相机的 Windows 启动客户端可立即解除阻塞。`--seconds 0` 已改用短命客户端发起独立 Linux 监督进程，查看器仍核对真实相机进程，启动与清理通过。首页现合成原厂 X11 OSD 与实际 LCD 视频提交。具体定位与验证见 `docs/HARDWARE.md`，底层 WSL 故障原因仍未确定。

后续再次发生WSL连根目录 `/bin/true` 都无法返回的故障。用户授权后终止并重新启动Debian，清理范围为本项目的Windows客户端；重启后真实app及录制照片回放恢复，七组桥状态就绪。最终七模块共载、UBIFS/UBI回读和SLP封装均已完成，原厂只读解析器实际接受本地候选，CRC路径到达、进程退出0；完整升级main、内核挂载、快照恢复及实体机尚未验证。证据和初次超时保留于 [SLP_VALIDATION.md](SLP_VALIDATION.md)。

本轮另修复进入原厂回放模式时 `SMA_SET_REMAIN_BUF` 未实现触发的断言。原厂 set／clear 隔离调用通过，生产会话完成正常取景→回放→返回取景，进程存活、照片输入和 OSD 恢复。证据为 `outputs/firmware-runtime/sma-remain-buffer-recovery.json`。ISO／最快快门的主页与遥控入口现共用 ARM 原生服务；原生路径的设置、读回、恢复及 getter 夹取通过，见 `outputs/controls/native-controls-validation.json`。Android 相应设置界面已构建，界面操作尚未动态验收。

## 启动依赖定位记录（含已解决的历史故障）

| 对象 | 实际输出／状态 | 结论 |
|---|---|---|
| 原厂 ARM busybox | 输出指定文本，退出 0 | 基础 ARM 程序、加载器和运行库可执行 |
| 原厂 st | 输出 help/cap/gpio/sys_mmap 等命令，退出 0 | 真实工具已运行，不代表服务端存在 |
| prefman info 1 | 导出完整应用参数表，退出 0 | 原厂自身的偏移命名表可用 |
| st cap | 退出 255，消息队列 id:34856 未建立 | 捕获服务尚未就绪 |
| 相机程序 + Xvfb | 连接 X11 并加载原厂 EFL 软件引擎，随后 SIGSEGV | 尚无可操作原厂界面 |
| 首次 core | `libudd.so!gpio_init+0x30`，库内地址 `0x1e84c`，`ldr r3,[r3,#8]`，r3=0 | 缺失 GPIO 设备时发生空指针解引用 |
| 加入偏好初始化和 GPIO 替代 | 越过首次 GPIO 故障，创建更多原厂线程和队列；仍有捕获缓冲错误、后续 SIGSEGV | 软件初始化推进，硬件后端仍缺失 |
| PWM 后端 | 原厂 pwm_open 完成 ioctl 和 mmap，下一处完整 core 位于 `libudd.so!spi_config` | PWM 空指针故障已越过；未模拟波形／中断 |
| SMA 和 SPI 后端 | 真实日志输出 `Initilized capture framework...`，执行到 UI Warm HW init 和镜头开机回调 | 已进入更后面的原厂启动流程，未证明硬件初始化成功 |
| 原厂取景缓冲查询 | `CLiveviewBuffer.cpp:843` 的 `address!=0` 断言中止；显式选择布局后，可能推进到 846 行的 `*phyAddress!=0` 断言 | 虚拟地址或物理地址无效，不能视为有效取景缓冲 |
| 配置覆盖 + 显式模式重新选择 | 原厂通过 SMA_ALLOC 申请 395 MiB，cap-cmm 22 MiB、cap-still 362 MiB、mm-display 4 MiB 均由原厂建立映射和客户端记录；随后出现新的 SIGSEGV | 分配／释放／原厂客户端映射链路已执行；整机启动仍未完成 |
| 当前 Xvfb 实际帧缓冲 | 原始 480×800，旋转后 800×480，有原厂图标、菜单和 Metol 面板 | 已有交互画面；无传感器图像 |

相机程序派生后台进程，不能以父进程退出 0 判断成功。运行器在私有 PID 命名空间内监督后代，默认 12 秒后结束观察，支持 `--seconds`；外层 `124` 与子进程是否崩溃必须分开解读。最新记录包含 `target_signals`、`target_sigabrt` 和具体断言，避免只检查 SIGSEGV 而遗漏 SIGIOT／SIGABRT。

## 已解决的启动依赖

1. 保留真实 ARM 加载器、库、资源、权限和符号链接。
2. 私有 Xvfb 提供 X11，实际帧缓冲直接导出，不绘制替代菜单。
3. 私有 `/dev/shm` 提供命名信号量，私有 `/pref`、`/adj` 提供原厂工具期望的存储位置。
4. 使用原厂 `prefman init` 创建共享内存，原厂 `prefman reset` 写入并加载默认数据。自定义 1 颜色字段已真实读回为 `-1`，共享内存、信号量和文件操作成功。
5. 可选 `gpio_preview.so` 通过 LD_PRELOAD 提供 GPIO 句柄及合成寄存器，GPIO 初始化和读写逻辑仍在原厂 libudd 内执行。默认空热靴情景在原厂 gpio_init 后重新施加 GPIO56 高输入，防止输出锁存复位被误当成附件接入；其余未指定输入仍无完整电气模型。快门位置输入另有显式情景。
6. `device_preview.so` 在文件／ioctl／mmap 层提供 PWM、SPI 寄存器后端，保留原厂 pwm_open、spi_open 和配置函数；未知 ioctl 返回 ENOTTY，未伪造 SPI 接收或中断完成。
7. SMA／BMA 后端有独立页分配、映射、释放和虚实地址转换实现，面向单个相机进程及其线程。SMA 的区域常量来自原厂 d4_cma.h；BMA 使用单独的 128 MiB 主机模拟区域。已观察到原厂释放临时 10 MiB 区域、申请 395 MiB、建立拍摄和显示客户端映射；新的原厂 PP／IQ 路径已分配并消费 BMA 缓冲。
8. 私有 `/var/run` 提供运行时通知存储，预建私有 AdjLog 文件，解决普通 UID 下重复的权限错误。

当前偏好共享内存与默认值均由原厂工具建立，没有手工填充数据。GPIO 替代只服务于用户态初始化研究，不代表模拟了真实传感器、镜头或 ISP。

## 尚待移植的接口

- IPCM／IPCS 读取已按三星不同的队列 ABI 适配，修复原厂 IPCM 中断线程 EOF 空转；隔离探针及完整运行检查通过。无图像输入时原厂六项取景启动条件仅剩第 5 项。新增的录制图像 WDMA 回放和原厂 VD 路径已让条件全部清除、raw state 进入 7；诊断为 `tools/pipeline_state.py`。
- 软件 YCC combi resize 已接入原厂配置／启动后执行真实像素运算，并在写完目标缓冲后发送完成通知。启动回放情景输入 300 帧、完成 298 次 528×352 缩放、0 次失败。新补齐 EP LVR 和 `/dev/fb0` 后，原厂显示程序又实际完成 282 次视频提交、失败 0 次；浏览器依据真实 OSD alpha 合成，已检查回放画面与不透明黑底菜单。传感器 Bayer ISP、硬件 FIR 等效及 still LUT 成像仍未实现；具体范围和运行命令见 `docs/HARDWARE.md`。
- 原厂相机与 Metol UI 均已运行；缺少传感器、镜头和完整 ISP 硬件后端，不能验证实际成像。部分 SPI ioctl 仍返回 ENOTTY，原厂设备错误和捕获超时保留在日志。
- 已实现 Allegro、PP core／SSIF／3A、IPCM／IPCS、BWM／CSM、MIPI 的设备传输后端。查看器默认提供明确的快门初始位置并加载这些后端，原厂已越过原先 CSM 打开断言、快门初始化和 MIPI 打开失败。原厂开设备／mmap／LUT 写表、MIPI 初始化及超时语义检查通过；传感器、DMA 和图像处理仍缺失。详见 `docs/HARDWARE.md`。
- SPI 等待预算转换、burst 片选 GPIO、reset 与 pin-control 已接通。进一步加入 A32 指令访存和 FIFO 模型，显式回环下原厂 8／16／32 位收发通过；默认会话使用 Aquila 已写寄存器模型，原厂启动已实际写入 91 个地址，原厂调试命令的修改、读回和恢复通过。未知寄存器仍标为未定义；MIPI 初始化和寄存器接口已接通，包／帧生成、DMA、slave 时钟及完成中断仍未实现。
- CSM wait-VD／readout 与 SSIF completion／poll/read 已接通显式事件输入，按原厂 timeout／ISR 分支执行 SPI 写入与 WDMA 启动。隔离探针核对 mask、等待、超时回退、寄存器读回和 WDMA 位保留，退出 0。完整相机会话已加载该后端并接收明确输入的一条 SSIF 事件；没有生成传感器帧或 DMA 完成。D4C 的 MIPI 接收流交给 `IQ_PAF`，不作为取景像素输入。详见 `docs/HARDWARE.md`。
- PP completion／poll/read 和 MIPI callback 订阅现已接通显式 IRQ 状态快照。原厂 ARM 等待接口、WDMA 与 LUT 分离、mask、MIPI 订阅及 CSM done／error 读回通过隔离探针，CSM 兼容检查也通过。没有执行真实 DMA 或 ISP，完整中断控制器锁存亦未实现；显式完成信号不证明图像有效。
- D4C SPI 1 的命令／数据传输、已写寄存器读回和 opaque burst 载荷接收已实现。原厂 `libd4c`／`libudd` 探针通过；完整相机实际写入 1148 个地址，原厂调试服务读回 0x0c20=0x00444444。MIPI DMA 控制位已启用，但仍缺接收载荷、DSP 和图像输出，详见 `docs/HARDWARE.md`。
- `capture_boot.so` 通过原厂 sysmmap 和内存管理 API 显式建立模拟拍摄布局。私有配置覆盖将回放编辑段的重复 `srp playback` 改为 `srp pbedit`，实际分配、映射和断言仍由原厂处理。
- Metol 的 NX500 七维参数、capdtm 命令和 A2 固件补丁不能直接用于 NX300M。原厂参数表目前只有颜色／饱和度／锐度／对比度四类 Picture Wizard 字段。当前保留原始七维配方，尚未做成像等效或消费验证。
- 原厂饱和度／锐度／对比度及色彩角度的运行时控制已接通：绕过原厂调试命令的负数溢出，执行原厂 SetData／OnNotify，独立读回原厂数据、IQ 参数层及原厂解码索引。网页有读取、应用与恢复入口，并提示模式限制。该结果不等于七维配方或 ISP 图像消费完成；范围见 [HARDWARE.md](HARDWARE.md#原厂画面参数控制)。
- `--sdcard` 已接入真实 MBR／FAT32 loop 镜像和显式卡检测输入，原厂完成容量／挂载识别并创建 DCIM；停止后 loop 自动释放、重启复用内容。持续回放下，半按进入原厂 3A Lock，全按进入 Capture。可选 `--shutter-motion ideal-8-sector` 根据电机 GPIO 和单调时间产生未标定的位置输入，已让原厂自行完成四边沿关帘和刹车。修复热靴检测输入被 gpio_init 错清为“接入”后，空热靴情景已返回 capPrepare、进入 capExposure；可选 PWM 有限计时后端在显式外部下降沿后已让原厂完成曝光并进入读出；早期未提供 Bayer 输入的会话随后 PP 图像 WDMA 超时，清理路径触发 SIGFPE、IPCS 未打开；最新 Bayer 会话进展见本页开头。回放提供成对同步边沿，在 IPCM 关闭时不解引用遗留配置指针、不写像素。默认无机械位置输入时保留原厂 Error 01。详见 [硬件接口](HARDWARE.md#模拟-sd-卡与原厂拍摄入口)。
- Metol 的 LUT 是 17³ 槽表；NX300M 原厂 `CIpcs3DLutD4::SetParam` 与 `ipcs_3d_lut` 使用三路 9³、10 位表，每路 1460 字节。`.cube` 和 Metol 槽转换器已实现，2187 个单位表样本及上游 ARM 工具生成的轴置换槽核对通过。适配器已能在原厂调用时转发表，原厂 libudd 写表指令向合成 RAM 的字节放置已验证；普通回放会话未观察到原厂 LUT 调用；最新 Bayer 开发会话已打开 IPCS 并执行 LUT 节点，像素处理和 ISP 消费仍未验证。
- 开源遥控／相册已接通原厂 OSD、按键、目录和原图传输；传感器取景、缩略图生成、Wi-Fi 推送及实机验证仍未完成，详见 `docs/REMOTE.md`。视频码率、HDR、批量录制等其余模块，安装钩子和实机安装包尚未实现。

## 并行开发中的客户端与直传

NXConnectForNX500 的 Android 客户端已加入本项目，适配源码位于 `mod/nxconnect`，与现有网页复用同一 ARM 遥控服务。机身服务已现场验证真实 LCD 视频提交与原厂 OSD 合成、菜单遮挡、Metol 不透明窗口，以及 JPEG／SRW 原图和 Range；该视频输入仍是明确的录制照片回放。新客户端的构建与运行以 [NXCONNECT.md](NXCONNECT.md) 和 `outputs/nxconnect` 中实际产物为准。

Quick Share 要求为相机直接作为原生协议设备传给手机／电脑。`mod/quickshare` 已生成 ARMv7 soft-float 静态程序，在 QEMU 中完成真实 Nearby V1／UKEY2 握手与加密文件回环，262267 字节逐字节一致。默认采用真实 poll／pipe 后端；对 getrandom 等新内核接口返回 ENOSYS 后仍完成传输并使用实际熵设备。统一遥控服务的发现／发送／状态／取消接口与同一图库网页入口已部署，发现、并发拒绝、取消和参数错误均实际核对；没有向外部设备发送照片。官方接收端互通、实体相机和无线链路尚未验证，详见 [QUICKSHARE.md](QUICKSHARE.md)。

EP MnF 的 Bayer split 软件执行已通过隔离验证，并在完整拍摄调用链实际执行，全量像素和等待语义见 [EP_MNF.md](EP_MNF.md)。本次 `ep-cs-grids` 采集另保留了真实上传前的 42720 字节 CS 源，四 lane 的系数全部为单位值 8192。第二段 PP 仍有动态 DPC、末尾输入行来源和固定点行为未解决；不能据此提交完整 PP 完成通知。原厂后续 JPEG 缓冲断言仍发生，DCIM 中没有新照片，详见 [PP_MEMORY.md](PP_MEMORY.md)。

## 保留的旧功能需求

按最新修订，先移植开源功能，再实现拨盘锁定、最快快门限制、ISO 步长、Auto ISO 上限扩展和手动 ISO 扩展。实时 ISO 显示已取消。`runtime/ui_mod.c` 已加入原厂 Ecore 指令拨盘事件过滤入口，默认不锁定；实机行为尚未验证。

ISO 步长、ISO 扩展及 Auto ISO 上限已有独立桥接模块与主页入口，经过原厂 UserData 设置、读回和恢复验证。最快快门限制通过原厂 capture AE 上限 getter 实现，设置 1/1000 后在正常 P 模式回放会话实际返回 Tv16=159，随后 off 恢复；未修改最低快门字段。运行证据为 `outputs/controls/session-controls-validation.json`，详细范围见 [CONTROLS.md](CONTROLS.md) 和 [SHUTTER_LIMIT.md](SHUTTER_LIMIT.md)。ISO 的实际 AE 策略消费、AE 完整求解和物理曝光仍待验证。

| 偏移 | 原厂 prefman info 1 命名 |
|---|---|
| `0x220` | APPPREF_ISO_STEP |
| `0x224` | APPPREF_ISO_EXPANSION |
| `0x228` | APPPREF_AUTO_ISO_RANGE |
| `0x22c` | APPPREF_AUTO_ISO_RANGE_1_3 |
| `0x9e0` | APPPREF_SHUTTER_SPEED_MIN_INDEX |
| `0x9e4` | APPPREF_SHUTTER_SPEED_MIN_AUTO |
| `0xe0 / 0x110 / 0x140 / 0x170` | 自定义 1 的颜色／饱和度／锐度／对比度 |

这些是存储位置，不证明 AE 或 ISP 已消费相应值。最快快门限制也不能通过覆盖原生最低快门设置来代替。

实际发现：共享内存缺失时，原厂 prefman get 会打印 pref_get_shm failed，同时打印 value = 0，进程仍可退出 0。引导脚本检查实际错误与同步状态，不把“出现数值”或退出 0 当作有效读回。

## 色彩与 LUT

已针对新提取的原厂库重新分析。libudd 存在 ipcs_3d_lut；libiq-fw-slpcam 存在 CIpcs3DLutD4 的三路表对象，各 1,460 字节；加载函数每路复制 0x5b4 字节。`runtime/lut_port.c` 在原厂主动调用时读取私有选择并转发三路，不伪造重载事件。以实际理光负片 LUT 执行未修改的 libudd ARM 指令，+0x9000／+0x9700／+0x9e00 的三路合成 RAM 内容逐字节一致，坏表回退通过；这不代表真实 ISP 输出成功。

`CIpcs3DLutD4::GetIndex` 和 `CIpcsGammaD4::GetIndex` 读取同一参数 +0x83，Gamma SetParam 的相应分支在 tone LUT 写入前额外应用 9 点 3DLutToneIn／Out 曲线，已核对 GOT 重定位。证据在 `outputs/metol/gamma-lut-domain.json`。硬件启用时序、输入颜色域与取景／JPEG 图像消费仍待验证。

已进一步执行原厂 C++ LUT／Gamma 索引及 LUT 虚函数 Update：确认原厂会跳过索引未变的写入。适配器在自定义选择变化时调用原厂 Reset，已验证两份不同理光组合表切换及取消后恢复原厂静态表。原厂 `ipcs_hl_3d_lut_onoff` 在合成 RAM 中确认 +0x1040 的 bit 13；板上时序仍未验证。证据见 `outputs/metol/lut-node-probe.json`。

原厂静态 LUT 的中性轴与 Gamma 3DLutToneIn 对应。sRGB 风格现生成 `P(stock3D)` 组合版和直接版，由实际原厂表来源选择；单位风格组合后 2,190 字与原厂表完全一致。原厂 RGB 编码仍未测定。`CIpcStillParamD4` 包含该 3D LUT 节点，取景的 `CIpcLiveviewParamD4` 不包含；静态照片更新还要求 IPCS 已打开。因此当前节点与合成 RAM 验证尚不能证明取景或 JPEG 图像效果。

本地 Lightroom 已盘点 4,292 份 DCP 和 184 份 XMP，导入用户指定 Exact 正片／负片、6 份富士风格（旧 v4 已保留到 work/reference/looks-v4）及 Sony FL／松下 Classic Neo／宾得 Bright 的 Creative LookTable。未移植其他相机的传感器矩阵，也未改动 Lightroom 原文件。另在 F:/Pictures 找到 2,973 张 NX300M SRW，筛出 924 组元数据相符的 RAW／JPEG 候选，供后续颜色对照；当前未做理光参考机校色。具体转换范围和使用方式见 `docs/LOOKS.md`。

参考项目 Metol 对应 NX500/NX1 的 DRIMeV 平台，地址、LUT 槽、七维数值和性能指标不能直接搬用。参考源码节选位于 work/reference/metol，保留 AGPL-3.0 和来源记录。

## 产物与来源

- tools/prepare_stock.py：核验原包并重新提取 SLP。
- tools/extract_stock_rootfs.py：UBIFS 导出为保留元数据的 TAR。
- tools/run_stock.py：运行真实程序并保留原始日志。
- runtime/bootstrap_preferences.sh：用原厂工具建立私有偏好环境。
- runtime/gpio_preview.c：可选 GPIO 替代接口。
- runtime/device_preview.c：PWM／SPI 寄存器、SMA／BMA 内存后端。
- runtime/startup_trace.c：观察并转发原厂内存管理调用。
- runtime/capture_boot.c：显式的模拟初始拍摄情景。
- tools/extract_vendor_headers.py：从核验的三星源码原包选择提取驱动定义，完整读取 gzip 校验 CRC。
- tools/disassemble_stock.py：读取原厂 ELF，显示动态／静态符号及 ARM PLT 调用名称。
- tools/analyze_stock_crash.py：结合首次 ARM core、实际库映射与原厂 ELF 定位 GPIO 故障。
- outputs/firmware-runtime：实际输出、系统调用、配置、崩溃分析、帧缓冲。

来源：

- https://www.samsung.com/hk/support/model/EV-NX300MDSTHK/
- https://org.downloadcenter.samsung.com/content/FM/201412/20141215163358663/NX300M_FW_v1.15.zip
- https://github.com/multiarch/qemu-user-static/releases/tag/v7.2.0-1
- https://github.com/WApaperplane/metol

当前交付包含真实原厂 ARM 程序运行、可操作原厂菜单、Metol ARM 配方／LUT 面板、LUT 扩展 API、本地 LR 风格片段转换，以及原厂写表 ABI 的合成 RAM 验证。可安装的 NX300M Mod、整套开源功能移植、完整硬件仿真与 ISP 成像验证仍未完成。
