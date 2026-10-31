# NX300M LUT 编码确认与实机校色

当前优先级是验证免刷加载和原厂一致性，再确定原生坐标，最后校色与拟合风格。不为这一步新增刷机。完整 ARM VM 的写表数值验证不代表真实 ISP 色彩正确。

## 已确认和待确认

- NX300M 的 still LUT 是三路 9³、每路 730 个 uint16（729 节点加填充），数值范围 0～1023。通道 0 对应慢轴、通道 2 对应快轴。
- 原厂静态表不是 identity；另一分支生成单位表。模块在每次 Update 中重新读取选择，通道 0 判断本次原厂来源，不缓存跨 Update 的风格转换表；LUT 节点缓存每次单独失效。因此当前不需要再给一个不存在的转换缓存扩充键。
- 实际输入／输出的颜色域仍未确认。`m_sRGBLut` 命名、三通道和网格布局不能证明标准 sRGB，更不能证明 NX500 的 CrCbY。
- 当前上下文是按 Linux TID 分配的固定槽，不是 libc TLS。写表只接受同线程的 Update 上下文；未匹配调用原样透传，可在诊断中单独记录。它不会把上下文自动传给异步线程。
- 常规 still 调用链已有原厂操作信号量证据；这不等于穷尽所有路径，也不等于 ISP 帧原子提交。模块不额外引入跨原厂调用的锁。

## 0% 与诊断

0% 时直接调用原厂写表函数并传入原始 source 指针，不生成 identity、不混合，也不读目标表。50% 是原生表数值的四舍五入均值，不宣称等同于视觉效果的一半。

在已有 shell 中显式启用诊断：

```sh
touch /var/run/nx300m-lut-trace-enabled
```

每个 Update 线程的最近一次记录为 `/var/run/nx300m-lut-trace-<tid>.json`。包括入口／返回阶段、Update TID、三个 writer TID、实际通道顺序、factory/unity 来源、选择代次／路径／强度、原始与转发指针。0% 的三组指针应逐项相同，正常同步路径的 writer TID 应与 Update TID 一致，通道顺序为 `[0,1,2]`。

没有匹配上下文的写表调用记录为 `/var/run/nx300m-lut-unmatched-<tid>.json`，原始指针仍直接透传。接入瞬间的原厂在途调用也可能没有上下文，不能仅凭这份记录认定存在异步线程。若出现跨线程或缺通道，先核对实际调用链，不把某次透传当作 LUT 生效。移除 `nx300m-lut-trace-enabled` 只关闭后续诊断，不卸载模块。诊断文件写 RAM，不写 SD；旧记录保留，不代表当前拍摄。

## 单独的原生坐标测试表

`tools/build_lut_diagnostics.py` 生成五组测试表，输出到 `outputs/deployment/lut-diagnostics/`，不修改主 `looks/`。可选诊断增量包使用：

```powershell
python -X utf8 tools/build_lut_mod.py --include-diagnostics
```

普通包仍为 19 项，诊断包另含以下 5 项，在菜单中标为诊断项：

| 探针 | 原生数值操作 | 检查目的 |
|---|---|---|
| identity | 三通道保持不变 | 组合表应与原厂表逐字一致；检查额外映射是否为零 |
| equal-channels | 三通道设为其整数平均值 | 检验 RGB 假设；只有确认 RGB 坐标后才能称为黑白 |
| axis0-shift | 通道 0 加 64，截到 1023 | 判断通道 0 实际控制方向 |
| axis1-shift | 通道 1 加 64，截到 1023 | 判断通道 1 实际控制方向 |
| axis2-shift | 通道 2 加 64，截到 1023 | 判断通道 2 实际控制方向 |

两种表来源都生成对应版本：直接表为 F(identity grid)，组合表为 F(factory)。这些是数值探针，未用未知的颜色模型预测具体色相。

## 最小实机次序

1. 固定场景与光线，M 挡、固定 ISO／快门／光圈／手动白平衡，关闭自动风格与动态范围调整，保存 RAW+JPEG。曝光参数按实际光照选定，不预设某张参考图的亮度。
2. 先拍原厂基线、Mod 0%、identity 100%。核对诊断中的 source 分支、指针与通道顺序，再看 JPEG 的灰阶及色块。正常图像波动与有损编码不要求整张 JPEG 字节相同。
3. 一致性正常后，再拍 equal-channels 和三个单轴探针；记录已知物体颜色／灰阶的变化。不要用复杂胶片 LUT 代替编码诊断。
4. 清选择并拍原厂恢复照，然后停用模块；核对调用槽、重复加载和机身响应。生产缓存链与旧内核行为仍须实机验证。
5. 之后再用固定曝光／白平衡的色卡照片建立输入域与输出域模型。色卡版本、光源、参考白点、ICC 和色适应必须一致；标准还原与目标风格拟合分别评价，不以风格图相对标准色卡的 ΔE 越低越好。

目前没有 NX300M 色卡测量结果，不生成 ΔE 或理光校准结论。已有 `cube_to_d4.py` 能做网格／量化及原厂表组合，缺少的是经实测确认的颜色域转换；不能将格式转换器等同于完成校色的通用 `.cube` 编译器。

## NX500 Metol 参考的准确范围

本地 Metol 来源固定在 `ddbe8b119e046798e557add13837834f5e8d90ac`（2026-10-18），不是最新 nxwa。核验时的上游提交为 `cb82f4886c1174261cbba8127000c7b696944ff4`；其 10/25 修复记录确认 17³ 个 `{Cr,Cb,Y,0}` 项、19712 字节槽，末尾为末项重复三次加 48 零字节。BT.601 full-range 是工作模型，边界采样使用线性外推，不能照抄旧 clamp 转换器。

- [NX500 实拍修复报告](https://github.com/WApaperplane/metol/blob/cb82f4886c1174261cbba8127000c7b696944ff4/docs/current/LUT_CRCBY_ONDEVICE_FIX_2026-10-25.md)
- [机内转换器修复](https://github.com/WApaperplane/metol/blob/cb82f4886c1174261cbba8127000c7b696944ff4/docs/current/CUBE2LUT_NATIVE_FIX_2026-10-25.md)
- [native_codec.py](https://github.com/WApaperplane/metol/blob/cb82f4886c1174261cbba8127000c7b696944ff4/tools/lutgen/native_codec.py)

上游报告描述 NX500 v1.12 的十张 JPEG 与 ROI 对照；该提交未附原始照片和测量产物，本轮未独立复算其像素。本地 `metol_slot.py` 只解码旧 RGBX／零尾部格式，不支持新版 CrCbY。现有六项 `metol-*` 来自独立 filmsim 的标准 RGB cube，并非新版原生 bin，不能据此认定本地 19 项都要按 CrCbY 重编码。
