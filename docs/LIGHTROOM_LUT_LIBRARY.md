# Lightroom 风格扩充

2026-11-03 从本机 Camera Raw／Lightroom 资源中新增 60 款，本批完成时活动库合计 80 款。原有 20 款逐文件核对未变，保留 Hard Mono、GR Neg／GR Pos、富士 CC／Astia／Eterna／NC／NN。随后导入的旧通用风格另见 [个人 Lightroom 风格移植](PERSONAL_LUT_LIBRARY.md)，当前总数为 143 款；本页保留这 60 款的独立评测和包。

## 新增目录

| 类别 | 数量 | 风格 |
| --- | ---: | --- |
| 尼康 Z fc | 20 | Binary、Bleach、Blue、Carbon、Charcoal、Denim、Dramatic、Dream、Graphite、Melancholic、Morning、Pink、Pop、Pure、Red、Sepia、Silence、Somber、Sunday、Toy |
| 适马 fp | 18 | Cinema、Forest Green、FOV Classic Blue／Yellow、Powder Blue、Sunset Red、Teal and Orange、Warm Gold，及 Duotone B1／B2／G1／G2／M1／OR1／P1／R1／YE1／YG1 |
| LR Artistic | 8 | 01～08 |
| LR Modern | 5 | 05、07～10 |
| LR Vintage | 7 | 01、02、03、05、08、09、10 |
| 富士 X-T5 | 2 | EBB（BLEACH BYPASS）、Sepia |

每款都保存在 `looks/<id>/`：

- `source.cube`：sRGB 输入输出的风格母版；原生 16³ 表烘焙为 33³，25³／32³ 表烘焙为 65³。
- `table.d4lut`：现有编译器生成的原生 9³／10-bit 表，4380 字节。
- `table-stock3d.d4lut`：与现有原厂 3D 表组合的版本，4380 字节。
- `look.json`：名称、来源资源、原始表身份、颜色编码及加载状态。

## 提取方法

`tools/import_lightroom_rgb_tables.py` 读取 XMP 的 `RGBTable`，解码 DNG 的小端 radix-85／zlib 内容；表不内嵌时，从本机 CameraRaw.dll 查找与 XMP 指纹一致的序列化表。每张实际解码表的 MD5 与引用 ID 核对。

统一处理 Adobe RGB 原色、SDK gamma 2.2 的 cubic toe、gamut clip、四面体插值和 amount 1.0，再转换为 sRGB `.cube`。转换为矢量实现后，与已有 NC 标量实现的 641 个随机／灰阶点最大差为 `1.1e-15`。

提取范围是配置中的 **creative RGBTable 色彩层**。原相机的标定、基础 CameraProfile／LookTable、LR 显影滑杆、颗粒和空间处理不在这层内，因此完整 LR 配置的渲染效果还会不同。`RGBTableAmount` 默认值另存元数据，当前母版统一使用 1.0。

来源记录仅保存 `Adobe/Profiles` 内的相对资源名。原始 XMP、DLL、照片及 Lightroom 目录数据库均未修改。

本轮没有纳入的内容：

- Modern 01／03／04 没有独立 RGBTable。
- Modern 02 使用 gamut-extend，尚未实现该色域处理。
- Modern 06、Vintage 04 使用另一组原色／Gamma 编码，尚未实现该转换。
- Vintage 06／07 与 Vintage 01 使用相同 RGB 表，不重复增加菜单项。
- LR B&W 目录的 HSV 表与灰度／显影调整需要另一套组合模型，本轮没有把它们当作独立 RGB 表导入。

逐项来源和跳过原因见 `outputs/looks/lightroom-import/inventory.json`。

## 色彩库包

`outputs/looks/lightroom-import/nx300m-look-library-80.zip` 包含 80 款目录、159 张原生表和 `looks.txt`，共 240 个文件。解压到 SD 卡根目录后，路径为 `nx300m-mod/looks/` 和 `nx300m-mod/looks.txt`。包内保留原 20 款及其顺序，不包含脚本、运行时二进制和选择状态。

目录数量低于现有引擎／面板的 256 项上限；打包时检查了所有表的字数、10-bit 范围和填充字，压缩包 CRC 核对通过。

## 编译与评测

新增批次使用与原 20 款相同的离线协议：21376 个训练样本；独立 24000 个连续 RGB、16000 个 RGB888 和 256 个灰阶样本，以及两张未用于拟合的照片。每款使用相同参数，强度 100%。

`tools/evaluate_lightroom_luts.py` 对比原编译器、`fit` 和 `fit-integral`，分别记录灰阶、肤色近似区、绿色、蓝色、阴影、高光、渐变、原厂组合表，以及从原 RGBTable 烘焙到 `.cube` 的误差。原厂直通作为额外对照。结果保存在独立批次，不覆盖原 20 款的评测。

正式库现默认使用普通 `fit`，直接表和原厂组合表均进入 SD 打包链。原始离线对照保留在 `outputs/looks/lightroom-import/profiles/`。软件评测采用 RGB-code／三线性模型，NX300M 的实际 LUT 颜色域与插值仍需实机标定。编译、写表验证与 legacy 回退见 [LUT 编译工作流](LUT_QUALITY.md)。

兼容检查覆盖 17³／33³／65³、非单位 DOMAIN、源输出越界后的裁剪、identity 与原厂组合逐字一致。完整结果为 `compatibility.json`。

### 60 款实际结果

RGB 误差为 8-bit 通道单位，均为全部风格的宏平均：

| 指标 | 原编译器 | fit | fit-integral |
| --- | ---: | ---: | ---: |
| 独立 RGB888 RMSE | 2.93673 | 2.01369 | 1.99466 |
| 平均 OKLab 距离 ×100 | 0.46549 | 0.33391 | 0.33060 |
| 近灰区 RGB888 RMSE | 1.57587 | 0.71218 | 0.71383 |
| 两照片／120 组合 RMSE | 1.42124 | 0.95475 | 0.96405 |
| 渐变步长残差 P99 均值 | 0.18254 | 0.18282 | 0.17891 |

普通 `fit` 的总体 RGB888 误差下降 31.43%，照片误差下降 32.82%。60 款总体 RGB／OKLab 和所列六个色彩区域的 RMSE 均低于原编译器。渐变局部步长并非所有指标都改善。连续积分法额外降低约 0.95% 的整体 RGB888 误差，但照片和近灰区均值略差，因此正式工作流采用普通 `fit`。

强烈非线性风格的剩余误差较高：Binary 的 `fit` RMSE 为 12.107、Powder Blue 为 5.239、Vintage 10 为 4.535。不能将这三款与误差不足 1 的柔和风格视为同等保真。

原 RGBTable → 母版 `.cube` 的误差另行统计，60 款均值为 0.356；Binary 为 2.741、Powder Blue 为 1.774。这部分来自色域／Gamma 转换与四面体源表重新采样，独立于后续 9³ 缩减，不能全部归因于 9³ 编译器。

浏览 `outputs/looks/lightroom-import/index.html` 可切换全部新增风格、两场景及候选算法；`comparison-0.png`、`comparison-1.png` 为代表风格接触表，`report.json` 保留逐款区域、灰阶、渐变和组合表指标。

### 编译耗时与机内开销

本机 CPU、单 BLAS 线程，直接表编译各重复 3 次，中位数如下。源文件解析与公共训练系统准备单独计时，不计入本表；这是批量编译的稳态计时。

| 母版 | 原编译器 | fit | fit-integral |
| --- | ---: | ---: | ---: |
| 富士 NC，33³ | 14.8 ms | 313.0 ms | 361.5 ms |
| 尼康 Binary，33³ | 14.9 ms | 2007.8 ms | 1935.7 ms |
| LR Artistic 01，65³ | 15.4 ms | 433.4 ms | 580.1 ms |

公共训练系统准备为 113.3 ms。33³ 文本源解析约 1.28～1.30 秒，65³ 样本约 9.85 秒；导入总耗时还包括这部分，以及 RGBTable 提取、母版烘焙、组合表和文件写入。原始计时、环境及每次样本见 `performance.json`。

三种方法输出都为 9³／10-bit／4380 字节。相机运行时只读取当前选择的表，再交给相同的原厂写表调用；拟合算法在电脑端执行，不加入取景逐帧路径。80 款增加 SD 存储和目录条目，不要求把 80 张表同时加载到 RAM。本轮未测实体相机的 FPS、完整拍后耗时或功耗。

复现：

```powershell
python -X utf8 tools/import_lightroom_rgb_tables.py

$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python -X utf8 tools/evaluate_lightroom_luts.py --resume
```

已保存的导入条目会按表身份复用。评测的 `--resume` 复用当前批次已完成的条目；更改编译算法后应去掉该参数重新评测。

取景与成片 Hook 没有改变。fit 表已接入原有成片写表链，机内颜色效果等待实拍验证。
