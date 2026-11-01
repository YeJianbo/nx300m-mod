# IPCM 分区色彩控制调研

## 结论

NX300M 1.15 的 IPCM 除了当前预览模型使用的矩阵和 Tone 曲线，还具有 PCSE（Primary Color Saturation Enhancement）分区色相／饱和度控制。它有 SDK 声明、UDD 实现、原厂 IQ 调用和取景参数节点，值得作为下一版通用拟合模型的首选扩展。

之前的拟合试验只能说明“矩阵 → 共享曲线 → 矩阵”这套模型改善有限，不能据此认定整个 IPCM 的能力已经用尽。PCSE现已接入运行时与响应驱动的通用联合拟合器，实际能降低多少偏差需要取得机身响应后测量，见 [实现与采样](IPCM_PCSE.md)。

本调研的证据来自静态逆向，没有运行机身参数修改。后续实现已经通过ARM参数通路检查。可复现提取器为 [inspect_ipcm_pcse.py](../tools/inspect_ipcm_pcse.py)，结果为 [JSON](../outputs/metol/ipcm-pcse.json) 和 [原厂反汇编](../outputs/metol/ipcm-pcse.asm)。地址均为对应原厂 ELF 的虚拟地址，运行时还需要加库加载基址。

## 已找到的能力

| 模块 | 已确认内容 | 对通用拟合的意义 |
|---|---|---|
| PCSE Primary | 12 个命名颜色参数，各有 hue、saturation 两个 selector | 可以分别表达不同颜色的变化，补足全局矩阵的局限 |
| PCSE Global | 全局 hue、saturation 参数 | 与分区调整共同求解，不能把寄存器位宽直接当像素增益单位 |
| PCSE Gray Protection | Primary／General 各有 min、max 阈值 | 控制近灰区域的保护范围；具体响应函数未取得 |
| PCSE Skin Protection | 色相、饱和度范围；另有 skin enhancement 参数 | 可用于约束肤色，但必须先确认开关和实际权重函数 |
| Chroma LUT | Orange、Cyan 两个 selector，各复制 64 字节 | 是另一条原生色彩路径；目前没有证据把它解释成小型通用 3D LUT |

声明来自 `work/vendor-source/TIZEN/project/NX300M/imagedev/usr/include/drime4/udd/ipcm_submodule_type.h:178` 及 `ipcm_submodule.h:47`。12 个字段的**结构体顺序**是：

```text
r, g, b, c, m, y, o, cg, v, yg, cb, p
红、绿、蓝、青、品红、黄、橙、青绿、紫罗兰、黄绿、青蓝、紫
```

字段顺序不是色相环顺序，也不能由名称推出每区恰好占 HSV 的 30°。

## 实际调用链

```text
CIpcLiveviewParamD4 构造取景参数列表
  → 构造 CIpcmPcseD4，加入参数列表
  → 参数列表 UpdateParameter
  → CIpcParameterNode::Update
      → 虚调用 CIpcmPcseD4::GetIndex
      → 与缓存索引不同才虚调用 SetParam
  → CIpcmPcseD4::SetParam
      → ipcm_pcse_sp_param                 ×2
      → ipcm_pcse_gray_th_param            ×2
      → ipcm_pcse_general_gain_param       ×2
      → ipcm_pcse_primary_gain_param       ×2
  → libudd 的参数检查与原生寄存器打包
```

| 证据 | 地址 |
|---|---|
| 取景列表构造 PCSE 节点 | IQ `0x3407a0` |
| 将该节点加入取景参数列表 | IQ `0x3407ac` |
| 通用 Update 的 GetIndex／索引比较／SetParam 分派 | IQ `0x350dc0–0x350e08` |
| PCSE GetIndex | IQ `0x34114c` |
| PCSE SetParam | IQ `0x3411c0` |
| 两次 Primary setter 调用 | IQ `0x341448`、`0x341454` |
| Primary setter 实现 | UDD `0x4a1b8` |
| PCSE Reset | IQ `0x341470` |

这条链证明原厂取景配置中确实接入了 PCSE。不同拍摄模式中是否实际启用、是否在每次需要时更新，仍需运行日志和像素输出确认。

### 参数不是任意 3D 表

`ipcm_pcse_primary_gain` 为 52 字节：selector 4 字节，12 个参数各 4 字节。selector 0 为 PHC，selector 1 为 PSC。UDD 把两个参数分别取低 13 位，放入一个 32 位寄存器字的 bit0 和 bit16。

原厂入口实际接受按 int32 解释的 −4095…4095，拒绝 −4096；负数传入的是完整 32 位补码。参数越界返回 2。位宽没有给出角度单位、饱和度倍率或 Q 格式，不能直接当 Q12 使用。

这些是 CPU 写入参数的编码规则。硬件内部怎样由像素算出色相、如何在相邻颜色间插值、何时裁剪，UDD setter 没有实现这些运算，因此仅反汇编 setter 无法得到完整前向像素模型。

### 原厂确实有不同颜色的非零配置

取景 `m_uiPrimaryGain` 位于 IQ `0x36e8f8`，144 字节，3 行 × 12 个 uint32：

```text
行0: 0 0    0   0 0   0 0 0 0    0    0 0
行1: 0 0    0 464 0   0 0 0 0    0 1000 0
行2: 0 1120 0   0 0 800 0 0 0 1600    0 0
```

`SetParam` 通过 GOT `0x3c3770` 引用这张表；提取器重新计算 PIC 地址并核对 ELF relocation，未只按相邻内存或符号名称猜测来源。

当前原厂 `GetIndex` 对输入 `+0x58` 的数值 13、14 分别选择行1、行2，其他数值选择行0；13／14 时的第二个索引取自输入 `+0x5c`。这两个数值对应的用户界面效果名尚未核实。

`SetParam` 向 selector0 发送 12 个零，向 selector1 发送 `表中值 × (第二索引+1) / 5` 的无符号整数结果。这里的除5来自 `UMULL 0xcccccccd` 与右移，不是推测的硬件增益公式。原厂 SetParam 没有检查每次底层 setter 的返回值，不能用它的返回成功作为生效凭据。

## 开关、缓存与恢复

原厂节点 Reset 除了使缓存索引失效，还通过 `CapIfDspSet` 发送 `0x30031…0x30038`，值为 `1,0,0,0,1,0,0,0`。桥接链已找到：

```text
IQ CIpcmPcseD4::Reset (0x341470)
  → Capture CapIfDspSet (0x1beb20)
  → CapIfDspParamSetIPCm (0x166cf4)
  → ipcm_set_pcse_sw (0x185a58)
  → UDD ipcm_liveview_pcse_on/off
```

`0x30031…0x30037` 对应 SDK selector0…6。但 `0x30038` 在原厂桥接代码 `0x185b30` 处设为 selector3，并没有设为 SDK 定义的 skin enhancement selector7。这个差异已经记录，不能假设八个桥接 ID 与 SDK 开关全部一一对应。

接入预览模块时需要解决两件事：

1. **切换真的触发下发。** 原厂 Update 只有索引变化才调用 SetParam。预览 generation 改变时，要让 PCSE 节点重新接收当前原厂参数，否则只更换配置不会立即改变取景。
2. **参数与开关一起恢复。** 保存本轮原厂输入及开关状态。不要恢复启动时快照，也不要只恢复12个增益却留下改变过的保护开关。选择原样后，应等原厂输入实际转交再报告恢复。

PCSE现已作为第三个原厂节点纳入同一预览管理器的generation和恢复机制。运行时升级为ABI3，新增v3磁盘配置456字节，兼容v1／v2配置；IPCS成片模块的196字节结构保持不变。

## 下一步怎么做

优先把剩余工作分为硬件模型确认和统一拟合两步。

**先确认一次机型级响应。** 固定测试图、白平衡与曝光，取得原始取景帧；逐项改变小幅 Primary hue／saturation，记录相邻颜色、近灰区域和不同亮度下的输出，再恢复本轮原厂配置。由此确定参数单位、分区权重、保护函数与组合顺序。优先利用已有帧获取能力，先查清是否会触碰 DMA 所有权；此次调研没有实现新的帧拦截。

**再给所有 LUT 使用同一个编译器。** 在模型已确认的前提下，共同求解 CCM、Tone、YCC 与24个 Primary 参数，增加相邻分区平滑约束。源 LUT 决定目标值，风格名不参与算法。硬件响应标定按 NX300M／固件模型复用，不需要每款胶片单独适配。

比较时继续使用独立颜色样本及25／50／75／100%强度，对照现有基线，检查平均误差、P95、灰阶与蓝青过渡。先看扩展模型是否确实减小剩余偏差，再决定是否打包为默认版本。分区模型仍不能完整表示任意依赖色相、饱和度、亮度的三维变化。

当前模拟器可以执行原厂参数检查、打包、缓存失效和恢复代码，**不会模拟 PCSE 的像素运算**。把一种自行假设的 HSV 分区公式接进视频评估，只能研究理想模型的表现，不能用来确认真实 LCD 效果。

若 PCSE 的实际响应仍不能达到需要的接近程度，再推进已有原厂 SmartFilter／IPCS 三维预览路径的调研；其缓冲区所有权和同步问题尚未解决。当前证据足以优先研究 PCSE，无须先更改成片 Hook 或扩大到显示 DMA 改造。

## 复现

```powershell
python -X utf8 tools/inspect_ipcm_pcse.py
```

脚本读取 `project.local.json` 指定的原厂1.15库，生成符号、直接调用点、常量表、经核对的表绑定及反汇编。直接调用扫描是静态线性扫描；虚调用链通过参数节点分派和取景列表构造另行审阅，不把扫描结果当实机调用记录。
