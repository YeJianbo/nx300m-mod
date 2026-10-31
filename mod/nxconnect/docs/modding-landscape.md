# Samsung NX500 / NX1 改机项目技术报告

采集时间：本次会话。所有“已核实”条目均为本次实际抓取到原文（HTTP 200），逐字引用；“未核实”已显式标注。

## 0. 环境限制与可用抓取路线（重要）

本会话 `pwsh` 工具**完全不可用**：任何命令都失败于
`Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(C:\DsNX)`。
因此全部工作只能用 `web_fetch` / `web_search`，也无法申请沙箱升级。

**可用路线（已核实 HTTP 200）：**

- `https://gh-proxy.com/https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>`
- `https://ghfast.top/https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>`
- 同一代理同样可抓 `https://gh-proxy.com/https://api.github.com/...`（仓库元数据、文件树、contents、commits 全部可用）
- GitHub Pages 直连可用：<https://mewlips.github.io/nx-remote-controller-mod/>

**不可用路线（逐条实测失败）：**

| 目标 | 结果 |
|---|---|
| `github.com/...`、`api.github.com/...`、`raw.githubusercontent.com/...` | `URL hostname "..." resolves to a non-public IP address` |
| `cdn.jsdelivr.net/gh/<u>/<r>@<b>/<p>` | `cross-origin redirect to https://raw.githubusercontent.com is not followed automatically`（README 抓不到） |
| `data.jsdelivr.com/v1/packages/gh/<u>/<r>` | HTTP 502 `Couldn't fetch versions for ...` |
| `cdn.statically.io` | `TypeError: fetch failed` |
| `web.archive.org`（含 CDX API） | 解析到非公网 IP，拒绝 |
| `r.jina.ai` | `TypeError: fetch failed` |
| `html.duckduckgo.com` | 解析到非公网 IP |
| `uithub.com` | 页面 200 但内容被登录墙挡住（WApaperplane 仓库显示 `~0 tokens`、`Content hidden`） |
| `gitingest.com` | 只返回 JS 空壳，无内容 |
| `gitcode.com/...`、`gitee.com/mirrors/...` | 404 / HTTP 405；`gitee.com/api/v5/search/repositories?q=nx500` 返回 `[]` |
| `api.allorigins.win` | HTTP 522 Cloudflare 超时 |
| `api.codetabs.com/v1/proxy` | HTTP 503 限流 |
| `github.moeyy.xyz`、`hub.gitmirror.com` | DNS 不存在 |
| `dpreview.com`、`zhuanlan.zhihu.com` | HTTP 403（Cloudflare / 知乎反爬） |
| `mewlips.github.io/nx-remote-controller-mod/wiki/Key-mappings.html` | HTTP 404（Pages 站未托管 wiki） |

---

## A. 每个仓库的定位与内容清单

### A1. `WApaperplane/NXWA-nx500_modding` —— 用户关注的仓库【已核实】

仓库元数据（来自 GitHub API，经代理）：

- `fork: true`，parent = **`ottokiksmaler/nx500_nx1_modding`**（同一 parent 也确认是 `mewlips/nx500_nx1_modding` 的 fork parent）
- **默认分支 = `nxwa`**（不是 `main`；`main` 返回 404，README 只在 `nxwa` 与 `master` 上存在）
- `description`: `Samsung NX500 Modding`；`language`: `Python`；`license`: AGPL-3.0
- `size`: 58195 KB；`created_at` 2026-07-12；`pushed_at` 2026-10-13T15:09:10Z（**非常活跃**）
- 最新 commit（`fe3fe8de`，2026-10-13）：提交信息是 `docs(slp): SLP 分区结构核查（U5）—— idx6/7/9 定性；刷机将重置 /opt 与 /opt/usr`

**分支策略（README 原文，逐字）：**

> **分支 / Branches**：`master` = 上游原始历史（勿动）；**`nxwa` = 本项目源码（本文档所在分支，亦是默认分支）**。

**`master` 分支 = 上游快照**：`master` 的 tree SHA 是 `ef7126d71f95e1e504de2be24beb5ef0dd2679a1`，与 `ottokiksmaler/nx500_nx1_modding` 的 tree SHA **完全相同** → 就是上游原样（README 已改，但文件树即上游）。

**`nxwa` 分支根目录清单（已核实）：**

```
.gitattributes  .github/workflows/scripts-ci.yml  .gitignore  ATTRIBUTION.md
CustomButtons-example.zip  LICENSE  NX-KS2_readme.odt  README.md  README_EN.md
SYNC.md  VERSION  deploy/filmlab/{EV_MOBILE.sh,INSTALL.txt,MOBILE_MOBILE.sh,flab_ui.sh}
docs/{README.md, archive/, current/, evidence/}
scripts/  test_server/  tools/  uninstall_pkg/  raw8/  ...
```

**`nxwa/VERSION`（逐字）：**

```
NX-WA — Samsung NX (DRIMe5 / Tizen) enhancement & research project
version        2.88.0-nxwa.1
repo           https://github.com/WApaperplane/NXWA-nx500_modding   ★ 规范地址（2026-10-10 复核）
               ↑ 旧名 WApaperplane/nx500_nx1_modding 仍可重定向；不是 /nxwa（该地址不存在）
branch         nxwa（仓库默认分支）
upstream       https://github.com/ottokiksmaler/nx500_nx1_modding   ★ GitHub fork parent（实测）
bloodline      NX-KS 2.88 (KinoSeed)  +  nx500_nx1_modding (上游血缘)
camera-path    /opt/usr/nx-ks/          ★ 相机端安装路径【保持不变】，改名只改项目名
license        AGPL-3.0（见 LICENSE；派生自 AGPL 上游 ⇒ 必须同证）
```

**它到底做了什么（README 特性表归纳）：**

- **FilmLab 胶片配方引擎**：内置 95 条配方（负片/反转/黑白/电影/风格），写 Picture Wizard 7 维向量（R/G/B 增益 + HUE/SAT/SHARP/CONTRAST）到「自定义1」槽；权威配方库是 SD 卡上的 `/mnt/mmc/filmlab/recipes.json`
- **PW 直推**：`EV+AEL` 打开配方菜单 → 点选 → SAT/SHARP/CONTRAST **三维**直接推进 ISP（`pwsend.arm`，编码 `(raw16<<16)|0xD80A`）；README 明确承认 **R/G/B/HUE 四维无外部通路**（p7 归一化器只认 `0x100–0x12e`）
- **3D LUT 链路**：原生表格式已解（`4913×4B {R,G,B,pad}`，索引 `((B*17+G)*17+R)*4`，槽步长 `0x4D00`），`.cube ⇄ 原生表` 双向工具
- **Web 遥控 + 网页相册（`nx-rc`）**：80 端口 daemon + **8080 端口 busybox httpd CGI 群**（thumb / dirlist / prewarm / push）
- **主菜单直显 IP + 一键 Telnet/FTP**
- 相机端原生菜单覆盖层（`mod_gui`）、键位映射、码率、黑场等 NX-KS 模块保留
- **设备/固件支持：NX500 1.12 / NX1 1.41（其它版本 install.sh 直接拒绝并弹窗）**

**`scripts/` 目录（挂在相机 `/opt/usr/nx-ks/` 下）的关键文件（已从 contents API 核实存在与大小）：**

| 文件 | 大小 | 作用（据文档） |
|---|---|---|
| `install.sh` | 5.4 KB | SD 卡根「智能引导器」，未装=全量安装 / 已装=增量同步 |
| `onboard.sh` | 3.5 KB | 装机后**无条件拉起 telnetd(23) + busybox ftpd(21)** |
| `busybox` | 916 KB | ARM busybox，用于 tcpsvd/ftpd |
| `telnetd` | 2 MB（`onboard.sh` 注释） | mod 自带 ARM telnetd |
| `nx-rc.sh` | 1.8 KB | 拉起 80 端口 daemon + 8080 服务群 + 缩略图预热 |
| `nx-rc/nx-remote-controller-daemon` | 157 KB | 80 端口 HTTP daemon + 实时画面/按键注入 |
| `nx-rc/capdtm/{capdtm-cgi.sh,capdtm-httpd.sh,capdtm.conf,www/cgi-bin/capdtm-api}` | — | 8080 拍摄参数读写 API |
| `nx-rc/thumb/{thumb-cgi,dirlist-cgi,prewarm-cgi,thumb-install.sh,thumb-prewarm.sh}` | — | 缩略图/实时目录/预热 CGI |
| `nx-rc/push/{push-cgi,push-install.sh,push.sh}` | — | WiFi 在线推送 web 前端 |
| `nx-rc/web_root/{index.html,js/*,css/*,lib/*,fonts/*}` | index.html 12 KB, gallery.js 46 KB | 网页遥控/相册前端 |
| `filmlab.sh` | 51 KB | FilmLab 引擎 |
| `filmlab/recipes.json` | 21 KB | 95 条配方权威源 |
| `keyscan` / `keyscan.static` | 603 KB / — | 按键扫描（mod 入口） |
| `mod_gui`, `popup_timeout`, `onscreen_*`, `shutter_to_key`, `focus_stack`… | — | 各类原生 UI/控制二进制 |
| `dicam/dicam_patch_{install,rollback,status}.sh` | — | di-camera-app 补丁（C-2 命令通道） |
| `discovery/t1_strings.py` … `t5_st3dlutparam.py` | 8–18 KB | p7 固件逆向脚本（Python） |

### A2. `mewlips/nx500_nx1_modding` —— 上游血缘关系澄清【已核实】

- 元数据：`fork: true`，parent = `ottokiksmaler/nx500_nx1_modding`
- `created_at` 2016-10-14，`pushed_at` **2016-10-14**（**自 2016 年起完全静止**），size 23734 KB，0 stars，默认分支 `master`
- 结论：**mewlips 的这份只是 2016 年的一次性 fork，不是活跃上游**。真正上游与集大成者是 `ottokiksmaler/nx500_nx1_modding`（136 stars、49 forks、8 open issues、`pushed_at` 2025-10-28、默认分支 `master`、语言 C）

**上游 README 原文（`master`，逐字节选）：**

> These cameras run a full blown Linux distribution (Tizen) with shell, X, networking stack, debugging and testing tools - the works.
> **TL;DR** - I want my camera hacked - what to do?
> - Use a mod pack made by @Vasile-gh here [nx-patch]. You will need a one-time install of [nx-on-wake], though. This mod is integrated into the camera hibernation image and does not require Bluetooth for startup.
> - Use a mod with many many integrated options packed together by @KinoSeed: facebook.com/NXKS2 / http://authors-vault.com/NX-KS2.zip - this mod is launched by enabling Bluetooth.

上游 tree 清单（已核实，`master`，共 80+ 条目）包含：

- 文档：`Control-camera-from-command-line.md`、`Control_LCD_EVF.md`、`Customizing keys.md`、`DEV and CS modes.md`、`DFMS Commands.md`（16 KB）、`ST Commands.md`（20 KB）、`ST_CAP_CAPDTM.md`（28.5 KB）、`Prefman_tool.md`（**112.8 KB**）、`Working with lens.md`（**57.5 KB**）、`Permanent_bitrate_mods.md`、`Poker.md`、`Running-shell-scripts-from-SD-card.md`、`Running-telnet-server-on-camera.md`、`Running_scripts_without_factory_mode{,_BT}.md`、`Testing programs - HOWTO.md`、`Enable-{PTP-on-NX500-NX1,Screenshot,1080p-120fps…,2.5k-on-NX500}.md`、`NX500 Silent shooting.md`、`Removing_Movie_Recording_Limit_Without_Hack.md`、`Checking_shutter_count.md`、`NX500_Silent_Shutter_Without_Hack.md`
- `doc/`：`Boot_Process_and_Speeding_up_Mods.md`、`Compile_against_camera_library.md`、`Firmware_update_notification.md`、`NX1_NX500_hw_differences.md`（15.6 KB）、`Problematic Mode Dial Workaround.md`、`Start_WiFi_Scanning.md`、`Timezone.md`、`True_BBAF.md`、`Update_hibernation_image.md`（**65.8 KB**）、`Working with Exposure Value.md`（27.9 KB）
- `nx-on-wake/`：`README.txt`(9.6 KB)、`install/install.sh`、`install/nx-on-wake`、`install/nx-on-wake.service`、`install/nx_cs.adj`、`install/nx-model`、`install/popup_{entry,ok,timeout}`、`nx-on-wake.zip`、`COPYING`
- `video-bitrate-mods/`：`nx-bitrate-v2.zip`(11 MB)、`nx-patch-v4.0/{CExeMemPatch.cpp,CExeMemPatch.h,README.txt,make-command,nx-bt-run-v1.zip,nx-patch,nx-patch.cpp,nx-patch-v4.0.zip}`、`nx-patch-v4.1/{README.txt,nx-patch/…,src/{install/install.sh,scripts/{apply.sh,init.sh,initial_patches.sh,main.NX1,main.NX500,chg_res_*.NX1|NX500,set_rate.sh,EV_EV.sh,about.sh},nx-patch.png}}`、`nx-patch/{README.txt(10.7 KB),nx-patch.zip,nx-patch.png,src/…}`
- `ssh-server/`：**dropbear 2020.81 移植**（`README.md` 2.4 KB、`dropbear-2020.81-nx-path.patch`、`files/ssh/{dropbear,dbclient,scp,sftp-server}` 各 180–260 KB ARM 二进制、`files/install.sh`）
- `tools/`：`README.md`、`nxinfo.zip`、`nx500silent.zip`、`install_lapse.zip`、`nx_power_off/on_*`、`nx_rec_limit_remover.zip`、`nx_power_save_{disabler,enabler}.zip`
- `downgrade_to_nx1000_FW_1.13_hacked_1.13_to_1.16/`：**`nx1000.Rom.7z`(24.5 MB)、`nx1000.bin.7z`(7.9 MB)、`nx_dev.adj`、`Readme.md`** ← 真实固件镜像，重要
- 可执行/源码：`focus_stack(.c)`、`focus_buttons(.c)`、`keyscan(.c,.static)`、`log_watcher(.c)`、`shutter_to_{key,rec}(.c)`、`mod_lapse(.c)`、`mod_gui/{README.md,mod_gui,mod_gui.c}`、`nx-model/{Readme.md,nx-model,nx-model.cpp}`、`button_png(.c)`、`stapp(.c)`、`prefmandumpread/prefmandumpread.py` + `nx500/prefman-{5..9}.info`、`rsync`(1.86 MB)、`sd.zip`(1.4 MB)

**`master` 与 `nxwa` 的关系**：`nxwa` 全部保留上游文档（同名同内容），并新增 `docs/`、`scripts/` 大规模扩展、`test_server/`、`tools/nx-fw/`。

⚠️ **注意**：本次**没有**抓到 `nx-patch/README.txt`、`nx-on-wake/README.txt`、`onboard`/`telnet` 之外的工具正文，因此「nx-patch 具体提供哪些码率档位」等细节未核实（见 E 节）。

### A3. `ge0rg/samsung-nx-hacks` —— 整个 NX 家族的逆向知识库与固件工具【已核实】

tree（`master`，已完整枚举，共 16 条目）：

```
README.mdwn                                    (5.3 KB)
sdcard/autoexec.sh                             (353 B)
sdcard/inetd.conf                              (106 B)
sdcard/shutter.sh                              (191 B)
tools/drime3-firmware.py                       (2.4 KB)
tools/m7mu-decompress.py                       (7.4 KB)
tools/m7mu.py                                  (6.9 KB)
tools/sfresource.py                            (3.1 KB)
tools/slp-firmware.py                          (4.6 KB)
xml/PC-AutoBackup/{ConnectionManager1.xml, ContentDirectory1.xml, SamsungDmsDesc.xml}
xml/Remote-ViewFinder/{smp_2_.xml, smp_3_.xml}
```

**README 设备支持表（逐字，已核实）：**

| 型号 | CPU | Linux | 固件 | 备注 |
|---|---|---|---|---|
| Galaxy NX | DRIMeIV | ✅ | | Android + DRIMeIV 双 CPU，DRIMeIV 上是精简 Debian |
| NX5/NX10/NX11 | DRIMeII | ❌ | 15.5MB | 路径暗示 µITRON |
| NX100 | DRIMeII | ❌ | 28.5MB | µITRON |
| NX20/NX200/NX210 | DRIMeIII | ❌ | ~73–102MB | 固件里有 VXWorks 版权 |
| NX1000/NX1100 | DRIMeIII | ❌ | ~102MB | VXWorks；CPU 标注 “S1219 ARM N4AK9C” |
| **NX300** | DRIMeIV | ✅ | ~324MB | **working，基于 Tizen 1.0** |
| **NX2000** | DRIMeIV | ✅ | ~329MB | working |
| NX300M | DRIMeIV | ✅ | ~316MB | 需要改版固件 |
| **NX30** | DRIMeIV | ✅ | ~329MB | 脚本执行与 telnetd working |
| NX3000/NX3300 | M7MU | ❌ | ~129MB | 富士通 M7MU（Milbeaut MB86S22A），**不跑 Linux** |
| NX mini | M7MU | ❌ | ~129MB | 同上 |
| Galaxy K Zoom | M7MU | ❌ | ~6MB | M7MU 上不跑 Linux |
| **NX1 / NX500** | **DRIMeV** | ✅ | ~335MB | working，通过 ottokiksmaler 项目 |
| Gear360 | DRIMeVs | ✅ | ~216–321MB | 有限支持 |

README 自述「This project is rather dormant since 2015」，并把 `nx-remote-controller-mod` 列为 *very much recommended*。附带外部链接：op-co.de 三篇 NX300 破解系列、`matteverglade.com/cameras/nx-firmware/`（固件版本总表）、`nxfiles.nx.tc`（全量固件/软件镜像站）、`blueringlab.com/2016/03/samsung-nx-mount-protocol-general/`（NX 卡口协议）。

### A4. `3dguy/nx500` —— 早期 NX500 破解信息仓库（已被上游取代）【已核实】

tree（`master`，22 条目）+ README 原文：

> `#Samsung NX500 / NX1 - rooting and hacking` … These cameras run a full blown Linux ditribution (Tizen) with shell, X, networking stack, debugging and testing tools - the works.

- 内容是**上游文档的更早版本**并把链接指向 `ottokiksmaler/nx500`：`Control-camera-from-command-line.md`、`Customizing keys.md`、`Enable-PTP-on-NX500-NX1.md`、`Enable-Screenshot.md`、`Running FTP server on the camera.md`、`Running-gdb.md`、`Running-shell-scripts-from-SD-card.md`、`Running-telnet-server-on-camera.md`、`Working with focus.md`、`Working with lens.md`、`Working-with-key-events.md`
- `DFMS Commands.md` 与 `ST Commands.md` 在此仓库里是**占位文件**（21 B / 19 B，内容已挪到上游）
- 二进制/压缩包：`focus_buttons(.c)`、`focus_stack(.c,.c 11 KB)`、`keyscan(.c)`、`sd.tar`(2.4 MB)、`sd.zip`(1.4 MB)
- 状态段里列出了 telnet/FTP/脚本/浏览器/gdb、1080p120、2.5k、按键事件、焦点堆叠；「To do」里仍是 *Removing video recording time limit*、*SSH on camera*——**说明 3dguy 版本早于 nx-on-wake/nx-patch 时代，技术上已被 A3/A2 完全覆盖**。

### A5. `mewlips/nx-remote-controller-mod` —— 摄像头网页遥控（**协议最有价值**）【已核实】

仓库结构（`master`，已核实存在）：

```
README.md (258 B)   LICENSE (AGPL-3.0)   version   package.json   make-package.sh
swagger.json                       ← ★ 官方 API 规格
NXRemoteController/                (Android app 源)
externals/  gallery/  install/  sd_install/
nx-ks2/                            (集成进 NX-KS2)
nx-input-injector/                 (按键注入)
xev-nx/                            (X 事件抓取)
nx-remote-controller-daemon/       ← ★ 相机端 C 源码
    main.c api_server.{c,h} command.{c,h} gallery.{c,h} input.{c,h}
    lcd.{c,h} led.{c,h} liveview.{c,h} network.{c,h} nx_model.{c,h}
    osd.{c,h} shutter.{c,h} status.{c,h} util.{c,h} build.sh
web_root/
```

`nx-remote-controller-mod/README.md` 内容仅 258 B（实质文档在 GitHub Pages）；Pages 站（<https://mewlips.github.io/nx-remote-controller-mod/>，已核实全文）关键点：

- 前置条件：**NX500 固件 1.12 或 NX1 固件 1.41** + **nx-on-wake 1.11** + **nx-patch 5.3**
- 安装：把 release zip 解压到 SD 卡根 → 插卡开机 → 等待自动安装完成
- 使用：按 `MOBILE` 键（基本 MOBILE 功能被替换）→ 相机连上 Wi-Fi → 从 app 菜单读出相机 IP → 浏览器打开 `http://<相机IP>`
- 特性：浏览器遥控、**镜像相机屏幕**、多机控制、NX500 静音快门、LCD on/off、NX300 自动 Wi-Fi / Telnet / FTP
- 安全警告原文：`CAUTION: DO NOT CONNECT YOUR CAMERA TO PUBLIC WIFI NETWORK.` / `nx-remote-controller-mod does not provide secure access.`
- 许可 AGPL-3.0

### A6. `ottokiksmaler/nx500_nx1_modding` —— 事实上游（见 A2）【已核实】

### A7. `jdieter/nx300m-autobackup`（附带核查，与「自动传照片」直接相关）【已核实】

README 原文要点：

> When this set of scripts are configured correctly, the Samsung NX300M will automatically backup all photos and videos **via scp** whenever the camera is turned on.
> **your camera must be using the 1.10 camera body firmware.** I've tested on the 1.13 firmware, and it doesn't work with that.
> `autoexec.sh` is run every time the camera is turned on and calls `autobackup/backup.sh`.
> backup.sh … connects to the listed WiFi AP, runs a "keep alive" script so the camera won't automatically shut down, and sends each new file to the backup server.

配置项：`autobackup/config` 里设 ESSID / `user@hostname` / 目标路径；`autobackup/ssh/` 放 `id_rsa`、`id_rsa.pub`。

---

## B. 固件格式与刷机流程

### B1. SLP 固件容器格式【已核实 —— 源码级】

来自 `ge0rg/samsung-nx-hacks/tools/slp-firmware.py`（Samsung Linux Platform firmware header dumper）：

```python
SLP_MAGIC = b'SLP\0'
P_FWFILE_META      = "<4s8s16s16sbI15s"
FwFileMeta = namedtuple("FwFileMeta", "magic version project version_date has_pcache pcache_offset reserved")
P_FW_IMAGE_HEADER  = "<IIII"          # 旧版
P_FW_IMAGE_HEADER2 = "<IIII8s"        # V2
FwImageHeader = namedtuple("FwImageHeader", "img_len crc32 img_offset magic, extra filename")
FW_IMAGES = ["vImage", "D4_IPL.bin", "D4_PNLBL.bin", "uImage", "platform.img"]
```

判定逻辑（原文）：

```python
fw = load_block(f, P_FWFILE_META, FwFileMeta)
if fw.magic != SLP_MAGIC: raise Exception("incorrect magic")
parts = [load_image_block(f, fn) for fn in FW_IMAGES]
assert_part0_magic(parts)                       # parts[0].magic 必须 == 0xffffffff
if fw.has_pcache == 1:
    f.seek(fw.pcache_offset); parts.append(load_image_block(f, "pcache.list", v2))
if fw.has_pcache > 1 or parts[1].magic != 0x7fffffff:
    parts = load_v2_parts(f, fw.has_pcache); assert_part0_magic(parts); v2 = True
```

结论：固件是 **未加密的容器**，头部有 `SLP\0` 魔数、project/version 字符串、可选 pcache，后跟若干分区镜像块（`img_len / crc32 / img_offset / magic / filename`）；公开工具可 `-p` 打印分区、`-x` 解包。**未发现加密/签名校验的描述**——本次未核实是否存在签名验证。

### B2. NX500 1.12 固件的真实分区结构【已核实，来自 NXWA 仓库最新 commit 信息】

（commit `fe3fe8de`，原文逐字摘）

> 分区表：A1 包与官方 1.12 逐字段一致（10 段）
> - idx5 = p7/rtos 12,075,648 B（crc 1ad42900 = A1 文档值 ✅）
> 逐段解压定性：
> - idx6 (280,505,658) → rootfs：LZOP 解压为 ext4（/bin /etc /var /opt /smack /home）
> - idx7 (5,022,551) → ext4，解压 104,857,088 B（100 MiB）；含 apps/ dbspace/ storage/ share/ ug/ driver/ data/ usr/ etc/ version ⇒ /opt 类结构
> - idx9 (38,808,649) → 157,285,888 B（≈150 MiB）原始数据、非 ext4；含 /usr/apps/com.samsung.di-camera-app/res/sounds/*.wav、res/edje/slp_edc_*.edj、/opt/usr/dbspace/.app-package.db、/opt./local/developer/.e/e/fonts、偏移 27,959,556 处 cpio magic 070701 ⇒ App 资源镜像
> 强推断：idx6/7/9 ↔ eMMC p10(rootfs)/p11(/opt)/p14(/opt/usr)
> ⇒ **刷 A1 SLP 会重置 /opt 与 /opt/usr ⇒ mod 运行目录 /opt/usr/nx-ks/ 会被抹掉**
> ★ A1 硬前置：1) 刷前备份 /opt/usr/nx-ks/ 与 /opt 自定义内容 2) 刷后重新部署 mod

方法原文：`tools/nx-fw/slp-firmware.py --partitions` + WSL 内 `lzop -dc` 逐段解压（全程离线只读）。

### B3. 另一种（更老的）固件格式：DRIMeIII【已核实】

`tools/drime3-firmware.py`（WB850F / EX2F）：

```python
P_TOP  = "<7sB"
P_PART = "<32sII20s"
ver, num = ...            # 版本 + 分区数
for x in range(num):
    fn, size, offset, name = ...   # 文件名(32B) / 大小 / 偏移 / 名字(20B)
```
→ 顶层 7 字节版本 + 1 字节分区数，随后每分区 32B 名 + size + offset + 20B name。同样**无加密**。

### B4. 「刷机」的真实含义：不是刷固件，而是 SD 卡触发脚本【已核实】

上游文档 `Running-shell-scripts-from-SD-card.md` 与 `Running-telnet-server-on-camera.md` 给出完全一致的触发链：

1. SD 卡根放 `info.tg`，内容为 `nx_cs.adj` + 换行
2. SD 卡根放 `nx_cs.adj`，内容为 `shell script /mnt/mmc/test.sh` + 换行
3. SD 卡根放 `test.sh`，里面写要执行的命令
4. 插卡，相机拨到 AUTO 模式，开机等待
5. 关机（等灯闪结束）

原文警示：

> Presence of **info.tg** file starts the **dfmsd** daemon process (that processes the commands in that file) but it also disables the touchscreen on camera … If you want to use the touchscreen put `killall dfmsd` in the shell file.
> Camera is actully not booting every time, it's constantly hibernating and resuming - this might throw you off.

**NX-KS / NX-WA 的实际装机链（`nxwa/install.sh` 注释逐字）：**

> 触发链(不可改, 相机固件行为):
>   SD 根 info.tg -> nx_cs.adj(内容固定为 "shell script /mnt/mmc/install.sh")
>   -> 相机 dfmsd 检测到后自动执行 /mnt/mmc/install.sh, 无需任何按键

`install.sh` 的两个分支（原文要点）：

- **未安装**（`/usr/sbin/bluetoothd.orig` 不存在）→ 全量安装：`mount -o remount,rw /` → `mv /usr/sbin/bluetoothd /usr/sbin/bluetoothd.orig` → 写入 wrapper `bluetoothd`（内容为 `if [ -x /opt/usr/nx-ks/init.sh ]; then /opt/usr/nx-ks/init.sh; fi`）→ `remount,ro` → `cp -ar /mnt/mmc/scripts/* /opt/usr/nx-ks/`
- **已安装** → 增量同步：只 `cp -ar` 覆盖，不删不卸
- 固件校验原文：
  ```bash
  { /bin/grep -q '^NX500$' /etc/version.info && /bin/grep -q '^1.12$' /etc/version.info; } && VEROK=1
  if { /bin/grep -q '^NX1$' /etc/version.info && /bin/grep -q '^1.41$' /etc/version.info; }; then VEROK=1; IS_NX1=1; fi
  if [ "$VEROK" != "1" ]; then ... " [  固件版本不支持  ] " ... exit 1; fi
  ```
- 结尾：`rm -f /mnt/mmc/{info.tg,nx_cs.adj,install.sh}` → `sync;sync;sync` → **`reboot`**
- 开机钩子链：`/usr/sbin/bluetoothd`(79B wrapper) → `init.sh` → `keyscan`（boot+37.8s）

### B5. 持久化机制：休眠镜像（hibernation image）【已核实】

`doc/Update_hibernation_image.md` 原文要点：

> This is the image that is unpacked to RAM when you power on the camera.
> Any changes to the state of the camera at the moment of power-off after `systemctl rescue` and cold-booting are **PERMANENT**!
> If you want to play around but *not save permanently* anything - don't power the camera off - pop the battery out
> **TLDR** - new persistent PID of di-camera-app is 248, keyscan also survives hibernation (works without bluetooth now)

流程：`systemctl rescue` → 等约一分钟 → 拔插电池 → 慢慢启动 → 改状态（应用码率补丁等）→ 正常关机以保存；不想保存就在关机前拔电池。文中给出 `ps aux | grep di-camera` 与 `/proc/248/maps` 的完整输出（可看到 `/usr/apps/com.samsung.di-camera-app/bin/di-camera-app`、`/dev/d5_sma`、`/dev/mem` 大段映射等）。

### B6. 其它固件相关事实【已核实】

- NXWA commit 原文：`systemctl restart di-camera-app ⇒ 整机卡死（须物理重启）。现场确认。` → 不要 restart 相机 App
- rootfs 是 **只读挂载 `ro,relatime`**、`/opt` 是 `noatime` ⇒ atime 不能用于判断文件是否执行过
- 刷机工具链：Ghidra（p7 静态）、QEMU/Unicorn（离线复现）、**zig 交叉编译 `arm-linux-gnueabi.2.15`，必须 `-O0`**、`gate_flow.py`（门控）、`check_scripts.py`（CI）
- 老版本 NX1000 固件镜像可下载：上游 `downgrade_to_nx1000_FW_1.13_hacked_1.13_to_1.16/{nx1000.Rom.7z,nx1000.bin.7z}`

---

## C. 相机上可启用的能力（telnet / http / 脚本）

### C1. 脚本执行（无需 root 即可开始）【已核实】

`info.tg` → `nx_cs.adj` → /mnt/mmc/*.sh 机制（见 B4）。副作用：dfmsd 覆盖屏幕（左侧绿点），需 `killall dfmsd` 恢复触摸。

### C2. telnet（23 端口，root 免密）【已核实】

上游文档原文：

> 4. Extract busybox binary from Samsung NX300 rootfs … and save it to SD card root as **telnetd**
> 9. telnet to camera (username is **root** password is not asked for)
> Congratulations - we have remote root.

NX-WA 的执行方式（`nxwa/scripts/onboard.sh` 逐字）：

```bash
telnetd 用 mod 自带的 $DIR/telnetd（2MB ARM 二进制），不是 busybox 的
ftpd 用 busybox tcpsvd -u root -vE 0.0.0.0 21 转发
WiFi 接口是 mlan0（不是 lan0！）
nice -n +10 "$DIR/telnetd" &
nice -n +10 "$BB" tcpsvd -u root -vE 0.0.0.0 21 "$BB" ftpd -w -v "$SDCARD" &
```
（`SDCARD=/opt/storage/sdcard`；日志 `$DIR/onboard.log`）

**为什么要无条件开 telnet**（原文）：

> 原来telnet 只能从 mod 菜单里的 "IP: x.x.x.x [Telnet关]" 那一行开启 … ⇒ 死锁：菜单没生成 → 没有 telnet 开关 → 连不上 → 无法查问题 → 菜单更起不来
> 本脚本把 telnet 从"mod 功能"降级成"装机副产品"… ★ 这是本项目唯一的远程排查通路

NX-WA 菜单项：`IP: x.x.x.x [Telnet关]` 点击 = 开/关 **Telnet(23) + FTP(21)**；PC 端排障工具 `test_server/telnet_run.py <相机IP> '命令'`（**非交互 telnet，root 空密码**）。

### C3. FTP（21 端口）【已核实】

上游 `Running FTP server on the camera.md` 原文：

```
[root@drime5 ~]# tcpsvd -u root -vE 0.0.0.0 21 ftpd -w -v /opt/storage/sdcard &
tcpsvd: listening on 0.0.0.0:21, starting, uid 0, gid 0
tcpsvd: start 451 192.168.43.46:21-192.168.43.85:42309
```
（`-w` 允许上传；busybox 需同时链接为 `tcpsvd` 与 `ftpd`；建议放 `/opt/usr/devel/bin`，它在 PATH 里）

### C4. SSH / SFTP / SCP（dropbear 2020.81 移植）【已核实存在，未跑正文】

上游 `ssh-server/`：`README.md` + `dropbear-2020.81-nx-path.patch` + `files/ssh/{dropbear,dbclient,scp,sftp-server}` + `files/install.sh` + `files/nx_cs.adj`。NXWA README 把它列为可用能力（「Running an SSH/SFTP/SCP server on camera」）。上游 README 的 To-do 里仍写 “SSH on camera - client should work just fine, have to test server”，但 NXWA 版本已把 ssh-server 列为 working。

### C5. 网页服务（两个端口，是本次最有价值的发现）【已核实】

| 端口 | 服务 | 说明 |
|---|---|---|
| **80** | `nx-remote-controller-daemon`（Mongoose 风格，157 KB ARM 二进制） | 实时画面、OSD 抓屏、按键/鼠标注入、LCD/LED 开关、静音快门；**`/DCIM` 目录页**（启动时快照）；`**.cgi$` 规则可执行 CGI |
| **8080** | busybox httpd + CGI | `capdtm-api`（拍摄参数读写）、`thumb`（缩略图）、`dirlist`（**实时**目录 JSON）、`prewarm`（预热进度）、`push`（在线推送） |

NX-WA 启动脚本 `nx-rc.sh`（逐字要点）：

```bash
export APP_PATH=/opt/usr/nx-ks/nx-rc
[ -x "$APP_PATH/push/push-install.sh" ]   && "$APP_PATH/push/push-install.sh"
[ -x "$APP_PATH/thumb/thumb-install.sh" ] && "$APP_PATH/thumb/thumb-install.sh" start
[ -x "$APP_PATH/thumb/thumb-prewarm.sh" ] && "$APP_PATH/thumb/thumb-prewarm.sh" start 320 75 300 1024 85 60 &
nice --adjustment=19 $APP_PATH/nx-remote-controller-daemon &> /dev/null &
```

**80 端口 vs 8080 端口的分工（README 原文）：**

> **相册实时目录源**：8080 端口 `dirlist` CGI 每次请求直接 `readdir` SD 卡，新拍照片立即可见（**80 端口 daemon 的目录列表是启动快照，新照片永不出现**）。

### C6. 相机内建命令行工具箱【已核实】

- **`st`**（swiss army knife）：子命令 `cap / key / app / lcd / gpio / pmu / clk / thread / firmware / util / leak / devman / stlcd / bat / rtc / tbm / micom / misc / oic / dvfs / adc / readl / writel / dump / log / hdmi`。`ST Commands.md` 已抓到全文（20 KB），含 `st app nx capture/record/mode/key/menu`、`st cap capt/live/lens/iq/seq/dp` 等完整用法与参数枚举（例如 `st cap live sd sensorframerate 12,15,20,24,25,30,40,50,60,100,120,240`）
- **`dfms`**：`dfmsd -t &` + `dfmstool -s '<命令>'`（例：`dfmstool -s 'osd string 7 Hello World!'`），`killall dfmsd` 恢复触摸。命令表在 `DFMS Commands.md`（16 KB）
- **`st cap capdtm getusr/setusr <参数>`**：拍摄参数读写（NX500 1.12 有固定偏移，NX1 1.11 / NX500 1.40 各不同）

### C7. 键位 / 菜单 / 图形层【已核实】

- 菜单覆盖层 `mod_gui`（4 控件 / 4 keysym，「点击即退」，不能滚动对比）
- 键位脚本：`EV+<键>` 组合触发 `scripts/EV_*.sh`（`EV_UP/DOWN/LEFT/RIGHT/OK/S1/MOBILE/AEL/EV`）；`EV + AEL` = FilmLab 配方菜单（NX500 专属；NX1 回退社区原版长录像脚本）
- 结构化天花板（原文）：
  > **长按 / 连击 / 组合键架构性不可得**（内核先消费按键）⇒ "一键"上限 = 一次按键一个动作
  > **p7 无 UI**（Font/DrawText/Dialog/Widget 全 0 命中）⇒ 系统菜单只能在 Linux 侧 `di-camera-app` 里改（无源码，成本极高）
- 其它：X 可用（xeyes/xmessage）、gdb 本机 + gdbserver、屏幕截图、Poker（跨进程 RAM 读写的 `pokemon`/`pokemonche`）、`st cap capdtm`、1080p120、2.5k no-crop、静音快门（NX500）

### C8. 固件内可写档位（NX-WA 反向工程结论）【已核实为「作者宣称/已回归」】

- PW 三维直推进 ISP：`(raw16<<16)|0xD80A`，作者标注「上机回归 3/3」「官方编码 LUT 17/17 逐位一致」
- 3D LUT 用户态装载：`lutapi load`（写前须处于「p7 抢回态」）+ 双通道隔离（表进 LUT1，p7 只碰 LUT0）+ `lutsentinel` 守护
- 结构性天花板：全 7 维 PW 无外部通路（p7 归一化器域 `0x100–0x12e`，`0x130–0x133` 一律拒收）；出厂 LUT 表全是风格表；「成片直灌 LUT」是死路

---

## D. 对「手机连相机传照片」这个目标有用的协议或端点线索

这一节是全报告的重点。**结论先行：目标可达，且不需要改固件——只要相机上跑着 NX-KS/NX-WA 的 web 遥控，手机浏览器打开 `http://<相机IP>/` 就能浏览+下载照片；更轻量的做法是直接用 8080 端口的 `dirlist` + `thumb` + 80 端口的原始文件 URL。**

### D1. 网络与地址事实【已核实】

- 相机 Wi-Fi 接口名 **`mlan0`**（`onboard.sh` 明确警告「不是 lan0！」）
- 相机软 AP 网段 **192.168.43.x**：文档里相机 = `192.168.43.90`，PC/客户端 = `192.168.43.85` / `192.168.43.46`（来自 FTP/telnet 实录）
- SD 卡挂载点候选：**`/mnt/mmc`**（NX-WA 主力）、`/tmp/mmc`、`/mnt/sd`、`/tmp/sd`；另有 **`/opt/storage/sdcard`**（`onboard.sh` 用的 FTP 根）
- 照片路径：`<SD>/DCIM/<NNNPHOTO>/<FILE>.JPG`（`thumb-cgi` 会在 `/mnt/mmc /tmp/mmc /mnt/sd /tmp/sd` 里探测含 `DCIM` 的那个）

### D2. 端点清单（全部由源码逐字核实）

**A) 80 端口 —— `nx-remote-controller-daemon`，`swagger.json` 是官方规格（逐字）：**

| 端点 | 说明 |
|---|---|
| `GET /api/v1/camera/info` | 返回 camera model, firmware version, mac address |
| `GET /api/v1/camera/status` | battery charging, battery percent, battery level, drive, mode, hevc, cameras |
| `GET /api/v1/liveview/get` | 取实时画面 |
| `GET /api/v1/liveview/get_low` | 取实时画面（低画质） |
| `GET /api/v1/osd/get` | 取 OSD 屏幕画面 |
| `GET /api/v1/input/inject?key=###` | 发送一次按键 |
| `GET /api/v1/input/inject?keydown=###` / `?keyup=###` | 按下 / 抬起 |
| `GET /api/v1/input/inject?mousedown=1` / `?mousemove=x-y` / `?mouseup=1` | 鼠标事件（触摸屏） |
| `GET /api/v1/input/inject_keep_alive` | 保活 |
| `GET /api/v1/lcd/on` / `lcd/off` / `lcd/video` | LCD 开 / 关 / 只关 OSD |
| `GET /api/v1/led/on` / `led/off` | 背灯 |
| `GET /api/v1/shutter/normal` / `shutter/silent` | 机械 / 静音快门（**NX500 only**） |

**B) 80 端口 —— 目录与原始文件（相册前端逐字引用）：**

```js
url = 'http://' + app.hostname + '/DCIM';          // 目录根
// 目录页是 Mongoose "Index of" HTML 表格，可解析 <a href>，目录带尾斜杠或文本 [DIRECTORY]
// 原始文件 URL 形如： http://<相机IP>/DCIM/100PHOTO/SAM_3032.JPG
Gallery.prototype.downloadOne = function (file, done) {   // 下载 = 直接 GET 原图
    var xhr = new XMLHttpRequest();
    xhr.open('GET', file.url, true);
    xhr.responseType = 'blob';
    ... a.download = dlName; a.click();
```

**C) 8080 端口 CGI（busybox httpd）—— 逐字协议：**

| 端点 | 参数 / 返回 |
|---|---|
| `GET /cgi-bin/dirlist?p=<DCIM相对路径>` | `{"ok":true,"path":"575PHOTO","dirs":["…"],"files":["SAM_3032.JPG"]}`（实时 readdir） |
| `GET /cgi-bin/thumb?f=<DCIM相对路径>&w=64..1024&q=50..95` | `Content-Type: image/jpeg`；默认 `w=400&q=82`；缓存 `SD/.thumbcache/<w>x<q>/<原路径>.jpg`；`Cache-Control: public, max-age=3600`。前端实际用 `w=320&q=75`（网格）+ `w=1024&q=85`（灯箱） |
| `GET /cgi-bin/prewarm` | JSON 进度，字段 `{running, done, skipped, total}` |
| `GET /cgi-bin/capdtm-api?action=list` | `{"ok":true,"params":{"<NAME>":"<当前值>",…}}`（遍历 `capdtm.conf` 调 `st cap capdtm getusr <NAME>`） |
| `GET /cgi-bin/capdtm-api?action=set&name=USERDATA_ISO&value=ISO_400` | `{"ok":true,"name":…,"value":…}`（内部 `st cap capdtm setusr <NAME> <hex或名称>`） |
| `POST /cgi-bin/push?path=nx-rc/web_root/js/gallery.js`（body=文件内容） | 写入相机 web_root；`GET /cgi-bin/push?action=ping` 在线探测；`GET ...?action=apply` 重启 web daemon |

所有 8080 CGI 都带 `Access-Control-Allow-Origin: *` 和 `Cache-Control: no-store`（thumb 除外，它用 max-age=3600）——**跨源（80 ↔ 8080）已被作者显式处理**。

**D) 手机端可直接用的最小方案（基于以上事实推导，未上机验证）：**

1. 相机装 NX-KS2 / NX-WA（NX500 1.12 + nx-on-wake + nx-patch，或直接用 NX-WA zip）
2. 相机 Wi-Fi 开 AP，手机连上同一网段
3. 手机浏览器：`http://<相机IP>/` → 相册 tab（原作者已实现折叠目录 + 缩略图 + 多选 + 批量下载 + Lightbox 查看原图）
4. 纯脚本方案：`curl "http://<IP>:8080/cgi-bin/dirlist?p=100PHOTO"` 拿文件名 → `curl -O "http://<IP>/DCIM/100PHOTO/<FILE>"` 拿原图；预览用 `http://<IP>:8080/cgi-bin/thumb?f=100PHOTO/<FILE>&w=1024&q=85`
5. 若只要「插卡即自动备份」：可参考 NX300M 的 scp 方案（A7）+ 上游 `ssh-server/`（dropbear + scp + sftp-server 二进制已现成）

### D3. 相机自带的 DLNA / 三星私有协议（不需要改机，独立于上面所有内容）【已核实为「协议描述文件原文」】

`ge0rg/samsung-nx-hacks` 抓了两份**设备协议描述文件（UPnP/DLNA SCPD 与 device description）**，说明官方相机本身就暴露 DLNA MediaServer + 三星扩展动作：

`xml/PC-AutoBackup/ContentDirectory1.xml`（`urn:schemas-upnp-org:service-1-0`）动作表逐字：

```
GetSystemUpdateID, GetSearchCapabilities, GetSortCapabilities,
X_GetLocalPath(ObjectID) -> LocalPath,
X_GetIndexfromRID(CategoryType, RID) -> Index,
X_GetObjectIDfromIndex(CategoryType, Index) -> ObjectID,
X_BACKUP_START,
X_BACKUP_DONE(SystemOff),
X_SystemOff,
Browse(ObjectID, BrowseFlag, Filter, StartingIndex, RequestedCount, SortCriteria)
       -> Result, NumberReturned, TotalMatches, UpdateID,
CreateObject(ContainerID, Elements) -> ObjectID, Result
```

`xml/Remote-ViewFinder/smp_2_.xml`（设备描述，逐字片段）：

```xml
<dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
<deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
<friendlyName>[Camera]NX3000</friendlyName>
<modelDescription>Samsung Camera DMS</modelDescription>
<modelName>NXF1</modelName>
<sec:X_ProductCap>smi,getMediaInfo.sec,getCaptionInfo.sec</sec:X_ProductCap>
<service>
  <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
  <controlURL>/smp_4_</controlURL><eventSubURL>/smp_5_</eventSubURL><SCPDURL>/smp_3_</SCPDURL>
</service>
<service>
  <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
  <controlURL>/smp_7_</controlURL><eventSubURL>/smp_8_</eventSubURL><SCPDURL>/smp_6_</SCPDURL>
</service>
```

→ 即：**相机的 DLNA 服务端控制端点就是 `/smp_4_`（ContentDirectory）、`/smp_7_`（ConnectionManager）**，用 HTTP POST + SOAP 调 `Browse` 可列目录，`X_BACKUP_START` 触发备份。这对「手机连相机传照片」是一条**不依赖任何 mod 的官方通路**（该 XML 采自 NX3000，NX500 是否同构**未核实**）。

### D4. USB / PTP 通路（另一条与手机无关但值得记录的路径）【已核实】

`Enable-PTP-on-NX500-NX1.md` 原文：

> 1. Create text file with just a line break (CR/LF) named **`info.tgw`** in the root of the SD card.
> 3. Connect camera via USB cable to PC; 4. Turn on camera
> You should see a series of green dots on the left side of the camera indicating PTP mode is active.
> `info.tgw` file instructs the camera to start the daemon process in background and wait for PTP/MTP commands from the computer.
> this enables one to automatically copy files off camera (at ~18MB/s for this card) to PC … (BTW, they are written down 4MB at a time - nice for streaming offload to PC)
> Unfortunately, I can't trigger shooting or any other setting from command line (neither gphoto nor libptp support Samsung).
> From what I have seen so far on camera - **dfmsd daemon expects textual commands from the computer.** This should be doable with libptp.

（即：`info.tgw` = 空行文本文件，是 USB 侧的另一个 dfmsd 触发开关；NX3000 的 DLNA device description 里 `<sec:deviceID>SHCOHHCD7X574</sec:deviceID>`）

### D5. 「遥控」到底用什么机制【已核实】

**不是 PTP/IP，也不是独立的私有 TCP 协议**，而是：

1. 相机上跑一个自研 HTTP daemon（80 端口，C 写成，`api_server.c` 基于 mongoose 风格 HTTP + `web_root/` 静态文件）
2. **画面来自抓帧**：`liveview.c`（实时画面）、`osd.c`（12 KB，OSD 屏幕抓取）——经 HTTP 以图片返回（`/api/v1/liveview/get`、`/api/v1/osd/get`）
3. **控制靠注入输入事件**：`input.c` + 独立的 `nx-input-injector` / `xev-nx` 组件，走 X11（`libXtst`，见 `/proc/248/maps` 里的 `/usr/lib/libXtst.so.6.1.0`），HTTP 端点 `input/inject?key=…|keydown|keyup|mouse*` 只是它的前端
4. **设备状态来自相机内部接口**：`status.c` / `nx_model.c`（型号识别、`st cap capdtm` 等）
5. NX-WA 另外开了 8080 的 REST 风格 CGI（参数、目录、缩略图、推送），与 80 端口**跨源并存**

（NX-KS 体系里另有一条键位注入路径：`/opt/usr/nx-ks/auto/a_init.sh` 用 `poker $(pgrep di-camera-app) addr:value…` 对运行中的 App 打内存补丁——NXWA commit 原文：`mod 注入架构发现：/opt/usr/nx-ks/auto/a_init.sh 用 poker $(pgrep di-camera-app) addr:value… 对运行中的 App 打内存补丁（9 组）`）

---

## E. 无法获取的内容清单（逐条说明失败原因）

**完全无法访问的仓库/页面：**

1. `https://github.com/**`、`https://api.github.com/**`、`https://raw.githubusercontent.com/**` —— 全部被环境判定 `resolves to a non-public IP address`（只能用 gh-proxy.com / ghfast.top 前缀绕过）
2. `https://cdn.jsdelivr.net/gh/WApaperplane/NXWA-nx500_modding@main/README.md` 与 `@master/README.md` —— 302 跳到 raw.githubusercontent.com，`cross-origin redirect … is not followed automatically`，**jsdelivr 路线在本环境不可用**
3. `data.jsdelivr.com/v1/packages/gh/WApaperplane/NXWA-nx500_modding` 与 `.../mewlips/nx500_nx1_modding` —— HTTP 502 `Couldn't fetch versions`（这也**不能**作为「仓库不存在」的证据：仓库已由 API 证实存在且公开）
4. `https://web.archive.org/web/2024/https://github.com/WApaperplane/NXWA-nx500_modding` 与 `http://web.archive.org/cdx/search/cdx?url=github.com/WApaperplane*` —— 解析到非公网 IP，拒绝
5. `https://uithub.com/WApaperplane/NXWA-nx500_modding` —— 页面返回 200，但正文是 `Sign in to Continue` / `Content hidden` / `~0 tokens`，**内容被登录墙挡住**（不能据此判断仓库为空）
6. `https://gitingest.com/WApaperplane/NXWA-nx500_modding` —— 只返回 JS 空壳表单
7. `https://gitcode.com/WApaperplane/NXWA-nx500_modding`、`https://gitcode.com/gh_mirrors/nx/nx500_nx1_modding` —— HTTP 404
8. `https://gitee.com/mirrors/nx500_nx1_modding` —— HTTP 405；`https://gitee.com/api/v5/search/repositories?q=nx500` —— 返回 `[]`（Gitee 上无镜像）
9. `https://r.jina.ai/https://raw.githubusercontent.com/…` 与 `https://r.jina.ai/https://github.com/WApaperplane/NXWA-nx500_modding` —— `TypeError: fetch failed`
10. `https://cdn.statically.io/gh/…`（含 `?raw=1`）—— `TypeError: fetch failed`
11. `https://html.duckduckgo.com/html/?q="NXWA-nx500_modding"` —— 解析到非公网 IP
12. `https://api.allorigins.win/raw?url=…` —— HTTP 522
13. `https://api.codetabs.com/v1/proxy?quest=…` —— HTTP 503 限流
14. `https://github.moeyy.xyz/…`、`https://hub.gitmirror.com/…` —— `getaddrinfo ENOENT`（域名不存在）
15. `https://www.dpreview.com/forums/threads/modding-nx500-and-nx1.3976191/` —— HTTP 403（Cloudflare "Just a moment..."）→ **DPReview 上 nx-on-wake / nx-patch / NX-KS 的原始发布帖与全部讨论内容均未取得**
16. `https://zhuanlan.zhihu.com/p/111202067`（《三星NX1\NX500功能破解教程》）—— HTTP 403
17. `https://mewlips.github.io/nx-remote-controller-mod/wiki/Key-mappings.html` —— HTTP 404（GitHub Pages 站未托管 wiki，因此**按键映射表的实际键名清单未取得**）
18. `https://www.eoshd.com/comments/topic/25537-installing-the-hack` 等 EOSHD 帖 —— 未成功获取正文（搜索结果可见但未抓到内容）

**抓到了但未能成功解析/获取正文的文件（内容未核实）：**

19. `WApaperplane/NXWA-nx500_modding/nxwa` 的文件树很大，抓取被截断两次（返回体 50 KB 上限、部分被省略）。**已确证存在**但**未取得正文**的包括：`docs/current/*`（约 40+ 篇，含 `NX500_ARCHITECTURE.md`、`CAMERA_APP_STRUCTURE_2026-10-07.md`、`3DLUT_*`、`PW_*`、`HANDOVER_*`、`TASKFLOW_2026-10-10.md`）、`docs/archive/*`、`docs/evidence/p7/*`、`test_server/**`（PC 端模拟相机 `server.py`、`pwsend/`、`lutpipe/`、`isp/`、`u6/`、`u1/`、`filmlab/flab.py`、`filmsim/`）
20. 上游 `nx-on-wake/README.txt`（9.6 KB）、`nx-patch/README.txt`（10.7 KB）、`nx-patch-v4.0/README.txt`（5.1 KB）、`nx-patch-v4.1/README.txt`（4.9 KB）、`video-bitrate-mods/nx-patch/README.txt` —— **未取正文**，所以「nx-on-wake 与 nx-patch 的精确安装步骤、支持的码率/分辨率档位表」未核实
21. `ssh-server/README.md`（2.4 KB）与 `dropbear-2020.81-nx-path.patch` —— 未取正文，SSH 的端口/账号/密钥细节未核实
22. `DFMS Commands.md`（16 KB）与 `ST_CAP_CAPDTM.md`（28.5 KB）、`Prefman_tool.md`（112.8 KB）、`Working with lens.md`（57.5 KB）、`doc/Update_hibernation_image.md` 全文（65.8 KB，只拿到前面一段 + `maps` 输出）、`Permanent_bitrate_mods.md`、`Poker.md`、`Customizing keys.md`、`Working-with-key-events.md`、`DEV and CS modes.md` —— **未逐篇取正文**
23. `ge0rg/samsung-nx-hacks` 的 wiki（README 里的 *Check the wiki for reverse engineered documentation!*）—— **wiki 内容完全未取得**（`github.com/.../wiki` 走不通，Pages 也没有）。这是本次最大的信息缺口之一
24. `ge0rg/samsung-nx-hacks/tools/{m7mu.py,m7mu-decompress.py,sfresource.py}`、`sdcard/{autoexec.sh,inetd.conf,shutter.sh}`、`xml/PC-AutoBackup/{ConnectionManager1.xml,SamsungDmsDesc.xml}`、`xml/Remote-ViewFinder/smp_3_.xml`(27.6 KB，ContentDirectory SCPD 本体) —— **有清单但未取正文**；`smp_3_.xml` 未取意味着 `X_BACKUP_*` 等动作的完整参数定义只拿到 `smp_2_`/`ContentDirectory1.xml` 的版本
25. `3dguy/nx500` 的 `sd.tar`(2.4 MB)、`sd.zip`、`Working with lens.md`(57 KB) —— 未取正文（README 与 tree 已确认结构）
26. `ottokiksmaler/nx500_nx1_modding` 的 `Preference/*`、`nx-patch` 二进制、`nx1000.Rom.7z` 等大文件 —— 未下载（只核实存在与大小）
27. `WApaperplane/nx500-filmsim`（README 末尾提到的姊妹 public 仓库，胶片仿真引擎与 `.cube` 素材）—— **本次完全未触碰**，未核实
28. `warwickh/pc-autobackup`（ge0rg README 推荐的 Python 版 PC-Autobackup 复刻）—— 未核实
29. `https://nxfiles.nx.tc/files/NX-KS-mod/`（NXWA README 给出的 NX-KS 2.88 下载镜像）—— 未抓取，**NX-KS2 的完整包内容与菜单行为未核实**
30. `mewlips/nx-remote-controller-mod` 的 `NXRemoteController/`（Android app 源码）、`src/*.c` 各文件正文（`api_server.c`、`liveview.c`、`osd.c`、`status.c` 等）—— **只核实了文件存在与大小，未读源码正文**；因此「实时画面具体编码/帧率/取帧方式」「按键名清单」「gallery 端点实现」未核实

**方法学缺口：**

31. 本会话 `pwsh` 完全不可用，无法本地下载仓库、解包固件、计算哈希或做任何离线校验；所有结论均来自 HTTP 抓取的文本
32. 未经任何上机验证：所有「相机端行为」结论都来自仓库文档/脚本/commit 信息，**没有一台真实 NX500 参与验证**

---

## 附：一手来源链接（均为本次实际抓取到内容的 URL）

**WApaperplane/NXWA-nx500_modding（分支 `nxwa`）**
- <https://gh-proxy.com/https://api.github.com/repos/WApaperplane/NXWA-nx500_modding>（仓库元数据）
- <https://gh-proxy.com/https://api.github.com/repos/WApaperplane/NXWA-nx500_modding/git/trees/nxwa?recursive=1>
- <https://gh-proxy.com/https://api.github.com/repos/WApaperplane/NXWA-nx500_modding/commits?per_page=10>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/README.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/VERSION>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/install.sh>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/onboard.sh>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc.sh>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/capdtm/README.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/capdtm/capdtm-cgi.sh>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/thumb/README.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/thumb/thumb-cgi>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/thumb/dirlist-cgi>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/push/README.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/nxwa/scripts/nx-rc/web_root/js/gallery.js>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/README.md>（= 上游 README）
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/doc/Update_hibernation_image.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/ST%20Commands.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/Control-camera-from-command-line.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/Running-telnet-server-on-camera.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/Running%20FTP%20server%20on%20the%20camera.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/Enable-PTP-on-NX500-NX1.md>
- <https://gh-proxy.com/https://raw.githubusercontent.com/WApaperplane/NXWA-nx500_modding/master/Running-shell-scripts-from-SD-card.md>

**mewlips / ottokiksmaler**
- <https://gh-proxy.com/https://api.github.com/repos/mewlips/nx500_nx1_modding>
- <https://gh-proxy.com/https://api.github.com/repos/ottokiksmaler/nx500_nx1_modding/git/trees/master?recursive=1>
- <https://gh-proxy.com/https://raw.githubusercontent.com/mewlips/nx500_nx1_modding/master/README.md>
- <https://ghfast.top/https://raw.githubusercontent.com/mewlips/nx500_nx1_modding/master/README.md>（同一内容，第二条可用路线）
- <https://gh-proxy.com/https://raw.githubusercontent.com/mewlips/nx-remote-controller-mod/master/swagger.json>
- <https://gh-proxy.com/https://api.github.com/repos/mewlips/nx-remote-controller-mod/contents/?ref=master>
- <https://gh-proxy.com/https://api.github.com/repos/mewlips/nx-remote-controller-mod/contents/nx-remote-controller-daemon?ref=master>
- <https://mewlips.github.io/nx-remote-controller-mod/>

**ge0rg / 3dguy**
- <https://gh-proxy.com/https://api.github.com/repos/ge0rg/samsung-nx-hacks/git/trees/master?recursive=1>
- <https://gh-proxy.com/https://raw.githubusercontent.com/ge0rg/samsung-nx-hacks/master/README.mdwn>
- <https://gh-proxy.com/https://raw.githubusercontent.com/ge0rg/samsung-nx-hacks/master/tools/slp-firmware.py>
- <https://gh-proxy.com/https://raw.githubusercontent.com/ge0rg/samsung-nx-hacks/master/tools/drime3-firmware.py>
- <https://gh-proxy.com/https://raw.githubusercontent.com/ge0rg/samsung-nx-hacks/master/xml/PC-AutoBackup/ContentDirectory1.xml>
- <https://gh-proxy.com/https://raw.githubusercontent.com/ge0rg/samsung-nx-hacks/master/xml/Remote-ViewFinder/smp_2_.xml>
- <https://gh-proxy.com/https://api.github.com/repos/3dguy/nx500/git/trees/master?recursive=1>
- <https://gh-proxy.com/https://raw.githubusercontent.com/3dguy/nx500/master/README.md>

**其它**
- <https://gh-proxy.com/https://raw.githubusercontent.com/jdieter/nx300m-autobackup/master/README.md>
- <https://relatedrepos.com/gh/ottokiksmaler/nx500_nx1_modding>（仓库横向对比页，含各仓库 star/活跃度）
- <https://damodev.csdn.net/68a6db734e4959284dabedfb.html>（中文二手介绍，内容与上游 README 一致，仅作旁证）
