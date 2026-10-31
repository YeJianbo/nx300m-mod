# 旧 NX300M Mod 曝光黑屏风险审计

日期：2026-10-23。检查范围为本地旧包、源码、ELF 和原厂反汇编；未运行旧 ARM 二进制、写设备或操作正在运行的模拟器。下文的 ZIP 内行号指归档成员的文本行号。

## 结论

**旧 V0 ZIP 存在可直接证实的活代码覆盖：实时 Auto ISO 显示补丁覆盖了原厂 Lock3A 会调用的 ISO 状态处理函数，并让原厂调用提前返回。** 这比单纯“显示文字错误”严重，足以列为曝光状态故障的重点嫌疑。另有快门补丁替换原厂动态边界、切换限制时不刷新当前 Tv 状态的风险。尚未取得当时相机实际安装版本、日志和曝光状态，不能把任一问题确定为用户所述“照片和取景全黑，调到约 1/60 后恢复”的唯一根因。

## 来源与版本区分

旧工作区根目录，下文记为 `OLD`：

```text
D:/codex/documents-codex/2026-08-16/nx300m-chatgpt-conversation-6a809ce6-5be0-83ec
```

| 对象 | 实际检查结果 |
| --- | --- |
| `OLD/work/nx300m-mfw-1.1.bin` | 333,237,352 字节，旧 MFW 原包仍在。 |
| `OLD/work/NX300M-MFW/README.md:1` | 上游说明其用途是恢复 `autoexec.sh` 执行；不是 V0 曝光补丁源码。 |
| `OLD/outputs/NX300M-Mod-V0.zip` | 40 个成员。内含原生注入器 **10,620 字节**；启动脚本仍启动菜单、拨盘锁和曝光控制器。 |
| `OLD/outputs/NX300M-Mod-V0/` | 后续修改过的展开目录。注入器 **13,540 字节**，与 ZIP 不同；`sdcard/autoexec.sh:32` 已明确禁用运行时补丁，等待黑屏根因核验。 |
| 展开目录 `tools/nxmod_remote_alloc.c` | 已移除 live ISO 路径，但目录内 ELF 仍有 `install_auto_iso_live_display`。不能把该源码视为目录内 ELF 的完整对应源码。 |

本次直接逐字节比较旧展开的原厂 1.15 与 MFW：`usr/bin/di-camera-app-nx300`、`usr/lib/libiq-fw-slpcam.so`、`usr/lib/libcapture-fw-slpcam-nx300.so` 相同；`bin/busybox`、`usr/lib/libmisc.so` 不同。原厂展开位于 `OLD/work/official-1.15-rootfs/128692023/rootdir`，MFW 展开位于 `OLD/work/mfw-1.1-rootfs/571445971/rootdir`。这将已确认的直接曝光代码破坏定位到 SD 上的 V0 注入层；没有据此全面排除 MFW 的其他行为。

## 1. 实际 ZIP 的 live ISO 补丁覆盖原厂状态函数

归档成员 `NX300M-Mod-V0/tools/nxmod_remote_alloc.c`：

- `:167–169`：`AUTO_ISO_LIVE_STUB=0x001bcbec`、`AUTO_ISO_LIVE_HOOK=0x00176c6c`、`ISO_ALWAYS_ON_STUB=0x001bce80`。
- `:202–244` 附近：live 代码数组；`:460–504` 的安装函数把该代码写入固定地址，并把 `0x176c6c` 改为跳转，另改 ISO 文本地址。
- `:676–679`：普通 `PID` 安装路径调用 live 安装函数，不要求用户单独打开显示选项。
- ZIP 内 `sdcard/autoexec.sh:25–30` 启动菜单控制器，控制器 `sdcard/NXMOD/bin/mode-dial-menu-patch.sh:45` 执行注入器普通 `PID` 路径；启动脚本 `:54–59` 另启动曝光控制器。

**不只依据源码：本次对 ZIP 内 10,620 字节 ELF 做了静态解析。**

- `main_raw` 地址 `0x206e4`，指令 `0x20b10: bl 0x217b8` 实际调用 `install_auto_iso_live_display`。
- 安装函数在 `0x21850` 比较循环计数 `0xa5`，即写入 165 个 32 位字，共 **660 字节**。`0x218dc` 的目标基址常量为 `0x1bcbec`，`0x218e4` 调用代码写入函数；函数尾部常量包括 `0x176c6c`、分支指令 `0xeb0117de` 与 `0x1bcbec`。
- ELF 的 `auto_iso_live_code` 数据与 ZIP 源码数组逐字节相同（660 字节）。首三条反汇编为 `ldr ip, =0x176c70; cmp lr, ip; bxne lr`。

原厂地址 `0x1bcbec` 并非空洞：原厂该范围 660 字节有 510 个非零字节，开头是正常栈帧代码。`OLD/work/di-camera-app-nx300.disasm.txt` 给出：

- `:353513`，`0x1bc18c`：`UI_Set_Lock3A_Info(eUI_LOCK3A_CB)` 调用 `0x1bcbec`，返回地址为 **`0x1bc190`**。
- `:354183–354209`，原 `0x1bcbec` 函数读取 ISO 相关 UI 值。
- `:354401–354421`，该函数调用 `UI_Set_State(24,1)`，并按 ISO 步长更新 `UI_Set_Pre_Value_Capture_Menu(26/27, ...)`。

覆盖后，上述原厂 Lock3A 调用的 LR 不等于 `0x176c70`，会立即 `bxne lr`，**跳过原来的 ISO 状态处理**。另一个 stub 地址 `0x1bce80` 也位于该原厂函数内部，不是专用代码区。可证实的是原厂处理被破坏；对曝光结果的最终影响尚无当时设备追踪。

该 live stub 本身意在读取数据并更新文字，未在所检查指令中直接写 Tv/Sv getter 返回值；但安装动作改写了活跃原厂代码，故不能称其为只读显示功能。ZIP 版本从 `0x1b4ae8` 读取捕获信息，并包含从调用者栈帧取值推算显示 ISO 的分支；后续展开目录 ELF 改为读取实时 AE S-value，不能将两者混为同一版本。

## 2. 快门边界替换与状态刷新缺失

ZIP 内源码 `tools/nxmod_remote_alloc.c:528–557`；展开目录源码同名文件 `:458–487`；另有 `OLD/work/nxmod_injector_menu6400.c:608–637`。ZIP 实际 ELF 的 `set_shutter_cap` 在 `0x20e28`，保留相同写入流程。

- 原厂 `UI_Get_Shutter_Speed_Low_Limit()`，地址 `0x169280`，被直接替换为返回固定 `1/denominator`。原函数有模式和闪光条件，并可返回 `1/180`，见原反汇编 `:267916` 起。
- `CApex::ChangeTv(int)` 的 `0x11f868`、`0x11f8bc`、`0x11f8cc` 原本读取对象 `+0x44` 的动态最快边界，被替换成固定表索引。没有取与原厂边界的较小值，因此在原厂限制更严的状态下可能扩大允许范围。
- 这三处均是 Tv 上界，不是 Nv/Sv。表索引并非 Tv16。本次直接读取原厂 `CApex::m_TvTable`（`0x251ecc`）：159 → 原始 `0x001e0001`（1/30），175 → `0x003c0001`（1/60），239 → `0x03e80001`（1/1000）。未发现这几项把表索引与 Tv16 混用。
- `set_shutter_cap` 只写代码，没有在限制切换时同步当前 Tv 索引 `this+0x30`、其镜像 `+0x78` 或执行 `updateUserData`。原厂 `ChangeTv` 的这些更新见反汇编 `:191044–191051` 和 `:191098–191110`。调动快门会重新进入原厂更新路径，因此“调动后恢复”与状态刷新问题相容；它仍只是症状解释。
- 控制器仅附加指定 PID 后逐条写入，没有在已读源码中发现暂停所有线程和成组回滚。这是补丁一致性风险，尚未观察到该竞态实际发生。

## 与当前方案的对照及剩余边界

当前方案独立于旧包：`shutter_limit.so` 在原厂 capture Tv getter 返回后取 `min(原厂上界, 用户上界)`；不覆盖旧 `0x1bcbec` ISO 状态函数，也不使用旧的 `ChangeTv` 三处固定立即数补丁。当前 `camera_firmware_mod.c` 已移除实验 `AeGetDeltaEVMulti` 和重复 capture Tv 导出，RPC 旧操作 2/3 非零请求返回 `-101`；动画仅在两个原厂入口完整匹配后默认瞬切。动画属于当前独立实现，本次旧 V0 的所查源码和脚本没有发现对应动画参数补丁。

下一步若要确认历史根因，最有价值的证据是当时实际部署的 SD 文件/备份，以及故障前后 ISO、Tv、Lock3A 状态和恢复动作记录。现有证据已经足够拒绝复用旧注入实现；不足以声称当前候选或实体相机不会出现黑屏。此次没有运行旧包、执行旧测试、完成实体复现或证明物理曝光正确。
