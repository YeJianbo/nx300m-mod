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
