# 2026-11-10 23:30 ZCode：菜单与 LUT 控制性能优化

用户授权今晚 23:30（Asia/Kuala_Lumpur，UTC+8）一次性执行，模型为 Start Plan / GLM-5.3-Flash，思考强度 **max**。前两项完成后，有剩余执行时间就继续排查相关性能问题并优化，不停在计划或建议。精确调度及原生回执见 `outputs/review/2026-11-10-performance/schedule-result.json`。

## 当前基线和边界

权威工作区 `.`，直接原位修改。根目录无 `.git`，`outputs/github-source/nx300m-mod` 是旧发布镜像，不要当工作副本。保留用户和其他执行者的修改，对受影响文件只做小范围备份，不备份全树或整卡。沿既有 NX300M 主会话串行执行，若有同项目写入任务正在运行，先等待，不重复启动竞争任务。

用户已经确认：撤回菜单实时取景与菜单防息屏修改后，关机再开机正常。故障具体根因尚未单独证实。保持静态参考图，不恢复菜单实时取景 producer、邮箱或刷新链路，不恢复通过 vconftool 写 memory/pwrmgr/trigger 的防息屏逻辑。不要把历史任务书中的这些旧要求重新接入。

保持当前 ABI7、build 3392396409 的稳定硬件预览路径，不修改原厂 Reset、Still IQ、IPCS Session、拍照仲裁、RAW、LCD DMA 或运行时硬件补丁，不做视频或新功能。不得用移除检查、缩短安全超时、提前返回成功或异步隐藏未完成操作来伪造提速。不上机、不写卡、不安装、不推送、不发布；本轮是离线源码与 ARM VM 工作。

## 已完成工作与证据

- `mod/metol/nxui_ximage.h` 已实现保留命令、变化区域 CPU 重绘和区域旋转上传；不要覆盖掉。
- `mod/metol/nxui_lut_panel.h` 通过图片 revision 标记参考图内容更新；`nxui.c` 记录 PANEL_RASTER。
- 当前候选菜单已编译并写卡，实机流畅度待用户反馈。当前文件是本轮新基线，不要把旧 before.arm 当作当前实现。
- `outputs/performance/menu-damage/arm-vm/report.json`：同一 ARM VM 中 813 次转盘输入全部处理、终值正确、持续输入中定期绘制，6 组画面逐像素一致，passed=true。首帧后 CPU 从 873.642ms 降至 611.904ms，约减少 30%，这是软件测试，不能宣称实体 FPS 提升 30%。
- 相应原始日志和截图在同目录，私有 VM 已停止。`outputs/performance/menu-damage/verify.py` 是本轮临时对照运行器，可适配复用。
- 卡上最近两次应用日志：BACKEND 约 2198ms、2149ms；preview-sync 约 1640ms、1851ms。一次打开菜单 trigger_elapsed_ms=951、first_draw_ms=300。更早记录出现 preview-sync=3422ms。优先分析这些阶段，不将所有等待都归因于编译 LUT。

## 按顺序实际执行

### 1. 参考图计算

检查 `nxui_lut_panel.h:lut_update_reference()`、`lut_reference_interp.h`、`lut_tune.h` 和现有缓存。三张静态图每张 192×112，每次有效微调仍对全部参考像素做三通道 LUT 插值。

先测相关计算成本，再选择收益明确的实现：固定图像的采样位置/分数预计算、强度变化复用未混合结果、有限的最近使用缓存等。控制常驻内存，避免为每款 LUT 缓存三张大图。保留当前插值、舍入、强度与微调顺序及黑白参数行为。连续输入只绘制最新状态，但不能丢输入、一直推迟绘制或在停止后显示旧值。

### 2. LUT 应用控制等待

检查 `lut_engine.c`、`lut_engine_shared.h`、`panel-actions.sh`、`smartfilter-live-preview.sh`、`lut-panel.sh` 等控制包装，先把已有 adapter-ready、card-persist、preview-sync 的耗时细分，识别进程启动、RAM 文件准备/复制、身份检查和 loader 调用各自成本。可以只读研究 loader；硬件 loader/blob/Session 实现本轮不修改。

仅优化有证据的重复准备或资源复用，保留媒体访问锁、相机会话身份及变更检查、符号链接拒绝、来源身份、超时/失败现场与 guard 生命周期。不能把 generation 已请求误报为已应用。不能牺牲重启后的正确状态、Fn 只保存当前款语义、OK 保存应用并退出、未提交草稿和预览偏好一致性。

### 3. 剩余时间继续排查优化

用户明确要求有时间就继续做相关性能工作。依照实际测量收益，继续检查菜单首帧/脚本启动、布局/字形和命令比较成本、连续触摸/转盘响应、重复日志与状态发布。Still LUT Hook 的 publish 调用频率若无法从离线证据确认，先留下测量结论；若有明确证据和可回归的收益再做小范围优化，保留原厂实际写表、错误和状态变化通知。不要扩成新功能开发，不为填满时间无目的重构。硬件相关无法离线定论的项记录最小上机验证步骤，然后继续其他可独立完成项。

## 编译和必要验证

Python 只用 `D:/conda/envs/nx300m-mod/python.exe -X utf8`。PowerShell 构建时 PATH 前置 `D:/Program Files/LLVM/bin;C:/Windows/System32;`，执行 `tools/build_metol.py --menu-only`。其他受影响产物按已有构建入口构建。

先构建，再跑针对改动和已知风险的必要已有测试，不重复已通过且无新信息的检查。同一私有 ARM VM 顺序比较本轮修改前与修改后：使用相同输入、数据、画面和计时口径；至少覆盖风格切换、列表/微调页、正负参数、强度、参考图切换/原图比较、持续转盘/触摸后的终值与中间绘制。若只改计算，优先用已有位级/画面对照确认舍入和显示未变。测试对照必须以今晚开始时的当前菜单为 before，不能只与局部重绘之前版本对比。

Shell 和绘制测试的私有 VM 串行，不争用共享 socket。需要 mount 的 probe 必须隔离 mount/PID namespace，不在共享 WSL 直接挂载 /var/run。完成后停止自己启动的 VM，并清理可重新生成的大型临时 initramfs，保留小型必要证据。

## 交付

写 `docs/AGENT_WORKLOG_2026-11-10-performance.md` 和 `outputs/review/2026-11-10-performance/result.json`。记录实际修改、构建与测试退出码、同条件前后性能/内存、原始日志位置、未完成项及原因和最小实机步骤。区分静态检查、VM 与实体证据，不把环境超时算通过。完成有价值的可验收工作后停止，不自行创建续行任务。
