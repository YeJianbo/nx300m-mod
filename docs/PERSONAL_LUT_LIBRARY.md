# 之前的 Lightroom 风格移植

导入来源是之前制作并安装的“通用相机与胶片风格”配置，包括宾得风雅、SATOBI、四季、交叉冲洗，ColorReps 胶片与相机风格，以及六款原创清新风格。读取当前安装的 XMP，保留当时对暗调氛围、清透明快和电影浓烈所做的曲线修正。

通用目录有 63 份配置。理光仍使用已指定的 GR Neg／GR Pos，不另加 GR II 鲜艳，其余 62 份进入机内库。ColorReps 项显示 `CR` 前缀，区分已有同名风格；原有 Hard Mono 和其他风格保留。

独立宾得 DCP 按 Creative LookTable 去重后新增 Natural：Bright 与现有 Pentax Bright 相同，Portrait／Vibrant 的这一层与 Natural 相同，不增加重复表。Natural、Portrait、Vibrant 的差异在 ForwardMatrix，属于本批未移植的相机显影部分。因此本批新增 63 款，活动库总计 143 款。PROVIA 线性 DCP 测试版依赖另一条基础显影链，本批使用通用目录中的 ColorReps PROVIA。完整清单和去重记录见 `outputs/looks/personal-import/inventory.json`。

## 转换

原配置使用 DNG RGBTable 的 sRGB 原色、sRGB Gamma 和 clip 模式。解码相对单位网格的 uint16 环绕差值，按四面体插值生成 65³ sRGB 母版，再沿用正式 `fit` 编译器生成两份 9³／10-bit 表：

- `table.d4lut`：直接表。
- `table-stock3d.d4lut`：与原厂成片基表组合的版本。

输入、输出均已是 sRGB 编码值，因此这条导入路径直接采样色彩表。现有 Adobe RGB／Gamma 2.2 路径继续使用其色彩转换。

DCP 项沿用现有 Creative LookTable 提取接口。DCP 自身的曝光偏移、ToneCurve 和相机标定仍由原来的 RAW 显影链处理，不包含在这份机内风格表内。

## 构建与导入

```powershell
conda activate nx300m-mod
python tools/import_personal_lut_profiles.py
```

可用 `--profiles` 指定通用 XMP 文件夹，`--output` 指定输出目录；`--no-dcp` 只处理通用 RGBTable。再次运行时核对已有来源，保留已有风格。

产物位于 `outputs/looks/personal-import/`：

- `nx300m-look-library.zip`：完整活动库的原生表、便携元数据和 `looks.txt`。
- `nx300m-personal-luts-legacy.zip`：本批新增风格的 legacy 编译表，用于单独回退。
- `inventory.json`：来源身份、逐款误差、跳过项及原库保留检查。
- `README.md`：名称和来源清单。

主包解压到 SD 卡根目录，更新 `nx300m-mod/looks/` 与 `nx300m-mod/looks.txt`。包中不包含运行时二进制、启动脚本或当前选择状态。

## 检查范围

导入逐项核对 Adobe 表指纹，并将解码后的表与独立标准 CUBE 四面体采样对照。使用同一组 16,000 个独立 RGB888 颜色和 257 个灰阶样本，比较源表、母版、legacy 与 fit 的离线误差。

打包检查每张表的 4,380 字节长度、10-bit 数值、填充字及元数据对应关系，回读 ZIP 内容。既有风格和原始 LR 配置逐文件核对保留。

这些指标衡量离线转换和缩减误差；本批尚未进行实体相机的色彩对照。

63 款的宏平均 RGB888 通道 RMSE 为：legacy `1.4371`，fit `0.9513`，下降 `33.81%`。这组独立样本中，所有新增风格的总体 RMSE 都低于 legacy。sRGB 表解码与独立四面体采样的最大差为 `0`；65³ 母版的重采样误差另存逐项记录。

`comparison-0.png` 和 `comparison-1.png` 提供街景与室内两组对照，每组包含原图、原 LR 色彩表和 fit 原生表的离线输出。
