# NX300M 救援可能性与功能部署边界

调研日期：2026-10-23；2026-10-24补查。范围为公开三星维修资料、上游维护者记录、项目内 NX300M GPL、已核验原厂1.15文件和当前九模块实现。救砖调研未访问实体相机或SD卡，未刷写、短接或运行恢复程序。

安装状态更正：2026-10-24已有MFW的 `autoexec.sh` 入口已实机执行本项目诊断，确认root身份、返回0及完成标记；准确MFW版本尚未读取。记录见 [MFW_ENTRY.md](MFW_ENTRY.md)，不为此再刷最小启动候选。本次未启动服务、加载SO或处理快照；已有MFW不补齐本页所缺的黑屏救援证据。

## 结论

NX 系列有维修级 SD 恢复设计，NX300 手册明确记录其操作；NX300M GPL 也包含专用 downloader 分支，实际1.15 bootloader 中确认了串口打断入口。但目前没有核验到可直接用于这台 NX300M 的完整恢复组合：适配的恢复程序、准确接点与电平、恢复触发过程和成功记录。因此可以继续准备救援路线，不能把它当成已验证的安装后兜底。

当前已实现功能没有一项被证明必须修改 kernel 或 bootloader。需要区分三件事：

- 独立运行相机程序：文件服务、部分遥控和 Quick Share 协议等需要执行权限与网络，但功能本身不要求持久修改固件。
- 介入原厂 app：机内 LUT、配方、曝光策略、相册分享入口等需要进程内模块；内存 hook 不等于改写磁盘中的原厂二进制。
- 自动持久加载：当前候选将九个 SO 和包装器写入 rootfs，并改两个启动入口。这是持久修改机内系统，即使不通过 SLP 刷写，也不能称为“完全不改固件”。原厂快照还需要另行处理。

原厂1.15的临时执行入口与安全 app 重启仍未打通；上面的技术拆分不代表现已能免刷安装。

## 救援路线与适用条件

| 相机还剩什么能力 | 可以考虑的方法 | 已有依据与缺口 |
| --- | --- | --- |
| 原厂界面和固件菜单可用 | 按三星对应型号更新流程恢复未修改固件 | 官方有1.15原包和更新指南；已查代码允许跳过同版本比较，实体重装仍未验证。恢复出厂设置只重置设置，不能用来撤销 rootfs 文件修改 |
| 相机界面异常，但已有独立 shell 可用 | 停止服务，结束仍使用模块的进程，恢复原启动文件及元数据，随后处理新增文件和旧快照 | 当前候选已保留两份原厂启动文件。仅运行 stop.sh 不会卸载 app 内 SO；这一路线取决于故障后 shell 仍可用，不能等到黑屏后才假定能开 telnet |
| Linux或相机app无法正常运行，早期加载链仍可工作 | 维修级 SD downloader 恢复；或经串口进入 bootloader 后进一步诊断 | NX300手册有SD恢复操作，NX300M源码有专用downloader；实际1.15有串口打断分支。缺专用恢复产物、接点、电平与实体操作证据 |
| 早期加载器、NAND布局或硬件本身受损 | 专业板级维修；外部读取/编程是否可行需具体硬件调查 | 没有确认NX300M具备可绕过已损坏IPL的通用USB/BootROM救援。不能承诺插USB即可恢复，也不能用别台整片镜像替代本机校准数据 |

### NX300 手册实际记载了什么

三星 NX300 Service Manual 的7-4节、印刷页7-9（PDF第68页）给出主板恢复流程：小容量SD、固件文件和专用 `dnloader.bin`，拆后盖、连接排线，在指定TP短接时上电，以LED状态判断恢复进度。已下载并渲染核对示意图；它标注的是 **NX300主板**，不作为NX300M短接位置说明。

- [原手册镜像](https://czips.pl/wp-content/uploads/2019/05/samsung-nx300-instrukcja-serwisowa.pdf)
- [本地原PDF](../outputs/deployment/recovery-research/NX300-service-manual.pdf)
- [已核对的恢复页](../outputs/deployment/recovery-research/NX300-recovery-page.png)

手册声明其规定流程保留调整数据；这不能外推成任意擦写、错误型号loader或整片NAND覆盖均保留校准。

上游维护者在 [issue #16的回复](https://github.com/ge0rg/samsung-nx-hacks/issues/16#issuecomment-499630879) 提供的是 **NX300-1.4 GPL中的bootloader目录**。这是恢复程序来源线索，不是已验证的NX300M救援二进制，也不能因为issue已关闭就认定用户救砖成功。

### NX300M 原厂与源码证据

1. 本地GPL `bootloader/usr/Main.c` 中 `CONFIG_DNLOADER` 有NX300M型号判断和 `nx300m.bin` 路径；它会进入NAND写入流程，不是只读启动器。不能为了“看看能否进入”而运行未知loader。
2. `Makefile.dnloader` 第7、15、21–22行有目标和NX300M构建分支，依赖列表所列源文件存在于GPL归档。尚未构建或验证工具链、输出格式及BootROM接受条件；有源码不等于已有可用恢复产物。本轮没有发现足以断言“缺专有源码所以无法构建”的证据。
3. 实际1.15 `D4_PNLBL.bin` 的分区相对 `0x9d50–0x9d80` 存在UART轮询：收到 `0x0d` 时，`0x9d70` 调 `0xb398`；未命中则在 `0x9d88` 进入启动函数 `0x9720`。分区在原厂BIN偏移 `0x4f5e04`，大小 `0xf85c`。这是实际代码证据，不只是日志字符串。
4. 仍缺NX300M物理UART接点、电平、可操作时间窗口及控制台后续可用命令的实体确认。不能据地址就给出TTL接线方案。上游 [UART调查](https://github.com/ge0rg/samsung-nx-hacks/issues/7) 中USB电阻切换也没有得到串口输出，不是现成USB救援线方案。
5. 不能把普通PNLBL里被条件编译排除的更新分支当作零售机可用菜单，或把维修TP入口直接断言为不可破坏的BootROM功能。

如果故障仅来自快照内容，而kernel/rootfs正常，经已确认的控制台选择冷启动有恢复可能；这是条件性判断。GPL `usr/Main.c:1151–1154` 在部分坏块错误下会直接停机，发生在cold-mode处理之前，所以不能把冷启动视为覆盖所有NAND故障的方案。`cmm/daisy_dnloader_ES.cmm` 描述的是TRACE32/JTAG装载程序，也不能当作BootROM自动读取SD的证据。

本轮保存的 [UART反汇编](../outputs/deployment/recovery-research/stock-1.15-uart-entry.asm) 和 [源码/二进制追踪记录](../outputs/deployment/recovery-research/report.json) 给出精确地址、GPL归档位置及行号。没有构建或运行downloader。

已知原厂升级用 `vImage` 内含 `check_card` 与 `fw_upgrade nx300m.bin` 的环境，说明存在独立升级阶段；但“有升级环境”不能证明用户在相机无法启动时一定能进入。正常菜单更新、生产USB命令、UART控制台、主板TP恢复是不同入口。

官方原包与正常更新流程来源：[三星NX300M支持页](https://www.samsung.com/it/support/model/EV-NX300MBSVDE/)。本地原厂包身份与完整性记录为 `work/stock-1.15/source.json`。公开检索没有取得已核验的NX300M专用维修恢复套件；这是本轮范围内的缺口，不是证明其不存在。

### 2026-10-24：同版本更新的静态核对

原厂 1.15 的正常菜单路径没有把同版本更新直接排除。实际 app 的六处直接 `UI_Is_Body_Fw_Available(bool)` 调用均传入 `true`；该函数在 `0x1d3d54–0x1d3d64` 因此跳过版本比较，返回文件校验结果。已查菜单事件在 `0x1d6a8c` 执行同样检查，随后进入 `UI_Manage_FW_Upgrade_Power_On`。

后者在 `0x1d5e84` 起检查 UI 项 `0x3ec > 3` 才继续；该项与电量有关，但本轮没有核对其枚举，不能将阈值换算为百分比。idle 路径再次检查固件可用性，并在 `0x1d6050`／`0x1d60bc` 执行启用 CRC 的 `fw_validate`。通过后显示 UI 项 `0x73` 并继续安排 idle，最终在 `0x1d6454` 调 `fw_start_upgrade`；已追踪的库函数路径再次校验、读取启动镜像表并准备升级，没有发现同版本拒绝分支。这里没有确认一个必然可撤销的后续确认页：触发这条菜单动作可能继续进入升级，不能把“点击更新看看”当只读检测。

这说明：**在原厂菜单仍可操作、文件与电量等前提满足时，用未修改的原厂 1.15 重刷在所查代码路径上可行。** 不等于实体升级或故障后的救援已经成功，也不能覆盖菜单本身无法启动的情况。反汇编材料位于 `outputs/deployment/version-review/app-validation.asm`、`library-validation.asm`。

当前另行构建只恢复 `libmisc.so:start_script` 的[最小启动候选](MINIMAL_START.md)，没有复用旧 MFW 或加入曝光模块。它仍是完整 SLP 固件包；其他分区内容保持原样，不代表升级过程只写一个文件。没有向本次连接的相机复制或刷入该候选。

### 2026-10-24：黑屏救援资料复核

**当前决定：先不刷最小启动候选。** 正常菜单重刷有静态依据，但菜单无法启动后的NX300M恢复链仍不完整，不能作为已具备的兜底。

三星对应型号[官方支持页](https://www.samsung.com/ae/support/model/EV-NX300MBSTSA/)列出1.15和升级指南。已读取[原厂升级指南镜像](https://nxfiles.ydns.eu/files/NX300M/FW_upgrade_guide_i-Launcher_ENG.pdf)：它描述正常界面更新及中断后重试，没有给出黑屏后强制进入恢复的步骤。指南的“可重试”不证明界面与启动链损坏后仍能恢复。实际代码还有自动继续安排升级的路径，因此不以点击更新作为只读检测。

公开来源复核保存在[简明报告](../outputs/deployment/recovery-research/public-source-check.md)和[原始读取记录](../outputs/deployment/recovery-research/public-source-check.json)。已查NXFiles实际目录及相关上游讨论，仍未取得NX300M专用维修手册、已知可用的恢复loader、准确TP/UART接点与电平，或完整成功记录。这里是检索范围内的缺口，并非断言不存在恢复方法。

需要排除几个容易误判的来源：

- [issue #23](https://github.com/ge0rg/samsung-nx-hacks/issues/23)标题涉及NX300M，但评论里的砖机是NX2000，维护者给出的三角TP说明针对NX300；不是NX300M恢复案例。
- [issue #16](https://github.com/ge0rg/samsung-nx-hacks/issues/16)提供NX300-1.4源码位置，没有NX300M恢复成功跟进。
- [NX300M资料目录](https://nxfiles.nx.tc/files/NX300M/)及其Manual子目录列普通用户手册、源码和固件；NX300的独立Service-Manual目录不能移作NX300M证据。
- [两机型拆机说明](https://www.ifixit.com/Guide/Samsung+NX300+and+NX300M+Back+Panel+Assembly+Replacement/36896)确认NX300M有额外螺丝，但没有定义维修触点或UART电平。拆机图不能补齐恢复接线。

NX300M GPL中的`do_dnloader`也不是可以先试运行的诊断环境：成功从固件取出升级镜像后，会执行`nand_EraseBlocks(0, 1)`并更新镜像信息，再进入升级kernel；备用`ROM.txt`路径直接写IPL、PNLBL等镜像。把原厂固件改名成`dnloader.bin`不能由此得到可用loader。保留eFuse字段或坏块信息也不等于证明保留相机调整数据；NX300手册的保留承诺只适用于其规定恢复流程。

实际1.15的UART shell中进一步确认了`boot`命令分派与参数0的冷启动含义：命令表项指向`0xb2c4`，参数处理在`0xb308–0xb310`调用`do_boot_kernel@0x9720`。这仍不是已验证的实体操作步骤；准确接点、电平及进入时序未核实。反汇编见[命令分派](../outputs/deployment/recovery-research/stock-1.15-shell-dispatch.asm)，源码与二进制的完整核对见[downloader/冷启动报告](../outputs/deployment/recovery-research/dnloader-analysis.md)。

UART冷启动最多能针对坏休眠快照：kernel/rootfs及必要读取仍须正常，冷启动不会还原被修改的`libmisc.so`。选择冷启动本身没有擦除快照，但后继Linux/app并非只读运行。独立的`hib2.sh`含擦除快照、修改状态和重新生成快照等写操作，不作为只读探针运行。尚未核验零售NX300M有Odin、EDL或通用USB强制恢复模式；手机恢复工具和JTAG开发脚本不能补上这一证明。

继续推进救援准备需要的具体证据是：**匹配NX300M主板版本的触发点与电平、来源清楚且可用的loader、完整恢复日志，以及本机调整/校准数据的保存与恢复办法。** 在此之前，离线代码分析与App开发可以继续，实体持久安装继续等待恢复路线核实。

## 我们各项功能究竟需要什么

| 功能 | 当前实现依赖 | 持久修改是否必要 |
| --- | --- | --- |
| One UI手机界面、相册排版/动画、EXIF与等效焦距、收藏、手机编辑 | Android本地代码；访问相机照片需有文件通道 | 不需要改相机固件 |
| LR/理光配置解析、转换、增加LUT/API | 电脑端工具与数据文件 | 转换和存放不需要；在机内成像生效另见下项 |
| 文件列表、RAW+JPEG配对、内嵌预览、下载、JPEG回写和原图恢复 | 独立ARM remote读取/写入SD | 不要求持久修改；必须先有可用执行入口，写照片还需要相应访问权限 |
| Quick Share发现、PIN、加密发送、取消 | 独立ARM CLI，由remote派生；当前原型需已有可用LAN，未满足免路由器直连要求 | 协议本身不要求改kernel/rootfs；设备发现、初始建链、Wi-Fi通道协商及网络恢复仍待接通 |
| 把原厂相册分享改成Quick Share，带入单张/多选，保留返回选择 | `gallery-share.so`修改原厂app的RAM函数入口并查询真实相册对象 | 必须app内hook；不必然要求改磁盘原厂程序，但当前候选通过rootfs持久加载 |
| 遥控按键/触摸、模式/电量档位/充电读取、原厂X11界面截图 | remote的XTest、sysfs读取和XGetImage | 可以独立运行，不必注入app |
| 包含视频层的完整LCD传输 | `lcd-capture.so`观察原厂ioctl/视频提交，再与X11界面合成 | 当前实现需要app内模块；尚不能据实验室画面证明实体传感器取景已通过 |
| LUT真正进入机内成像；配方RGB、色相、饱和度、锐度、对比度 | `lut_port.so`、`color-style.so`介入原厂IPCS/IPCM和参数节点 | 当前实现必须app内hook，不需要改kernel；真实ISP颜色仍需实机核对 |
| 手机设置ISO步长、扩展、Auto ISO上限 | `exposure_controls.so`在app Ecore线程调用原厂Set/GetUserData | 当前桥需要app内模块；原厂本就支持的设置并不因此变成新固件算法 |
| Auto ISO上限防回弹、最快快门限制、拨盘锁、拨盘动画、Custom面板入口、软件切模式 | `camera_firmware_mod.so`、`shutter_limit.so`、`ui_mod.so`等进程内hook | 必须介入app；没有已确认改kernel/bootloader的需要。ISO上限不等于当前曝光ISO，快门限制也不是扩大硬件能力 |
| 服务开机自启、SD挂载协调 | 当前 `service-loader.so`经app Ecore调用SD脚本 | 当前加载链需要持久入口；若后续有独立可靠启动入口，可替换这一机制 |
| 跨休眠继续工作、更新/卸载模块 | 原厂快照保存进程和映射，SO还需有稳定backing文件 | 不能只复制SD或改脚本；当前内置模块更新需要匹配的快照处理。实体恢复未验证 |
| Mod/固件管理页面 | 状态读取、效果切换、本地候选包/SLP检查 | 页面本身不要求改固件；当前没有远程刷写、rootfs安装或热卸载执行器 |

定时拍摄、B门任务、焦点包围、静音快门与视频规格等不在已完成能力中；它们仍需逐项确认原厂命令、镜头和硬件约束，不能因为NX500有类似项目就认定NX300M可以照搬。

### 代码依据

- `mod/metol/remote/remote.c`：XTest、X11截图、媒体服务、上传与备份恢复；`status_values.h`：真实节点读取；`screen.h`：X11-only、真实视频observer与实验室backend区分。
- `runtime/gallery_share.c`：对原厂函数字节核对，`mprotect`后修改进程入口；`runtime/lcd_capture.c`：`RTLD_NEXT`转发原始ioctl，不替换驱动。
- `runtime/lut_port.c`、`runtime/color_style.c`、`runtime/recipe_style.h`：真实参数/ISP hook和app IPC；外部recipe engine并不自行完成机内成像。
- `runtime/exposure_controls.c`、`runtime/shutter_limit.c`、`runtime/camera_firmware_mod.c`、`runtime/ui_mod.c`：原厂设置桥、AE边界与界面入口控制。
- `mod/metol/remote/mod_manager.h`：`remote_install_supported=false`、`hot_unload_supported=false`；Android `ModManagerActivity.kt`与`InstallPackageInspector.kt`只做本地包检查。
- `outputs/deployment/install-candidate/rootfs/usr/lib/nx300m-mod/preload.sh`：SO均从内置 `/usr/lib/nx300m-mod` 按组加载；SD存资源和可选的加载配置、停用标记，`rootfs-report.json`列出实际增量。
- [APP_LOADER.md](APP_LOADER.md)、[QUICKSHARE.md](QUICKSHARE.md)、[FIRMWARE_ENTRY_CONTROLS.md](FIRMWARE_ENTRY_CONTROLS.md)：启动/快照、联网未完成项和运行时hook的具体边界。

## 当前候选为什么仍有启动风险

它没有改kernel或bootloader。新候选默认 stock，按 [LAUNCH_PROFILES.md](LAUNCH_PROFILES.md) 分组启用，full 才在app启动前一次预载九个SO。helper会在停用标记存在、配置未知或选中组模块缺失、不可读、为空时保留原命令和环境；**文件齐全而构造函数崩溃、挂起、接口行为不匹配，不在这项回退保障内**。原厂界面依赖app，坏hook可能同时让菜单更新入口不可用。

内置配置选中某组时，SD没有资源也可加载该组；拔SD不能撤销已有映射。直接恢复旧休眠快照不会重新执行启动包装器，因此停用文件无法覆盖已经保存的坏映射。新 service-loader 会在定时器中检查停用标记，抑制后续服务启动；旧快照里的旧代码不会自动获得此检查。服务退出、恢复原厂配方与卸载SO是三种不同操作。

## 后续部署建议

先建立并验证不依赖相机app界面的诊断/恢复通道，保留本机配置和校准数据，再试独立服务。随后分组验证单项app hook，最后才决定是否持久写rootfs及如何处理快照。没有必要为现有功能修改bootloader或替换内核。

如果始终找不到原厂1.15临时执行入口，完整机内功能的现实选项仍是经过验证的最小持久加载改动；不能只改手机App绕过这个前提。旧MFW恢复autoexec的做法属于参考，不会因本次调研自动改为采用旧固件。

本轮没有执行实机动作或生成自动刷写脚本。本次交付是有来源和代码依据的可行性调查，不是已验证的NX300M救援操作包。
