# NX500 真机实测指南

这份指南的目的是**用最少的手动操作，把 App 里那些"文档级证据但没上机验证过"的假设一次性证实或推翻**。

---

## 0. 装 APK

```powershell
C:\DsNX\_host\android-sdk\platform-tools\adb.exe install -r C:\DsNX\dist\NXConnect-0.3.0.apk
```

Android 13+ 首次打开会问通知权限——**建议允许**，批量下载的进度和结果都在通知栏里。

---

## 1. 第一步：相机端准备（决定后面能测到哪一层）

### 1.1 如果你的相机已经装了 NX-KS2 / NX-WA（推荐，功能最全）

1. 相机开机，**按 `MOBILE` 键**
2. 等相机屏幕出现 mod 菜单，记下屏幕上显示的 IP
3. 手机连到与相机同一网络

**预期**：App 连接后状态行显示 `完全体：可实时列目录、缩略图、取景、快门`。

### 1.2 如果相机是原厂固件（没有任何 mod）

原厂 NX500 **没有 HTTP 服务**，但有两条路可以走：

**路线 A：SD 卡放脚本，启动 busybox httpd（不需要刷机）**

在 SD 卡根目录准备：

```
info.tg            <- 内容一行：nx_cs.adj
nx_cs.adj          <- 内容一行：shell script /mnt/mmc/start_http.sh
start_http.sh      <- 见下
busybox            <- ARM busybox 二进制（可从 NX300 固件里提取，或 NX-KS 包里拿）
```

`start_http.sh`：

```sh
#!/bin/sh
sleep 30
mkdir -p /dev/pts
mount -t devpts none /dev/pts
/mnt/mmc/busybox httpd -h /mnt/mmc -p 80
killall dfmsd      # 恢复触摸屏（dfmsd 会占屏显示绿点）
```

插卡开机。**预期**：App 连接后显示 `简化体：只能列目录与下载原图`——相册能用（走 HTML 目录页解析），取景和快门不可用。

> ⚠️ 这条路会让相机失去触摸（dfmsd 被 kill 后部分功能异常），测试完把卡拔掉即可恢复。

**路线 B：什么都不装**，先确认 App 的"如实报告"行为是否正确——连接任意 IP 时它应该说清楚缺什么，而不是假装能用。

---

## 2. 第二步：连接与能力探测

| 步骤 | 操作 | 预期结果 | 记录 |
|---|---|---|---|
| 2.1 | 打开 App | 显示"未连接"，底部有帮助文本 | |
| 2.2 | 点「自动探测」 | 10–25 秒内找到相机，状态行显示 `发现相机 <IP>` 并自动连接 | 实际耗时：____ |
| 2.3 | 看状态行 | `已连接 <IP>（机型 NX500 · 固件 1.12 · MAC …）` 或只有 IP | 实际显示：____ |
| 2.4 | 看能力探测行 | `完全体：…` / `简化体：…` / 原厂提示 | 实际显示：____ |

**这一步要确认的未知项**：

- `/api/v1/camera/info` 的**真实字段名**。App 用多候选 key 宽松解析（`model`/`camera`/`modelName`，`firmware`/`version`），如果状态行只显示 IP 而没有机型固件，说明字段名都不匹配。
- 抓真实响应：

  ```powershell
  # 把 <相机IP> 换成实际地址
  curl http://<相机IP>/api/v1/camera/info
  curl http://<相机IP>/api/v1/camera/status
  ```

  把这两条的输出贴回来，我就能把解析器改成精确匹配。

---

## 3. 第三步：相册与传输（核心功能）

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 3.1 | 点「相册 / 传照片」 | 几秒内列出 `1xxPHOTO` 目录，**最新目录在最上面** |
| 3.2 | 看目录行 | 每行显示"N 个媒体文件"和一张封面缩略图 |
| 3.3 | 点进最新目录 | 3 列网格，照片**最新在前**，缩略图逐张出现 |
| 3.4 | 滚动到底部 | 不卡顿；相机端 CPU 不会被打满（App 限了并发 2） |
| 3.5 | 点一张照片 | 全屏打开，可双指缩放、双击放大、左右翻页 |
| 3.6 | 返回相册，长按某张 | 进入多选模式，底部出现「下载所选」 |
| 3.7 | 选 5 张，点「下载所选」 | 通知栏出现下载进度，逐张完成；结束后提示"完成：成功 5 张" |
| 3.8 | 打开手机系统相册 | 在 `DCIM/NXConnect/` 相册里看到这 5 张，是**原图**（不是缩略图） |

**重点验证项**：

- **3.1 的关键**：如果目录列表为空但相机里明明有照片 → 说明 8080 的 `dirlist` CGI 没装，App 应该已经回退到 80 端口的 HTML 目录页。请记录状态行显示的是哪一句。
- **3.4 的关键**：如果相册页频繁弹"相机响应缓慢"或缩略图大面积失败 → 说明并发限制还需要再降，或者相机端 `convert` 不存在。
- 直接验证 CGI：

  ```powershell
  curl "http://<相机IP>:8080/cgi-bin/dirlist?p="
  curl -o t.jpg "http://<相机IP>:8080/cgi-bin/thumb?f=100PHOTO/SAM_0001.JPG&w=320&q=75"
  # 看 t.jpg 是否是 15-30KB 的正常 JPEG；若正文是 "thumb error: ..." 说明 convert 缺失
  ```

---

## 4. 第四步：遥控取景与快门

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 4.1 | 相机切到**拍摄模式**（不是播放模式） | — |
| 4.2 | App 点「遥控取景与快门」 | 进入遥控页 |
| 4.3 | 点「开始取景」 | 左上角出现 `720×480 · Nfps`，画面**实时动**（对着相机转镜头能看到变化） |
| 4.4 | 点「对焦」 | 相机镜头对焦动作（半按） |
| 4.5 | 点「快门」 | 相机真的拍了一张；回到相册能看到新照片 |
| 4.6 | 点「静音快门」 | 无反光板声（NX500 电子快门），相册新增一张 |
| 4.7 | 点「更多按键」 | 展开 16 键面板；点方向键/OK 能操作相机菜单 |
| 4.8 | 点「播放/拍摄」 | 相机在播放与拍摄模式间切换 |
| 4.9 | 把 App 切后台再回来 | 取景自动停止（省相机 CPU），可再点开始 |

**重点验证项**：

- **4.3 的帧率**：源码里参考实现是 100ms/帧（约 10fps），如果明显更低（比如 2–3fps）说明相机端出图慢，需要调大间隔。
- **4.3 的色彩**：如果画面偏色/发紫，说明 NV12 的 UV 偏移理解有偏差（本机已用标准 BT.601 比对过算法，偏差 ≤1，所以更可能是相机端的平面布局与 NX300 不同）。**请描述偏色方向**。
- **4.5/4.6 的语义**：`/api/v1/shutter/normal|silent` 在 NX500 上是改 `ADJUSTSHUTTERTYPE` 参数（源码里是 `st cap capdtm setusr ADJUSTSHUTTERTYPE 0x750000/0x750001`）。**它可能只是"设置快门类型"，不一定直接触发拍摄**。如果点了没反应但相机在之后按物理快门时变成静音/机械，就说明这个端点只改设置、不触发拍摄——那我们改用按键注入 `Super_R`（App 已实现该回退）。
- 相机端源码里的确切行为：

  ```sh
  # 在相机上（telnet 23，root，无密码）
  st cap capdtm getusr ADJUSTSHUTTERTYPE     # 看当前值
  ```

---

## 5. 出问题时怎么抓证据

### 5.1 抓 App 日志

```powershell
C:\DsNX\_host\android-sdk\platform-tools\adb.exe logcat -s NxApp:* NXDownload:* NetworkBinder:* CameraDiscovery:* AndroidRuntime:E
```

App 内部维护了 300 条环形日志（连接、目录读取、缩略图失败、下载结果都会记），上面这条能一次性看到。

### 5.2 直接问相机

```powershell
$IP = "<相机IP>"
curl "$IP/api/v1/camera/info"
curl "$IP/api/v1/camera/status"
curl "$IP/api/v1/liveview/get" -o lv.bin ; (Get-Item lv.bin).Length   # 期望 518400
curl "$IP/api/v1/liveview/get_low" -o lvl.bin ; (Get-Item lvl.bin).Length  # 期望 259200
curl "http://${IP}:8080/cgi-bin/dirlist?p="
curl "http://${IP}:8080/cgi-bin/thumb?f=100PHOTO/SAM_0001.JPG&w=320&q=75" -o t.jpg
```

### 5.3 进相机内部看（有 mod 时）

```powershell
# Windows 自带 telnet 客户端默认没装，用 python 更省事
python -c "import telnetlib,sys; t=telnetlib.Telnet('192.168.0.105',23); print(t.read_until(b'#',3)); t.write(b'ps | grep -E \"daemon|httpd|telnetd\"\n'); print(t.read_until(b'#',3).decode('utf-8','replace'))"
```

或者用你项目里现成的工具：

```powershell
python C:\DsNX\_host\src\NXWA-nxwa\NXWA-nx500_modding-nxwa\test_server\telnet_run.py <相机IP> "ps | grep -E 'daemon|httpd'"
python C:\DsNX\_host\src\NXWA-nxwa\NXWA-nx500_modding-nxwa\test_server\telnet_run.py <相机IP> "ls -l /opt/usr/nx-ks/nx-rc/ /opt/usr/nx-ks/capdtm/www/cgi-bin/"
```

**特别想确认的一件事**：相机端 8080 的 busybox httpd 是否真的在跑、CGI 目录在哪：

```sh
netstat -ltn | grep -E ':80|:8080'
ls -l /opt/usr/nx-ks/capdtm/www/cgi-bin/
cat /opt/usr/nx-ks/onboard.log
```

---

## 6. 常见故障对照表

| 现象 | 最可能的原因 | 处理 |
|---|---|---|
| 自动探测几十秒找不到 | 手机没连相机网络；或相机不在 80 端口 | 手动填 IP；确认手机 Wi-Fi 已连到相机热点 |
| 连上了但状态行说"遥控服务未响应" | 相机没按 MOBILE 键 / daemon 没起来 | 相机上按 MOBILE；或看 `/opt/usr/nx-ks/onboard.log` |
| 相册空、提示"读取目录失败" | 8080 CGI 未部署，且 80 端口也没有目录页 | 装 NX-WA 的 `thumb-install.sh`；或走 1.2 路线 A |
| 缩略图一片灰（占位） | `convert` 缺失或路径不对 | 相机上 `ls /opt/usr/nx-ks/tools/usr/bin/convert` |
| 缩略图全是原图、加载很慢 | CGI 未生效，App 在降级拉原图 | 检查 `capdtm-httpd.sh` 是否启动 |
| 频繁提示相机缓慢/断开 | 相机单核被 `convert` 打满 | 正常现象；等预热完成，或减少同时浏览的照片数 |
| 取景黑屏但显示 fps | 相机的 liveview 端点在这代固件上返回别的格式 | 抓 `lv.bin` 看字节数，把长度告诉我 |
| 取景偏色 | NV12 平面布局与预期不同 | 描述偏色方向 + 抓一帧 `lv.bin` |
| 点快门没反应 | daemon 的 shutter 端点只改设置不触发 | 用「更多按键」里的注入，或告诉我现象，我改成 `Super_R` 注入为主 |
| 下载的图在相册里看不到 | Android 9 及以下缺存储权限 | 在系统设置里给 App 存储权限后重下 |
| 手机提示"无互联网连接" | **正常**——相机热点没有外网 | 选择"仍然保持连接" |

---

## 7. 需要你回传的最小信息集

只要把这四项发回来，我就能把剩下所有"未验证"项收敛掉：

1. `curl http://<相机IP>/api/v1/camera/info` 的**完整输出**
2. `curl http://<相机IP>/api/v1/camera/status` 的**完整输出**
3. `curl http://<相机IP>:8080/cgi-bin/dirlist?p=` 的**完整输出**
4. App 连接后状态行 + 遥控页左上角的**原文截图或文字**

顺带一提：如果遥控取景能用，请说一下实测帧率和画面是否偏色——这两项决定要不要重写取景那条链路。
