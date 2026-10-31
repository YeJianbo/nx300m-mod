# LUT 库与色彩配置

LUT 库将 `.cube`、DCP 风格表和兼容的 Metol 表转换为 NX300M 使用的数据。每个风格保存在 `looks/<id>/`，包含源表、编译后的 D4 表和记录来源及色彩空间的 `look.json`。仓库自带 Hard Mono；其他风格需要从有权使用的来源导入。

本地风格包括 GR Neg、GR Pos，以及富士 CC、Astia、Eterna、NC 和 NN。理光两项来自指定的 GR IV Negative Film Exact 和 Positive Film Exact 配置；当前提取的是 DCP Creative LookTable，完整 Lightroom 曝光、色调曲线及 RAW 显影尚未移植。蓝色表现和机身色彩需要继续校准。

## 添加和编译

在项目根目录的 Conda 环境中执行：

```powershell
conda activate nx300m-mod
python tools/looks.py add "D:/LUT/example.cube" my-look --label "自定义风格" --input-space srgb
python tools/looks.py add-metol-slot "D:/LUT/slot.bin" my-metol-look --input-space srgb
python tools/looks.py add-dcp-look "D:/Profiles/profile.dcp" my-dcp-look --label "DCP 风格"
python tools/looks.py list
python tools/looks.py build my-look
python tools/looks.py sync
```

`source.cube` 是可编辑源表；`table.d4lut` 是三路 9³、10 bit 的直接表；`table-stock3d.d4lut` 用于原厂 3D LUT 与风格组合的路径。修改源表后重新运行 `build`，再同步到目标环境。

`input_space` 标明来源的颜色约定。NX300M ISP LUT 所处的颜色空间及前后变换仍需实机确认，标准 sRGB LUT 的视觉结果需要校色。

## Python 接口

```python
from look_library import LookLibrary

library = LookLibrary()
library.add_cube("example.cube", "my-look", label="自定义风格", input_space="srgb")
library.build("my-look")
library.list()
library.preview("my-look", "photo.jpg", "comparison.png", amount=0.8)
```

运行时需要将项目的 `tools` 加入 Python 导入路径。预览是在电脑上处理照片，机内加载方式见 [LUT 模块](LUT_MOD.md)，校色方法见 [LUT 校准](LUT_CALIBRATION.md)。

## 加载状态

选择风格后，适配器等待原厂 LUT 更新调用；`staged-awaiting-stock-call` 表示正在等待，`forwarded-to-stock-api` 表示数据已交给原厂写表函数。0% 强度透传原始数据。ARM VM 已检查加载、强度和恢复，实际成像需通过相机照片确认。
