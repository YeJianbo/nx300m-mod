# IPCM PCSE 实现与采样

PCSE 已接入预览 RAM 模块，运行时 ABI3。它对原厂 Primary 参数增加12个色相、12个饱和度偏移，支持强度、generation 切换与分别恢复。现有8款风格仍使用原来的 v1／v2 参数；目前没有取得真实 PCSE 响应数据，尚未生成改善颜色的 PCSE 风格配置。

## 运行时

新增两个入口：IQ 的 `ipcm_pcse_primary_gain_param` GOT 槽 `0x3c3088`，以及 `CIpcmPcseD4` 专属 Update 虚表槽 `0x3c0078`。总计7个槽；现有成片 LUT 的 Hook 与196字节配置不变。

每次原厂调用携带本轮参数，预览模块计算：

```text
result[selector][i] = stock[selector][i]
                    + round_away(delta[selector][i] * strength / 100)
```

偏移的单位就是原厂接口的整数单位，没有按角度或 Q12 解释。最终参数必须在 −4095…4095；其中一项超限，就把这个 selector 的全部12项转交原厂输入。0%或全零偏移直接保留原厂指针。模块不改变 PCSE 开关、灰阶保护、肤色保护、WB、ISP寄存器配置或显示缓冲区。

Color、Gamma、PCSE 三个节点分别记录 generation。只调用原厂基础 Reset 使索引失效，不调用会改变开关的 PCSE 专属 Reset。PHC、PSC 各自记录 pending；一组恢复不能代替另一组。v3切回v1／v2时，两组参数仍需收到真实原厂输入，才能清除恢复状态。

v3磁盘配置456字节：沿用v2的360字节内容，追加PHC12、PSC12，共24个int32。新运行时读取v1／v2时把追加内容置零。状态数组依次为 CCM、Tone、CHM、YCC、PHC、PSC。

**旧运行时升级：** ABI3不能接管仍在运行的ABI2。先用匹配的旧 loader 和 blob 执行 stock／restore，确认卸载，再让目标相机进程真正重启。旧配对文件保存在电脑的 `outputs/deployment/ipcm-preview/previous-runtime/abi2/`。普通关机可能恢复快照，不能只按关机判断进程已退出。

## ARM 模拟结果

`outputs/deployment/ipcm-preview/arm-vm/report.json` 已通过28轮参数提交比较，包括：

- 原厂 IQ Update／Reset、原厂 UDD 参数检查和打包执行；GetIndex／SetParam 为夹具函数。
- 0／50／100%强度；PHC／PSC共24项；正负数与舍入。
- 同一输入重复更新不累加，原厂输入改变后以新参数重新计算。
- 最终参数越界时整组回退；原始输入不被改写。
- v3切回v2时逐组恢复；选择原样后逐组恢复，未完成时保留挂钩。
- v1／v2原有行为、7个槽的只读页权限、线程与tracer恢复、IPCS哨兵区保持。

环境含机身导出的MFW `libmisc.so`、原厂1.15 IQ／UDD及当前构建，寄存器以普通RAM替代。它不执行PCSE像素运算、ISP DMA或真实LCD输出。

## 采样配置

候选包增加 `calibration/pcse/`，共有49份v3配置：

- `baseline.bin`：单位矩阵、单位Tone、PCSE零偏移。
- `phc-r-plus.bin` 等24份色相正／负参数。
- `psc-r-plus.bin` 等24份饱和度正／负参数。

顺序为 `r,g,b,c,m,y,o,cg,v,yg,cb,p`，默认步长8个原生整数单位。名称只对应接口字段，不表示已经确认的色相角位置。采样幅度过小可能测不到明显变化，需先检查真实帧差再决定是否增加。

在相机已有 shell 中，使用普通2D P／A／S／M，固定场景、曝光与白平衡，关闭特殊效果：

```sh
sh /mnt/mmc/nx300m-mod/ipcm-preview.sh probe baseline
sh /mnt/mmc/nx300m-mod/ipcm-preview.sh probe phc-r-plus
sh /mnt/mmc/nx300m-mod/ipcm-preview.sh probe phc-r-minus
sh /mnt/mmc/nx300m-mod/ipcm-preview.sh stock
# 返回原厂取景，等待正常参数更新后，再执行 restore。
sh /mnt/mmc/nx300m-mod/ipcm-preview.sh restore
```

这些命令只切换参数，不会自动获取帧。先验证基线与恢复，再做完整采样；不要把模拟器的RAM寄存器dump当成取景图像，也不要用手机拍LCD作为定量输入。

## 通用响应模型与联合拟合

`tools/ipcm_pcse.py` 提供 `probes`、`calibrate`、`fit`。所有风格使用同一套算法，不按名称分支。

响应模型使用实测的正负参数变化求局部导数，并在RGB输出坐标上拟合平滑的张量Bernstein基底。它不预设等间隔HSV分区、不猜硬件增益单位。运行时只读编译后的24个整数，采样模型不装入相机，也不逐帧计算。

标定输入NPZ，均使用同一明确编码、归一化至0…1的RGB：

| 字段 | 形状 | 内容 |
|---|---|---|
| baseline_before | N×3 | 变化前原厂基线样本 |
| baseline_after | N×3 | 恢复后相同位置的基线样本 |
| plus_rgb | 24×N×3 | 逐参数正向变化的匹配样本 |
| minus_rgb | 24×N×3 | 逐参数负向变化的匹配样本 |
| step | 24 | 各参数使用的原生整数步长 |
| metadata | JSON字符串 | camera=NX300M、firmware=1.15、source_kind=camera或synthetic，以及采样场景、编码、模式、WB与曝光记录 |

默认5阶模型需要至少864个匹配样本。基线平均漂移超过1个RGB代码值，或P95超过2个代码值时拒绝建模。报告另列响应的训练外误差和正负响应中点的非线性偏差。这些检查不代替对采样期间场景移动、闪烁和参数变化的排查。

响应模型是原厂输出附近的局部近似；默认限制 `sum(abs(delta)/step)<=1`，避免把单参数小幅采样外推到任意组合。CCM／Tone变更后的组合次序及多参数相互作用，仍需独立帧测量验证。模型元数据一直保留 `native_model_verified=false`，不从“文件由机身采样”自动推断完整ISP模型已确认。

联合拟合同时求解CCM、Tone、YCC和24个归一化PCSE偏移；包含单调曲线、平滑约束、感知／RGB共同目标和25／50／75／100%强度。独立选型样本比较量化后的候选，检查每档强度的RGB均值与P95；不胜出时保留原配置。原有默认风格目录不会被覆盖。

```powershell
python -X utf8 tools/ipcm_pcse.py calibrate captures.npz --output outputs/deployment/ipcm-preview/pcse-response.npz
python -X utf8 tools/ipcm_pcse.py fit --response outputs/deployment/ipcm-preview/pcse-response.npz --all
```

合成数据检查入口为 `tools/probe_ipcm_pcse_fit.py`。它验证响应学习、联合导数、求解与v3读写；没有模拟NX300M硬件颜色。其报告在 `pcse-fit-fixture/report.json`，不能用于评价GR／富士风格的实机误差。

当前交付已完成参数运行时、CPU编译器、ARM验证和采样配置。接下来的缺口是取得机身响应和匹配取景帧，再自动拟合8款风格并比较实际效果。
