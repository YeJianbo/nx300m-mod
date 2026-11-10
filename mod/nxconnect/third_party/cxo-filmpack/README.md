<div align="center">

# CXO FilmPack

### 从 RAW 开始，把照片留在胶片里。

Android 胶片照片编辑器 · RAW 显影 · 颗粒 · 后处理

[](https://github.com/Macchen666816/CXO-FilmPack/releases/latest)


[](LICENSE)

**[下载 1.0.1](https://github.com/Macchen666816/CXO-FilmPack/releases/tag/v1.0.1)** · [English](README.en.md) · [更新记录](docs/releases/1.0.1.md) · [反馈问题](https://github.com/Macchen666816/CXO-FilmPack/issues)

</div>

在 Android 上给照片加入胶片色彩与颗粒。从相机 RAW 开始显影，也可以直接编辑 JPEG、PNG 和 HEIC。照片在设备上处理，无需上传。

## RAW 效果展示

以下对照由 CXO 的实际 RAW 处理管线生成。每组使用同一张 RAW，依次展示 **Original、Kodak Portra 400、Kodak Elite Color 400**；胶片强度为 100，未额外添加颗粒或后处理。

### Panasonic RAW · P1176257.RW2



### Nikon RAW · DSC_3271.NEF



### Sony RAW · DSC07319.ARW



### 编辑界面

<p align="center">
  
</p>

## 可以做什么

- **胶片色彩**：153 项胶片与风格，支持搜索、收藏、横向切换和强度调整。
- **RAW 显影**：保留相机线性数据，结合机身标定、白平衡、色彩矩阵与 RAW 胶片曲线处理。
- **Apple ProRAW**：使用 DNG 内嵌 profile，进入独立的线性显影路径，再接 RAW 胶片核心。
- **颗粒与后处理**：99 种内置颗粒，可调整曝光、对比度等参数，并加入边缘效果。
- **预览与比较**：实时调整、按住比较原图、捏合缩放、双击复位。
- **保存**：JPEG（质量 1–100）或无压缩 RGB8 TIFF，可保留原尺寸或缩小输出。

照片在设备上处理，不需要上传到服务器。

## 安装

在 [Releases](https://github.com/Macchen666816/CXO-FilmPack/releases/tag/v1.0.1) 下载 **1.0.1** 的 APK，允许安装来自该来源的应用后打开。已安装正式签名版的用户可直接覆盖升级，保留应用数据。

建议在内存和存储空间充足时处理高像素 RAW。首次解码通常比后续切换胶片耗时，底部会显示处理进度、耗时和错误信息。

## 格式与兼容性

| 输入 | 支持情况 |
| --- | --- |
| JPEG / PNG | 直接进入照片编辑流程 |
| 相机 RAW | 需要 LibRaw 支持该文件模式，并具有对应机身和胶片适配规则 |
| Apple ProRAW | 已验证 iPhone 15 Pro Max 的三通道 Linear DNG；当前不支持 JPEG XL、HueSat/Look 表及三光源 profile |
| HEIC | Android 9.0 及以上尝试系统解码，内部转换为 8 位 sRGB JPEG 后处理 |

已进行实拍导入与导出验证的相机包括 Panasonic S1RII / S9、Sony A7 IV 和 Nikon Z6 II。标定目录可查询 584 个机身 profile，具体文件仍受压缩模式、位深和适配规则限制。缺少适配时会提示暂不支持。

导出为 SDR sRGB；TIFF 为 8 位 RGB，不提供 16 位或 HDR 输出。153 项胶片与风格中有 7 项实验模型。

## 1.0.1 更新

- 修复 Apple ProRAW DNG 无法导入的问题。
- 改为 ProRAW 线性显影与 RAW 胶片路径，修正导入和选胶片后的明显色差。
- 精简界面说明文字，保留进度、耗时和错误显示。

K40 和 ARM64 模拟器已完成导入、胶片、颗粒、后处理、JPEG / TIFF 保存与取消处理回归。IMG_3046 的 Original 与 Portra 400 对照原版 DxO，在同坐标、共同 sRGB 空间中的通道 RMSE 分别为 0.30 和 0.39 / 255。完整方法、样本范围与剩余差异见 [1.0.1 技术记录](docs/1.0.1-Apple-ProRAW.md)。

## 构建与项目结构

项目使用 Kotlin、Android NDK 和 CMake。推荐使用 Android Studio 自带 JBR，SDK / NDK 版本以 Gradle 配置为准。

首次构建前，从 Release 下载 `native-grain8-v1.cxograinpack`，放到项目根目录的 `releases/` 下。该资源包不存入 Git。

```sh
# 日常开发，无需正式签名密钥
./gradlew :app:assembleDebug

# 核心验证
./gradlew :core:check

# 正式版：先配置自己的签名
./gradlew :app:assembleStable
```

| 目录 | 内容 |
| --- | --- |
| `app` | 编辑界面、导入与保存、RAW / HEIC 桥接及原生代码 |
| `core` | 胶片、RAW、颗粒、色彩管理与后处理算法 |
| `android-adapter` | Android 图像与平台接口 |
| `glass-catalog` | 玻璃界面组件 |
| `docs` | 技术记录与演示图片 |

正式版输出位于 `app/build/outputs/apk/stable/app-stable.apk`。签名配置见 [发布说明](release/README.md)；密钥与密码不随仓库提供。`stable` 是唯一正式发布线，`preview` / `publicBeta` 已归档。

部分数值验证依赖开发机上的原版对照证据，克隆仓库后执行完整 `:core:check` 需要对应研究数据；应用构建不要求运行这些验证。

## 协议与致谢

本项目原创代码和材料采用[非商业使用协议](LICENSE)，**禁止未经授权的商业使用**。这是源码可查看的非商业许可，不属于 OSI 开放源代码许可。第三方依赖和资源仍遵循各自协议，详见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)。

CXO FilmPack 是基于 DxO FilmPack 8 逆向研究的独立 Android 项目，与 DxO 无隶属关系。胶片名称、商标和第三方资源归各自权利人所有。

使用了 LibRaw、Adobe DNG SDK、libjpeg-turbo、Little CMS 及 Backdrop 等项目。This software is based in part on the work of the Independent JPEG Group.
