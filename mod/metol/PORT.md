# Metol NX300M 适配

上游：WApaperplane/metol，提交 `ddbe8b119e046798e557add13837834f5e8d90ac`。沿用 AGPL-3.0，Nuklear 的 MIT 许可保留在上游 vendor 中。

`nxui.c` 和 `vendor/nuklear_xlib.h` 是本次适配的源码；model、services、Nuklear 核心和配方库直接使用 `work/reference/metol` 的固定版本。没有使用旧 NX300M Mod。

已适配：使用 NX300M 原厂 glibc/X11/Xft 链接 ARM 程序；将 Mod 自己的 pixmap 顺时针旋转到 480×800 X11 屏幕；触摸坐标反向转换；支持原厂 KP 方向键、OK、MENU。

当前配方面板为浏览模式。NX500 的七维 PW、capdtm 命令、A2 补丁、LUT 槽地址和安装钩子尚不能用于 NX300M。配方原始七维数据保留，不以未经校准的三项近似代替完整配方。七维参数与 NX300M 原厂 PW／IQ 节点的逐项适配判断见 [RECIPES.md](../../docs/RECIPES.md)。

LUT 数据适配：`tools/cube_to_d4.py` 将标准 3D `.cube` 三线性采样为 9³ 网格，按 R 外层、B 内层写成三路 little-endian uint16，每路 1460 字节（729 个 10 位样本加一个填充字）。`CIpcs3DLutD4::SetParam` 的原厂 ARM 代码给出了相同的轴顺序和 `i*1023 >> 3` 的单位表采样规则。实际输出 4380 字节，2187 个单位表样本核对通过。

`tools/metol_slot.py` 已适配上游 17³ RGBX 槽，执行原始上游 `cube2lut.c` ARM 程序生成的轴置换槽并核对转换。该提交未随仓库提供 LUT 素材包。

`lut_engine.c` 是本项目 NX300M 后端，暂存选择并支持进入前备份、关闭恢复与保留退出。UI 在 LUT 模式下调用该 ARM 后端，保留原七维配方浏览模式。当前库有 17 项：11 份本机 Lightroom DCP 风格片段和 6 份伴随 `nx500-filmsim` 颜色配方；后者重排了上游 B-fastest 存储顺序并保留曲线舍入，不含空间颗粒。扩展入口、来源和转换范围见 [LOOKS.md](../../docs/LOOKS.md)。

面板、engine 与 LUT 适配器通过 `NX300M_MOD_ROOT` 共享运行根目录，默认 `/mod`，可使用 `/mnt/mmc/nx300m-mod` 等 SD 路径；面板 `--root` 可显式覆盖。自定义根路径已经通过原厂 libudd 三路写表及坏表回退探针，路径约束和报告见 LOOKS.md。

`runtime/lut_port.c` 拦截并转发原厂 `ipcs_3d_lut`，不主动制造原厂调用。理光负片表已通过未修改原厂 libudd ARM 函数向合成寄存器 RAM 的三路字节核对；实际相机会话仍在等待原厂调用。LUT 启用与 Gamma 曲线存在联动，输入颜色域、重载和预览／JPEG 消费尚未验证。

2026-10-20：已接入原厂参数节点 Update 的 LUT 缓存失效处理，通过原厂虚函数路径验证换表和恢复。sRGB 风格增加 `P(stock3D)` 组合表，以保留原厂静态 LUT 与 Gamma 的配合；直接版仍用于其他原厂 source。11 个风格均已重建。静态照片参数链含 3D LUT，取景参数链不含同一节点；成像及取景适配仍未完成。

开发顺序：开源菜单／配方框架 → NX300M 成像与 LUT 后端 → 开源其余模块 → 自定义功能。实时 ISO 显示按用户修订取消；拨盘锁定、最快快门限制、ISO 步长与上限／扩展仍保留。
