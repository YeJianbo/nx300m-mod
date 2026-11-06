# NXConnect 连接核查：2026-11-06

结论：App 侧已有连接流程修复，真实无线关联失败与相机不能关机的问题仍未解决。11 月 6 日的菜单、预设和冷启动预览修复没有改 Android 连接代码，也没有完成相机独立热点接入。

## 当前实现

- 源码版本 `0.20.34 / 82`。`CameraPairing.kt` 的扫描选择与 NFC 凭据入口共用连接流程：Android 11+ 使用 `ACTION_WIFI_ADD_NETWORKS` 系统保存网络，已在对应 SSID 时直接识别；Android 10 保留 Specifier 请求。
- `CameraWifi.kt` 等待有效 IPv4，处理批准／DHCP 超时、取消、旧网络丢失与替换网络；已批准的请求保留。此入口服务 Android 10 及系统保存入口不可用时的兜底。
- `NetworkBinder.kt` 按目标地址的实际网段选择网络；`AndroidHttpBinder.kt` 捕获相机对应的 `Network`，通过 `network.openConnection()` 建立 HTTP 请求。应用专用网络可以承载实际相机 HTTP，不能仅凭系统仍显示其他默认网络断言相机流量没走 WLAN。
- 机身 `nxui_feature_pages.h` 的连接入口调用 `panel-actions.sh network-info`，仅执行只读地址查询和显示；没有热点、DHCP 开启或关闭动作。
- `NxApp.connect()` 仍调用 `stockRvf.attach()`，NX300M 随后请求原厂控制会话；进入遥控页才开启视频。当前相机无线入口依赖 Remote Viewfinder，普通拍摄与后台传图需要另外接通相机端独立热点生命周期。

## 已有证据

以下报告在本轮实际读取，未重新运行：

| 证据 | 覆盖范围 | 未覆盖 |
| --- | --- | --- |
| `outputs/nxconnect/wifi-lifecycle/report.json` | 实际 CameraWifi 源码，11 场景／41 断言通过 | 使用 Android 网络／计时替身；无真实无线关联 |
| `outputs/nxconnect/network-routing/report.json` | 路由选择／网络编译检查通过 | 无真实设备网络 |
| `outputs/nxconnect/system-wifi-takeover/report.json` | API31 模拟器扫描取消、返回、已有 WLAN 识别及服务失败不误报成功，5 项通过 | 新网络系统弹窗及相机真实关联未测 |
| `outputs/nxconnect/service-identification-report.json` | 真实识别 helper，4 项检查通过 | 不证明热点或无线驱动状态 |
| `outputs/nxconnect/rvf-emulator-0.20.34/report.json` | RVF 软件控制／视频／拍摄错误等协议集成通过 | 使用合成 HTTP／视频端点；没有运行实体固件 |
| `docs/AGENT_WORKLOG_2026-11-06-night.md` | 已修恢复所有权与超时的离线软件问题 | 明确保留实体无线 P0 未关闭 |

当前本地 Release APK 为 7,517,558 字节。历史 RVF 模拟报告记录的同名 APK 为 7,517,482 字节，不能将其作为当前 APK 字节一致的完整验收；系统 Wi-Fi 路径的报告也明确未执行真实关联。

## 未关闭项与后续入口

1. 确认故障属于热点未启动、手机关联失败、DHCP 无地址或 HTTP 服务不可达中的哪一阶段。历史服务监听 `0.0.0.0:8781`，该事实只证明监听配置。
2. 将原厂独立 SoftAP／DHCP 生命周期接入可退出、可恢复的相机控制入口。原厂 `wlan.sh` 已有相关静态调查，见 `docs/NXCONNECT.md` 开发工作区版本；当前菜单没有这条执行路径。
3. 连接卡住且无法关机的根因尚无故障当时的线程等待／TracerPid／无线状态证据。不能将 Session、驱动或 App 网络绑定列为已确认原因。

实体集中复核只需一轮：正常拍摄 → 打开无线 → 手机批准并关联、取得地址 → 访问服务 → 缩略图／单张下载 → 断开或连接失败能返回 → 机身仍可拍摄和关机。需要记录失败所在阶段；不要求用户在相机上运行 Shell。当前没有此轮实体通过记录。

## 本次“地址无响应”的归因核查

用户确认手机系统已连接相机热点，手填 `192.168.107.1:8781` 仍提示地址无响应。当前尚不能确定是相机服务、手机网络路径还是 App 探测失败。暂停 App 候选的构建与发布，先绕过 App 检查同一地址。

已确认安装内容发生变化：

- 旧 `tools/build_connected_mfw.py` 同时打包 `nx300m-lens-debug/autoexec-wifi.sh`、`manual-lens-wifi-mem.arm`、`WIFI_MEM_RUN_ID` 和 `WIFI_MEM_PROFILE`。
- 新 `tools/build_first_install_candidate.py` 仅收集根入口与 `nx300m-mod/` 资源，公开首装生成器继承该清单，未包含上述无线准入资源。核查时 H: 根目录也没有 `nx300m-lens-debug`；备份根只有 `autoexec.sh` 和 `nx300m-mod/`。
- 实际读到的卡日志在 `2026-11-06T21:17:01Z`、`21:18:11Z` 记录 daemon 启动环境 `0.0.0.0:8781`，随后有 daemon ready；对应根入口记录 `wifi=1 service=0`。这里的 `wifi=1` 表示无线补丁入口缺失或失败，不能解释为 WLAN 驱动启动失败。
- 补丁仅修改 `UI_Check_WiFi_ModeChange_Enable` 的 Lensconnection 与 Kmount 两处准入条件，保留 Lenslock、存储、busy 和 RVF 镜头事件检查。它不启动热点、DHCP 或 HTTP，不直接修复已关联后的 HTTP 访问。已确认打包遗漏，但尚未证明该遗漏是本次超时根因。

最小区分步骤是在同一手机、保持相机 Wi-Fi 连接时，用浏览器访问：

1. `http://192.168.107.1:8781/api/v1/health`
2. 仅当 health 成功时，再访问 `http://192.168.107.1:8781/api/v1/camera/info`

health 与 camera/info 均能返回，而 App 失败，排查 App 的网络绑定、代理与探测；health 成功而 camera/info 失败，排查相机 API；两者均不通，先核对手机实际 WLAN 地址、网关和相机监听/服务存活，再区分手机路由与相机端，不凭浏览器失败直接断定 MOD。当前电脑 WLAN 是 `192.168.31.76`，不在相机网段，不能用其请求超时作相机故障证据。

本次未从电脑连接相机、未写卡、未发布候选 APK。卡片在核查中途已不再显示为 H:，因此未继续读取 RAM 诊断或尝试修改卡内容。

用户随后回报的实体结果（按先后顺序）：

1. 同手机浏览器访问上述 health 地址“无法访问”。这绕过了 App 的服务识别代码，但仍依赖手机路由；没有 HTTP 状态码或网络错误详情，不能据此直接判定 MOD 服务故障。
2. 随后用户确认“App 连上了”。本线程在两条反馈之间未构建、更换 APK 或写卡。
3. 用户确认恢复前进行了“重启相机或重新进入原厂遥控取景器”。该回答未区分只执行其中一项还是两项均执行，不能声称仅重新进入就已修复。

目前记录为“实体连接已恢复，原间歇性故障根因未确认”，不是无线修复验收通过。恢复动作涉及相机生命周期，因此优先排查原厂热点/DHCP与 MOD 服务的启动、重入和休眠恢复；该顺序是排查判断，不是已确认归因。手机当时的实际 IP/网关和故障时相机 HTTP 状态仍缺失。无线准入资源遗漏作为独立安装包问题保留，不能因这次连通而关闭，也不能用它解释已连接热点后的所有超时。

源码另确认一个观测缺口：`mod/deployment/start.sh` 遇到 `record_alive` 即直接返回；`service.sh` 初次依据 `NX_REMOTE_READY` 日志发布 ready，随后只监测已拥有进程的身份和存活。`mfw/services.sh` 会在启动时独立请求一次本机 health，但没有持续的 HTTP 活性判定。因此“进程仍在但 HTTP 不响应”不会被现有存活监测区分；尚无故障当时的证据证明本次属于此情形，不据此添加自动重启或强杀恢复。

## 0.20.35：应用内选择 WLAN 后主动连接

用户进一步确认剩余问题是“App 内连接不上 Wi-Fi”，本轮因此先修手机加入网络流程，保留相机端间歇性 HTTP 故障的独立排查记录。

原流程在 Android 11+ 的 `ACTION_WIFI_ADD_NETWORKS` 返回成功后，直接进入服务识别。该结果只表示网络保存成功或已经存在，不保证手机已从原 WLAN 切换到相机热点；识别时还会遍历其他 WLAN，可能一直在旧网络上探测。

本版保留系统保存入口，随后核对所选 SSID 对应的网络和有效 IPv4。若短暂等待后仍未实际关联，使用既有 `CameraWifi.join()` 发起明确的系统连接请求，取得 IPv4 后才探测服务。系统已经连接目标 WLAN 时直接复用。保存路径的候选和 HTTP 探测也限制在选中的 WLAN，不扫描其他家庭网络。

连接阶段日志区分网络保存、请求系统连接、IPv4 就绪和服务探测；记录网络接口、地址及网关，不记录密码。HTTP 显式使用已选 Network 并绕过系统 HTTP 代理。用户取消连接时保留原有取消语义，服务识别失败时不撤销已经批准的 WLAN。

最小实机复核：

1. 覆盖安装 0.20.35；相机进入原厂遥控取景器。手机先处于其他 WLAN，直接在 App 点连接、选择相机 SSID。
2. 按系统提示确认保存与连接（如果系统保存后已自动切换，则不再额外请求连接）。无需另外去系统设置手动切换 WLAN。
3. 预期取得相机网段 IPv4 后自动识别 8781，相册可打开。若失败，提供 App 显示的最后阶段或其日志；“已保存”“已加入 WLAN”和“相机服务已连接”分别判定。

此修改不证明相机热点或 8781 的间歇性失效已经修复；本机没有已连接的实体 Android 设备，真实系统弹窗、关联和 DHCP 需用上述最小流程确认。

交付验证：release 构建退出 0；既有 `CameraServiceIdentificationTest` 4 项与 `DiscoveryAgainstFakeCameraTest` 5 项全部通过（仅软件服务识别，不是 Wi-Fi 关联测试）。APK 为 7,521,762 字节，v2 签名通过，与 0.20.34 使用相同证书，可覆盖安装。结果、APK 摘要与旧包备份在 `outputs/nxconnect/auto-connect-repair/`，没有写卡或上传发布。
