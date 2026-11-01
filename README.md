# NX300M Mod / NXConnect

为三星 NX300M 增加机内 LUT、手机相册与遥控、拍摄参数扩展和 Quick Share 分享。相机端通过 NX300M 1.15 / MFW 的脚本入口加载，手机端基于 NXConnectForNX500 适配。

## 我们改了什么

### 相机端

- **机内 LUT**：在 RAM 中挂接原厂 LUT 更新和写表入口，把自定义表交给原厂 ISP；支持 0–100% 强度、换表、原厂恢复和状态读回。
- **LUT 实时预览**：复用原厂智能滤镜“微型”的单个槽位，通过 IPCS 9³ LUT 处理取景帧；具体风格和强度由 MOD 独立切换。提供原样、单位表、黑白测试及恢复菜单。
- **LUT 转换**：标准 `.cube` 转为 NX300M 的 9³、10 bit D4 表，并为 sRGB 风格生成原厂组合表；保留源文件、色彩空间及来源信息。
- **拍摄设置**：接入 ISO 步长、扩展 ISO、Auto ISO 上限、最快快门限制、拨盘锁，以及 Picture Wizard、RGB 和配方参数接口。
- **相册服务**：同名 RAW＋JPEG 合并显示，提取内嵌缩略图和预览，提供原文件下载与分享入口。
- **Quick Share**：接入 rquickshare 核心，增加相册文件交接、设备选择、PIN、进度和取消；当前通过已有 IP 网络传输。
- **启动与菜单**：整合 SD 启动、RAM 服务运行、单实例启停、按键入口和分段性能计时；修复 LUT 闪现提示与正式面板重叠后无法再次打开菜单的焦点问题。
- **手动镜头**：增加无线入口镜头检查的 RAM 补丁，以及电子镜头状态和镜头校正更新链的诊断工具。

### Android App

当前源码版本 **0.20.30 / 78**。

- **界面重做**：采用 One UI 8.5 / 9 风格，统一折叠标题、浮动栏、底部弹框、详情排版、图标、明暗主题和页面过渡。
- **连接**：增加 NX300M 服务识别、自动发现、手动地址、WLAN 连接、NFC 配对入口和断线状态更新。
- **相册**：照片网格、批量选择、大图预览、缩略图条、上滑详情、RAW＋JPEG 配对、下载和分享。
- **照片信息**：编辑镜头预设、镜头型号和等效焦距，导出时写入 JPEG／兼容 SRW 副本。
- **遥控**：接入原厂 Remote Viewfinder 实时画面、对焦和快门协议，以及 Mod 屏幕镜像、MENU、方向键和 OK 操作。
- **相机设置与 Mod 管理**：集中管理曝光、配方、色彩参数、LUT 和启动配置，读取模块状态，检查本地 Mod 安装包与固件文件。
- **LUT 库**：选择相机已部署 LUT、调整强度、恢复原厂；手机端支持 cube 批量导入、重命名、排序、导出和删除。

详细实现和待办见 [当前进展](docs/DELIVERY.md)。

## LUT 怎么工作

```text
.cube / 风格数据 → D4 原生表 → App／机身选择 → RAM Hook
                                           ↓
                           原厂写表函数 → ISP → JPEG
```

效果在原厂 JPEG 编码前进入成像链。转换、加载与实拍校色分别维护，见 [LUT 模块](docs/LUT_MOD.md) 和 [校色流程](docs/LUT_CALIBRATION.md)。

实时预览复用原厂 SmartFilter 异步帧处理框架，经 IPCS 执行三维表，切换时将参数加载到 RAM。当前候选已通过 ARM 控制流、RAM 装入和机身菜单模拟；实机像素、颜色和拍照资源交接待验证。原厂滤镜的 RAW 菜单限制仍存在，首次测试使用仅 JPEG。操作见 [预览候选包](docs/SMARTFILTER_CANDIDATE.md)，调用链见 [预览实现](docs/SMARTFILTER_LUT_PREVIEW.md)。

## 目录

| 目录 | 内容 |
| --- | --- |
| `mod/nxconnect/` | Android App |
| `mod/metol/` | Metol 菜单适配、遥控服务与 LUT／配方后端 |
| `mod/quickshare/` | Quick Share 核心与相机 CLI |
| `mod/deployment/` | 启动、启停和菜单脚本 |
| `runtime/` | ARM 适配、RAM 加载器和硬件接口研究 |
| `tools/` | 构建、模拟器、固件分析、部署及色彩转换 |
| `docs/` | 接口、实现和逆向记录 |
| `looks/` | 自制 Hard Mono；其他风格通过导入工具生成 |

## 构建

Android 需要 JDK 17 和 Android SDK 35：

```sh
cd mod/nxconnect/NXConnect
./gradlew :app:assembleDebug
```

Windows 使用 `gradlew.bat`。SDK 路径配置在 `local.properties`，发布签名通过本地 `keystore.properties` 配置。

ARM／模拟器使用 Conda：

```sh
conda env create -f environment.yml
conda activate nx300m-mod
```

复制 `project.example.json` 为 `project.local.json`，配置 NX300M 1.15 rootfs 和匹配 SDK。ARM 构建使用 Clang/LLD 或交叉工具链；模拟器使用 Linux/WSL、QEMU 和 X11/Xvfb，执行原厂 ARM 程序并提供硬件替代接口及回放输入。

## 文档与来源

- [当前进展与待办](docs/DELIVERY.md)
- [LUT 模块](docs/LUT_MOD.md) / [App LUT 库](docs/NXCONNECT_LUT_LIBRARY.md)
- [LUT 实时预览与测试方法](docs/SMARTFILTER_CANDIDATE.md) / [实现与调用链](docs/SMARTFILTER_LUT_PREVIEW.md)
- [遥控与相册](docs/REMOTE.md) / [Quick Share](docs/QUICKSHARE.md)
- [菜单性能](docs/MENU_PERFORMANCE.md) / [手动镜头校正](docs/MANUAL_LENS_CS_RAM.md)
- [安装与启动](docs/INSTALL.md) / [ARM VM](docs/ARM_VM.md)
- [第三方来源与许可](THIRD_PARTY.md) / [源码与素材范围](SOURCE_SCOPE.md)

各组件沿用所在目录的 LICENSE 和 NOTICE；项目原创部分的公开许可证待确定。
