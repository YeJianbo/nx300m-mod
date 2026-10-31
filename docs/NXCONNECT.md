# NXConnect Android

NXConnect 连接 NX300M 的服务，提供相册、遥控、相机设置和 LUT 管理。源码位于 `mod/nxconnect`，基于 Katsiy/NXConnectForNX500；许可证及署名见该目录的 NOTICE。

## 连接

手机连接相机 Wi-Fi 后，在 App 中搜索或手动输入相机地址。App 提供 WLAN 自动连接和 NFC 配对入口；手动连接及相册传图已有实机反馈，自动连接仍需解决断线问题。

## 相册与信息

相册支持浏览、批量选择、查看详情、下载和分享，RAW 与 JPEG 可配对显示。镜头预设可填写镜头型号和焦距，并写入 JPEG 或兼容的 SRW 副本。具体写入和恢复方式见 [手动镜头](MANUAL_LENSES.md)。

## 遥控与设置

原厂 Remote Viewfinder 可向 App 提供实时画面。已有实机反馈显示点击快门后会断开，遥控拍摄和 RAW 输出需要继续联调。App 也提供机身按键、曝光和配方等设置入口；可用项目取决于相机端已加载的服务和模块。

## LUT

App 可选择相机上已部署的 LUT 并调整强度，也可在手机上导入、整理和导出 cube 文件。手机导入的 LUT 直接上传到相机的流程尚未接通；见 [手机 LUT 库](NXCONNECT_LUT_LIBRARY.md)。

## 构建

Android 工程位于 `mod/nxconnect/NXConnect`。构建说明见 [App README](../mod/nxconnect/README.md)，功能进展见 [当前进展](DELIVERY.md)。固件及安装包页面目前提供本地文件检查。
