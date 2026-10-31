# Metol 七维配方在 NX300M 上的适配边界

## 上游配方含义

Metol 上游把 Picture Wizard 配方保存为 `R_COLOR、G_COLOR、B_COLOR、HUE、SATURATION、SHARPNESS、CONTRAST` 七个整数。RGB 中性值为 100，另四项中性值为 10；校验范围分别为 RGB 0–200、其他四项 0–20。它们是上游离散控制值，不是物理单位。SAT、SHARPNESS、CONTRAST 的 0–15 只用于上游预览图表，不是配方值上限。白平衡不包含在七维配方中。

来源：上游 `work/reference/metol/scripts/filmlab/recipes.json`、`test_server/nxui/nxui_model.c`、`scripts/filmlab.sh` 和 `test_server/filmlab/src/nxflab.c`。当前 `tools/build_metol.py` 将七个原始值写入 `outputs/metol/recipes.txt`；普通配方面板、网页和 CLI 共用七维批次桥。LUT 模式仍把风格 key 交给独立 LUT engine。

## 统一七维入口

当前68项配方可通过网页“设置 → 配方…”、原生配方面板或 `python tools/recipe_style.py --apply portra400` 应用；`--reset` 恢复原厂。HTTP目录为 `GET /api/v1/color/recipes`，状态及应用为 `/api/v1/color/recipe`，POST接受 `{"id":"portra400"}` 或 `{"reset":true}`。

`runtime/recipe_style.h` 先检查全部七值和当前模式，再在同一锁内提交四阶段设置，清除额外自定义RGB行增益。四阶段在下一次真实原厂Update执行，配置提交不保证同一物理帧生效。预览与拍摄分别报告可用、应用和错误；预览四阶段完成即可返回，拍摄链未出现时保持未应用。单项控制即使设置同值也会使旧批次失效，回读显示当前控制值。

原厂程序模拟会话中已完成Portra400应用、同值单项修改、重新应用、非法末字段整批拒绝和恢复检查，记录见 `outputs/metol/recipe-session.json`。网页实际应用及恢复通过；原生面板通过遥控OK应用并关闭，日志记录 `preview_mask=15 capture_mask=0`。两组针对Gamma真实调用归属、严格chroma模式限制的夹具结果见 `recipe-runtime-state-probe.json`。

这些结果验证参数链与入口整合。当前照片回放位于ISP输出后，不能据此确认配方像素效果；拍摄链和实体NX300M仍待验证。设置仅保留当前会话，不写闪存。

## 原厂 NX300M 控制与七维字段

| Metol 字段 | NX300M 原厂路径 | 适配判断 |
|---|---|---|
| `R_COLOR` / `G_COLOR` / `B_COLOR` | 未发现三个独立的 PW 通道变量。色彩校正节点内部会生成并转发完整 3×3 矩阵，见下文。 | 原厂界面和参数服务没有对应的三个独立滑杆；本项目新增 RGB 行增益桥，属于自定义扩展，不是原厂控件，也不代表完整 Metol 配方。 |
| `HUE` | `PWCOLOR`（变量 ID `0x117`，IQ 参数 `0x2d`）选择并插值 14 个原厂 3×3 `m_iColorTone` 矩阵，含交叉通道系数和中性轴变化。YCC 路径另有有符号 2×2 色度矩阵。 | 已提取 NX500 原厂 21 张 HUE 表，并在最终色度矩阵 setter 接入；不通过 PWCOLOR 近似。完整支持模式已按源处理顺序接入，预览参数读写通过；像素效果待验证。 |
| `SATURATION` | `PWSATURATION`（`0x118` → IQ 参数 `0x2e`）由 `CIpcIfInfo::GetSaturation` 解码。`CIpcsColorD4` 暴露 9 档等比例 U/V 增益：80、90、100、112、128、140、160、176、192，128 为中性。 | 已接入源端 0–20 SAT 算术，在标准／Custom1 P/A/S/M、2D、原厂中性 CHM 下改写完整 YCC；不线性映射为 −4…+4。预览 setter／复位通过，像素待验证。 |
| `SHARPNESS` | `PWSHARPNESS`（`0x119` → IQ 参数 `0x2f`），由原厂 Edge 节点消费。 | 已在原厂计算前接入源21档Q8因子，保留NX300M基准和后续分支。预览设置与恢复通过；源六字段空间算子和像素效果尚未等效验证。逐点RGB LUT不能表达这一维。 |
| `CONTRAST` | `PWCONTRAST`（`0x11a` → IQ 参数 `0x30`），由原厂 Gamma／tone curve 路径消费。 | 已提取源端 21 档；其中 9 档七点表与 NX300M 拍摄端逐字相同。源21档已接入原厂 Contrast Gamma 阶段；预览采用显式8↔10位量化，后续仍使用目标原厂处理。参数和曲线检查通过，成像待验证。 |

原厂 PW 控制的取值和运行边界见 [HARDWARE.md](HARDWARE.md#原厂画面参数控制)。其中 `PWCOLOR` 的角度范围为 −1（关闭）或 0–359；原厂将角度变成离散矩阵索引后再插值。它不是独立 RGB 增益，也不是标准 HUE 旋转。相关解码数据在 `outputs/metol/pw-color-model.json`。

## RGB 行增益扩展

`runtime/color_style.c` 拦截静态照片 `ipcs_color_correction_param` 与取景 `ipcm_color_correction_param`，保存每次原厂刚生成的 `col_mat[9]`，然后把处理后的矩阵转交给原厂 setter。模块通过原厂 Ecore 定时器读写 `/var/run/nx300m-color-style-{request,response,ready}`；ready 文件包含 guest PID。`tools/color_style.py` 提供 set、reset、status API。关闭／复位会把最近一份原厂矩阵重新交给原厂 setter；它不改原厂 AWB/WB 状态，也不写持久偏好。

矩阵 ABI 和算术依据来自核验过的 NX300M 1.15 原厂文件：`ipcs_submodule_type.h`／`ipcm_submodule_type.h` 定义九个 32 位 `col_mat`；原厂 `CIpcMath::MatrixMult3x3` 对行主序矩阵计算 `A×B`。色彩节点乘PSS／ColorTone时传入分母256，整数除法向零截断；这一分母不代表输入基准或最终CCM的单位值就是256。目标基准由 `EncodeLMWtoD4COLMAT` 编码，初始每行和为128。原厂 UDD setter 将系数限制在 −1024…1023，并按 11 位有符号值打包。证据、隔离 ARM 探针和专属模块构建见 `outputs/metol/color-style-probe.json`、`color-style-matrix-math.asm`、`color-style-ipcs-abi.asm` 与 `color-style-ipcm-abi.asm`；早期基准顺序见 `outputs/metol/rgb-target.json/.asm`。

当前扩展把 R/G/B 定义为结果矩阵三行的增益：`M' = diag(R,G,B) × M`；百分比范围为 0–200，100 为中性。保留原厂系数尺度并按百分比四舍五入；若任何系数会超出原厂范围，整张原厂矩阵直通并计入 bypass，避免部分通道或溢出值。三项均为 100 时，直接把同一指针交给原厂 setter，保证中性路径无矩阵重算。

隔离探针包含两部分。数学探针调用未修改 NX300M 1.15 `CIpcMath::MatrixMult3x3`，确认行主序乘法、`scale=256` 和负数向零截断。桥接探针加载实际交付的 `color-style.so`，用 Ecore 调度替身取得并执行模块的请求回调，再调用未修改 `libudd.so.3.1` 的两个 setter；仅将 `ipcs_reg_base` 和 `ipcm_reg_core_base` 重定向到独立分配的合成 RAM。它对全部 RAM 字节核对中性直通、三行增益、重复调用不累积、新原厂 base、正负越界整表回退、恢复最近 base 以及复位后的中性路径，两个 setter 均通过。运行结果见 `outputs/metol/color-style-bridge-probe.stdout.log` 和 `color-style-probe.json` 的 `bridge_probe`。

该探针没有启动相机、打开传感器或替换原厂库；Ecore ready 和请求文件在独立 mount namespace 的临时 `/var/run` 内执行。随后在原厂程序模拟会话完成了多模块 Ecore 链、读写与恢复检查，见文末记录；传感器像素、RGB 屏幕效果和 JPEG 成像尚未验证。

### 源配方 RGB 与自定义行增益的差异

NX500配方的三个RGB值不能直接传给上述自定义桥。源编码为 `floor(255*value/200)` 放入raw高12位；源消费者右移20位直接取出code，没有再次归一化。普通配方100对应code127，200对应255；原厂绕过用的sentinel对应128。三份基准矩阵按输入列分别乘R/G/B code，每项执行 `low16(ASR7(signed16(coefficient)*code+64))`，不做饱和裁剪。code256也被消费者接受，但普通0…200配方编码不会产生它。恢复原厂必须关闭该阶段，不能用RGB100充当恢复。

`tools/inspect_nx500_rgb.py` 从原字节追踪两builder的54个系数存储来源，确认三份基准为Q7完整CCM。SAT/HUE处理独立的R2Y矩阵。ColorD5最终另将两份CCM右乘原表F并以SDIV128向零取整，随后取低11位打包；F的物理通道域仍未知，不能把这一输出直接写给NX300M。

`runtime/nx500_rgb_math.h` 实现原列缩放、配方编码和最终 `M*F/128`，复用已有有符号移位／low16规则。`tools/probe_nx500_rgb.py` 从已核验P7读取未修改的MUL、ADD、ASR、STRH原指令，以及最终矩阵乘法叶子和F原表。975组矩阵的8,775项缩放系数、另8,775项最终CCM系数均与C移植一致，包含0…256的全部code、全部201个配方编码、非对称通道、signed16边界、负值取整、原位操作及非法code不写输出；构建和运行退出0。证据为 `outputs/metol/nx500-rgb-probe.json`。

`tools/inspect_rgb_target.py` 已确认目标的完整基准getter为 `_Z16GetRgb2RgbMatrixv`：返回 `CColorProcess+0x10` 的signed16九项，两处ColorD4消费者立即逐项复制到各自节点，随后分别计算 `PSS*B/256` 和 `ColorTone*result/256`。两次向零取整使最终setter处再作列缩放不能视为等效。更早的LMW只存六个非对角项，先插值／暗部补偿再编码成行和128的完整矩阵；不能直接把六项当作三份源3×3基准缩放。

目标Update同步调用GetIndex与SetParam，getter和最终setter位于同一调用链。Reset使旧index为全1，后续GetRgb2RgbIndex的变化标记可触发矩阵未变情况下的重写。`runtime/source_rgb_style.h` 已在两个ColorD4的Update范围内建立线程局部上下文，仅在此范围包装完整基准getter，返回独立九项暂存；进入／退出时保存和恢复前一层上下文，保留原矩阵及其他调用路径。导出可替换性、两处PLT调用、vtable重定位和调用链记录见 `outputs/metol/rgb-target.json/.asm`。目标选基准／插值先于这一位置，仍不能宣称整个源builder逐位等效。

### 源 RGB 原厂阶段控制

源RGB值范围0…200，`set 100 100 100`仍启用code127列缩放，`reset`才关闭。配置或模式资格变化通过原Reset请求重算，支持标准／Custom1、P/A/S/M、2D。原输出行增益是独立扩展，继续作用于源RGB/PSS/ColorTone生成的矩阵；它的缓存记录所属源配置，后续重放仍能识别实际配置代次。

```powershell
python tools/source_rgb_style.py --set 108 100 93
python tools/source_rgb_style.py
python tools/chroma_style.py --status --context
python tools/source_rgb_style.py --reset
```

CLI与锐度／对比度共用 `tools/native_style.py` 的请求身份和代次等待。原生HTTP接口为 `GET /api/v1/color/source-rgb`，POST `{"red":108,"green":100,"blue":93}` 或 `{"reset":true}`；能力为 `source_rgb_style`。当前未增加独立网页RGB面板，保留整套配方入口的统一接入位置。

14字段协议依次为request_id、result、R/G/B、enabled、generation、capture_generation、preview_generation、capture_modified、preview_modified、capture_error、preview_error、guest_pid。响应代次和状态来自实际setter的同一把锁内登记，Update完成顺序不覆盖真实写入顺序。未写入或失败不算应用完成；已调用过的preview ready保持可用，错误／未知归属通过返回码暴露。错误−124表示写入归属未知，不会被客户端判为已应用。

`source_rgb_observer` 分开保留最近有效Update的getter输入／输出、setter输入和 `hardware_*` 写入归属，另有 `last_update_*` 记录空闲更新。缓存重放不伪造新的getter观察。`hardware_*` 是软件setter边界记录，不是物理ISP读回。

`tools/probe_source_rgb_state.py` 直接包含生产头文件，以显式getter／Update／返回码夹具检查8个状态案例：100仍启用、非对称通道与空闲更新、写入失败、带归属缓存重放、真正恢复成功、模式切换、无归属写入、嵌套及确定性的双线程完成乱序。首次构建与运行均退出0、stderr为空，记录为 `outputs/metol/source-rgb-state-probe.json`；没有把夹具当作原厂函数或像素验证。

实际原厂程序已完成100/100/100→200/80/120→重复更新→原厂ColorTone30→自定义行增益缓存重放→恢复。基准保持 `[191,-54,-9,-12,164,-24,4,-44,168]`；100时输出 `[190,-54,-9,-12,163,-24,4,-44,167]`；200/80/120时输出 `[381,-43,-11,-24,131,-29,8,-35,201]`。恢复后getter输出再次等于原基准，真实preview代次匹配、错误0；HTTP另完成108/100/93及reset。记录为 `outputs/metol/source-rgb-session.json`。原厂ColorTone和自定义行增益均已恢复原设置，源RGB关闭。拍摄端尚无实际调用，当前ISP后照片回放不验证RGB成像。

普通配方仍为浏览模式，不会先写其他四项再把整套配方标为成功。后续统一批次必须使用源RGB适配；现有输出行增益控件保留其自定义语义。

## 色彩节点能做什么

NX300M 1.15 的 `CIpcsColorD4::GetIndex` 会组合当前 WB、色彩矩阵和 RGB→YCC 参数。其 `SetRgb2Rgb` 从原厂矩阵与 ColorTone 表计算 3×3 矩阵，再依次交给 `CAWB::SetCc`、`CIQParamInfo::SetColorMatrix` 和 `ipcs_color_correction_param`。预览侧 `CIpcmColorD4::SetRgb2Rgb` 也计算 3×3 矩阵并转发 `ipcm_color_correction_param`。`SetWb` 使用 AWB/WB 引擎提供的增益与偏移；把它改作风格 RGB 增益会改变白平衡状态，因此不应混作七维配方字段。

该 RGB 桥只改变传入色彩校正 setter 的矩阵，不替换原厂矩阵表，因此每次调用都以新的场景/WB base 计算，避免累计缩放。当前仍没有像素样张验证色彩节点与 LUT 的先后顺序，也没有确认静态照片和预览效果一致；这些是成像验证边界，不是矩阵 ABI 的推测。

原厂 YCC 路径的饱和度表对 2×2 色度矩阵同倍率缩放。进一步检查 `SetRgb2Ycc` 后确认，subindex 6/7 的两个数值乘以 2 后分别加到 `ycc_mat[5]` 与 `ycc_mat[6]`，属于 RGB→YCC 矩阵系数调整，不是独立 affine U/V 偏置寄存器。静态增益与 2×2 表值见 `outputs/metol/recipe-color-table-values.json`。底层 `CHM_MAT` setter 接受 −1024…1023 的有符号系数并按低 11 位打包，SDK 的 `unsigned int` 声明不能用于推断系数只允许非负。尚未发现对应 Metol HUE 档位的原厂角度接口；若通过该矩阵实现源端算子，仍需确认源端角度、排列和量化。低层色彩节点反汇编见 `outputs/metol/recipe-color-nodes.asm` 和 `outputs/metol/recipe-wb-path.asm`。

## 当前实现状态与下一步

- Metol 普通配方模式保留并显示完整七维向量，但仍是浏览模式。
- NX300M 的 Picture Wizard API 已能读写三项标量和一个 ColorTone 角度；它尚未接收 Metol 配方，也没有确认这些值与上游算法等效。读写和原厂解码证据见 `outputs/metol/pw-control-probe.json`；该结果不含成像验证。
- RGB 行增益模块和命令行 API 已实现，可通过 `tools/run_stock.py --program camera --color-style` 或 `tools/simulator.py --color-style` 随原厂相机启动加载；构建／探针命令为 `conda run -n nx300m-mod python tools/build_color_style.py`。原厂程序会话已实际验证多模块 Ecore 链、ready、105/100/95 读写与100/100/100恢复；预览矩阵 setter 返回0，改写标记随设置／复位切换。报告为 `outputs/metol/color-style-session.json`。该会话的图像输入在 ISP 输出后回放，画面不能用于验证 RGB 成像效果；实机验证仍待完成。
- 3D LUT 可承载烘焙后的逐像素颜色变换，但不会保留七个独立滑杆，也不能替代空间锐度。当前 Lightroom 风格库的颜色域和校色状态见 [LOOKS.md](LOOKS.md)。

完整七维配方仍需补齐 SAT 的其他模式，以及 SHARPNESS/CONTRAST 的源算子与 NX300M 映射，再通过预览和 JPEG 样张核对结果。HUE 原表已单独接入下述色度桥；七维 JSON 当前仍是配方元数据，尚未驱动完整应用。

### 源端映射复查

上游 `filmlab.sh` 的槽值及整数编码只说明参数传输，尚未给出 NX500 的 HUE 角度、饱和度增益、锐化参数或对比曲线。把 0–20 线性缩放到 NX300M 的 −4…4 不能证明效果一致；RGB 百分比也还需确认源端矩阵的乘法方向、归一化与处理顺序。NX300M 首个有色色调矩阵三行和为 290/244/248，中性值为 256，因此 PWCOLOR 会改变中性轴，不能当作标准 HUE 旋转。

NX500 HUE/SAT 消费函数和表值已提取，两个算术叶子的 C 移植已与原厂 ARM 执行对照通过；后续继续接入完整模式选择，并比较两平台静态／预览 Gamma 和 Edge 参数。完整七维后端需要色彩矩阵、对比曲线、空间锐化三个路径；不能用逐点 LUT 替代锐化，也不能以部分参数写入成功报告整套配方已应用。

### 已提取的两平台原厂证据

`tools/inspect_recipe_tables.py` 从当前 NX300M 1.15 原厂 IQ ELF 按符号尺寸导出 13 张表，共 1664 字节，并记录 26 个相关函数、GOT 重定位和原始字节。结果为 `outputs/metol/recipe-native-tables.json` 与同名 `.asm`。拍摄 ContrastX 为 `[0,168,344,512,680,856,1023]`，取景为 `[0,42,86,128,170,214,255]`；两路 Sharpness 均为 `[160,180,202,228,256,288,324,364,410]`。ContrastY 每档 7 点、共 9 档有实际访问步长依据。取景 Edge 没有独立 Gain/Coring/Limit 动态表符号，其 setter 使用固定参数和 Sharpness 表。

NX500 参考原包来自 [三星官方支持页](https://www.samsung.com/ie/support/model/EV-NX500ZBMIFR/)，版本 1.12。已完整核验 281999904 字节 ZIP、内部 NX500.bin 的 CRC、型号及版本，10 个分区的 complemented CRC32 全部匹配。`tools/prepare_nx500_reference.py` 可复现提取，原包和提取文件分别位于 `work/downloads` 与 `work/reference/nx500-stock-1.12`。固件用于静态分析和下述隔离算术执行，没有加载到 NX300M 会话。

`tools/inspect_nx500_pw.py` 读取此原厂 p7 分区，确认 HUE/SAT/SHARP/CONTRAST 的实际表含 21 项，范围 0–20；编码为 `(((16*v-145)&0xffff)<<16)|0xd80a`。RGB 正值编码使用 `floor(255*v/200)<<20|0xff`；旧脚本在 0–200 中有 187 项与原厂编码不同。报告为 `outputs/metol/nx500-pw-source.json` 与 `.asm`，已追到 HUE/SAT 的消费函数、模式选择及实际算术。锐化和对比曲线的完整源映射尚未完成。

### 完整 HUE/SAT 算术移植

`runtime/nx500_color_math.h` 实现原厂两个叶子的完整运算：行主序 Q8 矩阵乘法；Q8 色度混色后，仅缩放矩阵元素 4/5/6/7，再按模式指定的行和重建元素 3/8。显式保留 signed32 累加回绕、统一加 128 后算术右移、signed16 写回；不使用会触发 C 有符号溢出未定义行为的写法。

`python tools/probe_nx500_color_math.py` 把这份 C 编译为 ARM，并将原厂两个函数的未改字节嵌入同一隔离 ELF。提取前检查原分区身份，以及函数没有 PC 数据访问、外部调用或跳出范围的相对分支，允许保持指令字节不变地搬移。QEMU 实际执行两份函数，并与 Python 转写对照：63 组 HUE、441 组 SAT／混色／行偏移、1 组 signed32 溢出，以及 21 组完整混色→SAT→HUE 组合，共 **526 组一致**，构建和执行退出码均为 0。结果为 `outputs/metol/nx500-color-math-probe.json`；不依赖尚未安装完成的 Unicorn。

该检查证明上述输入上的数值运算一致，不覆盖原厂 builder 的模式分支或 ISP 像素。完整后端同时保存和恢复新鲜的 RGB→YCC 九系数与 CHM 四系数；若已把完整源算子写入 YCC 后仍保留原有非单位 CHM，会再次叠加色度处理。目标 selector3 接受 signed11 的 Q8 九系数，无法表达所有 signed16 结果，需显式处理越界而非悄悄截断。

`runtime/chroma_pair.h` 已实现两组原厂参数的保存、完整结果应用和恢复。只有 CHM／YCC 都齐全时才允许完整应用；目标九系数先检查 signed11，再写中性 CHM 和完整 YCC。中途失败会尝试恢复两组最新原值，并分别保留原始错误和恢复错误；恢复失败时 `state_known=0`，不把未知状态写成已恢复。这里的连续写入不是物理帧级原子更新。

`python tools/build_chroma_pair_probe.py` 已构建并执行原厂 IPCS／IPCM setter 合成 RAM 检查，16 项全部通过，退出码 0。覆盖缺失 base 不写、完整 YCC＋中性 CHM、重复不累积、越界拒绝、双组恢复、第二次写入失败、恢复失败和恢复最新 base。写入／恢复失败由专属包装器注入返回码 7，其他写入均调用未改原厂 setter；完整内存与直接原厂调用结果对照。记录为 `outputs/metol/chroma-pair-probe.json` 和对应日志。生产色度桥已接入该组件的缓存、完整应用和双组恢复。标准／Custom1 P/A/S/M 路径已完成模式映射及在线设置／复位；其他模式尚未开放。

系统分区 06 是 LZO 压缩的 ext4；WSL `lzop -dc partition-06.bin > platform.img` 后，可用 `/usr/sbin/debugfs` 只读导出 `/usr/apps/com.samsung.di-camera-app/bin/di-camera-app`、`/usr/lib/libcapture-fw-prod.so` 和 `/usr/lib/libudd5.so`。分区 09 解压后是带 SWSUSP1 的休眠镜像，不是 ext4；未挂载或运行该镜像。

### NX300M 色度矩阵的实际 setter 检查

`runtime/ycc_abi_probe.c` 通过未修改的原厂 `libudd.so.3.1` 调用取景／拍摄两个 RGB→YCC setter，仅把各自寄存器基址指向独立合成 RAM。`tools/build_ycc_probe.py` 构建并运行；结果 `outputs/metol/ycc-abi-probe.json` 为 10/10 通过、退出码 0：Q7 中性 `[128,0,0,128]`、含负交叉项 `[111,-64,64,111]`、边界 `[-1024,1023,-1024,1023]` 均按预期打包；1024 与 −1025 返回 2，整片寄存器 RAM 保持不变。所有输入结构也未被 setter 改写。

此路径可承载有符号色度矩阵，但不能先把负数注入 `GetCc2CcMatrix`，再让非中性的原厂 SAT 路径处理：该路径使用逻辑右移及上限 255 钳位，会破坏负乘积。候选实现应在最终 `ipcs_rgb2ycc_param`／`ipcm_rgb2ycc_param` 的 `sel=CHM_MAT` 边界组合原厂已算好的矩阵，或直接计算完整矩阵。该探针只验证原厂函数 ABI、范围与寄存器打包，不验证硬件像素、旋转方向或 Metol HUE 映射。

## 已接入的 HUE/SAT 控制

`runtime/chroma_style.h` 接入现有 `color-style.so`，共用 RGB 模块的锁与 Ecore 定时器，保留原有 RGB 和 16 字段色度协议。`runtime/nx500_color_operator.h` 集中保存源端 HUE、混色、行和表，并按原顺序组合算术。

- HUE／SAT 范围均为 0–20，中性为 10。
- HUE／SAT 都为 10 时恢复原厂。支持完整路径时，任一档位非中性均执行完整 YCC 算术；SAT 从 14 回到 10 而 HUE 仍为 2 时，保持同一处理顺序，不切回另一量化边界。
- 缺少完整 base 或模式映射时，SAT=10 的 HUE-only 仍可在最终 CHM 边界左乘原厂 Q7 矩阵，按 `(+128) ASR8` 取整。原表第 2／18 档 235／234 的非对称值保留。
- 完整路径使用当前原厂 YCC，执行源端混色、SAT 系数重建、HUE 矩阵乘法，再写入完整 YCC 与单位 CHM。当前支持标准／Custom1 Picture Wizard 的 P/A/S/M、2D、原厂单位 CHM。非单位 CHM 不会被静默丢弃。
- 缺少矩阵返回 −110；signed11 越界返回 −111；不支持的模式返回 −112；非单位原厂 CHM 返回 −114。先检查再应用。运行中若模式／base 变化而不再满足条件，恢复两组最新原厂值；写入故障的恢复结果在上下文中单独保留。
- 每次原厂 CHM／YCC 输入都更新缓存；应用结果不进入缓存，避免累计。复位恢复两组原值。连续寄存器写入不是物理帧级原子更新。

```powershell
conda activate nx300m-mod
python tools/chroma_style.py --status --context
python tools/chroma_style.py --set --hue 2 --saturation 14
python tools/chroma_style.py --reset
```

统一遥控页“机身设置…”提供色相／饱和度输入、支持范围和复位。原生 `/api/v1/color/chroma` 接受 GET，以及 POST `{"hue":2,"saturation":14}` 或 `{"reset":true}`。响应报告预览／拍摄改写状态、setter 返回值、回退次数和控制支持。ready 必须匹配当前相机进程。`supported_saturation=true` 表示实现已提供，使用范围仍由运行时检查。

`tools/build_chroma_probe.py` 构建生产 SO，核对 21 张原厂 HUE 表，并以未修改的 NX500 ARM 算术叶子和 NX300M setter 对照合成 RAM。已通过完整 SAT→HUE、新 YCC 重算、模式变化双组恢复、复位，以及原有 HUE 检查；模式输入使用显式 Standard-P／Custom1-P 测试夹具。结果为 `outputs/metol/chroma-style-probe.json`。实际原厂程序及原生 HTTP API 也已设置 HUE 2／SAT 14，`preview_modified=1`、完整路径错误为 0；随后复位 10／10，`complete_modified=false`、恢复返回 0。记录为 `outputs/metol/chroma-saturation-session.json`。该记录的 `neutral_sat_transition` 还包含原厂程序中 SAT14→10、HUE2 保持不变的检查：两次均为完整 YCC 路径，最终复位返回0。Custom1 分支已通过原厂叶子／setter 对照的合成 RAM 检查，但尚未在真实原厂程序切换至 Custom1 联调。统一机身设置页面已在内置浏览器目视检查；新增对比度交互验证见下文。

### 模式语义与上下文

`tools/inspect_mode_context.py`、`tools/inspect_mode_enums.py` 提取 NX300M getter、名称解析和参数转换证据；`tools/inspect_nx500_modes.py` 提取 NX500 实际转换块、原厂语义日志字符串和消费分支。证据分别为 `mode-context*.json/.asm` 与 `nx500-mode-mapping.json/.asm`。

| 语义 | NX300M IQ 字段 | NX500 IQ 字段 | P/A/S/M 数值 |
|---|---|---|---|
| 拨盘 | `0x49 dial` | `0x77 CAPTURE_MODE` | 1/2/3/4 |
| 拍摄 | `0x41 capture` | `0x6f APP_MODE` | 2/3/4/5 |
| Picture Wizard | `0x2c PW` | `0x4f EFFECT_MODE` | Standard=0；Custom1=16 |

因此不能按字段名称直接对接两台机身。当前原厂会话为目标 capture2/dial1/PW0，对应源端 CAPTURE1/APP2/EFFECT0，即标准 P 模式。标准／Custom1 P/A/S/M 排除源端 capture0（自动）和 capture6（SmartPro）分支，effect0 排除 effect15 分支，因此 mix/offset/delta 均为0。Custom1 的 effect16 同样排除 effect15 分支，生产完整路径已开放；自动、SmartPro 和其他风格仍需各自适配。

`--context` 与响应核对 request ID／guest PID。它读取目标 capture、dial、3D、SmartPro、PW 五项，以及同一次桥锁快照中的两路原厂输入矩阵和调用状态。`source_mode_mapping_verified` 仅在当前已支持的 Standard/Custom1 P/A/S/M 2D 上下文为 true，同时给出具体 `source_modes`。输入矩阵不是硬件读回，也不表示两组来自同一帧。

当前预览原厂 CHM 为 `[128,0,0,128]`，YCC 为 `[77,150,29,-47,-91,138,138,-115,-23]`。拍摄端两组矩阵仍缺失。此前 HUE-only 的在线设置／复位证据保留在 `chroma-mode-context.json`、`chroma-session-applied.json`、`chroma-session-restored.json`，旧界面截图为 `outputs/firmware-runtime/chroma-settings.png`。

以上是源算术、参数写入和恢复检查。当前照片回放位于 ISP 输出之后，不能验证 SAT/HUE 像素效果；尚未确认真实 JPEG 路径或跨机型成像等效。七维配方整体仍未完成。

### Contrast 源端消费链

`tools/inspect_nx500_contrast.py` 已从原厂 P7 提取 IQ `0x57` 的实际消费链：`0x803865d8` 解码，写入 GammaD5 输入 `+0x80`，转为上限20的 subindex1，`0x8038f90c` 按28字节步长选中21行七点表之一。`0x8038dc68` 使用插值 helper `0x803887f0` 对基础 Gamma 的19个 Y 值重映射。静态分支有共同 X19 和5组 Y19，其他分支使用动态数组。证据为 `outputs/metol/nx500-contrast.json/.asm`。

共享 Contrast X7 为 `[0,168,344,512,680,856,1023]`。源档位2/4/6/8/10/12/14/16/18的整行 Y7，分别与 NX300M 拍摄端 −4/−3/−2/−1/0/1/2/3/4 完全相同。其余12档没有相同目标行；预览端使用0…255域，直接整数比较没有相同表。这是表值相同的证据，不等于插值算法、基础 Gamma 或图像输出相同。

helper 含端点返回、外侧线性外推，以及首尾／内部区间的浮点分支。`runtime/nx500_contrast_math.h` 已按原指令顺序移植这些分支，保留每步 float32 舍入、整数32位回绕、ARM有符号除法、`+0.5` 后向零转换及转换边界；没有替换为通用样条函数。

`python tools/probe_nx500_contrast.py` 把原厂插值字节及六类软件浮点依赖按原虚拟地址装入隔离 ARM ELF。提取前核对原 P7 身份、跳转范围及 PC 访问，原代码未改写；这不是运行完整 NX500 固件。对21行七点曲线逐一输入 −16…1039，覆盖完整10位整数 Gamma Y 域、原静态负值以及边界外推。C 移植与原厂执行的 **22,176 点全部一致**，构建／执行退出0。结果为 `outputs/metol/nx500-contrast-probe.json`，完整原厂输出保存在 `nx500-contrast-probe-values.json`。`--reference-only` 可仅执行原厂函数。

这次对照覆盖上述原表和输入域，不推广为任意节点表／异常输入的一般等价证明。源算术现已接入下述 Contrast 阶段，后续仍由 NX300M 原厂曲线处理与硬件接口执行；拍摄端保留19点／10位路径，预览端使用15点／8位量化适配。七维配方整体仍未完成，亦无新增成像结论。

### Gamma 调用边界与在线采集

`tools/inspect_gamma_hook.py` 用 GOT／PLT 重定位核对了调用身份。拍摄端 `SetParam` 的三次 `ApplyLut` 分别为 Contrast（0x33cbe4）、EffectTone（0x33cc18）和条件执行的九点3D tone（0x33cde8）；预览端两次分别为 Contrast（0x340fa0）和 EffectTone（0x340fd8）。`runtime/lut_port.c` 拦截 LUT Update／3D LUT setter，没有拦截 Gamma；共享模式位仍可让原厂 Gamma 选择附加3D tone。证据为 `outputs/metol/gamma-hook.json/.asm`。

`runtime/contrast_style.h` 在现有生产 `color-style.so` 中拦截两路 `ApplyLut`。它按原厂 Contrast X符号地址、Y表范围／28字节行对齐、七节点及19／15点数量识别调用；不会把 EffectTone 或3D tone 当作 Contrast。关闭源对比度控制时原样转发；启用后以本次原厂输入计算源曲线，替换该阶段输出。`tools/chroma_style.py --status --context` 的 `contrast_observer` 提供原厂输入、原厂输出、实际采用的输出、代际和改写标记。

实际原厂程序已采到预览15点输入 `[0,1,2,3,6,11,22,45,81,129,181,225,245,255,255]`。原生 PW 对比度由0设为+1，原厂表index从4变5，观察到Gamma输出变化；恢复原设置0后，index回4、输入与输出相同。两次均通过原厂服务通知链完成，结果 `outputs/metol/contrast-session.json`。拍摄Gamma尚未触发，不能从预览记录推出拍摄链已运行。

另外，`python tools/probe_nx500_contrast.py --compare-target` 确认两机原插值器不能无条件互换：源level0、query−16时，NX500返回−24，NX300M返回−1；对照器构建0、执行5并记录 `Target mismatch query=-16 count=7 source=-24 target=-1`。该不一致见 `nx500-contrast-target-probe.json`，源C移植的22,176点对照仍全通过，分别保存。原厂源静态Gamma包含负值，因此后续使用源算术，不直接借用目标插值器处理全部源输入。

拍摄动态Y19和预览Y15的实际生成、后续裁剪及写入尺寸需分别保留。预览自适应分支从19点曲线采样后按255/1023转换，已有8位输入无法无损还原源10位中间值。原厂 `IntSpline` 实际写入范围也不能只凭 UDD固定复制长度推定；目前只替换 Contrast 阶段输出；后续原厂样条及硬件写入保持原调用顺序，没有独立重写整片硬件 LUT。

### 对比度控制与复位

`runtime/nx500_contrast_operator.h` 组合原厂21张七点表、已验证的源插值和源 `ApplyLut` 的上界1023裁剪（不做下界裁剪）。捕获路径直接使用10位域；预览路径以整数对称四舍五入将8位输入转换为10位，源运算后再量化回8位。源表与原P7全字核对，原ARM15点预览向量及1039输入的上界裁剪检查通过，记录 `nx500-contrast-operator-probe.json`。这项显式量化适配不能无损恢复已丢失的低位。

设置变化通过现有 `CIpcParameterNode::Update` 链仅使 Gamma 节点原厂缓存失效，随后使用当次新参数重算，避免保留栈上的输入指针或直接重放过期 Gamma。它与现有 LUT hook 通过 `RTLD_NEXT` 串联。每次设置有代际标识；只有预览观察代际匹配、算子错误为0且改写状态符合配置，才报告 `preview_applied=true`。拍摄端尚未出现时保持 `capture_available=false`，不伪造拍摄成功。

```powershell
python tools/contrast_style.py --set 19
python tools/contrast_style.py
python tools/chroma_style.py --status --context
python tools/contrast_style.py --reset
```

`--set 10` 仍启用源对比度并覆盖原厂对比度阶段；`--reset` 关闭覆盖，恢复当前原厂 PW 对比度，二者含义不同。当前支持标准／Custom1的P/A/S/M。输入域不支持或模式变化时保留原厂结果并记录错误，不部分改写曲线。

在线原厂程序已设置第19档：预览代际匹配、改写为true、错误0，15个Gamma输出逐点等于原ARM参考经明确量化后的结果；复位后新代际匹配、改写false、实际输出等于原厂输出。记录为 `outputs/metol/contrast-source-session.json`。生产SO改用现有ARM工具链并私有链接所需libgcc浮点／int64函数，原有色相／饱和度检查仍通过；软件浮点符号不会替换原应用的运算实现。

统一“机身设置”页面已增加 NX500 对比度控件，范围0…20，并单独提供“恢复原厂”。接口 `GET /api/v1/color/contrast` 读取状态，POST `{"contrast":19}` 设置，POST `{"reset":true}` 关闭覆盖。设备能力 `contrast_style` 仅在当前进程的桥已就绪时开启。返回 `enabled`、`generation`、各路径实际代际／错误／改写标记、`preview_applied`、`capture_applied` 和 `pending`；UI按状态轮询，不将排队当作已经应用。

构建和JavaScript语法检查通过。原生HTTP实际经历设置19→pending→预览已应用→设置10保持enabled→reset关闭覆盖／预览恢复；新证据保存在 `contrast-source-session.json` 的 `native_http`。内置浏览器已实际填写19并点击应用，看到“待更新”转为“预览：已应用”，随后点击恢复并确认“覆盖已关闭；预览：原厂对比度”。截图为 `outputs/firmware-runtime/contrast-applied.jpg` 和 `contrast-restored.jpg`。导航旧分支名称已改为NX300M，窄屏相机／设置／帮助／控制器／相册增加可读名称并重新加载核对，见 `remote-navigation.jpg`。拍摄路径仍等待真实Gamma调用，ISP像素和照片输出仍未验证。

### Sharpness 源端消费链

`tools/inspect_nx500_sharpness.py` 已核对 IQ `0x56` → 参数包 `+0x88` → `CIpcEeD5` subindex0 → 原厂21档Q8因子表 → EE参数块。21项为 `[140,150,160,170,180,191,202,215,228,242,256,272,288,306,324,344,364,387,410,435,460]`。NX300M拍摄／预览九档恰好对应源档位2/4/6/8/10/12/14/16/18。证据为 `outputs/metol/nx500-sharpness.json/.asm`，脚本及编译检查退出0。

源端先将因子乘subindex5百分比并以无符号整数除100，再分别处理GS/GD/GH/WS/WD/WH六个EE字段。四个字段为8位、饱和到255；GH/WH只改低7位、饱和到127并保留高位。默认百分比100、打包偏移、乘法／除法／取整和阈值比较均保留原指令依据。

`runtime/nx500_sharpness_math.h` 已实现源端百分比、增益量化和低7位打包运算，明确保留32位乘法回绕、signed阈值比较、负值补255后取位。`tools/probe_nx500_sharpness.py` 以未修改的原ARM运算片段作对照，包装代码只提供寄存器参数和返回值；内部相对跳转保持原位移。构建及执行退出0，76,153组全部一致，覆盖21档因子、全部字节基准增益、百分比取整、signed／回绕边界及高位保留。结果见 `outputs/metol/nx500-sharpness-probe.json`。这些是隔离运算片段，未执行完整EE节点或验证空间锐化效果。

`tools/inspect_edge_abi.py` 已核对SDK结构、两条GOT重定位和27个原指令位置，生成 `outputs/metol/edge-abi.json/.asm`。NX300M两个32位槽对应正／负边缘增益；setter接受0…255但最终只写低7位，因此128会成为0，255会成为127。源六字段没有一一对应关系。当前原厂基准范围内，21档因子在拍摄端产生8…39的初始增益，预览最多35（模式3加倍为70），均在7位范围内；部分相邻源档位会量化到同一结果。

目标适配在原厂Q8因子读取处注入档位，保留每次新基准和后续分支；不对setter收到的已量化增益作比例补偿，例如原基准16／17乘160都得到10，改乘460却应分别得到28／30。源百分比override、源基准行和后处理helper仍未完整移植；当前实现是源档位因子适配，不能宣称源空间算子或成像等效。

### Sharpness 原厂阶段控制

`runtime/sharpness_native.h` 导出两张可动态替换的原厂九项表，并以同一互斥锁包围两个原厂 `SetParam` 的完整调用。启用时将该路九项临时设为选定源因子，调用原函数后恢复；关闭时使用原九项。TLS深度及逐层保存支持同线程嵌套，调用期间不持有控制状态锁。原始参数索引、每次基准、模式分支、LUT和setter调用保持原顺序。

`tools/probe_sharpness_native.py` 确认两原vtable实际命中挂钩，原库中每张表唯一的直接GOT读取均在对应消费者；未发现直接跳入两原入口绕过符号解析的调用。6,048组完整16KiB寄存器RAM对照一致，另有1,200次双线程调用，合计7,248组、失败0；原表恢复、嵌套及错误路径通过。构建和运行退出0，stderr为空，记录见 `outputs/metol/sharpness-native-probe.json`。设备打开计数和无关DSP调用使用显式夹具；这些结果不覆盖物理ISP像素输出。

`runtime/sharpness_style.h` 将档位／启用状态代际接入原厂Update→Reset→SetParam链，仅在配置或模式资格变化时请求重算。真实enhancement setter的返回值、调用次数和最后正／负增益进入 `sharpness_observer`；首次真实预览调用后才发布ready。支持标准／Custom1、P/A/S/M、2D；超出范围恢复原厂计算并报告−112。两路读回记录实际执行代际，不把请求排队当作应用成功。

```powershell
python tools/sharpness_style.py --set 20
python tools/sharpness_style.py
python tools/chroma_style.py --status --context
python tools/sharpness_style.py --reset
```

`--set 10` 仍启用源因子256；`--reset` 关闭覆盖并恢复当前原厂PW锐度。CLI与对比度共用 `tools/native_style.py` 的请求身份、会话及代际检查。统一遥控页提供锐度档位和恢复按钮，API为 `GET /api/v1/color/sharpness`、POST `{"sharpness":19}` 或 `{"reset":true}`；对比度与锐度共用状态解析、校验和前端轮询。`sharpness_style` 能力仅在当前相机桥就绪时开启。

已在重启后的NX300M原厂程序中完成：原厂0预览增益20→源20增益35→覆盖开启时原PW改+1仍为35→关闭覆盖恢复当前+1增益22→源10仍启用并输出20→最终恢复原PW0、覆盖关闭。真实浏览器应用19后显示“预览：已应用”，恢复后显示“预览：原厂锐度”；HTTP读回的preview代际匹配、错误0。记录为 `outputs/metol/sharpness-session.json`，截图为 `outputs/firmware-runtime/sharpness-applied.jpg` 和 `sharpness-restored.jpg`。拍摄端仍未触发实际Edge调用；当前ISP后照片回放不能显示或证明锐化像素效果。


## 色彩桥进程生命周期与模拟器恢复验证

### 故障现象与原因
在主会话仿真测试中曾出现配方 API 返回 503（“当前相机的配方桥尚未就绪”）。经 `/proc/mem` 与符号排查确认根因：
1. 旧 `color-style.so` 在构造器/析构器中无条件清理全局 ready 和 request 文件。原厂相机 fork/exec 产生的辅助子进程退出时误删除了父相机的就绪标记，并可能持有继承锁。
2. 原厂相机在 LD_PRELOAD 模块构造之后再次执行了 `fork()`，导致实际主相机进程（guest PID 91）与构造时记录的 PID（89）不一致，触发了过激的 PID 属主防护拦截。

### 修复机制
- **属主隔离**：构造函数仅初始化当前进程变量，严禁清理全局标记文件。
- **Fork 安全接管**：尚未 attach 的启动 fork 子进程在首次进入 Ecore 主循环时允许安全接管；重置继承锁并丢弃可能不完整的父缓存；已 attach 后的子进程/helper 严格隔离。
- **动态重发布**：本进程就绪标记若被外部误删，由本进程检测并重新发布；timer 绑定 owner PID，杜绝继承 timer 越权运行。

### 原厂程序仿真验证证据
- **Portra 400 应用**：`outputs/metol/color-lifecycle/recipe-preview.json`，代次 `generation=2`，四阶段全量生效（`preview_applied_mask=15`）。
- **S1 退出参数保持**：`outputs/metol/color-lifecycle/recipe-after-s1.json`，半按快门 S1 退出后 `generation=2` 保持不变，参数无重复提交或回滚。
- **原厂参数复位**：执行 `tools/recipe_style.py --reset`，代次递增至 `generation=3`，`enabled=false`，参数归位。
- **LUT 面板 S1 退出**：`outputs/metol/color-lifecycle/lut-panel-s1-verify.json`，验证 LUT 面板经 S1 退出后，`color_style_ready` 与 `recipe_style_ready` 均稳定保持为 `true`，参数代次无漂移。
- **综合恢复报告**：`outputs/metol/color-lifecycle/main-recovery.json`（`recovery_verified=true`）。
