# NX300M 1.15 图片向导与智能滤镜

本轮把风格名称、内部选择编号、矩阵／曲线表，以及取景／成片分支连了起来。图片向导和智能滤镜是两套机制，不能把它们当成一批隐藏的 `.cube`。

复现：

```powershell
python -X utf8 tools/inspect_native_style_pipeline.py
```

输出：`outputs/metol/native-style-pipeline.json`、同名 `.asm`。JSON 包含原库身份、PIC 表定位、逐款参数和直接调用地址；ASM 保留对应原指令。这里只读取原厂 ELF。

## 图片向导

已连通的选择链：

```text
USERDATA_PW（0x10，原厂界面名称）
  → CCapParamIf::convertParamValue（IQ 0x2c）
  → CIpcIfInfo::GetPss（0x3501f4）
  → Still / Liveview GetSysInfo
  → ColorD4::GetRgb2RgbIndex / GammaD4::GetIndex
  → PSS 矩阵、EffectTone 曲线选择
```

以下是普通 PW 路径的基础选择。饱和度、对比度、色彩设置和场景／镜头特效仍有独立输入。

| 图片向导 | IQ 0x2c | PSS | PSS 矩阵行 | EffectTone 行：成片／取景 |
| --- | ---: | ---: | ---: | ---: |
| Standard | 0 | 0 | 0 | 0 / 0 |
| Vivid | 6 | 6 | 6 | 0 / 0 |
| Portrait | 14 | 12 | 0 | 1 / 1 |
| Landscape | 15 | 13 | 0 | 0 / 0 |
| Forest | 5 | 5 | 5 | 0 / 0 |
| Retro | 4 | 4 | 4 | 0 / 0 |
| Cool | 3 | 3 | 3 | 0 / 0 |
| Calm | 2 | 2 | 2 | 0 / 0 |
| Classic | 1 | 1 | 1 | 2 / 2 |
| Custom 1 / 2 / 3 | 16 / 17 / 18 | 0 | 0 | 0 / 0 |

矩阵行 0 是单位矩阵。Landscape 在这两个选择器里使用默认行，**这不足以解释其最终效果**；完整风格还涉及已存用户参数和其他处理节点。Custom 使用默认行同样不表示用户调整无效。

### 原厂真正怎样形成风格

`m_iPssMatrix` 的成片和取景九张矩阵逐项相同。成片表地址 `0x36d5c8`，取景表地址 `0x36e158`。

Classic 的矩阵三行均为 `[78,150,28]`，分母 256：先在该阶段的 RGB 上形成相同的三个输出。它还选择第 2 行 EffectTone。不是仅把饱和度滑杆拉到最低。

Retro 的矩阵为：

```text
[206,  68,  14]
[ 18, 206,  18] / 256
[ 36,  72, 124]
```

三行和为 288、242、232，会改变中性输入的冷暖关系。不能在 LUT 优化中把所有灰色强制变成无色；那会消掉风格本身。

Portrait 的额外 EffectTone 使用七点：

```text
X: 0,168,344,512,680,856,1023
Y: 0,176,364,544,708,872,1023
```

它把中间调向上提，不靠 3D 表承担这段明暗曲线。取景使用对应的八位曲线 `[0,44,91,136,177,218,255]`，并非直接截取成片表的字节。

颜色节点的实际矩阵组合为：

```text
current_base → PSS × current_base / 256
             → ColorTone × result / 256
             → 原厂 CC setter
```

每次整数矩阵乘法向零取整。`SetRgb2Rgb @0x33e388` 的两次乘法调用位于 `0x33e508`、`0x33e51c`。当前 base 由 `CColorProcess::CalculateColorMatrix @0x334e28` 生成，包含 Illum D/T 插值及暗部补偿分支，不能拿启动时的一张矩阵永久覆盖。

### Gamma 与 3D 表的关系

Gamma 的**参数生成顺序**已确认：基础／自适应曲线 → EV 调整 → Contrast → EffectTone → Gamma 快照 → 条件 3DLutTone 重映射 → 最终样条表。它不直接证明硬件像素的经过顺序。

还确认了原厂 3D 选择的来源：

- `CIpcIfInfo::Get3DLut @0x350ad8` 调用 `IQ_AE_GetAeValue(output,0x18)`。
- `0x350af0–0x350af8`：返回值不大于 160 时选择 1，否则选择 0。该 AE 量的单位尚未查明。
- `CIpcs3DLutD4::GetIndex @0x33bba0` 和 Gamma 的选择字节均使用状态 `+0x83`。
- `m_i3DLutToneIn = [0,154,307,461,614,750,850,940,1023]`；原厂 3D 表的九个灰轴节点 R/G 逐项吻合，B 只有一点差 1。

这证明原厂曲线与 3D 表有配套关系，也证明这个 getter 不是“按图片向导名称挑不同 3D 风格表”。完整色彩域和硬件顺序仍需进一步确认，详见 `outputs/metol/isp-color-domain.json`。

## 智能滤镜

已拆开三套编号：界面 `SMARTFILTERTYPE`、`E_LIVEVIEW_SMARTFILTER` 和 `eADE_MODE`。例如暗角分别是 `1 → 0 → 10`，微缩是 `2 → 1 → 1`。直接复用其中一个编号会调用错效果。

已连通：

```text
取景：GetSmartFilterMode @0x26f67c
    → CLiveviewNodeSmartFilter::smartFilter @0x27d14c
    → ADE_SmartFilter_YccProcess @0x2a8c

成片：CProcessParameter::updateEffectMode @0x233828
    → CEffectProcess::processSmartFilter @0x2a7e10
    → 同一 ADE dispatcher 或独立选色处理
```

dispatcher 的 13 个效果分支及 14 个名称已提取。原厂处理不局限于 LUT：

成片 dispatcher 还接受界面 `SMARTFILTERTYPE` 名称表之外的编号：35 → Softfocus、37 → Defog、39 → Cross。`updateEffectMode` 有从 `CSmartArtAdapter::GetEffectType` 取得值的另一来源，不能把这些额外分支直接当成普通智能滤镜菜单的选项。

| 效果 | 可见的实际处理 |
| --- | --- |
| Miniature | 单独的 View 参数函数、MnF 微缩处理和多处 Y 卷积 |
| ColoredPencil / OilSketch / WashDrawing / Acryl / InkSketch | MnF Animate 等参数路径；部分有后续卷积 |
| WaterColor | MnF Suchae、卷积；另有 IPCS resize 调用 |
| Vignetting | MnF Open → Vignetting 参数 → Close |
| Softfocus | 软件一维曲线、卷积、YCC Mixer，含缓冲切换 |
| Defog | 直方图／峰值、MakeLUT、软件一维曲线 |
| Boke / RadialBlur | 此原厂库中的两个函数体均直接返回 `-1`，没有像素处理 |

`ADE_ProcessLut @0x5cc8` 按曲线结点生成 256 项一维映射后查输入字节，不是 9³ 3D LUT。Softfocus 的相应调用在 `0x89b8`，后续卷积调用在 `0x8aa8/0x8b24`，Mixer 在 `0x8be8`。这些地址是静态分支证据，具体走哪条仍取决于采样类型和图像状态。

原厂取景也不覆盖所有成片滤镜：`GetSmartFilterMode` 对 ZoomingShot、RandomMosaic、ColoredPencil、WaterColor、OilPainting、InkPainting 的对应 UI 编号返回关闭值 15；成片 dispatcher 有相应分支。另有 Animate/Uwha/Suchae/Sumuk 等别名编号进入可用取景映射。全部对应关系见 JSON，不能仅用显示名称推定预览能力。

负片、选色的完整处理、MnF 内部算法和所有间接调用还未展开。本轮没有把函数名或开关存在当成像素效果已验证。

## 用于改进本项目的结论

1. 原厂把明暗曲线、整体颜色和其他图像处理分开。复杂风格全部压入单张均匀 9³ 表，会让陡峭明暗变化占去较多精度。应先用离线模型量化固定表的下限，再评估曲线与 3D 分工的收益。
2. 保留源风格的暖灰、冷灰和分离色调。原厂 Retro 自身就改变灰轴，统一“去偏色”不能等同于恢复全部灰色中性。
3. 当前原厂矩阵随照明和其他状态生成。后续若复用原厂模块，应基于本次原厂参数组合，不能写死一次采样值。
4. 取景和成片共用风格定义，但原厂使用不同表尺度和执行路径。分开编译可以借鉴；逐项视觉等效仍要标定。
5. 卷积、暗角、空间混合不会因提高 3D LUT 拟合精度而出现。当前任务继续优化颜色映射，不把这些空间效果算作 LUT 编译器能力。

目前实际改动集中在电脑端编译和评测，保持原 4380 字节格式。未改变成片 Hook、Gamma、硬件路由或取景运行流程。
