# 第三方来源与许可

本项目使用的组件、来源和许可如下，各目录保留 LICENSE、NOTICE 与源码署名。

| 组件 | 来源 | 许可与修改 |
| --- | --- | --- |
| Metol 适配 | [WApaperplane/metol](https://github.com/WApaperplane/metol)，`ddbe8b119e046798e557add13837834f5e8d90ac` | AGPL-3.0；NX300M UI、LUT 后端及服务集成见 `mod/metol/PORT.md` |
| NXConnect | [Katsiy/NXConnectForNX500](https://github.com/Katsiy/NXConnectForNX500)，`4597f4483ffb544a444160bfb07930c767bc8f69` | AGPL-3.0-or-later；NX300M 连接、相册、遥控、One UI 风格界面与 LUT 管理适配；见 `mod/nxconnect/NOTICE` |
| Quick Share 核心 | [Martichou/rquickshare](https://github.com/Martichou/rquickshare)，`378d8ae969941bee4bf60ad34ac9cf8bb7005eb7` | GPL-3.0；保留修改后的 `core/`、CLI 和 NOTICE，不包含原始检出副本或桌面应用 |
| jsmn | [zserge/jsmn](https://github.com/zserge/jsmn)，`25647e692c7906b96ffd2b05ca54c097948e879c` | MIT；许可见 `mod/metol/vendor/jsmn-LICENSE` |
| SESL 图标 | [tribalfs/sesl-androidx](https://github.com/tribalfs/sesl-androidx) | Apache-2.0；改编范围见 NXConnect NOTICE，许可副本位于其 `licenses/` |
| Gradle Wrapper | [Gradle](https://github.com/gradle/gradle) | Apache-2.0；保留 wrapper 启动文件，不包含 Gradle 分发包 |

Nuklear 相关声明见其源文件及 Metol 上游。Cargo / Gradle 依赖按各自许可证获取，不随本仓库打包依赖缓存。


Samsung 固件、SDK、工厂标定、Adobe / 第三方相机配置及由其导出的风格数据不在源码快照中。App 中相机产品图仅用于说明兼容机型，原始权利不因放入仓库而改变，公开前需另核实图片授权或换成自绘素材。

Metol 历史血缘见 `mod/metol/ATTRIBUTION.md`。项目公开准备事项集中记录在 [SOURCE_SCOPE.md](SOURCE_SCOPE.md)。
