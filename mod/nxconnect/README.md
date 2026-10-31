# NXConnect NX300M 适配

基于 [Katsiy/NXConnectForNX500](https://github.com/Katsiy/NXConnectForNX500) 改造，当前版本 **0.20.29 / 77**。

## 相比上游的主要改动

- 接入 NX300M 统一服务的发现、能力识别、设置和 Mod 管理接口。
- 重做 One UI 8.5 / 9 风格界面，统一折叠标题、弹框、浮动栏、详情排版、图标与动画。
- 相册增加 RAW＋JPEG 配对、批量选择、大图预览、缩略图条、下载与分享。
- 增加镜头预设和元数据编辑，将镜头信息写入 JPEG／兼容 SRW 副本。
- 接入原厂 Remote Viewfinder 取景、对焦与快门协议，以及 Mod 屏幕镜像和机身按键。
- 增加 WLAN 连接、NFC 配对入口和被动断线状态更新。
- 集中管理曝光、拨盘、配方、Picture Wizard、RGB 和相机 LUT。
- 增加离线 LUT 库：cube 批量导入、重命名、排序、导出与删除。
- 增加相机模块状态、启动配置和本地 Mod／固件文件检查。

手机 LUT 库和相机已部署 LUT 分为两个页面。待办与验证进展见 [当前进展](../../docs/DELIVERY.md) 和 [LUT 库](../../docs/NXCONNECT_LUT_LIBRARY.md)。

## 构建

需要 JDK 17 和 Android SDK 35：

```sh
cd NXConnect
./gradlew :app:assembleDebug
```

Windows 使用 `gradlew.bat`。SDK 路径和发布签名通过本地配置文件提供。

许可与原作者声明见 [NOTICE](NOTICE) 和 [LICENSE](LICENSE)。
