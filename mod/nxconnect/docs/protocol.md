# NX500 遥控协议契约（实测汇总）

> 相机：Samsung NX500，固件 **1.12**，SoC **DRIMe5**，armv7l，kernel 3.5.0，hostname `drime5`，**glibc 2.13**
> 相机 IP 示例：`192.168.5.39`（DHCP 客户端，网关 `192.168.5.1`）
> 本文所有条目均为**真机实测**，未实测的已显式标注。

---

## English summary

This document is the protocol contract for remotely controlling a **Samsung NX500**
(firmware 1.12, DRIMe5 SoC, armv7l, glibc **2.13**). Everything here was verified
against real hardware — anything unverified is marked as such. The full document is in
Chinese; this summary is here so English-speaking reverse-engineers can tell whether it
is useful before translating.

### Critical prerequisite

The stock firmware exposes **none** of these services. All of them come from a
third-party camera-side package (see [NOTICE](../NOTICE)):

| Port | Service | Purpose |
|---|---|---|
| **80** | `nx-remote-controller-daemon` (mongoose HTTP) | JSON API, liveview, input injection, OSD |
| **8080** | busybox httpd + CGI | file listing, thumbnails |
| **21** | FTP (root, no password) | raw file retrieval, root = SD card |
| **23** | telnet (root, no password) | shell |
| 5681/udp | daemon broadcast | device discovery |

If the camera-side package is not installed, none of this works.

### What the API gives you

- **Liveview** — NV12 frames at 720×480, two quality levels
  (`GET /api/v1/liveview/get` 518,400 B; `/get_low` 259,200 B)
- **OSD mirror** — the camera's own screen as RGBA blocks, incremental by content hash
  (`POST /api/v1/osd/get` with a 108×8-hex `hashs` parameter). Full frame 1,383,276 B,
  changed-block responses typically 51 KB. This is the **only** way to read current
  exposure settings (see below).
- **Input injection** — X keysyms (`/api/v1/input/inject?key=…`). Keyboard injection is
  reliable; mouse injection only works in some screen regions.
- **Shutter** — half-press then release via `Super_L` / `Super_R` keysyms.

### What is *not* possible (hardware limits, verified)

- **Battery percentage** — not exposed. `st bat get_soc` returns `ret=-1`.
- **Lens information** — MCB bus is held by the camera app.
- **Reading or writing exposure parameters directly** — no usable path. `capdtm` exposes
  exactly one parameter (`ADJUSTSHUTTERTYPE`); `setusr` and `mcb` produce no output.
  The camera-side input injection is also broken (its `libxkbcommon` needs GLIBC 2.17,
  the camera has 2.13), so you cannot script the camera's own UI either.
  → **Therefore the OSD mirror is the only way to know what the camera is set to.**

### Measured performance ceilings

- Camera HTTP throughput: **~4.5–5 requests/second** total. The SoC is single-core and
  the camera's own app holds ~59% CPU. Liveview and OSD share this budget.
- OSD full frame: ~0.7 s (the camera redraws the whole overlay when the UI changes).

Everything else — exact JSON payloads, NV12 plane layouts, telnet prompt quirks,
`st` command availability — is in the Chinese sections below. Contributions that
translate or extend this document are welcome.

---

## 0. 端口与总体架构

| 端口 | 进程 | 说明 |
|---|---|---|
| **80** | `nx-remote-controller-daemon` | mongoose HTTP，**第三方组件**（非三星原厂）|
| **8080** | busybox httpd + CGI | 根目录 `/opt/usr/nx-ks/nx-rc/capdtm/www` |
| **21** | FTP（root 免密） | 根目录 `/opt/storage/sdcard`（SD 卡） |
| **23** | telnet（root 免密） | shell 通路，装机脚本无条件拉起 |
| 5681/udp | daemon 广播 | 设备发现 |

**重要**：原厂固件**没有** httpd、telnetd、ftpd。这些全部来自第三方改机包。
所以协议可用性取决于相机端是否装了该改机组件。

---

## 0.1 设备发现（UDP 5681）

相机是 **DHCP 客户端**，IP 不固定 → 应用广播发现而不是写死 IP。

- 端口 `5681/udp`，广播地址 `255.255.255.255`
- 载荷（**64 字节**，实测）：

```
NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|
```

字段依次为：`NX_REMOTE` | 协议版本 | 机型 | 固件版本 | MAC |

`/api/v1/camera/status` 的 `cameras[].packet` 字段就是同一串，可交叉验证。

---

## 0.2 实测响应原文（用于字段名精确匹配）

### `GET /api/v1/camera/info`（端口 80）

```json
{"model":"NX500","fw_ver":"1.12","mac_address":"02:30:0d:00:00:01"}
```

⚠️ 字段名是 **`fw_ver`** 与 **`mac_address`**（下划线），不是 `firmware` / `mac`。

### `GET /api/v1/camera/status`

```json
{"battery_charging":true,"battery_percent":-1,"battery_level":3,"drive":"UNKNOWN","mode":"P","hevc":"off","cameras":[{"ip":"192.168.5.39","packet":"NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|"}]}
```

⚠️ **`battery_percent` 恒为 `-1`** —— 这是"未知"哨兵值，不是真的 -1%。
相机硬件不提供百分比（见 §5.1）。解析时必须把负数当未知处理，否则界面会显示"-1%"。

⚠️ `drive` 实测常为 `"UNKNOWN"`、`mode` 也可能读不到 —— 是 daemon 的局限，不是请求错误。

---

## 1. 实时取景（liveview）

| 端点 | Body 长度 | 实测耗时 |
|---|---|---|
| `/api/v1/liveview/get` | **518400** 字节 | **178 ms** |
| `/api/v1/liveview/get_low` | **259200** 字节 | **126 ms** |

→ 相机端具备 **5–8 fps** 的输出能力，瓶颈在客户端解码与渲染。

### 1.1 NV12 帧布局（**关键，写错过**）

输出逻辑尺寸均为 **720×480**。Body 是原始 NV12（无文件头），布局如下：

#### 高清（518400 字节）
```
Y  平面 = 720 × 480        = 345600   （全分辨率）
UV 平面 = 720 × 480 / 2    = 172800   （交错 U/V）
合计                        = 518400
Y 索引:  yIdx = y * 720 + x
UV 索引: uvIdx = (y/2) * (720/2) * 2 + (x/2) * 2   ← 从偏移 yW*yH 开始
```

#### 低清（259200 字节）—— **Y 平面也减半！**

**精确尺寸表**（用 App 的真实公式反推的读数，`Nv12Decoder.kt` 已真机验证）：

| 画质 | Y 平面 | Y 字节 | UV（交错 U/V） | UV 字节 | Body 合计 |
|---|---|---|---|---|---|
| 高清 720×480 | 720 × 480 | 345600 | 360 × 240 | 172800 | **518400** = `w*h*3/2` |
| 低清 720×480 | 360 × **240** | **86400** | 360 × 240 | **172800** | **259200** = `w*h*3/4` |
| 高清 720×404 | 720 × 404 | 290880 | 360 × 202 | 145440 | 436320 |
| 低清 720×404 | 360 × 202 | 72720 | 360 × 202 | 145440 | 218160 |
| 低清 640×480 | 320 × 240 | 76800 | 320 × 240 | 153600 | 230400 |

**读法**：低清时 **Y 的宽高各自减半**（`w/2 × h/2`），所以 Y 字节只有高清的 **1/4**；
而 **UV 平面的字节数在两种画质下相同**（`(w/2) * (h/2) * 2`），因为它本来就是半分辨率存放的。
（低清 Y 平面是 **360 × 240**，不是 360×360 —— 这里错过一次。）

**唯一可靠的不变量是这条**（用它做自检，别手算平面尺寸）：

```
bodyLen == (低清 ? w*h*3/4 : w*h*3/2)
```

Kotlin 侧真机验证过的索引实现（**照这个写就对了**）：

```kotlin
val yW = if (low) w / 2 else w          // 低清 360
val yH = if (low) h / 2 else h          // 低清 240
val uvOffset    = yW * yH               // 低清 86400
val uvRowStride = (w / 2) * 2           // 720 字节/行
val yIdx  = if (low) (y / 2) * yW + (x / 2) else y * yW + x
val uvIdx = uvOffset + (y / 2) * uvRowStride + (x / 2) * 2
```

**这是本项目代价最大的一个 bug**：曾把低清 Y 平面当成 720×480 去读，
下标一路读到 259200 —— 正好越界，就是用户实测报的
`ArrayIndexOutOfBoundsException: length=259200; Index=259200`，App 直接崩溃。

**我后来在文档里又错过一次**：把"半分辨率的边长减半"误推成"Y 平面是 360×360 = 129600"，
于是一切加起来只有 194400，与 body 的 259200 对不上。
根因是**把「半分辨率」理解成了"两个维度都减半后仍是正方形"**，而实际是
`w/2 × h/2`（宽高各自减半，不是各减到 360）。

**结论：不要手算平面尺寸，直接用上面的不变量公式。**
启动时用 body 长度反查规格并断言，可以在解析阶段就拦住错误，而不是等到越界。

### 1.2 无歧义的绘图规格（共 10 种，各分高清/低清）

| 输出尺寸 | 高清 Body | 低清 Body |
|---|---|---|
| 720×480 | 518400 | 259200 |
| 720×404 | — | — |
| 720×380 | — | — |
| 640×480 | — | — |
| 800×480 | — | — |

> 表中"—"表示该组合未逐个实测长度；但都遵循 §1.1 的同一条布局规律。

### 1.3 失败响应（必须校验，不能只看状态码）

路由不存在时返回 **HTTP 404 且带一段 HTML 错误页**。
如果客户端只判断"拿到了字节就当帧解码"，会把错误页当图像 → 解码出垃圾或越界。

**正确做法**：严格校验 `Content-Type` 或以长度反查布局（§1.1 的自检式），
不满足就抛错，而不是硬解。

### 1.4 keep-alive

mongoose 默认支持 keep-alive。取景循环里**每次都 `disconnect()` 会显著掉帧**，
应复用连接（本项目在 `NxHttp.getBytesStrict(..., keepAlive = true)` 里实现）。

---

## 2. 按键注入

```
GET /api/v1/input/inject?key=<keysym>        # 按下并释放
GET /api/v1/input/inject?keydown=<keysym>    # 仅按下
GET /api/v1/input/inject?keyup=<keysym>      # 仅释放
GET /api/v1/input/inject_keep_alive          # 保活
```

keysym 名逐字对照相机端 `keyinput.js`。

### 2.1 ⚠️ 相机端注入实测失效（本项目结论）

`nx-input-injector` 缺 `libxdo.so.3`（库在但不在 `LD_LIBRARY_PATH`），
补上路径后撞上 `libxkbcommon.so.0` 要求 **GLIBC_2.17**，而相机是 glibc **2.13**。

**这是相机端的 ABI 问题，App 侧无法绕过。** 详见 `相机端遥控失效诊断报告.md`。

XTEST 扩展**本来就在**（`Initializing built-in extension XTEST`），无需额外开启。

---

## 3. 快门

```
GET /api/v1/shutter/normal
GET /api/v1/shutter/silent
```

⚠️ 实测这两个接口只写入 `ADJUSTSHUTTERTYPE`（可用 `st cap capdtm getusr ADJUSTSHUTTERTYPE` 看到变化），
**未见真的触发拍照**。设成 `MECHA`/`ELEC` 的行为差异未能确认。

### 3.1 真正可行的快门时序（脚本化实测）

相机 S1 = 半按（对焦，keysym `Super_L`），S2 = 全按（快门，`Super_R`）。

**必须 S1 按住不放，期间点击 S2**：

| 步骤 | 动作 | 等待 |
|---|---|---|
| 1 | `keydown(S1)` | — |
| 2 | 等待 | **450 ms** |
| 3 | `key(S2)` | — |
| 4 | 等待写入 | **2500 ms** |
| 5 | `keyup(S1)` | — |
| 6 | 冷却 | **1200 ms** |

实测矩阵结论：

- ✅ `keydown(S1)` → 等 → `key(S2)` → 等 → `keyup(S1)`：**能拍**
- ❌ `key(S1)` → `keyup(S1)` → `key(S2)`：**只对焦，不拍**
- ❌ 只发 `key(S2)`：无反应

原因：S1/S2 是机械两段式开关，全按需要半按**保持接通**。

### 3.2 ⚠️ 一次快门写 2 个文件

每按一次快门会同时生成：

```
SAM_xxxx.JPG
SAM_xxxx.SRW
```

所以**不能用"目录文件数变化量"判断拍了几张**——会把 1 张读成 2 张。
本项目曾因此误判为"连拍"。

---

## 4. 其他 daemon 端点

```
GET /api/v1/lcd/*      # 屏幕控制
GET /api/v1/led/*      # 指示灯控制
GET /api/v1/osd/get    # OSD 状态
```

> 这三个未逐个实测返回值，仅确认路由存在。

---

## 5. CGI 接口（端口 8080）

httpd 根目录：`/opt/usr/nx-ks/nx-rc/capdtm/www`（**不是 SD 卡**）

### 5.1 目录列表

```
GET /cgi-bin/dirlist?p=<路径>
```

返回**实时** JSON 目录列表（每次请求重新扫盘）。

### 5.2 缩略图

```
GET /cgi-bin/thumb?f=<文件名>&w=<64..1024>&q=<50..95>
```

约束与坑：

- `w` 有效范围 **64–1024**，`q` 有效范围 **50–95**
- **`srw`（RAW）不在白名单里** → 必然失败，App 端应直接显示占位而不是发请求
- **失败时返回 HTTP 200 + `text/plain`**，正文形如：

```
thumb error: source not found
```

（约 30 字节）

→ **必须校验 `Content-Type` 或 JPEG magic `FF D8`**，否则会把错误文本当图片解码。

- 相机端缩略图由 `convert` 生成，**单核 CPU 受限**，并发必须限制（本项目上限取 **2**）。

### 5.3 拍摄参数

```
GET /cgi-bin/capdtm-api?action=list
```

⚠️ **实测真机上所有字段都返回空字符串**：

```json
{"ok":true,"params":{"USERDATA_ISO":"","USERDATA_METERING":"","USERDATA_WB":"","USERDATA_IMAGEQUALITY":"","USERDATA_IMAGESIZE":"","USERDATA_IMAGEASPECTRATIO":"","USERDATA_PW":"","USERDATA_COLORSPACE":"","USERDATA_AFMODE":"","USERDATA_DRIVE":""}}
```

→ daemon 的用户态参数接口在这些机型上未实现，**不要依赖它**。

### 5.4 目录页面（mongoose 目录浏览）

`GET /DCIM/<dir>/` 返回 HTML **表格**，**含每个文件的大小**。
解析要点（实测踩坑）：

- 必须**先从原始单元格里取 `href`**，再 `stripTags` —— 顺序反了标签会被吃掉
- **跳过 `href=#` 的锚点**，否则目录里会混入伪条目
- 用**非贪婪** `<[^>]*?>` 匹配标签，避免跨标签吞内容

---

## 6. 文件下载

```
GET /DCIM/<目录>/<文件名>     # 端口 80，原图
```

- 相机 `100PHOTO` 目录实测有 **98 个文件**：91 个 `.SRW`（19–34 MB），少量 `.JPG`（7.4–9.2 MB）
- RAW 文件很大，**下载前应确认体积**再开始

---

## 7. 相机 shell（telnet 23，root 免密）

装机脚本无条件拉起 telnetd，这是最有价值的"逃生通道"。

### 7.1 telnet 客户端的两个必踩坑

**坑 1：IAC 协商 + 回显。** 一连上服务器就发：

```
\xff\xfd\x01   (IAC DO ECHO)
\xff\xfd\x1f   (IAC DO SGA)
\xff\xfb\x01   (IAC WILL ECHO)
\xff\xfb\x03   (IAC WILL SGA)
```

并且**开启回显**。所以原始流里既有协议字节、又有你发出去的命令回显。
过滤规则：遇到 `255`(IAC) 后读命令字节，若命令在 `0xFB..0xFE`（WILL/WONT/DO/DONT）则吞 **3** 字节，否则吞 **2** 字节。

> 曾经用"echo 标记 → 命令 → echo 标记"再数标记出现次数来切分输出，
> 结果因为**回显里也含标记**而永远匹配不上，返回一堆乱码。

**坑 2：提示符没有尾随空格。** 首个提示符是 `[root@drime5 ~]# `（带空格），
但**后续提示符是 `[root@drime5 ~]#`，空格被内核吞了**。

→ 用 `contains("# ")` 判断会永远等不到；必须用行尾正则：

```regex
(\[[^\]]*\]\s*)?#\s*$
```

并且读取要**按行缓冲**，不能因 socket 超时把读到一半的行丢掉。

### 7.2 时间设置（实测可用）

`/bin/date` 是 **GNU coreutils 6.9**（不是 busybox），支持 `date -s STRING`。

```
date -s "2026-10-14 05:51:58"     # ✅ 墙上时间，推荐
date -s @1791927968               # ⚠️ 绝对时间，会经相机时区换算
```

- 相机时区实测 **KST (UTC+9)**（`/etc/localtime` 是 396 字节 TZif，内容为首尔）
- `/usr/share/zoneinfo/Asia/Shanghai` 存在，可替换 `/etc/localtime` 改时区
- **`date -s @<epoch>` 的坑**：设的是绝对时间，显示要经时区换算，
  所以 UTC+8 的用户会看到相机**快 1 小时**。改用字符串形式后，
  相机按自己时区解释该字符串，**显示时间就等于传入字符串**。
- 相机时钟实测曾停在 **2015-02-17**（偏差约 11.6 年）

### 7.3 ⚠️ RTC 写不进去

```
hwclock -w
→ hwclock: select() to /dev/rtc to wait for clock tick timed out
→ hwclock: Could not open file with the clock adjustment parameters in it
   (/etc/adjtime) for writing: Read-only
```

→ 同步只对**当前开机周期**有效，重启后可能回退。因此每次连接都应检查并重同步。

### 7.4 其他 shell 事实

- 无 `timeout` 命令（busybox 未编入）
- 无 gcc / python / perl；busybox 无 `sendevent`
- **FTP 不提供 `/tmp` 与 `/var/log`** → 诊断输出要写 `/mnt/mmc/` 再取回
- `df -h /mnt/mmc` 实测：`/dev/mmcblk1p1  15G  3.1G  12G  21%  /opt/storage/sdcard`

---

## 8. 相机自带 `st` 调试命令

### 8.1 电量：硬件不提供百分比

```
st bat sel 1          → call set_battery(value=1) ret=-1
st bat get_soc        → call get_battery_soc(value=-1) ret=-1
st bat get_voltage    → call get_battery_voltage(value=-1) ret=-1
```

sysfs 侧：

```
/sys/class/power_supply/battery/capacity    → 1330   （原始 mAh，不是百分比）
/sys/class/power_supply/battery/status      → Unknown
（voltage_now / health / technology 均不存在）
```

→ **电量百分比在这台硬件上拿不到**，只能给档位（`battery_level` 0–4）与充电状态。

### 8.2 镜头信息：被 MCB 占用挡住

```
st cap lens info  → Cann't execute capture command over MCB!
```

`st cap lens` 有大量子命令（`general`/`info`/`status`/`getrange`/`pdget`/`sdget`/`zoom`/`focus_pos` 等），
但**相机 App 正占用 MCB 总线**，外部命令进不去。

→ **镜头信息无法从外部读取。** 这是架构限制，不是权限或命令写错。

### 8.3 可用的 `st` 命令

```
st cap capt <子命令>              # single start / conti / burst / quality / size / ois / caf ...
st cap capdtm getusr <参数名>     # 例：ADJUSTSHUTTERTYPE → UserData is ADJUSTSHUTTERTYPE_MECHA (0x750000)
st pmu show                       # 模块供电状态表
st firmware info                  # 固件信息
cat /etc/version.info             # 版本信息（实测：1.12 / NX500 / NX500GLU0APC1 / DSP_NX500GLU0APC1_SR1 / BABF43A）
```

---

## 9. 输入设备与键码（供直接写 `/dev/input` 的方案参考）

| 设备 | 节点 |
|---|---|
| Drime5 GPIO Keyboard | `/dev/input/event0` |
| Drime5 ADC Keyboard | `/dev/input/event1` |
| Melfas MMS100s Touchscreen | `/dev/input/event2` |

NX500 键码（实测）：

| 按键 | 键码 |
|---|---|
| S1（半按） | 133 |
| S2（全按） | 134 |
| MOBILE | 233 |
| EV | 181 |
| OK | 104 |
| MENU | 135 |

> 直接写 `event*` 需要对 ARM 交叉编译，本项目主机上未安装交叉编译器，故未走这条路。
> `camera_tools/evkey.c` 是为此准备的参考实现。

---

## 10. 客户端实现的注意事项汇总

1. **`battery_percent` 负数必须当未知**，否则界面显示 "-1%"
2. **字段名用下划线**：`fw_ver`、`mac_address`
3. **NV12 低清 Y 平面也减半**，并用长度做自检（`yW*yH*3/2 == len`）
4. **缩略图必须校验 JPEG magic**，因为失败返回 HTTP 200 + text/plain
5. **RAW 不要请求缩略图**（不在白名单）
6. **缩略图并发限 2**（相机端单核 `convert`）
7. **取景要复用连接**（keep-alive），否则掉帧
8. **快门必须 S1 按住 + S2 点击**
9. **别用文件数差值算照片张数**（一张写 JPG+SRW）
10. **telnet 要过滤 IAC，并用行尾正则匹配提示符**
11. **时间同步用墙上时间字符串**，不要用 `@epoch`
12. **时间同步不是一劳永逸**（RTC 写不进去），每次连接检查


---

## 11. 组网方式与"热点连不上"的坑

相机端 mod 的 `install/app/wifi.sh` 里能看到它怎么切 AP 模式：

```
$APP_PATH/externals/poker /tmp/var/run/memory/ap_setting/request_type 0x0:2900000001000000
killall yad menu.sh
```

即通过 mod 的 `poker` 工具**写相机原生 `ap-setting-app` 的内存映射**来请求 AP 模式。
所以相机做热点靠的是**原厂 AP 功能**，不是 mod 自己实现的。

### 11.1 客户端必须做的两件事（否则热点场景必失败）

| 做法 | 原因 |
|---|---|
| **收发前先把 socket / 进程绑到相机所在网络** | 手机连着"无外网的相机热点"时，系统默认路由通常仍是**蜂窝**。不绑网络就发 UDP 广播 → 从蜂窝网卡出去，相机根本收不到；HTTP 请求也一样超时 |
| **持有 `WifiManager.MulticastLock`** | Android 的 Wi-Fi 固件会过滤广播/组播帧。不持锁时收广播的成功率极低（息屏后更差） |

对应权限：`CHANGE_WIFI_MULTICAST_STATE`（组播锁必需）、`NEARBY_WIFI_DEVICES`（Android 13+）。

### 11.2 发现流程的正确顺序

```
for 每个候选网络（Wi-Fi 优先，无外网的优先）:
    bindProcessToNetwork(该网络)          <- 必须先绑
    在该网络上建 UDP socket 并 bindSocket  <- 让收发同路
    在该网络上做 UDP 广播发现
    在该网络上做 HTTP 地址扫描
```

**旧实现把 UDP 广播放在了绑定循环之前**，这正是"相机/手机做热点都连不上"的根因。

### 11.3 不要猜 IP

相机的地址由**对端的 DHCP 池**决定，猜不得。实测反例：

- 相机接家用路由器 → `192.168.5.39`（网关 `192.168.5.1`），不在任何"常见主机位"列表里
- 手机开热点 → 相机是 DHCP 客户端，通常落在 `192.168.43.x`，但具体值随机

所以 App 的兜底手段是**整段 /24 扫描**（64 并发，约 4 秒），只在前面几轮都没命中时才用。

### 11.4 telnet / FTP 的实际配置（来自 `sd_install/remote/inetd.conf`）

```
21	stream	tcp	nowait	root	ftpd	ftpd -w /mnt/mmc/
23	stream	tcp	nowait	root	telnet	telnetd -i -l /bin/bash
```

- telnet：root **免密**，shell 是 `/bin/bash`
- FTP：root 免密，**根目录是 `/mnt/mmc/`**（SD 卡）—— 这解释了为什么 `/tmp`、`/var/log` 取不回文件

---

## 12. B 门（长时间曝光）

### 12.1 协议层没有 B 门接口

- `nx-remote-controller-mod` 源码里**完全没有 bulb 相关代码**（`web_root/js/` 下 grep 无结果）
- daemon 的 API 只有 `/api/v1/shutter/normal` 与 `/shutter/silent`，二者都只改快门**类型**，不涉及曝光时长
- 相机自带的 `st` 命令里**有** `st cap capt bulb`，但 `st cap lens` 实测已被 MCB 占用挡住
  （`Cann't execute capture command over MCB!`），`st cap capt bulb` 能否成功**尚未实测**

### 12.2 App 侧的可行做法：模式拨盘 + 按住 S2

NX500 的模式拨盘有 **B 档**。拨到 B 后，**全按持续期间快门保持开启**，松开即结束曝光。

所以 App 只需把"点一下 S2"改成"按住 S2"：

```
keyDown(S1) -> 等 450ms -> keyDown(S2) -> [保持，等用户点结束] -> keyUp(S2) -> keyUp(S1)
```

两个要点：

1. **松键顺序必须先 S2 后 S1**。反过来会先退出半按，相机在半按释放时就结束曝光，
   长曝光时间不受控。
2. **模式拨盘是物理控件，App 无法代劳**。没拨到 B 的话，长按 S2 只会拍一张普通照片。

> **待实测**：B 档下"按住 S2"是否真能让快门持续开启。这一条尚未在真机上验证过
> （相机当前不可达），实现依据的是机械两段式开关的语义 + 普通拍摄已实测的 S1/S2 重叠关系。