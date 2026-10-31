# NX300 / NX300M 两个参考项目源码核对

核对日期：2026-10-31。只读审计，未运行第三方启动脚本、未写卡、未刷机，未修改现有 LUT 加载器。

## 版本

| 项目 | 本次读取的提交 | 本地参考目录 |
| --- | --- | --- |
| [Akira144/nx300-lut](https://github.com/Akira144/nx300-lut/tree/b57739d705a7fff46e68328d6de9f29f228b452d) | `b57739d705a7fff46e68328d6de9f29f228b452d` | `work/reference/nx300-lut/` |
| [blodburn/new_nx300m](https://github.com/blodburn/new_nx300m/tree/630a6451e90aa0aa3b45a4b9ad55ddb9849d7865) | `630a6451e90aa0aa3b45a4b9ad55ddb9849d7865` | `work/reference/new_nx300m/` |

## 结论

两个项目都可以作为参考，但不是现有 NX300M RAM LUT Hook 的直接替换件，也没有提供当前遥控快门断链或遥控 RAW 设置的现成修复。

### nx300-lut：拍摄后处理 JPEG

- 实际构建入口为 `Makefile` 中的 `src/lutd.c`。它用 inotify 监听写入结束，解码 JPEG、按 RGB cube 做插值、重新编码，然后替换文件。不是向 ISP 写 LUT。
- `handle_srw()` 提取 SRW 的内嵌 JPEG，处理后另存同名 JPG；不做 Bayer RAW 显影，也不使 Remote Viewfinder 获得 RAW 拍摄能力。RAW+JPEG 配对通过检查同名 JPG 并固定等待 1.5 秒处理。
- 可参考：事件驱动的新增文件处理、JPEG 行处理、文件完整性检查、元数据保留、RAW+JPEG 去重，以及只读输入监听与图形提示解耦。
- `src/lutshow.c` 直接使用 X11 socket 协议，以内置点阵字体显示短时状态条；没有交互菜单，没有调用输入抓取或设置输入焦点。可以参考轻量提示的思路，但这不能证明它能解决 NX300M 的窗口呈现问题。
- 性能问题不能照搬：同一个事件循环同步执行图像处理和按键操作；`hotkey_step()` 重新枚举并排序 LUT，还调用阻塞的 `led_blink(next + 1)`。每次闪灯含两个 150 ms 等待，32 项时单此等待就达 9.6 秒；图形提示也会 fork/exec 新进程。
- `KEEP_ORIG=1` 时先 `rename(in, orig)`，再 `rename(tmp, in)`。这是两个独立操作，中间存在原路径缺失窗口；备份仅用文件 basename，不区分 DCIM 子目录。不能把整条操作视为断电安全事务。
- 不建议接入当前自动拍摄主链：会再次有损压缩 JPEG，并增加写卡和连拍负载；若与现有 ISP LUT 同时启用还会叠加处理。其 README 的耗时描述不是本次机身测量结果。

关键实现：[lutd.c](https://github.com/Akira144/nx300-lut/blob/b57739d705a7fff46e68328d6de9f29f228b452d/src/lutd.c)、[lutshow.c](https://github.com/Akira144/nx300-lut/blob/b57739d705a7fff46e68328d6de9f29f228b452d/src/lutshow.c)。

### new_nx300m：主机端色彩研究和静态调用链

- `research/nx300m_lut18.[ch]` 是通用的 18³、12 bit、RGB 加 reserved 的 CPU LUT 引擎；文件明确说明尚未集成 NX300M。不能把这个尺寸或打包格式当作 NX300M ISP 的原生格式。
- 与我们当前三路 9³、每路 730 个 uint16（含一个 padding）、10 bit、总计 4380 字节的 D4 表不兼容；不能直接替换表文件或转换器。
- `nx300m_host_capture_copy_adapter.c` 只接受受验证的独立主机 RGB 输入，先处理私有副本，成功后再发布结果。它明确拒绝实验性的机身原生输入，不是实机 Capture Hook。
- `nx300m_camera_style_response.c` 和 `nx300m_look_builder.c` 用矩阵、曲线、饱和度等参数生成实验风格。富士／徕卡名称不构成原厂配置来源或实机颜色匹配证据，不用于替换我们从 LR 导入的 NC／NN、GR Neg／GR Pos 或用户要求保留的 Hard Mono。
- `nx300m_tizen_film_controller.c` 将浏览选择与已确认选择分开：确认保存成功后提交，取消恢复已确认项。这是可借鉴的菜单状态组织方法。
- `nx300m_tizen_filmsim_efl.c` 为独立 EFL 菜单原型，默认没有实机按键映射；确认只保存配置，没有对接 ISP。主机假 EFL 回调测试不能证明 NX300M 显示、焦点或按键适配成功。
- 静态逆向值得继续对照：Picture Wizard handler → `GUI_Set_System_Value(5,4,39,dynamic)` → 菜单状态分支 → Capture item 39 → DB/preference 候选链。项目没有证明真实 UI 事件已执行到 ISP；脚本核验指定参考二进制的指令，不能把函数地址或数字参数直接用于另一版本。
- 文档区分了 MFW 1.1 发布标签和内部三星固件 1.15；关于 1.14 的兼容缺口不能直接套用到我们已有 MFW 1.15 环境。仍应逐个核对实际二进制。

关键实现：[LUT18](https://github.com/blodburn/new_nx300m/blob/630a6451e90aa0aa3b45a4b9ad55ddb9849d7865/research/nx300m_lut18.h)、[菜单控制器](https://github.com/blodburn/new_nx300m/blob/630a6451e90aa0aa3b45a4b9ad55ddb9849d7865/research/nx300m_tizen_film_controller.c)、[Picture Wizard 条件调用链](https://github.com/blodburn/new_nx300m/blob/630a6451e90aa0aa3b45a4b9ad55ddb9849d7865/docs/NX300M_PW_EVENT_TO_CAPTURE_ITEM39_CONDITIONAL_ROUTE.md)。

## 对当前项目的取舍

1. 保留已打通的原厂写表 RAM Hook 和现有 LUT 库；不接入 JPEG 自动覆写守护进程。
2. 菜单可借鉴“浏览—确认—取消”的状态组织与轻量字体提示；不替换现有按键码、窗口集成和显示链，也不继承阻塞闪灯或同步图像处理。
3. 原厂 Picture Wizard 集成可把第二个项目的静态路径作为逆向线索；实际入口、线程、参数和 ISP 更新仍需本项目证据。
4. 遥控快门、会话断链、遥控 RAW 画质配置与 LUT 生效应继续独立排查。两个项目均无对应 Remote Viewfinder 协议实现。
5. 当前 `runtime/lut_ram_blob.c` 另含 `stage_follow()` 寄存器位实验；以上两个项目均不能为其地址、位含义或取景效果提供验证。不能由这些参考项目推出当前取景 LUT 已验证。

## 复用与验证边界

两仓库本次快照均未见 LICENSE/COPYING；第二个 README 也明确说明未选择开源许可证。本轮只读参考设计和证据，未复制其实现进入发布代码。

本轮未编译或执行参考项目，未复测本项目既有构建。上述结论来自具体源码和文档，不能替代实机 JPEG、RAW、遥控会话、窗口与按键验证。
