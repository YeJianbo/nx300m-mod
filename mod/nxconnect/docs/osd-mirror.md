# E 组交接简报：OSD 相机屏幕镜像

> 交接时间：2026-10-14
> 交接方：DeepSeek（已完成协议逆向与验证）
> 接手方：ChatGPT（负责 Android UI 侧实现）

---

## 0. 先说清楚：哪些是**实测事实**，不要重新推导

本简报里的所有数字都来自 **NX500 真机（固件 1.12，`192.168.5.39`）实测**。
接手时**请直接采信**，不要在无法访问相机的情况下重新推理或"优化"这些值 —— 它们是逆向出来的，不是设计文档。

若需复核，用 `_本会话整理-NXConnect-App/02_有用-相机诊断脚本/` 里的脚本，
或直接 `POST` 那个接口（相机可能不在线，注意先确认可达性）。

---

## 1. 当前状态

| 项 | 状态 |
|---|---|
| App 版本 | **v0.10.3**（versionCode 14），`dist/NXConnect-0.10.3.apk` |
| 单元测试 | **93/93 通过**（其中 18 个是 OSD 协议测试） |
| 相机可达性 | 最近一次实测**在线**（`192.168.5.39`，80/8080/21/23 全通） |
| 协议客户端 | ✅ **已完成**：`app/src/main/java/com/nxwa/nxconnect/net/NxOsd.kt` |
| 协议测试 | ✅ **已完成**：`app/src/test/java/com/nxwa/nxconnect/NxOsdTest.kt`（18 个用例） |
| Android UI 侧 | ❌ **未开始**（这就是要接手的部分） |
| 真机帧样本 | `_host/osd_real.bin`（1,383,276 字节，全量响应原样） |
| 渲染样例 | `_本会话整理-NXConnect-App/01_有用-技术文档/OSD界面样例-AF模式面板.png` |

---

## 2. 协议契约（实测）

### 2.1 请求

```
POST /api/v1/osd/get
Content-Type: application/x-www-form-urlencoded

hashs=<108 个块各自 8 位小写十六进制哈希，直接拼接，无分隔符>
```

- **必须是 POST**。用 GET 会 **404** —— 这是最容易误判的一点
- 语义：客户端把**自己当前持有的每个块的哈希**发上去，服务端**只回变化的块**
- 某块哈希填 `00000000` = "我没有这个块" = 请求该块全量数据
- 因此**首帧**（哈希表全 0）等价于全量请求

### 2.2 响应（大端字节序）

```
u16 frameWidth          实测 720
u16 frameHeight         实测 480
u16 blockWidth          实测 80
u16 blockHeight         实测 40
--- 以下重复 0..N 次（N = 本次变化的块数）---
u16 x                   块左上角 X
u16 y                   块左上角 Y
u32 hash                该块内容哈希（原样回填到下次请求）
byte[blockW*blockH*4]   RGBA 像素，行优先，每像素 4 字节
--- 结束 ---
u16 0xFFFF
u16 0xFFFF              结束标记
```

- **像素格式是 RGBA**（不是 BGR、不是 ARGB）。实测直接按 RGBA 解释即为正确画面
- 块数 = (720/80) × (480/40) = **9 列 × 12 行 = 108 块**
- 块索引 = `(y / blockH) * 9 + (x / blockW)`
- 单块像素 = 80 × 40 × 4 = **12800 字节**

### 2.3 实测性能（决定实现方式）

| 场景 | 数据量 | 耗时 |
|---|---|---|
| 全量（哈希全 0） | 1,383,276 字节 | **815 ms**（≈1.2 fps） |
| 增量（同画面，4 块变化） | 51,244 字节 | **50 ms** |
| 增量轮询（10 次平均） | 153,708 字节 | **108 ms → ≈9.2 fps** |

**结论：首帧之后必须走增量，否则只能跑到 1.2 fps。** 这是整个实现的核心约束。

### 2.4 两条数学自检（已写进单测，说明协议理解正确）

```
全量：8 + 108 × (4 + 4 + 12800) + 4 = 1,383,276   ← 精确命中实测值
增量：8 +   4 × (4 + 4 + 12800) + 4 =    51,244   ← 精确命中实测值
```

如果接手后改动了解析逻辑导致这两条不成立，说明改错了。

---

## 3. 输入注入（用于操作相机 UI）

```
GET /api/v1/input/inject?key=<keysym>        按下并释放
GET /api/v1/input/inject?keydown=<keysym>    仅按下
GET /api/v1/input/inject?keyup=<keysym>      仅释放
GET /api/v1/input/inject?mousemove=<x>-<y>   移动鼠标
GET /api/v1/input/inject?mousedown=1         按下
GET /api/v1/input/inject?mouseup=1           释放
```

### 3.1 实测：键盘可靠、鼠标部分有效

判定方法：注入前后比较 OSD **变化块数**（先确认静止时是稳定 0 块，排除动画干扰）。

| 注入 | 变化块数 | 判定 |
|---|---|---|
| 静止基线（8 帧） | 0 | 屏幕静态，判定有效 |
| 键盘 `KP_Up` | 25 | ✅ |
| 键盘 `KP_Enter`（OK） | 98 | ✅ |
| 键盘 `Menu` | 98 | ✅ |
| 鼠标点击中央 (360,240) | 0 | ❌ |
| 鼠标点击**左下 (100,400)** | 116 | ✅ |
| 鼠标点击右上 (620,80) | 0 | ❌ |

**读法**：键盘注入**每次都可靠**；鼠标注入**在部分区域有效**，说明事件能送到，
但相机 UI 只在特定控件上采纳鼠标。**建议第一阶段只做键盘导航**，鼠标点选留到后面单独摸清可点区域。

### 3.2 完整键表

`app/src/main/java/com/nxwa/nxconnect/remote/KeyMap.kt` 里已有 **30 个键**，
逐字来自相机端 `web_input/js/keyinput.js`，可直接复用（含 A/B/C 三组：曝光参数 / 拍摄模式与驱动 / 导航与转盘）。

---

## 4. 已完成的代码（可直接复用，不要重写）

### 4.1 `net/NxOsd.kt`（纯 JVM，无 Android 依赖）

提供了：
- `NxOsd.parse(raw: ByteArray): Frame` —— 解析一次响应，**坏数据会抛 `OsdParseException` 而不是静默产出垃圾**
- `Frame.applyTo(canvas: ByteArray)` —— 把变化块合成到整帧 RGBA 缓冲区
- `Frame.updateHashes(hashes: IntArray)` —— 用新哈希更新客户端哈希表
- `NxOsd.buildHashsParam(hashes, blockCount)` —— 拼出下一次请求的 `hashs` 表单值
- `NxOsd.newCanvas(w, h)` / `pixelRgb(...)` —— 缓冲区工具
- `NxOsd.buildFrame(...)` / `solidBlock(...)` —— **造**响应（测试与 mock 用）

### 4.2 `test/NxOsdTest.kt`（18 个用例，全绿）

覆盖：帧头解析、块索引、结束标记、画布合成坐标、108 块全铺不越界、
画布过小报错、哈希更新、`hashs` 拼接、以及 5 类坏数据（过短 / 非法帧头 /
不可整除 / 像素截断 / 缺结束标记）。

---

## 5. 待实现（接手范围）

### 5.1 `ui/OsdView.kt`（自定义 View，核心）

- 持有 `ByteArray` 整帧 RGBA 画布（720×480×4 = 1,382,400 字节）
- 收到新帧 → `frame.applyTo(canvas)` → **只重绘变化块的矩形区域**（`invalidate(rect)`），不要整帧重绘
- 缩放显示：按控件尺寸等比缩放（`Matrix` 或 `Canvas.scale`），保持 3:2 不变形
- 用 `Bitmap` + `copyPixelsFromBuffer` 把 RGBA 画布贴上去（比逐像素 `setPixel` 快得多）
- 帧循环：建议 `Dispatchers.IO` 里串行请求（**不要并发**，相机端单核），间隔目标 100–150 ms

### 5.2 接进 `ui/RemoteActivity.kt`

- 加一个「相机屏幕」开关：打开时暂停/降频 liveview（**两者同时跑会争带宽**，实测 liveview 已占 3–5 fps，OSD 增量要 9 fps）
- OSD 模式下，键盘按键走现有的 `sendKey` / `holdKey`
- 提供方向键 / OK / MENU / Fn 的直接按钮（键表已有）

### 5.3 鼠标坐标映射（第二阶段）

- OSD 显示区域坐标 → 720×480 帧坐标的线性映射（注意缩放与 letterbox 黑边偏移）
- `mousemove` 用 `<x>-<y>` 格式，然后 `mousedown=1` / `mouseup=1`
- **先摸清哪些区域真能点**：实测中央与右上无效、左下有效。可用「注入前后比较变化块数」的方法自动探测

---

## 6. ⚠️ 实现时容易踩的坑（都踩过）

1. **必须 POST**。GET 返回 404，很容易误以为接口不存在
2. **全量很慢（815 ms）**，首帧之后一定要走增量，否则 1.2 fps
3. **像素是 RGBA**，不要按 ARGB/BGR 解
4. **哈希必须原样回填**，不要自己算（服务端用自己的哈希算法）
5. **坏数据要抛错**。本项目历史上出过
   `ArrayIndexOutOfBoundsException: length=259200; Index=259200` 导致 App 崩溃的事故
   （NV12 解码把平面尺寸理解错了），所以解析层**宁可报错也不要静默越界**
6. **OSD 只在相机出流时才有数据**：实测相机空转时 `POST` 返回 **0 字节**（HTTP 200 但空体），
   拉一次 liveview 之后立刻就有 1,383,276 字节。所以空响应要当"暂不可用"处理并重试，
   不要当协议错误
7. **键盘 `keydown`/`keyup` 语义**：相机上「曝光补偿」和「AE 锁定」是**按住生效**的，
   单击（`key=`）会立刻松开，配合转盘调节无效。现有 `KeyMap.KeyDef.hold` 标记了这两个键

---

## 7. 构建与验证

```powershell
$env:JAVA_HOME='C:\DsNX\_host\jdk'
$env:ANDROID_HOME='C:\DsNX\_host\android-sdk'
$env:ANDROID_SDK_ROOT=$env:ANDROID_HOME
$env:PATH="$env:JAVA_HOME\bin;$env:PATH"
& 'C:\DsNX\_host\gradle\bin\gradle.bat' -p C:\DsNX\NXConnect `
    :app:testDebugUnitTest :app:assembleRelease --rerun-tasks --no-daemon --console=plain
```

产物在 `NXConnect/app/build/outputs/apk/release/app-release.apk`，
发布方式是复制到 `dist/NXConnect-<版本>.apk` 并**递增 `versionCode` 与 `versionName`**
（`NXConnect/app/build.gradle.kts` 的 `defaultConfig`）。
App 工具栏会显示版本号，方便确认设备上装的是哪个包。

---

## 8. 可用的参考实现

`app/src/main/java/com/nxwa/nxconnect/ui/LiveViewView.kt` 已经解决了同类问题，
**建议照它的结构写 `OsdView`**：

- 解码在 `Dispatchers.Default`、用 `FRAME_INTERVAL_MS` 限流
- 帧循环整体包 `try/catch`，`CancellationException` 重抛、其余 `Throwable` 记日志并停止
- 尺寸变化时重建 `Bitmap` 并回收旧的
- 画质等开关用 `@Volatile` 标志传递，**不要 stop/start 重启循环**（会因竞态导致直接退出）

---

## 9. 一句话总结

**协议层已经做完并验证过了（含真机帧样本与数学自检），接手只需写 Android 侧的差分渲染与接线。**
所有实测数字在 §2，不要重新推导；有疑问优先查 §6 的坑列表。
