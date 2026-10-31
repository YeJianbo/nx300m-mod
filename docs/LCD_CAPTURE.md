# 原生 LCD 提交捕获

`runtime/lcd_capture.c` 是 ARMv7 soft-float 的原生 `LD_PRELOAD` 观察模块，供原厂 NX300M 程序与真实显示驱动运行时导出视频层。它独立于 `device_preview.so`，不提供设备模拟，也不改写相机固件。

## 真实调用与采集条件

仅观察 `/proc/self/fd/<fd>` 指向 `/dev/fb0` 的三个请求：

| 请求 | 参数 | 处理 |
| --- | --- | --- |
| `0x40407406` | 64 字节 `stvideodisplay` | 原厂返回成功后记录几何、地址与开关，导出像素 |
| `0x40207407` | 32 字节 `stvideo_address` | 原厂返回成功后更新已知窗口地址，导出像素 |
| `0x40087405` | 窗口及开关两个 u32 | 原厂返回成功后更新可见性 |

所有请求均调用真实的下一层 `ioctl`，保留其返回值和 `errno`。ARM EABI 入口直接转发 r0/r1/r2，不通过 `va_arg` 读取无参数请求。观察失败只影响导出状态。模块不生成 IRQ、vsync、DMA 完成或队列出队结果。

原厂 `libmm-displayer.so` 的 `CDisplayLCD::Draw(CYcc&, int, Direction)` 会提交 `VID_SET` 或 `VID_ADDRESS`，但不检查返回值；随后无条件调用的软件缓存更新也不代表驱动接受。因此采集点位于真实 `ioctl` 返回之后，不能用 Draw 返回或缓存更新作为接受证据。反汇编见 `outputs/firmware-runtime/lcd-capture-stock-submit.asm`。

首版只接受现有远程读图器已支持的形态：8 位 YCbCr 420/422；非零、偶数、最大 8192 的宽高；显示宽高等于源宽高；stride 为 8 的倍数且 `stride/2 >= width`；Y1/C1 分别为 Y0/C0 加一个行距；地址模式 0 为虚拟，1 为物理。三星该 ABI 的 stride 覆盖两行，逐行 pitch 为 `stride/2`。

虚拟地址通过内核 `write` 的用户指针复制路径读取；描述本身经私有 pipe 安全复制。失效指针导致导出失败，不直接解引用而使原厂进程崩溃。物理地址只打开 `/dev/d4_sma`，查询 `SMA_GET_REGION_START_ADDR` 和 `SMA_GET_REGION_SIZE`，检查完整映射页落在实际 SMA 区间内，再以 `PROT_READ | MAP_SHARED`、64 位物理偏移映射。使用只读 `O_SYNC` 打开，使原厂 `sma_mmap` 选择非缓存映射；不使用 `/dev/mem`，不写相机像素内存。

## 与现有远程服务共用协议

输出仍为 `mod/metol/remote/screen.h`、`tools/lcd_plane.py` 使用的协议：

- `/var/run/nx300m-lcd-state.bin`，4096 字节；magic `0x434c584e`，version 1。
- `/var/run/nx300m-lcd-video-{0..3}.yuv`：64 字节原始视频描述，随后为去 padding 的逐行 Y、逐行交错 CbCr。
- 状态结构包含 16 字节头部及四个 92 字节窗口结构。
- 原有头部 `reserved` 字段以 `1` 标记本原生 observer 写入者；`device_preview` 仍为 `0`。此 provenance 仅区分导出来源，不代表真机验证。隔离 fixture 使用同一 observer，也会写 `1`；前端不能据此把 `sensor_liveview` 标为真。
- 先完成临时帧，再发布奇数 sequence 状态、原子替换帧、发布偶数 sequence 状态。文件均以临时文件加 rename 替换；读者继续使用前后状态一致且为偶数的检查。
- `completed=1` 仅指该窗口最近一次导出成功，**不是硬件帧完成**。提交失败或格式、地址、复制不受支持时置零，远程读者不会把旧文件当作该次有效帧。成功但描述不可读时，所有窗口导出失效。

模块没有与显示硬件同步冻结缓冲区；若原厂在复制期间重用图像内存，导出仍可能撕裂。首版同步采集每次受支持提交，尚未测量真机开销。色彩解码沿用远程读者的 BT.601 full-range CbCr 假设，尚未校准真实面板/相机编码。

## 构建及隔离验证

在项目 Conda 环境运行：

```powershell
python -X utf8 tools/build_lcd_capture.py
python -X utf8 tools/probe_lcd_capture.py
```

独立产物为 `outputs/firmware-runtime/lcd-capture.so`；脚本不替换任何生产 `.so`，不重启现有相机会话。未来由启动器为原厂相机进程指定该库的绝对路径，并确保 `/proc`、可读 `/dev/fb0`、可读 `/dev/d4_sma` 和可写 `/var/run` 可用。不要与负责模拟同一输出文件的 `device_preview.so` 同时使用。

`tools/run_stock.py --program camera --lcd-capture` 已提供显式预加载选项；与 `--device-preview` 共用会被拒绝，避免两个写入者覆盖同一状态。正常模拟器继续使用原有实验室显示后端。远程 `camera/info` 现在按真实状态头返回 `stock-lcd-observer`、`laboratory-lcd-backend` 或无视频层时的 `none`，无视频层使用 X11 OSD。来源标签不会开启 `sensor_liveview` 能力。

`runtime/lcd_capture_fixture.c` 和 `runtime/lcd_capture_probe.c` 只在独立 mount/net/ipc namespace 内使用普通文件充当目标 fd，受控 fixture 返回 ioctl 结果；ARM 程序运行在原厂 glibc 2.13 下。它验证返回值及 errno、无参数转发、带 padding 的 YUV 内容、地址更新、可见性、失败提交隐藏、无效虚拟指针安全、SMA 区间外不发起 mmap 及偶数 sequence 发布。报告 `outputs/firmware-runtime/lcd-capture-probe.json` 的退出码为 0、stderr 为空。

这些结果未验证真实 framebuffer 驱动接受、有效物理 SMA 映射、硬件显示内容或采集性能。最小真机检查是在正常拍摄界面和 Playback 各提交一帧，确认视频层文件与当前画面一致、窗口状态可切换，且相机按键、播放切换和显示流畅度正常；任何检查都不能以导出完成推断硬件完成。

## 依据

- `work/vendor-source/TIZEN/packages_nx300m/linux/include/video/drime4/d4_dp_type.h`
- `work/vendor-source/TIZEN/packages_nx300m/linux/include/video/drime4/d4_dp_ioctl.h`
- `work/vendor-source/TIZEN/packages_nx300m/linux/drivers/video/drime4/lcd_nodrm/d4_dp_lcd_ioctl.c`
- `work/vendor-source/TIZEN/packages_nx300m/linux/include/media/drime4/sma/d4_sma_ioctl.h`
- `work/vendor-source/TIZEN/packages_nx300m/linux/drivers/media/video/drime4/sma/d4_sma_ioctl.c`
- 核验原厂 1.15 `libmm-displayer.so` 的 Draw、SetCurWinParam 反汇编。
