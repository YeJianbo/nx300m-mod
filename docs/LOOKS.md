# LUT 库和 Lightroom 配置移植

当前已有 19 个风格，位置为 `looks/<id>/`：11 份 DCP 风格片段、2 份 Adobe RGBTable 风格片段及 6 份伴随 filmsim 颜色配方。理光仅保留 GR Neg、GR Pos；两项蓝色修正候选已移至 `work/reference/looks-blue-adjusted/`，不再进入活动库。原有 Lightroom 配置、照片和目录数据库均未修改。

2026-10-31：0～100 强度已接通 engine、HTTP、网页、Android 和原生面板；免刷 RAM 加载已通过完整 ARM VM 的真实 ptrace、两种表源六组逐字对照、0% 原指针透传、调用线程／顺序与同映射重复加载及恢复检查，尚未实机验证。默认包仍为 19 项，可选诊断包另加 5 项原生坐标探针，见 [LUT_CALIBRATION.md](LUT_CALIBRATION.md)。独立原厂拍摄会话也已通过录制 Bayer＋软件 ISP 保存 LUT JPEG；真实硬件 ISP、取景滤镜与理光校色仍未完成。当前加载入口、产物和证据边界见 [LUT_MOD.md](LUT_MOD.md)，以下旧阶段记录保留各自验证范围。

## 现有风格与本地来源

| ID | 显示名称 | 来源 |
|---|---|---|
| `ricoh-positive-exact` | GR Pos | NX300 GR IV Positive Film Exact |
| `ricoh-negative-exact` | GR Neg | NX300 GR IV Negative Film Exact |
| `nx300-fuji-astia` | 富士 Astia | 用户 NX300 Fuji DCP |
| `nx300-fuji-classic-chrome` | 富士 CC | 用户 NX300 Fuji DCP |
| `nx300-fuji-eterna` | 富士 Eterna | 用户 NX300 Fuji DCP |
| `nx300-fuji-classic-negative` | 富士 NC | 本地 X-T5 CLASSIC Neg XMP 引用的 Adobe RGBTable |
| `nx300-fuji-nostalgic-neg` | 富士 NN | 本地 X-T5 NOSTALGIC Neg XMP 引用的 Adobe RGBTable |
| `nx300-fuji-pro-neg-std` | 富士 Pro Neg Std | 用户 NX300 Fuji DCP |
| `nx300-fuji-reala-ace` | 富士 Reala Ace | 用户 NX300 Fuji DCP |
| `nx300-fuji-velvia` | 富士 Velvia | 用户 NX300 Fuji DCP |
| `sony-fl-look` | Sony FL | Lightroom Sony ILCE-7M4 Camera FL.dcp |
| `panasonic-classic-neo-look` | Pana Classic Neo | Lightroom DC-S5M2 Camera L Classic Neo.dcp |
| `pentax-bright-look` | Pentax Bright | Lightroom K-3 Mark III Camera Bright.dcp |
| `metol-portra400` | Metol Portra 400 | nx500-filmsim portra400 颜色烘焙 |
| `metol-superia400` | Metol Superia 400 | nx500-filmsim superia400 颜色烘焙 |
| `metol-velvia50` | Metol Velvia 50 | nx500-filmsim velvia50 颜色烘焙 |
| `metol-ektachrome` | Metol Ektachrome E100 | nx500-filmsim ektachrome 颜色烘焙 |
| `metol-trix400` | Metol Tri-X 400 | nx500-filmsim trix400 颜色烘焙 |
| `metol-hp5` | Metol HP5 Plus | nx500-filmsim hp5 颜色烘焙 |

### NC / NN 的本地 RGBTable 导入

`tools/import_fuji_rgb_tables.py` 只读用户已安装的 `XT5_CLASSIC_Neg_NX300.xmp`、`XT5_NOSTALGIC_Neg_NX300.xmp` 和本地 `CameraRaw.dll`。按 XMP 的 RGBTable ID 查找压缩记录，并用解压记录的 DNG MD5 指纹确认身份，不依赖固定 DLL 偏移或相邻字符串。

两份来源均为 16³、Adobe RGB 色度、SDK gamma_2_2（含暗部三次曲线）、clip gamut。导入器解码相对 identity 的 uint16 环绕差值，执行四面体插值和 sRGB ↔ Adobe RGB 转换，生成 33³、R 最快的 sRGB master，再编译两种 D4 表。`flags=1` 表示 SDK 的 embed-never 属性，不参与颜色计算。参考实现保存在 `work/reference/dng-big-table/`；来源身份及导入记录为 `outputs/looks/fuji-rgb-table-import.json`。

```powershell
conda activate nx300m-mod
python tools/import_fuji_rgb_tables.py
```

这两项仅迁移创意 RGBTable，不包含基础 CameraProfile 或完整 Lightroom RAW 显影，也未完成 NX300M 实机校色。

### 伴随 filmsim 配方

新取来源为 `WApaperplane/nx500-filmsim@babb2ebf378f6fc886fdf47785a494a74cde131a`，原始 JSON 与代码保存在 `work/reference/nx500-filmsim`。`tools/import_metol_filmsim.py` 适配其 `make_cube.py` 的颜色矩阵、亮度饱和度和 256 项曲线，生成 33³ 标准 master、4380 字节 D4 直接表和原厂组合表。它们与 68 条七维 Picture Wizard 配方是不同来源，颗粒是空间随机效果，未烘焙到颜色 LUT 中。

上游 baker 用 B 最快的行序，本项目标准 `.cube` 用 R 最快；导入器重排存储，不交换输入颜色轴。同时保留原始浮点运算顺序，避免 8 位曲线索引在半整数附近改变。六套各 35937 个网格节点已与实际执行的上游 baker 逐点对照，最大每通道差为 `4.9e-7`（文本小数精度）；未重排时最大差最高为 `0.92549`。证据为 `outputs/looks/metol-filmsim-validation.json`，离线颜色总览为 [metol-filmsim-preview.png](../outputs/looks/metol-filmsim-preview.png)。

```powershell
python tools/import_metol_filmsim.py
# 明确替换本导入器创建的配方和编译表时使用：
python tools/import_metol_filmsim.py --refresh-imported
```

输入按已渲染 sRGB 解释；源没有相机校色数据。README 仅声明研究学习使用，未附独立许可证授权，本项目当前只做本地适配、未发布此素材。离线预览不是机内 ISP 输出。

用户配置在 `%APPDATA%/Adobe/CameraRaw/CameraProfiles` 和 `Settings`。当前库中的两项理光以用户指定的 Exact 内部名称为准。此前 v4 两项已移至 `work/reference/looks-v4/` 保留，不再出现在活动 LUT 库中。Lightroom 内置资源在 `./local/lightroom/Resources/CameraProfiles`。原盘点得到 4,292 份 DCP、184 份 XMP，来源和解析结果保存在 `outputs/looks/local-profiles.json`，选用来源在 `outputs/looks/imported.json`。

### 用户收藏的理光来源

用户随后明确指定移植来源为 `NX300 GR IV Negative Film Exact`（负片）与 `NX300 GR IV Positive Film Exact`（正片）。对应已安装文件位于 `CameraProfiles/Imported/NX300_DCP_Camera_Profiles_v1/NX300 GR IV Negative Film.dcp` 与同目录 `NX300 GR IV Positive Film.dcp`。以这次指定为当前来源；下文「理光绿 1」是此前查找收藏时找到的另一套配方记录。已核对文件内部名称和 Samsung NX300 相机标识，生成各自的 33³ master、D4 直接表与原厂组合表。当前接入范围仍是 Creative LookTable，蓝色校色及完整 Lightroom 显影对照继续暂缓。

用户要求以 LR 收藏夹实际使用项为准。2026-10-19 的本地逐项 UI 核对记录位于 `./local/private-reference`，其中收藏为「理光绿 1」；同目录的整理说明记载「理光绿」已停用。当前安装的 `Settings/理光绿 1.xmp` 顶层 UUID 为 `D7624C4BC1236B4FA44E5401D76C10D2`，引用 `GR IV Negative Film - NX300M`，并包含自定白平衡、HSL、曲线和过滤器。它不是当前 v4 Creative LookTable 的同义项。

`tools/inspect_lr_favorites.py` 只读收藏记录和来源，将该 XMP 与下载原包中的同名 DCP 复制到项目 `work/reference/lightroom-favorites/`，报告为 `outputs/looks/lr-favorites.json`。同名 DCP 已核对 profile name 和 Samsung NX300 相机标识；其 Lightroom profile digest 尚未核对，不安装回 LR。原收藏记录使用的内部 ID 不等于 XMP UUID，不能直接据 UUID 宣称已解析当前收藏全部项目。本次界面只读请求在用户授权后仍被工具拒绝，因此收藏名称依据为之前的 UI 记录与当前文件核对。

当前用户配置目录没有安装这个精确名称的 DCP；先前 LR 实测记录也记载此依赖缺失。旧 v4 预览既漏掉收藏配方，也使用了不同基础风格，不能作为「理光绿 1」的效果。蓝色问题尚未完成修正；应先确认该依赖并以完整配方渲染比较，再生成可移植 LUT。

旧库的 11 个来源核对记录保留在 `outputs/looks/sources-verified.json`；当前 Exact 两项的内部名称、表大小与部署记录见 `outputs/looks/ricoh-exact-import.json`。当前查看器也已显示原厂 LCD 视频回放与不透明的 Metol LUT 面板，截图为 `outputs/firmware-runtime/lcd-metol.png`；面板选择仍是等待原厂 LUT 调用，不会把已在 IPCM 输出之后注入的照片当作机内滤镜结果。

DCP 导入只提取 Creative LookTable，经标准 sRGB／线性 ProPhoto D50 转换生成独立风格片段。donor camera ColorMatrix、ForwardMatrix、双光源 HueSatMap、ToneCurve 和 Adobe 显影过程不包含在片段内，因此当前结果不等价于完整 Lightroom 显影。富士配置沿用用户本地 Inspired 风格来源，不作为富士原厂精确复现。

2026-10-20 的当前 Exact 审计见 `outputs/looks/ricoh-color-audit.json`，复现入口为 `tools/audit_ricoh_looks.py`。两份 master 全网格匹配当前指定 DCP（最大差小于 `5e-10`），两种 D4 编译表字节一致。两份 Exact 的相机矩阵与 HueSatMap 和 NX300 Adobe Standard 相同，但风格另含 `BaselineExposureOffset=+0.5 EV`、128 点非恒等 ToneCurve 及 `DefaultBlackRender=None`，这些并未进入当前 LookTable 片段。蓝青样本与 SDK 分阶段公式一致；负片纯蓝样本在 ProPhoto 域被源表移相约 `+22.05°`。当前证据不支持用交换通道或反转矩阵修正；完整风格迁移需处理曝光在 LookTable 之前对 V 查询的影响，以及后续 RGB ToneCurve，不能把末端提亮当作等价实现。已渲染 JPEG 的原厂色调/黑位不能直接当作线性 RAW 基础，因此当前校色仍未完成。

理光两份 v4 DCP 的 ColorMatrix、ForwardMatrix、HueSatMap 和 ToneCurve 已与本机 Samsung NX300 Adobe Standard 核对相同；这说明其基础标定结构一致，尚不能作为实拍校色结果。XMP 中的清晰度、局部蒙版、高光／阴影等需要实际 Lightroom 渲染或独立实现，当前仅盘点字段。

## 添加与修改

2026-10-21 扩展 `audit_ricoh_looks.py`，在同一假设的未渲染线性 ProPhoto
输入上比较 LookTable 片段与完整的曝光／LookTable／RGB Tone 阶段。
`sky-blue` 样本转换到sRGB后，完整阶段减片段的8位RGB差为正片
`[16.63,-13.52,40.83]`、负片`[56.22,67.35,41.57]`；纯蓝样本还出现输出
sRGB色域裁剪，报告保留裁剪前值和通道。这说明遗漏阶段有明显影响，
不是证明用户照片的蓝色偏差已被定位或校正。样本不是从相机JPEG逆显影
得到；完整阶段仍不能直接替换当前JPEG/sRGB库。活动master、D4表及原始
LR配置均未修改。数值在 `ricoh-color-audit.json` 的
`sample_colors.*.unrendered_style_comparison`。

在项目根目录的 Conda 环境中执行：

```powershell
conda activate nx300m-mod
python tools/looks.py add "D:/LUT/example.cube" my-look --label "自定义风格" --input-space srgb
python tools/looks.py add-metol-slot "D:/LUT/slot.bin" my-metol-look --input-space srgb
python tools/looks.py add-dcp-look "D:/Profiles/profile.dcp" my-dcp-look --label "自定义 DCP 风格"
python tools/looks.py list
python tools/looks.py build my-look
python tools/looks.py sync
python tools/looks.py inspect "D:/Profiles/profile.dcp"
python tools/looks.py preview ricoh-negative-exact "F:/Pictures/example.SRW" outputs/looks/example-preview.png --amount 0.8
```

ID 为 1–63 个小写字母、数字、下划线或连字符，以字母或数字开头。名称最多 191 个 UTF-8 字节，不含竖线、换行或 NUL；当前 Metol 面板最多显示 256 项。新增时拒绝覆盖已有条目；修改已存在风格的 `source.cube` 后运行 `build`。每项包含：

- `source.cube`：可编辑的标准 3D LUT master。
- `table.d4lut`：原厂 D4 格式，三路 9³、10 位数据，每路 729 个样本和一个零填充字，共 4,380 字节。
- `table-stock3d.d4lut`：sRGB 风格的组合表，在每个原厂 3D LUT 网格输出上采样风格 master，再量化为 10 位；用于原厂仍启用其 3D LUT 的路径。
- `look.json`：来源、输入／输出颜色空间、转换范围、标定状态。

`.cube` 支持 DOMAIN_MIN/MAX，拒绝 1D shaper 和非有限数据。Metol 槽格式为 19,712 字节，17³ RGBX 网格，源采样点为 0,16,…,240,255；已执行上游原始 ARM `cube2lut.c` 并核对轴置换结果。上游该提交没有随仓库提供 LUT 素材包，已有内容是槽格式转换与本地风格导入。

`input_space` 记录源 LUT 的颜色约定，不会自动确认或转换成尚未查明的 IPCS 颜色域。`srgb` 默认生成两份表；`nx300m-ipcs` 等其他声明生成直接表。当前组合解决原厂基表被替换的问题，原厂 RGB 编码与源 LUT 的颜色域仍需验证。所有当前机内表的 `calibration.status` 均为 `unmeasured`。

## Python 和本地 HTTP API

Python 调用入口位于 `tools/look_library.py`：

```python
# 在 tools 的导入路径下使用。
from look_library import LookLibrary

library = LookLibrary()
library.add_cube("example.cube", "my-look", label="自定义风格", input_space="srgb")
library.add_metol_slot("slot.bin", "my-metol-look", input_space="srgb")
library.add_dcp_look("profile.dcp", "my-dcp-look")
library.build("my-look")
library.list()
library.preview("my-look", "photo.jpg", "comparison.png", amount=0.8)

from metol_stock import sync_looks
sync_looks()  # 发布项目库到运行中的模拟器，不打开面板、不改变选择
```

查看器仅监听 `127.0.0.1:8765`：

同步会检查表长度、10 位数值和零填充，再使用独立暂存目录与现有 engine 文件锁发布。它不改变 selection generation；若更新了当前选中的同名 LUT，需重新选择该项，等待原厂调用重载。`status: synced` 表示文件同步完成，不表示 ISP 已使用新表。移除的目录不会自动删除，目录列表以当前库为准。

模拟器遥控页的“LUT 风格…”弹窗提供“同步本地 LUT 库”按钮，同步后自动读取原生服务目录。按钮通过 `/simulator/info` 协商，仅在本地查看器提供库同步能力时显示。已在运行中的原厂会话点击验证：同步 17 项，当前选择仍为原厂，没有打开 Metol 面板或重启相机。界面记录为 `outputs/firmware-runtime/lut-library-sync.png`。

| 方法和路径 | 内容与效果 |
|---|---|
| `GET /looks` | 列出当前库的元数据 |
| `POST /looks/sync`，`{}` | 校验并同步项目库，返回数量和 ID；保留当前选择 |
| `POST /lut`，`{}` | 备份进入前选择，打开真实 Metol ARM LUT 面板 |
| `POST /look-select`，`{"id":"ricoh-negative-exact"}` | 部署库，调用 ARM engine 暂存该表 |
| `POST /look-off`，`{}` | 恢复最近一次打开面板前的选择；无有效备份时恢复原厂输入 |
| `GET /status` | 查看真实进程、适配器和 LUT 状态 |

暂存使用私有 `/var/run/nx300m-lut-selection`，由独立计数器给出新 generation；清除选择后不重用旧代号。engine 文件锁串行处理面板和 API 写入。重新打开面板会选中已有请求，OK／“保留并关闭”保留请求，MENU／“关闭”恢复进入前状态。会话结束后这些私有运行文件消失。

面板、ARM engine 与 `lut_port.so` 共同读取 `NX300M_MOD_ROOT`，未设置时为实验室目录 `/mod`。SD 部署可设为 `/mnt/mmc/nx300m-mod`，其下仍使用 `looks/<id>/table.d4lut`；面板显式 `--root` 优先，并把实际根目录传给子进程。根目录必须是最多 160 字节的绝对路径，仅含 ASCII 字母、数字、下划线、连字符、点和斜杠，不含空组件、尾斜杠或 `.`／`..` 组件。无效环境值使 engine 拒绝请求、适配器继续使用原厂表。调用面板、engine 与相机进程时应使用同一根目录。

面板默认从根目录读取 `looks.txt`／`recipes.txt` 和后端；LUT 后端在机身上直接执行 ARM 程序。实验室启动器显式传 `--arm-runner /qemu-arm-static`，该选项仅用于跨架构运行。

自定义 SD 根目录已经过未修改的原厂 `libudd.so.3.1` 隔离探针：Portra 400 三路各 1460 字节一致，坏表回退通过，报告为 `outputs/metol/lut-probe-metol-portra400-custom-root.json`。这里的目标寄存器仍是合成 RAM；该检查验证路径、ABI 与写表字节。

```powershell
python tools/probe_stock_lut.py --look metol-portra400 --mod-root /mnt/mmc/nx300m-mod-probe
```

RAM 服务部署将程序 root 与 LUT 数据 root 分开：原厂相机 hook 保留原 SD root，remote／LUT engine 使用 `NX300M_MOD_SOURCE_ROOT` 与 `NX300M_MOD_SOURCE_ID` 核对规范路径、目录及设备号／inode 后，才从该源目录读取 LUT 并生成同根选择路径；缺少半组环境或身份变化时拒绝，不回退到其他库。没有 SOURCE 环境时保持原 root 行为。RAM 面板的代码、engine、字体、cwd 和状态仍在 RAM，仅 LUT 目录及 hook root 比较使用核验源。这不表示原厂 app 已释放 SD 引用，也不证明色彩已在 ISP 生效。

专项实际复现旧 RAM／SD root 不匹配，再检查新 hook／status／selection 匹配、错误身份拒绝以及真实面板脚本的路径分离；结果见 `outputs/deployment/lut-data-root-probe/report.json`。相机 PID 校验器在此专项中使用替身，未运行实体 hook 或 ISP；ARM remote 和 engine 已构建通过。

`runtime/lut_port.c` 只在原厂程序实际调用 LUT 节点或 `ipcs_3d_lut` 时处理请求，不主动伪造拍摄或重载事件。原厂节点按参数索引缓存写入结果，该索引不包含本项目的选择文件；适配器在文件选择或组合版本变化时调用原厂 Reset，只清除 LUT 节点缓存，然后继续原厂 Update。以 channel 0 开始写表，随后三路使用同一代数据；文件无效时使用原厂 source。状态含义：

- `staged-awaiting-stock-call`：仅暂存，原厂尚未调用。
- `forwarded-to-stock-api`：已转发到原厂写表函数；`forwarded_mask=7` 仅说明三路调用均执行。
- `composition=after-stock-3d-lut`：原厂传入其静态 3D LUT，已选用组合表；`direct` 表示直接表。
- `stock-restore-awaiting-stock-call`：已清除选择，等待原厂下一次调用恢复。
- `image_verified=false`：尚未验证 ISP 输出。

## 已有运行证据及下一步

### 同一原生遥控服务的 LUT API

SD 与实验室共用 ARM remote 服务的 `GET /api/v1/looks`、`POST /api/v1/looks/select` 和 `POST /api/v1/looks/stock`。GET 从 `NX300M_MOD_ROOT/looks.txt` 读取实际条目并核验表；选择 body 仅为 `{"id":"metol-portra400"}`，恢复原厂为空 body 或 `{}`。成功写入返回 202，仍需原厂后续 LUT 调用才可能生效。网页原有设置菜单新增“LUT 风格…”入口，先读实际选择，显示暂存和原厂转发状态，提供明确的恢复原厂按钮。

`lut_engine_shared.h` 供 CLI 与 HTTP 共用清单／ID／实际路径／4380 字节表校验、代次与文件锁。当前直接表和存在的组合表均需有效；文件不能借符号链接转向根目录之外或另一条目。API 用非阻塞锁，争用返回 409，不阻塞遥控 reactor。CLI 新增显式 `stock` 动作，与 HTTP 同样直接清除选择；既有 NXUI `off` 仍恢复 `begin` 备份。二者不会混用。

原生响应分为 `looks`（逐项有效性及是否可选）、`selection`（当前 ID／代次／原厂选择）、`hook`（真实相机 PID 和 root 匹配）与 `status`。缺 hook 时列表仍可读、写入返回 503；marker 必须包含当前 PID 和 root。原厂 JSON 状态也核对 PID、root、generation、selection_path；只有匹配本次选择且 `forwarded_mask=7`、阶段为 `forwarded-to-stock-api` 时，才标记三路已转发。没有回调时显示等待，不构造刷新或拍摄事件。接口参数、错误码与完整字段说明见 [REMOTE.md](REMOTE.md)。

统一HTTP／网页入口已完成重新加载后的定向验收：17项有效且可选，两份Exact选择及读回、CLI面板备份恢复、明确恢复原厂全部通过；网页负片选择后显示“已暂存”，最终已恢复原厂。记录为 `outputs/metol/remote/looks-integration.json`，截图为 `looks-panel.png`。hook标记在原厂Ecore主循环入口写入，避开app在ELF构造器后切换进程留下旧PID的问题。该会话没有执行still LUT调用，`forwarded=false`、`image_verified=false`、`persistent=false`。录制照片回放不经过该ISP风格节点，成像检查仍待完成。

`preview` 只读本地图片，生成“原图／master／D4 9³ 10 位离线近似”三栏 PNG；已有目标文件会拒绝覆盖。SRW 使用原有内嵌 JPEG，不显影 RAW；有 ICC 时按相对色度转换至 sRGB，否则明确记录假设 sRGB。只接受声明为 sRGB 输入／输出的风格，`--amount 0…1` 是本次离线预览的混合强度，不修改 master 或相机选择。

此前使用旧 v4 表和本地 NX300M 照片生成 [理光正片对照](../outputs/looks/ricoh-positive-preview.png) 和 [理光负片对照](../outputs/looks/ricoh-negative-preview.png)，报告为同名 JSON。这张 600×400 样图上，master 与 D4 直接表的每通道平均绝对差约 0.39–0.59 个 8 位单位；这只量化 Pillow 三线性采样下的网格缩减和量化差异，不是理光参考机校色或硬件误差上界。该图片没有参与拟合，不作为跨场景结论。

当前指定 Exact 配置的离线对照另见 [Exact 负片](../outputs/looks/ricoh-negative-exact-preview.png) 与 [Exact 正片](../outputs/looks/ricoh-positive-exact-preview.png)。两张使用 `photo-reference.jpg` 原厂 SRW 内嵌 JPEG、700×467 预览、100% 强度；左为原图，中为当前 master，右为 D4 直接表。负片 master 与 D4 的 RGB 平均绝对差为 `[0.8901, 0.2232, 0.4589]`，正片为 `[0.7131, 0.6361, 0.7479]`（8 位单位）。这些图替代旧 v4 图作为当前导入结果的查看入口，但只覆盖 LookTable 片段和网格量化，不是完整 LR 渲染、机内成像或理光校色结果；该场景蓝色样本很少，不能据此判断蓝色问题已解决。

快门开发已用显式理想化转子和外部 PWM 输入让原厂完成曝光；此情景尚未标定。录制 Bayer 回放已交付数据，EP MnF 的软件 Bayer 拆分也已运行，随后进入第二段 PP 内存处理。第二段 PP 像素处理仍未实现，原厂最终因 JPEG 缓冲为空断言退出，没有生成照片或自定义 LUT 成像。当前开发证据见 `outputs/firmware-runtime/ep-cs-grids-development.json`，实现范围见 `docs/EP_MNF.md` 和 `docs/PP_MEMORY.md`；较早的 `bayer-readout-development.json` 不代表当前进度。

真实面板选中理光负片，OK 保留、切换后 MENU 恢复均已核对私有选择和 ARM 后端日志。清除后重新选择时 generation 从 1 增至 2 的已知回归检查见 `outputs/metol/lut-engine-lifecycle.json`。

```powershell
python tools/probe_stock_lut.py --look ricoh-negative-exact
```

此前 v4 探针执行未修改的 NX300M 1.15 libudd ARM 写表指令，三路目标分别为寄存器基址 +0x9000、+0x9700、+0x9e00，每路 1,460 字节逐字节一致；坏表回退也通过。目标寄存器是合成 RAM，原厂报告 `IPCS DD has not been opened yet`，所以此结果证明 ABI 和字节放置，没有证明 ISP 处理、LUT 启用或成像效果。证据见 `outputs/metol/lut-probe-ricoh-negative-v4.json`。

原厂逆向还确认：`CIpcs3DLutD4::GetIndex` 读取参数 +0x83，Gamma 节点也读取同一字段并设置 sub-index 5；非零时 `SetParam` 额外应用一组 9 点曲线后调用 `ipcs_tone_lut`。曲线 In 为 `[0,154,307,461,614,750,850,940,1023]`，Out 为 `[0,128,256,384,512,640,768,896,1023]`，GOT 重定位已核对。详见 `outputs/metol/gamma-lut-domain.json` 与 `gamma-pipeline.asm`。LUT 颜色域和 Gamma／enable 联动应一起适配，当前不能直接宣称 sRGB 表已在机内正确还原。

原厂静态 3D LUT 的中性轴输出与上述 ToneIn 对应，B 通道一个节点相差 1 个 10 位量化单位。直接用风格表替换它会丢失原厂映射，因此编译器现在另生成 `P(stock3D)` 组合表。单位风格与原厂表组合后，2,190 个字逐个完全相同，证据在 `outputs/metol/stock-composition-check.json`。适配器只有在原厂 source 指向其静态 3D LUT 时选组合版本；原厂传入单位表等其他来源时选择直接版本。原厂启用策略保留，原厂 RGB 编码仍未测定。

```powershell
python tools/probe_stock_nodes.py
```

此探针通过原厂 C++ vtable 调用 LUT 节点，核对 LUT／Gamma 节点对 +0x83 的索引一致性，验证“索引不变会跳过写入”、选择负片／正片时自动清缓存并写入对应组合表、清除请求后恢复原厂静态表。还执行原厂 `ipcs_hl_3d_lut_onoff`，确认寄存器 +0x1040 的 bit 13，其他位保持。构建链接原厂 `libiq`、`libudd`、`libdiosal` 和 production-mode 依赖；结果在 `outputs/metol/lut-node-probe.json`。参数和 MMIO 均为显式合成输入，没有传感器或 ISP 输出。

原厂 `CIpcStillParamD4` 构造器将 3D LUT 加入静态照片参数链，`CIpcLiveviewParamD4` 使用另一组 IPCM 节点且没有该节点。前者 UpdateParameter 还受 `ipcs_get_open_count()>0` 约束。当前模拟器没有完成传感器拍摄路径，正常相机会话仍显示“等待原厂重载”；静态照片节点的验证不能推广为取景 LUT 已实现。分析记录在 `outputs/metol/iq-process-path.asm`、`lut-enable.asm` 和 `ipcs-opinfo.asm`。

## 本地 RAW／JPEG 对照素材

### 原厂 LUT 输入域追踪（2026-10-21）

运行 `python tools/inspect_isp_color_domain.py` 可从原厂 1.15 ELF 和厂商头文件重新提取 still Gamma、GOT 绑定、路由相关定义和原厂 3D LUT 中性轴；结果在 `outputs/metol/isp-color-domain.json`。静态 BaseGamma 为 19 个 X 节点、三组 Y 曲线，X 范围 0..8191、Y 范围 0..1023。按此范围归一化后，三组节点与标准 sRGB OETF 的最大绝对差分别约为 0.171、0.389、0.081；这些表不是标准 sRGB 曲线，但不能由此推断完整管线的最终编码或色域。

原厂索引 +0x83 同时控制 Gamma 的九点调整与 3D LUT；3D LUT 中性轴与 Gamma ToneIn 的反向补偿关系已核对，R/G 完全吻合，B 仅一个节点相差 1。调用顺序和寄存器地址仍不证明硬件像素顺序。下一步需要确定实际路由选择含义，并在固定 still 模式捕获生效 Gamma 和矩阵；目前不能将新的线性 ProPhoto 阶段表直接接入原厂 3D LUT。

### Exact 配置的曝光与曲线阶段（2026-10-21）

运行时参数观察入口：先用 `python tools/build_isp_color_observer.py` 构建并执行独立探针，再在 `tools/run_stock.py --program camera` 的现有启动参数中增加 `--isp-color-observer`。默认关闭，不改变主窗口配置。观察器在私有 `/var/run/nx300m-isp-color-<pid>-XXXXXX/` 中按 Gamma、CCM、RGB→YCC 各保留最近 8 条 JSON，记录完整 8192 项 Gamma 或 9 个矩阵原始字、调用方、后继库及返回值；目录只对运行用户开放。RGB→YCC 仅记录 selector=3 的矩阵，不读取其他 selector 下可能未初始化的矩阵字段；原厂调用仍全部原样转发。

记录点位于该观察器调用后继函数之前；如果同时加载修改色彩的插件，记录不一定等于最终硬件输入。采样原厂参数时应关闭这类插件。独立探针使用指定参数和分配的寄存器 RAM，原厂 Gamma 节点及 UDD setter 真正执行；其结果只证明记录和转发，不证明当前拍摄模式或 ISP 像素结果。观察器构建/探针记录位于 `outputs/firmware-runtime/isp-color-observer-build.json` 和 `outputs/metol/isp-color-observer-probe/`。

`python tools/analyze_capture_color.py <记录目录> --output <汇总.json>` 汇总完整 Gamma 值域、相邻下降数量、同进程同类更新的变化项数，以及矩阵原始位值。它不猜测原色、白点或系数比例。指定参数探针中的两种 Gamma 模式有 8,173 项不同，说明开关关联的不只是少数曲线节点；这不等于当前拍摄模式的实际选值。观察器采用有限槽位，记录缺失不能证明对应处理阶段未运行。

`tools/probe_capture_color.py` 已在独立原厂会话中取得拍摄流程调用记录，成功采样位于 `outputs/firmware-runtime/capture-color/attempt3/`。原厂 P 档由快门输入进入曝光、读出和 IPCS 设置；输入仍是录制 Bayer、实验室时序及软件硬件替代，不是物理传感器。自定义 LUT 和色彩插件均关闭。`observer/72-1-ccm.json`、`72-2-gamma.json` 显示调用方为原厂 libiq、后继为原厂 libudd，矩阵接受返回 0；矩阵有符号位视图为 `[191,-54,-9;-12,164,-24;4,-44,168]`，比例与原色尚未认定。Gamma 为 8,192 项，单调非降、范围 0..1023；与此前节点探针模式0/1分别有8,163/8,162项不同，不能用节点探针参数替代这次实际调用值。

该次拍摄随后出现 IPCS tile 完成错误及 JPEG 设备错误，最终触发 `DscJfif.cpp:242 mybuf.size > 0` 断言（SIGABRT 6），没有新照片。独立会话已停止，主会话身份未改变。前两轮失败证据保留：首轮回放等待就绪失败，第二轮有效快门输入较晚、采集限时结束。第三轮解决采样时机后才获得上述记录；CCM→Gamma 是参数提交顺序，完整硬件像素路由和色域仍待确定，不据此认定蓝色已修正。

补充 RGB→YCC 后的采样在 `capture-color/rgb2ycc/`：原厂实际提交 `[77,150,29;-47,-91,138;138,-115,-23]`（int32 位视图），CCM 与上次一致，Gamma 仍为8,192项。此次提交顺序是 CCM→RGB2YCC→Gamma，同样不能作为像素顺序证明。RGB2YCC观察器先与直接调用原厂setter作全寄存器RAM及输入不变对照，构建/探针通过后才采集原厂拍摄调用；采集最终仍停在相同JPEG断言，独立namespace19972已停止、主会话不变。原始JSON和`analysis.json`保留，未用通用BT.601矩阵替代捕获值。

`python tools/inspect_capture_matrix.py` 生成 `capture-color/matrix-analysis.json`，核对原厂编码和矩阵来源。CCM 的接口接受符号扩展32位整数 −1024..1023，打包低11位；例如 −54 必须传 `0xffffffca`，仅传低11位 `0x7ca` 会被视为1994并拒绝。软件单位基准128已有构造器、Q10 LMW运算、转换右移3位及Q8风格组合除256的代码依据，不能据此声称已验证硬件MAC右移7位。捕获矩阵来自动态 `CColorProcess` 经 PSS/ColorTone 组合，并非可唯一识别的一行静态矩阵。原色、白点及Gamma输入是否线性仍未确认。

`tools/adobe_profiles.py` 新增 `DcpStyleStage(profile).apply_prophoto(rgb)`，供相机校准和 HueSatMap 后、尚未进行风格渲染的线性 ProPhoto D50 数据使用。按 Adobe DNG SDK 顺序实现正曝光 ramp → LookTable → 负曝光 tone → ProfileToneCurve；曲线使用自然三次样条，RGB 通过最大/最小通道映射并插值中间通道，避免逐通道套曲线改变色相。缺少曲线默认报错，只有调用者显式指定时才采用单位曲线。

当前指定正片、负片 Exact 均包含 `+0.5 EV`、128 个曲线控制点和 `DefaultBlackRender=1`。两套 33³ 阶段表已导出至 `outputs/looks/dcp-style/`，对应 JSON 记录源配置名称、颜色域、处理阶段及假设。接口使用 RAW baseline=0、stage3 gain=1、black/shadows=0；不包含相机校准、RAW 显影或 Lightroom 局部调整，也不把已渲染 JPEG 当作该阶段的输入。

```powershell
python tools/looks.py export-dcp-style "配置.dcp" outputs/looks/dcp-style/example.cube --size 33
```

语法编译和针对曝光顺序、保色相映射、真实曲线的定向检查通过；每套 1,127 个曲线采样点与独立自然样条方程对照，最大差约 `4.44e-16`，详见 `outputs/looks/dcp-style/verification.json`。这是浮点算法核对，不是 Adobe Lightroom 输出对照。此前只提取 LookTable 的生产 sRGB LUT 未被新表替换；仍需确认 ISP 输入颜色域并接入正确阶段，才能判断蓝色偏差是否修复。

已扫描 `F:/Pictures`：2,973 张 SRW 的型号均为 NX300M，2,285 张 JPEG。按同名、型号、拍摄时间差不超过 3 秒及现有曝光／ISO／光圈／焦距字段筛出 924 组候选，其中 842 组 JPEG 的 Software 字段是相机版本号形式。文件名和元数据匹配仍可能包含连拍或重复归档，正式拟合前需要核对图像内容和拍摄条件。

```powershell
python tools/photo_pairs.py inventory
python tools/photo_pairs.py preview "F:/Pictures/某文件.SRW" outputs/looks/preview.jpg
```

盘点只读元数据，结果在 `outputs/looks/photo-pairs.json`；`preview` 原样提取 SRW 内嵌 JPEG，不重新编码。已提取一份原始预览并与候选外部 JPEG 做 192×128 对照，RGB 平均绝对差约 0.14／0.12／0.16（8 位），见 `photo-reference.json`。这是素材配对证据，不是理光校色测量。

后续应先确认原厂成像链路中的颜色域和重载／enable，再用这些 NX300M 素材比较离线预期与机内结果。理光参考机的同场景数据仍未找到，当前无法给出“已校准至理光”的结论。

### 真实 RAW＋完整 DCP 阶段的离线参考（2026-10-21）

`tools/render_ricoh_reference.py` 使用 Conda Python 调用 WSL Debian 官方包 `rawtherapee 5.11-2+b2`，只读同一张 `SAM_4262.SRW`，分别指定 NX300 Adobe Standard、用户选定的 `NX300 GR IV Positive Film Exact` 和 `NX300 GR IV Negative Film Exact`。这次从 RAW 开始处理相机 Color/Forward Matrix、双光源自动插值 HueSatMap、LookTable、BaselineExposureOffset 和 ProfileToneCurve；没有把已渲染 JPEG 当作线性输入，也没有只改相机名称或仅抽取 LookTable。

```powershell
python -X utf8 tools\render_ricoh_reference.py
```

CLI 使用独立 XDG 配置／缓存目录，不传 `-d`、`-s` 或 `-S`，从中性参数开始，避免用户默认配置和原 RAW 旁车叠加。有效 PP3 已核对：Camera 白平衡、AMaZE 去马赛克、ProPhoto 工作空间、RTv4_sRGB 输出，四个 DCP 开关均为 true；自动曝光、直方图匹配、额外曝光补偿、黑位／阴影／高光调整、额外色调曲线、锐化和降噪关闭。正负片各自的 `+0.5 EV` 由 DCP 阶段应用，未作为附加曝光补偿重复设置。

三次渲染全部退出 0，输出为同一尺寸 `5528×3688`、orientation=1 和字节完全相同的 `RTv4_sRGB` ICC，图像均非黑图且有正常明暗变化。基准／正片／负片 RGB 均值分别约为 `(116.88,118.94,109.42)`／`(107.78,109.79,98.47)`／`(124.26,130.21,119.09)`。全尺寸 PNG、三张 1440 宽预览、对照拼图、输入 PP3、渲染器保存的完整有效 PP3 和原始日志均在 `outputs/looks/ricoh-reference/`；`report.json` 保存实际来源、配置和图像检查结果。拼图目视可见三种渲染的树叶、路面、人物和车辆，构图一致；该街景天空很少，不能单凭它确认蓝色校准。

官方 5.11 源码选段在 `work/reference/rawtherapee-5.11/`，来源链接记录于 `SOURCE.json`：`procparams.cc` 核对中性默认和 PP3 字段，`main-cli.cc` 核对配置合并顺序和有效 PP3 输出，`rawimagesource.cc` 核对显式 DCP 路径及相机变换，`dcp.cc` 核对色相表、曝光和曲线步骤。

此参考有明确的渲染器边界：Adobe Standard 没有存储 ToneCurve，RawTherapee 根据 Adobe copyright 使用其内置 Adobe 默认曲线；两份 Exact 使用自身 128 点曲线。RawTherapee 将 BaselineExposureOffset 作为 LookTable／ToneCurve 前的乘法增益，并不等于本项目 DNG SDK 风格阶段的所有过曝处理细节；它也不解析 DefaultBlackRender，本次通过关闭额外黑位和阴影调整保持中性。RawTherapee 解码范围比机内 JPEG 的 `5472×3648` 更大，直接逐像素比较会混入裁切／缩放差异。这些图可以作为真实 RAW 上各 DCP 阶段的可视参考，不能宣称与 Lightroom 或三星 ISP 逐像素等价，也没有替换活动 LUT、主模拟器 ISP 或用户 Lightroom 配置。此前偏色尚待用正确输入域和阶段接入后复核。

### 蓝天天空的 LookTable 单因素定位

`render_ricoh_reference.py` 新增 `--profiles` 选择需要渲染的配置，以及诊断用的 `--without-look-table`，默认仍渲染三份完整阶段。关闭 LookTable 时，输入 DCP 文件保持原样，报告、有效 PP3 校验和拼图都明确标注关闭；相机矩阵、Camera WB、HueSatMap、曝光偏移和 ToneCurve 均保留。

```powershell
python -X utf8 tools\render_ricoh_reference.py --profiles positive-exact negative-exact --without-look-table --raw "F:\Pictures\SRW\2026\10\1018\普通保留\SAM_4819.SRW" --output-dir outputs\looks\ricoh-reference\blue-sky-no-look
python -X utf8 tools\analyze_ricoh_sky_roi.py
```

同一张 `SAM_4819.SRW` 的完整参考在 `ricoh-reference/blue-sky/`，两张关闭 LookTable 的参考在 `blue-sky-no-look/`。两次诊断渲染退出 0；分析器读取两组完整有效 PP3，确认每套配置的唯一参数差异都是 `Color Management / ApplyLookTable: true→false`。图像尺寸、ICC 和像素坐标一致，无需重新配准。

固定开阔天空区域的归一化坐标为 `(0.83,0.23)-(0.96,0.28)`，实际像素框为 `(4588,848)-(5307,1033)`。`sky-roi-location.jpg` 保存框选位置，已目视确认位于右上方两条电线之间，避开树叶；`sky-roi-comparison.jpg` 保留两套配置开／关 LookTable 的四张裁片。`sky-roi-comparison.json` 保存完整均值和有效参数差异，指标来自未缩放的 PNG，HSV 是对编码 sRGB 平均 RGB 应用 `colorsys.rgb_to_hsv`，不是色差 DeltaE 或理光校准。

| 配置 | LookTable 开启的平均 RGB | 关闭后的平均 RGB | 开启／关闭色相 | 源表引入的色相变化 |
|---|---|---|---|---|
| 正片 Exact | 180.46, 190.75, 230.61 | 175.98, 203.84, 233.62 | 227.68°／211.00° | +16.68° |
| 负片 Exact | 210.50, 215.39, 236.92 | 195.74, 216.65, 240.16 | 228.89°／211.76° | +17.13° |

同区域 Adobe Standard 色相为 208.32°。本例中两份原始 DCP 的 LookTable 都使天空相对于关闭版本更偏紫，目视四张裁片与数值方向一致。因此，这个偏移在完整 RAW 阶段参考中已存在，可以直接定位到指定源 DCP 的 LookTable 效果，并非只有本项目 JPEG 上的 LUT 预览才出现。此结果不证明源配置的所有蓝色都错误，也不单独证明活动模拟器 LUT 的偏色只有这一个原因；仍需按目标效果修正风格并在正确输入域复核。本次没有修改原始 DCP、活动 LUT 或主模拟器。

本机 Adobe `CameraProfiles/Camera/Ricoh GR IV` 中对应正片／负片原生配置的
LookTable（尺寸、数据、编码）、ToneCurve、BaselineExposureOffset 和
DefaultBlackRender，与指定 NX300 Exact 文件逐值相同；记录为
`ricoh-reference/donor-style-comparison.json`。两者 HueSatMap 不同，NX300
文件使用其相机校准。该证据排除了这几项风格数据在导入时损坏或来自另一套
旧表的解释；并未证明跨相机复制风格表已经正确校色，也未取得 Lightroom
或原生理光同场景输出。后续修正应保留原始配置，将 NX300 适配改动与原生
风格数据区分。

另核对原生 Ricoh GR IV 的 Standard／Positive Film／Negative Film：HueSatMap 尺寸、两光源数据和编码（50937／50938／50939／51107）相同。结合上述风格表一致性，当前证据应解释为原生风格数据作用于 NX300 校准输出时需要颜色适配；不能据本例宣称原生理光相机本身一定出现相同紫偏。实际 Lightroom 显影仍未对照。

### 独立 Blue Adjusted 适配候选

`tools/build_ricoh_blue_candidate.py` 生成两份独立 DCP，名称为 `NX300 GR IV Positive Film Blue Adjusted` 和 `NX300 GR IV Negative Film Blue Adjusted`，不使用 Exact 名称。候选在 `outputs/looks/ricoh-blue-candidate/`，不写入 Lightroom 配置目录或活动 LUT 库。这是主观蓝色保色适配，不是理光标定。

候选仅减弱 LookTable 中蓝色区域的正向 HueShift。ProPhoto HSV 色相 190→215° 使用 smoothstep 升权、215..255° 全权、255→280° 降权；饱和度 .08 以下权重为零、.08→.25 平滑升权。`--strength` 取 0..1，当前候选为 1。公式为 `HueShift -= strength × hue_weight × saturation_weight × max(HueShift,0)`，不修改负向 HueShift、SatScale 或 ValScale。

实现保留原 DCP 全部字节，追加新 LookTable 与名称 payload，仅重定向 50982 和 50936 的 IFD 入口；其他 tag、相机矩阵、HueSatMap、曝光、曲线和编码逐字节核对相同。正片修改 4,071 个、负片 4,852 个 HueShift 节点。中性／非蓝／非正向 HueShift 节点、SatScale 和 ValScale 均通过不变检查，源文件前缀除两项 IFD 入口外不变，原文件读取前后相同。权重在原生 `90×16×16` 网格节点采样，插值会使连续阈值附近有少量过渡外溢；节点检查不等于连续输入在阈值边界外绝对零变化。具体核对见 `build-report.json`。

```powershell
python -X utf8 tools\build_ricoh_blue_candidate.py
python -X utf8 tools\render_ricoh_reference.py --profile-dir outputs\looks\ricoh-blue-candidate --raw "F:\Pictures\SRW\2026\10\1018\普通保留\SAM_4819.SRW" --output-dir outputs\looks\ricoh-blue-candidate\blue-sky
python -X utf8 tools\render_ricoh_reference.py --profile-dir outputs\looks\ricoh-blue-candidate --output-dir outputs\looks\ricoh-blue-candidate\street
python -X utf8 tools\compare_ricoh_blue_candidate.py
```

两张蓝天与两张街景候选均真实从 RAW 渲染，退出 0。比较器复用已有原始 DCP 参考，确认每组完整有效 PP3 的唯一差异都是输入 DCP 路径，四项 DCP 开关和其他参数保持一致。`comparison-report.json` 保存实际结果，两个场景各有 `source-candidate-comparison.jpg`。

| 天空 ROI | 原 Exact 色相 | Blue Adjusted 色相 | 候选平均 RGB |
|---|---|---|---|
| 正片 | 227.68° | 211.02° | 171.90, 199.83, 229.73 |
| 负片 | 228.89° | 212.47° | 206.14, 220.03, 236.42 |

固定同一天空框的色相分别减少 16.66°／16.42°，与此前关闭 LookTable 的 211.00°／211.76° 方向接近，但仍保留各自完整风格的饱和度、亮度和曲线。目视源／候选拼图可见天空紫色减弱、蓝色恢复；街景的树叶、人物和整体曝光未见明显严重异常，蓝色车架及部分路面颜色也有所变化。这是当前两个样本上的主观改善，不证明所有场景正确、也不等于 Lightroom 或三星 ISP 颜色已修复。当前产物保留为可审查候选，未安装到 Lightroom、未替换活动 LUT、未接入主模拟器 ISP。

候选另经现有 `LookLibrary(root=...)` API 编译到
`outputs/looks/ricoh-blue-candidate/library/`，每套包含33³ sRGB master及两张4380字节
的直接／原厂3D表组合版本，记录见 `library-build.json`。这些表仍只包含Creative
LookTable切片，不包含上述RAW参考的完整DCP阶段。

两套切片现已作为独立可选项加入活动 `looks/`：`ricoh-positive-blue-adjusted`
和 `ricoh-negative-blue-adjusted`，标签为“GR IV 正片／负片 蓝色修正”。原 Exact
项保留，目录共19项；已同步主模拟器和 SD 候选包，没有安装到 Lightroom。
原生 API 对两项均返回合法可选表，选择返回202并读回相同ID；网页正片选择
也已实际暂存。最终恢复原厂选择，主相机PID28986、namespace28897保持不变。
证据见 `ricoh-blue-candidate/simulator-integration.json` 与
`simulator-ui-selected.png`。实际状态为 `staged-awaiting-stock-call`，
录制照片回放不会因此显示风格效果，完整ISP成像仍未验证。

### 由机内 JPEG 与完整 DCP 参考拟合 LUT（2026-10-22）

`tools/fit_camera_look.py` 新增经验拟合接口：输入同一曝光的 SRW（只读取其原始机内 JPEG）、已有完整 DCP 显影图和现有 sRGB LUT，拟合独立的9³ sRGB LUT。输出 `.cube`、4380字节 `.d4lut`、场景留出评估和对照图；不自动加入活动库，也不改变模拟器像素链。它用于近似目标显影的整体颜色，不将 JPEG 当作线性 RAW 或直接套完整 DCP。

几何处理先把带 ICC 的目标转换至 sRGB，再以 SIFT 特征估计缩放、旋转和平移受限的仿射变换。25%匹配点只用于几何误差检查。颜色采样排除边界、强纹理和源端近裁剪像素，以颜色格限额减少大面积天空或墙面占比。拟合采用三线性节点权重、对原风格切片的残差和相邻残差平滑，未覆盖区域主要由原 LUT 与平滑约束决定。原始文件和先验 LUT 不允许被输出覆盖。

当前 `outputs/looks/camera-fit/pairs.json` 连接已核实的蓝天 `SAM_4819.SRW` 和街景 `SAM_4262.SRW`，目标为前节的完整 RawTherapee Blue Adjusted 正片／负片参考。没有修改 Exact 或 Blue Adjusted DCP。依赖已在 Conda `nx300m-mod` 内安装，记录于 `environment.yml`。

```powershell
python tools/looks.py fit-camera-look outputs/looks/camera-fit/pairs.json outputs/looks/camera-fit
```

Python API 为 `fit_camera_look.run(manifest_path, output_dir, size=9, max_edge=1000, limit=10000, ridge=2., smooth=4.)`。JSON 包含 `styles` 字典（每项提供 `prior_cube`）和 `scenes` 列表（每项提供唯一 `id`、`raw` 及按风格名索引的 `targets` 图片路径）。每个场景应使用不同曝光的 RAW；目标必须由对应 RAW 显影，使用相同几何与一致的渲染设置。增加场景后，工具逐一留出该场景，用其余场景拟合；最终候选再使用全部场景。

语法编译与实际拟合退出码均为0。1000像素宽检查空间中，蓝天／街景各有828／862个未参与几何拟合的匹配点，误差中位数0.110／0.138像素，95分位0.315／0.533像素。裁切与缩放差异已经配准，没有将原图与显影图直接按相同坐标比较。

| 风格 | 留出场景 | 原风格切片 RMSE | 新拟合 RMSE |
|---|---|---:|---:|
| 正片 | 蓝天 | 16.39 | 10.33 |
| 正片 | 街景 | 11.54 | 7.57 |
| 负片 | 蓝天 | 29.86 | 13.47 |
| 负片 | 街景 | 23.76 | 9.36 |

单位是采样区域编码 sRGB 的8位通道 RMSE，不是 DeltaE。每行所用新模型未见该行场景的颜色样本。留出图 `*-heldout.jpg` 与全部场景拟合图分开保存；已目视检查负片蓝天和正片街景留出图，整体明暗／颜色更接近参考，仍有树叶饱和度和天空明度差异。完整数据在 `outputs/looks/camera-fit/report.json`。

两张照片仍只覆盖有限颜色，最终两套729节点中各有569节点的累积采样权重小于1；部分节点通道还需裁剪至0..1。工具没有把这些节点称为已校准。当前结果不能证明人像、夜景、其他白平衡或其他机内画面设置下的效果，也不能恢复原JPEG中已裁剪的颜色。机内JPEG未嵌ICC，输入按sRGB处理；目标是RawTherapee参考，不是Lightroom或理光实机测量。原厂ISP的LUT输入域仍未确定，因此这些产物只保存在独立候选目录，活动库维持19项。



### 外部 LUT 样本的诊断编码与相册读取（2026-10-23）

这条实验链路是：对已有 JPEG 在宿主应用 LUT，生成 Y/CbCr 样本，在独立相机会话的 JPEG 任务边界注入，再经 CPU libjpeg 压缩及原厂 EXIF/MPO 封装、写盘。它验证诊断编码和相册读取，不是传感器→ISP→机内 LUT 成像。所用 `ricoh-positive-exact` 仍是前文审计的 Creative LookTable 切片，不代表完整 DCP。

早期 `capture-ricoh-positive/SAM_0004.JPG`、`capture-fuji-classic-chrome/SAM_0005.JPG` 使用了半高度色度样本，输出下半部呈绿色，保留为失败证据，不能作为颜色正确的示例。旧加载代码忽略短读，未覆盖的区域继续使用原快照；并非 EOF 自动填零。

后续改用本次任务所需的全高度交织 CbCr（4:2:2，NV16），现有输出已完整解码：

| 外部 LUT 样本 | 保留的诊断输出 | 文件字节 | 下半部 RGB 均值 |
|---|---|---:|---|
| 理光正片 | [SAM_0006.JPG](../outputs/firmware-runtime/capture-ricoh-positive-nv16/SAM_0006.JPG) | 5,189,142 | [141.806,143.312,144.954] |
| 富士 Classic Chrome | [SAM_0007.JPG](../outputs/firmware-runtime/capture-fuji-classic-chrome-nv16/SAM_0007.JPG) | 5,166,440 | [141.592,144.443,144.883] |

两份主图均为5472×3648、JPEG 4:2:2。它们与当前注入 Y/CbCr 重建 RGB 的8位通道 RMSE 分别为0.8375、0.8363，反映压缩和解码误差，不是相对 LR、理光相机或原厂 ISP 的色差。原厂封装还保存1616×1080预览和160×120缩略图；相册缩略图读取不构成全尺寸颜色验证。

样本目录可显式传入 `probe_capture_color.py --jpeg-cpu-diagnostic --diagnostic-sample-dir /mod/samples_ricoh`，仍须指定全新的独立输出目录。启动记录和 session 保存选定目录、文件名、长度及mtime；这些是使用前的来源记录，不保证运行期间文件未被其他进程修改。编码入口另外核对实际请求的完整平面长度、读取和 EOF，任一缺失、短读、额外字节或 I/O 错误均不提交输出。详见 [JPEG_ENCODE.md](JPEG_ENCODE.md)。

此路径不更新主模拟器 ISP，报告继续保留 `image_verified=false` 和 `new_capture_jpeg_verified=false`。预览与新拍 JPEG 的机内颜色仍待完成。

### 完整 RAW 显影与显式颜色阶段 API（2026-10-23）

`tools/convert_camera_look.py` 将完整 DCP RAW 显影封装为可调用接口，和用于已有 JPEG 的经验 LUT 分开使用：

```python
from convert_camera_look import render_raw, selected_profile
report = render_raw("SAM_4819.SRW",
    selected_profile("ricoh-negative-blue-adjusted"), "negative.png")
```

命令行入口为 `python tools/looks.py render-raw-dcp INPUT.SRW OUTPUT.png --style ricoh-negative-exact`。
四个可选 ID 为 `ricoh-positive-exact`、`ricoh-negative-exact`、`ricoh-positive-blue-adjusted`、`ricoh-negative-blue-adjusted`。Exact 仍严格使用用户指名的两份已安装 DCP；蓝色修正仍单独命名。也可以显式使用 `--profile PATH.dcp` 扩展 NX300 配置。输入 SRW、DCP 和用户 LR 配置保持只读，已有输出不会被覆盖。

此接口在隔离的 WSL RawTherapee 配置内处理相机白平衡、相机校准、双光源 HueSatMap、LookTable、曝光偏移和色调曲线，并核对实际生效的参数及输出 ICC，成功后才提交 sRGB PNG。它依赖电脑端 RawTherapee，不是相机 ARM 内的显影程序。新的 `SAM_4819.SRW` 负片蓝色修正输出为 `outputs/looks/dcp-style/negative-blue-api.png`；与之前同一 RAW、同一 DCP 的完整参考逐像素核对记录在 `raw-api-verification.json`。

另有矢量化接口 `transform_linear(rgb, profile, input_space=..., input_stage=INPUT_STAGE, output_space="srgb")`：输入只允许 `linear-prophoto-d50` 或 `linear-srgb`，并且必须已经完成相机校准与 HueSatMap、尚未应用风格曝光与曲线。接口拒绝编码 sRGB，不能直接给已渲染 JPEG 使用。它处理完整 DCP 风格阶段与最终色域／编码转换，按65536像素分块控制内存；不会猜测原厂 IPCS 的颜色域。四份真实 DCP、每份3006个随机与边界输入和已有标量 DNG 风格实现比较，最大差小于 `2e-15`，见 `dcp-style/api-verification.json`。

这次 API 核对证明转换与输入阶段约束有效。理光实机校色仍需要同场景参考；原厂 IPCS 表的硬件输入域仍不能用 sRGB 或线性 ProPhoto 猜代。

### 同源 Bayer 包的完整 DCP 软件参考

`tools/bayer_dcp.py` 提供 `packet_to_dng(packet, reference_raw, output, profile, crop=...)` 和可追加新实测数据的 `write_bayer_dng(pixels, output, profile, whitebalance=..., black=..., white=..., pattern=...)`。前者先按原厂12位两像素三字节格式解包，确认选定活动区域与指定原始 RAW 的可见 Bayer 样本完全相同，再写标准无压缩 DNG；后者接受明确的未渲染 `uint16` CFA 数组。DNG 包含真实 AsShotNeutral、黑白位、CFA 相位和 NX300 的双光源 ColorMatrix／ForwardMatrix，不使用已有 JPEG 替代传感器样本，也不引入未测量的补边。

当前有效包为 `outputs/firmware-runtime/pp-active-bayer-fixture.bin`，活动区为 `[78,18,5536,3696]`。已恢复同源 RAW 在 `work/reference-media/SAM_4262.SRW`。这20,461,056个样本与原始 RAW 完全一致；生成的 `outputs/looks/dcp-style/recorded-bayer-4262.dng` 经 LibRaw 重新解码后也逐样本相同。实际白平衡为 `[11792,4096,7120,4096]`，黑位均0、白位4095、左上相位GRBG。核对见 `dcp-style/bayer-dng-verification.json`。

最小使用方法（输出路径必须尚不存在）：

```powershell
python tools/looks.py bayer-to-dng outputs/firmware-runtime/pp-active-bayer-fixture.bin work/reference-media/SAM_4262.SRW outputs/looks/my-frame.dng --crop 78 18 5536 3696
python tools/looks.py render-raw-dcp outputs/looks/my-frame.dng outputs/looks/my-positive.png --style ricoh-positive-exact
python tools/looks.py render-raw-dcp outputs/looks/my-frame.dng outputs/looks/my-negative.png --style ricoh-negative-exact
```

本次四份完整显影输出为 `dcp-style/bayer-positive-exact.png`、`bayer-negative-exact.png`、`bayer-positive-blue-adjusted.png` 和 `bayer-negative-blue-adjusted.png`，与原始 SRW 的对应 RawTherapee 完整参考比较记录在 `dcp-style/bayer-full-dcp-verification.json`。它们是从实测 Bayer 样本开始的完整 DCP 软件参考，可用于核对轻量软件 ISP 的去马赛克、白平衡、矩阵和风格阶段；仍不代表三星硬件 ISP 或实体理光的颜色已测量。
