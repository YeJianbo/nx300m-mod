# MFW 独立遥控与文件服务

Fn 入口最新修订：`20261031-fn-evdev` 使用既有隔离 helper 旁听实体 Fn 完整按下／松开，退出与重新打开仍由共享 launcher 管理。Fn 不再在屏幕抓取队列中轮询 XQueryKeymap。原厂 Fn 菜单正常但 Mod 不弹出的上一轮反馈仍视为实机失败；本轮尚待机身验证。

2026-10-31：最新入口按用户要求恢复长按 Fn ≥1.2 秒后松开，关闭 MENU/Custom 监听。首次部署的 Xft 原版弹框与六项菜单放在独立 `fn-panel/`，通过 `fn-panel.sh` 启动；其动作包装调用当前连接服务目录的 `mfw-panel-actions.sh`。构包使用 `prepare_lut_test_card.py --fn-shortcut`，限定写卡加 `--deploy --panel-entry-only`。保留当前无线镜头准入、连接服务和独立 LUT 程序；原版弹框本身没有 LUT 入口。本次机身显示待确认，旧 MENU/Custom 候选记录保留作排查证据。

2026-10-31 最新实机反馈：CPU XImage 版仍不显示面板，MENU 后停在原厂取景、关机不响应，按快门能恢复，不能据此判为内核硬死锁。当前本地候选改为核验后的原厂相机子窗口及 RevertToParent，补充遮挡／失焦退出、条件归还焦点和分段日志；保留 CPU 后端，没有再换字体／旋转路线。本轮暂未再次写卡，实机显示及该次实际运行日志仍待确认。构建、模拟与部署边界见 DELIVERY.md 顶部。

2026-10-31 最新部署：当前卡改为**长按 MENU 约 1.2 秒后松开**打开 Mod，短按保留原厂操作；Custom/Fn 关闭。MENU helper 非独占观察 evdev，不抓键、不读取原厂程序内存；状态见 `camera/info.menu_key`，版本 `20261031-menu-evdev`。服务菜单增加“LUT 滤镜”，未加载适配器时只浏览。软件旋转面板及入口已构建并定向模拟通过、写卡回读确认，真实键码／LCD 驱动与实机故障恢复未验证；完整记录见 DELIVERY.md 最新段落。

2026-10-31 较早的字体优化测量：同一 ARM VM 中首次 **1061 → 266 ms**、重开 **326 → 212 ms**，重开截图逐像素一致。固定字体路径配合构建时提取的 8 KB 字符覆盖表，不再生成菜单字体链接/Fontconfig 缓存；两把锁由 nxui 原生获取，nxui 需配套 `fonts/mfw-font.charset`。该数据早于当前软件旋转版，不能作为当前版时延。完整数据见 [MENU_PERFORMANCE.md](MENU_PERFORMANCE.md)。

**2026-10-25：基础服务正常；恢复 Custom 后，用户确认按键后等待很久，面板迟迟不出现。** 当前电脑端候选为 `20261025-panel-cache`，部署和实机结果以包外 [DELIVERY.md](DELIVERY.md) 为准。Fn 轮询继续默认关闭。

最终缓存版已完成 ARM 检查并于 2026-10-25 写入原卡：首次程序首帧 1060 ms，重开 198 ms，字体缓存保持不变；52 项部署回读一致，38 个照片文件未变。实体 Custom 总延迟尚待确认。包内 README 为构建时快照，最新结果以包外说明为准。

随后用户确认实体自定义键执行原厂快捷功能、未打开 Mod 面板；实体入口尚未通过。此反馈不是回退请求，当前未据此回退。需要读取本次 RAM 运行版本及 `custom_key` 状态，不能用 ARM 面板首帧结果代替按键入口成功。

同轮用户截图显示首页可读型号、电量及 S 档，相册却返回“读取 DCIM 失败：无法列目录：unknown”，并报告开机指示灯持续闪烁。源码中的媒体暂停会允许信息读取、阻断相册及 Custom，因此是待核对路径，尚未证实为本次原因，也不能据此解释闪灯。相机地址为用户提供的 `192.168.107.1`；电脑对 8781 的只读采集超时退出 1，报告为 `outputs/deployment/mfw-services/camera/runtime/20261025T142311Z/report.json`。本轮没有回退、写卡或清除媒体暂停。手机端新增三个既有只读端点的诊断复制入口，用于取得当前运行版本、暂停和 USB 观察状态。

本轮只索引原厂内部所需的一份字体，RAM 状态目录内保存稳定的单文件软链接及独立缓存；字体本体不复制到 SD 或 RAM，重开不重建链接和缓存。UI 以 nice 0 运行，后台任务仍为 nice 19，UI 构建改为 O2。首次布局、字形及 pixmap 绘制完成后才映射窗口，映射后重绘保留画面，再异步读取服务状态，避免先覆盖原厂画面再等待内容。Custom 初始化也去掉紧随初始化的重复窗口树扫描；按键触发、父进程接收、fork、脚本和首帧均记录分段耗时，脚本 uptime 差值使用包含休眠的 CLOCK_BOOTTIME，失败为 -1。旧 ARM 日志冷启动字体初始化为 3372 ms、首帧为 3830 ms，重开首帧为 242 ms；这些是模拟环境测量，未确认实体的具体耗时来源。中间直接打开字体的版本首次加快，但重开因重复解析而变慢，已改为单字体缓存；对应试验和部署记录保留在包外说明中。

此前实体对照为：无卡开机流畅；停用 `autoexec.sh` 后，同卡开机也流畅且不再报“不支持的卡”。这将故障范围收窄到 Mod 启动／运行路径，尚不能确定实体阻塞点。不刷机、不改快照。

## 当前启动修复候选

基础启动修复标识为 `20261025-startup-fix`，本轮面板候选在此基础上继续使用已有 MFW 的 SD 脚本入口。

- 重复入口先比较已发布资源，相同则保留 inode，跳过整树复制；失败清理本次暂存目录，只保留一代旧运行资源。
- 复制前同时检查 tmpfs 容量及物理可用内存，保留 32 MiB 余量。Linux 3.5 使用扣除 Shmem 的缓存估算；这只是瞬时估算，不保证机身工作内存。暂存进程以 nice 19 运行。
- 面板直接读取原厂内部 `/usr/share/fonts/SDIC_GP_US_20120720.ttf`，不再把该 10,680,224 字节字体打包并复制到 RAM。程序、网页、状态、日志仍在 `/tmp/nx300m-mfw`。
- 默认启动只请求 health，其余诊断明确记录为 skipped，不主动遍历相册。只有 `TRACE_HTTP` 或 `NX_MFW_FULL_DIAGNOSTICS=1` 才执行完整诊断。
- USB 观察交给唯一独立 worker；HTTP 主线程不读 USB sysfs。采样期限 1.5 秒，失败后退避 5 秒，旧 worker 未回收时不创建替代实例。超时／缺失为 unknown，不自动恢复已暂停媒体。支持原厂 `[UsbMenuSel] UMS` 文本。
- Custom 初始化失败按 2／4／8／16／32／60 秒退避；状态接口记录失败阶段、errno、读取地址和重试等待。字段表示最后失败诊断，不能单凭它判断当前状态。面板随所属服务退出，避免留下孤儿进程。

只读诊断可用项目 Conda 执行 `tools/collect_mfw_runtime.py --base http://相机地址:8781`。应读取运行版本；SD 文件更新不代表 RAM 内进程已经换版。Custom 及机身启动仍须实体验收。

**以下为历次实现与实机记录。版本、文件数、字体打包和部署状态均按对应轮次理解；当前状态以上文及包外交付记录为准。**

**2026-10-25 自定义按键入口：** MFW 独立服务默认开启事件驱动的 Custom 监听，取景时短按自定义按键并松开打开服务面板。独立子进程只读核验唯一原厂 1.15 程序、完整状态读取代码和当前取景状态；菜单、回放、快览或无法确认状态时将按键交回原厂，不加载 SO、不重启原厂程序。Fn 后台轮询仍默认关闭。实体触发结果以机身检查为准，软件验证及部署结果见包外 `docs/DELIVERY.md`。

**2026-10-25 最新反馈：机身停在 Direct Link 的 Remote Viewfinder 启动引导页。** 上一版的非阻塞 X11 修复尚未解决此实机故障。当前取消默认后台 Fn 轮询，并修正按键释放记录；此故障按机身启动／交接处理，不能记作手机握手失败。新入口保留拒绝覆盖活动旧 SD 服务的处理，不自动终止旧进程或处理休眠快照。

已有MFW的SD脚本入口已实机执行诊断。本包沿用此入口，加载独立remote服务及按需机身面板，不加载任何相机SO，不重启原厂app，不改内部文件或快照。脚本启动后立即把工作交给后台，服务采用已有单实例监督与有限启动等待。

新版采用 RAM 运行：SD 仅作启动资源来源，完整暂存并核对后，程序、脚本、字体、网页、工作目录、锁和日志全部使用真实可执行 tmpfs。2026-10-25 已按用户要求通过读卡器替换卡上部署。显式媒体暂停、USB 状态观察和备份下载已有 28 项定向 ARM 检查通过。完整联合启动检查见下文及包外 ARM VM 报告；原厂 USB 完整卸载与重新挂载协调、实体休眠恢复仍未验证。

当前实机结果：五项本机HTTP自检全部成功，原始日志位于 `outputs/deployment/mfw-services/camera/probe-option-fix/`。读取到NX300M/1.15、S档、电量档位4、DCIM下的104PHOTO目录。用户已确认手机App连接成功且能看到相册缩略图；遥控页截图也已显示原厂等待手机连接的界面，确认OSD屏幕镜像可用。原厂Remote Viewfinder会话尚未建立，传感器取景、照片原图下载及遥控拍摄尚未验证；ISO、LUT、配方等九模块未加载。后文各轮失败记录保留用于说明修复依据，不代表当前HTTP状态。

## 2026-10-25 Direct Link 启动与空闲 X11

用户照片显示 `[DIRECT LINK 按钮] 启动 Remote Viewfinder`。原厂 `di-camera-app-nx300` 的 `CAPPGUISmartLinkState::drawDirectLink (0xfc668)` 与 `smart-wifi-app-nx300` 的 `GUIViewDirectLink::Enter (0xbe0e0)` 均使用这段文案；照片不能唯一确定当前进程，也不能证明 SoftAP／DHCP／RVF 已就绪。原厂代码的无线链路包含 `RVF_Construct → SoftAP → DHCP → ModeServerStart`，不能用手机 HTTP 配对结果替代此阶段验证。

原厂两条调用链均有自动推进：相机主程序 `UI_Manage_Smart_Link_Modechange (0x1cc4a4)` 注册 idler，回调 `0x1cf110` 延时约 2 秒后切换无线模式；无线程序 `smartLinkAppStart (0x7e60c)` 显示引导页并注册 3 秒计时器，RVF 分支随后交接。因此不能把停在该页解释为必须再按一次 Direct Link。最小机身检查是单按 MENU，若出现退出提示或返回，说明当时事件循环仍响应；初始化可能屏蔽按键，无响应也不能单独证明死锁。RVF 中半按快门为无效键，Direct Link 用于启动／切换／清理，不是配对确认。

实机失败后只读检查 H:：53 项文件与刚部署的包全部逐字节一致，`chkdsk H:` 退出 0；未见再次损坏的证据，仍不等于介质健康保证。报告在 `outputs/deployment/mfw-services/camera/directlink-freeze/card-check.json`，没有写卡或修复文件系统。

已确认此前 `NO_PANEL_SHORTCUT` 只阻止启动面板，仍每 100ms 查询 X keymap；没有手机时也会建立 X11 客户端。当前默认不启动 Fn 后台观察，只有显式环境变量 `NX_REMOTE_PANEL_SHORTCUT=1` 才开启；停用标记和媒体暂停同时阻止查询。按需遥控辅助进程在无持有输入且空闲 1 秒后关闭。新 Custom 监听独立运行，仅在初始化、Custom 按下和松开时采样按键，不周期查询 keymap；状态核验失败时不注册抓键。

按键／鼠标的持有记录在 down 前置位，在 up 的 XSync 成功后才清除。释放超时或辅助进程被结束时保留记录，后续只补发释放，不重放按下，避免把未确认释放误记成已完成。此处是确定的代码修复，仍不能认定为整机停住的唯一原因。

ARM 故障注入另发现 `XKeysymToKeycode(NoSymbol)` 可能返回未绑定键位，导致无效名称被当成可注入按键；输入处理必须先拒绝 `NoSymbol` 再查 keycode。这是无效输入验证缺陷，不作为本次启动卡住的根因结论。

只读 `camera/info` 当前返回 `service_revision=20261025-custom-key`，`custom_key` 记录独立入口状态、辅助进程和触发／交回／超时计数；`x11.background_enabled/helper_running/held_input` 仍表示原来的按需遥控／Fn 辅助进程。读取本身不打开 X11。必须核对运行版本，不能以 SD 已复制替代运行确认。当前 App 继续使用 0.20.22，无需为此重复安装同版 APK。

## 2026-10-25 X11 阻塞隔离

旧 remote 在唯一 HTTP 线程直接执行屏幕截图、X11 输入和 Fn 按键状态查询；X11 往返阻塞会同时阻止 health、相机信息和连接监测响应。这是已确认的服务失联路径，尚不能认定为整机卡死的唯一原因。

新版把这些调用交给一个持久辅助子进程，使用最多 8 项 FIFO、共享完成帧及从入队起 1.5 秒的请求期限。HTTP 循环只做非阻塞收发；超时只结束所属辅助进程，丢弃未确认输入，5 秒后才允许重建，避免重复注入按键。正常退出也有界释放输入，父进程死亡由 `PDEATHSIG` 回收辅助进程；不终止原厂相机程序或 X server。辅助进程关闭继承描述符、工作目录为 `/`，运行程序仍在 RAM。

RAM 暂存器仅接受已验证 remote 的直属辅助进程，同时核对进程名、PPID、启动时间、boot ID 与可执行 inode。未知或旧 SD 服务仍被拒绝。正常重复入口不会把合法辅助进程误判为第二套服务。

ARM 程序构建通过。生产 remote 源码的隔离 Linux 故障注入已验证 X server／辅助进程暂停时 OSD／输入返回 503，而 health／info 保持响应，且同时仅一个辅助进程；报告在 `outputs/metol/remote/x11-worker-probe/report.json`，其执行 ABI 是 x86_64，不能当作机身性能。完整实际 ARM 启动及挂起检查以包外 `ram-runtime/arm-vm/report.json` 与候选快照为准；部署结果另见 `reader-deployment/latest.json`，不由软件构建推定实体已启动。

配套 NXConnect 0.20.22 取消进入遥控就自动配对，配对期间停止 Mod OSD、按键和保活输入。相册仍通过 Mod HTTP；真实 RVF 需手动连接并在机身允许。

新版日志位于相机 RAM 的 `/tmp/nx300m-mfw/bootstrap.log` 和 `state/`，可经诊断 HTTP 读取；卡上旧 `.state/` 不是这次启动日志。拔电后 RAM 日志不保留。2026-10-25 当前 USB 卡上未找到此次卡死的持久现场记录，不把旧日志当作本轮启动成功证据。

## 2026-10-25 相机端发现与连接信息

本次修改的是机身 ARM Mod。旧独立 remote 没有发送 NXConnect 等待的发现广播，当前补上 UDP 5681 的 64 字节 `NX_REMOTE|0.9|型号|固件|MAC|` 报文。每秒重读实际启用的 IPv4 接口，按各接口真实掩码定向广播，并明确指定来源地址／接口；相机先启动服务、之后才开启无线，或 DHCP 换址，不需要为此重启服务。仅向 HTTP 监听实际覆盖的接口广播；只有回环监听、未联网或媒体暂停时不广播。报文版本不含端口，当前只为 App 已识别的 80／8781 服务广播。

只读 `GET /api/v1/network/status` 返回实际接口、IPv4、MAC、HTTP 端口、广播开关与发送／错误计数，媒体暂停后仍可查询。`camera/info` 使用实际可用接口 MAC 与其派生身份；无可用接口时返回 null。此接口不更改无线设置，也不读取 WLAN 密码或 NFC 正文。

机身服务面板包含“手机连接信息”，显示当前非回环 IPv4、配置端口及连接提示，无地址则明确提示先开启原厂无线。面板主入口为独立 Custom 监听，不依赖原厂进程内模块。此只读操作在媒体暂停时也可用，不访问来源卡。MFW 有限启动诊断新增网络状态查询。所有后台程序和日志继续沿用 RAM 暂存方式。

更新包为 `outputs/deployment/mfw-services/nx300m-mfw-services.zip`，与 NXConnect 0.20.21 配合使用；已于 2026-10-25 通过读卡器写入实体 SD，未替换固件或重启原厂相机程序。构建与 ARM 启动检查分别以包外 `build-report.json`、`ram-runtime/arm-vm/report.json` 为依据；网络报文检查在 `outputs/deployment/network-discovery/report.json`。私有 ARM VM 没有实体无线硬件，不把其 HTTP 成功记为手机已收到广播。

使用时仍先从相机原厂 Wi-Fi 菜单开启无线，再允许手机加入相机 WLAN，NXConnect 选择查找相机。此包不接管原厂 AP 生命周期，不自动启动热点、不写 NFC 标签。NFC 的原厂私有载荷兼容与关机唤醒尚未完成。独立服务的更新也不会把 ISO／LUT／配方模块加载到已运行的原厂程序；它们的加载条件见 [只读曝光候选](MFW_EXPOSURE_TEST.md)。

## 文件与启动

### 2026-10-25 读卡器部署

执行 `tools/deploy_mfw_reader.py --drive H: --serial E956-B7F2`，返回 0。H: 关联 Generic MassStorageClass USB 读卡器，exFAT 卷序列号与原卡一致；并使用修复前照片备份逐项核对身份与内容。实际更新 9 个文件，包括根 `autoexec.sh`、RAM 暂存入口、服务脚本、remote、nxui 与面板资源；全部 53 项包内容与 ARM 验证快照及最终卡上文件逐字节一致。旧 Mod（含历史诊断）备份到电脑并回读核对；被替换旧文件另外移入卡上 `.nx300m-before-ram-20261025T022153Z/`，不截断其原数据分配。新入口最后发布，避免先启用未完整写好的资源。

部署报告与电脑备份为 `outputs/deployment/mfw-services/reader-deployment/20261025T022153Z/`。写入前后只读 `chkdsk H:` 均返回 0，报告未发现问题；输出开头仍有 `Access is denied`，不能据此宣称取得独占锁或完成介质健康检测。两次照片核对均为 20 项一致。

仅写入 SD 文件，未刷固件、改快照、运行相机代码或写照片。卡上没有 DISABLED、RESTART_SERVICE、TRACE_HTTP 或 NO_PANEL_SHORTCUT 标记。新入口如发现旧 SD remote／服务 shell 仍活动会拒绝启动，未绕过此处理；此次文件部署不代表已解决旧休眠进程或完成机身验收。装回相机前安全弹出读卡器；原厂无线配对与真实取景仍需配套 NXConnect 0.20.21 及机身允许连接后检查。

另有 [只读曝光启动候选](MFW_EXPOSURE_TEST.md)，与本服务包分开；本包仍不启动或预加载相机 SO。新版 remote 能区分 `exposure_status` 与 `iso_controls`：诊断桥就绪时前者为 true、后者为 false，GET 曝光返回 `read_only=true`，POST 返回 403。当前只完成构建，实体尚未加载诊断桥。

SD根目录放本包 `autoexec.sh` 和 `nx300m-mod` 文件夹。替换前把原有 `autoexec.sh` 备份到电脑；不要追加执行旧Mod启动器。诊断目录 `nx300m-diagnostics` 可保留，其完成标记不影响此独立服务。

USB断开后正常重开机，服务监听已有网络接口的TCP 8781。它不会自行启动热点、连接路由器或更改原厂Wi-Fi配置。首次只检查启动日志与本机HTTP，不自动发送按键、不下载照片，也不发送Quick Share文件。

可先使用原厂Wi-Fi菜单建立手机直连网络；手机连接相机提供的Wi-Fi后，在NXConnect手动填写相机IP，控制端口和CGI端口均填8781，或浏览器打开 `http://相机IP:8781/`。原厂NX300系列不同无线功能使用的地址可能不同，不把某个固定IP视为本机实测。手机端连通和不同原厂Wi-Fi模式下的行为还需要实体验证。

NXConnect 0.20.15将自动探测8781与80，并把发现的实际控制/相册端口传给连接流程；旧版本自动查找仅探测80，可能在已连相机热点时仍报告未找到。旧App可先使用手动连接，两个端口均填8781。相机热点下可将手机WLAN详情中的网关作为相机候选地址；手机自身的IP不能当作相机地址。当前手机截图只确认分配到192.168.107.3，未确认网关或8781的手机端可达性。有VPN时可暂时断开VPN做连接排查，应用仍通过Wi-Fi网络绑定访问相机。

手机按上述排查操作后，用户已确认“连上了”，并确认“能看到照片缩略图”。这确认当前手机App连接及相册缩略图读取可用；随后截图确认OSD镜像，但原厂遥控仍等待配对。尚未核验实际采用的相机IP及VPN设置，不能将候选192.168.107.1记录为已测定地址。0.20.15还按当前WLAN网关与手机IPv4网段推导相机候选，识别服务失败时保留系统已批准的WLAN请求，允许自动查找或手动重试；断开连接位于首页连接菜单。新版自动加入和重试行为仍需在实体手机验证。

首次验证结束，正常关机再连接USB，读取SD目录 `nx300m-mod/.state/`：

- `mfw-entry.log`：启动身份、DISPLAY、初始网络地址、`start_exit`和本机HTTP查询退出码。
- `remote.log`：成功时含 `NX_REMOTE_READY`；X连接失败或端口占用时保留真实错误。
- `supervisor.log`、`service.state`、`remote.pid`：已有服务生命周期记录。
- `latest-diagnostic`：本轮诊断子目录路径。新入口将JSON、各请求的 `*.client.log`、`http-probes.state`、进程快照与remote日志副本写入 `.state/diagnostics-启动ID-入口PID/`，避免下一次入口覆盖上一轮证据。仅成功请求将 `.pending` 改为JSON；失败状态为 `http-probes-failed`。旧版本结果位于 `.state/` 顶层，不能当作当前运行结果。
- `camera-loading/`：新增机内加载只读取证，复制当前安装的 `/usr/lib/libmisc.so`、型号版本和唯一相机进程的 exe／inode／starttime／maps，供电脑端核对 MFW 实装。`camera-loading.state` 单独记录采集成功或失败；采集不向相机进程发送命令，也不加载 SO、重启程序或读取照片。

日志与ready不能代替手机连接、真实拍摄或原厂休眠恢复。独立服务只有X11画面；没有LCD observer时不能把传感器视频层当作已接通。ISO、LUT、RGB等进程内功能在缺hook时应报告不可用。

## 机身服务面板与自定义按键

2026-10-25 用户补充：服务面板仅成功打开一次，服务状态停在“获取中”，之后无法再次触发。当前源码确认：新增“手机连接信息”使菜单达到 7 项，UI 却仍只接受 6 项而直接返回 6；旧 336 像素布局也无法显示全部按钮；状态任务运行时触屏“退出”被 child guard 拦住，任务还可能继承 launcher 锁。新版移除固定项数校验，保留逐项动作白名单，按原始屏幕短边限制扩展布局。退出操作先于忙碌检查，MFW 子任务不继承 UI／launcher 描述符；只读状态任务关闭或超时后有界取消，不把只读获取沿用为配方写入等待。不可中断的内核任务只能请求结束，不能声称已回收；配方、LUT 和偏好写入的等待语义不变。

这里是独立机身服务面板，只含状态、连接信息、原厂入口和自动启动设置；不是 LUT／配方菜单。本包未加载原厂进程内 LUT 模块，不能以菜单可打开或按键投递成功作为机内滤镜生效证据。已审计的 MFW 只恢复 `autoexec.sh`，没有 Custom 脚本接口；新版通过独立 X11 子进程监听原厂 Custom 对应的 `KP_Delete`，只在原厂窗口有焦点且只读确认取景、没有快览或半按／全按快门时消费完整按键。在菜单、回放或状态不明时使用 `ReplayKeyboard` 交回原厂，因此保留回放删除行为。初始化已按住、按键重复及混入其他键会取消本次触发。生产代码不伪造原厂进程身份或状态，不写进程内存、不调用原厂函数。

Custom 监听与 HTTP 分开；辅助进程挂起只结束该子进程并延迟重试，父进程退出也会回收它。MFW 模式默认 `NX_REMOTE_CUSTOM_KEY=1`，完整进程内加载模式不默认开启，避免双入口。`NO_PANEL_SHORTCUT`、`DISABLED` 和媒体暂停会停止 Custom 抓键；停用标记沿用 RAM／来源同步机制。无法读取原厂进程、原厂代码不匹配或 X11 不可用时，保留原厂按键功能，查 `camera/info.custom_key`，不改相机内部文件。

上述面板修复的 ARM 程序为 527,616 字节。生产源码的 native 定向检查确认主卡超时提示、触摸退出和锁释放，报告在 `outputs/metol/mfw-panel-probe/timeout-report.json`；截图已查看。当前完整 ARM 检查包含实际 `mfw-panel.sh` 的打开、MENU 关闭、两把锁释放和再次打开。整包检查及 SD 写入结果记录在 `docs/DELIVERY.md`；这些检查与实体自定义按键的加载和触发分别验收。

新版 SD 包包含独立 `nxui.arm`、`mfw-panel.sh`、固定动作脚本、机身菜单和原厂字体。正常取景时短按自定义按键并松开打开面板，MENU、半按快门或选择退出关闭，再次短按可重开。Fn 长按保留为显式启用的旧入口，默认关闭。

面板提供服务状态、原厂菜单、原厂相册、停用／恢复下次自动启动和退出。菜单、相册入口先关闭面板，再投递原厂按键；投递完成不等于机身已执行。状态读取复用服务已有的启动ID、进程启动时间和可执行文件身份检查；扩展模块数量来自真实原厂进程的映射，不把ready文件或菜单存在当成模块已加载。

显式启用后的快捷键只观察当前 X keymap 中的 Fn 和 S1／S2，不固定 NX500 的 evdev 键码。启动时已按住 Fn、快门、面板运行中或采样间断超过 500 毫秒时，本轮长按不触发。根目录放置 `NO_PANEL_SHORTCUT` 会停止后台查询及之后的面板启动；删除后仅在已显式启用的进程中恢复。该文件不关闭已打开面板，也不影响相册服务。

“停用下次自动启动”写入现有 `DISABLED` 标记，当前服务继续运行；关闭面板后快捷键也会停用。恢复可在面板仍打开时选择对应操作，或关机后在电脑删除 `nx300m-mod/DISABLED`。此操作不卸载旧固件、不修改内部启动文件。

这套独立面板没有ISO／LUT／配方写入入口；这些功能仍需要原厂进程内的扩展加载链。独立 Custom 入口的物理触发、关闭重开和回放原厂删除功能仍需实体验证。

2026-10-24：独立服务和NXUI的ARM构建通过，Fn识别的短按、单次触发、启动时已按住、快门冲突、面板运行、采样间断与时钟回退检查通过。包内共51个文件。通过Samsung UMS磁盘关联重新识别H:后，已向SD更新8个Mod文件（remote、NXUI、菜单、两个脚本、字体与配置、许可证）；全部先暂存回读，再改名替换，旧remote保留同卡副本及电脑备份。根autoexec未改，未操作照片、原厂app或固件。已放置 `RESTART_SERVICE`，下一次现有MFW入口只重启所属remote以加载新版本。

部署记录：`outputs/deployment/mfw-services/native-panel-deployment.json`；电脑备份：该记录的 `backup` 路径。SD复制完成不代表新面板已在机身运行；须正常断开USB、重新开机，再长按Fn并松开核对。若没有打开，保留 `.state/remote.log`，其中 `NX_PANEL_SHORTCUT`、`NX_PANEL_START` 和 `NX_PANEL_EXIT` 分别记录观察器、启动与退出结果。

## 停止与恢复

在已有机身shell中可运行 `/bin/sh /mnt/mmc/nx300m-mod/stop.sh`，只终止属于这套服务的remote及其子任务。没有shell时，在相机关机、卡由电脑访问后创建 `nx300m-mod/DISABLED`，或移走根目录 `autoexec.sh`，阻止之后自动启动。停用文件不主动结束已运行进程，也不能清除休眠快照中的进程；当前未验证实体休眠行为。不要运行原厂快照脚本处理它。

本包没有内置安装器、固件更新器或任意shell执行HTTP入口；不替换相机固件。提供Quick Share CLI仅用于满足现有服务依赖，后台不会自行发送照片，也不代表免路由器Quick Share建链已经完成。

## 电脑端构建

使用Conda环境，从当前项目执行：

```powershell
python -X utf8 tools\build_remote.py
python -X utf8 tools\build_mfw_services.py
```

输出位于 `outputs/deployment/mfw-services/`。构建器只写电脑目录，不复制实体SD、不操作正在运行的模拟器。现场部署与运行结果另行记录，不能从构建成功推定相机服务已运行。

## 2026-10-24 SD准备结果

remote ARM后端及网页已重新构建，两个MFW入口脚本通过shell语法检查；未重复已有服务长生命周期测试。首次批量语法检查的shell传参失败（退出1，`cannot open`空路径），随后改为分别传入绝对文件路径，两份脚本均返回0。

再次通过磁盘关联确认Samsung UMS Composite首逻辑单元对应H:，原入口逐字节匹配上一轮诊断脚本。已备份到 `outputs/deployment/mfw-services/card-backup/autoexec-before-services.sh`，再向卡放入43个文件、4,213,795字节，全部回读匹配。服务资源先写入并核对，最后替换根目录入口；没有写固件、内部启动文件或照片，没有从电脑重启相机app。

部署记录是 `outputs/deployment/mfw-services/card-deployment.json`。当前仅确认SD准备完成，实体remote启动、HTTP、本机状态和手机连通结果尚待用户断开USB并正常重开机后读取；不把准备完成记作服务已经运行。先检查 `.state/mfw-entry.log` 和 `remote.log`，再处理联网，首轮不加载SO。

## 首轮实机失败与定向跟踪

用户重开机后已读取真实SD日志，原始证据保留于 `outputs/deployment/mfw-services/camera/first-start/`。MFW成功启动ARM remote，原厂型号读取为NX300M/1.15，日志有 `NX_REMOTE_READY`、`start_exit=0`；但四个本机HTTP查询全部退出1，stderr为 `wget: download timed out`，JSON均为空。`ready`在已有监督器中只表示监听已建立，不能记作相册/API通过。日志有两次MFW入口记录，因此跟踪版必须保证替换的是独立remote，而非仅更新磁盘后继续使用旧进程。

启动时只列出回环接口和空路由表，说明当时未建立Wi-Fi网络；这不能解释本机HTTP超时，也不能证明Wi-Fi硬件损坏。后续离线复现已找到原厂wget的连接关闭兼容问题，见下文。

针对该失败新增无相机状态依赖的 `/api/v1/health`、默认关闭且最多32条的HTTP事件跟踪，以及前三次poll的进入/退出记录。SD的 `TRACE_HTTP` 仅供下一次采样，采集已验证属于本服务的remote之 `/proc/PID/status/wchan/syscall/stack/fd`，并在原厂strace与timeout可用时对该remote附加8秒的系统调用跟踪；不附加相机app。`http-process-before.log`、`http-process-after.log`、`http-syscalls.log`保留实际结果。代理环境变量在本机探针中清除，避免环回请求经过继承代理。

更新时 `RESTART_SERVICE` 是一次性部署请求，仅调用已有精确进程归属检查的 `stop.sh` 停止本remote，再显式启动新remote；停止失败则不继续。它不重启相机app或修改快照。标记在成功停止后移除。程序/脚本更新保留原文件备份，不能把新跟踪包的构建与复制写成超时问题已修复；仍需下一轮实体日志确认。

## 第二轮日志与SD读取异常

真实日志已保存至 `outputs/deployment/mfw-services/camera/http-diagnostic/`。五个请求包括health全部超时；前后进程快照显示remote仍存活，监听8781，事后有五个环回TCP TIME_WAIT条目。这不足以断定HTTP响应已正确发送。该进程还继承了原厂app的GPIO、镜头、帧缓冲及管道描述符。电脑端修正版在remote打开自己的资源前关闭这些继承副本，不会关闭原厂进程中的描述符；尚未实机验证该修改。

原跟踪命令 `timeout 8 ...` 退出127，stderr为 `can't execute '8'`，没有运行strace。已通过独立QEMU运行原厂BusyBox 1.20.2帮助，确认其要求 `timeout -t 8 ...`，修正版使用该语法并发送INT让strace退出。非零跟踪退出码不再标成预期成功。该离线命令不涉及正在运行的模拟器或实体相机。

通过Samsung UMS磁盘关联确认H:后，`http-probes.state`连续读取报“文件或目录损坏且无法读取”，其余上述日志可读。当前暂停向该卡写入，未运行磁盘修复、格式化、固件更新或快照命令。损坏原因未确认，不能排除相机与USB主机访问期间的写入冲突。下一次更新前应关机取卡、使用读卡器，并先备份照片及可读文件；读卡器下仍报错时，先处理卡的数据备份与文件系统问题。新的电脑端构建不代表已替换卡上的版本。

## HTTP自检兼容问题的离线复现与修正

使用原厂BusyBox wget、原厂glibc及当前工具链构建的最小Mongoose服务，在独立QEMU进程复现：服务已发送完整HTTP 200、Content-Length和JSON，普通socket客户端可立即读到，但原厂wget等待连接关闭，3秒后退出1且输出为空。相同响应发送完关闭连接，则wget退出0并得到JSON。请求本身携带 `Connection: close`。证据位于 `scratch/http-timeout-review/`；不是实体HTTP成功记录。

remote现识别Connection头的close token及HTTP/1.0默认关闭行为，在实际发送响应并排空缓冲后关闭连接。HTTP/1.1持久连接继续保留；异步请求在尚未生成响应时不会被提前关闭。此修正解决了已复现的自检兼容问题，但仍需换卡访问方式后进行实体回归，不能据离线结果确认全部实机接口可用。

定向兼容检查直接抽取当前remote的Connection处理代码，使用相同ARM Mongoose及原厂glibc运行：原厂wget读取JSON、延迟350ms响应、chunked响应及1MiB文件均退出0，内容逐字节匹配；HTTP/1.1同连接连续两请求成功，HTTP/1.0及含混合大小写close token的请求在完整响应后收到EOF。实际输出见 `scratch/http-timeout-review/strategy-report.json`。ARM后端构建和MFW服务脚本语法检查通过，候选包仅更新在电脑端。

## HTTP修正版SD部署结果

已重新通过Samsung UMS设备关联确认H:，对比包内43个实体部署文件，只有 `nx300m-mod/mfw-services.sh`（4375字节）和 `nx300m-mod/bin/nx-remote-controller.arm`（527636字节）需要更新。两份旧文件备份到电脑 `card-backup/http-fix/`，卡上也按 `before-http-fix-时间` 保留旧文件；先暂存并回读验证，再改名替换，最终回读一致。其余41文件与候选包一致。

已放置并回读验证 `TRACE_HTTP`、`RESTART_SERVICE`，供下一次MFW入口运行。部署记录为 `outputs/deployment/mfw-services/http-fix-deployment.json`。没有写固件、照片或损坏的状态文件，没有执行文件系统修复；该状态文件的损坏问题尚未解决。修正版现已放卡，仍待断USB并正常重开机后的实机HTTP日志，不能把文件部署当作运行验证通过。

## 修正版启动与探针参数修正

已回收实机证据至 `outputs/deployment/mfw-services/camera/http-fix/`。remote成功启动，前三次poll均正常返回；进程393的快照只保留标准输入输出及X11/监听socket，先前继承的GPIO、镜头、帧缓冲和原厂管道描述符已不存在。strace成功附加、退出0，采样期间监听socket没有请求。

本轮五个探针实际均未发出HTTP请求：相机的wget不支持新增 `-S`，返回 `invalid option -- 'S'`、退出1。实际版本横幅为BusyBox 1.20.2（2013-06-04），与离线原厂包中的2014-05-15构建不同，不能把离线包的帮助当作机上可执行文件的完整能力清单。脚本现删除 `-S`，使用机上帮助明确列出的 `-T 3 -Y off -O FILE`，stderr继续逐请求保留。本轮未验证HTTP响应、相册或手机联网。下一次仅需更新MFW服务脚本，ARM程序无需重编。

该脚本修正已放回经Samsung UMS关联确认的H:，4383字节最终回读一致；旧脚本保留电脑备份及卡上改名副本，两个下一轮采样标记也已回读。记录为 `outputs/deployment/mfw-services/probe-option-fix-deployment.json`。尚待下一次实体启动，未重编或替换ARM程序。

## 实机HTTP通过与休眠日志目录修正

实体开机后的 `diagnostics-7e8d8cd5-d0c5-4105-a840-b9ec813dfb9a-995/` 中，五项HTTP查询退出码全部为0，`http-probes.state`为passed。health返回ok，camera/info返回NX300M/1.15，camera/status返回S档、电量档位4（不是精确百分比），mods/status确认九模块未安装，dirlist返回104PHOTO子目录。HTTP事件日志可见五次完整接收、处理、发送和关闭；strace退出0。这些结果仅覆盖本机请求，不替代手机端验证。

随后恢复时，boot ID与入口PID复用导致旧诊断目录重名，mkdir退出1，入口未继续。脚本现为已存在目录递增后缀，避免恢复后因保留日志而中止启动；仍使用原有入口锁。shell语法检查通过，尚未实机验证该重名分支。未改相机快照。

## SD 交叉占簇与只读备份

下一次实体采样保存在 `outputs/deployment/mfw-exposure-test/camera/diagnostics-7e8d8cd5-d0c5-4105-a840-b9ec813dfb9a-2305/`。五项 HTTP 检查再次全部通过；实际 `/usr/lib/libmisc.so` 为 8140 字节，与本地审计的 MFW 1.1 样本逐字节相同。但服务脚本首 512 字节被日志数据覆盖，末尾记录语法错误，目录也出现交叉内容。已停止向相机 SD 写入，没有用重复复制脚本来覆盖故障。

通过 Samsung UMS 设备／分区关联确认源卷后，只读解析原始 exFAT 目录项和分配位图，完整扫描确认 8 个交叉簇：

| 簇 | 交叉引用 |
|---|---|
| 2470–2474 | remote ARM 二进制与本轮 HTTP JSON／请求日志 |
| 2475 | `mfw-services.sh` 与 `gallery-root.json.client.log` |
| 2049 | 只读曝光候选目录与本轮诊断目录 |
| 1649 | 旧 `http-probes.state` 与旧诊断目录 |

目录项校验和有效，故障不是单纯文件名／重定向写错。remote 在本轮采样时能运行，不代表卡上的 ELF 在被交叉写入后仍可重新执行。原厂 exFAT 驱动在挂载期间缓存分配位图；原厂 libstorage 在普通卸载失败后存在 `umount2(path,3)` 延迟卸载回退，UMS 则直接导出块设备。这支持旧挂载状态与主机写入冲突的可能性，但尚未证明本次走过该失败路径，也未排除其他原因。静态证据见 `outputs/deployment/mfw-exposure-test/startup-supervision.md`。

已从本地镜像与本轮部署源比较：remote 为 527852 字节，差异 3084 字节，ELF 文件头已被覆盖；服务脚本仍为 6607 字节，差异仅在首个 512 字节扇区，510 字节不同。结果为 `camera/sd-backup/damaged-programs.json`。这两份卡上文件不能继续执行，不是仅有诊断日志受损。

只读备份保存在 `outputs/deployment/mfw-exposure-test/camera/sd-backup/`：

- `allocated-volume.img`：元数据及全部 2492 个已分配／引用簇，共采集 343,408,640 字节；逻辑长度 64,072,187,904 字节的稀疏镜像。未采集空闲空间，不能用于保证恢复此前删除／丢失的数据，也不能直接当成整卡恢复镜像刷回。
- `backup-report.json`：源卷只读、本地镜像回读校验通过、采集期间元数据稳定、目录遍历错误为零，保存全部交叉引用与采集范围。
- `photos/DCIM/104PHOTO/`：从本地镜像导出 20 个文件，即 10 组 JPEG＋SRW，共 261,513,945 字节。导出与镜像回读一致，当前分配图未发现这些文件参与交叉占簇；10 个 JPEG 的像素解码及 10 个 SRW 的 LibRaw 原始数据解包均通过。明细为 `photo-export-report.json`、`photo-decode-report.json`；这些检查不证明历史上没有数据丢失，也不替代图像内容／颜色比较。
- `failed-initial-extension.json`：首轮 Windows CRT 稀疏扩展仍写零，已停止该电脑进程；尚未复制源卷数据。丢弃的仅是本地无效零填充文件，随后改用 Win32 扩展，第二次备份退出 0。源卷始终只读。

后续先安全弹出并关机取卡，通过读卡器离线处理；不再通过相机 UMS 更新这张卡。读卡器能隔离主机与相机同时持有卷的状态，但不能单独证明下次相机恢复休眠时不会使用旧分配状态。先确定完整卸载／重新挂载或不恢复旧状态的启动路径，再决定离线修复或更换卡；不自动运行 `chkdsk /f`、格式化、删除／重建快照或加载进程内模块。若无法确认安全的恢复路径，则保留原卡与备份，继续电脑端开发。

只读启动链调查目前仅找到原厂 PNLBL 串口 prompt 的 `boot 0` 单次冷启候选：静态代码支持保留快照并以 `noresume` 冷启，但物理串口引脚／电平／时序未验证，未找到当前 SD 或网络命令的等价入口。普通开关机、拔电池或 `reboot` 不能因此视为绕过快照。详见同目录 `lifecycle-audit.md` 的后续调查章节，不将串口候选作为用户当前操作步骤。

## 读卡器复核与电脑副本修复

用户已关机取卡并通过 Generic MassStorageClass USB 读卡器接入。重新按设备／分区关联确认 H:，卷序列号 `E956-B7F2`、分区起点 16,777,216 字节；全部已采集范围与此前备份完全一致，变化扇区为零。只读 `chkdsk H:` 返回 3，报告目录项、交叉占簇及分配位图错误，未加 `/F`，没有修改原卡。日志前段另有 `Access is denied`，但随后实际完成只读扫描并报告错误；不将该行解释为修复或检查通过。结果为 `camera/reader-inspection/`。

使用 `tools/clone_camera_exfat_backup.py` 仅复制电脑备份的已采集范围，生成独立稀疏工作镜像。Debian exfatprogs 1.2.9 解压在本项目 `work/exfatprogs/`，未安装进系统。`fsck.exfat -n` 首次返回 4（存在未修复错误）；仅对工作镜像运行 `-y` 返回 1（已修正错误），随后再次 `-n` 返回 0、文件系统为 clean。修复涉及截断交叉的 Mod／诊断项及处理重复文件名；修复后的 20 个照片文件与备份逐字节相同。日志及结果为 `camera/offline-repair/repair-report.json`。

这证明电脑副本可修到一致状态，不代表原卡已修复或卡上旧 Mod 能恢复运行。工作镜像省略空闲空间，不整卷刷回相机卡；原卡的写修复须单独明确授权，并在修复后核对实际结果。

随后用户明确允许修复原卡文件系统。重新核对 Generic 读卡器设备标识、H: 分区关联和卷序列号后，实际执行 `chkdsk H: /F`，返回 1，日志明确记录已修正文件系统错误：重复字体配置目录项改名，脚本及 remote 的交叉引用通过复制分离。该动作没有格式化、刷机或启动 Mod，也未强制卸载；若卷忙则输入 N 拒绝强制处理。

修复后 `chkdsk H:` 返回 0，报告未发现文件系统问题；检查期间另一个只读照片核对进程占用卷，日志含只读检查无法排他访问的提示，不据此宣称执行了离线独占扫描或介质健康测试。全部 20 个实体卡照片文件与修复前备份逐字节一致。原始日志为 `chkdsk-repair.txt`、`chkdsk-after.txt`，照片结果为 `photos-after-repair.json`，均在 `camera/reader-inspection/`。文件系统复制分离不会恢复此前被日志覆盖的程序字节，旧脚本和 ELF 仍不能继续使用。

## RAM 运行候选

新版 `autoexec.sh` 先确认解析后的 `/tmp` 真正在可写、可执行 tmpfs，再启动 `mfw-ram-stage.sh`。构建生成 `runtime-files.list`，逐文件复制并比较全部字节；只有完整资源验证通过才执行 RAM 副本，失败不回退到 SD 执行。固定 RAM 基目录为解析后的 `/tmp/nx300m-mfw`，`runtime/` 放代码和资源，`state/` 放锁、PID、日志、诊断和字体缓存，`bootstrap.log` 记录入口错误。

已运行的旧 SD remote 或服务 shell 会使新入口拒绝启动；它不会静默迁移、发信号或操作原厂相机进程。相同 RAM 资产重复入口保留原 inode 和实例；需要替换运行中的 RAM 代次时，须有新的 `RESTART_SERVICE` 请求，并沿用现有准确归属的 RAM `stop.sh`。该流程不是处理原厂休眠状态或 SD 旧挂载的替代方案。

`DISABLED` 和 `NO_PANEL_SHORTCUT` 从来源卡读取并镜像到 RAM。面板显式停用／恢复下次启动时，先核对来源目录身份，再瞬时修改 SD 持久标记并同步 RAM；来源缺失或身份变化则失败，不假报已保存。`TRACE_HTTP` 与 `RESTART_SERVICE` 在来源卡上保留，通过 RAM 内的身份与字节记录避免同一运行状态重复消费；RAM 真正清空后可再次请求。启动链不会自动写回来源卡。

RAM 日志通过只读 `GET /api/v1/diagnostics` 列出并下载，详见 [REMOTE.md](REMOTE.md#mfw-ram-诊断日志)。新增开发用 `POST /api/v1/media/pause` 与 `GET /api/v1/media/status`，只有本服务的下载连接释放、Quick Share 子进程回收、面板来源卡操作结束后，才报告 `service_media_released=true`；暂停在 RAM 内跨服务重启保留，尚无自动恢复接口。定向 ARM 检查见 `ram-runtime/media-pause-report.json`，范围及限制见[媒体暂停](REMOTE.md#mfw-媒体暂停)。USB 只读观察接口为 `GET /api/v1/storage/status`：发现 backing 打开且未明确断开 USB 时触发暂停，这是导出后的限制，不是原厂导出前握手；节点缺失返回 unknown。LUN 关闭或新挂载 ID 均不会自动恢复。完整 ARM 链检查见下文；原厂 USB 交接及实体休眠恢复仍需验收，本服务排空不代表原厂 app 已完成卸载。

本轮有限暂存验证运行实际入口与暂存脚本，使用私有 mount／PID namespace、真正 tmpfs 和只读绑定的临时资源源；仅将 fixture 的 `mfw-services.sh` 换成短命报告 worker。51 项运行资源与该 fixture 来源逐字节一致，worker／launcher 的 cwd、标准输入输出及脚本 FD 不持有 SD 来源；缺文件、非 tmpfs 与未知 remote 拒绝，未向未知进程发信号，控制标记的来源内容保持不变。四组检查退出 0，报告为 `outputs/deployment/mfw-services/ram-runtime/staging-report.json`。初轮 fixture heredoc 暂时改写 stdin，造成检查失败；修正 fixture 后通过，未据此修改生产脚本。这不是真实 ARM remote 经 RAM 启动链运行或实体 USB 验收。

完整 ARM VM 联合检查使用未替换的 `autoexec → RAM stage → mfw-services → supervisor → ARM remote`，核对全部资源字节、真实进程身份及 cwd／FD／maps 的来源引用，检查健康接口、RAM 日志、重复入口、准确停止、手动停止保持及显式重启。媒体暂停检查通过真实 HTTP 请求等待本服务排空，并在服务重启后核对暂停仍有效。

当前结果以包外 `outputs/deployment/mfw-services/ram-runtime/arm-vm/report.json` 为准；报告记录候选身份与各项实际结果，旧包证据保留在 `arm-vm/previous/`，不混作当前包验收。原始证据位于 `guest/mfw-results/`，重现入口为 `tools/probe_mfw_ram_vm.py`。来源是私有 initramfs RAM 根上的只读 bind，不是块设备／exFAT，不证明实体 SD 卸载或原厂 USB 交接，也不保证实体开机。检查使用独立 VM，不重启主模拟器。
