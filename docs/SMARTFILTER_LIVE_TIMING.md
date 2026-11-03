# S2 连续黑白短采样候选（ABI4）

实机黑白采样已保存并通过完整性检查。当前继续使用同一套硬件处理及恢复顺序，先修正软停占用菜单入口的问题。

## 最短测试

包名为 `nx300m-smartfilter-s2-live-timing.zip`。写卡工具使用 `--s2 --live-timing --quick-panel` 启用临时小面板；`--check-only` 只检查包。

1. 正常开机，保持原厂智能滤镜关闭，保留 RAW／RAW+JPEG 设置。
2. 长按 Custom 打开小面板，选第二项“黑白预览 · 短采样”。该项只改取景，不改变照片 LUT 选择。
3. 若仍彩色，通过原厂回放进入再返回取景；此自然重进已在旧候选上验证有效。不按快门激活，不强制重建。
4. 移动相机观察数秒。采样最多保存128条帧／初始化记录，容量满后继续黑白预览。拍摄记录使用独立空间。
5. 再次长按 Custom 会在后台请求软停，同时打开小面板；也可选第一项“关闭预览并保存采样”。后台任务先排空已开始的完整事务，再软停并保存上一窗口。菜单可以关闭、重开，不等待恢复进程。一次后台任务超时后，开菜单不自动重试；如需重试，显式选择第一项。

6. 取景恢复原样、写卡灯熄灭后，正常关机，再通过读卡器读取 `nx300m-mod/logs/fn-key.log`。后台任务的退出码、软停和存卡回执写入该日志；卡日志不可写时仍执行软停，控制日志保留在 RAM。

软停和保存分别回执：`restore` 的新 generation 表示软停提交；`restore-timing` 的旧 token/generation 标识刚才的黑白样本。`persisted` 表示已存卡，`ram_only` 表示只有 RAM 快照，不能依赖开关机保留它；`export_failed`／`snapshot_unavailable` 表示保存失败。保存失败仍保持预览关闭。

普通“连续黑白”“当前 LUT”“sync”仍为 timing=0。普通无采样软停不创建诊断快照。独立 probe-only 包及 identity-only 单帧限制不变。

## 当前未解决事项

- 已定位主要分段：119 个已保留的 render 样本，处理 wall 中位数 19.585 ms、p95 21.727 ms；IPCS open 中位数 11.659 ms，YCC 中位数 2.347 ms。尚未实施跨帧硬件参数或句柄复用。
- [IPCS Open 性能审计](IPCS_OPEN_PERFORMANCE.md)已在 1.15 固件内核中确认首次上电的两次约 5 ms 忙等，并补齐引用计数、PMU、Clock 与额外生命周期信号量证据；每帧实际分支和 76 ms 长尾尚待只读实机采样。
- 本次两次 restore 返回 rc=5，后一次成功并保存了样本。后台分离修复菜单入口占用，未改变恢复 tracer 的等待机制；超时本体仍需进一步定位。
- 开启不会主动重建运行中的图；不得自动调用 Stop/Start/Create。
- 旧版照片 LUT 自动同步存在 RAM 代码根／数据根混用，仍未修；不能以该项验收本次性能候选。
- 不自动拍照。没有 Prepare/Finish/drain 样本时，不能声称已解释拍后等待；后续必要拍摄对照由用户手动完成。

## 约束与分析

不刷固件、不改原厂代码段或 RAW Bayer，不改 JPEG 引擎；不省 Reset/Still IQ，不跨帧持锁，不跳帧，不用黑白专用路径冒充任意 LUT 加速，不删除驻留 state 或终止恢复 tracer。

使用现有 `tools/analyze_smartfilter_timing.py` 分析完整旧窗口。保留 cold/first、pending、控制污染、计时校准和有效样本数；分别看 LUT/cache/YCC/Reset/IQ 等段，不机械相加嵌套时间，不从按钮 elapsed 或累计计数推算 FPS。VM 只验证 ARM 控制逻辑、原厂 worker 路由和软件像素替身，不证明真实 ISP、DMA/cache、颜色或流畅度。

本次实机原始日志、提取的 139 条记录及分析报告位于 `outputs/deployment/smartfilter-preview/camera-sampled-20261102/`。短窗口容量已满，分段统计仅覆盖保留样本；退出回执的累计计数为 rendered=8096、errors=0，不能代替逐帧像素或整段时延测量。
