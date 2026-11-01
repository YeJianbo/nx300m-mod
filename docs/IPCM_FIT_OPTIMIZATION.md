# IPCM 通用拟合优化

最新一轮已覆盖全库20款，并核对原生参数舍入、Gamma假设和灰阶冷暖方向。5款选择新候选，15款保留基线；NC尚未解决。代码、根因与全部对照见 [颜色模型审计](IPCM_COLOR_AUDIT.md) 和 [20款结果](../outputs/deployment/ipcm-preview/evaluation/tonal/comparison.md)。下文保留此前8款多起点试验的结果。

当前预览编译器为每份源 LUT 自动求参数，风格名称不参与计算。上一轮按 CC、Eterna 等分别展示误差，并不代表存在逐款手调配置。本轮继续改进共同算法，实验配置单独保存，默认候选包保持原样。

## 已实现

- RGB 代码值与 OKLab 明度、色度的联合目标，保留源 LUT 自己的灰阶和色偏。
- 覆盖 RGB 体积、等间隔色相／饱和度／明度和灰阶的共同采样，不使用照片训练，不给某款风格单独加权。
- 同时检查 25%、50%、75%、100%；参数强度的混合通过完整前向模型求值。
- 从原算法、单位变换、源 LUT 自动估计的前矩阵／后矩阵开始求解，减少局部极值对结果的影响。
- 33 节点到 65 节点的单调曲线细化，加入平滑约束；输出仍为原有 256 个 uint8 Tone 值，运行时开销和配置 ABI 不变。
- 增加平滑 L1 的 RGB 稳健求解，减小少数难拟合颜色对全部颜色的牵制。
- 用独立颜色样本比较量化后的候选，同时检查 RGB 体积中的均值、P95 和 OKLab 距离，避免中性区域或 HSV 样本掩盖其他区域退步。
- 修正求解器两处实现问题：停止在检查点之间时纳入最后的量化结果；黑色 LMS 下限内的导数与前向函数一致。

全部设置集中在 `tools/ipcm_preview_general.py`，函数只接收 cube 和基线参数，没有风格名分支。矩阵与曲线的求导已做方向有限差分核对，误差约 10⁻¹⁰；黑色下限的导数也已单独核对。

## 如何判断结果

新候选需要按同一规则胜出；保留原配置的结果照实记录为 `baseline-v2`。优化器不能靠少数风格、某张照片或某一项分数证明整体效果。

测试使用未参与拟合或选择的 seed=47629、32,000 个 RGB 点，加上灰阶及近灰样本。各强度、RGB 区域和感知距离均写入报告。另从相同源 LUT 当场生成 9³／10-bit 三维表，作为软件表示能力对照，不使用旧缓存代替源 LUT。

结果见 [通用拟合对照](../outputs/deployment/ipcm-preview/evaluation/general-fit.md) 和 [完整 JSON](../outputs/deployment/ipcm-preview/evaluation/general-fit.json)。候选参数保存在 `outputs/deployment/ipcm-preview/evaluation/general-candidates/profiles/`。

## 当前判断

本轮八款中六款仍选择原算法。NN 的灰阶误差下降，但 RGB 体积均值略增；Hard Mono 的改进只有极小量化误差。整体没有达到替换默认配置的质量，因此没有更新安装包。

增加迭代、多起点或更换误差目标，仍可能只是在不同颜色之间重新分配误差。当前矩阵 → 共享一维曲线 → 矩阵模型，不能表示任意三维 LUT 中随色相、饱和度和亮度共同变化的局部映射。更细的一维曲线也没有增加三维颜色寻址能力。这是当前拟合模型的限制，不能扩大为整个 IPCM 的硬件能力结论。

这些试验提供当前候选的误差，没有证明全局最优。原生 IPCM 的颜色域和处理顺序也仍待标定；软件模型与真实取景的差异需要另行测量。

这轮没有修改 IPCS、RAW、RAM Hook 或默认 SD 候选包。通用实验入口使用 `--method general`；默认仍为 `native-ycc`。后续静态逆向已确认原厂取景链具有尚未纳入模型的 PCSE 分区色相／饱和度控制，优先研究扩展统一模型，见 [IPCM 分区色彩控制调研](IPCM_PCSE_RESEARCH.md)。

```powershell
python -X utf8 tools/fit_ipcm_preview.py --all --method general --profiles-root outputs/deployment/ipcm-preview/evaluation/general-candidates/profiles --iterations 80
python -X utf8 tools/analyze_ipcm_general.py --profiles-root outputs/deployment/ipcm-preview/evaluation/general-candidates/profiles
```
