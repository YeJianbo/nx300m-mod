# NX300M Mod / NXConnect

三星 NX300M 的相机端 Mod、Android 客户端，以及原厂 ARM 程序运行与逆向研究工具。

## 项目组成

| 目录 | 内容 |
| --- | --- |
| `mod/nxconnect/` | NXConnect Android 客户端：相册、连接、遥控、相机设置、LUT 库管理 |
| `mod/metol/` | Metol 菜单适配、统一遥控服务及 LUT / 配方后端 |
| `mod/quickshare/` | 基于 rquickshare 的无界面传输核心与相机命令行接口 |
| `mod/deployment/` | SD 卡启动、服务启停和菜单入口脚本 |
| `runtime/` | ARM 运行时适配、RAM LUT 加载器、诊断和实验性硬件接口 |
| `tools/` | 构建、固件只读解析、模拟器、部署及校色工具 |
| `docs/` | 功能边界、接口、逆向证据和安装研究 |
| `looks/` | 自制 Hard Mono 测试风格；其他风格数据需自行提供 |

## 当前状态

NXConnect 为 **0.20.29 / 77**。App 提供相机 LUT 选择与强度设置，以及手机本地 cube 的导入、重命名、排序、导出和删除。手机本地导入尚不等于上传安装到相机。

相机端已有免刷机 RAM LUT 加载实现。原厂取景、成片 JPEG、RAW、遥控拍摄是不同验证范围；不能以接口返回成功或模拟器通过代替实拍效果。遥控快门断链及部分机身菜单显示问题仍需继续验证。

模拟器运行原厂 ARM 用户态程序，硬件接口包含替代实现和显式回放输入，不能保证完整 ISP 等效、实体机开机或刷机安全。安装说明和历史实验仅供开发参考，仓库不提供可直接刷写的固件。

## Android 构建

需要 JDK 17、Android SDK 35；Gradle Wrapper 随源码保留。

```sh
cd mod/nxconnect/NXConnect
./gradlew :app:assembleDebug
```

Windows 使用 `gradlew.bat`。本地 SDK 路径写在该目录的 `local.properties`，不要提交。正式签名材料自行生成；仓库没有发布密钥或口令。

## ARM / 模拟器开发

```sh
conda env create -f environment.yml
conda activate nx300m-mod
```

复制 `project.example.json` 为 `project.local.json`，配置自行获取的 NX300M 1.15 原厂 rootfs 和匹配 SDK。ARM 构建另需 Clang/LLD 或对应交叉工具链；模拟器还需 Linux/WSL、QEMU 和 X11/Xvfb。各工具的输入和依赖见相关文档。

- [功能与部署边界](docs/DELIVERY.md)
- [LUT 模块](docs/LUT_MOD.md) / [App LUT 库](docs/NXCONNECT_LUT_LIBRARY.md)
- [遥控服务](docs/REMOTE.md) / [Quick Share](docs/QUICKSHARE.md)
- [菜单性能](docs/MENU_PERFORMANCE.md)
- [手动镜头校正调查](docs/MANUAL_LENS_CS_RAM.md)
- [参考项目源码核对](docs/NX300_UPSTREAM_LUT_REVIEW.md)

## 源码范围与许可

本仓库保存经过筛选的源码快照，不包含个人照片、相机固件、rootfs、内存转储、Android 签名材料、个人 Lightroom 配置及旧 Git 历史。缺失的专有依赖必须自行合法获取；依赖本地数据的研究工具不能在空仓库中直接运行。

各组件保留自己的许可证和作者署名，见 [THIRD_PARTY.md](THIRD_PARTY.md)。尚未统一选择根目录原创代码的公开许可证，不能把本仓库整体视为已按单一许可证发布。具体排除项和公开前剩余事项见 [SOURCE_SCOPE.md](SOURCE_SCOPE.md)。
