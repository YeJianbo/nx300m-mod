# NXConnect 的 One UI 8.5 / 9 适配

核验日期：2026-10-23。目标是重写 Android 手机端界面；下载、相机协议和照片回写逻辑沿用现有实现。

## 设计依据

- [三星 One UI 设计系统](https://developer.samsung.com/one-ui/index.html)提供布局、导航、图标和动效的通用规则。这些页面没有逐项标注 One UI 9 版本，不能把所有示例都当成 9 的像素规范。
- [Basic layout](https://developer.samsung.com/one-ui/layout/basic.html)：上方为阅读区，操作按逻辑分组，放在容易触及的位置。大标题需要与滚动内容相连，不能靠固定空白把按钮推下去。
- [App bar](https://developer.samsung.com/one-ui/comp/app-bar.html)：大标题可随滚动收起，返回页面保留折叠状态；顶栏只保留少量主要操作。
- [Motion](https://developer.samsung.com/one-ui/motion/basic.html)：用动画表达页面关系；照片交替时避免先完全消失再显示下一张。官方通用基础曲线为 `PathInterpolator(.22, .25, 0, 1)`。
- [One UI 9 产品页](https://www.samsung.com/us/apps/one-ui/)确认新版强调连续导航、保留上下文和多种屏幕形态；它不是可直接导入的 Android 控件库。

用户允许复用 One UI 8.5。实际页面以用户提供的 Galaxy Fit2 Plugin 与两段 Gallery 录屏为主要视觉依据，通用设计文档和 SESL 源码用于补充交互、资源与实现。抽帧位于 `outputs/nxconnect/oneui/reference/`。

## GitHub 选型

| 项目 | 核验结果 | 本项目用途 |
| --- | --- | --- |
| [tribalfs/sesl-androidx](https://github.com/tribalfs/sesl-androidx) | 社区维护的 AndroidX 分支，默认 `sesl-androidx-main` 已列出 SESL9；有 AppCompat、RecyclerView、SwipeRefresh 等；Apache-2.0 | 优先参考控件资源、图标、尺寸和交互实现 |
| [tribalfs/sesl-material-components-android](https://github.com/tribalfs/sesl-material-components-android) | 社区维护的 Material 分支，默认 `sesl/master` 列出 SESL9；包含新版悬浮栏、底栏与面板；Apache-2.0 | 优先参考菜单、栏、详情面板及显隐动效 |
| [tribalfs/oneui-design](https://github.com/tribalfs/oneui-design) | 额外设计组件库，README 声明 One UI 8 / 7 / 6；当前 One UI 8 坐标为 `io.github.tribalfs:oneui-design:0.9.20+oneui8`；MIT | 可参考成熟 8.x 示例，不能将它标成完整 One UI 9 框架 |
| [OneUIProject/oneui-core](https://github.com/OneUIProject/oneui-core)、[oneui-design](https://github.com/OneUIProject/oneui-design) | 默认 core 是 SESL4，design 示例也以 One UI 4 为主 | 旧组件参考，作为本轮基础会与目标版本距离过大 |
| [SimonBumiller/oneui-compose](https://github.com/SimonBumiller/oneui-compose) | 面向 Jetpack Compose 的第三方实现 | 当前项目是 XML / Views；本轮不为使用它改写业务与整个渲染栈 |

SESL 是社区适配库，不是三星向第三方发布的官方完整相册 SDK，也不含本项目的网络相册业务。

上述仓库以 SESL8 / SESL9 等版本划分，没有将独立的“8.5 完整相册框架”列为产品。允许复用 8.5 不表示把任意 8.x 组件改名后就达到了 8.5 / 9 的全部效果。

## 接入限制与当前选择

仓库文档要求通过 GitHub Packages 下载并提供 `read:packages` 凭据。无凭据请求上述设计库 POM 返回 HTTP 401。它不能像普通 Maven Central 依赖一样直接加入当前工程。Maven Central 的补充探测遇到本机 Python 证书过期错误，未据此推断中央仓库一定没有该包。

核对 SESL9 当前源码：Material 项目使用 compileSdk 37、AGP 8.13.1、Kotlin 2.2.20、Java / JVM 24；AndroidX 项目的版本目录使用更新的 AGP。NXConnect 当前为 compileSdk 35、AGP 8.7.3、Java / JVM 17。直接替换时还必须按上游文档排除对应 AndroidX / Material 模块，防止重复类。上游 README 的通用最低要求不能替代当前源码的真实构建配置；发布 AAR 的兼容性仍需独立验证。

本轮在现有 Views 工程内移植可独立使用的资源与交互，保留资源来源和许可证；共享主题、图标、菜单、动效统一。完整 SESL runtime 尚未作为 Gradle 依赖接入。后续若整套迁移，先验证一组配套版本的依赖和编译兼容，不混用 SESL8 与 SESL9 核心模块。

## 落地规则

- 首页底色取录屏近似 `#F2F2F2`，功能组近白；相册和查看器采用中性 `#FCFBFE`。录屏有压缩，颜色是参考值，不作为三星官方色值声明。深色使用语义颜色切换。
- 首页：上方居中显示由三星官方 NX300 产品照片编辑的透明相机图与型号，来源和编辑说明见 `mod/nxconnect/NXConnect/NOTICE.md`。阅读区随滚动退出，查找工具栏吸顶；松手后标题只停在展开或收起位置。功能组接近全宽、组间距紧凑；连接提示右下为连接操作，相册与遥控横向分组，下方为独立相机设置和相机状态。关于相机、Mod 与固件、连接帮助各有独立页面。
- 相册：居中展开标题，默认手机照片四列、相册封面三列。日期行与照片间隙紧凑。标题完全收起后，照片到达状态栏下方，搜索和更多移到右上悬浮胶囊；底部照片／相册／收藏／菜单采用悬浮胶囊及灰色椭圆选中底。多选切换到计数、全选和批量操作。日期取真实文件信息。
- 底部菜单采用图标操作面板，顶部更多保持锚点菜单；仅展示已有真实功能。浮动容器使用半透明主题色与轻影，当前没有实时背景模糊。
- 查看器：左上独立返回圆钮、右上轻量工具胶囊、底部细缩略图带与悬浮操作栏。计数提供给无障碍描述。上滑时照片连续上移并露出日期、文件路径、相机和曝光文字，完整 EXIF 属性可展开。单击隐藏工具栏时背景转黑，再次单击恢复主题背景。保留已有编辑、首次备份、同步及恢复业务。
- 详情中的镜头、实际与等效焦距直接展示，只有完整 EXIF 属性折叠。相机标题旁放细边格式胶囊，文件大小、图像尺寸、曝光和焦距各占一行；浏览状态右上也显示格式胶囊，随工具栏显隐，避免和详情中的标识重复叠加。
- 详情字体按用户提供的三星相册截图保留明确字重：日期、相机名14sp粗体，镜头名13sp粗体，参数与文件信息12sp常规字重，辅助说明11sp。首页使用与相册一致的 RecyclerView 滚动容器，标题收起行程224dp，保留原生拖动、惯性和两端吸附；首页不叠加边缘拉伸。
- 动效随用户操作触发，包括照片进入/退出、选中、详情展开和工具栏显隐。SESL9 `FloatingAnimationConfig` 的浮动栏 alpha 为 150ms，短版为 80ms；这些数值仅用于相应栏的显隐，不能推广成所有动画的时长。
- 动画取消后不能由旧回调隐藏新页面或遗留零高度；系统关闭动画时直接完成状态转换。
- 普通弹窗由统一组件设置最终的中性色背景、底部位置、12dp 外边距、26dp 圆角和等宽文字按钮；表单使用下划线输入栏。页面卡片统一24dp圆角，选择通过中性底色与强调文字表达。设置与下载面板复用面板尺寸，不叠加厚描边。设置面板使用嵌套滚动列表，先回到内容顶部，继续下拉关闭；面板没有半展开停靠点，位移由原生行为控制。
- 标题拖动、惯性与两端吸附由原生 `scroll|snap` 统一处理；不在 nested scroll 停止时再调用 `setExpanded`，不把真实抬手改成 `CANCEL`。工具栏保留标题控件，阈值处只改变显示与无障碍状态，避免重复重新布局。
- 查看器入场与退出分开管理；返回取消未完成手势和详情拖动，退出完成独立于可被取消的动画回调，避免页面卡在退出状态。
- 从相册进入查看器时携带实际缩略图位置，图片由来源裁切框展开；同一照片返回时缩回原位置。翻到其他照片或屏幕旋转后使用短回退，避免沿用失效坐标。

具体参考源码：[尺寸](https://github.com/tribalfs/sesl-material-components-android/blob/sesl/master/lib/java/com/google/android/material/oneui/res/values/dimens.xml)、[显隐动画](https://github.com/tribalfs/sesl-material-components-android/blob/sesl/master/lib/java/com/google/android/material/oneui/floatingactioncontainer/FloatingAnimationConfig.kt)、[AppCompat 图标](https://github.com/tribalfs/sesl-androidx/tree/sesl-androidx-main/appcompat/appcompat/src/main/res/drawable)。
