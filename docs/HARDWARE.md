# 拍摄硬件后端开发

查看器默认加载快门协议、PP／IPCM／IPCS 寄存器后端，并显式提供 PI1=1、PI2=0 的启动位置输入。原厂程序据此完成快门初始化，继续运行菜单、Metol 和遥控。传感器数据与完整 ISP 运算仍未实现。

## IPCM／IPCS 中断读取与取景状态

`runtime/ipc_events.h` 修复了原厂 `udd_ipcm_intr_h` 线程读取普通寄存器文件、遇到 EOF 后持续空转的问题。读取协议依据三星 `ipcm/d4_ipcm_ioctl.c`、`ipcs/d4_ipcs_ioctl.c`，事件编号依据同包 `imagedev/usr/include/drime4/udd/d4_lib.h`。

- IPCM 无事件时阻塞，即使打开时带有 `O_NONBLOCK`。每批最多三个事件，只改写缓冲区偏移 0、4、8 的字节，保留各 word 的其他字节；返回剩余队列长度，原厂 poll 始终不就绪。
- IPCS 使用 512 项队列，每次读一个事件字节并返回剩余队列长度；空队列的非阻塞读返回 EAGAIN，有事件时 poll 返回读取就绪。
- 私有 FIFO `nx300m-ipcm-events` 接受 18…20，`nx300m-ipcs-events` 接受 23…26。这是显式的内核 ISR 后队列通知入口，只用于隔离接口开发；它不执行 DMA、不产生像素、不触发 WDMA ioctl completion，也不模拟 IRQ 寄存器和订阅。默认相机会话没有自动通知。

```powershell
python tools/build_runtime_shim.py
python tools/probe_ipc_events.py
python tools/pipeline_state.py
```

本次针对 EOF 故障的探针退出 0，核对两套读协议，并通过原厂 `d4_setfd`／`d4_register_isr` 启动未修改 libudd 中断线程，确认只有显式通知到达时调用已注册回调。原厂线程提高调度优先级失败，但仍创建并成功递送通知；见 `outputs/firmware-runtime/ipc-events-probe.json`。

修复后 `udd_ipcm_intr_h` 等待于 `do_sys_poll`，2 秒采样 CPU tick 增量为 0，记录见 `ipc-events-development.json`。无图像输入时，六项取景启动条件仅余第 5 项、`CLiveview` raw state 为 2。后续回放输入已让原厂自行清除该条件并进入 state 7，详见下节。`pipeline_state.py` 从当前 ELF 和进程映射解析对象位置，核对虚表，只读原厂状态及 IPCM 初始配置／中断计数，不修改内存或调用回调。初始配置中的地址不随 WDMA 地址更新而改变，实际写入地址必须读取当前寄存器。

## 回放输入与软件 YCC 缩放

`tools/replay_ipcm.py` 提供明确的录制图像输入情景。当前入口位于 IPCM 主 WDMA 输出边界：从本地 JPEG 生成 YUV420／422，逐行写入原厂配置的共享 SMA 缓冲，保留 padding；它不模拟 Bayer 传感器、PP／D4C 或完整硬件 ISP。

每帧先向 SSIF 输入 VD rising，等待原厂地址更新 alarm 注册目标缓冲，再读取 WDMA even／odd 地址和 delta、校验 SMA 范围、写完 Y／C 像素后通知 B2Y。仅发 B2Y 时原厂无法取得有效 WR buffer；加入 VD 路径后，原厂缓冲自行轮换，`CLiveviewStartCompletion` 六项条件从 `0x20` 变为 `0x00`，`CLiveview` raw state 从 2 变为 7。未改写原厂对象或直接调用启动回调。

原厂随后请求 YCC combi resize。`runtime/ipcm_resize.c` 转发原厂 `ipcm_ycc_combi_set`／`ipcm_ycc_combi_do_1frm` 配置和启动，再对实际映射的图像执行软件双线性缩放。支持渐进式、未压缩 YUV420／422 的裁切、行跨度、CbCr 顺序和重叠输入快照；完成写入后才发送对应 completion／队列通知。颜色校正、NLC、交错扫描等未支持的请求保留完成超时，不返回虚构的处理结果。该软件滤波器不是 DRIMeIV FIR 的逐位模型。

```powershell
python tools/build_runtime_shim.py
python tools/probe_ipcm_resize.py
# 查看器默认启用；独立 run_stock.py 使用 --cpu-ycc-resize --isp-preview。
python tools/replay_ipcm.py --image outputs/looks/photo-reference.jpg --frames 10 --fps 5
python tools/inspect_ipcm_frame.py
```

针对实际 resize timeout 的隔离探针已退出 0，执行原厂 ARM 配置／启动／等待，核对裁切后的梯度四角、chroma 顺序、padding 保留、无输入时超时以及未实现的颜色操作不会产生 completion。记录在 `outputs/firmware-runtime/ipcm-resize-probe.json`。第一次探针因链接器裁掉 libdl，退出 127 并报 `undefined symbol: dlsym`；探针现显式引用后端导出符号以保留该依赖，重跑通过。

查看器默认配置已通过 10 帧回放、8 次缩放的完整运行检查。手动开始回放可能错过 MMF 的启动时限，因此 `run_stock.py` 新增 `--replay-packet outputs/firmware-runtime/ipcm-replay-input.json`：以已经生成的 JSON／YUV 包在原厂流就绪后立即送帧，持续 300 帧／5 fps。回放进程属于相机的私有 PID 命名空间，停止相机会一并结束它。

当前启动回放会话完整输入 300 帧：原厂 B2Y／VD／地址更新计数各为 300，启动条件全部清除，软件缩放完成 298 次、失败 0 次；该会话未出现先前的 `resize timeout error` 和 MMF `Time Out: Waiting Notification Callback`。从原厂最后配置的 528×352 目标地址实际读出 278,784 字节，亮度范围 5…255。证据为 `ipcm-replay.json`、`ipcm-resize-readback.json`、`ipcm-resize-readback.png`，辅助进程输出在 `startup-replay.stdout.log`／`startup-replay.stderr.log`。LCD 显示已继续接通，见下节；完整 ISP、still LUT 和理光校色仍待开发。

## 原厂 LVR 与 LCD 视频层

原厂 MMF 选择 `cSimpleDisplay`，由 `libmm-displayer.so::CDisplayLCD` 打开 `/dev/fb0`。此前 fd 为 −1；即使 IPCM 缩放缓冲有图像，也没有显示设备接收它。补齐该设备后，又定位到 `display lvr open fail`：原厂在 LCD 提交前通过 `/dev/drime4_ep` 执行 90 度旋转。

`runtime/ep_preview.h` 提供 EP 的寄存器映射、LVR path 注册／查询／释放、原厂配置锁及中断等待。物理标识 `0x92000000` 起的 9 页仅为模拟映射 token。原厂 libudd 仍执行 EP 配置和 LVR 寄存器写入；紧随启动寄存器写入的 UDD_UNLOCK 是软件执行边界。后端根据真实 LVR 寄存器旋转 Y／CbCr 像素，写完后才提供 18／26 号完成信号。当前支持无 flip 的 little-endian 8-bit YUV420 旋转；YUV422 的 90／270 度色度重采样、图形旋转及其他 EP 图像处理未实现，不产生相应成功完成。

`runtime/fb_preview.h` 接收原厂 video full-set、address-update、独立 on/off、ring count 和同步控制。原厂 stride 是 even／odd 两行的跨度，单行步长为其一半。后端校验输入映射，读取实际像素并原子发布 `/var/run/nx300m-lcd-video-N.yuv`；文件头为三星 64 字节窗口结构，后面为紧凑 YUV。`nx300m-lcd-state.bin` 记录四个窗口的配置、显隐、提交／成功／失败计数。软件同步导出后队列深度为零，等待只消费已有导出完成；这不是 LCD vsync 时序模型。未知 ioctl 返回 ENOTTY，fb mmap 明确拒绝。

本次原厂实际输入 LVR 为 720×480，输出为 480×720，LCD 窗口位于竖向 480×800 画面的 `(0,80)`。300 帧输入期间，原厂显示端提交并导出 282 帧、失败 0 次。另一路 528×352 resize 缓冲也保留；它不是这次 LCD 所用的缓冲。

`tools/input_stock.py lcd-frame` 同时读取原厂 X11 像素和 LCD 提交。当前 Xvfb 窗口虽为 depth 24、32 bpp，高字节仍保留原厂 Evas alpha：透明黑为 `00000000`，不透明黑为 `ff000000`，还存在半透明图标。`render_runtime_frame.py` 使用这些真实预乘 ARGB 值与视频层合成，再整体旋转到 800×480；没有将黑色设为透明或重绘菜单。Metol 的 Nuklear／Xft 窗口是普通不透明 RGB，高字节不含有效 alpha；工具按实际可见的 `Metol-NX300M` 窗口几何将其作为不透明区域，避免视频穿透面板。主查看器 `/frame.png` 采用此路径，遥控模块自身的 OSD 接口保持原样。

```powershell
python tools/lcd_plane.py
python tools/render_runtime_frame.py --live
```

证据为 `lcd-video.json`、`lcd-video-0.png`、`framebuffer.json`、`lcd-development.json`；浏览器回放、原厂黑底菜单与 LUT 面板分别见 `lcd-viewer.png`、`lcd-menu.png`、`lcd-metol.png`。当前颜色解码按 BT.601 full range、CbCr 处理，硬件颜色编码仍需实拍核对。此链的输入已在 IPCM 输出之后，不能证明机内 LUT 被消费或完整 ISP 成像。

## Allegro 快门接口

来源为核验后的三星 NX300M 源码包：`include/linux/allegro_ioctl.h`、`drivers/misc/shutter/allegro.c` 和板级 GPIO 定义，位于 `work/vendor-source/TIZEN/packages_nx300m/linux`。适配代码在 `runtime/allegro_preview.h`，通过 `device_preview.c` 接入文件／ioctl 层。

实现内容：

- `/dev/allegro` 打开与初始化、GPIO_GET、IRQ 配置／启停／等待、5 个定时器通道。
- 定时器使用 Linux CLOCK_MONOTONIC timerfd；工作线程在到期时执行 GPIO 动作，再通知等待者。零时长按立即到期处理，不误解为 timerfd 的取消。
- GPIO 操作调用未修改的原厂 `get_gpio_number`、`gpio_open`、`gpio_data_set`。原厂表读回 PI2 为 51，MG1 为 103，MG2 为 104，与三星板级头文件一致。
- 独立协议探针的 PI2 初值为高电平；查看器通过 `--shutter-park 10` 指定启动位置。私有 `/var/run/nx300m-allegro-pi2` FIFO 接受 ASCII `0`／`1`，只有输入边沿能减少已配置的 IRQ 次数，保留驱动的 2 ms 去抖规则。
- 无输入时 IRQ_WAIT 保持等待；未知 ioctl 返回 ENOTTY。没有机械运动、传感器数据或自动生成的拍摄完成事件。

原厂头文件定义了 MG1_OFF／MG2_OFF 常量，但所发布 ISR／timer handler 未执行这两个动作，后端保留该行为。

```powershell
conda activate nx300m-mod
python tools/build_runtime_shim.py
python tools/probe_shutter.py
```

最后一次接口探针退出 0：60 ms 定时器实测约 60.9 ms；零时长 GPIO 动作在进入 WAIT 前已执行；IRQ 在无输入及仅一条边沿时均保持等待，收到所配置的两条边沿后才返回。使用的是原厂 ARM GPIO／版本库和本项目设备后端，结果在 `outputs/firmware-runtime/shutter-probe.json`。这验证设备协议，不证明物理快门或成像。

## 模拟 SD 卡与原厂拍摄入口

`run_stock.py --sdcard` 和查看器同名选项在私有挂载命名空间接入 `/opt/nx300m-lab/media/nx300m-card-1g.img`。镜像为 1 GiB，MBR 的 FAT32 分区从 LBA 2048 开始；通过真实 Linux loop／partition／vfat 驱动提供扇区、块设备 ioctl、文件与容量。只在镜像不存在时格式化，已有内容原样复用。依赖 Debian `dosfstools`，本机已安装。

`tools/sdcard.py` 在独立、允许设备节点的私有 tmpfs 创建块设备，再绑定为 `/dev/mmcblk0` 和 `/dev/mmcblk0p1`；卡在线／写保护／介质错误属性及 `/proc/partitions` 中的板级设备名是显式模拟输入，容量及 major/minor 来自实际镜像和 kernel partition。卡挂载为 uid/gid 65534，不映射照片原件或相机设备。loop 的 AUTOCLEAR 在会话挂载释放后生效，停止后已观察到关联设备为空。

首次把节点放在已有 `nodev` 目录时，mount 退出 1、errno 13（EACCES）；已改用上述独立设备挂载。512 MiB 镜像被原厂 `CStorageDeviceSDMMC::CheckValid` 拒绝（错误 3），反汇编确认其容量检查要求大于 512 MiB。当前 1 GiB 镜像通过原厂 MBR／PBR、块大小、容量和文件系统检查，自行生成 `DCIM`，界面显示约 80 张剩余容量；未覆盖原厂检查结果。

有卡启动时“Body Firmware update completed”的来源已定位到 `APPPREF_FW_UPGRADE_CHECK`：原厂 common-menu 枚举 `0xfa` 的 getter 跳表项 `0x15fa0c` 指向 `0x15fcf4`，调用 `pref_get_long(1, 0x384)`；setter 对应 `0x1600f4`，调用同组、同偏移的 `pref_set_long`。`prefman info 1` 确认该字段为四字节 long。原厂 `libprefman.so::pref_app_default`（`0x6acc`）在 `+0x384` 的默认值为 1；`pref_init` 创建同步对象与共享内存，`pref_reset` 经 `nvd_save`／`nvd_load` 将默认数据写入偏好文件并装入内存，因此私有 bootstrap 每次 reset 都会重新开启此检查。原厂 `UI_Camera_Init` 在标志非零且两项 hibernation 条件成立时打印 `FW_UPGRADE_CHECK ON` 并调用 `fw_clear_snapshot_request`，随后创建 `upgrade_done`；这不是本会话实际升级的证据。

`runtime/bootstrap_preferences.sh` 现于私有初始化后执行 `prefman set 1 0x384 l 0` 并重新读取，核对退出状态、同步／共享内存错误及零值，成功记录 `PREFMAN_FW_UPGRADE_CHECK=0`。新增修复仅修改会话内 application 共享内存，不调用 `prefman save`，不改 hibernation 返回或全局固件文件。`sh -n` 退出 0；重启后的私有模拟会话（namespace `30940`、camera `31016`）确认零值读回、相机运行及配方桥就绪，日志未出现 `FW_UPGRADE_CHECK ON` 或创建 `upgrade_done`，证据为 `outputs/firmware-runtime/startup-normal-session.json`；内置浏览器已确认原厂正常界面，升级提示消失，截图为 `outputs/firmware-runtime/startup-normal.jpg`。原厂启动分支证据还见 `outputs/deployment/app-loader/boot-chain/snapshot/di-camera-app-nx300.asm` 与 `docs/APP_LOADER.md`。

`input_stock.py shutter half|full|release` 及查看器三个快门按钮使用原厂映射：`Super_L` 为半按、`Super_R` 为全按。半按保持键与私有 shutter 属性 1，释放为 0；全按输入为 1→2→0，伴随相应 XTEST 边沿，结束后松开两键。原厂 Ecore 处理器和 `device_get_shutter` 仍自行处理输入。`GET /status` 给出实际 SD 卡和回放状态。

有限回放停止后，半按曾停在 `C3AStateS1AE`。`--replay-continuous` 让真实帧缓冲／VD／B2Y 输入持续运行直到停止会话；运行状态每五帧发布，worker PID 由当前 `/proc` 的命令行和命名空间核验，记录仅保留最近 60 帧及总数，不无限积累。`device_preview.c` 对反复的 SMA／BMA ioctl 只记录前四次，保留所有实际调用与错误结果。

关闭启动提示时，原厂会把 IPCM 输出从 720×480 改为 640×424。回放入口根据新配置，对原录制 YCC 做偶数对齐居中裁切与最近邻重采样，色度使用独立采样网格；这属于录制输入准备，不是 ISP 运算。源 packet 保留原始尺寸。暂时没有有效 WR buffer 时，连续模式发布 `waiting-stock-buffer`，继续等待下一个原厂 VD 注册，不写像素或通知 B2Y；固定帧数命令仍保留超时退出。实际新尺寸下的持续送帧与 LCD 提交已推进，证据在 `sdcard-development.json`。查看器区分回放运行、结束和中断。

回放现按每周期 1／0 交替提供 SSIF 上升／下降沿，下降沿位于半周期，`frame_sync` 记录两类输入及宿主延迟。原先只有上升沿，会缺失原厂拍摄准备／读出可能等待的下降沿。原厂没有注册写缓冲时，时钟仍按设定帧率推进，像素和 B2Y 不生成；IPCM 关闭时发布 `waiting-stock-ipcm`。写像素前再次检查模块、缓冲注册和地址更新代号，模式变化时跳过本帧。

`pipeline_state.py` 现从原厂 `d4_get_udd_open_count` 的实际 ARM 指令解析计数数组，输出 IPCM／IPCS 打开计数。原厂 `ipcm_close` 释放 `ipcm_ctx` 后不清其导出指针；读取器以前会误读已释放对象并导致回放退出，现仅在打开计数大于 0 时解析配置。一次原厂正常模式切换已经复现该故障并修复；同一相机 PID 中重新启动输入后，关闭的 IPCM 不再导致读取异常，VSYNC 两类计数持续增长且没有写入退役缓冲。证据见 `readout-wait-state.json`、`readout-resumed-session.json` 和 `readout-synced-wait-state.json`。内部 `--submit --resume --layout ...` 可恢复既有命名空间的输入，依旧通过锁禁止多个写入者，不重启相机。

持续输入时，原厂 3A 已进入 `C3AStateLock`（raw 1），全按使 `CStateManager` 从 idle（1）进入 capture（2）。随后 Allegro 配置 PI2 level 1、4 次边沿和 210000 µs 定时器；没有机械运动输入时，原厂实际报 `Shutter error occured`，界面为 Error 01。该次运行的取景地址更新停止，旧版回放辅助进程按错误退出；当前连续模式的缓冲等待行为见上段。没有伪造这些边沿或成功回调。IPCS 未打开，尚无 still LUT 调用和新照片。证据为 `capture-development.json`、`capture-half-state.json`、`capture-full-state.json` 与 `capture-shutter-error.png`。`pipeline_state.py` 新增只读拍摄状态及 3A 虚表核对，便于继续定位机械运动和静态图像链。

### 电机观测与可选理想化转子

`--shutter-motion observe` 每 1 ms 经原厂 `gpio_data_get` 采样供电、MOT1 和 MOT2，不生成位置边沿。板级映射经原厂库核对为供电 GPIO199、MOT1 GPIO68、MOT2 GPIO69、PI1 GPIO50、PI2 GPIO51。原厂 `motorFORWARD` 写 01、`motorBRAKE` 写 11、`motorSTANDBY` 写 00；原厂 GPIO 数据指令未替换。

`--shutter-motion ideal-8-sector --shutter-step-us 10000` 显式开启未标定的开发情景：八等分转子，PI1 前半圈为 1、后半圈为 0，PI2 每扇区翻转；初始扇区由 `--shutter-park` 决定。仅供电打开且电机处于 01／10 时按单调时间前进／后退，刹车和待机停止位置推进，不模拟惯性、磁铁或帘幕动力学。反转行为为理想 H 桥假设，尚未观察到原厂使用。运动计算不读取 IRQ 的目标 level、count、capture 状态或回调。宿主调度错过一个完整扇区时记录 stall 并冻结该区间，不批量压缩边沿。参数范围 3000…1000000 µs，默认 10000 µs；默认查看器不开启此模型。

位置边沿经过原 Allegro 去抖／计数／GPIO job。新增 eventfd 唤醒异步 IRQ_WAIT，保留 FIFO 输入；延迟分支按三星源码只交给 timer channel 2，不错误地唤醒 IRQ_WAIT。`session_stock.py` 的 `shutter_motion` 读取私有 64 字节状态，明确给出未标定、调度 stall 和非同步快照。

```powershell
python tools/probe_shutter.py --motion
python tools/simulator.py --sdcard --replay-packet outputs/firmware-runtime/ipcm-replay-input.json --replay-continuous --shutter-motion ideal-8-sector
```

ARM 构建及既有 Allegro ABI 检查通过。运动检查验证了“已配 IRQ 但刹车时不完成”、GPIO 前进产生两边沿并唤醒 WAIT、原厂刹车 job 停止位置、未配 IRQ 时转子仍推进，退出 0，见 `shutter-motion-probe.json`。完整相机会话实际接收四次边沿后自行刹车，又执行 13000／7000 µs 定时器，未出现原先的 Shutter error；停在 capture（2），尚无 IPCS 打开、LUT 调用或新照片。完整证据为 `shutter-motion-development.json`、`motion-capture-state.json`、`motion-capture-session.json` 与 `motion-capture.png`。

前一轮转发式跟踪定位到 `capPrepare(true) → ChangeStillMode → capSetSsif → SetupFlash(false)`：模式切换、SSIF 配置返回，SetupFlash 进入后未返回；当时曝光和读出函数均未进入。现场 IPCM／IPCS 打开计数均为 0，持续输入存活，关闭模块期间不写像素。该次等待属于曝光准备阶段，后续解决见下节。历史证据为 `capture-readout-development.json`、`waits-capture-state.json` 与 `capture-preparation-wait.png`。跟踪不清原厂状态位、不修改事件或调用成功回调。曾尝试 QEMU 7 GDB，但守护进程派生和多线程暂停后的栈请求未能完成，未取得可用栈；临时 GDB／前台执行代码已移除。

### 空热靴输入与闪光灯配置等待

上述历史等待已进一步定位：触发源设置返回，GV 配置消息发送返回 −1，调用方随后进入 `LockMutexForGv(300)`；原厂这个方法实际使用无超时的 `pthread_cond_wait`。原厂 `StrobeSvc` 线程存在，轮询日志定位到热靴连线判断／通信；消息队列容量未直接测量，不据此认定已满。

三星 `GPIO_EXT_STR_DET` 是 GPIO7(0)，原厂 `IsHotshoeDeviceAttachedByPort` 将低电平解释为接入。原厂 `gpio_init` 会清 DATA 的输出锁存位；此前平面 RAM 因而把这个输入脚错误地读成低，持续尝试访问并不存在的附件。查看器／`run_stock.py` 默认使用 `--hotshoe-input absent`，经 `NX300M_HOTSHOE_ABSENT=1` 明确提供空热靴的高输入：`gpio_init` 仍完整执行原厂代码，返回后只重新施加 GPIO7(0) 这一外部输入位。`gpio_data_get/set` 均保留原厂指令，不修改原厂“逻辑接入／移除”标志或虚构通讯应答。`--hotshoe-input unmodeled` 保留未建模输入供定位；没有实现外接闪光灯或 GPS 通讯模型。

`session_stock.py` 的 `hotshoe_input` 表示当前启动选择的外部输入情景。针对原故障的 ARM 检查确认原厂 gpio_init 后 GPIO56 读回为 1、相邻输出不受影响，原 Allegro 定时器／IRQ 检查仍通过，退出 0，见 `shutter-probe.json` 的 `GPIO_INPUT` 输出。

在空热靴情景下，原厂已自行返回 `capPrepare` 并进入 `capExposure`，`TriggerShutter` 也已返回。该情景原先停在曝光完成事件 mask 2 的无限等待；后续 PWM 计时实现见下节。此情景没有再经过前一次的 GV 等待分支，不将“拍摄准备返回”声称为外接闪光灯 GV 通讯已实现。历史证据为 `strobe-input-development.json`、`strobe-absent-capture-state.json`、`strobe-absent-capture-session.json` 和 `strobe-exposure-wait.png`。

### PWM 有限计时与曝光完成

原厂 `CFocalPlaneShutter::trigger → PWMReady → MG2StartPWM` 配置 PWM3，`mg2PWMThread` 在原厂 `pwm_wait_irq` 中等待。旧设备后端把 PWM 的 poll 一直屏蔽，因此无论配置何种计时都不会完成。新增 `runtime/pwm_timing.h`，通过 `run_stock.py` 或 `simulator.py` 的 `--pwm-timing` 显式开启，默认关闭。

后端复用 A32 MMIO 指令解码器，让原厂 `pwm_config`、`pwm_enable`、`pwm_disable`、`pwm_int_enable` 的实际寄存器读写继续执行。按两组通道的 0x100 间隔、0x1000 组偏移和 0x900 外部输入选择寄存器解释 START、INTCLEAR、prescaler、period、count；有限脉冲计时由 Linux CLOCK_MONOTONIC timerfd 执行，到期后按原厂单通道内核 handler 唤醒 poll。7.5 ns 计数时钟取自原厂 libudd 的 `period*10/75` 转换，尚未经实体时钟测量；周期端点／分频精度亦没有硬件校验。当前只实现有限脉冲的 LAST_END 中断，不实现连续波形、逐周期中断、双通道 handler 的 GPIO 动作、引脚波形或帘幕动力学。

本次原厂 MG2 配置是外部输入 3、下降沿。`/var/run/nx300m-pwm-events` 接受 `source*2+level` 编码；第一次电平只建立基准，重复电平、其他输入源、未 START 的通道均不触发计时。没有将未知连线擅自接到电机或 MG1 GPIO。开发会话在原厂已 armed 后输入：

```powershell
python tools/hardware_events.py pwm 7  # external 3 高电平基准
python tools/hardware_events.py pwm 6  # external 3 下降沿
```

`python tools/probe_pwm.py` 已执行原厂 ARM libudd：无边沿／错误输入源保持等待，60 ms 计时实测约 61.6 ms，通知消费后不重复返回，软件触发、disable 取消和 IRQ 屏蔽检查通过，退出 0，见 `outputs/firmware-runtime/pwm-probe.json`。这个检查针对旧后端无法完成 PWM wait 的实际故障，不验证物理快门。

完整开发会话使用上述显式下降沿后，原厂自行收到曝光完成、返回 `capExposure`，并进入和返回 `capFrameReadout`，没有直接调用成功回调或写原厂事件位。读出继续启动原厂 CSM SPI 序列，但图像 WDMA 尚无像素输入，原厂等待 2 秒后超时，随后清理路径触发 SIGFPE（目标信号 8），相机进程退出。当前仍无 IPCS 打开、新 JPEG 或 LUT 成像；不能把读出函数返回当作拍摄成功。完整日志和结果见 `exposure-pwm-development.json`。查看器已恢复普通回放会话，PWM／理想转子保持默认关闭。下一步是 PP 图像数据传输及超时清理路径。

### 录制 Bayer 读出及后台处理边界

`tools/replay_bayer.py` 使用 Conda 中的 LibRaw 解码本地 NX300M SRW mosaic，不做去马赛克或调色。原始解码尺寸为 5546×3714；原厂 still mode 3 的 global 尺寸为 5624×3714，active offset 为 (78,18)、active 尺寸为 5546×3696。原始宽度不匹配的输入由后端拒绝。显式开发 fixture 将原始 active 样本放入此窗口，缺失边框填未实测常量 0；它不是完整传感器帧或校色样本。packet、来源及边框信息见 `outputs/firmware-runtime/pp-active-bayer-fixture.json`。

原厂 PP 设置为非 SECC、12 bit，even/odd 地址相差 8448 字节，双行 delta 为 16896 字节。每两个 Bayer 像素按三字节打包；`tools/probe_bayer.py` 执行原厂 `dbg_get_pixelValue_fromPackBayer`，已与独立 LibRaw 样本核对。完整 31,375,872 字节 payload 写入原厂指定 SMA 缓冲后，全量只读读回逐字节相同；见 `bayer-mnf-config-readback.json`。

早期在 CSM ioctl 内立即提交完成中断，虽进入真实原厂回调，仍发生超时。原厂 `dd_csm_start_read_out_mode` 会在 ioctl 返回后清零完成标志，覆盖了过早的结果。现在仅转发这条原厂命令，保持其参数、指令与返回值；成功返回后等待下一条显式录制帧尾，再复制数据并进入普通 PP IRQ 队列。实际日志观察到原厂 read 消费编号 11、完成回调自行写 flag=1、等待函数返回，首次等待由此完成；最新 MnF 配置会话在 capEnd 及 PP ISR 重新注册后又进入第二次等待，仍打印 WDMA 超时（stdout 第 267 行，trace 第 2730–2759 行），第二次等待的调用来源与重新置位原因尚未查明。没有调用成功回调或修改原厂标志。这个时间安排是开发输入情景，未经实体传感器时序标定。

继续执行暴露了另一线程中的 RAW 后台除零。真实 core 和同进程映射定位到 `libudd!MnF_set_image_information+0x1f8`，分母为 `MnF_parameter.tile_size`。现有 EP 后端拒绝 MnF 路径申请，原厂后续仍配置未初始化参数。现已为 mode 20 的独立 MnF Bayer-split 路径提供独立 descriptor，可与 LVR 路径分别分配；仅接入路径配置，不生成 MnF 像素或完成中断。原厂已越过该处 SIGFPE 并打开 IPCS、执行真实 LUT 参数节点；当前没有启用自定义 LUT。

EP MnF Bayer 拆分及其 DMA 等待现已实现，并在完整拍摄调用链实际执行；像素范围见 [EP_MNF.md](EP_MNF.md)。第二段 PP、IPCS 运算和 JPEG 硬件输出仍未完成，原厂后续在 `DscJfif.cpp:242` 因 `mybuf.size > 0` 断言失败而 SIGABRT（信号 6）退出。模拟卡 DCIM 未生成文件。最新完整采集为 `outputs/firmware-runtime/ep-cs-grids-development.json`；早期统一报告 `bayer-readout-development.json` 和 `bayer-core-analysis.json` 保留原始故障。第二段 PP 输入与动态 DPC 边界见 [PP_MEMORY.md](PP_MEMORY.md)。

连续 IPCM 回放另修复了半按／模式切换中主输出暂时关闭导致的误退出：此时维持成对 VD 输入，不写像素、不通知 B2Y，等待真实输出和缓冲重新启用；有限帧数输入仍保留原失败条件。普通查看器默认不开启 Bayer fixture、理想转子和外部 PWM 情景。

## PP／IPCM／IPCS 与拍摄时序

`runtime/isp_preview.h` 提供 PP core、SSIF、3A、IPCM、IPCS、BWM、CSM、MIPI 的文件和 ioctl 接口，使用私有文件映射寄存器。设备布局来自三星平台资源和驱动：

| 设备 | 寄存器／存储 |
|---|---|
| PP core／SSIF／3A | 共用 0x50040000 起的 0xb000 区域；SSIF 偏移 0x2400，3A 偏移 0x100 |
| MIPI | 共用 PP 映射；CON／CSIM／CSIS 分别偏移 0x8000／0x9000／0xa000，MIPI fd 自身无 mmap |
| IPCM | 0x50060000，资源长度 0x2001，映射覆盖 3 页 |
| IPCS | 0x50050000，长度 0x10000，包含三路 3D LUT 表 |
| BWM | Sonics 总线 0x5e000000／0x5000，低速 DDR 0x50000000／0x1000 |
| 3A 结果存储 | 独立零初始化 1 MiB，物理标识 0x88000000 仅为本机模拟 token；没有统计结果或 DMA 完成事件 |

原厂 `libudd` 的六种设备打开接口均已返回成功；IPCM 的原厂映射与第二份映射共享数据。三路 LUT 通过原厂 `ipcs_open`／`ipcs_3d_lut` 写入后可逐字节读回，没有手工覆盖库内基址指针。无 IRQ 时 poll 不就绪，完成等待按 200 Hz 内核粒度超时并返回原厂定义的失败结果。证据：`outputs/firmware-runtime/isp-probe.json`。

`cf_pp_ioctl_init()` 的原先打开断言实际针对 `/dev/d4_csm`，并非 PP core 本身；该路径已接通。CSM 的 wait-VD／readout 现支持显式同步事件和原厂超时分支，详见下节。提供初始快门位置后，完整原厂程序已越过此前断言进入可操作界面。无初始位置的历史运行因电机归位超时退出，两种情况均记录在 `outputs/firmware-runtime/isp-development.json`。

源码选取当前为 204 份，含板级资源、内核配置、相关驱动、LCD／EP 及 PP／SSIF／3A、IPCM／IPCS 用户态类型定义；完整 gzip CRC 复核通过。独立检查：

```powershell
python tools/probe_isp.py
```

单独启动与查看器相同的设备情景时，先停止查看器中的相机：

```powershell
python tools/run_stock.py --program camera --display --preferences --gpio-preview --device-preview --shutter-preview --shutter-park 10 --isp-preview --spi-mmio --sensor-registers --d4c-registers --capture-boot --mmap-script-fix --ui-mod --lut-port --keep-awake --no-strace --seconds 0 --tag isp-parked
```

两个相机会话会共用部分临时硬件存储，因此不能同时运行。独立设备探针另挂载私有 tmpfs，不接触活动相机会话的硬件存储。传感器 SPI 已写寄存器模型和 MIPI 初始化已接通，取景帧、DMA 以及实际成像仍待实现。

## CSM 拍摄时序与显式 SSIF 事件

`runtime/csm_transport.h` 依据本项目三星 `d4_csm_ioctl.c`、`d4_csm_ctrl_dd.c` 和 `d4_pp_ssif_ioctl.c` 实现 CSM 两类命令及 SSIF 等待／poll／read。后端不自行生成同步时钟；私有 FIFO `/var/run/nx300m-ssif-events` 接受以空白分隔的十进制事件编号 0…12。0 为 VSYNC falling，1 为 USER_DEFINE_0／rising。IRQ 未打开或相应位被 mask 时，输入事件不会触发回调。

```powershell
python tools/probe_csm.py
# 仅向当前隔离会话输入一条同步事件，不是开始拍摄或生成图像。
python tools/hardware_events.py ssif 1
```

CSM 的 sensor SPI 命令交给同一原厂 `spi_open` 单例及 `spi_polling_write`，继续经过原厂 ARM 指令、MMIO/FIFO 和已写寄存器 peer。只使用原驱动为 NX300M 请求的 SPI 1／5；没有对应上下文或总线失败时记录结果，保留原驱动忽略 SPI 返回值的行为。该探针已核验 SPI 5 的传感器写入；SPI 1 的 D4C 传输另行实现并验证，见后面的 D4C 小节，不能由 CSM 探针推广。

时序遵循发布源码：

- wait-VD 等待 200 jiffies，超时仍返回成功，不执行 sensor 写入；已注册回调仍可能被后续事件调用。
- readout 等待 1000 jiffies，随后延迟 5 ms、写 sensor[1]。若此前未发生读出同步，则执行 D4C 命令、SSIF reset、PP WDMA 启动及 sensor[0] 的回退，最后返回 −1／EPERM。
- 显式 rising 事件在 ISR 分支执行上述读出启动，随后 ioctl 线程写 sensor[1] 并成功返回。发布 ISR 固定恢复 USER_DEFINE_0 回调、完成索引 1；其他 reference 不被擅自等效为索引 1。
- PP DMA 窗口位于 PP +0x3000；仅编程中断使能、WDMA enable 和 vsync write enable，不写入图像、不报告完成。
- SSIF poll/read 沿用原驱动的 512 项字节队列；read 写出一个事件字节，返回剩余队列长度，可能返回 0。空队列的非阻塞读返回 EAGAIN。WAIT_INT 使用独立 completion，不能被另一种事件放行。

隔离探针退出 0，验证队列的返回值、mask 下保持等待、解除 mask 后由显式事件放行，以及两种 CSM 命令通过原厂 SPI 写入后的寄存器读回。wait-VD 无事件实测约 1.002 s；readout 无事件约 5.011 s 后执行回退并返回 EPERM。WDMA 控制字段保留其他位，完成状态仍为 0。证据在 `outputs/firmware-runtime/csm-probe.json`。

当前查看器已加载新后端，`GET /status` 的 `ssif_event_input_ready` 表示 FIFO 已建立。CLI 的 `input_written=true` 只说明事件已写入；是否被 mask、派发或消费需看会话日志，不等于原厂回调执行完成。此前完整原厂会话接收明确输入的事件 1，菜单仍可用，尚无取景帧或 ISP 输出；记录在 `outputs/firmware-runtime/csm-development.json`。FPS 计时统计和实际 DMA 运算仍未实现。后续 PP 完成信号接口见下节。

## PP 完成信号与回调队列

`runtime/pp_events.h` 按三星 `d4_pp_core_dd.c`／`d4_pp_core_ioctl.c` 实现七类 completion、512 项回调队列以及 MIPI callback 订阅。私有 `/var/run/nx300m-pp-events` 接受十进制的非零 IRQ 状态位组合，CLI 同时接受十六进制：

```powershell
python tools/probe_pp_events.py
# 开发调试输入；没有执行 DMA，也没有向缓冲区提供像素。
python tools/hardware_events.py pp 0x80000
```

| 位／状态值 | 原厂处理 |
|---|---|
| bit 19／0x80000 | PP WDMA completion、回调队列编号 11，关闭 WDMA vsync-write bit 28 和该中断使能，更新 CSM done |
| bit 22／0x400000 | PP RDMA completion、队列编号 12 |
| bit 0／4／5／6／7／8 | 共用 LUT-load completion |
| bit 2 | DPC LUT-generation completion |
| bit 9 | VFPN write completion |
| bit 23／0x800000 | 已订阅时进入 MIPI RX 回调队列编号 13，保留该中断使能 |
| bit 26／0x4000000 | 已订阅时完成 MIPI TX completion，随后清除一次性 callback |
| bit 21／0x200000 | 无同次 LUT-load 信号时更新 CSM WDMA error |

PP IRQ 未打开或状态组合与当前使能位无交集时，输入不派发。每份输入被当作一次明确提供的 ISR 状态快照，未模拟被遮罩状态的硬件锁存、IRQ 电平及寄存器写入触发；不能将这部分推广为完整中断控制器模型。发布源码的 `PP_MIPI_POLL_ENABLE` 将 RX 路由到队列而不是 RX completion，后端保留此区别。

无信号时阻塞等待或按 200 Hz 粒度超时；init ioctl 清除对应 completion。poll/read 保留与 SSIF 相同的非阻塞 EAGAIN、写出一个字节并返回剩余队列长度的 ABI。CSM readout 重新初始化 PP WDMA completion 和两项状态；GET_CSM_WDMA_STATE 现读回明确输入的 done／error 信号，而非固定零。

隔离探针调用未修改的原厂 `pp_core_dd_open_reg_base`、`pp_dd_wait_wdma_done`、`pp_core_dd_com_wait_lut_load_done_with_timeout`，退出 0。验证无事件超时、mask 下不放行、WDMA 不误放行 LUT、LUT completion 恢复等待、MIPI 订阅／取消与 CSM done／error 读回。未覆盖的逐位路径不作为已独立验证项。因 CSM 共用 PP completion，另运行现有 `probe_csm.py`，仍退出 0。

查看器加载记录在 `outputs/firmware-runtime/pp-events-development.json`，探针记录在 `pp-events-probe.json`。`GET /status` 的 `pp_event_input_ready` 仅确认输入 FIFO 已建立。所有完成信号来自明确输入；没有 DMA 复制、传感器帧或 ISP 运算，不能由 completion 成功推导有效图像。

## SPI 配置与片选

`runtime/spi_control.h` 实现原厂启动实际使用的 `HS_SPI_WAIT_TIME`、`HS_SPI_BURST`、`HS_SPI_RESET` 和引脚选择控制。依据是本项目已提取的三星 `linux/drivers/spi/hs_spi_ioctl.c`、`hs_spi.c` 与 `hs_spi_ioctl.h`。时钟、位宽、CPHA／CPOL 等仍由未修改的 `libudd::spi_config` 直接写入 mmap 寄存器；设备后端额外保留同一 backing file 的映射，以处理内核侧 reset。

等待时间 ioctl 按原厂整数运算顺序计算 `loops_per_jiffy / 1000 * HZ * milliseconds`。本机显式使用 LPJ=1000、原厂配置 HZ=200，得到每毫秒 200 次的循环预算。这是模拟器的预算输入，不是 NX300M CPU 校准数据，不保证原厂轮询在主机上具有相同的墙钟时间。普通 RAM 映射模式不生成 status／RX 完成数据；下面的指令级 FIFO 模式按所选 peer 处理收发。

突发传输控制按三星驱动切换 CS GPIO 电平；reset 根据 CPOL 设置时钟引脚，并脉冲 CH_CFG 的 SW_RST 位。GPIO 方向及电平操作调用原厂 libudd，寄存器后端使用 `gpio_preview.so` 提供的私有银行，未加载该后端时返回 ENOSYS。引脚复用记录按控制器通道保存；没有模拟引脚电气波形或外部设备响应。原厂源码在 reset 时将 `~inversion` 传入 GPIO 输出；两个结果都非零，当前保留其驱动 CS 高电平的实际语义。

```powershell
python tools/probe_spi.py
```

探针使用原厂 `spi_open/spi_config/spi_polling_read`，独立挂载私有 `/tmp`，不共享活动相机的 SPI backing file。结果：16 位配置读回正确，5ms 输入变为 1000 次预算，burst 两次操作分别读回 CS=0／1；无 RX 输入时原厂返回 −1，接收缓冲 `0xa55a` 保持不变，未知 ioctl 返回 ENOTTY。记录在 `outputs/firmware-runtime/spi-probe.json`。

默认查看器已加载此控制后端，原先的 `0xc004680d` 和 `0x4004680f` ENOTTY 不再出现。首次控制后端的运行日志路径和现场检查在 `outputs/firmware-runtime/spi-development.json`；后续 FIFO 和寄存器模型进展如下。

## SPI 指令访存与 Aquila 寄存器模型

普通 mmap RAM 无法处理 FIFO 的逐次读写。`runtime/mmio_trap.h` 只保护已登记的 SPI 页，通过 Linux ARM EABI SIGSEGV 上下文处理原厂 A32 LDR／STR、字节／半字及有符号单次访存，执行设备回调后恢复下一条原厂指令。可处理相同数值的连续 TX 写入及每次 RX 弹出，不改写原厂代码。注册页之外的故障转交原有处理器；未支持的指令记录地址和指令字后保留故障。当前不支持 Thumb 或成组访存，现有原厂 SPI 路径均已实际执行。

`runtime/spi_fifo.h` 实现 master TX/RX FIFO、接收弹出、状态计数、pending 清除和 reset，并按配置处理帧位宽、有效位和 swap。TX 使能后按寄存器事件完成移位，没有墙钟 SPI 波形、DMA、slave 外部时钟或 IRQ 模型。满 FIFO 的六位计数饱和为 63，当前未声称与芯片满状态编码完成实测比对。

```powershell
python tools/probe_spi.py --mmio
python tools/probe_spi.py --mmio --peer loopback
```

两种情景分别验证：没有 RX 事件时原厂读取返回 −1 并保留缓冲；显式回环 peer 下，未修改的 `libudd::spi_polling_rw` 以 8／16／32 位收发重复和不同的数据均返回 0，内容一致。对应 `outputs/firmware-runtime/spi-mmio-none.json` 与 `spi-mmio-loopback.json`。回环只用于独立探针，相机会话不会使用它。

`runtime/aquila_registers.h` 按原厂 `CAquilaDriver`／`CAquilaCt3Driver` 的总线格式处理 32 位帧：高半字为地址、bit 31 为读标记、低半字为数据。通道由原厂 `get_com_channel(2)` 取得；原厂 NX300M 版本表为 SPI 5，和运行日志一致。原厂 SPI “只写”函数仍会等待并丢弃收到的字，因此该 peer 在写入时也提供明确的 idle-high MISO 输入；这不是外设 ACK。其他通道保持未提供 RX 事件的情景。

寄存器只有在原厂实际写过后才具有定义值。已写地址可读回；未定义地址提供 idle-high `0xffffffff`（原厂 16 位返回值为 `0xffff`），并记录 defined=0，不填入芯片 ID 或复位默认值。该模型没有模拟物理复位、寄存器副作用、模拟电路或帧生成。原厂普通启动主要写寄存器，ReadRegister 的直接调用来自调试入口，因此读回验证不能作为原厂启动需要某个芯片 ID 应答的证据。

```powershell
python tools/stock_command.py st cap cis regr 700c
```

现场验证在 `outputs/firmware-runtime/sensor-register-probe.json`：原厂 `st → cis regw/regr → CAquilaCt3Driver → CSPIDriver → libudd → MMIO/FIFO → register peer` 将 `0x700c` 从 `0x021a` 改为 `0x021b`，原厂读回一致，再恢复 `0x021a`；读取未定义地址 `0x7fff` 得到 `0xffff`。原厂 `st` 客户端正常仍退出 1，证据来自服务端结果与总线日志。`stock_command.py` 现排除单独的 `CaptureConsole` 前导行，避免它让等待逻辑在实际命令返回前提前结束。

寄存器状态存放在会话私有 `/var/run/nx300m-sensor-registers.bin`：前 64 KiB 为 32768 个 little-endian uint16，随后 4096 字节是定义位图。`GET /status` 返回模型种类、已定义数量和若干采样值；这是调试采样，不是跨字段事务快照。当前正常启动实际定义 91 个地址，写寄存器失败日志已消失。该阶段曾暴露 MIPI 打开失败，历史记录在 `outputs/firmware-runtime/spi-mmio-development.json`；后续解决结果见下节。

查看器默认启用 `--spi-mmio --sensor-registers`。单独的 `--spi-mmio` 使用无 RX 事件的情景；两个选项都不启用回环。开启 QEMU syscall strace 会记录大量正常 MMIO SIGSEGV，应使用 `--no-strace` 进行交互。运行报告保留“观察到 SIGSEGV”字段，并另列 `mmio_sigsegv_expected` 和 `target_terminal_sigsegv`，不能把受控访存异常等同于原厂进程崩溃。

## MIPI 传输与 D4C 接收路径

`runtime/mipi_transport.h` 按三星发布的 `d4_mipi_ioctl.c`／`d4_mipi_ctrl_dd.c` 实现 `/dev/d4_mipi`。原厂 `mipi_open` 通过 `pp_get_virtual_reg_base` 取得共享映射，三个导出基址由原厂库自行设置，不覆盖指针。注册／注销中断更新 CSIM／CSIS mask；无输入事件时，完成等待保留超时结果，不产生帧、PHY lock 或 DMA 完成。

```powershell
python tools/probe_mipi.py
```

探针链接未修改的 NX300M 1.15 `libudd`，在独立 tmpfs 中执行 `pp_core_open`、`mipi_open`、`mipi_device_enable` 和 `mipi_csis_use`，退出 0。核对三个基址、PP 第二映射共享、原厂 1280×720 初始化及 800×600 更新、两组 mask 注册／注销。2 jiffies 输入实测等待约 10 ms 后返回 0，输入仍为 2。保留原驱动 quirks：GET_PHYS 返回 1；六个 `_IO` 中断命令执行副作用后返回 −1／EPERM；read／write 返回 0。DPC PDMA 尚不支持，显式返回 EOPNOTSUPP。证据在 `outputs/firmware-runtime/mipi-probe.json`。

默认相机会话已实际加载此后端，MIPI 打开和 enable 失败日志已消失，原厂菜单与 LUT 面板可操作。现场 CSIS enable 位为 1，分辨率寄存器为 256×7；原厂调用链为 `cf_pp_d4c_video_mode → cf_pp_d4_mipi_initialize`，它将传输大小分成宽 256 的行并选用 422 格式。这是 D4C 接收传输的配置，不能作为传感器图像尺寸或有效帧证据。反汇编在 `outputs/firmware-runtime/mipi-pipeline.asm`。

`GET /status` 的 `mipi_register_model` 读取 PP backing RAM 的 CON／CSIS 控制、mask、源状态和编程尺寸。这是调试采样，没有跨寄存器快照保证。当前原厂仍报告等待 Capture Framework 通知超时，未产生传感器帧、ISP 或 JPEG 输出；下一步需处理 D4C 接收／PP-SSIF 事件及数据路径。完整现场记录为 `outputs/firmware-runtime/mipi-development.json`，菜单截图为 `mipi-stock-menu.png`。

## D4C SPI 已写寄存器与突发载荷

`runtime/d4c_registers.h` 依据同一原厂文件系统的 `libd4c.so.0.19` 实现 SPI peer。通道通过原厂 `get_com_channel(3)` 取得，当前为 SPI 1；Aquila 仍为 `get_com_channel(2)`／SPI 5。查看器默认启用 `--d4c-registers`，独立运行需同时开启 `--spi-mmio`。

普通命令由 32 位头和一个数据时钟字组成，头部低 16 位为地址、bit 16 为写入。原厂单次写发送两字；读先用原厂 `spi_polling_write` 发送命令，再用 `spi_polling_read` 发 dummy 字取得返回值。后端提供明确的 idle-high MISO 时钟输入，控制器 FIFO reset 不清除外部 peer 的待处理命令。未知地址返回 `0xffffffff` 并记录 defined=0，不填入芯片 ID、复位默认值或运算结果。

普通 burst 及 DPC pipe burst 按原厂编码接收载荷，原样保存最近一次完整包，不推断寄存器递增、DPC LUT 消费或 DSP 效果。文件 `/var/run/nx300m-d4c-burst.bin` 以两个 little-endian uint32 保存命令、后续字数，再跟随载荷；使用临时文件完成后 rename。单寄存器数据在 `/var/run/nx300m-d4c-registers.bin`：前 0x40000 字节是 65536 个 uint32，后 0x2000 字节是定义位图。`GET /status` 的 `d4c_register_model` 和 `d4c_burst_payload` 分别报告已写地址采样及完整包元数据。

```powershell
python tools/probe_d4c.py
python tools/stock_command.py st cap d4c regr 0c20
```

探针直接执行原厂 `d4c_register_write/read/burst_write` 及 `libudd` SPI 指令，退出 0。SPI 配置通过原厂 `d4c_if_config` 所调用的内部 helper（该版本库地址 0x5c08）设置，没有覆盖库内指针。寄存器 0x4008 的四组读回包括命令形状的数值、零和全 1，均一致；4 字普通 burst、64 字 DPC pipe burst 的载荷逐字一致，未据此定义额外寄存器。记录在 `outputs/firmware-runtime/d4c-probe.json`，反汇编在 `d4c-register-transport.asm`、`d4c-spi-config-helper.asm`。

完整相机会话已核对 QEMU 参数包含 `NX300M_D4C_REGISTERS=1`，启动时实际写入 1148 个地址；原厂 `st → CD4CDriver::Read → libd4c → libudd → FIFO peer` 读回 `0x0c20=0x00444444`。相应 PP MIPI RX 中断使能为 0x00800000，DMA enable 为 0x9。图像 WDMA 地址仍为零，原厂仍报告捕获通知超时；这些结果说明传输及初始化推进，没有证明 PAF、传感器像素或 ISP 输出。记录在 `outputs/firmware-runtime/d4c-development.json`。

原厂 D4C 调试方法对缺失参数检查不足；本项目 CLI 拒绝裸 `cap d4c` 和缺少地址／数值的 regr／regw。`st` 客户端正常仍退出 1，读取结果以相机服务端日志为准。

## SMA 剩余内存与回放切换

正常取景切换到回放时，原厂 `sysmmap_osif_update_remain_buf` 会调用 `SMA_SET_REMAIN_BUF`（`0xc008730a`）。旧后端返回 ENOTTY，触发 `sys_mmap_manager.c:1317` 断言；现场保存在 `outputs/firmware-runtime/sma-remain-buffer-crash.json`。

发布驱动的 `d4_sma_if.h` 启用 `SMA_TEMP_RESERVED_AREA`。后端现在按原厂 8 字节 `{addr,size}` ABI 更新 borrowed region：非零 addr 时 `top=addr+size`、cursor=top、capacity=size；addr=0 清空。该区域的释放走原厂专属分支，普通 CMA/BMA 记账不变。原厂 `d4-cma.c` 没有调用 remain getter，因而没有添加推测性的分配 fallback。坏指针通过内核复制返回 EFAULT，不直接解引用后崩溃。

专属 probe 实际执行未修改的原厂 set／clear 调用，退出 0；只读输入、非法指针和 CMA 记账检查通过。生产构建后又完成正常取景→回放→返回取景：实际配置 `{addr=0xda400000,size=0x01400000}`，返回时清空，原厂进程持续运行，未再次出现该 ENOTTY 或断言。回放显示原厂 `No Image File`，返回后照片回放及 OSD 恢复；模拟 SD 卡没有新照片。记录及截图为 `sma-remain-buffer-recovery.json`、`sma-remain-playback.png`、`sma-remain-return.png`，均在 `outputs/firmware-runtime`。

## 原厂调试服务

`tools/stock_command.py` 在当前相机的挂载／网络／IPC 命名空间调用原厂工具。原厂 `st` 客户端投递消息后以 1 退出，结果由原厂相机写入 stdout；工具分别记录客户端退出码、客户端输出和服务端日志，不能仅凭客户端退出码判断失败或成功。

```powershell
python tools/stock_command.py st cap capdtm getusr PW
python tools/stock_command.py st cap capdtm getvar PWCOLOR
python tools/stock_command.py st cap capdtm getvar PWCONTRAST
```

已读回 PW_STANDARD（0x100000）、PWCOLOR=0x00000000_ffffffff，以及 PWSATURATION／PWSHARPNESS／PWCONTRAST=0x00000000_000ff004。后者是原厂标量零档的打包值；三项均使用 `(signed_value << 20) | 0xff004`，原厂 UI 的范围是 −4 至 +4，依据为 `di-camera-app-nx300` 的 `0x1bb6b4–0x1bb7c0`。不能按 NX500 七维格式解释。

该固件的 `usrlist` 返回 `Unkonwn Operation`；裸 `st cap capdtm` 会因原厂参数处理错误引发 SIGSEGV，工具已拒绝这个已知故障调用。`setvar` 还使用 32 位有符号 `strtol` 解析十六进制，负档打包值如 `0xffeff004` 会溢出成 `0x7fffffff`，即使服务打印 `VariableData is set`。复现并恢复的完整记录在 `outputs/metol/stock-pw-writeback.json`。

## 原厂画面参数控制

`runtime/pw_control.h` 随 `ui_mod.so` 加载，在原厂 Ecore 主循环上接收本地参数请求，直接调用原厂 `CDataManagementService::SetData`，避免上述文本解析错误。它保留原厂锁和 `OnNotify` 路径；不直接改写 IQ 内存。三个标量对应变量 ID `0x118/0x119/0x11a`，原厂 `CCapParamIf::convertParamID` 将其转发到 `CIQDataRepeater` 的 `0x2e/0x2f/0x30`。原厂颜色角度对应 `PWCOLOR`（`0x117` → `0x2d`），四项都已接通写入和读回。

```powershell
python tools/picture_wizard.py
python tools/picture_wizard.py --saturation -2 --sharpness 1 --contrast 3
python tools/picture_wizard.py --color-tone 179
python tools/picture_wizard.py --color-tone -1
```

统一遥控页“设置 → 机身设置…”中的原厂画面参数提供读取和差量应用；兼容入口`GET /picture-wizard`读取，`POST /picture-wizard`接受四项的任意子集，例如`{"saturation":-2,"contrast":3,"color_tone":179}`。三个标量必须是−4…+4的整数；`color_tone`为−1（关闭）或0…359的整数角度。响应分别给出`raw`、`iq_raw`、`iq_matches`。原厂调用失败会尝试恢复本次涉及的参数。请求带短期有效时间，避免主循环长期阻塞后再应用旧命令。

桥接还调用原厂 `CIpcIfInfo::GetColorTone/GetSaturation/GetSharpness/GetContrast`，返回 `decoded_indices`，从而检查原厂实际解码结果。三个标量将打包的当前值和 −4／+4 边界归一化为 0…8，中性档是 4。原厂只在 IQ mode `1/2/3/4/8` 使用这些标量；其他模式可能回到中性或 SmartArt 参数，因此返回 `iq_mode` 和 `scalar_mode_enabled`，界面提示当前模式是否采用三项调整。该结果是调用原厂消费者得到的，不是 Python 按写入值自行计算。

颜色关闭值 −1 解码为索引 0；非负角度 `a` 解码为 `6 + floor((2*a+5)/10)`。still／liveview 共用相同的 14 份原厂 3×3 色彩矩阵，在相邻矩阵之间以六档插值。索引 0 是单位矩阵，角度 0 对应第一份有色矩阵，不能把角度 0 当成关闭。矩阵含交叉通道和中性轴偏色，故此颜色旋钮也不等同于 NX500 的独立 RGB 增益或普通 HUE 旋转。分析在 `outputs/metol/pw-color-tone.asm`、`pw-iq-consumers.asm` 与 `pw-color-model.json`。

当前验证了 −4／+4、角度 0／179、混合更新及恢复：数据层与 IQ 层逐字一致，原厂解码索引分别符合其当前值，记录在 `outputs/metol/pw-control-probe.json`，浏览器操作截图在 `outputs/metol/pw-control-browser.png`。第一次在启动阶段读取曾退出 124；原厂主循环稍后处理了该只读请求。随后正常运行中的读写均已响应，现增加请求有效期。

这是当前会话的原厂运行参数，不写 `prefman`、不持久化保存。原厂菜单或模式切换可能覆盖它们。IQ 读回和原厂解码证明参数通知已传入原厂图像参数层，仍没有传感器或成像结果。七维配方继续保留原值；RGB 独立增益、HUE 与 NX300M 色彩旋钮的等效关系尚未建立，不能将这四项接口称为完整七维配方后端。

## WSL 启动通道

Windows 查看器曾因 `SO_REUSEADDR` 同时存在多个 8765 监听者，旧实例可能继续接受请求，导致新启动选项未生效。已核验并结束本任务残留的查看器进程，改用 `SO_EXCLUSIVEADDRUSE`；`GET /status` 返回实际 `viewer_pid`。重复启动检查以退出 1 被拒绝，原查看器和 Linux 相机 namespace 均保持不变，监听者只剩一个。记录在 `outputs/firmware-runtime/viewer-exclusive.json`。这不涉及实体相机，也不改变 Linux 隔离边界。

观察到长驻 Windows `wsl.exe` 监督相机 PID 命名空间时，Linux 内的相机和网关仍响应，但新的 WSL 命令无法启动。关闭其他辅助客户端没有解除阻塞，结束相机启动客户端后，两个已排队的 echo 命令立即返回。

`--seconds 0` 现用短命 WSL 客户端启动脱离 Windows 客户端的 Linux 监督进程，日志仍由 Linux 直接写入。启动器退出 0 只代表发起成功，查看器继续检查真实相机进程。新方式的启动、停止、辅助进程清理及遥控原图检查均已通过；Windows 不再保留相机对应的长驻 WSL 启动客户端。

后续WSL整体无响应时，用户授权重启Debian后恢复。`session_stock.py` 的Windows查询现有4秒截止时间，只清理本次查询的Windows进程树；健康并发请求有界等待并共享同一结果，健康缓存0.5秒、故障退避2秒，未回收客户端不再派生新查询。故障返回 `backend_available=false/camera_state=unknown/running=null`，清除旧PID与ready；停止前强制刷新实际身份。查看器返回HTTP503、显示后端不可用和相机状态未知，并禁用启停，避免把查询失败当成停止或重新启动相机。

两文件编译与定向离线超时、并发和刷新检查通过。仅重载Windows查看器后，真实namespace193/camera277保持不变，两条并发只读HTTP请求均成功，回放2529帧、失败0；内置浏览器显示“运行中·照片回放”。证据为 `outputs/firmware-runtime/viewer-backend-recovery.json/png`。没有为验证清理路径再次制造真实WSL卡死，故该故障下的Windows进程树回收仍只有离线覆盖。


### 后续内存处理与 EP DMA 队列

新增调用来源观测确认两次等待来自不同路径：首次是 `CFrontEndPP::stillBayerOTF`（返回地址位于库内 `0x1a94b8`），随后是 `CFrontEndPP::stillBayer`（`0x1a9050`）。后者执行 `cf_pp_capture_frame_read_write_start → pp_rw_dma_start`，是内存 RDMA→PP→WDMA，不能再次复制传感器 fixture 代替这段处理。当前转发式启动钩子仅保存其 PP 寄存器供开发，不写图像、不产生完成通知。

`EP_IOCTL_DMA_INTR_WAIT_QUEUE_INIT` 与 `EP_IOCTL_DMA_INTR_WAIT` 已按发布的驱动实现独立的 18 个 DMA completion 队列，保留参数检查、`O_NONBLOCK` 下 `ENOTBLK`、200 Hz 超时取整及无任务时 `ETIMEDOUT`。原厂 libudd 将失败 ioctl 转成自身的 −2 返回值。`tools/probe_ep_dma.py` 通过原厂 ARM 接口运行，11 ms 请求约 15.44 ms 超时，边界参数与非阻塞错误核对通过；当时没有像素任务或完成事件输入。报告见 `outputs/firmware-runtime/ep-dma-probe.json`。后续 MnF 软件 Bayer split 与实际对象／边界观测已完成，证据见 `PP_MEMORY.md`；这不表示后续 PP/IPCS/JPEG 已完成。

第二段内存 PP 现已接入真实任务准备：`pp_rw_dma_start` 先执行原厂调用，再由
`runtime/isp_preview.h` 引入的 `pp_memory_task_impl.h` 解析当前寄存器。
解析器按原厂 PP_CH_4／packed-12／SECC 模式计算四路物理地址、行距、有效
访问跨度和含行 padding 的完整范围，通过实际分配器映射表核对完整范围。
它不修改像素、补尾行或发送完成通知；尚无完整像素执行器时返回 `-95`
（EOPNOTSUPP），地址／布局错误分别记录 `-14`／`-22`。
当前准备结果保存到会话私有 `/var/run/nx300m-pp-memory-task.bin`，与原有
`nx300m-memory-pass-pp.bin` 寄存器快照配合使用。

本轮仅重建受影响的设备模块到独立
`outputs/firmware-runtime/pp-memory-task/pp-memory-device.so`，通过以下 probe
执行未修改的原厂 `libudd::pp_rw_dma_start`，没有在主相机会话触发拍摄：

```powershell
python -X utf8 tools\probe_pp_memory_task.py
```

独立挂载 namespace 使用实际 `ep-cs-grids-nx300m-memory-pass-pp.bin` 配置
及私有哨兵 RAM。ARM 构建 `-Wall -Wextra -Werror` 通过，probe 退出 0：

| 任务范围／结果 | 实测值 |
|---|---|
| mosaic／plane | 5624×3714／2812×1857 |
| 单行有效 payload／stride | 4218／4224 bytes |
| 每平面有效跨度／完整行范围 | 7843962／7843968 bytes |
| 四平面 RDMA、WDMA 完整范围 | 均在 probe 的实际私有映射内 |
| 未映射／未对齐／溢出／短行距 | EFAULT／EINVAL／EINVAL／EINVAL |
| 原厂 start 返回后输入、输出完整缓冲 | 共两份 31375872 bytes，逐字保留原哨兵 |
| PP 完成回调队列 | 空；没有产生 RDMA／WDMA 完成 |

`probe.json`、stdout／stderr、`task.bin` 位于上述独立目录。这里验证了真实
启动路径的任务解析和拒绝行为，不是 PP 像素完成，也不是一次新拍摄。

本轮继续核对了原厂算法来源，具体阻碍为：

- 动态 DPC：原厂 `CPreProcessorD4::SetDynamicDpc(bool)`（`0x346158`）生成
  参数，`pp_core_dpc_dynamic_set_info` 仅写硬件寄存器。当前 `0x4040=0x01013fff`
  的 correction mode=1；原厂 `pp_core_type.h:207–209` 定义它为
  `STATIC_METHOD_CORRECTION`，mode=0 才是四邻域方式。寄存器另有
  `DPC_CORRECTION_TH/ZCORRECT_RATIO/ZCORRECT_RSW`，公开源码没有给比较、
  邻域选择、替换及定点规则。阈值 16383、gain 全零和 static enable=0
  均不能证明 dynamic 分支恒等，不能用通用中值滤波代替。
- CS：本次已保存的 42720-byte 上传源为四 lane 全单位系数，89×60 网格
  和源布局已有原厂指令依据；但上传源、当前片上 LUT 及像素插值／乘法规则
  是不同证据，尚未建立完整软件消费路径。不能仅凭源全单位宣布这一步完成。
- 位宽／输出：实际 RDMA bit-shift 配置对应已有 12→14 数值 helper，而
  WDMA 选择 12-bit packed 输出。新增 `pp-memory-task-format.asm` 核对原厂
  `pp_dd_wdma_set_info` 只编程 bit-format／channel／stride，没有 CPU 像素
  的 14→12 截位、舍入或饱和公式；KNEE enable 本次关闭，也不能据此猜公式。
  该 helper 本轮没有作为孤立演示重复运行或接成完整 PP 输出。
- DAMP／输入尾部：本次 offset=0、gain=256 的寄存器仍不替代硬件像素公式。
  已观察的 MnF 尾 9 行在 split 前后全零且相同，没有确认其原厂生产者；
  本轮不改变 3714 行 RDMA 请求，也不补齐或跳过这些行。

因此当前没有解除 `stillBayer` 的第二段等待。要实现该段，需要匹配这些实际
配置的硬件像素规则或已确认的原厂 CPU 实现；配对硬件输入／输出还需覆盖
DPC 判别边界、非均匀 CS 和位宽量化。不能以首次 Bayer fixture 或软件
split 的输出冒充这段 PP 已执行。

## JPEG 任务、设备打开失败与 CPU 编码边界

原厂静态调用链已经定位到 `libsif.so`：
`DscJfif::EncodeJfif`（`0x28c50`）经 PLT 调用
`JpegEncode(sJpegOp&, bool)`（`0x28a88`），后者调用已注册的 HAL 函数；
当前 HAL 是 `DscJfifHal::Encode`（`0x29830`），内部使用原厂 `libudd` 的
`jpeg_open → jpeg_init → jpeg_encoder → jpeg_deInit → jpeg_close`。

**已观察的空 size 失败首先来自 JPEG 设备打开失败。** 已保存开发 stdout
打印 `JPEG Device could not be opened.`；HAL 在 `0x29858` 调用 `jpeg_open`，
失败即返回 false，尚未构造和执行编码请求。`libudd` 打开的设备是
`/dev/d4_jpeg`。上层 `JpegEncode` 调用前把 produced-size 清零，
`EncodeJfif` 在调用返回后的 `0x28e98..0x28ec8` 才检查 `mybuf.size > 0`
并触发源码第 242 行断言。因此这条已观察失败路径不是“成功编码后结果为空”，
也不是编码前的输出容量断言；仅替换 `jpeg_encoder` 到不了当前失败点。

`sJpegOp` 是可实际接入的任务边界。下表为原厂 32-bit ARM ABI 中的字节
偏移；地址是交给原厂库进一步转换的调用方地址，不可直接当作宿主指针：

| 偏移 | 实际消费字段 |
|---|---|
| `+0x00 / +0x04` | width / height |
| `+0x08 / +0x0c` | Y even / odd 地址 |
| `+0x10 / +0x14` | 第二分量组 even / odd 地址；随 pack 可含交织 CbCr |
| `+0x18 / +0x1c` | 第三分量组 even / odd 地址；部分 pack 下为 0 |
| `+0x20 / +0x24` | Y / chroma 的地址 delta |
| `+0x28` | JPEG 输出缓冲地址 |
| `+0x2c` | YCC type |
| `+0x30` | 实际输出字节数，调用前清零 |
| `+0x34 / +0x38` | pack type / quality enum |
| `+0x3c` | timeout；`EncodeJfif` 当前写入 2000 |

`JpegEncode` 在 `0x28b0c` 调用
`DscUtils::GetMaxEncodeSize(width, height, quality, false, 12)`，将返回值作为
HAL 的输出容量参数；它不是 `sJpegOp` 中已记录的分配长度。任何消费端仍须
核对真实 SMA 映射范围，不能仅凭容量公式写入输出。

HAL 构造的 `libudd` 请求中，`+0x10/+0x14` 是宽高，`+0x18` 是输出长度，
`+0x1c..+0x30` 是六个源地址，`+0x34/+0x38` 是 delta，`+0x3c` 是输出
地址，`+0x40` 是 timeout，`+0x44` 是按 `(capacity >> 10) - 4` 得到的
16-bit 容量字段，`+0x48` 是错误结果。原厂硬件编码实现 `0xd3594` 使用
ioctl `0xc0086a07` 等待完成，再读取寄存器 `0x6bc` 填入长度，并通过
`0x80046a09` 读取错误状态。当前打开失败没有提供一次成功执行的实际请求
快照，不能把静态字段布局当作已采集到的运行参数。

原厂固件同时带有可用的 CPU 编码实现：
`libmmutil_jpeg.so.0.0.0::mm_util_jpeg_encode_to_memory`（`0x2cb0`）调用
内部软件函数 `0x1d48`，其 ARM ABI 参数依次为
`dst**、size*、src、width、height、format、quality(0..100)`。它调用同一
固件的 `libjpeg.so.8`，不依赖 `libudd` JPEG 设备。format 3 的分支明确为
RGB888；format 0..2 进入的 raw-data 分支固定设置 H2/V2，消费连续
8-bit planar 4:2:0，并按 16 行批次取样，没有尾行保护。

将它用于真实任务仍须实现以下边界：按原任务的 pack、even/odd 和 delta
整理输入，只接受已经确认的格式和完整可读范围；先限制为宽高均按 16
对齐的 planar 4:2:0；编码成功且长度不超过原任务容量和真实分配范围后，
才复制到原输出地址并回填 `+0x30`，让原厂 EOI 检查和保存链继续执行。
HAL 将 YCC type 0 配成 4:2:2，非 0 配成 4:2:0；CPU raw 分支不能静默
降采样承接 4:2:2。原厂 quality enum 0/1/2 对应硬件量化表 index 10/6/2，
目前没有它与 CPU quality 百分比的等价映射。

现已直接使用原厂 `libjpeg.so.8` 实现独立 CPU 任务适配，保留 4:2:2／4:2:0、
planar／interleaved 和 even／odd 布局；显式扩展尾行与右边缘，不受上述
`libmmutil_jpeg` 包装器的16像素对齐限制。构建、原厂量化表依据及隔离运行结果
见 [JPEG_ENCODE.md](JPEG_ENCODE.md)。34×18 合成输入的16种布局组合已实际编码，
各画质档位输出已独立解码；未知来源或缓冲范围不足时拒绝输出。

原厂 `EncodeJfif` 的 pack mode 2（连续 Y＋交织色度）已在隔离 ARM 进程中
经真实 PLT 调用编码模块，原厂容量计算、长度回填及尾标记检查通过。原厂
软件 raw-data 路径还要求关闭 `do_fancy_downsampling`；遗漏它时，文件结构
虽合法，色度仍会明显错误。关闭后，该生成样本解码的 Y 最大误差为1，Cb／Cr
逐像素一致；此结果不代表完整拍摄或理光风格已经校色。原厂
pack mode 1 的色度地址间距与所传 stride 不一致，尚未确认硬件解释；原厂
hook 明确拒绝 planar task。独立 CPU API 的显式 planar 布局检查不能证明
这个原厂分支可用。详见 `jpeg-encode/stock-call-report.json` 与编码文档。

拍摄进程的 SMA 映射与像素生产者确认接口仍未接入，主模拟器没有加载该编码
模块，也未新运行完整拍摄。当前 PP／IPCS 像素缺口仍在，不能据此宣布完整 Bayer
拍摄链完成。物理 PP 等价性和当前开启的 DPC／CS／位宽规则仍需硬件规范或配对
输入／输出观测。最小反汇编摘录为
`outputs/firmware-runtime/jpeg-task-boundary.asm`；原失败记录继续保留在
`ep-cs-grids-development.json`、`ep-cs-capture-development.json` 和原始日志中。

## 原厂相册的软件解码与显示

`run_stock.py --cpu-jpeg-decode --cpu-playback` 显式加载两个实验室像素模块，要求 `--program camera --device-preview`。它们不进入实体固件或 SD 服务预加载清单，也不改变上面的拍摄编码状态。

`tools/build_jpeg_decode.py --probe` 构建 `runtime/jpeg_decode.c`，复用原厂 `libjpeg.so.8` 解码真实 JPEG。原厂解码结构为 504 字节，较 IJG8d 在 scale_denom 后增加 16 字节；局部兼容头按已核对指令生成并检查所用字段偏移，不能直接拿 488 字节标准头调用。独立 ARM 探针对 5472×3648、422 的原厂 JPEG 两种输出布局逐像素比较通过，短输入、伪 EOI 和不足输出范围返回失败。证据在 `outputs/firmware-runtime/jpeg-decode/probe-report.json`；ABI 指令在 `jpeg-decode-abi.json/.asm`。

`tools/build_playback_pixels.py` 构建 `runtime/playback_pixels.c`，实现原厂 `resizeOne(CYcc&,CYcc&)`、`CMmDeviceDriver::ResizeImage` 与 `ConvertYccToRGB` 的实际像素操作。单张回放实测路径为 5472×3648 → 1624×1080 → 720×480 → Evas，保留原厂文件解析、缓冲管理与 UI。软件使用双线性缩放、CbCr 和 opaque BGRA 约定，不代表硬件 FIR 或色彩转换逐位等价。

首轮解码成功后仍出现绿屏，因为原厂 libsif 内部还调用另一条 IPCS 分块缩放，不能只接 libmm-photo 包装层。补齐 `resizeOne` 后照片显示，但底部一行仍绿；实际 RGB 缓冲与原指令证明，DscImage 重建 CYcc 时将 C0/C1 比真实解码地址多移一行。现以成功解码登记的尺寸、Y 地址、步长、格式和准确一行偏移共同匹配，再为该次 libsif 输入使用生产者地址；不改 JPEG 输出、不裁掉底行。最终 UI 验证以 `outputs/quickshare/gallery/stock-session/` 的实际运行报告和截图为准。

## 映射权限与 CPU 像素访问（2026-10-21）

`device_preview.c` 现在跟踪每个 SMA/BMA 映射区间的权限。mprotect 按页拆分，
恢复相同权限后合并物理和虚拟地址均连续的区间；PROT_NONE 和只读区间保留
来源，恢复后仍能查回原物理地址。权限变更前同时预检成功和失败两种记录
容量，512条记录不足时在调用系统接口前返回 ENOMEM。

新增 `nx300m_map_physical_access(physical,size,required)` 和
`nx300m_map_virtual_access(address,minimum,remaining,required)`，required 使用
Linux READ/WRITE/EXEC 位。旧通用 helper 的调用方既有读也有写，因此要求 RW；
只读来源会被旧接口保守拒绝，明确只读的调用方可用 access 接口。
现有 JPEG 解码、YCC 缩放、相册像素转换、Bayer 拆分、旋转、LCD 读取和
录制 Bayer 写入已按各自输入／输出角色改用 READ／WRITE 查询。
`ipcm-resize-probe.json` 实际验证：只读输入加仅写输出可以完成原厂
配置／启动／等待链和软件缩放；输出改为只读后拒绝执行、不发完成事件。
JPEG 与相册像素模块 ARM 构建通过；这些结果不证明硬件算法逐位等价。
这些是查找时检查，返回裸指针后的并发 unmap/权限改变仍需调用方生命周期保护。

实际 ARM/QEMU11 反例：两页最初只读，后一页为只读 fd 的共享映射；请求全部
改为 RW 返回 EACCES，但前页实际已可写，guest `/proc/self/maps` 仍报告只读。
因此失败后不能用该表恢复真实权限，受影响的跟踪范围标为显式 UNKNOWN，
所有 helper 均拒绝它；成功的普通 mprotect 可恢复已知权限。未将 UNKNOWN
伪装成 PROT_NONE。若已有跟踪区间，GROWSDOWN/GROWSUP 在系统调用前返回
EINVAL，避免请求范围之外扩展导致漏记；其它未涉及跟踪区间的普通映射透传。

`outputs/firmware-runtime/mapping-replace/probe.json` 和 stdout 记录：
NONE/只读与读写恢复、页取整、拆分合并、失败保守拒绝、满表变更前拒绝均通过。
失败后成功恢复只读时，受控子进程实际写入触发 SIGSEGV；再恢复 RW 成功。
原有 MAP_FIXED/munmap 与原厂 PWM pthread 启动检查也通过，退出0。
已将同一已验证 ARM 产物复制为默认 device_preview.so 并重启正常模拟器。
分配代次和裸指针使用期间的生命周期保护仍未实现；mremap 的后续进展见下节。

## 缓冲搬移跟踪（2026-10-21）

SMA/BMA 的 mremap 现按实际返回地址更新虚拟地址、物理偏移及权限，支持
缩短、MAYMOVE 搬移和 FIXED 覆盖目标记录。扩大要求原范围的物理地址连续，
且不越过 backing arena 边界。元数据容量在系统调用前检查，固定覆盖可回收
被替换记录。tracked 零 old_size 克隆、DONTUNMAP 等新语义及涉及 MMIO trap
的搬移明确拒绝，未跟踪的普通操作透传。

失败不能统一视为无变化：Linux3.5 的 FIXED 路径先删除目标和被缩短的旧尾，
随后才检查源；失败时撤销这些可能失效的跟踪。QEMU11 默认 reserved_va 模式
另有非 FIXED MAYMOVE 失败后仍清掉源范围的行为，因此该失败分支撤销源记录。
显式 FIXED 的 QEMU 分支只在成功时额外清源；失败时保留未受影响源前段。

ARM 实际验证见同一 `mapping-replace/probe.json`：原地缩短、MAYMOVE 搬移、
权限／物理偏移保留、FIXED 覆盖及失败后的目标删除、默认 QEMU MAYMOVE
失败后的源删除、满表容量拒绝均已检查。失效地址使用内核 pipe copy 检查
EFAULT，避免陈旧 guest page flags 引发 QEMU 信号重试。
默认 QEMU 下原地扩大返回 ENOMEM，测试明确记录该限制；原地扩大成功分支
尚未验证，不能把允许搬移后的扩大当作原地扩大成功。

最新映射产物和按读写权限区分的像素模块已加载到正常主会话 namespace16511。
原厂回放实际显示照片，启动LCD错误计数为0；界面记录为
`outputs/firmware-runtime/mapping-replace/main-playback.png`。
这验证本轮模块的相册集成，不代表完整拍摄ISP已完成。
