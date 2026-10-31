# NX300M 1.15 部署入口

**2026-10-24 实机状态：已有 MFW 的 `autoexec.sh` 入口及独立remote已运行，本机health、相机信息、状态、Mod状态、相册根目录五项HTTP查询全部返回成功。准确MFW版本尚未读取；当前沿用已有入口，不为脚本执行重复刷机。** 原始入口结果见 [MFW 诊断记录](MFW_ENTRY.md)。下文“原厂入口未打通”适用于未修改的原厂1.15；九模块尚未加载，不能视为已经安装或验证。

独立remote服务的 [MFW 实机记录](MFW_SERVICES.md)已取得：HTTP自检通过，读出NX300M/1.15、S档、电量档位4及DCIM下的104PHOTO目录。用户已确认手机App连接成功且能看到相册缩略图；原图下载、屏幕画面和按键控制仍待实机验证。此包不加载九模块，也不修改Wi-Fi配置。

当前开发安装候选包含九个原生模块和配套完整 SD 服务包，统一入口为 `outputs/deployment/install-candidate/` 及 `outputs/deployment/nx300m-mod-install-candidate.tar.gz`，由 `tools/build_install_candidate.py` 生成。目录同时保存 rootfs 增量、两份被修改启动文件的原厂恢复原文、最新 SD 归档与部署说明；具体成员以 `manifest.json` 为准。没有安装实体相机或重建快照。下文七模块、17 项 LUT、87 文件以及 UBIFS／SLP 大小均属于历史候选证据，不代表这份九模块候选已经封装或通过实体更新。

当前整合方案只有一条服务启动链：desktop包装器或`hib2.sh`通过共用helper按配置加载SO → 真实app的Ecore主循环注册 `service-loader.so` → 发现 `/mnt/mmc/nx300m-mod/start.sh` → 现有单实例remote监督脚本。九模块随包提供，新候选默认 `stock` 不增加项目 preload；按 [LAUNCH_PROFILES.md](LAUNCH_PROFILES.md) 分组启用和准备停用标记。服务加载器负责资源出现、有界启动重试和停用检查，remote的退出、拔卡和手动停止继续由原有脚本管理。下文 `libmisc:start_script` 恢复库是独立入口研究产物，没有混入该候选；真实冷启动与快照恢复证据见 [APP_LOADER.md](APP_LOADER.md)。

最新 SD 为 130 文件、19 项 LUT，产物字节以当前清单为准。此前独立 ARM VM 的原厂 app 完整加载、自动唯一 remote、重复启动、手动停止／重启均通过；生成 JPEG 的双上传、保留首次备份、原字节还原及截断体 HTTP 400 已验证，见 `outputs/deployment/app-loader/installed-vm/probe-report.json`。本轮分组策略、服务停用和状态接口修改已构建，分派及服务定时器验证通过，未重跑完整 app VM。物理相机、真实 SD 及休眠恢复尚未完成。

## 原厂启动与SD路径

救援路线、适用条件和各功能是否需要持久修改，见 [RECOVERY_AND_DEPLOYMENT.md](RECOVERY_AND_DEPLOYMENT.md)。NX300维修手册的SD恢复流程不能直接当作NX300M已验证救援；当前候选也未提供构造函数失败或坏快照时的自动回退保证。

### 首次实机接入顺序

当前九模块包尚无经过实体 NX300M 验证的一键安装和无法启动时的救援流程。首次接入先不覆盖 rootfs、不替换启动文件，也不执行 `hib.sh`／`hib2.sh`、快照擦除或生产调整命令。

1. 相机开机并连接电脑，运行 `tools/inspect_camera_usb.ps1`。它仅枚举 Windows 当前 PnP 记录，将设备与驱动信息写到电脑的 `outputs/deployment/camera-usb.json`，不发送相机协议请求、不安装驱动或向相机写入文件。Samsung USB／WPD 候选也可能是手机；名称匹配不能单独证明执行权限。
2. 确认原厂 1.15 上的临时执行入口。原厂 `start_script` 是空实现，普通 USB／MTP 连接和 SD 资源文件不等于 shell；现有文档中的 USB 生产接口仍有输入格式和副作用未确认，不能拿未知命令试机。当前没有据此交付可用的无刷机执行入口。
3. 取得经过核对的 shell 后，先记录进程、挂载和启动状态。首轮按下文“SD服务包”的手动服务方法进行，不停止或重启原厂相机 app，不设置其 `LD_PRELOAD`，不加载九模块。停止服务只撤销该服务；它不能证明机内 hook、实际成像或休眠恢复已验证。
4. 临时加载机内 hook 需要另外核对原厂 app 的退出、重启和休眠行为；不能因为文件放在 SD／RAM，就承诺断电或拔卡一定恢复。只有确认可操作的备份、停用与救援入口后，才考虑持久安装。

离线准备入口见 [OFFLINE_PREPARATION.md](OFFLINE_PREPARATION.md)。`tools/camera_readonly_preflight.sh` 供以后取得已核对的机身 shell 后使用，只向标准输出列出内核、挂载、启动文件是否存在、进程和已映射模块；当前未在相机执行，也不随自动启动链加载。它不能通过普通 MTP 自行取得 shell。

分析源为 `work/stock-1.15/rootfs.tar` 和 `nx300m.bin`。`source.json` 记录三星下载条目元数据匹配及完整ZIP条目CRC核验；构建时重新核对原厂BIN身份。

| 对象 | 已确认内容 | 部署含义 |
| --- | --- | --- |
| app | `/usr/bin/di-camera-app -> di-camera-app-nx300` | 名称含nx300的程序确属NX300M 1.15 |
| SD | `/sdcard -> /mnt/mmc`；fstab列出整卡和首分区的exfat/noauto挂载 | 原生资源目录以 `/mnt/mmc` 为基准；具体挂载完成事件仍需确认 |
| init | `/sbin/init -> ../bin/busybox`；TAR无 `/etc/inittab`，有历史 `~inittab` | 冷启动顺序还需结合内核初始根环境确认 |
| 启动脚本 | `go.sh` 运行X和app；`hib2.sh` 解析app链接后运行 | 原厂app入口明确，正常快启还涉及休眠恢复 |
| 数据区 | `rcS` 挂载 `/mnt/ubi1`、`/mnt/ubi2`，连接 `/pref`、`/adj` | SD资源、临时状态与原厂数据应按实际挂载安排 |
| snapshot | `rcS` 发现 `/mnt/ubi1/fw/snapshot` 时进入 `hib2.sh` | 新代码进入快启镜像的顺序需要核对 |
| app hook | app @0x179a98调用 `start_script` PLT @0x6bdac | 存在实际调用点，完整事件条件尚待追踪 |
| 原厂实现 | `libmisc.so:start_script` @0x1218直接返回0 | 本项目为该入口增加了独立的新实现 |

`rc.local` 是 `exit 0` 模板，在正常快启中的执行尚未证实。`hib2.sh` 包含mtd9擦除、swap和snapshot操作，本轮仅静态阅读。

## 已构建的最小恢复库

### 修改边界

`tools/build_stock_start_hook.py` 从原始TAR读取 `usr/lib/libmisc.so`，严格核对ELF32 ARM、原始入口8字节及原有段布局，然后生成独立输出。

| 位置 | 原内容 | 新内容 |
| --- | --- | --- |
| 文件偏移0x1218首4字节 | `00 00 a0 e3` | `a4 02 00 ea`，ARM `b 0x1cb0` |
| 0x121c | `bx lr` | 保留 |
| ELF程序头0x64、0x68 | RX文件/内存长度5520 | 7459 |
| 原文件末尾 | 长7340 | 补4字节对齐；0x1cb0追加20字节代码和固定命令字符串 |

原文件实际差异为 `[100,102)`、`[104,106)`、`[4632,4636)`，追加范围 `[7340,7459)`。构建检查确认其他原字节及全部动态符号保持一致，原有和新段的虚拟地址范围不重叠。

追加代码：

```asm
0x1cb0  push {r4, lr}
0x1cb4  add  r0, pc, #8
0x1cb8  bl   0xb20          ; 原厂 system@plt
0x1cbc  mov  r0, #0
0x1cc0  pop  {r4, pc}
```

固定后台命令：

```sh
test -f /mnt/mmc/nx300m-mod/start.sh && /bin/sh /mnt/mmc/nx300m-mod/start.sh >/dev/null 2>&1 &
```

该入口不读取输入，保持返回0和ARM调用约定。现有调用点没有建立专门参数；原始C头文件原型尚未取得。

回退可直接使用同目录保留的 `libmisc.stock.so`。构建还验证：截断追加部分、恢复入口首4字节和两个RX长度字段后，逐字节等于原厂成员。完整差异和恢复方法见 `build-report.json`。

### 隔离检查结果

执行：

```powershell
python -X utf8 tools/build_stock_start_hook.py --probe
```

probe使用新的tmpfs根目录、独立mount/network/IPC命名空间和uid/gid 65534；依赖库从原始TAR单独提取，没有借用运行根目录中的库。结果退出码0：

- 原厂 `ld-2.13.so`、`libc-2.13.so` 成功加载修改库；r4–r11和8字节栈对齐检查通过。
- SD资源目录中没有启动脚本时，调用返回0，没有生成脚本或调度标记。
- 放入受控fixture脚本后，单次调用生成恰好一行19字节调度标记。
- QEMU系统调用日志记录了真实 `clone` 和 `execve("/bin/sh",["sh","-c",固定命令])`；`system()` 没有被替代。

跨架构环境显式使用 `/bin/sh` 脚本桥，经 `qemu-arm-static` 启动原厂ARM BusyBox的sh applet，避免注册全局binfmt。实际脚本解释器来自原厂包；该桥是实验环境替代，机身的原生exec路径仍需实机验证。

证据：`start-hook/probe-report.json`、`probe.stdout.log`、`probe.stderr.log`、`runtime-provenance.json`。

### 服务接入与生命周期

这个hook在相机app运行后触发，调用下述SD启动器。启动器管理独立服务的生命周期；新后台进程与原厂 `stop_script` 状态无关。

LUT、曝光控制等依赖进程内符号拦截，需要经过验证的冷启动注入或进程内加载入口。子进程设置 `LD_PRELOAD` 不会改变已运行的app。

原厂 `UI_Camera_Quit` @0x1542e0 的已见逻辑只删除界面对象，完整捕获硬件清理尚未证实。`UI_Camera_Init` 又区分普通启动、播放参数、休眠数据和 `hib()` 路径，当前仍需确认安全的app重启方式。

## SD服务包

### 构建与目录

先使用现有工具生成ARM remote、网页和ARM Quick Share，并构建原生Metol面板及引擎，再打包：

```powershell
python -X utf8 tools/build_metol.py
python -X utf8 tools/build_sd_bundle.py
```

输出包括可复制目录 `outputs/deployment/sd-bundle/nx300m-mod/`、保留Unix执行权限的 `nx300m-mod-sd.tar.gz` 和文件清单 `build-report.json`。`--probe` 可另行运行已有服务生命周期检查，结果写入 `probe-report.json`。包内目录为：

```text
nx300m-mod/
  start.sh  stop.sh  common.sh  service.sh  panel.sh
  bin/nx-remote-controller.arm
  bin/nx-quickshare-armv7
  bin/nxui.arm
  bin/recipe_engine.arm
  bin/lut_engine.arm
  web_root/
  recipes.txt
  gui_ini.NX300M
  fonts/SDIC_GP_US_20120720.ttf
  fonts/fonts.conf.in  fonts/SOURCE.txt
  licenses/
  looks.txt
  looks/<id>/look.json
  looks/<id>/table.d4lut
  looks/<id>/table-stock3d.d4lut  # 有原厂3D表组合版本时
```

将包内 `nx300m-mod` 目录放在SD卡根目录，对应机身 `/mnt/mmc/nx300m-mod`。归档解包会设置脚本及二进制为0755、网页、配置、字体和LUT为0644；SD文件系统也必须允许执行。普通复制目录时应保留上述权限。包内包含按需运行的NXUI，但不含app hooks、实验室预览服务、GPIO替代或启动捕获程序。

`recipes.txt` 原样来自 `build_metol.py` 生成的68项七维配方表，供网页API和原生配方面板共用。三个面板ARM程序均由同一次Metol构建生成；构建器检查全部二进制的ARM ELF身份，并在报告中列出动态库需求。NXUI复用原厂 `libX11/libXft/libXrender/libc/librt`，不携带现代替代库或QEMU。字体从已有原厂1.15 TAR精确读取 `SDIC_GP_US_20120720.ttf`；配置只使用包内字体及状态目录缓存，不依赖实验室字体目录。

构建器从项目 `looks/` 读取现有17项LUT，校验ID、名称、4380字节表长、10-bit值和各平面padding后写入同一目录格式；包括用户指定的GR IV负片和正片Exact。`look.json`保留来源配置名称、色彩空间和未校准状态，去除本地LR文件路径；母版`.cube`仍在项目中。之后添加LUT可使用现有LookLibrary接口，重新构建即可更新包和原生API目录。实际选择需要相机进程已加载同根目录的LUT hook；仅复制表不会使其进入成像链。

原厂 `start_script` 仍是空实现；仅放入SD包不会获得自动执行。当前内置候选通过独立 `service-loader.so` 观察资源并启动脚本，无需替换 `libmisc`。它必须先随真实app加载；只有SD包、没有相机端加载入口时，仍需已有shell手动执行。候选的机身安装及完整启动链尚未验证。

### 手动启动与配置

在已具备机身shell且相机app/X运行的条件下：

```sh
/bin/sh /mnt/mmc/nx300m-mod/start.sh
/bin/sh /mnt/mmc/nx300m-mod/stop.sh
```

启动成功输出 `remote HTTP ready`，表示remote已连接X并成功绑定HTTP；镜像来源和可用能力以 `/api/v1/camera/info` 的实际返回为准。启动器不创建或伪装相机画面。

### 机内原生面板

在同一相机app/X会话中按需执行：

此处配方/LUT 面板保留 SD 执行及源资源 root，以匹配原厂 app 中的颜色与配方 hook。本轮 RAM 自动入口覆盖 remote、监管脚本、服务发起的 Quick Share 和图库发起的 Quick Share 面板；没有迁移这两个手动颜色面板，也没有迁移父 app 的 LUT root。

已有 RAM 服务环境调用 RAM `panel.sh` 时，脚本会核验 `NX300M_MOD_SOURCE_ROOT` 与 `NX300M_MOD_SOURCE_ID`，把 LUT catalogue/hook 根映射回对应源 SD；UI、engine、字体、cwd 和 state 仍使用 RAM。该兼容路径不表示全部手动入口或原厂 app 的 SD 引用已释放。专项结果见 `outputs/deployment/lut-data-root-probe/report.json`。

```sh
/bin/sh /mnt/mmc/nx300m-mod/panel.sh recipes
/bin/sh /mnt/mmc/nx300m-mod/panel.sh lut
```

`panel.sh` 在前台直接exec包内ARM NXUI，默认 `DISPLAY=:0`，显式传入同一mod根目录、配方／LUT表和对应ARM engine。它不启动remote或额外监督进程，也不使用QEMU。字体配置与缓存写入已有状态目录 `.state`；面板使用独立启动锁和NXUI原有单实例锁，已有面板时立即失败，不覆盖其LUT进入备份。查看器之外的硬件快捷键绑定尚未加入此脚本。

配方桥的ready PID属于当前真实camera时启用会话应用，否则明确保持浏览模式。LUT hook的PID和根目录均匹配时先运行engine `begin`记录进入前选择，再打开live面板；否则只浏览。两种模式均可添加 `--dry-run` 强制浏览。普通配方面板关闭保留当前已经应用的会话设置；LUT的MENU／关闭恢复进入前选择，OK／保留并关闭／半按保留当前选择。没有保存到闪存。

原样SD脚本已在独立ARM Linux VM中实际启动包内remote，读取68项配方、17项LUT及 `gallery_preview=true`。recipes／lut两种浏览面板均由原厂ARM加载器直接执行，包内字体、配置与engine路径解析成功，实际绘制首帧；此次无camera hook，`recipe_style/lut_selection/sensor_liveview` 均为false，没有制造ready标志。结果见 `outputs/deployment/sd-bundle/vm-probe/delivery-report.json`；后续归档中remote／Quick Share／网页脚本的定向核对见同目录 `delivery-update/report.json`，最终成员与验证范围见 `final-delivery-report.json`。此检查不覆盖新增Quick Share面板的动态行为，也未部署实体机或重跑完整长生命周期。

最小机身检查为：打开recipes与lut，确认中文可读、表项完整；缺hook时仅浏览，hook就绪时应用后能从同一API读回。LUT退出恢复及半按保留需分别检查；关闭面板后原厂按键焦点应恢复。

### 远程服务配置

默认仅监听 `127.0.0.1:8781`。需要通过已建立的相机网络访问时，先停止已有服务，再显式选择监听地址：

```sh
(
  NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod NX_REMOTE_LISTEN=0.0.0.0:8781 \
    /bin/sh /mnt/mmc/nx300m-mod/ram-start.sh --service || exit
  ram=$(CDPATH= cd -P /tmp && pwd -P)/nx300m-mfw
  export NX300M_MOD_ROOT="$ram/runtime" NX_MOD_ROOT="$ram/runtime" NX_STATE_DIR="$ram/state"
  unset NX300M_AUTO_START
  /bin/sh "$ram/runtime/stop.sh" || exit
  NX_REMOTE_LISTEN=0.0.0.0:8781 /bin/sh "$ram/runtime/start.sh"
)
```

| 环境变量 | 默认值与含义 |
| --- | --- |
| `NX300M_MOD_ROOT` | RAM 入口接收 SD 源 root；常驻服务改用解析后的 `/tmp/nx300m-mfw/runtime`，兼容旧别名 `NX_MOD_ROOT` |
| `NX_STATE_DIR` | RAM 入口固定为解析后的 `/tmp/nx300m-mfw/state`；后续启停须使用同一目录 |
| `NX_REMOTE_LISTEN` | `127.0.0.1:8781`；仅在新服务启动时生效 |
| `NX_MEDIA_ROOT` | `/mnt/mmc/DCIM`；相册与Quick Share可选文件来源 |
| `DISPLAY` | `:0`；使用已有X服务 |
| `NX_START_TIMEOUT` | 15秒；允许1–60秒 |
| `NX_STOP_TIMEOUT` | 6秒；允许1–30秒，仅向所拥有remote发送TERM |

`ram-start.sh --service` 核对并复制 SD 资源后才从 RAM 启动。已有服务时保留现有 PID 和监听配置；已有手动停止记录时，普通入口也不会自动拉起。上述 RAM `stop.sh` 写入本次开机的手动停止记录，随后显式 RAM `start.sh` 清除该记录并应用新监听地址；仅对该子 shell 改环境，原厂 app 的 SD/LUT root 不变。媒体暂停记录独立保留，重启服务不会恢复读卡。

### 进程归属与拔卡

remote 是唯一常驻业务服务，按 API 请求自行 fork/exec Quick Share；启动器不运行第二条常驻 Quick Share 队列。remote 收到 TERM 后负责停止、回收其 Quick Share 子进程。RAM 监管脚本持有独占服务锁，检查 RAM runtime 的目录身份和 `start.sh`；服务代码、锁和日志不再依赖源 SD 保持挂载。原厂 app 及其 LUT/已映射模块仍可能引用 SD，服务迁移不代表可以直接拔卡或已经完成 UMS 交接。

`start.sh`/`stop.sh` 用 `flock` 串行化操作。PID记录保存boot ID、PID、`/proc/PID/stat` 的starttime、精确exe路径和启动前从该路径取得的exe设备号/inode；启动后再与 `/proc/PID/exe` 对比。后者用于在原目录被移走或卸载后仍识别同一个二进制；替换目录中的新文件不会自动取得旧PID的归属。陈旧记录只清理元数据；停止时格式损坏的记录报错且不发信号。停止超时则保留记录并返回失败，不向相机app或其他进程发信号。

日志在状态目录的 `remote.log`、`supervisor.log`。`ready` 只代表HTTP监听已建立；启动超时或X连接失败时，应读取日志确认实际原因。手动执行脚本时，无SD资源会启动失败；已加载的 `service-loader.so` 由app内0.5秒定时器等待资源，并读取持续打开的mountinfo文件的挂载变化通知，失败最多重试五次。通知来自整个mount namespace，触发幂等启动协调，不代表已定位到SD事件；已经运行的remote保留原PID。

`stop.sh` 在状态目录的 `manual-stop` 写入当前boot ID。loader调用脚本时设置
`NX300M_AUTO_START=1`，`start.sh` 在同一次开机内尊重该记录；手动执行
`start.sh` 会清除记录，旧boot记录不阻止新开机启动。无需新增常驻服务。
`probe_sd_manual_stop.py` 使用原样生产脚本与明确的本机daemon fixture检查了
自动启动幂等、停止前尚无进程、停止后自动恢复被抑制、手动再启动、旧boot
记录不永久禁用，共10次脚本操作退出0且stderr为空；记录为
`outputs/deployment/service-loader/manual-stop-report.json`。旧boot项使用合成
记录，不是实际重启测试。挂载通知的原厂Ecore隔离证据见[SERVICE_LOADER.md](SERVICE_LOADER.md)。
SD拔出监控取决于内核及时返回文件状态；物理I/O阻塞、整机休眠和快照恢复
仍未在机身测量。

### 已执行检查与机身检查

`--probe` 创建全新PID、mount、network、IPC命名空间，在独立tmpfs中以本机测试程序替代remote，检查了脚本语法、默认监听参数、并发单实例、TERM及重复停止、旧boot/starttime和错误exe记录不误杀、SD根目录inode替换，以及缺少READY时的失败与回收。全部通过；记录见 `sd-bundle/probe-report.json`。另以独立QEMU调用原厂ARM BusyBox，核对了四个脚本的ash语法，以及 `stat -L -c`、`readlink`、继承FD的 `flock -n`，退出码均为0，记录见 `stock-compatibility.json`。这些检查不替代ARM完整服务或物理拔卡测试。

原生服务另以以下命令检查：

```powershell
python -X utf8 tools/probe_sd_native.py
```

该检查从同一SD归档读取实际ARM remote和17项LUT，从核验原厂TAR单独提取加载器、libc、X依赖及BusyBox，在独立mount/network/IPC/PID命名空间和临时chroot中连接私有Xvfb。显式QEMU启动的ARM服务返回真实HTTP ready，`camera/info`为NX300M 1.15、`looks=true`、`lut_selection=false`；GET目录包含17项有效表且均不可选择。未加载camera hook时，select和stock均返回503，选择文件与generation文件均未产生。核对实际子进程完整命令行及QEMU可执行文件inode后发送TERM，服务退出码0且HTTP关闭。记录位于 `sd-bundle/native-probe/probe-report.json`，库来源见同目录 `runtime-provenance.json`。

原包启动脚本也经原厂ARM BusyBox实际执行，跨架构直接执行remote时报 `Exec format error`，启动返回1；原始日志保留在 `native-probe/script-remote.log` 等文件中。检查只为shell及BusyBox applet设置显式解释器桥，没有注册全局binfmt，也没有改写生产脚本或转换 `/proc/PID/exe` 身份。QEMU进程的真实exe为模拟器，不能据此证明生产脚本对ARM remote的完整归属与停止链已经通过。临时根目录及私有进程快照中均无相机app；上述结果证明独立服务和无hook拒写行为，不证明相机成像、LUT生效、OSD图像或Quick Share收发。

随后使用通用ARM Linux全系统VM补齐原样脚本检查：Debian内核`6.12.107+deb13-armmp`、1GiB RAM、真实ARM Xvfb，原厂BusyBox与加载器直接执行归档中的remote。四个生产脚本逐字节未改；真实remote PID为179，`/proc/179/exe`与部署文件路径相同，设备号/inode均为`2:420`。启动返回实际HTTP ready，重复启动保持PID179，原样stop后进程被监督脚本回收，PID记录移除；HTTP返回NX300M 1.15及17项相同LUT。该轮没有camera app，hook ready为false，未制造ready文件或转换进程身份。报告及原始HTTP、日志、身份记录位于`sd-bundle/vm-probe/probe-report.json`和`guest-results/`。

复现时先按[ARM_VM.md](ARM_VM.md)构建含Xvfb及SD overlay的VM，再运行`python tools/probe_sd_vm.py run`。512MiB首次启动因临时rootfs空间不足而解包不完整，检查退出1；1GiB重启后生命周期退出0。随后证据收集因Windows→WSL传输展开shell变量而失败，工具已改用base64传输命令，仅重新收集同次成功运行的证据，未重复启停。失败日志保留于同目录`xvfb-unpack-failure-*`及`collection-transport-failure-*`。这补齐通用ARM内核下的脚本生命周期，不代表原厂内核、app自动启动、成像或实机检查通过。

机身最小检查：启动后应看到 `remote HTTP ready`；在所配置的监听地址读取 `/api/v1/camera/info` 并确认真实能力；重复启动应返回相同PID；停止后HTTP连接应关闭且相机app继续运行。最后在已有结果可恢复的条件下检查拔卡后remote退出。尚未执行实体机启动、HTTP访问、Quick Share收发和物理拔卡检查。

## USB/MTP生产接口

### 已确认的桥接

新增静态追踪从原厂 TAR 直接读取 `mtp-responder`，可运行 `tools/inspect_stock_usb_entry.py` 复现，结果为 `outputs/deployment/mtp-host-entry-report.json`。它不执行固件或 USB 请求。本轮核对的本地 ELF 与原厂 TAR 成员逐字节一致。

- 接收处理 @0x3ac10 在 @0x3ac60／0x3ac7c 调用校验函数 @0x1dc0c，随后 @0x3aca0 解码容器，@0x3acc0 进入操作分派器 @0x28da4。
- 操作码 `0x1011`（SelfTest）在 @0x2910c／0x29338 调用 `selfTestMtpHandle` @0x2205c；`0x9000` 调 @0x22758，`0x9102` 调 @0x2288c，`0x9001` 调 @0x25b94。之前 `mtp-operation-table-candidates.json` 的空结果仅是绝对地址引用搜索，不能用于否定这些直接分支。
- 校验函数对 `0x1011` 单独分支：@0x1dd78 起清空100字节缓冲区，@0x1ddbc 将接收缓冲偏移 `0x10` 后的字符串复制到全局 `0x564dc`；后续 self-test 使用同一地址。这里的“校验函数”并不代表该分支已充分校验：这段复制之前未见本地长度上界，整体 USB 分包／长度条件仍待核对。
- self-test @0x221b8 等待共享完成标志，所检查的等待循环未见本地超时。生产模式回调不就绪时存在等待不返回的可能，不能把它列为首次连接的只读探针。

以上补齐静态的请求字符串来源与分派路径；尚未验证实体 USB 数据阶段、模式门控、可靠完成条件或临时 shell。没有生成或发送生产命令。

`mtp-responder:selfTestMtpHandle` @0x2205c 在 @0x2218c 加载 `memory/private/pmode`，将全局命令缓冲区交给 @0x2f698；包装函数在 @0x2f6a0调用真实 `vconf_set_str`。

`libproduction-mode.so:CProductionPTP::registerPTPComm` @0x17574 注册：

- `memory/private/pmode` → `PTPCommByScript` @0x17990 →缓冲→ `ReadScript` @0x16db0 → `CProductionScript::Parsing`。
- `memory/private/pmode_protocol1` → `PTPCommByProtocol` @0x17aec；MTP调整路径同时设置protocol2..5。

因此USB服务与生产命令解释器之间有真实调用链。命令缓冲的完整输入来源、操作码注册、数据阶段和响应条件尚需补齐。

### 模式条件与能力

`CProductionMode::CanPModeWorking` @0x13c04先检查生产脚本，再检查PTP标志。`checkPTPFlag` @0x15c84读取 `/mnt/mmc/info.tgw` 或 `LINEPREF_PTP_AUTO_ENABLE`，返回工作状态。app的 `UI_Check_Production_Mode` @0x1416bc可据此初始化生产工作线程，也另行检查 `ptpconnect.txt` 等标志。

| 接口 | 静态结果 | 剩余证据 |
| --- | --- | --- |
| 普通MTP传文件 | 有 `sendObjectInfoMtpHandle`、`sendObjectMtpHandle` | 存储和路径限制、实体机枚举；传文件后的执行入口 |
| 生产字符串协议 | 已接入内部命令解析器 | 主机请求格式、可用命令及副作用 |
| 生产数值协议 | protocol1..5进入事件分发 | 命令枚举与对应操作 |
| MTP的system调用 | 唯一直接ARM调用 @0x23dd8执行固定 `xberc dump raw /sdcard/osd.raw` | 提供OSD导出，未发现自由shell命令 |
| 生产 `snapshot make` | 固定运行 `/usr/bin/hib.sh` | 属于snapshot操作 |
| `vendorCommand_1MtpHandle` | 读取参数后返回响应码0x2002 | 已见代码未提供应用启动 |
| 通用USB debug分支 | app/usb_setting保留sdb、USB网络和SSH分支 | 原厂TAR未含对应sdbd/SSH启动脚本；实际设备驱动和可达条件待确认 |

`release_shell` 从stdin读命令，`#` handler在 @0x209618调用system；已见启动点要求全局 @0x4dbb60=1且 `SHELL_UI=1`。电脑输入通道和环境设置方式尚未确认。`nx_cs.adj/nx_dev.adj` 的已查检查函数 @0x1c7a44仅验证 `smart_camera` 字符串；开发界面和生产脚本应分别追踪。

USB下一步是静态补全操作表和输入来源，再设计只读主机探针。首轮实机请求可限定为USB描述符、设备信息和存储信息。生产模式涉及调整数据管理，具体命令需先确认副作用。

### 2026-10-24：实体 UMS 与厂商驱动复查

本机当前枚举为 `04e8:1402`、USBSTOR 驱动，关联的 Samsung UMS 磁盘挂载为 `H:`。这是大容量存储接口，不是前述 MTP responder；能看到 SD 卡不能证明生产命令可用。本轮只读识别了设备与卡根目录，没有写卡或发送生产请求。

`tools/inspect_stock_ums_source.py` 从已核验来源的 NX300M GPL 归档按需提取 USB gadget 源码，完整读取 gzip 尾部校验通过，材料在 `outputs/deployment/ums-source/`。核对到：

- `f_mass_storage.c:633–675` 的 `fsg_setup` 处理 Bulk Reset 与 Get Max LUN，其他请求返回 `-EOPNOTSUPP`。
- `f_mass_storage.c:1987–2263` 的 `do_scsi_command` 处理标准读写、查询和介质控制；未知命令进入 `SS_INVALID_COMMAND`，没有发现生产脚本或 shell 分支。
- `composite.c:1570–1628` 将非核心请求转交 interface/configuration 回调；`android.c:171–200` 只向已启用的 function 分派。
- `android.c:911–975` 的 USB 模式切换来自相机系统内的 `UsbMenuSel` sysfs 写入。它不是主机可直接调用的任意控制请求。
- 机型 `drime4_nx300m_defconfig` 启用 Samsung composite、mass storage 和 Samsung MTP，SDB、ADB、ECM、RNDIS 关闭。该源码配置不能替代正在运行的 1.15 kernel 配置，但也不支持“接 USB 就会提供调试 shell”的判断。

结论限于这份 GPL 源码及实际枚举：当前 UMS 通道没有找到现成执行入口。生产解释器另有参数进入 `system` 的静态线索，必须离线验证参数解析和副作用，不能直接当实机探针。只恢复原厂 1.15 `start_script` 的独立最小候选与完整九模块候选分别准备；是否刷写须在候选可审阅且恢复条件明确后决定。

## 固件封装与快启

本节保留七模块 UBIFS／SLP 和 87 文件 SD 包的历史验证记录；它们没有包含当前九模块及完整 SD 的最终字节。当前九模块统一候选不生成新的 UBIFS、SLP 或刷机操作。2026-10-24 单独构建的[最小启动候选](MINIMAL_START.md)仅替换原厂 `libmisc.so`，用于先确认 SD 诊断脚本入口；它不是下述七模块 SLP，也不是完整功能安装包。

原厂SLP包331140200字节，包含以下条目：

| 条目 | 偏移 | 长度 | 原校验值 |
| --- | ---: | ---: | --- |
| vImage | 144 | 5193616 | 2b188808 |
| D4_IPL.bin | 5193760 | 7652 | 73a3dc5c |
| D4_PNLBL.bin | 5201412 | 63580 | 27cfe4fc |
| uImage | 5264992 | 2745848 | aa0df737 |
| platform.img | 8010840 | 323092480 | 1f9255d9 |
| pcache.list | 331103336 | 36864 | ebcd7717 |

六项实测均满足 `header_crc = zlib.crc32(partition_bytes) XOR 0xffffffff`。这是完整性及外层校验算法证据，重建包的升级接受行为仍需单独核对。

当前内置候选改变`platform.img`里的`hib2.sh`与desktop入口，新增`/usr/bin/nx300m-camera-launch`、`/usr/lib/nx300m-mod/preload.sh`及七个原生SO，共11个文件；原始`libmisc`保持不变，前述独立恢复库没有混入候选。原厂UBIFS元数据已完整盘点：16831个inode，无xattrs或特殊节点。原样重建的路径内容和语义元数据零差异；含Mod候选16841路径，相对原厂14个路径变化均符合覆盖文件及父目录size/nlink预期，非预期差异为0。独立UBI容器的卷参数和内嵌UBIFS也已核对通过，见[UBIFS.md](UBIFS.md)。

`fw_upgrade`含擦除、ubiformat、nandwrite和引导镜像表更新逻辑。原厂成功更新前会先擦除mtd9旧快照，成功后才在@0xa744附近创建`/mnt/ubi1/fw/snapshot`、写0并重启；更新后冷启动由rcS连接hib2和app休眠入口。这条静态连接与实际bootloader直接恢复分支见[APP_LOADER.md](APP_LOADER.md)。版本检查、重建包的升级接受性、内核挂载及实际快照仍未验证。

SD启动器及服务包已构建并通过隔离生命周期检查，真实ARM服务也已在独立原厂依赖环境启动。当时SD归档87个文件，包含统一拨盘锁API及网页。七模块在原厂loader下强制立即绑定共载退出0；主模拟器的真实app已自动注册服务加载定时器，缺少start.sh时不执行脚本，见[SERVICE_LOADER.md](SERVICE_LOADER.md)。

`tools/build_slp_candidate.py` 已将最终 `outputs/deployment/ubifs/mod/platform-mod-candidate.ubi` 封入 `outputs/deployment/firmware-candidate/nx300m-mod-candidate.slp`，331140200字节。构建回读核对全部偏移、长度、magic、补码CRC和准确EOF；platform与输入逐字节一致，vImage、D4_IPL、D4_PNLBL、uImage及pcache五项内容均保持原厂字节。版本保留1.15，日期也保留原值。`build-report.json`记录构建结果；这仍是本地开发候选，完整升级接受性及实体刷写尚未验证。

用户授权恢复WSL后，原厂解析器对候选的唯一重试已返回0、进程退出0，真实CRC路径到达；输入前后不变，未执行升级main。`parser-report.json`汇总当前结果；首次环境超时保留于独立诊断，详见[SLP_VALIDATION.md](SLP_VALIDATION.md)。解析接受不替代完整升级、内核挂载和实机启动验证。

App启动前预加载的独立候选已生成，差异和来源见[APP_LOADER.md](APP_LOADER.md)。它覆盖已确认的更新后快照创建入口；`go.sh`的实际调用方和普通非更新冷启动路径仍未确认。此内置候选没有装入SD服务包，也没有写入运行rootfs。

## 证据与上游版本

新获取的参考版本为 `ge0rg/samsung-nx-hacks` @`c4587838970a5d5bed4ed373b8b71cde122b6997`、`fritzchentastig/NX300M-MFW` @`7de63983480cc568c3c2043da7d6e442360bc6ec`、GPLv3的 `HausnerR/nx300-hacks` @`92b0e66c85d8e0da6d8cc46410a2c2849f0cb5e7`。本实现使用原厂1.15库，未获取或复用旧MFW二进制。

除start-hook子目录外，`outputs/deployment/`保留：

- `stock-layout-review.json`、`start-script-patch-review.json`：原厂布局、校验和修改规格。
- `stock-service-inventory.json`：服务、导入导出与字符串。
- `mtp-script-dispatch.asm`、`mtp-vconf-wrapper.asm`、`mtp-script-literals.txt`、`mtp-tms-literals.txt`：USB至生产接口桥。
- `production-ptp-entry.asm`、`production-ptp-literals.txt`、`production-script-entry.asm`：模式条件和内部解释器。
- `app-production-entry.asm`、`app-lifecycle.asm`、`app-camera-init.asm`：app生产模式与生命周期。
- `fw-upgrade-validation.asm`、`fw-upgrade-operation-literals.txt`：升级器校验和snapshot操作。
- `libmisc-script-entry.asm`、`app-start-script-caller.asm`、`stock-startup-inventory.json`：原始hook与启动证据。

静态工具覆盖ARM直接分支、绝对引用及候选PIC常量重建；间接调用、Thumb和机身可达性需要进一步证据。地址均为对应ELF虚拟地址，明确标注文件偏移的条目除外。


## 当前开发候选与诊断样本（2026-10-23）

- [SD开发候选包](../outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz) 包含ARM程序、19项LUT、配方、网页与服务脚本。解压可形成 `/mnt/mmc/nx300m-mod/` 目录，但相机侧加载入口和实体机启动仍需按前文验证；不能将目录完整等同于已完成机身安装。
- [NXConnect Android debug APK](../outputs/nxconnect/NXConnect-NX300M-debug.apk) 为 0.18.3（45）开发构建。查看器支持“同步信息到相机”和“还原相机原始 JPEG”：首次写入前保留原始备份，完整下载核对后更新本地状态；普通编辑仍保存本地副本。需要支持备份协议的新原生服务，RAW 不回写。NFC凭证／URI解析不等于手机与NX300M实机碰一碰已经验证。
- [理光正片诊断输出](../outputs/firmware-runtime/capture-ricoh-positive-nv16/SAM_0006.JPG)（5,189,142字节）与[富士Classic Chrome诊断输出](../outputs/firmware-runtime/capture-fuji-classic-chrome-nv16/SAM_0007.JPG)（5,166,440字节）来自宿主LUT渲染、NV16样本注入、CPU JPEG压缩及原厂封装写盘。它们可用于相册／解码检查，不是机内ISP调色样张。早期 `capture-ricoh-positive/SAM_0004.JPG`、`capture-fuji-classic-chrome/SAM_0005.JPG` 有下半部绿屏，保留为失败证据，不列为颜色示例。

样本来源、格式和读取错误处理见 [JPEG_ENCODE.md](JPEG_ENCODE.md)，色彩迁移边界见 [LOOKS.md](LOOKS.md)。

本轮候选已修复上传路径的 shell 拼接，并统一使用原生媒体根目录校验；原生
上传使用同目录唯一临时文件，确认完整写入、fsync 和关闭成功后才替换原文件。
空上传和跨来源浏览器上传均拒绝。NXConnect 的 LUT／配方状态按后端反馈展示，
NFC 使用普通配对的端口探测。实时 ISO 不再进入菜单或包。

默认开发模式由开发服务管理遥控，不再同时对不完整的 SD 目录启动服务加载器。
可选固件模式 RPC 需要显式 `run_stock.py --firmware-mod`，其原厂 A/M/P 模式读回
与剩余范围见 [CONTROLS.md](CONTROLS.md)。实体安装、手机 NFC 和机内色彩效果
仍需设备验证；菜单 LUT 操作成功表示已暂存，不能据此判断拍摄像素已改变。
