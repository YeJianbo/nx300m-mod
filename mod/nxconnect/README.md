# NXConnect NX300M 适配

基于 Katsiy/NXConnectForNX500 的 Android 客户端，许可证及原作者声明见 [NOTICE](NOTICE) 和 [LICENSE](LICENSE)。

当前版本 **0.20.29 / 77**。包含相册、连接与 NFC 配对入口、屏幕镜像、原厂 Remote Viewfinder 客户端、相机设置、相机 LUT 选择及手机本地 LUT 库管理。功能是否可用还取决于机身当前模式、原厂协议能力及相机端 Mod 服务。

```sh
cd NXConnect
./gradlew :app:assembleDebug
```

需要 JDK 17 和 Android SDK 35。Windows 使用 `gradlew.bat`。相机端能力与未验证项见仓库根目录的 `docs/NXCONNECT.md`、`docs/NXCONNECT_LUT_LIBRARY.md`。

没有包含签名密钥、口令、个人照片或手机截图。`tools/publish.ps1` 旧发布辅助脚本未纳入此源码快照，避免沿用其旧仓库目标及强制推送行为。
