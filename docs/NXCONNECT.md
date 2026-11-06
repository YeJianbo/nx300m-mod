# NXConnect Android

NXConnect 连接 NX300M 的服务，提供相册、遥控、相机设置和 LUT 管理。源码位于 `mod/nxconnect`，基于 Katsiy/NXConnectForNX500；许可证及署名见该目录的 NOTICE。

## 连接

在 App 中选择相机 Wi-Fi，按系统提示确认后自动识别服务，也可手动输入相机地址。0.20.35 修复 Android 11+ 添加网络成功后没有确保实际连接的问题：先检查所选 SSID 和 IPv4，尚未关联时主动发起系统连接请求；Android 10 沿用系统请求网络入口。HTTP 请求使用相机对应的 Network，绕过系统 HTTP 代理。系统保存、真实 WLAN 关联及相机服务可达分别判断。

[下载 NXConnect 0.20.35 APK](https://github.com/YeJianbo/nx300m-mod/releases/download/v0.1.0-preview.3/NXConnect-NX300M-v0.20.35.apk)。可覆盖安装旧版。release 构建、签名及 9 项软件服务识别检查通过，真实手机关联仍待验证。相机需先进入原厂遥控取景器。

用户报告的“直接连接失败、相机相关界面卡住且无法关机”仍未关闭。当前相机 MOD 的手机页只显示网络信息，没有开启独立热点的功能；普通拍摄界面下持续联网传图尚未接通。现有链路仍依赖原厂 Remote Viewfinder。实现、历史测试和缺失证据见 [连接核查](NXCONNECT_CONNECTION_STATUS_2026-11-06.md)。

## 相册与信息

相册支持浏览、批量选择、查看详情、下载和分享，RAW 与 JPEG 可配对显示。镜头预设可填写镜头型号和焦距，并写入 JPEG 或兼容的 SRW 副本。具体写入和恢复方式见 [手动镜头](MANUAL_LENSES.md)。

## 遥控与设置

原厂 Remote Viewfinder 可向 App 提供实时画面。已有实机反馈显示点击快门后会断开，遥控拍摄和 RAW 输出需要继续联调。App 也提供机身按键、曝光和配方等设置入口；可用项目取决于相机端已加载的服务和模块。

## LUT

App 可选择相机上已部署的 LUT 并调整强度，也可在手机上导入、整理和导出 cube 文件。手机导入的 LUT 直接上传到相机的流程尚未接通；见 [手机 LUT 库](NXCONNECT_LUT_LIBRARY.md)。

## 构建

Android 工程位于 `mod/nxconnect/NXConnect`。构建说明见 [App README](../mod/nxconnect/README.md)，功能进展见 [当前进展](DELIVERY.md)。固件及安装包页面目前提供本地文件检查。
