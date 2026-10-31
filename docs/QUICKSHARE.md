# NX300M Quick Share

目标是相机自己运行 Nearby/Quick Share 协议，直接将照片或 RAW 传到手机或电脑。Android 端的系统分享菜单不是本功能的替代实现。

## 当前互通状态（2026-10-23）

本轮重写相机原生分享面板：浅色圆角附件卡、格式胶囊、双列设备卡、阶段标题、验证码和进度卡、底部操作；长设备名按 UTF-8 边界省略。传输和等待完成确认阶段收起旧 PIN。发送进度达到 100% 时仍显示“等待接收端确认”，沿用原协议完成条件。

`outputs/quickshare/gallery/oneui-preview/report.json` 和同目录截图记录本轮真实 ARM 面板与 ARM 服务的隔离 UI 检查：触屏选择第二设备、发送请求只有一次、返回关闭与父窗口焦点恢复均通过。网络对端为本地 fixture，只用于界面状态展示，不是新一次手机互通测试。主模拟器仅原子更新面板文件，实际字节与构建产物相同；相机 PID 与启动时间不变，见 `main-panel-update.json`。更新在下一次打开分享面板时生效。下文带 namespace/PID 的条目为历史验证记录，不代表当前进程身份。

分享面板现与配方/LUT面板共用 `mod/metol/panel_input.h` 的半按、焦点和
输入处理。X11 `Super_L` 按下立即隐藏面板，取消面板持有的任务，将实际
捕获的按下/释放转交原窗口；仅收到释放时不关闭、不抢走面板焦点。
evdev S1 按下只触发退出，不重复注入 X11，也不生成 S2 全按。

真实原厂相册与协议验证见 `stock-protocol-cancel-s1/report.json`：PIN 等待
阶段半按，sender=cancelled、exit1、signal=null，接收端实际记录 Cancelled，
未接收文件。原厂随后回到拍摄态，菜单可操作；不能把半按路径称为“回到
同一张相册照片”。独立会话已清理，主会话身份未变。物理机 evdev 时序尚未验证。

取消修复已验证：`stock-protocol-cancel-pin/report.json` 记录真实相册发起分享、双方 PIN 一致后从面板返回。发送端 `state=cancelled`、`exit_code=1`、`signal=null`，本地取消与清理均完成；受控接收端实际记录 `Cancelled`，没有授权接收或生成文件。相册返回后菜单仍可打开，独立测试会话已清理。测试覆盖等待接收确认阶段；大文件数据传输中和拥塞网络的取消尚未验证。网络阻塞时协议 CANCEL 为尽力发送，仍按有界清理退出，不宣称远端确认。ARM/Windows CLI、相册面板与服务构建通过，SD 候选包已更新。

最新复测：正常主模拟器已重启为 namespace `19623`，加载默认 QEMU 11.0.2 及当前相册模块。浏览器实际验证单张回放 → Share(Wi-Fi) → Quick Share 携带 `SAM_4262.JPG` → 返回同一照片。主会话没有选择接收设备或发送用户照片；扫描结束显示未发现设备。

新版多地址发现器已通过真实原厂相册到受控 ARM 接收端的完整复测，退出 0：真实 mDNS、PIN、UKEY2 和加密传输，收到 33618 字节生成 JPEG，与发送文件逐字节一致，可解码为 640×480。完成后返回原厂相册并能再次打开菜单；隔离测试会话已清理，主会话保持运行。证据：`outputs/quickshare/gallery/stock-protocol-transfer-final/report.json` 及同目录截图。这次未重新验证手机收件、实体机无线网络或传输中取消。

主模拟器已重启加载最新多选与错误处理，该轮验证会话 namespace 为 `28897`。浏览器实际操作回放 → 缩略图 → Share(Wi-Fi) → 勾选 → Upload，打开 Quick Share 并显示 `SAM_4262.JPG`；返回后勾选保留。主卡只有一组 RAW+JPEG，此次验证新版在主会话的选择入口和返回，双照片交接证据仍见独立会话。截图为 `main-multiselect-installed.png` 和 `main-multiselect-return.png`。未发送照片到手机。

原厂缩略图多选已在独立原厂程序会话验证：回放 → 缩略图 → Menu → Share(Wi-Fi) → 勾选两张 → Upload，实际面板参数为 `SAM_4262.JPG`、`SAM_4263.JPG`。返回后两项仍勾选，取消第一项再分享时仅携带 `SAM_4263.JPG`，没有沿用上次批次。使用原厂选择目标 `0xa`；`0xf` 会关闭复选框并进入单张预览，不适合多选。原生面板、启动脚本和服务统一支持 1–16 个文件。此路径当前限定普通 DCIM 模式；RAW+JPEG 项初始交接 JPEG，原生面板现可展开为 JPEG、RAW 或两者。证据在 `outputs/quickshare/gallery/stock-multiselect/`。该独立验证没有向手机发送照片，也未重启主模拟器；多选 hook 随后已集成到上述主会话及候选包。

2026-10-21 主查看器集成复验：`8765` 的原厂回放 → MENU → Share(Wi-Fi) → OK 已实际打开 Quick Share，并携带 `SAM_4262.JPG`；返回后恢复同一照片，原厂菜单仍可操作。主会话启用软件 JPEG 解码、回放和相册分享，遥控服务保持单实例。修复了主查看器把 Quick Share RGB 窗口误当作带透明度 OSD、导致照片透底的问题；原生遥控和备用截图路径均识别该不透明窗口。最新 ARM 构建退出 0，SD 候选包已更新。截图及记录：`outputs/quickshare/gallery/main-share-open.png`、`main-gallery-report.json`。此次仅验证隔离模拟器相册交接与返回，没有重新验证手机收件或实体机安装。

| 通道 | 已验证范围 | 尚未完成 |
|---|---|---|
| Google Nearby / Quick Share | 独立 ARM/QEMU → 外版 Galaxy Z Flip6，74 字节生成文本；手机实际收到且用户确认能打开 | 实体 NX300M 无线网络、相册入口到手机的完整流程、大照片/RAW与多文件 |
| 三星原生 Quick Share / S 互传 | 本机三星 Windows 官方程序 2.3.22.0 存在；尚无该通道传输证据 | 国行 S23 的发现、握手、文件传输及相机移植 |

成功证据：`outputs/quickshare/phone-test-20261020T133401Z/arm-send-report.json`、同目录 stdout/stderr 与生成文本。新版请求安全断开，Flip6 在约 72 ms 后返回 `request_safe_to_disconnect=true, ack_safe_to_disconnect=true`，程序随后 Finished、退出 0；用户另行确认文件收到并能打开。此结果是 **Google/Nearby 通道在 ARM/QEMU 下的真实手机互通**，不是实体相机或三星独立协议已通过。

原始失败 `flip6-test-20261020T122115Z` 与两次停在 100% 的后续测试保留为失败，不覆盖为成功。`loopback-arm-ack-20261020` 是新构建完成前误用旧产物的预检查，不能用作 ACK 修复证据；新版受控回环使用 `loopback-arm-safe-disconnect.jsonl`，262267 字节与双方 PIN 一致。

### 本次协议修复与来源

- 按 [rquickshare PR #439](https://github.com/Martichou/rquickshare/pull/439) 接受 17 字节无名称 mDNS 信息，使用 hostname 显示。普通 Windows discover 已发现 Flip6；独立 WSL/ARM discover 仍未输出设备。本次地址由 Windows 重新发现，随后 ARM 直接连接手机，Windows 不代理文件流。
- 按 [Bada PR #108](https://github.com/kyujin-cho/Bada/pull/108) 及其 `IntroductionFrames.kt`、`OutboundConnectionDriver.kt` 核对：附件提供非零且唯一的 `FileMetadata.id`，与 payload ID 对应；Introduction 提供 `use_case=NEARBY_SHARE`。
- 连接响应声明已实现的安全断开版本 1；文件仍使用独立空结束包。所有文件写出后主动发送安全断开请求，处理手机返回的 ACK、取消和错误。没有固定 sleep 作为成功条件。
- 新版 [Google Nearby proto](https://github.com/google/nearby/blob/main/connections/implementation/proto/offline_wire_formats.proto) 有独立 `PAYLOAD_ACK=3`，旧 CONTROL ACK 亦可解析。Google 的文件 ACK 特性需要版本协商；Flip6 返回版本 4，不能无条件等待版本 6 才提供的能力。
- `sent_bytes` 是本地写出量，`ack_bytes` 仅是收到逐文件 ACK 的量；`completion_basis=safe_disconnect_ack` 表示完成了安全断开确认，不伪造逐文件 ACK。单独 EOF、超时或本地发完不置 Finished。
- 发送按块交替处理远端响应；帧长前缀跨事件保存，避免取消 `read_exact` 丢失部分前缀。测试接收器写满并同步文件后确认，保留连接回应安全断开请求。

目前已完成 Windows/ARM 构建、针对短广播与 ACK 的核心回归、独立 ARM 加密回环和上述 Flip6 实机收件验证。安全断开 ACK 是协议层证据；官方接收端实际落盘仍由单独的手机结果确认。

### 现成实现调查与系统集成

检索时间：2026-10-20。已查项目首页、相关代码、三星互通记录以及 GitHub 仓库检索；没有找到已核验支持**国行 S23 独立三星协议**、可直接移植到相机的开源发送器。这是本次检索范围的结论，不是证明不存在。

| 项目 | 实际实现 | 对 NX300M 的用途 |
|---|---|---|
| [Bada](https://github.com/kyujin-cho/Bada) | Kotlin/JVM 自行实现 Nearby，不依赖其本机 GMS；文档记录三星 One UI 互通 | 本次附件与安全断开修复已参考；不是国行三星独立协议的验证 |
| [open-quickshare](https://github.com/ignotusbucius/open-quickshare) | rquickshare 派生 Rust 核心，扩展 BLE/L2CAP、Wi-Fi LAN/Direct | 可作为后续传输层参考；依赖 BlueZ 5.x、NetworkManager，作者明确三星尚未验证 |
| [omarchy-samsung-quickshare](https://github.com/marcho78/omarchy-samsung-quickshare) | rquickshare + Python/QML 桌面集成，记录 Galaxy S26 Ultra 测试 | 可参考任务/界面封装，名称含 Samsung 但不是另一套原生协议 |
| [kidfromjupiter/nearby](https://github.com/kidfromjupiter/nearby) | Google Nearby C++ 的 Linux 平台实现 | 依赖 systemd、NetworkManager、BlueZ ≥5.85，不适合直接替换当前旧内核 ARM 移植 |
| [Samsung-Quick-Share-4-All](https://github.com/magliocchetti/Samsung-Quick-Share-4-All) | Windows 官方程序的注册表兼容脚本 | 不是协议实现，不能在相机上运行 |

三星原生通道可继续调查本机官方程序的 `QSIP.dll`、`QSFileShare.dll` 与 `QSBLE.dll`；同安装目录另有 `NearbyShareCore.dll`，不能把两者混为一条通道。已公开的 [Samsung Quick Share Agent 分析](https://www.nccgroup.com/media/jjqfejnv/chainspotting-2-slides-light.pdf) 涉及 `CreateSession`/`FsaMetadata` 的 JSON 会话格式，可作为协议定位线索，不是现成可用客户端。当前没有安装额外反编译工具，也未调用官方程序转发文件。

当前适合先集成已实测的 Google/Nearby：继续使用已有相册、`quickshare.h` 任务桥、四个 `/api/v1/quickshare/*` API 和单一 ARM CLI。发送时由机身进程打开原图库文件，JPEG/RAW 使用现有单张、多选与配对格式选择；页面区分本地进度和接收端结束确认。无需再引入桌面项目的 GUI、Python 服务或另一套相册。

协议入口为 `outputs/quickshare/nx-quickshare-armv7`；`tools/remote_stock.py` 和 `tools/build_sd_bundle.py` 均已引用该入口。主模拟器已接入相册分享，遥控服务和网页已更新为多文件版；主模拟器私有网络只有回环时，菜单可用不代表能发现 LAN 手机。

## 原厂相册集成（2026-10-21，候选产物）

本阶段推进 Google Quick Share。独立原厂会话已实际通过“照片回放 → Share(Wi-Fi) → 原生 Quick Share 页面 → 返回同一照片”，并确认返回后原厂菜单仍可操作。页面带入真实所选文件 `SAM_4262.JPG`，不再要求选择 Facebook/Picasa。该会话使用真实 ARM 服务和协议程序，但网络隔离，本轮没有重新向手机传文件。三星独立通道暂缓。

构建命令（项目根目录，指定 Conda Python）：

```powershell
python -X utf8 tools/build_gallery_share.py
python -X utf8 tools/build_gallery_share.py --package-only
```

`outputs/quickshare/gallery/` 包含 `gallery-share.so`、`quickshare-panel.arm`、`nx-remote-controller.arm`、构建报告，以及独立的 `sd-bundle/nx300m-mod-sd.tar.gz` 和 `firmware/persistent-candidate/rootfs/`。后者是 rootfs 文件候选目录，不是可直接刷入的固件。打包复用当前颜色/配方产物，不重编它们，也不覆盖原有部署 staging 目录。协议 CLI 仍由 `tools/quickshare_build.py --target arm` 构建。

原生页面与真实 ARM 任务服务在新建 mount/net/PID/IPC 命名空间内通过生成文本检查：所选路径正确传给子进程，显示设备与完成状态，退出后恢复原窗口焦点。协议子进程在该检查中使用只输出 JSON 的本地 fixture，没有连接手机。证据：`probe-report.json`、`fixture-arguments.txt`、`devices.png`、`complete.png`。`tools/probe_gallery_share.py` 可复现这项限定检查。

### 功能路径

当前单张路径：原厂回放页 → 当前文件 → 分享 → Quick Share 设备列表 → PIN 与手机接受 → 发送进度 → 接收端结束确认 → 返回相册。网络须已经连接；自动联网尚未实现。缩略图多选路径为 Share(Wi-Fi) → 勾选 → Upload → 同一 Quick Share 面板。

实际菜单激活由 `CAPPGUIMenuDepth2State::onEnter` 接入：仅当用户展开 Share 项（SVS=0x1fb）、来源为单张播放、当前仍在该菜单且没有确认框时，原回调完成后排入 Ecore job。job 通过原厂 `UI_Manage_PB_Menu_Info_By_GUI({6,0,0,0}, false)` 恢复单张，再携带当前文件启动面板，避免在菜单构造中销毁对象。菜单高亮不触发；其他相机菜单保持原流程。原有单张分享与区域触控上传入口也保留各自的原厂条件及回退。

`runtime/gallery_share.c` 在原厂相册线程调用 `ASLFileManager_getCurIndex()` 和 `ASLFileManager_getFilename(index)`，立即复制 ASL 的共享字符串，再 fork/exec `quickshare-panel.sh FILE`。原生页面沿用 ASL 指定的照片集合，支持选择接收设备，以及原文件、JPEG、RAW、JPEG+RAW 格式；配对查找和上限按下节的统一规则处理。`mod/metol/quickshare_panel.c` 复用 NXUI 的 X11/Xft 旋转绘制与中文字体，使用 760×440 逻辑画布及同一组面板互斥锁。原厂相册没有被 relaunch，退出时恢复保存的 X11 焦点。

已对本地原厂 `outputs/deployment/stock/usr/bin/di-camera-app-nx300` 完成只读符号与反汇编核对：

- `UI_PB_Share_Single()`，ARM地址 `0x1e9ef8`，500字节：读取 `ASLFileManager_getCurIndex()`，再调用 `ASLFileManager_getFilename(index)`；随后构造包含 `pbshare-type`、`pbshare-data` 的 bundle，调用 `UI_Launch_WiFi_App()`。这是单张回放分享的现成路径。
- `UI_PB_Share_Thumbnail()`，`0x1ea0ec`，116字节：调用 `ASLFileManager_setSelAllDisable()`，再进入原厂缩略图选择构造函数。它是选择流程入口，不能直接当作已选文件列表。
- 多选确认已接入 `_Z25UI_Thumbnail_Upload_Filesv`，通过 selected-first/next 枚举复制文件列表，见下节的实测结果。
- 已核对单张分享的两个直接 BL 调用点：`0x1ea41c` 和 `0x1f2558`；缩略图入口调用点是 `0x1ea494` 和 `0x1f2570`。旧 caller 报告漏掉了符号范围之外的指令，不再据它判断不存在调用者。
- 钩子在 Ecore 主循环开始前，核对可执行文件身份、符号地址和单张分享函数完整 500 字节；匹配才将入口前 8 字节替换为 ARM 跳转。原始两条非 PC 相对指令保存在 RX trampoline 中。接管位置在原厂所有分享状态变更及 `UI_Manage_App_Relaunch()` 之前。缺少启动脚本或所需符号时保留/回退原厂路径。

静态证据包括 `outputs/quickshare/stock-gallery-symbols.json`、`stock-gallery-share.asm` 和 `stock-gallery-thumbnail-share.asm`。`libasl.so` 反汇编确认 `getFilename(int)` 返回共享字符串；原厂动作中具体路径形式仍需运行核实。入口支持绝对路径及相对 DCIM 路径；面板和服务均使用 realpath 校验图库边界，不按最新文件推定选择。

原厂机身多选通过 `_Z25UI_Thumbnail_Upload_Filesv` 接管真实确认；使用原厂 selected-first/next 枚举，复制路径后恢复当前照片。普通 DCIM 模式的双文件及返回后重新选择已验证，分组数据库模式仍保留原逻辑。遥控网页与原生面板共用一次 CLI 多文件传输。

### 遥控相册多文件发送（2026-10-21）

网页使用既有图库选择模式，支持多张及配对照片的JPEG、RAW或JPEG＋RAW，最多16个实际文件。单个配对照片选“JPEG＋RAW”计2个文件。所有路径由相机侧CLI直接打开，浏览器不下载再转发照片。原厂机身面板支持单张、缩略图多选与原文件/JPEG/RAW/JPEG+RAW格式选择。

`POST /api/v1/quickshare/send` 沿用form编码、endpoint/name/client_token。旧 `path=` 保持兼容；批量使用 `path_count=2&path0=100PHOTO%2FA.JPG&path1=100PHOTO%2FA.SRW`。不允许混用旧path和批量字段；缺失/多余索引、重复字段、NUL、不可读或非普通文件、同一规范路径或同inode重复均返回400，并且不会启动发送进程。路径每项最多1023个解码字节，累计文件大小须适配协议的有符号64位size字段。成功回包和status新增`file_count`、`total_size`，取消/任务归属仍共用现有机制。

ARM构建和隔离HTTP探针通过：双文件、旧单文件、16文件边界的argv顺序和统计正确；12类非法请求均返回400且未启动测试CLI。证据：`outputs/quickshare/gallery/multifile-probe/report.json`。该探针只使用生成文件及argv记录替身，不证明多附件手机收件。主窗口已实际选择`SAM_4262.JPG`＋`SAM_4262.SRW`并显示2个待发送文件，截图为`multifile-selection.png`；未点击向接收设备发送。

### 相册分享交互修正

原生面板现提供原文件、JPEG、RAW、JPEG+RAW四种格式。转换始终基于初始选择，同目录同stem匹配JPG/SRW扩展名（忽略扩展名大小写），按规范路径及inode去重。缺配对、不可读或超过16个实际文件时整批阻止；切回原文件恢复初始列表。发送中锁定格式，切换后刷新实际文件名并清除旧PIN/进度。最新 `panel-multifile-probe/report.json` 已验证四种格式的实际HTTP和CLI参数、缺配对不发送、发送锁定及焦点恢复。主模拟器也实际选择了 `SAM_4262.SRW` 和 `SAM_4262.JPG + SAM_4262.SRW`，截图为 `main-format-raw.png`、`main-format-both.png`；没有外发照片。状态轮询期间点击发送现在会排队一次，固定目标与附件，待新有效状态返回后执行；错误、离线、超时或返回会撤销。隔离ARM延迟status场景只按一次Enter，实际POST/send恰好一次且目标/格式不变，见同报告queued_cases。

2026-10-21 错误处理验证：独立原厂相册勾选测试照片后暂时移走文件，点击 Upload 实际打开“文件不可用，请重新选择”，返回按钮恢复原选择页；测试副本已恢复，证据为 `stock-errors/report.json`。ARM 面板的四种错误页退出路径均无 HTTP 请求且恢复焦点，已有单/双文件交接仍通过，见 `panel-multifile-probe/report.json`。原厂界面实际勾选超过16项的整条链路尚未实测。

原生面板在点击发送时固定设备端点和名称；停止发现期间不再跟随列表位置改变目标，目标离线则要求重选。运行中的发送以任务 `operation` 判断，首个协议事件尚未到达时也不允许再次发送。机身 Return／KP_Enter 与触屏共用发送动作，上下选择会滚动设备列表。

发现／发送请求携带可选 `client_token`，服务状态原样回显。启动回包丢失后，面板只认领关联标识匹配的任务，不因编号变化取消别的页面的分享；原有不带标识的客户端仍兼容。退出等待从真正发出自有任务取消后开始，避免启动请求晚返回时漏取消。相册钩子在接管前检查面板可执行性及所选文件的可读性、常规文件类型和真实图库边界。普通DCIM多选超过16项或文件不可用时，留在相册并打开错误提示，不调用旧无线程序；错误页面不访问网络服务。启动器本身不可用时只能保留相册并记日志。单张入口未安装时仍保留原厂路径。创建子进程的日志仅表示启动请求，不能单独证明页面已打开。

最新 ARM 构建及 `tools/probe_gallery_share.py` 退出 0。限定的隔离检查通过：六设备列表中第五项键盘选择与发送、首事件延迟时重复 OK 只发送一次、目标移除、启动回包丢失后的归属核对、退出取消及焦点恢复。实际页面截图为 `outputs/quickshare/gallery/devices.png`、`complete.png`，结果为 `probe-report.json` 和 `state-result.json`。协议事件仍由本地 fixture 产生，这些结果不覆盖原厂相册点击或手机收件。首次扩展检查退出 1 是测试驱动在 Escape 关闭窗口后继续向已销毁 XID 发送 KeyRelease（X11 `BadWindow`）；修正驱动后同范围检查通过。

### 控制与传输

```text
原厂 camera app + 现有最小入口
  ├─ 现有 service-loader → SD start.sh → 统一 ARM 服务（127.0.0.1:8781）
  └─ 原厂相册分享回调 → 携带选中照片打开 Quick Share 页面
          │ 本机发现/发送/状态/取消请求
          ▼
      现有 quickshare.h 单任务桥
          │ fork/exec + JSON 进度
          ▼
      nx-quickshare-armv7 → Wi-Fi → 手机 Quick Share
```

原生分享页面携带原厂相册给出的照片路径，通过本机 loopback 使用已存在的四个任务 API；协议进程直接读取照片并连接手机。无需外开 HTTP、电脑中转、手机辅助 App、相机上的 Python/JVM 或第二套守护服务。网页遥控与机身页面共享同一任务状态，并发发送仍由当前服务拒绝。

页面区分扫描、等待手机接受、发送、等待接收端结束和完成/失败。本地字节数达到总量仍显示等待确认；只有 Finished 且进程退出 0 显示完成。取消携带 `task_id`，服务在编号改变时返回 409；退出页面只取消自己启动的任务。CLI 已将固定总时限改为 180 秒无进展超时，仅协议状态变化或新增发送字节延长时限；大照片/RAW 的真实吞吐尚未验证。

### 网络接入是首要缺口

已只读核对原厂 1.15 TAR：存在 `usr/bin/wlan.sh`、`smart-wifi-app-nx300`、`wifi-direct-server.sh`、`wpa_supplicant`、`wpa_cli`、`hostapd`、`connmand` 和 `net-config`，名单存于 `outputs/quickshare/stock-network-inventory.json`。`wlan.sh` 明确管理 `wlan0`、`ath6kl_sdio`，分别包含 station/P2P/AP、DHCP 与清理路径；脚本中存在 `killall`，不应未经状态协调直接调用。

这些文件只证明原厂有对应组件和入口，尚未证明从 NX300M 的当前拍摄/回放状态可安全调用。`net-config`/`connman` 的 init 脚本还包含注释启动行及机型条件，不能按桌面 Linux 惯例假定服务正在运行。

**交付要求是无需预先接入同一局域网的相机直连。当前 LAN 版本仅为协议与相册集成原型，未满足这一要求。** 已验证的同 LAN 传输继续保留为开发路径；下一步的首要工作是设备发现与初始连接、Nearby 无线通道协商、原厂 Wi-Fi P2P／AP 网络生命周期及退出恢复。不能把相机启动热点或原厂 P2P 服务存在，等同于手机原生 Quick Share 已能发现并协商连接。需要识别原厂网络管理者与模式请求路径，避免另起网络管理服务抢占网卡。

相机自身 AP、Wi-Fi Direct 协商和 BLE 发现尚未实现。GPL 参考内核配置没有启用 Bluetooth；这不是对实际硬件是否具有蓝牙无线芯片的确认，也不等于已提取并验证原厂实际内核配置。必须先确认可用的发现与初始连接方式，再实现 Wi-Fi 通道切换。主模拟器只有 loopback，不能用它的菜单测试证明真实无线互通。

### 固件与 SD 的分工

rootfs 的 `PERSISTENT_HOOKS` 与 preload 模板已加入 `gallery-share.so`，共 8 个模块。SD 包已包含 `quickshare-panel.sh`、`bin/quickshare-panel.arm`、更新后的任务服务和协议程序，沿用 `/mnt/mmc/nx300m-mod/` 的字体与资源。没有 SD 启动脚本时，单张分享调用原厂 trampoline。构建与打包不会修改实体相机、执行休眠脚本或重启现有模拟器。

现有内置候选与 SD 服务链有 ARM VM 验证，实体安装、冷启动、休眠快照与实际无线驱动仍须分别核验。发送期间的临时防休眠、关机/拔卡取消、退出后的网络模式恢复必须接原厂真实状态；不持久修改全局禁休眠偏好，也不在相机主线程执行文件传输。

### 剩余整机验收

1. 原厂相册入口及返回已在独立会话通过，具体结果见下段；实体机安装、真实按键和休眠恢复仍需对应验证。
2. 核实相机已有 station 网络及 mDNS；未联网时应无设备，不能把界面 fixture 当作无线发现证据。原厂网络模式管理、防休眠、关机/拔卡处理仍未接通。
3. 从该入口把生成文件传到 Flip6，以手机收到且能打开为准；之后再经用户授权发送实际 JPEG/RAW。实体固件安装和快照激活另行授权。

独立原厂会话最终记录为 `outputs/quickshare/gallery/stock-session/stock-gallery-report.json`，结果 `PASSED_STOCK_UI_HANDOFF_RETURN`。正确回放键为 `XF86Tools`，再通过 Menu、KP_Enter 展开 Share；实际启动的 ARM panel argv 携带 `/mnt/mmc/DCIM/100PHOTO/SAM_4262.JPG`。点击返回后 panel 退出，显示同一照片，Menu 可再次打开。截图为 `quickshare-stock-open.png`、`quickshare-stock-return.png` 与 `after-share-menu.png`。没有直接调用回调或协议事件 fixture，没有外发照片。

先前 JPEG 设备缺失导致 `File error`、菜单 `Not Supported` 的结果保留为 `stock-gallery-before-software.json`。现以显式软件 JPEG 解码、真实多级缩放及 YCC→RGB 补齐相册显示；原厂 libjpeg 的 504 字节扩展和末行色度偏移已依据原指令及实际像素处理，见 [HARDWARE.md](HARDWARE.md)。联调还修复了机身 glibc 2.13 没有 `stat64` 导致的运行时装载失败，改用已存在的 `__xstat64`，实际菜单链路随后通过。

复现工具 `tools/check_stock_gallery_share.py` 使用独立输出、独立 FAT 卡及 `run_stock.py --gallery-share`。QEMU 用户态实验室缺少 ARM binfmt，私有 busybox dispatcher 与 `NX300M_ARM_RUNNER=/qemu-arm-static` 仅提供跨架构 exec；实际 SD 脚本与 ARM 面板保持原路径。未设置 runner 的实体机仍直接执行 ARM。该独立会话已停止，卡挂载已释放。相册 JPEG 软件解码已接通；最新主会话验证见本页“当前互通状态”，本地协议事件 fixture 不作为手机收件证据。

2026-10-21 完成了一次从原厂相册入口到受控接收端的真实协议传输，证据为
`outputs/quickshare/gallery/stock-protocol-transfer-qemu11/report.json`。专用新 FAT 卡
只放由程序生成的 640×480 JPEG，含独立生成的 DCF EXIF 和 160×120 JPEG 缩略图。
真实原厂相册通过 `XF86Tools → Menu → KP_Enter` 打开 Share(Wi-Fi)，由 ASL 所选
路径启动 ARM 面板；面板使用真实 ARM 任务服务和协议 CLI，经过 mDNS 找到另一
隔离 network namespace 内的真实 ARM 接收端。两侧仅以 veth 直连，无默认路由。
控制器核对双方独立派生的 PIN 相同后才批准接收；双方进入 Finished，发送任务
退出0，完成依据为 `safe_disconnect_ack`。收到的33,618字节与生成文件逐字节
相同，并完整解码为640×480 JPEG；接收端随后按控制器指令停止。

`05-receiver-confirmed.png` 实际显示“接收端已确认完成”，
`06-returned-gallery.png` 返回同一生成图，`07-stock-menu-after-return.png` 显示
原厂菜单仍可打开；这些截图已实看。独立会话已清理，主模拟器28897的进程身份
前后相同。接收端仅是一次性受控 example，未装入生产 CLI 或 SD 包；没有外发
用户照片，也没有测试手机互通。

复现使用项目 Conda，并指定独立 runner 和新的输出目录：

```powershell
python -X utf8 tools/quickshare_build.py --target arm --example controlled_receiver
python -X utf8 tools/probe_stock_quickshare_transfer.py --qemu /opt/nx300m-lab/tools/qemu-11.0.2/usr/bin/qemu-arm --output-dir outputs/quickshare/gallery/stock-protocol-transfer-next
```

这里显式使用独立提取的 Debian QEMU11.0.2；runner仅 bind 到测试相机的私有
mount namespace，同版也运行受控接收端。原先QEMU7.2对真实IPv4
`setsockopt(IP_MULTICAST_IF, len=4)` 返回errno92，导致两端mDNS跳过接口；宿主
在同一私网配置下设置该选项成功。syscall及宿主对照分别保存在
`network-discovery-strace/`、`network-native-sockets/`，新版真实ARM发现成功见
`network-discovery-qemu11/`。未修改协议库，也未把失败的setsockopt伪装为成功。
后续启动默认产物 `work/qemu/qemu-arm-static` 已更新为经过上述验证的11.0.2，
原7.2保留为 `qemu-arm-static-7.2.0`，来源及完整性记录见 `work/qemu/SOURCE.json`。
当前共享rootfs和已运行主模拟器未热更新；上述成功限于明确指定新版runner
的独立会话。早期非DCF生成图、控制脚本环境代理和旧runner发现失败分别保留在
`stock-protocol-transfer/`、`stock-protocol-transfer-dcf/`、
`stock-protocol-transfer-direct/`，不计作生产相册或协议传输成功。

## 采用的协议核心

`mod/quickshare/upstream-rquickshare` 保存 [Martichou/rquickshare](https://github.com/Martichou/rquickshare) 源码，当前取到提交 `378d8ae969941bee4bf60ad34ac9cf8bb7005eb7`。本地精简核心在 `mod/quickshare/core`，保留其 GPL-3.0 许可。发布移植产物时应一并提供对应源码、修改及许可；本项目尚未发布。

核心执行真实的 Nearby Connections V1、UKEY2 P-256 握手、HKDF、AES-256-CBC/HMAC-SHA256 帧和文件载荷，不使用 HTTP 文件下载冒充 Quick Share。设备发现使用 `_FC9F5ED42C8A._tcp.local.` mDNS 服务。

另外核对了 [Google Nearby](https://github.com/google/nearby)（Apache-2.0）与 [NearDrop](https://github.com/grishka/NearDrop)（Unlicense）。Google 仓库包含更完整的平台抽象，但其完整桌面构建不是 NX300M 现有工具链可以直接使用的相机包。NearDrop 是 Google Nearby/Quick Share 实现，和 AirDrop 无关；其 macOS 平台实现没有直接搬到相机。

## 当前网络边界

首条移植路径采用**相机原生、同一 Wi-Fi 局域网**。相机与接收端之间直接进行协议握手和 TCP 传输，电脑及 Android 应用不代理照片。

三星 GPL 源码 `linux/arch/arm/configs/drime4_nx300m_defconfig` 明确 `CONFIG_BT` 未启用，同时配置了 `CONFIG_CFG80211`、`CONFIG_ATH6KL_SDIO`。这能证明当前参考内核配置不提供 Bluetooth，不能据此承诺 BLE 自动唤醒接收端。Wi-Fi Direct、热点协商及带宽升级仍未移植；仅有 Wi-Fi 驱动源码也不等于这些路径已可用。

Android 接收端经常只在接收页面开放 mDNS。上游 rquickshare 文档说明无 BLE 时可能发现不到设备，尤其是三星手机。当前 NearDrop 文档提供打开 `https://quickshare.google/qrcode#key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA` 对应接收页面的方法，并要求公开可见；三星手机通常需要该入口。它用于让手机开放原生接收端，文件仍由相机直接传输。本次 Flip6 在用户打开接收页面时已完成实际收件验证；二维码入口本身未单独核验。

Windows 的 Google Quick Share 接收端是否会在无 BLE 条件下持续公布相同 mDNS 服务，尚未实机核验。不能将 Linux 核心之间的成功当作 Android、三星或 Windows 互通已完成。

## 原型接口

设备发现会过滤本机及无效IPv4地址，优先探测同一设备仍在广播的已有IP／端口，
其余地址稳定排序。真实TCP探活最多4路并发，每地址限500ms、每设备一轮限2秒；
取消会丢弃尚未完成的连接。多地址设备不再因为随机挑到一个不可达地址而直接被跳过。

探测区分可达、全部尝试失败、扫描预算耗尽和取消。候选很多时2秒内可能未探完；
预算耗尽且旧端点仍在广播时保留旧记录。明确移除或替换端点时，事件保留完整的
名称、IP、端口及ID，并先移除旧记录，机身面板才能正确撤下旧目标。

这项修复已通过Windows／ARM构建，以及4项限定于地址筛选、回退顺序、取消／超时、
替换／移除事件的回归（`outputs/quickshare/test-core.log`）；另15项测试按范围过滤。
最新协议程序已进入相册SD候选包。真实相册传输证据为上节记录的QEMU11独立会话；
本次发现优化尚未重新验证手机互通。

`nx-quickshare` 是无 GUI、无 BLE 依赖的原生协议原型，标准输出逐行输出 JSON，日志走标准错误：

```text
nx-quickshare discover 15
nx-quickshare send 192.168.1.20:12345 "接收设备名" /mnt/mmc/DCIM/100PHOTO/SAM_0001.JPG
nx-quickshare loopback-test /tmp/nx-quickshare-fresh-run
```

`discover` 输出 `event=discovered` 及端点的 IP、端口、名称和设备类型。`send` 显式指定已发现的端点和文件，输出 `event=transfer` 的协议状态、进度及双方应核对的 PIN。发送端仅在 loopback 上绑定内部监听器，不发布可接收文件的网络服务。

`loopback-test` 使用两个真实核心端点，传输 262267 字节的确定性生成数据。只绑定 `127.0.0.1`，不创建 mDNS 服务；双方独立派生的 PIN 一致后才允许测试接收。只有双方实际进入 Finished 且逐字节比较一致，才输出 `event=loopback_verified`。测试路径已存在接收结果时拒绝执行，避免复用旧文件冒充成功。它不是手机/电脑互通测试。

`NX_QS_DEVICE_NAME` 可设置显示名称，默认 `Samsung NX300M`。协议没有 camera 设备枚举，使用 Unknown，不伪装成 Laptop。

统一机身服务应直接 exec 此程序并读取 stdout JSON 行：

- discover：`{"event":"discovered","endpoint":{"id":...,"name":...,"ip":...,"port":...,"present":...}}`。时间到退出 0；未发现设备时可以没有输出行，不能据此写成发现成功。
- send：`{"event":"transfer","message":{"direction":"LibToFront","rtype":"Outbound","state":...,"meta":{"pin_code":...,"total_bytes":...,"ack_bytes":...,"files":[...]}}}`。收到带 `completion_basis` 的 Finished 且进程退出 0 才写协议任务成功；官方接收端落盘另行核验。
- 参数、文件、连接或协议失败退出 1；Disconnected、Rejected、Cancelled 及 180 秒超时均不能写成功。
- CLI 在 Unix 接收 SIGTERM/SIGINT，在 Windows 接收控制台取消事件。已建立密钥时请求发送协议 CANCEL 并关闭连接，未建立密钥时关闭连接；清理预算为 1.5 秒，服务保留 2 秒强制退出兜底。取消退出非零，输出本地取消与清理状态，不将其当作接收端确认。服务接受取消后，正常退出和信号退出均标为 cancelled，保留实际退出码或信号；相册及遥控页优先显示取消状态。

模拟器通过现有 QEMU prefix 运行同一 ARM 文件；实际相机直接 exec。此 CLI 不提供独立相册、照片列表或另一套任务队列。

## 本地移植修改

- 核心新增显式绑定、关闭接收广告的 direct 模式，供发送端和受控测试使用。
- 去除 `sys_metrics` 主机统计依赖，设备名称改为显式相机名称。
- 去除仅用于文件长度的 Unix 元数据接口；接收文件在检查预期 offset 后用 seek/write，便于 Windows 主机验证同一核心。
- HMAC 比较使用库的 `verify_slice`，保留认证失败处理。
- 默认关闭上游 experimental BLE 特性；没有导入 Tauri、WebView 或桌面托盘。
- 支持读取由本机已安装 protoc 从当前 `.proto` 生成的 descriptor set，便于 Windows 构建。

上游完整接收器尚未审查和开放。本阶段对外功能仅为发现及发送；测试接收器只能用于受控 loopback。

## 构建与验证状态

Windows 主机原型已从源码构建为 `outputs/quickshare/nx-quickshare.exe`。`outputs/quickshare/loopback-windows.jsonl` 记录一次实际协议验证：双方独立派生的 PIN 一致、双方进入 Finished，262267 字节载荷逐字节一致，进程退出码 0。该结果证明本地精简核心与 CLI 已跑通真实握手及加密文件传输；不是 Android、三星或 Windows 官方 Quick Share 的互通证明。

ARM 产物已从源码构建为 `outputs/quickshare/nx-quickshare-armv7`，默认采用下述真实 poll/pipe 后端。`arm-elf.txt` 确认 ELF32、ARMv7、EABI5 soft-float，无动态 INTERP。musl 1.2.5 已从官方源代码通过本机 ARM 交叉编译器构建，独立安装于 WSL `./local/developer/nx300m-quickshare-toolchain/musl-armv7`；可用 `mod/quickshare/tooling/build_musl.sh` 重建。静态路径用于避免相机 glibc 2.13 的动态符号版本障碍，仍需实际机身验证。

该最终 ARM 产物已由现有 QEMU `cortex-a9` 执行真实协议回环：262267 字节一致、双方 PIN 一致、双方 Finished、退出 0。证据为 `loopback-arm-poll.jsonl` 和对应 strace。实际 syscall 使用 poll/pipe2，没有 epoll/eventfd/timerfd 调用。

针对旧内核明确缺少的 getrandom，另外只对测试子进程加 seccomp ENOSYS 过滤器，同时禁用 epoll/eventfd/timerfd：相同 ARM 二进制仍完成上述真实协议回环，退出 0。`loopback-arm-legacy.strace.log` 可见 getrandom 返回 errno 38 后，实际打开 `/dev/random` 与 `/dev/urandom`；`loopback-arm-legacy.jsonl` 记录逐字节一致。过滤器源码为 `mod/quickshare/tooling/legacy_syscalls.c`，它只制造 syscall 不可用条件，没有伪造随机数据或传输完成。

另在 `unshare -n` 的独立网络命名空间内创建 veth，执行 2 秒 discover 并正常退出 0，验证 mDNS 的真实 poll/pipe 后端能绑定 socket 并收尾；证据 `discover-isolated.strace.log`。该命名空间没有外部网络，也没有发送照片。这不证明发现或传输了任何官方接收端。

Windows 主机环境有旧 MinGW 与新 Rust 标准库。构建工具显式使用 Rust 自带导入库及对应完整 `.rmeta`，解决了本机 `GetHostNameW` 缺失和 metadata stub 编译故障；没有替换协议认证或随机数实现。没有使用当前缺少自动发现链接环境的 MSVC 构建路径。

安装好的工具具备后，使用项目 Conda 环境构建：

```powershell
python -X utf8 tools/quickshare_build.py --target windows
python -X utf8 tools/quickshare_build.py --target arm
```

脚本不会自动安装依赖。它需要 Windows Rust GNU 工具链、对应 `armv7-unknown-linux-musleabi` 标准库、WSL Debian 的 protoc，以及上述 ARM musl 编译器。日志位于 `outputs/quickshare/build-<target>.log`。ARM 链接经 `quickshare_link.py` 调用 WSL，不访问相机或原厂进程。

从已核验原厂 1.15 包的 rootfs 前区域实际提取出两份 gzip kernel（包偏移 22096 和 5286944），版本均为 Linux 3.5.0，编译日期分别为 2013-12-06 和 2014-12-08。两份均未找到内嵌 IKCONFIG，详见 `outputs/quickshare/stock-kernel-analysis.json`，因此无法直接从实际镜像证明 epoll 配置。GPL 的 NX300M defconfig 同时关闭 EPOLL、EVENTFD 和 TIMERFD；仅核对 syscall 年代不足以支持兼容结论。

ARM 默认构建已改选依赖中真实的 **poll + pipe** 后端：Mio 的 `mio_unsupported_force_poll_poll` / `mio_unsupported_force_waker_pipe`，以及 polling 的 `polling_test_poll_backend`。这些配置会参与 Cargo 依赖构建指纹，避免复用先前 epoll 产物；不是拦截 syscall 或虚构就绪事件。它们属于所锁定上游版本的内部选择项，升级对应依赖时需要重新核对。

CLI 的 Tokio runtime 显式使用 `current_thread`，不会按 WSL 宿主 CPU 数量创建工作线程池。mDNS 库自己的守护线程仍存在；实际机身资源与性能没有测量。

当前锁定依赖的定点源码检查结果：

- `getrandom 0.2.15` 与 `0.3.1` 在 ARM/musl 默认配置上均保留 `/dev/urandom` 回退；前者检测 syscall 的 ENOSYS，后者检测 `getrandom` 符号缺失或 ENOSYS/EPERM。回退前先 poll `/dev/random` 等待熵准备完成，没有固定种子或伪随机替代。运行环境必须提供真实可读的 `/dev/random` 和 `/dev/urandom`。
- `rand 0.9.0` 经 `rand_core 0.9.3` 使用 getrandom 0.3.1；P-256 的 `rand_core 0.6.4` 使用 getrandom 0.2.15。`uuid 1.15.1` 没有启用随机生成特性。
- Tokio 1.43.0 的 `mio 1.0.3` 默认 Linux 后端要求 epoll；ARM 构建改用其 poll 等待和 pipe 唤醒实现。accept4 等基本网络调用早于 Linux 3.5。
- mDNS 的 `polling 2.8.0` 默认 Linux 后端要求 epoll/eventfd；ARM 构建改用其 poll 与通知 pipe，不依赖 timerfd。

这些检查限定于已锁定的随机数及等待/网络依赖。WSL 使用宿主内核，即使 ARM/QEMU 回环成功，也不能当成 Linux 3.5、相机熵设备或实际无线驱动已经验证。

尚未连接或修改实体相机，也未向外部接收端发送照片。主模拟器已加载相册分享集成；照片原件未修改。

## 当前候选包核对（2026-10-22）

使用 `tools/build_gallery_share.py --package-only` 重打 SD 包和持久 rootfs 候选，退出码0，未安装到实体相机。SD 包内 `nxui.arm`、`quickshare-panel.arm`、`nx-remote-controller.arm`，以及 rootfs 候选中的 `color-style.so` 均与当前构建产物逐字节一致。颜色模块由 rootfs 候选提供，不在 SD 包内。

同一主模拟会话中，Quick Share 设备发现页面经 Escape 关闭，随后 Menu 打开原厂回放菜单，实际显示 Share(Wi-Fi)、Delete、Protect、Rotate 和 Start Slide Show。相机进程保持运行，配方接口返回200，颜色桥保持就绪。此次检查未重复手机传输；记录见 `outputs/quickshare/gallery/package-current-check.json`，菜单截图见 `outputs/quickshare/gallery/current-gallery-menu.png`。

