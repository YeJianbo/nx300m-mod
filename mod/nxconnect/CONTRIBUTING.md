# 参与贡献

感谢你有兴趣参与。这个项目有几个**和普通 App 不一样**的地方，先看这几条能省很多来回。

---

## 最重要的前提：没有真机基本无法开发

本 App 的全部行为都依赖**相机端的第三方组件**（`nx-remote-controller-mod` 等），
这些组件不是三星官方固件的一部分。因此：

- **不接真相机就跑不起来**。没有相机时最多只能跑单元测试。
- 问题几乎都是"相机端状态相关"的，**在模拟器或纯代码层面看不出来**。
- 提交 PR 前请**务必在真机上验证**，并在 PR 描述里写清相机型号、固件版本、相机端组件版本。

## 提交代码前的检查清单

```bash
cd NXConnect

# 1. 单元测试必须全绿（当前 110 个）
./gradlew :app:testDebugUnitTest

# 2. 能构建出 APK
./gradlew :app:assembleDebug
```

`unit test` 覆盖的是**纯逻辑**部分（协议解析、NV12 解码、OSD 校验）。
界面与网络行为不在单测范围内，需要真机验证。

---

## 环境搭建

### Android 侧

- JDK 17+
- Android SDK：platform 35、build-tools 35.0.0
- 用 Android Studio 直接打开 `NXConnect/` 目录即可

### 签名

仓库**不含签名密钥**。`app/build.gradle.kts` 会依次尝试：

1. 读 `NXConnect/keystore.properties` —— 有就用它签
2. 没有该文件 —— 回退到 debug 签名（**贡献者无需做任何事**，直接能构建）

想生成自己的发布密钥：

```powershell
pwsh tools/make-keystore.ps1
```

### 相机侧

需要相机上已安装 `nx-remote-controller-mod`（或 NX-KS2 / NX-WA 这类包含它的整合包）。
装好后按相机上的 `MOBILE` 键启动服务，相机会开一个热点。

App 会通过 UDP 广播发现相机；也可以手动填 IP。

---

## 诊断脚本

`tools/` 下是这台相机的诊断脚本，能在排查问题时直接取证：

```bash
python tools/probe_camera_basic.py 192.168.5.39   # 连通性、端口、相机信息
```

脚本假设相机端 telnet（23 端口，root 免密）与 FTP（21 端口，root 空口令）可用 ——
这两个服务是相机端 mod 提供的，不是原厂固件自带。

---

## 代码约定

### 协议常量不要"优化"

`net/NxOsd.kt`、`net/Nv12Decoder.kt` 等文件里的数字（帧尺寸、块尺寸、长度公式、
时序）**全部来自真机实测**，不是设计选择。看到"奇怪的"数值时：

- 先读该文件顶部的注释，通常会说明这个数字怎么来的
- 真想改，请先跑一遍对应的单元测试，里面有**数学自检**（例如
  `12 + N × 12808` 必须精确等于真机响应长度）
- 这些自检**故意**写成硬编码的实测值，就是为了防止有人"顺手优化"掉

### 解析层宁可报错，不要静默越界

这个项目历史上因为 NV12 平面尺寸理解错误，出过
`ArrayIndexOutOfBoundsException` 导致 App 崩溃的事故。
所以解析层的约定是：**遇到不合契约的数据直接抛异常**，绝不返回部分结果。
改这块代码时请保持这个约定。

### 注释写"为什么"，不写"是什么"

现有代码里大量注释记录的是**踩过的坑**（例如某处为什么不能用 `readBytes()`、
为什么某个圆角必须小）。这类信息比"这行在做什么"有价值得多，请保持。

---

## 提交信息

```
<范围>: <一句话说清改了什么>

可选的补充说明。如果修的是某个具体现象，写清现象。
```

范围用模块名，例如 `osd`、`liveview`、`gallery`、`remote`、`docs`、`tools`。

例：

```
liveview: 修掉高清取景的网络读取瓶颈

HttpURLConnection 默认缓冲约 8KB，一帧 518KB 要读 63 次。
实测网络耗时从 288ms 降到 199ms。
```

---

## Issue

请用 `Bug 报告` 模板。**缺相机型号 / 固件版本 / 相机端组件版本 的 issue 无法处理**，
会直接请求补充信息。

如果只是使用上的疑问（装不上、不确定怎么用），请用
[Discussions](https://github.com/Katsiy/NXConnectForNX500/discussions)，不必开 issue。

---

## 许可证

本项目以 **AGPL-3.0** 发布。提交贡献即表示你同意以同一许可证授权你的贡献。
详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
