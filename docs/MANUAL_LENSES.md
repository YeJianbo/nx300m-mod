# 手动镜头：无镜头释放、无线入口与换镜头偏色

2026-10-31 新调查入口：[手动 RAM 调查与修复候选](MANUAL_LENS_CS_RAM.md)、[原厂事件调用链](MANUAL_LENS_EVENT_CHAIN.md)。新的只读探针包含电子镜头状态，GPIO 仅作诊断；新候选不复用下文启动 SO 的自动 ready override。实机 RAM 变更暂未开放，三状态取证、PP 串行化和硬件完成仍待确认，不能将以下旧构建记录当作泛绿已修复。

2026-10-27。本文对应 NX300M 1.15 原厂程序。无镜头释放已实机开启并确认 S 挡可拍；无线内存补丁及开机有限校验已在实体核对读回，用户反馈无线入口测试“可以了”。App服务的合并启动版已重新写卡，待实机连接验证。偏绿 SO 尚未在机身加载。

后续用户报告手机关联 Wi-Fi 后 App 仍无法连接。已确认此前无线专用根入口没有启动 NXConnect 的 HTTP／发现服务，不能把无线入口通过当作 App 连接完成；现转为[合并启动修复](CONNECTED_BOOT.md)。以下各卡的无线专用部署记录保留为历史，当前实际根入口以合并包部署报告为准。

同日使用 FAT32 调试卡（`LOCAL_REDACTED`）。当前根入口已从有限只读采样切换为下面的无线内存校验；已启用明确的开机 apply 配置，每个新的 RAM 会话至多执行一次，不加载颜色 SO，也不重复修改无镜头释放。旧诊断记录见 [调试卡说明](MANUAL_LENS_DEBUG_CARD.md)。

2026-10-27 实体回收已读到 IQ／PP：三次 GPIO88=0，IQ ready=0、镜头数组为空。用户随后明确已取掉纸片；开关闭合仍不能当作电子镜头识别。颜色候选已改为按电子镜头服务和有效 IQ 校正参数判断，不再要求机械开关松开。此轮没有确认发绿的唯一原因。CS 源在 `/dev/d4_bma`，外部读取 EIO，不据此断言表为空或损坏。原厂进程未加载项目 SO；仅更换 SD 脚本不会使候选进入已有原厂进程。

## 换镜头后泛绿

用户关联会话记录了：关机从电子 NX 镜头换成手动镜头后出现左右绿／紫色偏，拔电池重新开机可消失，曾用纸压住机械检测开关。这个时间关系支持检查休眠／镜头状态残留，尚不足以确定具体补偿表就是唯一原因。

来源：[用户关联会话](chatgpt-conversation://6ad35f27-9c84-83ec-9a6f-b178c3c3c490)、[原始使用报告及评论](https://matteverglade.com/cameras/legacy-lenses-and-colour-problems/)。原报告也描述真实 Lens detached 事件后恢复，但其“RAW 已受到影响”是作者推断，不作为本项目的 SRW 像素测量结论。其他传感器故障、手动广角镜头的固有空间色偏仍需分别判断。

原厂静态证据：

- `CIQLensInfo::CallbackDetach()` 会通知 AE、释放并清理镜头数组，但不直接清除 PP 已上传的 CS 表。不能从 HTTP 线程随意调用它。
- `CPreProcessorD4::UpdateCsParam(bool)` 的 2D 路径在 ready 为零或 shading 为空时返回，不会自行重建无镜头默认表。
- 原厂取景设置经 `IQ_PP_SetCoreParam(1)` → `SetPPCoreForDraft()` → `InitCsParam()`。后者的无镜头 2D 分支生成原厂统一表并上传，仍使用原厂模式／capture gain，不硬编码所有模式的增益。
- `CLensInfoAppLayer::Reset()` 会触及持久镜头数据文件，因此未用它做“仅清运行状态”的复位。

### 已实现的修复候选

`runtime/manual_lens_cs.c` 只在进程启动时显式启用 `NX300M_MANUAL_LENS_CS=1`：

1. 完整核对两段 IQ 函数、电子镜头状态方法及其只读状态表，并核对已有 singleton 的位置和 capture gain getter。直接读取已有 `CLensOperation::m_pInstance` 后调用 `IsModuleReady()`，不调用可能懒构造镜头服务的 `GetInstance()`。
2. 在 `InitCsParam()` 的 `0x343534` 和 `UpdateCsParam()` 的 `0x344010`，仅当电子镜头服务 ready、IQ ready 非零且 shading 指针有效时沿用原厂镜头校正。其他情况只将本次调用的 ready 视为0，不以 GPIO 闭合认定电子镜头存在。
3. Init 走原有 `0x3433d0` 无镜头默认分支；Update 不再仅跳过更新，而是在当前 PP 调用线程内补调原厂 Init，生成并提交默认表，然后走原有返回分支。跳过的是旧镜头校正参数的使用，不是删除或重写持久镜头文件。
4. 以 PP 对象、表源、模式／几何参数和原厂 capture gain 缓存默认状态；相同状态不反复重建。原厂的 force 参数经常为1，不能作为缓存失效依据。重新出现有效电子镜头校正或原厂再次 Init 时缓存失效。
5. `0x3434b4` 新增结果桥接，记录本次匹配 PP 的 `pp_core_common_lut_init` 返回码，然后重放原 `mov r5,r0` 和 `add r0,sp,#0x7c`。只在本次返回0后缓存；旧 flags 的 bit4 不会被误用作成功回执。该返回值不证明后继 set_info／onoff 或实拍颜色全链路成功。
6. `flags&2` 表示仍有 LUT 操作待完成时先暂缓，不重写其 backing，也不添加可能阻塞的硬件等待。原厂无 ready 的 Update 会提前返回，pending 可能长期不解除；此时仍属未完成，不能报告颜色修复成功。

这没有释放共享镜头数组、伪造全局拆镜头事件、从后台线程补调 Init、关闭整个校准模块或修改校准存储。它不改风格 LUT、白平衡、RGB 调色或照片文件。两处 ready 桥接保留 r0–r12、LR、SP，只有原本被读取的 r3 使用有效 ready，CMP 重放后返回原 BEQ；结果桥接重放原厂 MOV／ADD 的寄存器变化。

边界：仅处理原厂 2D CS 路径；3D 路径保持原厂。补调默认表仅覆盖已审查的 PP 模式0..3；-1／4原本走关闭 CS 的分支，不产生上传回执，因此不补调，避免无缓存地反复重建。其余模式保留原厂。候选不清空所有镜头状态。无 ready 的 Update 必须实际执行到该 PP 路径且没有持续 pending 才能补建默认表；不保证仅恢复快照就立即处理。机械开关闭合不再阻止处理。真实拆换电子镜头时继续由原厂 Ready 回调完成初始化。

构建与隔离启动：

```powershell
D:/conda/envs/nx300m-mod/python.exe -X utf8 tools/build_manual_lens_cs.py
D:/conda/envs/nx300m-mod/python.exe -X utf8 tools/run_stock.py --program camera --preferences --display --manual-lens-cs --seconds 30 --output-dir outputs/firmware-runtime/manual-lens-cs
```

第二条是开发用独立原厂程序会话，不是实体安装步骤，不会把候选写到相机。模块位置为 `outputs/controls/manual-lens/manual-lens-cs.so`；当前 12,020 字节。`nx_manual_lens_cs_status(unsigned int[5])` 导出只读计数：安装掩码（全部三处为7）、Init 检查／默认分支数、Update 检查／默认分支数。`nx_manual_lens_cs_rebuild_status(unsigned int[2])` 返回本次 LUT 初始化返回0的次数和 pending 暂缓次数；不增加后台轮询。

验证：新版 ARM `-Wall -Wextra -Werror` 编译通过。`tools/probe_manual_lens_cs.py` 在独立 QEMU ARM 进程执行生产桥接和修复策略，19项桥接检查、13项修复序列检查通过，退出0、stderr为空。覆盖电子 ready／非 ready／实例缺失，陈旧或空 IQ ready、空 shading，结果桥接的寄存器／栈与原指令重放，不变状态复用、gain／几何变化、电子镜头恢复、DMA 暂缓、关闭模式不反复重建，以及失败 Init 保留旧 flags 时不得缓存。镜头状态与原厂 Init／硬件提交为夹具，不是实机色彩或完整原厂成像流程。报告为 `outputs/controls/manual-lens/cs-gate-probe.json`。本次首次构建因手写 getter 预期字节不匹配退出1；核对原厂实际指令后修正，最终构建通过。

下一步实体最小验证：在可靠的进程内加载方式就绪后，记录“原厂电子镜头关机→手动镜头开机”的初始化是否发生，对比同一场景 SRW＋JPEG 与拔电池冷启结果，再装回电子镜头确认恢复。当前未加载到机身、未拍照、未确认 RAW 与 JPEG 均消除色偏。模拟器没有真实传感器色彩输出，不能替代这一步。

无线外部写内存成功后，对颜色的同类简化做了静态检查：Init 的 `BEQ@libiq+0x34353c` 改为 B 可在下次初始化强制走默认表；Update 的 `BEQ@+0x344018` 改为 B 只会提前返回，无法主动重建已上传的旧表。当前实机 IQ ready=0，原厂 Init 本来就会选择默认分支；仍缺实际执行初始化的证据。自然 Init 调用点为 Draft `0x3471cc`、Still `0x347164`、Still_M2M `0x3470f8`，未确认具体机身操作必定触发。此简化也需显式还原才能恢复电子镜头校正，未采用或写卡。不能把无线两字节方案直接当作偏绿修复。

## 无镜头释放

`mod/deployment/manual_lens_release.sh` 使用原厂 `prefman` 读写 `APPPREF_B_ENABLE_NO_LENS_RELEASE`，组 1、偏移 `0x30c`、long，写后读取同一字段确认。原厂 UI 的 getter/setter 直接读取／修改该共享偏好，不需伪造镜头连接。

```sh
/bin/sh manual_lens_release.sh status
/bin/sh manual_lens_release.sh enable
/bin/sh manual_lens_release.sh disable
```

脚本默认只读；不调用 reset、init、save，不绕过 AF、蓄能、存储及其他快门许可。共享偏好是否被之后的原厂保存动作持久化不能保证。v4 已在实体执行 `status` 并读到0。v5 在与 RUN_ID 匹配的显式一次性请求存在时执行 `enable` 并读回，成功后删除请求。round-05 实机日志确认 `previous=0`、`no_lens_release=1`、`readback=verified`，请求已消耗；用户确认能拍。回收 SAM_5065／5066 的 RAW＋JPEG，EXIF 为 S 挡、1/125 秒。没有接入常驻服务或面板，未据此判定偏绿修复或无线可用。

## 无镜头无线入口

`camera_firmware_mod.so` 支持独立 opt-in `NX300M_MANUAL_LENS_WIFI=1`；启动器对应 `--firmware-mod --manual-lens-wifi`。核对完整 `UI_Check_WiFi_ModeChange_Enable(int)` 后，把 `0x1cccb8` 的 BEQ 改为 BLS（Lensconnection 0／1都继续检查），把 `0x1ccd08` 的 BEQ 改为B（仅跳过Kmount提示及拒绝块），保留其他原厂门禁。

它不全局伪造 attached，不吞掉 RVF 拆镜头事件，不绕过 Lenslock、存储和忙状态，不修改 `Is_Kmount_Adaper_Equipped` 或其他地方的Kmount状态。Lensconnection 是原厂状态机字段，不能简单当作电子触点状态。当前 ARM 编译通过，但未运行原厂无线流程或实机验证，也未进入实体服务包。

已拆出 `manual-lens-wifi.so`（`tools/build_manual_lens_wifi.py`，5324 字节），仅包含上述门禁修改，无曝光、拨盘、Custom、ISO、Ecore 和后台线程。它和 controls 模块复用 `runtime/manual_lens_wifi_patch.h` 及同一原厂字节生成器，避免两套修复漂移。仅启动时显式 opt-in；同样需要可靠的进程启动加载入口，目前未部署。只读 `nx_manual_lens_wifi_status()` 返回0未启用、1非相机进程、2原厂不符、3权限修改失败、4两处已安装；4不代表无线连接成功。两个 ARM 模块已构建通过，证据为 `outputs/controls/manual-lens/wifi-guard-evidence.json`。

原厂判断已确认：`0x1cccdc` 的 Lenslock 非零会拒绝（提示项 `0x5c`）；`0x1ccd04` 的 Kmount 判断非零也会拒绝（提示项 `0x61`）。UI 缓存分别为 `g_ui_variable_info+0x1fc/0x200/0x204`（Kmount／Lensconnection／Lenslock）；`Is_Kmount_Adaper_Equipped(false)` 会调用两个 driver ioctl 并更新缓存，不能作为无副作用 getter 调用。v4 实体三次均读到 connection=1、lock=0、kmount=1，对应原候选未覆盖的Kmount拒绝，现已补齐此入口分支。静态有界追查RVF与SoftAP回调未发现另一个直接Kmount门禁；真实拆镜头和锁定事件仍会退出RVF，保留原逻辑。底层驱动及实际联网链路尚未验证。

针对该实体回归执行 `tools/probe_manual_lens_wifi.py`：QEMU 与原厂旧 ARM libc 实际执行 `0x1cccb0..0x1ccd8f` 原厂尾段、共享 production 安装函数，8组输入×原厂／候选共16项通过，退出0。当前connection=1、kmount=1、lock=0在原厂拒绝0x61、候选放行；connection=0可放行，lock=1仍拒绝0x5c，非法connection仍拒绝0x62，后继限制仍拒绝6。前置存储／忙状态只做静态保留核对，没有在此片段测试中执行；没有据此认定真实联网成功。报告：`outputs/controls/manual-lens/wifi-gate-probe.json`。

### 加载方式与当前候选

#### SD 无线内存补丁（实体读回通过，开机校验版待启动）

独立于 SO 加载，`manual-lens-wifi-mem.arm` 从现有 MFW 脚本直接修补运行内存。只允许修改 Wi-Fi 两条 A32 分支的条件字节（指令地址+3）；不调用目标函数、不附加 ptrace、不暂停／重启 App。NX300M GPL 3.5源码中的 `fs/proc/base.c:mem_rw` → `access_remote_vm` → `mm/memory.c:__access_remote_vm` → ARM `copy_to_user_page` → `flush_ptrace_access` 有可执行页的缓存维护路径。源码不证明实装 kernel 完全相同或实体写权限已通过。

测试入口源为 `mod/deployment/mfw/autoexec-lens-wifi-mem.sh`，入口和内层 worker 的 shell语法检查均退出0。缺少配置时默认只读 status；`WIFI_MEM_PROFILE=manual-lens-wifi` 明确启用每个新 RAM 会话的有限 apply，`stock` 只读。与 `WIFI_MEM_RUN_ID` 匹配的一次性 `WIFI_MEM_REQUEST` 优先选择 apply／restore，执行前消耗请求，分别保留 before／action／after、动作来源和退出码。重复 apply 对已知完整补丁幂等，不重复写。无常驻轮询，也不加载颜色 SO。不能承诺关开机自动撤销运行内存修改。

工具现为 70,596 字节，ARM 编译、旧 libc 链接及 QEMU 隔离夹具退出0；报告为 `outputs/controls/manual-lens/wifi-mem-build-probe.json`。夹具覆盖 status 零写入、apply／restore 幂等、未知字节拒绝、短写及回退失败报告；其内存 IO 和进程身份为模拟，不是实体权限验证。完整14项原厂核验及1312字节函数比对仅放行两个已知条件字节的4种组合。

首次用 `tools/prepare_manual_lens_wifi_card.py --action apply --run-id wifi-mem-20261027-01` 写入 H:（序列LOCAL_REDACTED）。回收实体 session 的 before／action／after／worker 退出码均为0；action 的14项原厂核验通过，2字节由 `[10,10]` 改为 `[154,234]`，after_verified=true。独立 status 再次核验 `[154,234]` 且零写入，PID／start ticks 相同。用户在“手动镜头进入遥控取景器”测试后反馈“可以了”；没有据此扩大为手机配对或照片传输全链路验证。证据在 `outputs/deployment/manual-lens-debug/card-LOCAL_REDACTED/camera/wifi-mem-20261027-01/`，本次回收未写卡。

当前卡已更新到 `wifi-mem-20261027-02`，启用 `--repeat-on-boot`：工具、运行标识、manual-lens-wifi 配置、一次性请求和新根入口均已回读一致；工具二进制与实体通过的版本相同。备份和报告在 `outputs/deployment/manual-lens-debug/card-LOCAL_REDACTED/updates/wifi-mem-20261027-02/`。其他636个文件元数据未变，没有修改照片、内部文件或固件。包装入口变化已检查语法，尚未实机执行新入口；不重复运行未改变的 ARM 夹具。

用户随后指定写入当前插入的另一张64GB exFAT卡，H: 序列 `LOCAL_REDACTED`。已用 `--serial LOCAL_REDACTED --initialize --action apply --run-id wifi-mem-20261027-03 --repeat-on-boot` 安装相同无线修复和启动入口，5个文件均回读一致。该卡原入口与项目旧 services 模板完全一致，已备份至 `outputs/deployment/manual-lens-debug/card-LOCAL_REDACTED/updates/wifi-mem-20261027-03/before/autoexec.sh` 后替换；旧 Mod 文件保留，但当前根入口不再启动旧服务。其他400个文件元数据未变。本卡新一轮日志位置为 `nx300m-lens-debug/logs/wifi-mem-20261027-03/`；尚未在本卡执行，不混用上一张卡的实体结果。

后续最小实机操作：安全弹出读卡器，关机装卡、断开 USB 后开机，等待约15秒；确认手动镜头仍可进入 Wi-Fi → 遥控取景器。下次回收 `nx300m-lens-debug/logs/wifi-mem-20261027-02/` 下的 before／action／after JSON 和 session.txt。只有 action 成功且 after_verified=true、after=[154,234] 才能确认两处补丁读回；status 返回0也可能是合法的部分修改，不能单凭退出码判断全部安装。本轮不要求颜色对照或重复拍摄。连续启动的该目录只保留最近一次结果，需留证时先回收再启动。

撤回运行内存修改须以新 run-id 准备 `--action restore`，执行并确认 after=[10,10]；准备工具同时将开机配置写为 stock，防止后续重新应用。删除／改名根 autoexec 仅阻止后续执行，不能撤销已经修改的内存；快照可能保留私有页。两次单字节写入不是事务，超时或回退失败可能留下已知混合状态，须检查日志；不以强杀 App 或清快照恢复。

实装 MFW 的 `libmisc.so` 在 worker 中通过 `system()` 执行 autoexec，未提供进程内 SO 加载接口。在该 shell 设置 `LD_PRELOAD` 不影响已运行或由快照恢复的相机进程。round-05 仍无项目 SO 映射；此次“能拍、Wi-Fi 不行”不能当作已加载无线补丁后的失败。多轮相同 boot_id、PID 与 start ticks 也不能证明发生新 exec。

`tools/build_manual_lens_wifi_loader.py` 已生成 `outputs/controls/manual-lens/loader-candidate/`：复用已审查的 hib2 App 分派及 camera desktop 包装入口，配套独立 helper，仅新增加载 `manual-lens-wifi.so` 并设置对应 opt-in。候选默认 profile 为 `manual-lens-wifi`；`stock`、未知 profile、禁用标记或库缺失在下一次 exec 使用原命令。原有继承环境保留，不表示能卸载现有映射。三份脚本 shell 语法检查通过；未执行启动脚本。

该 overlay 不含曝光、Custom、颜色、服务模块，也不替换 `libmisc.so` 和原厂相机 ELF，因此可作为保留 MFW 文件的增量准备材料。它不是可运行的 SD 安装包，也未封装 SLP。将来合并前仍须核对实装的 `hib2.sh`／desktop 与旁置原件，不能覆盖未知 MFW 改动；还须确认真正新 exec／匹配快照的路径与恢复方式。尚未写入内部 rootfs、处理快照或验证实体无线，不应直接套用原有九模块完整固件。

另有 `tools/build_manual_lens_wifi_builtin.py` 生成 `outputs/controls/manual-lens/builtin-candidate/`，只修改原厂 ELF 中上述两条分支（实际2个字节不同），其余内容与布局保持。它不需 SO，但始终启用这两项改动，且来源为原厂1.15，不包含已装 MFW 的其他编辑。原文件旁置保留；同样未封装或部署，不能直接替换实装 App。两种候选互为替代，不叠加安装。
