# Samsung NX500 相机 Wi-Fi / USB 通信协议技术调研报告

面向目标：第三方 Android App —— 连接相机、列出照片、下载照片、远程实时取景（live view）+ 快门控制。

---

## 0. 调研条件、可信度标记与重要前提

**环境限制（影响本报告的可验证性）**

- 本会话中 `github.com` / `raw.githubusercontent.com` 的 DNS 解析到非公网 IP，**无法直连**；`web.archive.org`、`r.jina.ai`、`dpreview.com`（Cloudflare 403）、`lite.duckduckgo.com`、`mojeek.com` 均不可用。
- 本报告中的 GitHub 原文是通过以下镜像/代理取得的**真实原文**（引用链接给原始 GitHub URL）：
  - `https://ghproxy.net/https://raw.githubusercontent.com/...`（raw 文件）
  - `https://dejavu.org/cgi-bin/get.cgi?url=<encoded>&ver=93`（HTML 页面）
  - `https://ungh.cc/repos/<owner>/<repo>/files/<branch>`（GitHub 文件树 JSON）
- **本地 shell 不可用**（沙箱初始化失败 `SetNamedSecurityInfoW failed`），因此**没有任何真机抓包 / nmap / 端口扫描**。所有结论均为公开文档、源码、博客与论坛原文，**未经真机复现**。

**标记约定**

| 标记 | 含义 |
|---|---|
| ✅ | 已核实：来自一手源码 / 官方 wiki / 项目文档原文 |
| ⚠️ | 推断：有间接证据，但无直接一手确认 |
| ❓ | 未证实：本会话未找到任何可靠来源，明确说明"查不到" |

---

## 1. Samsung NX 相机的 Wi-Fi 模式、SSID、AP IP 与端口

### 1.1 NX500 支持哪些 Wi-Fi 模式 ✅

来源：ge0rg `samsung-nx-hacks` wiki 的 **WiFi Feature Matrix**（[WiFi-Feature-Matrix.md](https://github.com/ge0rg/samsung-nx-hacks/wiki/WiFi-Feature-Matrix)，原文经 `raw.githubusercontent.com/wiki/ge0rg/samsung-nx-hacks/WiFi-Feature-Matrix.md` 取得）。

| 机型 | MobileLink | Remote Viewfinder | PC Auto-Backup | E-Mail | Social Networks | AllShare Play | Quick Transfer / AutoShare | Group Share | Samsung Link |
|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| NX30 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| NX300 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| NX300M | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| NX3000 | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| **NX500** | **✅** | **✅** | **✅** | **✅** | **❌** | **❌** | **✅** | **❌** | **❌** |
| NX1000 / NX1100 | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ |
| NX2000 | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ✅ |
| NX mini | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

wiki 对该表的关键说明（原文要点）：

- **"MobileLink" 和 "Group Share" 会创建 Wi-Fi AP，并且需要 "Samsung SMART CAMERA App" 才能访问图像。**
- **"Remote Viewfinder" 会创建 Wi-Fi AP，并且需要 "Samsung SMART CAMERA App" 才能拍摄 / live view。**
- **"Samsung Link" 是加入一个已有 Wi-Fi 网络并暴露 DLNA 服务。**
- `Specials`：**NX1 和 NX30 有 Remote Studio**；**NX500 有 Pro Suggest Market**，以及从回放模式分享：`Smartphone`、`PC (Auto-Backup)`、`TV`、`DMS (DLNA)`；NX mini 有 Baby Monitor；NX3000 有 Home Monitor+。

**注意：NX500 没有 Social Networks / AllShare Play / Group Share / Samsung Link。** 只有 E-Mail 是"云"类上传（Samsung 的 SNS 上传 API 已于 2021 年停服）。

其他相关分类（[README.mdwn](https://github.com/ge0rg/samsung-nx-hacks/blob/master/README.mdwn) 原文）：

- NX1 / NX500 = **DRIMeV**，跑完整 Tizen Linux（✅ 可 root、有 shell、X11、网络栈）。
- NX300 / NX2000 / NX300M / NX30 = DRIMeIV（Tizen 1.0）。NX3000 / NX3300 / NX mini = M7MU（**非 Linux**）。NX1000 / NX1100 = DRIMeIII（**VxWorks，非 Linux**）。NX20/NX200/NX210 也是 VxWorks。
- NX500 的 shell 主机名是 `drime5`（✅ 见 [Control-camera-from-command-line.md](https://github.com/ottokiksmaler/nx500/blob/master/Control-camera-from-command-line.md) 中的 `[root@drime5 ~]#` 提示符）。

### 1.2 SSID 命名规则 ✅

来源：[Remote Viewfinder wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder)，原文：

> `AP_SSC_[camera model]_0(possibly some other digit)-[first 3 digits of camera mac address]`
> for example `AP_SSC_NX3000_0-7E:8A:C1`

另一处一手记录（[Hacking the Samsung NX300 'Smart' Camera](https://op-co.de/blog/posts/hacking_the_nx300/)，NDEF 内容原文）：

```
Type: MIME: application/com.samsungimaging.connectionmanager
Payload: AP_SSC_NX300_0-XX:XX:XX

Type: EXTERNAL: urn:nfc:ext:android.com:pkg
Payload: com.samsungimaging.connectionmanager
```

即 **SSID = `AP_SSC_<机型>_<数字>-<MAC 前三段>`**，NX500 预期为 `AP_SSC_NX500_0-XX:XX:XX` ⚠️（NX500 字面量未在一手来源中出现，但命名规则对全部 NX 机型一致）。

论坛评论还记录：某 NX1000 升级固件后 SSID 变为 `AP_SSC_NX1000`（无后缀）。

### 1.3 相机作为 AP 时的 IP ✅（对 NX300/NX3000/NX1000 已核实；NX500 未核实 ❓）

| 相机 IP | 出现场景 | 来源 |
|---|---|---|
| **192.168.102.1** | NX300 Remote Viewfinder（`GetInfomation` 响应里的 `StreamUrl` 直接写死该地址） | [op-co.de 博客原文](https://op-co.de/blog/posts/hacking_the_nx300/) |
| **192.168.107.1** | NX3000 / 通用 RVF 文档（`HOST: http://192.168.107.1:7676`、`192.168.107.1:7679`） | [Remote Viewfinder wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder) |
| **192.168.16.x** | NX1000 旧固件（1.10） | 论坛评论，见 op-co.de 评论区 |
| 客户端侧（手机）示例 | `HOST-Address: 192.168.107.11`、`HOST-port: 7788` | [Remote Viewfinder wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder) |

**明确结论：**

- ❓ **我无法证实 NX500 在 AP 模式下的具体 IP 是 `192.168.1.1` 或 `192.168.1.100`。** 本会话穷尽搜索（含 NX500 手册镜像、Samsung 下载中心 PDF 搜索结果）**没有找到任何一手来源给出 NX500 的 AP IP**。
- ⚠️ 强烈建议：**不要硬编码 IP**。已在 NX300 系列上观察到 `192.168.102.1`、`192.168.107.1`、`192.168.16.x` 三套网段，说明 Samsung 各代/各固件会换网段。正确做法是：
  1. 手机加入 `AP_SSC_NX500_*` 后，**读取自身 DHCP 租约的网关地址 / `getprop dhcp.<iface>.gateway`**，用网关作为相机 IP；
  2. 或对 `/smp_2_`（7676 端口）做广播/网段扫描探测。

### 1.4 端口清单 ✅

来源：NX300 上的真实 `nmap -sS -O` 输出（[op-co.de](https://op-co.de/blog/posts/hacking_the_nx300/)），以及各项目源码。

| 端口 | 协议 | 用途 | 出现模式 | 来源 |
|---|---|---|---|---|
| **6000/tcp** | TCP | **X11 服务器（Enlightenment，完全无认证）** | E-Mail 模式即开放 | op-co.de nmap 原文 |
| **7676/tcp** | TCP/HTTP | **DLNA / UPnP MediaServer（DMS）HTTP 端点**，SOAP 控制全在这里 | AllShare Play、**MobileLink**、Remote Viewfinder | op-co.de nmap 原文 + RVF wiki |
| **7679/tcp** | TCP/HTTP | **实时视频流** `/livestream.avi`、`/qvga_livestream.avi` | **Remote Viewfinder** | op-co.de nmap 原文 + RVF wiki |
| **7788/tcp** | TCP/HTTP | **UPnP "mode/control" 配对/发现**（`GET /mode/control`） | Remote Viewfinder | [RVF wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder) |
| **5681/udp** | UDP 广播 | **`NX_REMOTE|...` 相机发现广播**（PC Auto-Backup 与 nx-remote-controller-mod 都用它） | Wi-Fi 联网时 | [PC-Auto-Backup wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/PC-Auto-Backup) + mewlips 源码 `network.h` |
| 80/tcp | TCP/HTTP | **仅存在于改装固件/Mod**（见第 3 节），**原厂固件无** | 仅 mod | mewlips `api_server.c` |
| 23/tcp | TCP | telnetd，**仅存在于 Mod**（NX300 原厂自带 busybox；NX500 原厂无 telnetd） | 仅 mod | MultiNX `autoexec.sh` |
| 21/tcp | TCP | FTP（busybox ftpd），**仅 Mod** | 仅 mod | MultiNX `autoexec.sh` |
| 52235/tcp | TCP | 这个端口在 **PC 侧**（PC AutoBackup 作为 UPnP 服务器），不是相机 | — | PC-Auto-Backup wiki |
| 15740/tcp | TCP | **PTP/IP 标准端口 —— 未在 NX 系列 Wi-Fi 上发现任何证据** ❓ | — | 见第 2.5 节 |

⚠️ **重要陷阱（原文明确警告）**：RVF wiki 说 *"On subsequent sessions a port may vary, always pay attention to previous responses"* —— **7676 / 7679 / 7788 并非永久固定，7529 类端口可能变化**，必须从 `GetInfomation` 的 `StreamUrl` 响应中动态读取流地址，而不是硬编码 7679。

**UDP 发现报文格式 ✅**（两处独立来源一致）：

来自 [PC-Auto-Backup wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/PC-Auto-Backup)，相机广播到 `255.255.255.255:5681`：

```
NX_REMOTE|0.9|NX300|1.45|a0:21:95:**:**:**|\x00\x00...（补 NUL 到 64 字节）
```

字段顺序：`NX_REMOTE` | 协议版本 | 机型 | 固件版本 | 相机 MAC | 补齐到 **64 字节**。

来自 mewlips `nx-remote-controller-daemon/network.c` + `network.h`（✅ 源码原文）：

```c
enum { PORT_UDP_BROADCAST = 5681, };
#define DISCOVERY_PACKET_SIZE 64
...
// HEADER|VERSION|MODEL|FW_VERSION|MAC_ADDRESS|
snprintf(msg, sizeof(msg), "NX_REMOTE|%s|%s|%s|%s|",
         VERSION, get_nx_model_name(), get_nx_model_version(), get_mac_address());
sendto(sock, msg, sizeof(msg), 0, ... INADDR_BROADCAST ...);
```

并且该 daemon 每秒广播一次，同时用 `SO_REUSEADDR` 绑定 5681 接收其他相机的广播，把结果暴露成 `/api/v1/camera/status` 的 `cameras` JSON 数组 —— **这可以直接照搬到 Android 上做"自动发现同一网络里的多台相机"**。

另有一个与 NX500 有关的网卡名差异（✅ 源码原文，`network.c`）：

```c
if (is_new_nx_model()) {
    strncpy(ifr.ifr_name, "mlan0", IFNAMSIZ-1);   // NX1 / NX500 (DRIMeV)
} else {
    strncpy(ifr.ifr_name, "wlan0", IFNAMSIZ-1);   // 老机型
}
```

---

## 2. 官方 App 与它的 Wi-Fi 协议

### 2.1 官方 App 的包名 ✅

| 包名 | App 名称 | 适用机型 |
|---|---|---|
| `com.samsungimaging.connectionmanager` | **Samsung SMART CAMERA App** | NX300 / NX2000 一代（NDEF 里写死的包名） |
| `com.samsungimaging.samsungcameramanager` | **Samsung Camera Manager App** | NX1 / NX500 一代（[Google Play 页面](https://play.google.com/store/apps/details?id=com.samsungimaging.samsungcameramanager)） |

NX300 的 NFC 标签内容（✅ 原文，[op-co.de](https://op-co.de/blog/posts/hacking_the_nx300/)）把 App 包名 + AP SSID 一起写进 NDEF：点击 NFC 即拉起 App 并发起连接。**该标签是可写的**（安全提示，与开发无关但属实）。

### 2.2 协议本体：**不是 PTP/IP，是 UPnP + SOAP** ✅

这是本次调研最重要、也是最能直接落地的结论。

NX 相机的 Remote Viewfinder / MobileLink **不使用 PTP/IP（ISO 15740 over IP，15740 端口）**。它用的是：

1. **DLNA / UPnP Device Architecture**：相机作为 `urn:schemas-upnp-org:device:MediaServer:1`（DMS，`X_DLNADOC = DMS-1.50`），friendly name 形如 `[Camera]NX3000`；
2. **SOAP 1.1 over HTTP/1.0**，服务类型 `urn:schemas-upnp-org:service:ContentDirectory:1`；
3. 视频流是**裸 HTTP GET 一个 `livestream.avi`**（Motion-JPEG in RIFF/AVI 容器）；
4. Samsung 私有的**扩展 SOAP Action**（`GetInfomation`、`MULTIAF`、`Shot`、`touchAF`、`SetResolution`……）挂在同一个 ContentDirectory:1 上。

### 2.3 完整的 URL 端点表 ✅

| 路径 | 方法 | 作用 | 服务 |
|---|---|---|---|
| `http://<cam>:7676/smp_2_` | GET | **设备描述 XML**（device description / `LOCATION` 指向的文档） | UPnP DMS |
| `/smp_3_` | GET | **SCPD：ContentDirectory:1 的完整 Action 表**（27KB XML） | ContentDirectory:1 |
| `/smp_4_` | POST | **控制端点（所有 SOAP Action 都 POST 到这里）** | ContentDirectory:1 |
| `/smp_5_` | SUBSCRIBE | 事件订阅（eventSubURL） | ContentDirectory:1 |
| `/smp_6_` | GET | SCPD：ConnectionManager:1 | ConnectionManager:1 |
| `/smp_7_` | POST | 控制端点（ConnectionManager） | ConnectionManager:1 |
| `/smp_8_` | SUBSCRIBE | 事件订阅 | ConnectionManager:1 |
| `http://<cam>:7679/livestream.avi` | GET | **高质量实时取景流** | — |
| `http://<cam>:7679/qvga_livestream.avi` | GET | **QVGA(320×240) 低质量实时取景流** | — |
| `http://<cam>:7788/mode/control` | GET | **配对 / 模式控制（UPnP "mode" 发现）** | — |

`/smp_2_` 的真实响应（✅ 一手原文，`xml/Remote-ViewFinder/smp_2_.xml`，抓自 NX3000）：

```xml
<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:sec="http://www.sec.co.kr/dlna" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>[Camera]NX3000</friendlyName>
    <manufacturer>Samsung Electronics</manufacturer>
    <manufacturerURL>http://www.samsung.com</manufacturerURL>
    <modelDescription>Samsung Camera DMS</modelDescription>
    <modelName>NXF1</modelName>
    <modelNumber>1.0</modelNumber>
    <modelURL>http://www.samsung.com</modelURL>
    <serialNumber>20081113 Folderview</serialNumber>
    <sec:X_ProductCap>smi,getMediaInfo.sec,getCaptionInfo.sec</sec:X_ProductCap>
    <UDN>uuid:16a65700-0034-1000-8873-a021957e8ac1</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <controlURL>/smp_4_</controlURL>
        <eventSubURL>/smp_5_</eventSubURL>
        <SCPDURL>/smp_3_</SCPDURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <controlURL>/smp_7_</controlURL>
        <eventSubURL>/smp_8_</eventSubURL>
        <SCPDURL>/smp_6_</SCPDURL>
      </service>
    </serviceList>
    <sec:deviceID>SHCOHHCD7X574</sec:deviceID>
  </device>
</root>
```

（源文件：[ge0rg/samsung-nx-hacks `xml/Remote-ViewFinder/smp_2_.xml`](https://github.com/ge0rg/samsung-nx-hacks/blob/master/xml/Remote-ViewFinder/smp_2_.xml)）

### 2.4 握手 / 配对报文（原始 socket 文本）✅

**第 1 步：配对 / 发现（端口 7788）** —— 原文（[RVF wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder)）：

```
GET /mode/control HTTP/1.0

HOST: http://192.168.107.1:7788
User-Agent: SEC_MODE_[your mac]
Access-Method: manual
NTS: alive
Content-Length: 0
HOST-Mac: [your mac]
HOST-Address: 192.168.107.11
HOST-port: 7788
HOST-PNumber: none
```

> 若相机不认识你的平台，会在屏幕上弹出**配对确认对话框**，需要人在相机上按"确定"。配对后相机把你记为已知平台并开始提供 viewfinder 服务。

**第 2 步：请求远程取景（SOAP，端口 7676）** —— 原文：

```
POST /smp_4_ HTTP/1.0

Content-Type: text/xml
HOST: http://192.168.107.1:7676
SOAPACTION: "urn:schemas-upnp-org:service:ContentDirectory:1#GetInfomation"

<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
    <u:GetInfomation xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
        <GPSINFO>UNKNOWN</GPSINFO>
    </u:GetInfomation>
</s:Body>
</s:Envelope>
```

**NX300 上的等价抓包（来自 op-co.de 评论区 Serg 的原文，含 `SEC_RVF_` User-Agent）**：

```
GET /smp_2_ HTTP/1.0
User-Agent: SEC_RVF_{MAC ADRES}_
Access-Method: manual
HOST: 192.168.107.1
Connection: close
```

```
GET /smp_3_ HTTP/1.0
HOST: 192.168.107.1
Connection: close
```

```
POST /smp_4_ HTTP/1.0
Content-Type: text/xml; charset="utf-8"
HOST: 192.168.107.1
Content-Length: 303
SOAPACTION: "urn:schemas-upnp-org:service:ContentDirectory:1#GetInfomation"
Connection: close
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
   <s:Body>
      <u:GetInfomation xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1"></u:GetInfomation>
   </s:Body>
</s:Envelope>
```

**NX1000 上更精简的可用版本（Yarkhan，op-co.de 评论区原文）**：
> "For pairing, just send a POST or GET to `/smp_2_` or `/smp_3_` with the User-Agent header. The other headers seems to be unnecessary."
> `User-Agent: SEC_RVF_00:00:00:00:00:00`
> "The only necessary header [for /smp_4_] is the SOAPACTION header."
> `SOAPACTION: urn:schemas-upnp-org:service:ContentDirectory:1#GetInfomation`
> 之后 "will start streaming on `http://192.168.102.1:7679/qvga_livestream.avi`"，"You don't even need to send any xml for any of these actions."

⚠️ 注意：**User-Agent 里的 `SEC_RVF_<MAC>` 是配对身份标识**。评论区有人提到，用 "cloned MAC of before paired device" 就可让相机进入等待 RVF 状态，从而绕过重新配对。

### 2.5 关于 `drime` / `DSC` HTTP 端点 / `nx_control` / `vapix` / PTP/IP 15740 ❓

明确结论 —— **本会话未找到任何证据表明 NX 相机通过 Wi-Fi 暴露以下任何东西**：

- ❌ **PTP/IP（ISO 15740 over IP，TCP 15740）**：没有任何来源提到 NX 系列在 Wi-Fi 上开 15740。PTP 在 NX 上**只走 USB**（见第 5 节）。
- ❌ `drime` 作为 HTTP 端点：**"DRIMe" 是 Samsung 的 ISP/SoC 品牌名（DRIMe II / III / IV / V / Vs），不是 API 名。** NX500 的 shell hostname 是 `drime5`，仅此而已。相机的私有命令通道叫 **`dfms` / `dfmsd` / `dfmstool`**（见 4.4 节），它是**本机进程间通道**，不是网络端点。
- ❌ `DSC` HTTP 端点：未发现。
- ❌ `nx_control` REST API：未发现（**注意别和下面 mewlips Mod 的 `/api/v1/` 混淆，那是第三方 Mod 加的功能，不是原厂**）。
- ❌ "vapix 类 REST API"：未发现（那是 Axis 摄像机的 API）。

唯一在 NX 上真实存在的网络 API = **第 2.3/2.4 节的 UPnP/`smp_*_` + SOAP**，加上第 1.4 节的 5681/UDP 发现广播。

另有一组**相机主动外连**的硬编码 Samsung 云主机名（✅ 原文，[samsung-nx-emailservice README](https://github.com/ge0rg/samsung-nx-emailservice)）：

```
gld.samsungosp.com  www.samsungimaging.com  www.ospserver.net  snsgw.samsungmobile.com
```

上传路径示例（FOSDEM 2025 演讲原文，[slides](https://op-co.de/talks/samsung-mastodon-bridge/fosdem2025.html)）：

```http
POST http://www.ospserver.net/social/columbus/email?DUID=123456789033 HTTP/1.0
Authorization:OAuth *snip*
x-osp-version:v1
User-Agent: sdeClient
Content-Type: multipart/form-data; boundary=---------------------------7d93b9550d4a
```

```http
POST http://snsgw.samsungmobile.com/facebook/auth HTTP/1.1
```

**这些是相机→云的方向，对"手机连相机拉照片"毫无用处**，且服务已停。列出仅为排除法。

### 2.6 官方 App 的实现细节 ⚠️

一位曾在 op-co.de 评论区留言的逆向者（NikoNX）称反编译过官方 App：*"they use CyberGarage open-source upnp/dlna connector and bunch of frameworks on top of it, ffmpeg for example"*。**⚠️ 这是论坛评论，非一手代码，仅作参考**——但它与"协议 = UPnP/DLNA + ffmpeg 解码 AVI 流"的观察完全自洽。

---

## 3. 相机内置 HTTP 服务器（80 端口）

### 3.1 直接结论

> **原厂 NX500 固件在 Wi-Fi 模式下不提供 80 端口的通用 HTTP 服务器，也没有 Web 相册 UI、没有 JPEG 直链、没有 JSON/XML 列表接口。** ✅（有强证据）

**证据 1（最直接）**：MultiNX 项目 README 原文（[ajavamind/Multi-NX-Camera-Control](https://github.com/ajavamind/Multi-NX-Camera-Control)）：

> "With the NX500 camera, **Tizen lacks code for Telnet, HTTP and FTP servers execution, so these services were copied from the NX300 and placed on the SD card to start on power up.**"

即：**NX500 原厂 Tizen 里根本没有 HTTP 服务器可执行文件**，必须从 NX300 拷过来放 SD 卡上启动。相应的一手脚本 `sdcard-NX500/autoexec.sh`（✅ 原文）：

```sh
#!/bin/sh
# Samsung NX500 startup script
# wait for WiFi connection
sleep 30
mkdir -p /dev/pts
mount -t devpts none /dev/pts
# Telnet server
/mnt/mmc/telnetd &
# HTTP server
/mnt/mmc/busybox httpd -h /mnt/mmc
# FTP server
/mnt/mmc/busybox tcpsvd -u root -vE 0.0.0.0 21 /mnt/mmc/busybox ftpd -w /mnt/mmc &
killall dfmsd
```

→ 这里的 80 端口 HTTP 服务是 **busybox httpd**，根目录是 SD 卡的 `/mnt/mmc`，**这是用户自己放上去的，不是三星固件**。

**证据 2**：NX300 上的真实 nmap（✅ [op-co.de](https://op-co.de/blog/posts/hacking_the_nx300/)）显示 E-Mail 模式只开 `6000/tcp`，MobileLink/AllShare 加 `7676/tcp`，RVF 加 `7679/tcp` —— **从未出现 80/tcp**。

**证据 3**：NX300 的 Tizen rootfs 里确实有 busybox（`BusyBox v1.20.2 (2013-06-04 15:18:37 KST) multi-call binary`），所以 NX300 可以起 httpd；但 NX500 的 Tizen 缺这些 applet（见证据 1）。

### 3.2 那"在浏览器打开 `http://<相机IP>`"是什么？

出自 [nx-remote-controller-mod 文档](https://mewlips.github.io/nx-remote-controller-mod/)：

> "Find IP Address of your camera from the app menu. Open `http://<IP address of camera>` in web browser."

**这是该第三方 Mod 自己起的 Mongoose HTTP 服务器**，不是三星固件。✅ 源码 `nx-remote-controller-daemon/api_server.c` 原文：

```c
static const char *s_http_port = "80";
...
s_http_server_opts.document_root = web_root;   // "<app_path>/web_root"
s_http_server_opts.enable_directory_listing = "yes";
nc = mg_bind(&mgr, s_http_port, ev_handler);
...
print_log("Starting threaded server on port %s", s_http_port);
```

### 3.3 那么"列出照片 / 下载照片"在**原厂固件**下怎么做？✅⚠️

**唯一已核实的路径 = 7676 端口上的 DLNA ContentDirectory `Browse` Action。** ✅（Action 存在于 SCPD 里）

`/smp_3_` 中 `Browse` 的签名（✅ 原始 XML）：

```xml
<action>
  <name>Browse</name>
  <argumentList>
    <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
    <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
    <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
    <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
    <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
    <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
    <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
    <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
    <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
    <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
  </argumentList>
</action>
```

`BrowseFlag` 允许值：`BrowseMetadata` / `BrowseDirectChildren`（✅ 来自同一 XML 的 `allowedValueList`）。

⚠️ **未在本会话取得**：`Browse` 实际返回的 DIDL-Lite XML 中 `<res>` 元素里的 JPEG 下载 URL 长什么样（Samsung DMS 的相册 URL 形如 `http://<cam>:7676/...`）。**这是实现"列照片+下载"的最后一块拼图，需要真机抓一次 `Browse` 响应。** 但协议骨架（POST `/smp_4_`，SOAPACTION `...#Browse`）已完全确定。

**另一条已核实但方向相反的路径**：`PC Auto-Backup` 模式下是**相机主动把照片推给 PC**（PC 作为 UPnP MediaServer，相机作为客户端）。PC 侧端点（✅ [PC-Auto-Backup wiki](https://github.com/ge0rg/samsung-nx-hacks/wiki/PC-Auto-Backup)）：

```
1. /DMS/SamsungDmsDesc.xml
2. /DMS/ContentDirectory1.xml
3. /DMS/ConnectionManager1.xml
```

SSDP 通告原文：

```http
HTTP/1.1 200 OK
CACHE-CONTROL: max-age = 1800
EXT:
LOCATION: http://192.168.0.108:52235/DMS/SamsungDmsDesc.xml
SERVER: MS-Windows/XP UPnP/1.0 PROTOTYPE/1.0
ST: urn:schemas-upnp-org:device:MediaServer:1
USN: uuid:4a682b0b-0361-dbae-6155-eb5163ae01ca::urn:schemas-upnp-org:device:MediaServer:1
Content-Length: 0
```

相机查询 PC 时带的"认证"头是 `User-Agent: Samsung User-Agent DLNADOC/1.50`。

Python 重实现：[warwickh/pc-autobackup](https://github.com/warwickh/pc-autobackup)。

**MobileLink 的照片列表协议 ❓**：ge0rg wiki 的 Home 索引里**没有 MobileLink 页**（只有 Email service / PC Auto Backup / Remote Viewfinder / Social Media / WiFi Feature Matrix），本会话也**没有找到任何 MobileLink 的协议文档**。MobileLink 走 7676 DLNA 是 ⚠️ 推断（因为 nmap 显示 MobileLink 模式打开 7676）。

---

## 4. Remote Viewfinder 细节

### 4.1 `ge0rg/samsung-nx-hacks` wiki 页面 `Remote-Viewfinder` 全文要点 ✅

原始 URL：<https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder>（最近编辑 2023-02-27，作者 Georg Lukas）。以下为**原文摘录 + 中文说明**：

**标题**：Connecting as remote viewfinder to NX camera —— *"Simple yet powerful way to utilize remote viewfinder mode on NX camera without having special app."*

> **⚠️ 重要免责声明（原文）**："This information is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY. The authors take no responsibility for deleting your baby photographs, bricking your expensive camera or making demons fly out of your nose."
>
> **测试范围（原文）**："The information below is only tested on **Samsung NX 3000** smart camera and possibly works somewhere else in the NX ecosystem, try at your own risk."

**协议定性（原文）**：*"Communication with NX camera is based on **UPnP** tech and **SOAP** for requests."*

**连接 Wi-Fi（原文）**：
> 首先要连接相机在切换 Remote Viewfinder 时开出的专用 AP。若不是通过 NFC 开启，屏幕上会显示格式如下的 SSID：
> `AP_SSC_[camera model]_0(possibly some other digit)-[first 3 digits of camera mac address]`
> 例如 `AP_SSC_NX3000_0-7E:8A:C1`
> 部分平台（Android、Linux）可编程连接。**Fun fact：用任何支持 NFC 的设备碰一下相机的 NFC，就会让相机进入指定模式，而不检查"App"是否真的启动了。** 因此任何 NFC 设备都能更快地连上相机。

**命令封装方式（原文）**：
> 连上 AP 后，相机等待你的命令。推送命令最佳方式是 HTTP 请求或裸 socket。下面的命令以**裸请求**形式给出（适合 socket）；更高级的 HTTP 客户端应剥掉第一行 `[HTTP method name] [url part] HTTP/1.0` 和 `HOST` 头，把完整 URL（HOST+url part）与 method 交给 HTTP 客户端对象，其余头按语言惯例传递。**头之后的空行表示后面是请求体。**

**应答/服务清单**：页面链接了两个仓库文件作为附件：
- `[Camera info]` → `smp_2_.xml`（设备描述）
- `[SOAP requests to NX camera - full table]` → `smp_3_.xml`（Action 全表）

**流地址（原文）**：
> 相机在以下 URL 上打开高清视频流：`http://192.168.107.1:7679/livestream.avi`
> 及其 320×240 (QVGA) 版本：`http://192.168.107.1:7679/qvga_livestream.avi`
> 从那一刻起，你就可以在 viewfinder 状态下继续发送 SOAP 请求控制相机。

### 4.2 完整的 SOAP Action 表 ✅（这是最核心的成果）

来源：`smp_3_.xml` 原文（27,591 字节），<https://github.com/ge0rg/samsung-nx-hacks/blob/master/xml/Remote-ViewFinder/smp_3_.xml>。
服务类型统一为 `urn:schemas-upnp-org:service:ContentDirectory:1`，**全部 POST 到 `/smp_4_`**。

| Action | 入参 | 出参 | 用途 |
|---|---|---|---|
| **`Browse`** | ObjectID, BrowseFlag, Filter, StartingIndex, RequestedCount, SortCriteria | Result, NumberReturned, TotalMatches, UpdateID | **DLNA 浏览（列照片）** |
| `GetSearchCapabilities` | — | SearchCaps | DLNA |
| `GetSortCapabilities` | — | SortCaps | DLNA |
| `ImportResource` | SourceURI, DestinationURI | TransferID | 让相机拉取资源 |
| `X_GetFeatureList` | — | FeatureList | DLNA 扩展 |
| `GetDeviceConfiguration` | — | TranferOption | DLNA |
| `GetSystemUpdateID` | — | Id | DLNA |
| **`GetIP`** | — | GETIPRESULT | 取相机 IP |
| **`GetInfomation`** | GPSINFO | GETINFORMATIONRESULT, **StreamUrl** | **启动 RVF 的关键**：返回能力集 + 流地址 |
| `SetResolution` | RESOLUTION | AVAILSHOTS, ResolutionRatio, RatioChangeStatus | 改照片分辨率 |
| **`MULTIAF`** | — | AFSTATUS | 多点自动对焦 |
| `setTouchAFOption` | TOUCH_AF_OPTION | — | 触摸 AF 选项 |
| **`touchAF`** | AFPOSITION | TOUCHAF_RESULT | **点按对焦** |
| `touchAFPoint` | AFPOSITION | — | 触摸 AF 点 |
| `ReleaseSelfTimer` | RELEASETIMER | — | 自拍计时 |
| **`Shot`** | — | **AFSHOTRESULT（= `RVF_LAST_PHOTO_URL_VALUE`）**, AVAILSHOTS, RemainRecTime | **拍照！并在响应里返回"最后一张照片的 URL"** |
| **`ShotWithGPS`** | GPSINFO | AFSHOTRESULT, AVAILSHOTS, RemainRecTime | 带 GPS 拍照 |
| `SetLED` | LEDTIME | — | 机身 LED |
| **`SetFlash`** | FLASHMODE | CurrentFlashDisplay | 闪光灯 |
| **`SetStreamQuality`** | Quality | — | **切换 high / low 流** |
| `SetShutterSpeed` | ShutterSpeedValue | ApertureValue, EVValue | 快门 |
| `SetAperture` | ApertureValue | ShutterSpeedValue, EVValue | 光圈 |
| `SetEV` | EVValue | ApertureValue, ShutterSpeedValue | 曝光补偿 |
| `SetISO` | ISOValue | ApertureValue, ShutterSpeedValue, EVValue | ISO |
| `SetWB` | WBValue | — | 白平衡 |
| `SetMetering` | MeteringValue | — | 测光 |
| `SetDrive` | DriveValue | — | 驱动模式 |
| `SetQuality` | QualityValue | AVAILSHOTS, RatioChangeStatus | 画质 |
| `SetDialMode` | DialModeValue | GETINFORMATIONRESULT, RatioChangeStatus | **模式转盘（P/A/S/M…）** |
| `SetMovieResolution` | MovieResolutionValue | RemainRecTime, MovieResolutionRatio | 视频分辨率 |
| **`StartRecord` / `StopRecord`** | — | RemainRecTime / RemainRecTime, AVAILSHOTS, StopRecTime | **录像起停** |
| `StartShot` / `StopShot` | DriveValue | — | 连拍起停 |
| `StartZoom` / `StopZoom` | ZoomDirection / ZoomDirection | — / CURRENTZOOM | 电动变焦 |
| `SetFileSave` | FileSaveValue | — | 文件保存目标（卡 / 传输到手机） |
| `SetAFArea` | AFAreaValue | — | AF 区域 |
| `FillLight` | Light | — | 补光灯 |
| **`HeartBeat`** | Duration | — | **保活心跳（防止相机自动断网）** |
| `SetOperationState` | StateEvent | — | 状态机切换 |
| `SetTotalCopyItems` | TotalNumber | — | 批量拷贝计数 |

相关状态变量（`serviceStateTable`，`sendEvents="yes"` 的即为事件可订阅）：`RVF_FLASH_STATUS`、`RVF_FLASH_STROBE_STATUS`、`RVF_FOCUS_STATUS`、`RVF_SHUTTER_SPEED_SETUP_VALUE`、`RVF_SHUTTER_SPEED_LIST`、`RVF_APERTURE_SETUP_VALUE`、`RVF_APERTURE_LIST`、`RVF_REMAIN_SHOT_COUNT_VALUE`、`RVF_REMAIN_REC_TIME_VALUE`、`RVF_EV_SETUP_VALUE`、**`RVF_LAST_PHOTO_URL_VALUE`**、`RVF_ROTATION_STATUS`、`RVF_CURRENT_ZOOM_VALUE`、`RVF_MOVIE_RECORD_TIME`、`RVF_MONITOR_SWITCH`、`OperationState`（默认 `IDLE`）、`SystemUpdateID`。

**给 Android App 的三条关键推论**：
1. **快门 = `POST /smp_4_` + `SOAPACTION: "urn:schemas-upnp-org:service:ContentDirectory:1#Shot"`**，空 body 也行（Yarkhan 实测："You don't even need to send any xml for any of these actions"）。
2. **拍完照后 `Shot` 响应里的 `AFSHOTRESULT` 就是照片 URL**（状态变量名 `RVF_LAST_PHOTO_URL_VALUE`）→ **这就是"拍完立刻下载"的官方握手方式**。
3. **`HeartBeat` 必须定期发**，否则相机会自动断网（评论区 Leprous 报告 NX1000 "camera turn off its network very shortly, less than a minute"；另一人报告 "always loses its wi-fi networks after 10-20 minutes"）。

### 4.3 `GetInfomation` 的真实响应 ✅

op-co.de 原文（抓自 NX300，注意里面**硬编码了 `http://192.168.102.1:7679/...`**）：

```xml
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:GetInfomationResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
    <GETINFORMATIONRESULT>
      <Resolutions>
        <Resolution><Width>5472</Width><Height>3648</Height></Resolution>
        <Resolution><Width>1920</Width><Height>1080</Height></Resolution>
      </Resolutions>
      <Flash>
        <Supports><Support>off</Support><Support>auto</Support></Supports>
        <Defaultflash>auto</Defaultflash>
      </Flash>
      <FlashDisplay>
        <Supports><Support>off</Support><Support>auto</Support></Supports>
        <CurrentFlashDisplay>off</CurrentFlashDisplay>
      </FlashDisplay>
      <ZoomInfo>
        <DefaultZoom>0</DefaultZoom>
        <MaxZoom>1</MaxZoom>
      </ZoomInfo>
      <AVAILSHOTS>289</AVAILSHOTS>
      <ROTATION>1</ROTATION>
      <StreamQuality>
        <Quality><Option>high</Option><Option>low</Option></Quality>
        <Default>high</Default>
      </StreamQuality>
    </GETINFORMATIONRESULT>
    <StreamUrl>
      <QualityHighUrl>http://192.168.102.1:7679/livestream.avi</QualityHighUrl>
      <QualityLowUrl>http://192.168.102.1:7679/qvga_livestream.avi</QualityLowUrl>
    </StreamUrl>
    </u:GetInfomationResponse>
  </s:Body>
</s:Envelope>
```

`SetFlash` / `SetStreamQuality` / `SetResolution` / `SetLED` / `MULTIAF` / `touchAF` / `AFRELEASE` / `ReleaseSelfTimer` / `ShotAF` / `ShotWithGPS` 这一组名字在 op-co.de 的 SCPD 摘要里也出现过（"Additional SOAP services are provided for changing settings like focus and flash (`/smp_3_`)"）。

### 4.4 视频流的坑 ✅（对 Android 端实现极其重要）

来源：wiki 页 [What's known about livestream on NX camera](https://github.com/ge0rg/samsung-nx-hacks/wiki/What's-known-about-livestream-on-NX-camera)

1. **相机对流请求返回 HTTP 206**（Partial Content），但没有 Range 请求 —— 违反 W3C 语义。原文：
   > "HTTP 206 error while accessing the stream"
   > "The w3c states that HTTP 206 - partial content, means that there should be a range defined by the request, but that seems not the case with continuous video stream."
   > 解决办法：在中间加一个代理，把 `HTTP/1.1 200 OK` 转发给客户端。

2. **RIFF/AVI 头不完整**（因为是无限流，长度字段为 0）。原始头：
   ```
   RIFF ('AVI '
        LIST ('hdrl'
              'avih' 56 0 0 0 272 0 0 1 0 640 480)
        LIST ('strl'
              'strh' 56 'vids' 'H264 ' 0 0 0 0 1001 30000 0 0 1048576 10000 921600)
        ...
   ```
   需要重写 `avih` 里的 `dwMicroSecPerFrame`、`dwMaxBytesPerSec`、`dwTotalFrames` 与 `strh` 里的 `dwLength`（原为 0）才能让标准播放器/API 接受。

3. **实际可行的播放方式（原文）**：
   ```
   ffplay -analyzeduration 5000 http://192.168.107.1:7679/livestream.avi
   ```
   或设 `avFmtCtx->max_analyze_duration = 5000;`

4. 有人在评论里报告：**VLC 会因 206 卡住**；`ffplay` 直接可用但有撕裂；加 `-analyzeduration 5000` 后不卡。

→ **对 Android 的启示**：不要用 `MediaPlayer`/`ExoPlayer` 直接播 `livestream.avi`（大概率失败）。要么自己解析 RIFF 头 + 用 MediaCodec 解 H.264，要么在 App 内做一个本地 HTTP 代理把 206 改写成 200 并补全 RIFF 头。**这是本项目风险最高的一块。**

### 4.5 `mewlips/nx-remote-controller-mod` 的实际端点 ✅

项目主页：<https://mewlips.github.io/nx-remote-controller-mod/>（AGPL-3.0，支持 NX1/NX500/NX300）
仓库：<https://github.com/mewlips/nx-remote-controller-mod>

**它是什么**：把 NX1/NX500 的 `MOBILE` 按钮劫持成一个**相机上运行的 HTTP 服务器 + Web UI**，"Remote control camera from web browsers / Mirroring camera screen / Remote control functions / Multiple cameras control / Silent shutter on/off (NX500) / LCD on/off / Automatic Wi-Fi connection (NX300) / Telnet/FTP (NX300)"。

**安装前提 ✅（原文）**：
- NX500 固件 **1.12**，或 NX1 固件 **1.41**
- 需要 [nx-wake-on 1.11](https://github.com/ottokiksmaler/nx500_nx1_modding/tree/master/nx-on-wake) + [nx-patch 5.3](https://github.com/ottokiksmaler/nx500_nx1_modding/tree/master/video-bitrate-mods/nx-patch)
- 把 zip 解压到 SD 卡根目录，插卡开机即安装

**REST API 全表 ✅**（来自仓库根目录 `swagger.json` 原文，标题 `nx-remote-controller-mod` v0.9）：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v1/camera/info` | GET | 返回 JSON：`camera model, firmware version, mac address` |
| `/api/v1/camera/status` | GET | 返回 JSON：`battery charging, battery percent, battery level, drive, mode, hevc, cameras` |
| `/api/v1/liveview/get` | GET | 取实时取景图 |
| `/api/v1/liveview/get_low` | GET | 取低质量实时取景图 |
| `/api/v1/osd/get` | GET | 取 OSD 屏幕图像 |
| `/api/v1/input/inject?key=###` | GET | 向相机注入按键事件 |
| `/api/v1/input/inject?keydown=###` | GET | 按键按下 |
| `/api/v1/input/inject?keyup=###` | GET | 按键抬起 |
| `/api/v1/input/inject?mousedown=1` | GET | 鼠标按下 |
| `/api/v1/input/inject?mousemove=x-y` | GET | 鼠标移动（`-` 分隔，源码里被替换成空格） |
| `/api/v1/input/inject?mouseup=1` | GET | 鼠标抬起 |
| `/api/v1/input/inject_keep_alive` | GET | **保活**（对应原厂 `HeartBeat`） |
| `/api/v1/lcd/on` / `/off` | GET | LCD 开/关 |
| `/api/v1/lcd/video` | GET | 关闭 OSD |
| `/api/v1/led/on` / `/off` | GET | 机身 LED |
| `/api/v1/shutter/normal` | GET | 机械快门（**仅 NX500**） |
| `/api/v1/shutter/silent` | GET | 电子快门（**仅 NX500**） |

按键映射表在 wiki：<https://github.com/mewlips/nx-remote-controller-mod/wiki/Key-mappings>

`api_server.c` 原文（✅）显示它是 **Mongoose** 库，监听 **80**，非 `/api/v1/` 前缀的请求走 `mg_serve_http` 静态文件（`web_root/index.html` 等），且 `enable_directory_listing = "yes"`。所有 API 响应都带 `Access-Control-Allow-Origin: *`。

**NX500 静音快门的精确命令 ✅**（`nx-remote-controller-daemon/shutter.c` 原文）：

```c
void shutter_set_silent(bool silent)
{
    if (silent) {
        system("/usr/bin/st cap capdtm setusr ADJUSTSHUTTERTYPE 0x750001");
    } else {
        system("/usr/bin/st cap capdtm setusr ADJUSTSHUTTERTYPE 0x750000");
    }
}
```

对应的读回命令（`install/app/shutter.sh` 原文，且 **NX1 不支持**）：

```sh
if is_nx1; then echo 'Not supported.'; exit 0; fi
val=$(/usr/bin/st cap capdtm getusr ADJUSTSHUTTERTYPE | sed -e 's/.*(//' -e 's/).*//')
if [ "$val" == "0x750001" ]; then echo '^Silent!Normal'; else echo 'Silent!^Normal'; fi
```

**重要提醒**：Mod 的 liveview 是直接读相机内部 framebuffer / IPC 输出（`liveview.c`、`osd.c`），**不是**通过 Wi-Fi 的 7679 流。所以 Mod 的 `/api/v1/liveview/get` 对"不装 Mod 的纯 App 方案"没有直接可复用性 —— 但它证明了**同一台相机上"HTTP + JSON 轮询单帧 JPEG"是可行的交互模型**。

**仓库里还有一个现成的 Android 客户端** ✅：`NXRemoteController/`，Java + Gradle，包名 `com.mewlips.nxremote`，关键文件：
- `app/src/main/java/com/mewlips/nxremote/DiscoveryPacketReceiver.java`（2,688 B）—— **接收 5681/UDP 的 `NX_REMOTE|...` 广播**
- `MainActivity.java`、`NXCameraInfo.java`
- `AndroidManifest.xml`

→ **这是"Android 上如何发现并连接 NX 相机"的最佳参考实现，直接可读。**

### 4.6 通过 telnet 控制 NX 相机（需要 Mod / 自制启动脚本）✅

#### 4.6.1 如何拿到 telnet（NX500）

`sdcard-NX500/autoexec.sh`（见 3.1 节）用 `/mnt/mmc/telnetd &` 起 telnet。⚠️ 前提是 NX500 固件进入"诊断/factory mode"，且需要 SD 卡上放好从 NX300 拷来的 `telnetd` / `busybox` 二进制。MultiNX README 原文补充：

> "The NX500 also enters a diagnostic factory mode that disallows touch screen focus unless it is circumvented by installing code in the NX500 file system as described by [ottokiksmaler/nx500_nx1_modding - Running_scripts_without_factory_mode_BT.md](https://github.com/ottokiksmaler/nx500_nx1_modding/blob/master/Running_scripts_without_factory_mode_BT.md)"

即：**NX500 需要靠 `autoexec.sh`（旧路线）或"开蓝牙触发脚本"（新路线，NX-KS2 用这条）才能拿到 telnet。**

#### 4.6.2 `st` 命令 —— 相机的"瑞士军刀" ✅

来源：[Control-camera-from-command-line.md](https://github.com/ottokiksmaler/nx500/blob/master/Control-camera-from-command-line.md) 与 [ST Commands.md](https://github.com/ottokiksmaler/nx500/blob/master/ST%20Commands.md)（注意文件名里有空格）

一级子命令：`help readl writel dump gpio hdmi log lcd cap pmu clk thread key firmware util app leak devman stlcd bat rtc tbm micom misc oic dvfs adc`

**拍照 / 录像 / 取景**：
```sh
st app nx capture single              # 单张拍摄（内部就是发 S1/S2 键事件）
st app nx capture quality raw+sfine   # sfine,fine,norm,raw,raw+sfine,raw+fine,raw+norm
st app nx capture wb auto
st app nx capture af-mode single      # single,caf,auto,manual
st app nx record start                # 开始录像
st app nx record stop
st app nx record pause
st app nx record resume
st app nx record resolution [show,name]
st cap capt liveview                  # 取景
st cap capt single start
st cap capt conti / burst / aeb / bulb / timer / panorama / 3dshot
st cap capt remotevf [start|stop]     # ★ 直接切换 Remote Viewfinder！
st cap capt iris drive 7.1            # 光圈
st cap capt smart                     # SMART 模式
st cap live start / stop              # liveview 管线
st cap fenx lv [otf|mem|120|stop]
st cap fenx still [single|post|smat|postm|lrc|postlrc]
```

**参数设置**：
```sh
st app mode [smart-pro|auto|p|a|s|m|custom2|custom1]
st app drive [single|conti_n|conti_h|timer|bracket]
st app lens [info|pzoom][tele|wide|stop]
st app bb [lcd|evf|tv][on|off|osd|video]      # 黑屏控制
st app disp [lcd|evf|auto]
st app isfull
st app wifidirect start / stop
st app led [blink|off] [duration]
st app power [on]
st cap capt size [full|n1..n4|w1..w4|s1..s4]
st cap capt hdr [off|on|art]
st cap capt nr [off|ltnr|adnr]
st cap capt ois [, mode]
st cap capt lens focus [near|far]
st cap capt mf on / off
```

**★ USB 模式切换（与第 5 节联动）**：
```sh
st app usb [mass | ptp | on | off]
#   mass : set usb type as mass storage
#   ptp  : set usb type as PTP
#   on   : connect usb
#   off  : disconnect usb
```

**按键注入**（`st key` / `st app nx key`）：
```sh
st key click OK
st key press S1
st key click S2
st key release S1
st key jog jog1_cw
st key touch click 300 300
st key touch push 200 200
st key touch release 400 300
st key getmode
st key getdrive

st app nx key show              # 列出所有键名
st app nx key push [name]
st app nx key released [name]
st app nx key single [name]     # 按下+抬起
```

**NX500 的键码表 ✅**（`xinput test 6` / `xinput test 8` 原始输出）：

| 键 | keycode | 键 | keycode |
|---|---|---|---|
| EV | 181 | JOG1_CW | 185 |
| MENU | 135 | JOG1_CCW | 186 |
| REC | 179 | JOG2_CW | 171 |
| Fn | 180 | JOG2_CCW | 173 |
| PB | 191 | AEL | 164 |
| DEL | 91 | **S1（半按快门）** | **133** |
| OK | 104 | **S2（全按快门）** | **134** |
| LEFT/UP/RIGHT/DOWN | 83/80/85/88 | MOBILE | 233 |
| MODE_DIAL_SAS/S/AUTO/P/A | 67/70/72/73/74 | MODE_DIAL_M/C | 76/79 |

原文注：*"I guess that NX1 should have C1/C2 at 77/78. NX500 actually has **two** Custom Modes supported. They can be accessed by simulating mode dial operations by using **st key** command."*

X11 输入设备名：`Drime5 ADC Keyboard`（id=6）、`Drime5 GPIO Keyboard`（id=8）、`Touchscreen`（id=7）。按键走标准 `/dev/input/event*`。

#### 4.6.3 `dfmsd` / `dfmstool` —— 工厂命令 & PTP 通道 ✅

来源：[DFMS Commands.md](https://github.com/ottokiksmaler/nx500/blob/master/DFMS%20Commands.md)、[Control-camera-from-command-line.md](https://github.com/ottokiksmaler/nx500/blob/master/Control-camera-from-command-line.md)

```sh
dfmsd -t &                                     # test 模式启动 daemon
dfmstool -s 'osd string 7 Hello World!'        # script 命令（屏幕显示）
dfmstool -p 'proximity wait in'                # ★ 发 normal 格式 PTP 命令
dfmstool -P 0x10030000 1397563418              # ★ 发 predef 格式 PTP 命令
dfmstool -l                                    # 列出 process mark ID
dfmstool -a / -r <ID> / -R <ID>                # 读 calibration/adj 数据
dfmstool -w <ID> <size> <value>                # 写 adj 数据（危险）
dfmstool -d                                    # 存默认 adj
killall dfmsd
```

`dfmstool -h` 原文对 `-P` 的说明：
```
-P|--ptp-predef [CMD...]    Send ptp command with predef format.
                            Ex) dfmstool --ptp-predef 0x10030000 1397563418
```

⚠️ `dfmstool -p/-P` 是**在相机本机 shell 里发 PTP 命令**，不是网络 PTP。但这是"用 PTP 控制 NX500"最有希望的实验入口。

从 `dfmstool -R WIFI_MAC_ADDR` 的输出 `A021959D****` 可知 NX500 的 Wi-Fi MAC 前缀是 `A0:21:95`（与 NX300 同厂 OUI，`op-co.de` 的 nmap 也显示 NX300 MAC 为 `A0:21:95:**:**:**`）—— **可用于根据 SSID 后缀反推/校验相机身份**。

#### 4.6.4 MultiNX 的 telnet 用法 ✅

[ajavamind/Multi-NX-Camera-Control](https://github.com/ajavamind/Multi-NX-Camera-Control)，Processing/Java，GPL-3.0。README 原文要点：

- "MultiNX uses **Telnet and HTTP protocol commands** to communicate with the connected NX cameras."
- "The application **only invokes telnet and HTTP protocols** … does not modify the internal Tizen file system to add shell or application code. The application sends commands to the camera provided by telnet. **A start up `autoexec.sh` shell script in the root folder of the SD memory card starts the telnet and HTTP services in the camera.** The shell script also starts an **optional FTP server for photo transfer** after a shoot is finished."
- 支持机型：**NX2000、NX300、NX500**（+ NX30）。**不支持 NX1000/NX1100**（VxWorks，非 Linux）。
- NX2000/NX300 用相机里的 **E-Mail Wi-Fi 设置服务**接入本地网络；**NX500 用菜单打开 Wi-Fi**。
- **"The app does not provide Live-View with the cameras because I have not found a way to set this up with only telnet commands."** ← ⚠️ **重要负面结论：纯 telnet 路线做不到 live view，必须走 7679 的 RVF 流。**
- "The NX cameras do not show their local network IP address. Only the camera's MAC address is available in the Menu - Settings - Device Information." → 需要用网络扫描 App 按 MAC 找 IP。
- **警告原文**："Using telnet commands incorrectly can lock up the camera… If the camera hangs, a power-on restores the camera functions after removing and reinserting the battery. **USE AT YOUR OWN RISK**."
- README 明确列出 NX500 上"not all hardware keys can be controlled remotely"，NX300/NX500 实现"not yet complete"。
- 照片回传：**不用 HTTP，用 FTP**（`busybox tcpsvd -u root -vE 0.0.0.0 21 ... ftpd -w /mnt/mmc`），PC 端用 FileZilla。它说"This application is not an FTP client"。

MultiNX 软件按键（对 NX2000 物理键的映射）：Focus（按住对焦，读回 shutter/FN/ISO）、Shutter、EV Left/Right（转轮）、EV、Record、Home、PB、Screen（截屏）、Show、Save（**只存 JPG**）、Sync、MENU、Fn、Back、Mode、Status、EXIT。

#### 4.6.5 NX500 静音快门 / 其他 Mod 特性 ✅

[ottokiksmaler/nx500_nx1_modding](https://github.com/ottokiksmaler/nx500_nx1_modding) README 列出的可用能力（相关者）：

- 命令行控制相机（`st` 与 `dfms` 两条路）—— 改变模式、曝光参数、拍照、设分辨率与画质
- 处理按键事件（检测与发送，含触摸）
- 对焦命令行操作、镜头操作
- LCD / EVF 控制
- 在相机上跑 **SSH/SFTP/SCP 服务器**、**FTP 服务器**、**telnet 服务器**
- **开启 PTP 模式**（见第 5 节）
- 在相机上跑完整浏览器（`browser` 命令）
- 截图 LCD
- NX500 全电子（静音）快门
- NX500 1080p120 视频
- 焦点堆栈（`focus_stack.c` / `focus_stack` 二进制）
- NX-KS2 / nx-patch 一键包

**GHOST 提示**：wiki 的 [WebBrowser](https://github.com/ge0rg/samsung-nx-hacks/wiki/WebBrowser) 页揭示相机自带一个浏览器，且可以这样从 telnet 调起：

```sh
web-browser __AUL_MIME_TYPE__ mime mode-dial 7 wifi-key popup url https://op-co.de/
```

其中第一个参数是一个持久化"安全 cookie" `zaybxcwdveuftgsh`（来自 Linux 内核 `crypto/ansi_cprng.c` 的 `DEFAULT_V_SEED`），`mode-dial` 必须等于当前转盘档位（0=Auto … 7=Wi-Fi）。**支持 `file://` 协议**，可显示 SD 卡上的本地 HTML/照片。⚠️ 与"手机连相机"无关，但说明相机端 UI 可被完全接管。

---

## 5. PTP / USB

### 5.1 默认行为：USB = 大容量存储，相机功能被禁用 ✅

一手来源：[Enable-PTP-on-NX500-NX1.md](https://github.com/ottokiksmaler/nx500_nx1_modding/blob/master/Enable-PTP-on-NX500-NX1.md)（ottokiksmaler）

> "To best of my knowledge **NX500 is not supposed to be used tethered by USB to PC**, that was left as a domain of NX1 as professional model. **When you connect it via USB cable to PC it automatically switches SD card to mass storage mode and disables any camera operation** (at least my NX500 does when connected to Linux)."

### 5.2 开启 PTP/MTP 的方法：SD 卡根目录放空文件 `info.tgw` ✅

**原文步骤（完整照抄）**：

1. Create text file with just a line break (CR/LF - simple enter in Notepad will do) named **`info.tgw`** in the root of the SD card.
2. Insert SD card in camera
3. Connect camera via USB cable to PC
4. Turn on camera

> "That's it. You should see **a series of green dots on the left side of the camera indicating PTP mode is active**. `info.tgw` file instructs the camera to **start the daemon process in background and wait for PTP/MTP commands from the computer**."

⚠️ 注意命名不一致：ottokiksmaler 文档写 **`info.tgw`**，而 mewlips Mod 仓库里的文件叫 **`install/info.tg`**（12 字节）。**建议两个名字都试**（这是 Mod 作者实际打包的文件名）。

### 5.3 实测行为与限制 ✅

- Linux 下可直接浏览 DCIM 并**实时看到文件写入**（"you can see the size increasing"）。
- 传输速度：慢速 class 4 卡上约 **18 MB/s**。
- **RAW、JPG、视频全部可用**；只有菜单里的"照片尺寸"变灰（固定 28MP）。
- 视频文件**按 4 MB 分块写入** —— "nice for streaming offload to PC"。
- 相机进入 PTP 模式后**屏幕左侧显示 8 行绿字 OSD**（"you can see dots on the left - these are the lines, they just contain '.' by default"），这是 `dfmsd` 的 OSD overlay（见 4.6.3）。
- 要恢复触摸屏：`killall dfmsd`。

### 5.4 `gphoto2` 的实测能力 ✅（原文输出）

```
$ gphoto2 --port usb: --abilities
Abilities for camera : USB PTP Class Camera
Serial port support : no
USB support : yes
Capture choices :
: Image
: Preview
Configuration support : yes
Delete selected files on camera : yes
Delete all files on camera : no
File preview (thumbnail) support : yes
File upload support : yes
```

**关键限制（原文）**：
> "Unfortunately, **I can't trigger shooting or any other setting from command line (neither gphoto nor libptp support Samsung).**"
> "From what I have seen so far on camera - **`dfmsd` daemon expects textual commands from the computer.** This should be doable with libptp."

→ ✅ **结论：NX500 的 USB PTP 可以浏览/下载/删除文件，但公开工具链（gphoto2/libptp）无法触发快门或改设置。** 有希望的方向是逆向 `dfmsd` 的文本命令协议（它同时是 "waiting for commands over USB" 模式）。

### 5.5 USB 描述符 ✅⚠️

dpreview 帖 "NX500 - tethered shooting - simple hack"（[thread 3964253](https://www.dpreview.com/forums/threads/nx500-tethered-shooting-simple-hack.3964253/)）的搜索结果片段里出现：

```
libusb1(1): Found USB class device (class 0x6, subclass, 0x1, protocol 0x1)
```

⚠️ 这是**搜索结果摘要片段**，我无法取得该页完整原文（dpreview 被 Cloudflare 拦、代理也 403）。但 `class 0x6` = Still Image，`subclass 0x1` = Still Image Capture，`protocol 0x1` = **PTP**（若为 `0x2` 则是 MTP）。**这与"NX500 在 `info.tgw` 下暴露标准 PTP 接口"完全一致**，且与 gphoto2 识别为 `USB PTP Class Camera` 互相印证。

### 5.6 Android + USB-OTG 拉照片 ⚠️❓

**本会话没有找到任何针对 NX500 的 Android USB-OTG + PTP 实测案例。** 以下为需要你自己验证的要点，**标注为未验证**：

- ✅ 可确认的前提：NX500 在 `info.tgw` 模式下是**标准 USB PTP 设备**（见 5.4/5.5），Android 的 `android.hardware.usb.host` + `UsbManager` 理论上能拿到 bulk endpoint 并与 PTP 会话通信。
- ❓ **Android 系统自带的 MTP/PTP 服务会抢占设备**：`UsbManager` 的 `openDevice()` 之外并没有公开的 "getPtpDevice" API；系统 `MtpService` 会为 PTP 设备建立会话。第三方 App 若要与相机对话，通常需要自己实现 PTP over `UsbDeviceConnection`（OpenSession / GetStorageIDs / GetObjectHandles / GetObject），或让用户在开发者选项里关掉相关系统行为。**这一条我没有在本次调研中找到 NX 专属的一手证据，属通用 Android 知识，务必实测。**
- ❓ **"没有任何 App 就能拉照片"不成立**：系统级 PTP 只在系统文件管理器/相册支持 MTP/PTP 导入的部分厂商 ROM 上可用；标准 AOSP 不提供面向任意 PTP 相机的通用浏览 UI。所以**自研 App 内实现 PTP 是可行且必要的路径**。
- ⚠️ 通用参考（非 NX 专属，仅方向性）：[Android 相机有线连接开发：基于 PTP/MTP 协议的实现与多机型兼容实践](https://blog.csdn.net/m0_68791363/article/details/161196277)、[Access Camera Storage via USB Host Mode on Android](https://stackoverflow.com/posts/31741717/revisions)。
- ⚠️ 注意：即使用 `st app usb ptp`（见 4.6.2）也能在 shell 里切到 PTP，但前提是你已经有 shell。

---

## 6. 与 Samsung NX 相机通信的开源项目清单

| # | 项目 | 语言 | 它做什么 | 揭示的协议细节（已核实） |
|---|---|---|---|---|
| 1 | [ge0rg/samsung-nx-hacks](https://github.com/ge0rg/samsung-nx-hacks) | Python / shell / 文档 | NX300/NX2000 固件 hack、逆向知识库（142★） | **本次调研的协议主源**：`xml/Remote-ViewFinder/smp_2_.xml`、`xml/Remote-ViewFinder/smp_3_.xml`（完整 SOAP Action 表）、wiki 的 RVF / WiFi-Feature-Matrix / PC-Auto-Backup / WebBrowser / livestream。工具：`tools/drime3-firmware.py`、`tools/slp-firmware.py`、`tools/m7mu.py`、`tools/m7mu-decompress.py`、`tools/sfresource.py`；脚本：`sdcard/autoexec.sh`、`sdcard/inetd.conf`、`sdcard/shutter.sh`。 |
| 2 | [mewlips/nx-remote-controller-mod](https://github.com/mewlips/nx-remote-controller-mod) | C (Mongoose) + JS + **Java/Android** | NX1/NX500/NX300 上跑 HTTP 服务器 + Web UI，镜像屏幕、远程按键、静音快门、多机控制 | **`swagger.json` 给出 `/api/v1/camera/{info,status}`、`/api/v1/liveview/{get,get_low}`、`/api/v1/osd/get`、`/api/v1/input/inject?key=/keydown=/keyup=/mousedown=/mousemove=x-y/mouseup=`、`/api/v1/input/inject_keep_alive`、`/api/v1/lcd/{on,off,video}`、`/api/v1/led/{on,off}`、`/api/v1/shutter/{normal,silent}`**；端口 **80**；UDP 发现 5681/64 字节；`st cap capdtm setusr ADJUSTSHUTTERTYPE 0x750001/0x750000`；网卡 `mlan0`(新) / `wlan0`(旧)；**仓库内含 Android 客户端 `NXRemoteController`（`com.mewlips.nxremote`）与 `DiscoveryPacketReceiver.java`** |
| 3 | [ajavamind/Multi-NX-Camera-Control](https://github.com/ajavamind/Multi-NX-Camera-Control) | Processing / Java（Windows/Linux/Android） | telnet + HTTP 同步控制最多 4 台 NX2000/NX300/NX500，间隔拍摄 | `sdcard-NX500/autoexec.sh` **全文**（telnetd + busybox httpd + FTP 21 + killall dfmsd）；`sdcard-NXnnnn/autoexec.sh`、`inetd.conf`；JSON 配置格式；**明确说明 NX500 原厂缺 telnet/http/ftp、纯 telnet 做不了 live view** |
| 4 | [ottokiksmaler/nx500_nx1_modding](https://github.com/ottokiksmaler/nx500_nx1_modding) | Markdown + C | NX500/NX1 modding 总索引（nx-patch、nx-on-wake、NX-KS2） | **`Enable-PTP-on-NX500-NX1.md`（`info.tgw` 开 PTP，gphoto2 abilities 全文）**；`Checking_shutter_count.md`；`Running_scripts_without_factory_mode_BT.md`；`Control_LCD_EVF.md`；`Customizing keys.md`；`doc/Boot_Process_and_Speeding_up_Mods.md` |
| 5 | [ottokiksmaler/nx500](https://github.com/ottokiksmaler/nx500) | Markdown + C | NX500 具体操作手册与工具 | **`ST Commands.md`（`st` 全命令参考，含 `st app usb [mass|ptp|on|off]`、`st cap capt remotevf`）**；**`DFMS Commands.md`（`dfmsd`/`dfmstool -p/-P` PTP 通道、adj 数据全表、WIFI_MAC_ADDR）**；`Control-camera-from-command-line.md`；`Working-with-key-events.md`（键码表）；`Enable-PTP-on-NX500-NX1.md`；`Enable-Screenshot.md`；`Running-telnet-server-on-camera.md`；`Running FTP server on the camera.md`；`ssh-server/`；`focus_stack.c`；`focus_buttons.c`；`Working with focus.md`；`Working with lens.md`；`Poker.md`（跨进程内存读写） |
| 6 | [ge0rg/samsung-nx-emailservice](https://github.com/ge0rg/samsung-nx-emailservice) | Python (Flask) | 重实现 Samsung SNS/Email 云 API，让相机能把照片发到自建服务器/Mastodon | 相机侧硬编码主机名 `gld.samsungosp.com`、`www.samsungimaging.com`、`www.ospserver.net`、`snsgw.samsungmobile.com`；Email 端点为 `POST /social/columbus/email?DUID=...`，multipart 里一个 `message`(XML) + 多个 `binary`(JPEG)；社交为 `POST /<site>/auth/`、`/<site>/photo/`、`/upload/<upload_id>/file`；**NX500 支持 email 上传**；Samsung 网络栈的怪癖：multipart 缺终止 boundary、发 HTTP 响应必须**单个 TCP 段**（否则 Zoran 机型不认）；**服务不能跑在 192.168.x.x 上，要用 10.x.x.x** |
| 7 | [warwickh/pc-autobackup](https://github.com/warwickh/pc-autobackup) | Python | PC Auto-Backup 的完整 Python 重实现（Linux/Mac） | 相机→PC 的 UPnP/DLNA 方向；`/DMS/SamsungDmsDesc.xml`、`/DMS/ContentDirectory1.xml`、`/DMS/ConnectionManager1.xml` |
| 8 | [jdieter/nx300m-autobackup](https://github.com/jdieter/nx300m-autobackup) | shell | NX300M 自动备份 | 被 ge0rg wiki 与 mewlips 列为相关项目 |
| 9 | [HausnerR/nx300-hacks](https://github.com/HausnerR/nx300-hacks) | shell/C | NX300 间隔拍摄 + 充电即拍 | — |
| 10 | [hunkreborn/Samsung-NX2000-Moding-Hack](https://github.com/hunkreborn/Samsung-NX2000-Moding-Hack) | 脚本 | NX2000 modding | — |
| 11 | [fritzchentastig/NX300M-MFW](https://github.com/fritzchentastig/NX300M-MFW) | 固件 | NX300M 修改固件（可执行脚本必需） | — |
| 12 | [3dguy/nx500](https://github.com/3dguy/nx500) | 文档 | NX500 rooting & hacking（dpreview 帖衍生） | ❓ 本会话未能取得其内容（search 只给出标题）。⚠️ 与 `ottokiksmaler/nx500` 是两个不同仓库，勿混淆 |
| 13 | [jam1garner/wb35f-research](https://github.com/jam1garner/wb35f-research) + [jam1.re 博客](https://jam1.re/blog/camera-hacking-and-emulation) | Python | Zoran COACH 紧凑机型固件分析 | 非 NX 系列，方法论可借鉴 |
| 14 | NX-KS2（闭源二进制） | — | NX500/NX1 最全 mod 包（EV 双击菜单、延时、码率、静音快门、去 30 分钟限制） | 镜像：<https://nxfiles.nx.tc/files/NX-KS-mod/>，[Facebook](https://www.facebook.com/NXKS2/)（已停更）。**包含 nx-remote-controller-mod（自 v2.56 起）** |
| 15 | NXFiles | 归档站 | 所有 NX 固件/手册/OSS 发布 | <https://nxfiles.nx.tc/>；含 [NX500 Service Manual PDF](https://nxfiles.nx.tc/files/NX500/Service-Manual/SM-NX500.pdf)（本会话无法解析 PDF） |
| 16 | lemmster / cedarandthistle 脚本 | shell | NX300 通过 FTP 后台自动备份 | <http://lemmster.de/auto-backup-from-nx300-via-ftp.html>、<https://cedarandthistle.wordpress.com/2014/11/01/autobackup-to-linux-from-the-samsung-nx300m/> |
| 17 | Yarkhan 的 PHP/JS 脚本 | PHP + JS(AJAX) | NX1000/NX1000 系列 RVF 配对 + 取流（**未公开仓库**） | ⚠️ 仅存在于 op-co.de 评论区的自述（"I did the pairing and video streaming using a couple of simple PHP scripts"、"I rewrote the script … using javascript and ajax"）。GitHub 账号 [Yarkhan](https://github.com/Yarkhan/) 下未见对应仓库 |

---

## 7. 给 Android App 的落地结论（基于以上已核实事实）

### 7.1 推荐架构

**方案 A（唯一"原厂固件、无 Mod"可行路线）—— UPnP/SOAP + 7679 流**

1. 用户在相机上选择 **Remote Viewfinder**（或 MobileLink，若 7676 也开）。
2. 手机连 SSID `AP_SSC_NX500_*`（**不要硬编码相机 IP**，从 DHCP 网关取）。
3. `GET http://<cam>:7788/mode/control`，带 `User-Agent: SEC_MODE_<phone-mac>`、`Access-Method: manual`、`NTS: alive`、`HOST-Mac`、`HOST-Address`、`HOST-port: 7788`、`HOST-PNumber: none` → **相机屏幕会弹配对确认，需用户点确定**。
4. `POST http://<cam>:7676/smp_4_`，`SOAPACTION: "urn:schemas-upnp-org:service:ContentDirectory:1#GetInfomation"`，body 用上面那段 Envelope + `<GPSINFO>UNKNOWN</GPSINFO>` → **解析响应里的 `<StreamUrl><QualityHighUrl>` / `<QualityLowUrl>`**。
5. 实时取景：`GET` 该 `livestream.avi` URL，**必须自己处理 HTTP 206 与不完整 RIFF 头**（见 4.4）。
6. 快门：`POST /smp_4_`，`SOAPACTION: "…#Shot"`（空 body 亦可）→ 响应 `AFSHOTRESULT` 即**照片 URL**。
7. 对焦：`MULTIAF`（全部区域）或 `touchAF` + `<AFPOSITION>`（点按位置）。
8. 列照片/下载：`POST /smp_4_` `…#Browse`，`BrowseFlag=BrowseDirectChildren`，`ObjectID=0`，解析 DIDL-Lite（⚠️ 响应 XML 需真机抓一次确认 `<res>` URL 形态）。
9. **每 1–3 秒发 `HeartBeat`（`<Duration>`）**，否则相机自动断网。
10. UDP 5681 广播监听 `NX_REMOTE|...` 可做多机自动发现。

**方案 B（功能最全但需装 Mod）—— nx-remote-controller-mod**
装 NX-KS2（含该 Mod），然后 App 直接调 `/api/v1/*`（第 4.5 节全表），得到 JSON 状态、单帧 JPEG 取景、按键注入、静音快门。**代价：需破解固件、无鉴权（官方警告 "DO NOT CONNECT YOUR CAMERA TO PUBLIC WIFI NETWORK"）。**

**方案 C（有线，最省电但功能受限）—— USB PTP**
SD 卡根目录放 `info.tgw`（或 `info.tg`）→ USB 连接 → 手机作为 USB Host 自实现 PTP 拉文件。**能下载、不能控制快门**（gphoto2/libptp 都做不到）。⚠️ Android 侧系统 MTP 服务抢占问题需实测。

### 7.2 风险清单（按严重度）

| 风险 | 说明 |
|---|---|
| 🔴 **实时取景流兼容性** | HTTP 206 + 不完整 RIFF 头，Android `MediaPlayer`/`ExoPlayer` 大概率播不了。需自解析 + MediaCodec，或 App 内本地代理改写响应。**建议第一件事就是拿真机验证这一步。** |
| 🔴 **NX500 的 AP IP 未知** | 已核实 NX300 系列有三套网段。**必须动态取网关**，不能硬编码 `192.168.1.1`。 |
| 🟠 **端口可能变化** | wiki 明确说 "a port may vary"。流地址只能从 `GetInfomation` 响应读。 |
| 🟠 **配对需人工确认** | 首次连接相机屏幕会弹框。之后靠 `SEC_RVF_<MAC>` User-Agent 记住。 |
| 🟠 **必须心跳** | 不发 `HeartBeat` 相机很快断网。 |
| 🟡 **NX500 老机型的相机端不稳定性** | 论坛记录 "camera turn off its network… less than a minute"、"loses its wi-fi networks after 10-20 minutes"、相机 hang 后需拔电池。 |
| 🟡 **`GetInfomation` 只在 NX3000 上被完整验证** | wiki 原文自述 "only tested on Samsung NX 3000"。**NX500 上必须重测。** |
| 🟡 **v1 流是 Motion-JPEG/H.264 in AVI** | 640×480 起（`avih` 里 `640 480`），H.264 (`strh ... 'H264 '`)。 |
| 🟢 `Browse` 响应格式未取得 | 最后一块拼图，真机抓包即可解决。 |

### 7.3 明确"查不到"的清单

1. ❓ **NX500 在 AP 模式下的确切 IP**（`192.168.1.1` / `192.168.1.100` 等）—— 无任何一手来源。
2. ❓ **NX500 的 AP SSID 字面量**（按规则应为 `AP_SSC_NX500_x-XX:XX:XX`，但未见实例）。
3. ❓ **MobileLink 的协议文档** —— ge0rg wiki 没有该页，全网未见；只知道它开 7676。
4. ❓ **`Browse` 返回的 DIDL-Lite / JPEG 直链具体格式**。
5. ❓ **NX 系列 Wi-Fi 上是否存在 PTP/IP（15740）** —— 无任何证据，判定为"不存在"。
6. ❓ **`drime` / `DSC` / `nx_control` / `vapix` 类 HTTP API** —— 不存在（混淆项，见 2.5）。
7. ❓ **NX500 原厂固件 80 端口 HTTP 服务器** —— 判定为"不存在"（有强证据，见 3.1）。
8. ❓ **Android USB-OTG + PTP 直连 NX500 的实测案例** —— 未找到。
9. ❓ **3dguy/nx500 仓库内容** —— 本会话未能取得（工具受限）。

### 7.4 次要但有用的观察

- **`st cap capt remotevf [start|stop]`**（4.6.2）：如果拿到 shell，这是**一行命令切进/切出 Remote Viewfinder**，可用于自动化流水线。
- **`st app usb [mass|ptp|on|off]`**：shell 里切 USB 模式，不必插拔 SD 卡。
- **静音快门只属于 NX500**（NX1 不支持），命令 `st cap capdtm setusr ADJUSTSHUTTERTYPE 0x750001`。
- **NX500 Wi-Fi MAC OUI = `A0:21:95`**（`dfmstool -R WIFI_MAC_ADDR` 输出 `A021959D****`），可用于在局域网里识别 Samsung 相机。
- **NX500 是 DRIMeV + 完整 Tizen**：`mount / -o remount,rw` 可直接改根文件系统（NX300 系则是只读 rootfs，重启还原）。
- 相机拒绝与 **192.168.x.x** 网段的自建服务器通信，需用 **10.x.x.x**（⚠️ 来自 emailservice 项目经验，与本项目关系不大但同一网络栈）。

---

## 附：本报告引用的关键 URL

**一手协议文档 / wiki**
- <https://github.com/ge0rg/samsung-nx-hacks/wiki/Remote-Viewfinder>
- <https://github.com/ge0rg/samsung-nx-hacks/wiki/WiFi-Feature-Matrix>
- <https://github.com/ge0rg/samsung-nx-hacks/wiki/PC-Auto-Backup>
- <https://github.com/ge0rg/samsung-nx-hacks/wiki/What's-known-about-livestream-on-NX-camera>
- <https://github.com/ge0rg/samsung-nx-hacks/wiki/WebBrowser>
- <https://github.com/ge0rg/samsung-nx-hacks/wiki>（Home / 索引）

**一手协议原文（XML / 源码）**
- <https://github.com/ge0rg/samsung-nx-hacks/blob/master/xml/Remote-ViewFinder/smp_2_.xml>
- <https://github.com/ge0rg/samsung-nx-hacks/blob/master/xml/Remote-ViewFinder/smp_3_.xml>
- <https://github.com/ge0rg/samsung-nx-hacks/blob/master/README.mdwn>
- <https://github.com/mewlips/nx-remote-controller-mod/blob/master/swagger.json>
- <https://github.com/mewlips/nx-remote-controller-mod/blob/master/nx-remote-controller-daemon/api_server.c>
- <https://github.com/mewlips/nx-remote-controller-mod/blob/master/nx-remote-controller-daemon/network.c>
- <https://github.com/mewlips/nx-remote-controller-mod/blob/master/nx-remote-controller-daemon/network.h>
- <https://github.com/mewlips/nx-remote-controller-mod/blob/master/nx-remote-controller-daemon/shutter.c>
- <https://github.com/mewlips/nx-remote-controller-mod/blob/master/install/app/shutter.sh>
- <https://github.com/ajavamind/Multi-NX-Camera-Control/blob/master/sdcard-NX500/autoexec.sh>
- <https://github.com/ottokiksmaler/nx500_nx1_modding/blob/master/Enable-PTP-on-NX500-NX1.md>
- <https://github.com/ottokiksmaler/nx500/blob/master/ST%20Commands.md>
- <https://github.com/ottokiksmaler/nx500/blob/master/DFMS%20Commands.md>
- <https://github.com/ottokiksmaler/nx500/blob/master/Control-camera-from-command-line.md>
- <https://github.com/ottokiksmaler/nx500/blob/master/Working-with-key-events.md>
- <https://github.com/ottokiksmaler/nx500_nx1_modding/blob/master/README.md>
- <https://github.com/ge0rg/samsung-nx-emailservice/blob/master/README.md>

**博客 / 演讲**
- <https://op-co.de/blog/posts/hacking_the_nx300/>（NX300 nmap、SOAP、`GetInfomation` 响应、NDEF、X11）
- <https://op-co.de/blog/posts/samsung_wifi_cameras/>
- <https://op-co.de/talks/samsung-mastodon-bridge/fosdem2025.html>（FOSDEM 2025）
- <https://mewlips.github.io/nx-remote-controller-mod/>

**项目页**
- <https://github.com/mewlips/nx-remote-controller-mod>
- <https://github.com/ajavamind/Multi-NX-Camera-Control>
- <https://github.com/ottokiksmaler/nx500_nx1_modding>
- <https://github.com/warwickh/pc-autobackup>
- <https://github.com/3dguy/nx500>
- <https://nxfiles.nx.tc/>
- <https://play.google.com/store/apps/details?id=com.samsungimaging.samsungcameramanager>

---

*报告完成。所有 ✅ 标记的结论均可通过上述链接之一追溯；❓ 标记项为本次调研明确未能证实的内容。无真机验证是本报告最大局限。*
