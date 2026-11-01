# IPCM 实时风格预览候选

最新颜色审计已扩展到全库20款，新增分阶段通用拟合和原生参数参考模型；原安装包仍为下述8款。NC暗部偏色尚未解决，结果见 [颜色模型审计](IPCM_COLOR_AUDIT.md)。按原厂帧处理流程实现完整三维预览的调查见 [SmartFilter调用链](SMARTFILTER_LUT_PREVIEW.md)。

这一版把源 `.cube` 自动拟合成 IPCM 的颜色矩阵、色调曲线和色度矩阵，通过独立 RAM 模块应用。成片仍使用现有 IPCS 9³ LUT；现有成片代码、196 字节配置和 RAW 路径均未修改。

目前是**手动测试候选**。ARM 模拟器验证函数挂接、整数运算和恢复；照片生成的动态视频评估软件近似颜色。原生 IPCM 色彩域、硬件像素顺序与 LCD 效果还没有经过机身标定。

## 本版内容

- 8 款预览：GR Neg、GR Pos、Hard Mono、富士 CC／Astia／Eterna／NC／NN。
- 导入时离线拟合完整 3×3 CCM、共享单调 Tone 和完整 3×3 YCC。新配置为 v2、360 字节；兼容 v1 的 340 字节 CHM 配置。附源文件哈希及模型版本。
- 运行时只在原厂颜色参数更新时计算，Hook 内不读卡、不分配堆内存、不处理逐帧像素。
- 强度 0% 直接转交原厂指针；50／100% 使用整数参数组合。每次都从本轮原厂输入计算，避免重复叠加。
- `stock` 请求当前原厂参数恢复；`restore` 等恢复完成后卸下挂钩。代码映射留在 RAM，以免在途函数返回到已释放内存。

运行时现已扩展为ABI3、7个槽，加入PCSE的12组色相／饱和度参数、独立恢复和49份采样配置。上述8款风格仍为原有v1／v2配置。新PCSE实现与编译流程见 [IPCM PCSE](IPCM_PCSE.md)；真实响应采样尚未完成。

没有自动启动、按键接入或 App 改动。现有面板选择之后可运行 `sync`，或通过配套脚本一次选择成片与预览。

旧 `color_style.c` 是 LD_PRELOAD 色彩模块；本候选给 MFW 提供独立 RAM 入口，尚未合并旧模块的控制界面。加载器要求七个原厂槽或本模块已确认的入口，不能与改写这些槽的另一套色彩 Hook 同时安装。

## 调用链与证据

原厂 1.15 的 `libiq-fw-slpcam.so`：

```text
原厂 IQ 参数更新线程
  → CIpcmColorD4 / CIpcmGammaD4 专属 Update 虚表槽
  → 预览 Hook：generation 改变时 Reset(self)
  → 原厂 Update → GetIndex → 原厂 SetParam
  → IQ 的 CCM / Tone / RGB2YCC GOT 槽
  → 以本轮原厂参数组合预览变换
  → 原厂 libudd setter
```

| 位置 | 原厂偏移 | 已核实的作用 |
|---|---:|---|
| Color Update 槽 | `0x3bffc8` | 专属虚表第一项，原值指向继承的 Update |
| Gamma Update 槽 | `0x3c0048` | 同上，仅修改这两个专属槽 |
| 原厂 Update | `0x350dc0` | 计算索引，变化时调用节点 SetParam |
| 原厂 Reset | `0x350e0c` | 将 `self+4` 设为 −1，使后续真实 Update 重算 |
| CCM setter | UDD `0x47aec` | 9 个 32 位 signed11 系数，范围 −1024…1023 |
| Tone setter | UDD `0x4b7d4` | 复制 2048 个 uint8；返回 void |
| RGB2YCC setter | UDD `0x47da4` | sel1 的 CHM4 位于 +24；sel3 的 YCC9 位于 +40 |

CCM／Tone／RGB2YCC 三个 IQ GOT 槽分别为 `0x3c0c68`、`0x3c0418`、`0x3c1bec`。CHM 与 YCC 共用第三个 setter，由 sel 区分。完整原厂库身份、函数入口校验与构建结果在 `outputs/deployment/ipcm-preview/build-report.json`。

Color 重算也会由原厂重写 WB 参数。模块不改变 AWB 算法，但真实图像反馈是否影响 AE／AWB，仍需机身观察。原厂 Color／Gamma SetParam 的返回值不能证明底层参数成功；状态单独记录矩阵 setter 返回值。Tone 只能报告已转交，无法确认硬件接受。

## 配置格式与计算

`looks/<id>/preview-ipcm.bin` 为小端格式。32 字节头包含 NXVP、版本、大小、flags、payload CRC32 和 3 个保留字。

| 版本 | 大小 | 头后内容 | 原厂 setter 路径 |
|---|---:|---|---|
| v1 | 340 B | CCM9 int32 Q12、CHM4 int32 Q12、Tone256 uint8 | sel1 组合 CHM；sel3 原指针 |
| v2 | 360 B | CCM9 int32 Q12、YCC9 int32 Q12、Tone256 uint8 | sel3 组合 YCC；sel1 原指针 |
| v3 | 456 B | v2内容，追加PHC12、PSC12 int32原生偏移 | YCC路径同v2，PCSE两个selector分别加偏移 |

当前 7 款颜色风格使用 v2，Hard Mono 保留拟合效果相同的 v1。版本转换显式处理旧 Tone 偏移和 CRC，旧配置不会被按新布局解释。

```text
CCM'       = A × CCM_stock
CHM'       = B × CHM_stock        （v1，仅 sel1）
YCC'       = C × YCC_stock        （v2，仅 sel3）
Tone'[i]   = T[Tone_stock[i]]     （2048 个条目）
```

A／B／C 对单位矩阵、T 对 identity 映射插值后再组合原厂输入。整数舍入采用 ties away from zero。最终系数超出 signed11 范围时，整组回退原厂并增加 fallback 计数。CCM、Tone、CHM、YCC 分开记录恢复状态；v1 与 v2 互切后，旧 stage 仍等待新的原厂输入恢复，不能用另一组参数的到达代替恢复完成。

v1 保留 Rec.709 色度代理的原有语义。v2 使用 CCM → clip → Tone → 原厂默认 Q8 YCC 基底 → 完整 YCC3×3 → RGB → clip。默认基底取自 IQ 的 `CIpcmColorD4::m_iRgb2Yuv`（`.rodata 0x36e494`，36 B）：

```text
[ 77, 150,  29
 -43, -85, 128
 128,-107, -21 ] / 256
```

证据在 `outputs/metol/ycc-abi.json` 与 `tools/inspect_ycc_abi.py`。默认系数只定义线性软件代理，未确认真实 ISP 色彩编码、量程、裁剪及处理顺序。原厂非单位 CHM 位于 YCC 后，两者通常不能交换，代理效果也会受其影响。软件误差不是机身色差，也不是 ΔE。

## 本轮偏差修正

通用拟合的后续优化、全库对照及模型表达限制见 [IPCM 通用拟合优化](IPCM_FIT_OPTIMIZATION.md)。该实验尚未替换下述默认配置。

原厂PCSE已接入ABI3运行时，通用联合拟合器支持输入响应样本。调用链与编码证据见 [IPCM 分区色彩控制调研](IPCM_PCSE_RESEARCH.md)，实现、采样格式和当前结果见 [IPCM PCSE](IPCM_PCSE.md)。

旧 CHM2×2 只改色度，不能表达 Tone 后的颜色相关亮度变化。旧拟合因此抬升 Tone 暗部、改变 CCM 行和来补偿，灰阶也被提亮或染色。保留同一模型仅更换优化器，改善有限；完整 YCC3×3 才明显降低这类误差。

新拟合器使用带投影的阻尼 Gauss–Newton，同时拟合 50%／100%，33 节点共享 Tone 施加单调与平滑约束，最终按量化后目标选配置。训练由合成 RGB、灰阶和近灰样本构成，未使用评估照片训练。旧编译器和产物保存在 `evaluation/baseline-v1/`。

另取 seed=20261101 的 32,000 个训练外 RGB 样本，100% 强度的平均 RGB 代码值误差如下，标度 0～255：

| 风格 | 全体旧 → 新 | 灰阶旧 → 新 |
|---|---:|---:|
| GR Neg | 10.36 → 8.63 | 5.76 → 5.19 |
| GR Pos | 6.18 → 6.14 | 6.99 → 3.59 |
| 富士 CC | 8.85 → 6.78 | 13.90 → 4.94 |
| 富士 Astia | 8.07 → 7.23 | 3.47 → 2.52 |
| 富士 Eterna | 10.08 → 7.80 | 16.52 → 5.09 |
| 富士 NC | 4.53 → 4.40 | 2.48 → 2.45 |
| 富士 NN | 8.97 → 7.68 | 5.95 → 2.89 |
| Hard Mono | 0.008 → 0.008 | 约 0 → 约 0 |

仍有取舍：50% 的 GR Pos 全体误差 3.59 → 3.72、蓝色 3.99 → 4.25；Astia 全体 4.16 → 4.30、蓝色 3.78 → 4.43、肤色代码区域 3.61 → 4.72。GR Neg 半强度蓝色及 NC 肤色代码区域也有小幅退步。这里的“肤色”是 RGB 条件区域，不是真人肤色测量。详细 P95、半强度、现有 9³ 表目标对比及量化误差见 `evaluation/fit-improvement.json`。量化误差单独列出，不能把模型表达能力不足归因于 Q12 精度。

完整矩阵与共享曲线仍不能还原任意 3D LUT 的局部颜色变化；本轮降低主要偏差，未消除全部偏差。

## 模拟器与视频检查

ARM 结果读取 `outputs/deployment/ipcm-preview/arm-vm/report.json`。私有 VM 使用原厂 IQ、UDD、相机导出的 MFW `libmisc.so` 及当前构建。真实 Update／Reset 执行；GetIndex／SetParam 使用测试函数，寄存器 backing 替换为普通 RAM。它没有模拟传感器、ISP DMA 或整个机身内核。

最新 ABI3 的 ARM 检查共28轮，保留v1／v2兼容检查，并增加24个PCSE偏移、0／50／100%、独立恢复、v3切回v2、范围回退及7个槽的页权限检查。原厂输入、不累加、卸槽与重新应用、线程和tracer状态以及IPCS RAM检查通过。独立VM已关闭。未验证真实像素、DMA、busy并发或跨节点同帧原子性。

`evaluation/ipcm-look-comparison.mp4` 使用两张真实 NX300M JPEG 生成平移缩放片段，三列依次为原图、源 LUT 目标、量化预览近似。逐款 `profile.json` 保存训练外随机样本误差，`evaluation/evaluation.json` 保存实拍图像分区误差。源照片已经经过相机成像；视频用于比较代理模型，不能当作原始取景帧测试。

`evaluation/correction-comparison.jpg` 比较 GR Pos／CC／Eterna 的目标、旧版与修正版，含 50% 和 100%；`evaluation/index.html` 集中展示视频、图像和误差报告。两张照片用于观察已有 JPEG 经软件变换后的差异，没有运行原生 ISP 像素管线。

## 安装与最短机身测试

候选 ZIP 是现有安装的附加文件：合并 `nx300m-mod/` 到卡上同名目录。没有 `autoexec.sh`，不会覆盖原成片模块，也不会因复制文件就启用预览。

首次仅用**普通 2D P／A／S／M**，关闭原厂特殊效果；固定白平衡，保持一个彩色物体和中性物体同框。当前尚无场景／电影／3D 模式资格判断，切换这些模式前先恢复。

本版运行时为 ABI3，不能直接接管旧ABI1／ABI2驻留。已加载旧候选时，先用匹配的旧loader和blob完成 `stock`／`restore`，使目标相机进程重新启动，再加载新版。普通关机可能恢复快照，须确认目标进程确实重启。电脑上的ABI2配对备份位于 `outputs/deployment/ipcm-preview/previous-runtime/abi2/`；v1／v2配置文件兼容不等于旧驻留运行时兼容。

在已有相机 shell 中运行（无需刷固件）：

```sh
cd /mnt/mmc/nx300m-mod
# 1. 安装但保持原厂输入，确认取景、按键、拍摄正常。
sh ipcm-preview.sh apply nx300-hard-mono 0
# 2. 验证最明显的变化。等待原厂下一次参数更新后观察取景。
sh ipcm-preview.sh apply nx300-hard-mono 100
sh ipcm-preview.sh status
# 3. 恢复；返回正常取景让原厂更新发生，然后再卸槽。
sh ipcm-preview.sh stock
sh ipcm-preview.sh restore
```

若 `restore` 返回 4：仍在等待新的原厂参数写回。保持正常取景，稍后重试同一条 `restore`，直到返回 0 且 `installed:false`；不要删除状态文件。`status` 中 `setter_forwarded` 增长只证明转交，真实 LCD 是否变化需要直接观察。

黑白变化和恢复都正常后，再试 `ricoh-positive-exact` 的 50／100%，观察白平衡改变后是否仍稳定。最后使用已就绪的成片引擎同时选择两条链：

```sh
sh lut-with-preview.sh apply ricoh-positive-exact 50
sh lut-with-preview.sh stock
# 或在现有面板选择后，显式同步当前选择和强度：
sh ipcm-preview.sh sync
```

两条链各等原厂更新，不保证同一物理帧同时切换。预览失败会保留已成功暂存的成片选择，并打印失败状态。缺少预览配置的风格请求原厂预览，避免继续显示上一款。

## 恢复与后续

正常恢复使用 `stock` → 原厂更新 → `restore`。退出后保留约 12 KiB RAM 映射，不卸载正在执行的代码。首次候选没有自动重挂钩；快照复活但状态丢失或代码版本不符时会拒绝覆盖，需保留日志排查，不能无条件重写槽位。

若出现取景或按键异常，先停止后续命令；有 shell 时读取状态，按上面的恢复流程处理。删除 SD 文件不会撤销已驻留的 RAM Hook，普通关机也可能保留快照状态；必须验证恢复结果。

本轮尚待机身验证：真实 LCD 变化及颜色方向、特殊模式、AE／AWB 反馈、拍摄与预览并发、休眠恢复。先通过黑白变化和原厂恢复，再接自动同步与开机恢复。

## 重建

```powershell
python -X utf8 tools/fit_ipcm_preview.py --all
python -X utf8 tools/build_ipcm_preview.py
python -X utf8 tools/probe_ipcm_preview_vm.py
python -X utf8 tools/analyze_ipcm_preview_fit.py
python -X utf8 tools/evaluate_ipcm_preview_video.py
python -X utf8 tools/package_ipcm_preview.py
```

本地评估视频与照片不放入 SD 候选包，也不上传。
