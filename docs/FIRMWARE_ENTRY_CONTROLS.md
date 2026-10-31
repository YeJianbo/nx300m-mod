# NX300M 原厂程序入口控制

2026-10-25 用户选择自定义按键作为主入口。当前独立 MFW 服务包只含 `mfw-panel.sh`，没有加载本模块，也不满足完整 `panel.sh settings` 的资源要求；因此服务包更新与实体 Custom 启用是两项工作，不能把前者视为后者完成。本轮未向实体加载 SO、重启原厂程序或更改休眠快照。

`camera_firmware_mod.so` 通过 `run_stock.py --firmware-mod` 显式加载。动画两处入口均匹配时默认瞬切；Custom 入口完整字节匹配后默认启用；拨盘锁、ISO 锁默认关闭。测光使用原厂实现，最快快门只由独立的 `shutter_limit.so` 管理，默认关闭。实机部署由启动器决定是否加载；本工具不刷机，也不修改磁盘中的原厂可执行文件。候选默认 `stock` 组不加载本模块，因此也不改变原厂按键或动画。

构建读取 NX300M 1.15 原厂程序及 `libcapture-fw-slpcam-nx300.so`，核对五个函数的地址、大小和 ARM 前导指令，生成 `outputs/controls/camera_firmware_mod_stock.h`。加载时再次比较完整函数字节，匹配才创建原函数 trampoline 并替换进程中的前八字节；不匹配的功能保持原厂路径，启用请求返回 `-110`。修改在构造期进行，Ecore 循环中处理后续控制。

- 物理模式拨盘：过滤 `UI_Event_ModeDial_Manager`，与已有指令拨轮锁分开；软件切档不经过该过滤器。
- 拨盘动画：`0` 原厂；`1` 保留原厂绘制对象并直达最终位置、中心图和末帧；`2` 清除拨盘覆盖层。原厂定时器在后两档停止。动画 start 和 draw 两处都成功匹配后默认 `1`；未完整匹配时保持 `0`。
- Custom：接管原厂取景状态的自定义按键入口，启动当前完整 Mod 的 `panel.sh settings`；子进程清除预加载变量并关闭相机文件描述符。诊断入口与实际按键共用 launcher 路径、脚本可读和 UI 可执行检查。面板沿用自己的文件锁。缺少安装文件时保留原厂按键处理，`--custom-settings 0` 可恢复原厂行为。不全局抓取回放删除键。
- Auto ISO 防回弹：启用时经原厂 Set 设置 6400，并用未修改的 Get 读回 `0x003b000f`；后续写入其他上限时仍调用原厂 Set 写入 6400。解除锁定后可正常修改上限，不伪造读回。
- 无镜头无线入口候选：只有启动时显式设置 `NX300M_MANUAL_LENS_WIFI=1`（启动器 `--firmware-mod --manual-lens-wifi`）才核对完整原厂无线入口，再将 `0x1cccb8` 的 BEQ 改为 BLS，允许 Lensconnection 0／1 继续检查；入口掩码 bit 32 表示已应用。其余状态和门禁保留，尚未验证 RVF／实机。颜色补偿由另一个默认不加载的候选处理，见 [手动镜头](MANUAL_LENSES.md)。

例如：

```powershell
D:/conda/envs/nx300m-mod/python.exe -X utf8 tools/camera_firmware_mod.py --session <session.json> --lock-mode-dial 1 --dial-animation 1 --custom-settings 1 --auto-iso-lock 6400
```

选定会话的独立 IPC 使用 `nx300m-firmware-mod-{ready,request,response}`，响应为 26 个整数；前 13 项保留旧版布局，已移除的实验 AE 字段固定为零，后 13 项依次为物理拨盘锁、动画档、Custom 开关、ISO 锁值、入口匹配掩码、物理事件数、拦截数、动画调用数、动画抑制数、Custom 调用数、面板启动数、ISO 回弹拦截数、原厂 ISO 上限读回。请求检查编号、过期时间、操作和取值；软件切档等待原厂当前模式读回。

旧操作 2（实验测光）和 3（重复的 Tv 限制）只接受关闭值 `0`，非零返回 `-101`，本库不再导出 `AeGetDeltaEVMulti` 或 `calcTvMaxCapture`。SD 菜单不再提供实验测光入口。`--metering-plus 0` 可用于关闭已运行的旧模块；更新磁盘文件不会替换已映射的旧库。本变更排除了本库对 AE 的重复干预，不代表已经验证实体光学曝光；独立快门限制仍需实机测试。

主动合成原厂事件的诊断操作 8 默认返回不支持 `-101`；只有进程启动时显式设置
`NX300M_FIRMWARE_ENTRY_DIAGNOSTICS=1` 才允许执行。实验启动器对应参数为
`--firmware-mod --firmware-entry-diagnostics`，正常部署无需开启该参数。

2026-10-25 的当前入口代码已完成 ARM 构建（17,712 字节），共享解析、参数、环境清洗和入口失配回退的定向检查通过。`tools/probe_firmware_mod_runtime.py` 在 55 秒独立原厂会话中确认初始 Custom 已开启，直接调用原厂 Custom 入口即可打开 live 设置面板，菜单操作及半按退出通过，最后 IPC 关闭成功。当前报告为 `outputs/firmware-runtime/firmware-mod-panel-custom/advanced-control-results.json`；模块来自本轮构建，面板使用此前完整 bundle，未据此验收新版 MFW 面板或实体按键。

`tools/probe_firmware_mod_runtime.py` 在限时独立原厂会话显式开启诊断并执行入口验证。
此前通过记录为
`outputs/firmware-runtime/firmware-mod-panel-live/advanced-control-results.json`，
包括四项入口、真实 ARM Custom 设置面板、菜单物理拨盘锁开→关和 S1 半按退出。
同目录保留本次启动日志；`firmware-mod-panel-final` 保留修复前锁文件权限失败证据。
原生面板验证使用 `tools/lab_panel_runtime.py`，仅在该命名空间添加 QEMU 解释器桥，
原始面板二进制不变，不注册全局 binfmt。该验证不代表物理触点、屏幕和实际曝光
已经在相机硬件上测量。运行时控制目前不跨关机保存。

实验室 QEMU user 需要在所选私有 mount namespace 中、guest `/tmp` 已挂载后执行：

```sh
python3 tools/lab_panel_runtime.py --guest /opt/nx300m-lab/stock-1.15 --bundle /path/to/sd-bundle/nx300m-mod
```

工具保留当前 `/mod` 的资源和 `remote`，覆盖完整包中的同名文件，并将可写 `.state`
归属 UID 65534；仅为当前私有复制创建 ARM 解释器包装。私有 `panel.sh` 只接受本
PID 命名空间中固定的 firmware owner，逐次核对 QEMU 可执行文件及独立参数
`/usr/bin/di-camera-app-nx300`；生产脚本保持原文。`/bin` 保留原厂独立程序、
脚本及其原始字节，不将其误当作 BusyBox applet。相机进程应使用
`NX300M_MOD_ROOT=/mod`；候选 SD 包和原厂展开文件不会被改写。
