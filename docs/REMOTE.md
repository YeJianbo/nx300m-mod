# 开源遥控与相册

原生服务的相机身份检查已支持 Linux `/proc/PID/exe` 在可执行文件被替换后
附加的 ` (deleted)` 后缀。仍核对可执行名称、命令行、mount namespace 和
guest PID；不因实验室 QEMU 文件更新而误报存活相机的 hook 不可用。
实测修复前 LUT 选择返回503，修复并单独重启遥控服务后恢复为202，选择／读回／
恢复通过。主相机进程未重启，记录见
`outputs/looks/ricoh-blue-candidate/simulator-integration.json`。

入口：启动查看器后访问 [遥控／相册](http://127.0.0.1:8765/remote/)，或点击首页的“机身设置 / LUT / 遥控相册”。当前在私有模拟环境运行，不连接实体相机。

手机局域网测试：`python tools/simulator.py --profile development --host 0.0.0.0`，
手机与电脑连接同一网络，访问 `http://电脑局域网IP:8765/remote/`。
Android APK 下载入口为 `/downloads/nxconnect.apk`，控制端口和 CGI 端口均填 8765。
默认不传 `--host` 时仍只监听 `127.0.0.1`。

## 来源与构建

- 网页：`WApaperplane/metol@ddbe8b119e046798e557add13837834f5e8d90ac` 的 `scripts/nx-rc/web_root`。
- 原生服务的型号、版本、状态代码及 OSD 分块协议：`mewlips/nx-remote-controller-mod@1a931e2569e11154415bdf4685af06dfbcc268cf`。
- HTTP：`cesanta/mongoose@1b324510ccc86d340f7a3e678a0f8419a2bb7fd7`。字体来源与许可保留在 `work/reference/bootstrap-3.3.7`。

前两项沿用 AGPL-3.0；参考源码和许可留在 `work/reference`，本次适配代码在 `mod/metol/remote`。运行程序由源码构建，没有执行从上游下载的预编译遥控 daemon。

```powershell
conda activate nx300m-mod
python tools/build_remote.py
python tools/simulator.py --profile development
```

只改网页时可用 `python tools/build_remote.py --client-only`，无需重编 ARM 服务。资源在遥控服务下次启动时部署；已运行的服务需要结束后重新打开。完整构建日志在 `outputs/metol/remote/build.log`，当前链接原厂 glibc，只要求 GLIBC 2.4 / 2.7。

## 已接通的能力

| 能力 | 实现与验证 |
|---|---|
| 型号／版本 | 由原厂文件读取，当前 NX300M / 1.15 |
| 屏幕镜像 | ARM 服务读取原厂 CDisplayLCD 的 LCD 视频平面与 X11 OSD，按预乘 alpha 合成，再旋转为 800×480；80×40 分块，共 120 块。此接口不是传感器取景 |
| 按键与触摸 | ARM 服务经原厂 XTest 发送；网页“菜单”按钮已打开真实原厂菜单，原厂 Ecore 日志确认 Menu 按下／松开 |
| 相册 | 实时目录、JPEG/SRW内嵌预览及按需内存缓存；原文件由 Mongoose 传输，保留 Range |
| 退出清理 | 停止会话时结束遥控、Metol、网关和原厂会话；一次完整重启检查的旧 PID 均已消失 |

模拟相册使用从用户 SRW 原样提取的既有 JPEG 副本，位于运行副本的 `/mod/remote/media/DCIM/100PHOTO/SAM_4262.JPG`，没有修改 `F:/Pictures`。浏览器实际加载尺寸为 5472×3648；HTTP 返回 2,094,736 字节，与项目参考 JPEG 完全相同。部署时也读取 `outputs/looks/photo-reference.json` 中的 RAW 来源，将存在的 SRW 原样复制到同一私有相册，供 NXConnect 下载；来源和大小记录在 `outputs/metol/remote/media-fixture.json`。这些都是既有照片副本，不是新拍摄的照片。

OSD 首帧 1,536,972 字节，静止时下一帧 12 字节（头部和结束标记）。客户端保留上游分块校验和及字节字符串解码方式，响应类型必须为 `text/plain; charset=x-user-defined`。实测数据见 `outputs/metol/remote/integration.json`，界面见 `outputs/metol/remote/controller.png`。

## 网络与生命周期

ARM 服务监听相机会话私有网络中的 `127.0.0.1:8781`。WSL 常驻网关监听其初始网络中的 `127.0.0.1:8782`，每个请求线程进入相机网络命名空间。Windows 查看器只监听 `127.0.0.1:8765`，将 `/remote/`、`/api/v1/`、`/cgi-bin/`、`/DCIM/` 转发至网关；不逐帧启动 WSL。

查看器负责按需启动，`tools/session_stock.py` 根据真实进程判断运行状态。输入 5 秒无心跳时自动松开；这只解决输入保持，不代表缺失的拍摄硬件已经就绪。

网页与 NXConnect 共用这一 ARM 服务的屏幕、按键、状态和原图目录，无需再启动 NXConnect 专用 daemon。`camera/info` 通过 `capabilities` 声明 `screen_mirroring`、`key_input` 和 `keep_alive`，并明确 `sensor_liveview/liveview/lcd_control/silent_shutter/time_sync=false`；`osd` 对象返回实际图像与分块尺寸。已有网页所用的 `osd/input/gallery/lcd` 字段继续保留。

首页查看器通过 `remote_stock.read_osd_frame()` 读取同一服务的合成画面；未构建 ARM 服务时才使用原 X11/LCD 桥接。网页、首页与 Android 采用同一个屏幕来源。

`/api/v1/osd/get` 保留原先二进制增量协议和 `hashs` 参数，输出 RGBA 合成屏幕。当前模拟会话的视频是录制照片回放；没有模拟传感器或验证 ISP 色彩等效。`camera/info` 从实际 LCD 状态读取来源：`laboratory-lcd-backend` 为实验室后端，`stock-lcd-observer` 为原生只读观察模块，无视频层时为 `none` 并使用 X11 OSD。原生观察模块也用于隔离 fixture，来源标记不代表真机验证；`sensor_liveview` 继续为 false。原生模块的加载、条件和验证见 [LCD_CAPTURE.md](LCD_CAPTURE.md)。黑色菜单按原厂 alpha 遮住视频，不使用黑色抠除；Metol 的 RGB 窗口按其可见窗口范围视为不透明。当前视频解码采用与本机查看器一致的 BT.601 full-range CbCr 假设，实际硬件编码与颜色仍待核对。尚未支持的视频格式或正在更新的帧会返回 503，不用错误像素替代成功帧。

本次实际加载 ARM 服务后，已目视核对默认照片与 OSD、原厂黑色菜单、Metol 不透明窗口三种画面，并关闭面板恢复普通回放。与 Python 原合成器的逐像素差异各通道最大 1，来自整数颜色转换舍入；不能据此判断真实相机校色。完整帧为 1,536,972 字节，静止增量为 12 字节；SRW 的 `Range: bytes=0-63` 返回 206 与 `Content-Range: bytes 0-63/22487872`。数据和画面在 `outputs/metol/remote/screen-composition.json`、`screen-default.png`、`screen-black-menu.png`、`screen-metol.png`。

```powershell
python tools/session_stock.py
python tools/probe_remote.py
# 仅在需要检查会话停止／恢复时使用；会打断当前菜单操作。
python tools/probe_remote.py --restart
```

## 机身状态

`GET /api/v1/camera/status` 复用上游 NX300M 旧机型状态路径。每次直接读取 `/sys/devices/platform/d4keys-polled/mode`；不依赖上游输入监听线程或模式缓存。该 sysfs 枚举为 `0=Lens Priority`、`2=Wi-Fi`、`3=Smart`、`5=Smart Auto`、`6/7/8/9=P/A/S/M`，与 IQ 模式数值不同。只接受单个ASCII数字及前后空白；节点缺失、乱码、空内容或未识别枚举返回 `mode="UNKNOWN"`，避免上游 `atoi` 把无效内容误认成0。

`battery_charging` 读取 `/sys/devices/platform/jack/charger1_online`，仅接受单个 `0` 或 `1` 及前后空白，对应 `false` 或 `true`；文件缺失、读取失败及无效内容均返回 `null`。电量档位沿用 `/var/run/memory/Battery/Capacity` 偏移16处（第17字节）的ASCII数字读取；不足17字节或该字节非数字时返回-1。NX300M没有上游百分比来源，`battery_percent`保持-1。网页已有未知电量处理。

2026-10-21 ARM服务构建通过；`tools/probe_status_values.c`验证空内容、乱码、尾随字符、嵌入NUL和短电量记录均返回未知，合法值保留。主会话仅重新加载遥控服务，原厂相机PID保持28986；状态接口返回200。当前实验室没有这些真实硬件节点，因此仍返回未知，不生成模拟电量或拨盘读数。

状态修复已构建并部署到模拟会话，接口返回正常；实体设备动态变化尚未验证。模拟环境没有这些模式／充电节点，电量档位文件也缺失时继续显示未知；没有新增模拟值。设备验证时，连续查询该接口并转动未锁定的模式拨盘，预期模式随真实节点更新；插拔充电器时，预期充电字段与节点 `0/1` 一致。缺失节点仍应返回未知，不能据此判断为电池供电。

## 相机端网络发现

相机 ARM 服务每秒通过实际可用、HTTP 可访问的 IPv4 接口向该接口的子网广播 UDP 5681，载荷为 NUL 补齐到 64 字节的 `NX_REMOTE|0.9|型号|固件|接口MAC|`。来源 IP 与接口显式固定，接口晚启动、换址或关闭由下一轮枚举反映。仅监听回环或非 App 兼容的 HTTP 端口时不广播；媒体暂停也停止广播。它不会开启或重配原厂热点。

`GET /api/v1/network/status` 是只读接口，返回 `http_address`、`http_port`、`broadcast_port`、`broadcast_enabled`、`media_paused`、`packets_sent`、`send_failures`、`last_errno`、`interfaces_truncated`、`mac_address`、`instance_id` 和 `interfaces`。每个接口包含 `name`、`ipv4`、`netmask`、`broadcast_address`、`mac`、`http_reachable`、`broadcast_enabled`。地址来自内核接口状态，`http_reachable` 表示监听绑定覆盖该接口，不等于手机实际可达。无可用身份返回 null，不使用所有相机共用的占位身份。POST 返回 405；媒体暂停后 GET 仍可用，不访问 SD 卡。

网络定向检查在 `outputs/deployment/network-discovery/report.json`：使用真实生产发现模块和私有 namespace 的内核 dummy 接口接收报文，覆盖接口晚启动、多接口、换址、暂停、监听绑定、未知端口与接口关闭。HTTP 输出使用测试 sink，这不是实体 NX300M／手机验收；完整 ARM 服务启动和真实 HTTP 结果另见 MFW 包外 `ram-runtime/arm-vm/report.json`。

## 内嵌预览与配对相册

模拟器录制照片回放的青蓝偏色已修复：旧输入writer误把原厂输出的 `reverse_CbCr=1` 当作绝对CrCb内存格式，而实验室LCD解码器按CbCr读取。`tools/replay_ipcm.py` 现在给新包记录明确的 `chroma_order`，并识别旧包的原打包规则，在实际回放入口规范化顺序；没有改LUT或屏幕RGB解码公式。当前旧包重启后已实际恢复，720×480参考JPEG与导出LCD视频的平均RGB绝对误差由9.337降为1.359（0–255），证据为 `outputs/firmware-runtime/preview-color-repair.json`。该检查仅覆盖已有JPEG回放显示，不证明物理LCD打包规范、传感器ISP或理光风格校色。

`GET /api/v1/gallery/preview?p=100PHOTO%2FSAM_4262.SRW&size=grid` 返回原照片的JPEG预览；`size=full`选择大预览，省略size默认grid。能力字段为 `gallery_preview=true`。JPEG从EXIF取小图，SRW沿TIFF的IFD/子IFD取内嵌JPEG，支持II/MM字节序。无内嵌图的JPEG回退原JPEG；RAW无预览返回404，不把RAW文件当图片发送。视频缩略图未实现。

服务只为被请求的文件提取预览，以路径、文件身份、大小和mtime/ctime纳秒核对缓存，最多64项、8MiB；超限结果可返回但不缓存。文件改变后重新提取，既有照片不写缓存旁文件。`X-Preview-Cache`说明hit/miss，`X-Preview-Source/Width/Height/Orientation`给出实际来源及尺寸；没有缩放或像素重编码。父TIFF有方向而内嵌JPEG缺方向时，仅补EXIF Orientation；已有有效方向保留，`X-Preview-Metadata-Changed`明确是否修改了响应元数据。原文件下载不受此修改影响。

网页同目录唯一同名的SRW+JPEG合并成一格，保留两个实际文件路径。网格优先RAW内嵌小图；下载明确选择JPEG、RAW或两者。Quick Share保持单文件发送，配对项另外选择JPEG或RAW，不自动发送两个文件。原 `dirlist.files` 字符串数组保持兼容，新增 `sizes` 对象提供各原文件字节数。

当前NX300M样本的网格响应为160×120、12,506字节，重复请求命中缓存；大预览2,094,736字节与原SRW提取结果逐字节一致。原SRW的Range下载仍返回206并匹配原字节。实际ARM服务记录在 `outputs/metol/remote/gallery-session.json`；提取及方向元数据检查分别见 `outputs/metol/media-preview/report.json` 和 `media-preview-exif/report.json`。这些检查使用既有照片副本，不代表相机已生成新照片。

## 尚未接通

传感器取景、LCD硬件控制、任意尺寸JPEG重采样、视频缩略图、Wi-Fi推送和实机网络尚未验证。相册使用已有内嵌预览，不启动整卡预热。网页将LiveView／LCD标为未接通；模拟器读不到电池和模式时显示未知。

遥控接口可以发送原厂快门按键，但完整拍摄和录像成像尚未完成，可能使原厂 UI 等待硬件；恢复普通回放可使用首页停止、启动。当前已接通屏幕、输入与既有照片传输，实体相机安装包和实机网络仍待开发验证。

## 机身曝光设置

机身设置菜单现已增加 NX500 原厂表驱动的色相档位（0–20，中性 10），复用同一原生服务和颜色模块。`GET/POST /api/v1/color/chroma` 返回实际支持范围；当前饱和度仅支持中性 10，界面只读显示。网页已完成设置 2、原厂预览参数读回及复位 10；未验证 ISP 像素效果，完整源 SAT 算法仍在适配。协议、原厂表来源和运行证据见 [RECIPES.md](RECIPES.md#已接入的原厂-hue-表)。

网页 ISO 差量提交已完成一次实际多客户端检查：页面读取 Auto ISO 上限 3200 后，独立 API 客户端将其改为 6400；随后页面只修改步长为 1 EV，原生读回的上限保持 6400，未被旧页面值覆盖。读回保存在 `outputs/metol/remote/iso-concurrent-readback.json`；验证后已恢复 1/3 EV 和 3200。RGB 设置弹窗的深色主题标题／复位按钮对比度也已在内置浏览器目视核对。

原生 ARM 服务提供以下接口，网页原有设置菜单中的“机身设置…”入口提供 ISO、快门与 RGB 控制：

| API | GET | POST JSON 或表单 |
|---|---|---|
| `/api/v1/exposure/iso` | 读取三个 ISO UserData 项 | `iso_step`、`iso_expansion`、`auto_iso_max` 中至少一个 |
| `/api/v1/exposure/shutter` | 读取最快快门限制及原厂 getter 计数 | `fastest` |

`iso_step` 仅接受 `one-third`／`one-stop`；`iso_expansion` 的 JSON 类型必须为布尔值，表单值为 `true`／`false`；`auto_iso_max` 的 JSON 类型必须为整数，允许 125、160、200、250、320、400、500、640、800、1000、1250、1600、2000、2500、3200、6400。`fastest` 为字符串 `off`、`1/60`、`1/125`、`1/250`、`1/500`、`1/1000`、`1/2000`、`1/4000`、`1/6000`、`1/8000`。未知、重复、类型不符或越出已核实映射的参数返回 400；GET 不写参数。

接口直接使用 `runtime/exposure_controls.c` 与 `runtime/shutter_limit.c` 的原有请求／响应文件、随机请求 ID、原子重命名及同名 flock 锁，没有启动 Python 或另一个 HTTP 服务。等待锁和原厂响应均由 Mongoose 主循环非阻塞推进，同一时刻只接受一个曝光请求，等待期间继续处理屏幕与相册。锁等待 2 秒、每条请求有效期 6 秒、响应等待 8 秒，整个 HTTP 请求期限 12 秒；客户端应设置至少 14 秒超时。客户端断开后在途任务继续消费响应并释放锁，避免另一个客户端覆盖尚未完成的请求。

`camera/info.capabilities.iso_controls`／`shutter_limit` 来自 readiness 文件中的 guest PID，并与当前同挂载命名空间内的实际相机进程命令行及 `NSpid` 末项比对。旧内核缺少 `NSpid` 时使用实际 PID，缺少命名空间符号链接时核对进程根目录。不会仅凭 readiness 文件存在宣称就绪。未就绪返回 503，并发或锁占用返回 409，原厂错误／读回不符返回 502，超时返回 504。

成功的 JSON 字段兼容 `tools/exposure_controls.read_controls/set_controls` 与 `tools/shutter_limit.request`。原生服务将 `namespace_pid` 置为 `null`，另返回核实的 `camera_pid` 与 `native_service=true`，由宿主代理补充其自身的命名空间句柄。ISO 返回原始 `before_raw/actual_raw`、解码值及原厂接受／通知标志；快门返回原始限制值、getter 计数和最后读回。多项 ISO 设置逐项执行，失败不会回滚先前已应用的项；异步错误返回 `bridge_code/completed_items/mutation_may_have_been_applied`，有有效响应时附原始 `readback`。保留 `persistent=false` 与 AE 消费／物理曝光未验证标志，不将设置读回视为成像验证。

网页入口复用现有设置菜单。已完成原生构建、JavaScript 语法检查和部署，HTTP 返回的 `exposure.js` 与构建产物逐字节相同。原生路径的 ISO 三项设置、读回与恢复、最快快门配置及真实 getter 夹取均已通过；直接 API 返回 `native_service=true`，三个能力字段实际为 true。证据为 `outputs/metol/remote/exposure-integration.json` 和 `outputs/controls/native-controls-validation.json`。主页旧路由现在只是该原生服务的适配代理。Android 新设置界面已构建，界面操作尚未动态验收。拨盘锁的统一原生接口见下节。

当前面板打开时重新读取机身值，读取成功后才允许写入；ISO 仅提交相对本次读取发生变化的字段，避免用网页中的未改值覆盖 Android 刚修改的其他 ISO 项。写入失败、失联或读回无效后禁用写入，需手动重新读取。读取某组失败时该组保持不可写，不把 capability=true 当作已成功读回。

## 指令拨盘锁

`GET /api/v1/controls/dial-lock` 读取；`POST` 仅接受 `{"enabled":true}` 或 `{"enabled":false}`。未知、重复、缺失字段、字符串、数字及表单请求返回 400。成功响应为 `{"enabled":false,"native_service":true,"camera_pid":46,"persistent":false}`，其中 PID 为当前实际相机，布尔值为读回结果。

接口沿用 `ui_mod` 的 `/var/run/nx300m-dial-lock` 文件，缺文件默认未锁定；存在时只接受单个 `0` 或 `1` 及前后空白。空文件、其他数字或多余内容返回 502，并以 `enabled:null` 报告无法取得有效状态。显式 POST 会写入规范的 `0\n`／`1\n`，可以修复异常状态文件。文件锁为独立的 `/var/run/nx300m-dial-lock.lock`，GET／POST 均 `LOCK_EX|LOCK_NB`，争用立即返回 409；临时文件完整写入后原子重命名，再校验读回。没有新增后台服务或任务队列。

`camera/info.capabilities.dial_lock` 复用 `nx300m-pw-ready` 的当前相机身份核验，表示同一 `ui_mod` 的原厂按键拦截桥已就绪。操作前后重新核验 ready PID；无 hook 或身份变化返回 503，文件／锁 I/O 失败返回 500，写后读回不符返回 502。写后发生失败时保留 `mutation_may_have_been_applied=true`。状态只在当前会话有效。

网页“机身设置…”增加“指令拨盘”组，成功读取后才能勾选并应用，值未变化时不提交；读取失败或写入失败后遵循现有禁写及重新读取约定。已有 ISO、原厂画面参数、RGB 和 LUT 的值不因拨盘锁操作改变。

已完成 ARM 构建、JavaScript 语法检查和重新加载后的动态验收。网页将锁设为开启，原生接口及首页旧 `/dial-lock` 代理均读回 true；向真实相机进程发送一次 `XF86ScrollUp`，按下与释放均记录 `blocked=1`。数字、未知及重复字段均返回400。随后通过旧代理恢复 false，原生接口及网页重新读取均为未锁定。证据为 `outputs/metol/remote/dial-lock-integration.json` 和 `dial-lock-panel.png`；本次相机 guest PID67，未验证实体拨盘。

## RGB 通道增益

`GET /api/v1/color/rgb` 只读取；`POST` 接受 JSON `{"red":105,"green":100,"blue":95}`，三个字段必须同时给出，类型为 0–200 的整数，100 为中性。复位使用 `{"reset":true}`，与 RGB 字段互斥。也接受同名表单值；未知、重复、缺失、字符串形式的 JSON 数字、非整数和越界值均返回 400。此接口沿用同一个 ARM remote 服务，没有另起 Python HTTP 后端。

桥接复用曝光任务的非阻塞锁、随机请求 ID、原子发布、超时与断线清理；使用 `/var/run/nx300m-color-style-{ready,request,response,lock}`。读／设定／复位分别对应原厂桥操作 2／1／0。RGB 与曝光请求共用单个在途任务，其他机身控制请求在任务完成前返回 409，屏幕和相册仍可继续处理。`camera/info.capabilities.color_style` 复用同一相机进程身份核验，响应中的 guest PID 还需与 ready PID 一致；未就绪、身份改变、协议错误与超时分别按现有控制接口返回 503／502／504。

成功响应保留桥的 17 个字段：`request_id/bridge_result/red/green/blue/active/capture_calls/preview_calls/capture_range_bypasses/preview_range_bypasses/capture_setter_result/preview_setter_result/capture_base_available/preview_base_available/capture_modified/preview_modified/guest_pid`；另含 `camera_pid/native_service/namespace_pid/operation/persistent/image_verified/applied_to`。原厂 setter 返回值和拍摄／预览矩阵是否可用、是否修改均原样返回。HTTP 200 表示桥操作成功及增益读回一致；某个 setter 是否接受修改由对应字段表示，尚未收到该路径矩阵时不会编造已应用状态。发生桥错误或读回不符时，错误响应保留有效的 `readback` 与 `mutation_may_have_been_applied`。

网页按 `color_style` 能力显示 RGB 数字输入、应用和复位按钮，首次打开不设默认数值、不写机身；成功读取后才能应用。弹窗标题、帮助文字与默认按钮使用现有主题变量。增益作用于最近原厂 Q8 色彩矩阵的输出行，只在当前会话生效，`image_verified=false`。

原生后端与网页资源已构建，JavaScript 语法检查通过。重新加载后，原生 HTTP 接口及内置浏览器面板均已完成读取中性值 → 设置 105/100/95 → 读回 → 复位为 100/100/100。`outputs/metol/remote/color-integration.json` 记录 `color_style=true`、`native_service=true`、匹配的 guest/camera PID 46，以及预览原厂 setter 返回 0、`preview_modified` 从 0 变为 1 再恢复 0；混合 RGB 与 reset 参数返回 400。拍摄路径尚未收到原厂矩阵，`capture_base_available=0`。当前画面是实验室 LCD 录制照片回放，不代表 ISP 增益效果；这些结果不构成图像校准或实体相机验证。失败后禁写行为仍待定向动态验证；ISO 差量提交已完成前述多客户端检查。此前 `outputs/metol/color-style-session.json` 是原有文件桥的读／写／恢复记录。

## 原厂 Picture Wizard 参数

`GET /api/v1/color/picture-wizard` 读取原厂设置存储及其 IQ 消费端；`POST` 仅接受 JSON 对象，至少含下列一项，按字段差量更新：

| 字段 | 整数范围 |
|---|---|
| `saturation` | -4 至 4 |
| `sharpness` | -4 至 4 |
| `contrast` | -4 至 4 |
| `color_tone` | -1 至 359，-1 关闭色彩调整 |

例如 `{"saturation":1,"color_tone":-1}`。未知、重复、空对象、字符串、布尔值、非整数、越界及表单请求均返回 400。GET 不写入；POST 成功前逐项核对实际 raw 读回。接口返回 `values/raw/iq_raw/iq_matches/decoded_indices/iq_mode/scalar_mode_enabled/image_verified/persistent`，另含 `native_service=true`、匹配的 `camera_pid` 和 `namespace_pid=null`。无法按已核实编码解码的 raw 对应 `values` 为 `null`，仍保留实际 raw。

此接口作为第四种任务复用现有 `ex_task`、非阻塞文件锁、随机请求 ID、原子发布及截止时间，不新增服务。文件前缀为 `/var/run/nx300m-pw`；`-ready` 中单个 guest PID 必须属于当前实际原厂相机，在发送前、轮询及接受响应后重新核验。桥请求仍为 7 字段 `id mask expires satRaw sharpRaw contrastRaw toneRaw`，响应仍为 15 字段；响应中的 `raw/iq_raw` 数组顺序为色彩角度、饱和度、锐度、对比度。后三项编码为 `(value << 20) | 0xff004` 的 32 位值，色彩角度 -1 编码为 `0xffffffff`。IQ 原始读回及原厂解码索引直接保留，`scalar_mode_enabled` 对应原厂 IQ 模式 1、2、3、4、8。

`camera/info.capabilities.picture_wizard` 只表示当前 Ecore 参数桥就绪。未就绪或身份变化返回 503，并发／锁占用返回 409，桥协议错误／原厂错误／变化项读回不符返回 502，超时返回 504；有效错误响应保留实际 `readback` 及可能已写入标志。IQ 与设置存储不一致或当前模式未启用三项参数时，按实际状态报告，不把它改写成成功消费的结论。

网页“机身设置…”按能力显示“原厂画面参数”，成功读取后才能修改，仅提交相对本次读取变化的字段；写入失败或读回无效后要求重新读取。该入口提供四项原厂参数，不是完整七维配方。`persistent=false/image_verified=false` 保留。

已完成原生构建、JavaScript语法检查及主会话动态验收：设置2/-1/1/120后，四项值、原厂IQ读回及独立CLI均一致，随后完整恢复0/0/0/-1。布尔、数字字符串、越界、未知字段及空对象均被拒绝且未改变设置。首页旧`/picture-wizard`也已转为同一原生接口的代理。浏览器读取原值后，另一API客户端将对比度改为2；网页仅把饱和度改为1，最终原生读回保留了对比度2。参数随后恢复原值。证据为 `outputs/metol/remote/picture-wizard-integration.json`、`picture-wizard-delta.json`；此结果不包含ISP像素或机内JPEG效果。

## 原生 LUT 风格入口

设置菜单中的“LUT 风格…”读取原生服务根目录下实际部署的 `looks.txt`，展示当前选择及原厂状态；选择和“恢复原厂”均先读取后操作。`NX300M_MOD_ROOT` 默认为实验室 `/mod`，SD 使用 `/mnt/mmc/nx300m-mod`。网页资源包含独立的 `looks.js/looks.css`，沿用当前主题。

| API | 参数与响应 |
|---|---|
| `GET /api/v1/looks` | 返回实际 `root`、`looks`、`selection`、`hook`、`status` |
| `POST /api/v1/looks/select` | JSON 仅含 `{"id":"metol-portra400"}`；必须是已部署列表中的合法 ID 和有效表 |
| `POST /api/v1/looks/stock` | 空 body 或 `{}`；清除当前选择，等待原厂下一次 LUT 调用 |

GET 返回 200；成功暂存或清除返回 202，表示请求已暂存，不表示原厂已写入或图像效果已实现。`looks` 各项含 `id/label/table_valid/composed_table_available/selectable`；`selection` 含 `stock/valid/id/generation`。`hook.ready` 要求 marker 为 `<guest_pid> <root>`，PID 属于同一挂载命名空间的实际原厂相机，root 与服务配置一致；`camera/info.capabilities.looks` 表示已部署清单可访问，`lut_selection` 表示当前 hook 匹配。缺 hook 时仍可读取列表，写入返回 503。

原生服务与 ARM CLI 共用 `mod/metol/lut_engine_shared.h` 的 ID、清单、文件路径、表校验及代次逻辑，直接操作同一选择文件；没有 shell、子进程或 Python HTTP 后端。清单最多 256 项，ID 为 1–63 个小写字母、数字、下划线或连字符并以字母／数字开头，显示名称最多 191 个 UTF-8 字节。根目录必须实际存在，清单和表解析后的路径必须留在对应根目录及条目中；表必须为 4380 字节、三路 10 位数据且各路填充字为零。若可选的组合表存在，也必须通过同样校验。非法／重复／缺失参数返回 400，未列出的 ID 返回 404，坏表返回 422；根目录或清单不可用返回 503。列表用共享锁、写入用独占锁，两者均 `LOCK_NB`，与 NXUI 争用时立即返回 409。

“恢复原厂”直接清除 `/var/run/nx300m-lut-selection`，不调用 engine `off`，不读取 `/var/run/nx300m-lut-panel-backup`。NXUI 的 `begin/off` 继续保留进入前备份／恢复语义；下一次选择仍从独立计数器取得新代次。

`status.current_camera` 只认可当前 hook 的 PID/root；`selection_matches` 另核对代次和 `selection_path`。仅当前选择匹配、原厂状态为 `forwarded-to-stock-api` 且三路 `forwarded_mask=7` 时，`forwarded=true`；旧状态只作为 `observed_stage/forwarded_mask/composition` 观察值，不会被算作本次转发。网页区分“已暂存，等待原厂调用”“三路已转发”和“已选择原厂输入，等待调用”，提供手动刷新。`persistent=false/image_verified=false` 始终保留；当前录制回放不显示 ISP 风格效果。

已完成 ARM remote／Metol 构建、JavaScript 语法检查和重新加载后的动态验收：17项表均有效，GR IV负片／正片Exact选择均返回202并可读回；CLI `begin/off`能恢复API切换前的负片选择，HTTP `stock`明确清空选择。内置浏览器实际选择负片并显示暂存状态，随后恢复原厂。证据为 `outputs/metol/remote/looks-integration.json` 和 `looks-panel.png`。原厂app在ELF构造器后切换进程，hook标记现于真实Ecore主循环入口发布，当前PID与root匹配；ISO、快门、RGB桥仍全部就绪。此次没有原厂still LUT调用，`forwarded=false`、`image_verified=false`。原有原厂节点／RAM写表证据见 [LOOKS.md](LOOKS.md)。

## JPEG 回写与原始备份

NXConnect 查看器通过主动操作回写编辑后的 JPEG，也可还原首次回写前的文件。
原生服务只允许媒体根目录内已有的 `.jpg/.jpeg`，最大 120 MiB，不修改 RAW。
首次回写在同目录保留隐藏的 `.<文件名>.nxoriginal`，后续回写不替换这份备份。
备份完成并同步目录后才提交新图；上传与恢复使用独占锁及同目录临时文件，
完整写入、同步和关闭成功后以 rename 替换目标。失败时保留本地编辑供重试。

| API | 用途 |
|---|---|
| `GET /api/v1/gallery/upload/capabilities` | 协商 `backup_available/restore_available/original_download_available/jpeg_only/max_bytes` |
| `POST/PUT /api/v1/gallery/upload?p=相对路径&require_backup=1` | JPEG 二进制 body；成功返回 `ok/backup_available/bytes` |
| `GET /api/v1/gallery/edit-status?p=相对路径` | 查询当前大小及原始备份是否存在 |
| `GET /api/v1/gallery/original?p=相对JPEG路径` | 下载首次原始备份；缺失返回404，非法备份返回409 |
| `POST /api/v1/gallery/restore?p=相对路径&require_backup=1` | 从首次备份恢复；成功返回实际字节数 |

`camera/info.capabilities` 新增 `gallery_writeback` 与 `gallery_original_backup`。
Android 先协商能力，上传后整文件逐字节下载核对；恢复前下载首次备份到私有临时文件，恢复后再次完整下载并逐字节比较内容及长度，通过后刷新查看器与相册大小。恢复需要 `original_download_available=true`，旧服务在写入前拒绝。
旧服务、缺少备份或读回不一致时不清除本地修改。HTTP 失败不再降级为无备份 Telnet 覆盖。
并发编辑返回 409，非法输入返回 400；写入／恢复失败返回 500。
上传体须包含 JPEG 起始与结束标记，拒绝已知的客户端二进制截断；此检查不替代完整解码。
浏览器的跨来源修改请求被拒绝，开发代理为备份及 SD 同步保留较长等待时间。

宿主 C 故障检查已覆盖部分写入、备份失败、不可变首次备份、恢复、锁和符号链接拒绝。
最终 ARM 原生 HTTP 已用生成的634字节JPEG确认两次完整回写、首次备份不变及逐字节恢复，
四字节截断返回400；记录见 `outputs/deployment/app-loader/installed-vm/probe-report.json`。
实体相机和手机回写仍需连接设备确认。

## 机身原生 Quick Share

独立 MFW 服务的运行资源现在由 RAM 候选提供；这不改变下述 Quick Share 的网络条件及对端验收范围。

单一 ARM HTTP 服务可异步启动 `/mod/quickshare/nx-quickshare-armv7`，由机身上的协议程序直接打开同一图库文件并执行原生协议；HTTP 只承载任务控制和状态。没有新增 HTTP 传图替代协议或另一套相册。`tools/remote_stock.py` 在产物存在时将 `outputs/quickshare/nx-quickshare-armv7` 部署到运行副本，模拟器显式配置 `--quickshare-runner /qemu-arm-static`；实体机直接执行时省略 runner。`capabilities.quickshare_native` 仅在配置的程序与 runner 可执行时为 true；它表示任务桥可用，不代表已验证三星官方设备互通。

网页入口在现有相册工具条内：使用“选择”勾选一个文件，点击“发现设备”并选择发现到的接收设备，再点击“发送所选文件”。发送仅由该按钮触发；页面显示原生协议返回的 PIN、字节进度、任务状态及日志，可取消当前任务。能力缺失时操作禁用。SRW/DNG 与 JPEG 共用图库和选择状态；RAW优先显示内嵌预览，缺失时显示占位，不请求浏览器解码原始文件。RAW+JPEG配对项须选择其中一种格式后发送。新增页面脚本由 `tools/build_remote.py --client-only` 生成，已完成构建、JavaScript 语法检查与静态资源部署；HTTP 实际返回和源文件一致，记录在 `outputs/metol/remote/quickshare-web.json`。网页到外部接收设备的发送流程尚未实测。

| API | 方法与参数 |
|---|---|
| `/api/v1/quickshare/discover` | POST 表单 `duration=15`，允许 1..60 秒，缺省 15 |
| `/api/v1/quickshare/send` | POST 表单 `endpoint=IPv4:port&name=接收设备名&path=100PHOTO/SAM_4262.JPG`，每次一个文件 |
| `/api/v1/quickshare/status` | GET，读取当前或最近任务 |
| `/api/v1/quickshare/cancel` | POST，可选 `task_id=编号`；编号不符返回 409，匹配后终止任务；2 秒内未退出则 SIGKILL |

任务启动返回 202；并发第二个任务返回 409；未部署可执行程序返回 503。发送路径复用图库的 `media_path()`，拒绝越界、目录和不可读文件；端点仅接受 IPv4 与 1..65535 端口。子进程使用 `fork/execv` 参数数组，无 shell。

状态包含 `available/running/task_id/operation/state/exit_code/signal/cancel_requested`，以及 `events:[{sequence,line}]`、`events_dropped/stderr/output_truncated`。`line` 是 CLI 原始 JSON 行，最多保留最近 32 行、每行最多 8191 字节；超长整行丢弃并标记截断，stderr 保留最后 32767 字节。设备发现来自 `discovered.endpoint`；PIN、字节进度与传输阶段来自 `transfer.message`。当前后端为 Google/Nearby 通道。页面分别读取 `sent_bytes`（本地写出）、`ack_bytes`（逐文件确认）和 `completion_basis`（例如 `safe_disconnect_ack`）；安全断开 ACK 不转换为逐文件确认字节数。服务不编造完成事件或收件确认：发送成功需要真实 `Finished` 事件和退出码 0，单独退出码 0 不替代该事件。取消与失败保留真实退出码或终止信号。相机会话退出时会终止并回收任务子进程。

已在相机私有网络命名空间中实际验证：未部署程序时 `available=false` 且发现请求返回 503；部署 ARM 程序后 `available=true`，2 秒发现任务返回 202 并以真实退出码 0 结束，并发请求返回 409；取消 60 秒发现任务记录真实 SIGTERM 15，最终 `state=cancelled`。非法端口、越界路径和目录均返回 400。该次发现未收到设备事件，也没有接受任何发送任务；没有向外部设备发送照片。证据在 `outputs/metol/remote/quickshare-bridge.json`。原生协议自身的受控文件回环与正式设备互通边界见 Quick Share 模块记录。

## MFW RAM 诊断日志

`GET /api/v1/diagnostics` 列出本次 RAM 运行的固定日志及可读状态；`GET /api/v1/diagnostics?file=remote.log` 读取其中一个文件。允许名称为 `mfw-entry.log`、`remote.log`、`supervisor.log`、`service.state`、`remote.pid`、`latest-diagnostic` 和 `bootstrap.log`。

接口只接受 GET，只读取实际位于 tmpfs 的普通文件，拒绝未列出的路径和最终文件符号链接。不提供任意文件浏览；没有 RAM 状态目录时返回 503，文件不可用返回 404。日志最大返回末尾 256 KiB，`X-Log-Offset` 记录起始偏移。断电后 RAM 日志可能消失，应在退出服务或断电前通过已有连接取回，启动程序不会自动将日志写回 SD。

真实 ARM Mongoose、原厂 glibc 和生产诊断处理函数的有限检查已通过：日志列表、日志尾部、路径越界拒绝、符号链接拒绝、POST 拒绝和 bootstrap 日志读取。报告为 `outputs/deployment/mfw-services/ram-runtime/diagnostics-report.json`；此检查没有启动相机程序、实体服务或主模拟器。

## MFW 媒体暂停

开发接口 `POST /api/v1/media/pause` 暂停本服务的媒体访问，返回 202；调用方再用 `GET /api/v1/media/status` 等待 `service_media_released=true`。202 只表示已接受暂停，排空可能尚未完成。接口要求资源、网页和状态目录实际在 tmpfs；非 RAM 部署返回 503。

暂停后拒绝相册列表、预览、原图下载、回写、恢复、Quick Share 新任务及远程控制等请求，返回 503 和 `code=media_paused`。已有原图下载连接被关闭，并等待 Mongoose 销毁连接、释放文件；每个原图连接只允许一次文件流并返回 `Connection: close`，额外流水线请求直接关闭，防止旧版 Mongoose 覆盖原 FILE 导致引用泄漏。Quick Share 使用现有取消流程，必要时两秒后 SIGKILL，回收子进程后才允许报告释放。面板的来源卡读取与持久偏好操作共用 RAM 文件锁；排空须等待已开始的面板操作完成，之后拒绝新的操作。Fn 快捷入口同时暂停。

健康、相机状态／信息、USB 只读观察、RAM 诊断、Quick Share 状态／取消及 RAM 网页仍可读取；`camera/info.media_paused` 同步暂停状态。暂停标记位于 RAM 的 `state/media-paused`，服务重启继续暂停，重复请求不恢复访问。当前没有 HTTP 恢复接口：原厂挂载恢复与块设备交还尚未可靠确认，不能仅根据 LUN 变空、路径重新出现或固定等待时间恢复读写。

`service_media_released` 的范围是本 remote 服务、它管理的 Quick Share 子进程及配套面板脚本；不是原厂相机 app 的卸载确认。状态固定返回 `scope=remote_service_only`、`stock_usb_handoff_verified=false`、`resume_supported=false`，不会切换 USB、卸载介质或刷机。独立原厂进程／其他 Mod 不受该锁控制。

定向检查使用生产媒体路径、下载函数、暂停处理函数和 Quick Share 进程管理代码，实际 ARM Mongoose 与原厂 glibc 经 QEMU user 运行；28 项通过，包括正常下载逐字节完整、双 GET 流水线不泄漏照片 FD、慢速下载的照片 FD 实际关闭、忽略 SIGTERM 的替身子进程被终止和回收、面板锁阻止提前报完成、暂停跨服务重启保留。报告为 `outputs/deployment/mfw-services/ram-runtime/media-pause-report.json`，可用 `tools/probe_media_pause.py` 在 WSL 重现。这不包含真实 Quick Share 协议、相机进程身份、原厂 USB 切换或实体卡验收。


## USB 只读观察与暂停触发

`GET /api/v1/storage/status` 读取固定 sysfs LUN、USB cable／mode 及 `/proc/self/mountinfo`，不扫描或 stat 媒体树。返回 `raw_backing=open/closed/unknown`、`usb_cable`、`usb_mode`、挂载点／设备／文件系统及 `changed_since_start`。节点缺失和读取失败保持 unknown；挂载 ID 变化仅作线索。

观察到 backing 非空且 USB 未明确断开时暂停媒体；这是事后信号，无法阻止此前已经发生的原厂 raw 导出。接口固定返回 `observation_only=true`、`stock_usb_handoff_verified=false`、`resume_supported=false`。LUN 关闭、拔线或新 mount ID 均不自动恢复。暂停状态另含 `pause_persisted`、`pause_reason`；标记写入失败仍阻断访问，且不报告 `service_media_released=true`。

上述定向 ARM 报告同时覆盖缺失／读失败节点、mountinfo 最长匹配、备份精确下载、方法／越界／符号链接拒绝、USB 信号触发暂停及新挂载不恢复。sysfs 和 mountinfo 使用普通文件 fixture，不是物理 USB 或内核 exFAT 验证。


RAM 完整服务的 LUT 接口按核验后的 `NX300M_MOD_SOURCE_ROOT`／`NX300M_MOD_SOURCE_ID` 识别原厂 hook 的数据根，不再用 RAM 程序路径匹配 SD hook。`hook`、原厂观察状态和 selection 路径使用同一数据根；身份缺失或变化时不可选，不回退别库。原厂 app 的 root 与环境不变；具体专项范围见 [LOOKS.md](LOOKS.md)。
