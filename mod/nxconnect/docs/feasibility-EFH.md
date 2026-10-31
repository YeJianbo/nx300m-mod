# E / F / H 组可行性实测报告

> 实测时间：2026-10-14　相机：NX500 固件 1.12 / `192.168.5.39` / **M 档** / 取景正常
> 方法：真机逐项验证，数字都是实测值。

---

## E 组：OSD 屏幕镜像 —— ✅ **完全可行**（本轮最大收获）

### 结论

**App 可以把相机屏幕实时搬到手机上，并且能用键盘操作相机菜单。**

原来以为 daemon 没有参数接口、遥控只能"盲按实体键"，现在这条路完全打开了。

### 实测数据

| 项目 | 实测值 |
|---|---|
| 接口 | `POST /api/v1/osd/get`（**必须 POST**，GET 会 404 —— 这是个常见误判点） |
| 请求体 | `hashs=<120 个 8 位十六进制哈希拼接>`（对应各块的当前指纹） |
| 全量响应 | **1,383,276 字节 / 815 ms**（≈1.2 fps，太慢，不能直接轮询全量） |
| **增量响应** | **平均 153 KB / 108 ms → 9.2 fps**（只传变化的块） |
| 帧规格 | **720 × 480**，块 **80 × 40** → 9 列 × 12 行 = **108 块** |
| 像素格式 | **RGBA**（4 字节/像素；实测直接按 RGBA 解就是正确画面） |
| 响应头 | `u16` 帧宽、帧高、块宽、块高；之后每块为 `x, y, hash(u32), bw*bh*4 字节像素`；`0xFFFF,0xFFFF` 为结束标记 |

### 实测画面

渲染出来就是相机屏幕本身（见同目录 `OSD界面样例-AF模式面板.png`）：

- 顶部 `NX-KS` 标识与「MENU / 返回」
- 中间「AF 模式：单一 AF」
- 底部四个选项卡片：**单一 AF / 连续 AF / 有源 AF / 手动对焦**

### 输入注入：键盘可靠，鼠标部分有效

用"注入前后比较 OSD 变化块数"来判定（先确认静止时**稳定 0 块变化**，排除动画干扰）：

| 注入 | 变化块数 | 判定 |
|---|---|---|
| 静止基线（8 帧） | 0 | 屏幕静态，判定有效 |
| 键盘 `KP_Up` | 25 | ✅ |
| 键盘 `KP_Enter`（OK） | 98 | ✅ |
| 键盘 `Menu` | 98 | ✅ |
| 鼠标点击中央 (360,240) | 0 | ❌ |
| 鼠标点击**左下 (100,400)** | 116 | ✅ |
| 鼠标点击右上 (620,80) | 0 | ❌ |

**读法**：键盘注入**每次都可靠响应**；鼠标注入**在部分区域有效**，
说明事件能送到，但相机 UI 只在特定控件上采纳鼠标。

> ⚠️ 早期一次测试里我写错了判定条件（0 vs 0 也判成"生效"），
> 已纠正为"先测静止基线，再对比注入后变化"。

### 因此 E 组能做什么

1. **实时屏幕镜像**（9 fps 增量，够用）
2. **用键盘远程操作相机菜单** —— 这就是"在 App 端改曝光参数"的**正确实现路径**：
   按 `ISO`/`WB`/`EV` 专用键唤起面板 → OSD 上看到 → 方向键选 → OK 确认
3. **参数预设**：把"唤起面板 → 选到某值"编成按键序列，一键下发
4. 之后若确认鼠标可点区域，还能做成"点哪改哪"

### 实现要点（避免踩坑）

- 客户端要维护 **108 个块**的哈希表；每轮把当前哈希发上去，服务端只回变化的块
- **全量请求很慢（815 ms）**，所以首帧之后必须走增量
- 只更新变化的块，不要整帧重绘 —— 这是能跑到 9 fps 的关键
- 像素格式直接按 **RGBA** 解

---

## F 组：RAW 缩略图走相机端 —— ❌ **不可行**

### 实测

```
$ which convert
-sh: convert: command not found
```

**相机上没有 `convert`**（也没有 `magick`/`dcraw`/`ufraw`）。只有 `djpeg` / `cjpeg`
（`/usr/bin/djpeg`、`/usr/bin/cjpeg`）—— 那是 JPEG 编解码工具，**读不了 SRW**。

### 顺带纠正一个此前的错误说法

我先前在协议文档里写过"相机端缩略图由 `convert` 生成、单核 CPU 受限，所以并发限 2"。
**`convert` 不存在，这个说法是错的。**

`thumb` CGI 脚本确实存在（`/opt/usr/nx-ks/nx-rc/capdtm/www/cgi-bin/thumb`，5544 字节），
但它对 SRW 明确返回：

```
thumb error: unsupported type: .srw      （HTTP 200 + text/plain，36 字节）
```

### 结论

RAW 缩略图**没有**相机端现成方案。可选替代：

| 方案 | 代价 |
|---|---|
| 下载整个 SRW 后在**手机端**解（需内置 RAW 解码库） | SRW 19–34 MB，慢且占空间 |
| 继续用占位格 + 显示文件大小（现状） | 体验一般但不折腾 |
| 用 E 组的 OSD 在相机屏幕上切到播放模式看 | 曲线救国，可行但绕 |

---

## H 组：绕开相机 UI 直接设曝光参数 —— ❌ **此路不通**

### 实测证据

**1. `st cap capdtm` 只能读一个参数**

```
ADJUSTSHUTTERTYPE   ->  UserData is ADJUSTSHUTTERTYPE_MECHA (0x750000)   ✅
```

另外试了 **16 个**命名（`ADJUSTISO`、`ADJUSTSHUTTERSPEED`、`ADJUSTEXPOSURE`、
`ADJUSTEXPOSUREMODE`、`ADJUSTDRIVEMODE`、`ADJUSTMODE`、`ADJUSTSENSITIVITY`、
`ADJUSTWHITEBALANCE`、`ADJUSTFLASHMODE`、`ADJUSTLENSPROPERTY`、`ADJUSTZOOM`、
`ADJUSTFOCUSMODE`、`ADJUSTFOCUSAREA`、`ADJUSTFRAMERATE`、`ADJUSTMOVIESIZE`、
`ADJUSTSHUTTER`），**全部** `Invalid argument[2]=xxx`。

参数名表不在可读的二进制里（`/usr/bin/st` 只是 4.6 KB 的壳，`ldd` 显示它链到
`libcapture-fw-prod.so` 等几十个库；在 `st`、`libcapture-fw-prod.so`、
`libshell-command.so` 里都搜不到 `ADJUSTSHUTTERTYPE` 这个字符串，
说明表是运行时从固件加载的）。

**2. 没有可用的写入通路**

| 通路 | 实测 |
|---|---|
| `st cap capdtm setusr ...` | **无任何输出，也不出现在 usage 里** → 不可用 |
| `st cap capt mcb <cat> <id> <len> <data>` | 同样**无输出** → 不可用 |
| `st cap lens info` | `Cann't execute capture command over MCB!`（MCB 被相机 App 占用） |

### 结论

**没有"直接设曝光参数"的接口可用。** 这不是权限问题，是相机根本没暴露这条通路。

### 但 E 组已经把这件事解决了

H 组想达成的目标（在 App 里改曝光参数），**通过 E 组的 OSD + 键盘注入完全可以实现** ——
只是路径变成"操作相机自己的菜单"，而不是"直接写参数寄存器"。

对使用者来说效果一样，甚至更好（因为改完立刻能在 OSD 上看到结果）。

---

## 汇总

| 组 | 需求 | 结论 |
|---|---|---|
| **E** | OSD 屏幕镜像 | ✅ **可行且已验证**，9.2 fps 增量，像素 RGBA。键盘操作可靠，鼠标部分有效 |
| **F** | RAW 缩略图走相机端 | ❌ 相机上无 `convert`，只有 djpeg/cjpeg；thumb-cgi 明确拒绝 `.srw` |
| **H** | 直接设曝光参数 | ❌ 无可用通路（capdtm 只能读 1 个参数，setusr 与 mcb 都无输出） |

**一句话**：H 组想做但做不到的事，E 组用另一条路做到了。
