# CXO FilmPack

# CXO FilmPack 0.7.3-preview（当前）

versionCode30。导出前增加设置菜单：JPEG／TIFF、JPEG质量1–100（默认98）、原始尺寸／最长边预设／自定义。保持比例，只缩小不放大；先按源尺寸生成胶片、颗粒和后处理，再面积缩小。TIFF明确为8位RGB sRGB无压缩无损，不是16位。

最终非调试preview：模拟器20项、K40 14项通过，均安装相同固定包且hash一致。含真实颗粒与后处理缩小对照、方向、取消清理、菜单；S9 RAW保存与独立面积参考在模拟器通过，K40本轮无新增RAW整图验收。缩小仍需要源尺寸计算，不承诺同比加速。

包：CXO-FilmPack-0.7.3-preview-export-options-20261105.apk，276,859,624bytes，SHA256 4d14ef37f0e6e7b09f8db175c1d9a8a4229f3d39bac72d11c91b613d8e65cd1d。

Obsidian《CXO FilmPack 0.7.3 导出格式、质量与分辨率》及 evidence/export-options-20261105/REPORT.md 记录用法、颗粒关系和验收。颜色资产及DxO算法恢复范围沿用之前版本。

OPPO最新补验：同一0.7.3固定包，33次工作流、13项功能检查通过。24MP／44MP RAW保存中位数3.229／5.918s，51MP JPEG9.987s；同图无颗粒输出与0.5.6一致。颗粒准备、后处理、缩小及TIFF开销分别见Obsidian《CXO FilmPack 0.7.3 OPPO性能复测与导出开销》与研究目录evidence/oppo-performance-20261105/REPORT.md。

---

# CXO FilmPack 0.7.2-preview（历史）

versionCode29。胶片强度与后处理接入拖动中快速帧、松手精细预览；缓存胶片／颗粒底图，合并连续请求。后处理改为内联折叠分组，颗粒弹窗支持实时临时变化、取消回滚。快速帧最长边640，导出仍走原尺寸完整管线。

最终非调试preview：模拟器25项、K40 6项通过，均安装相同固定包且hash核对一致。K40受控1200×800图，后处理／强度首帧187／262ms；不是任意照片固定帧率。RAW缓存后处理在模拟器测试通过，K40本轮未新增RAW性能测量。

包：CXO-FilmPack-0.7.2-preview-live-adjustments-20261105.apk，276,843,240bytes，SHA256 bfc7c2be6879f4c87b87212f980eee795390b5d2c1e098bc9daa4aba6070f466。

Obsidian《CXO FilmPack 0.7.2 实时预览与折叠后处理》与研究证据 evidence/live-preview-20261105/REPORT.md 记录使用、实现和限制。本轮未扩大DxO算法等值范围，漏光完整几何／空间／Toning仍待恢复。

---

# CXO FilmPack 0.7.1-preview（历史）

code28。漏光改原版mode6 Linear Light、直通RGBA、采样后灰度、LCG旋转，移除猜测镜像。原版22组540672通道与Kotlin零差；完整affine／空间／Toning未闭环，基础冲洗仍独立实现。

K40和ARM64 API33模拟器已安装相同最终包且hash一致。模拟器6个不同debug用例、同6项preview重验通过；K40本轮检查安装和启动，无完整照片／性能复测。

CXO-FilmPack-0.7.1-preview-native-edge-blend-20261105.apk，276,826,852bytes，SHA256 67f0bfd323474d42908d5102529765007aef893698a988c59faa0cc5c4fd5168。Obsidian《CXO FilmPack 0.7.1 原版漏光混合恢复与验证》、evidence/edge-blend-native-20261105/REPORT.md记录范围。

---

# CXO FilmPack 0.7.0-preview（历史）

versionCode27。独立后处理模块：基础冲洗、4种曲线预设、46项边缘缺陷（23张原版PNG的彩色／白色变体）。冲洗为CXO独立输出sRGB实现，漏光合成仍近似，非DxO完整冲洗数值恢复。

`CXO-FilmPack-0.7.0-preview-post-processing-20261105.apk`，276,826,856字节；SHA-256 `3368288db82a593f843b805be20b47dbd80747310698297c33e63265953e61c4`。K40和ARM64 API33模拟器安装hash一致。模拟器debug10个不同用例通过，最终preview产品3项通过；K40本轮仅面板点击／截图及安装核对，无完整照片／性能复验。

详情见Obsidian《CXO FilmPack 0.7.0 后处理与边缘缺陷首版》与研究证据 `evidence/post-module-20261105/REPORT.md`。

---

> 历史交付：**0.6.6-preview / versionCode26**。新增胶片详情入口，97项原版中文介绍，56项暂无正文。K40和模拟器已安装最终preview；模拟器四项交互回归通过，K40实际详情点击及截图检查通过。

> 历史交付（2026-11-05）：**0.6.5-preview / versionCode25**。修复搜索键盘、列表越界重叠和颗粒尺寸慢拖。K40与模拟器已安装最终preview，K40实际输入和慢拖通过，安装hash一致；模拟器三项交互回归通过。主题／照片处理保留，未做新RAW色彩全量验收。

固定包：`releases/CXO-FilmPack-0.6.5-preview-search-grain-fix-20261105.apk`，SHA-256 `ddf61719253f3757050c2a457e4788bd905a155ea245549d412c231967fdb584`。Obsidian《CXO FilmPack 0.6.5 搜索输入与颗粒慢拖修复》及研究证据`evidence/ui-input-fix-20261105/REPORT.md`。下文为历史快照。

> 历史交付（2026-11-05）：**0.6.4-preview / versionCode24**。深棕复古主题、暖金玻璃控件和轻微静态纹理；照片预览底色保持中性，原有抽屉避让、紧凑顶栏与胶片菜单保留。K40和模拟器已安装最终非调试preview，K40安装hash一致。虚拟机两项CatalogInteractionTest通过，K40本轮只做启动和面板显示检查。

固定包：`releases/CXO-FilmPack-0.6.4-preview-warm-umber-20261105.apk`；SHA-256 `5c967eabfa24ea5f9e3d33f493420cc2e0a40050f01082d483507a184c911c7d`。详情见Obsidian《CXO FilmPack 0.6.4 深棕复古主题与K40安装》，证据在研究目录`evidence/ui-umber-20261105/`。下文为历史版本快照。

> 历史交付（2026-11-04）：**0.6.2-preview / versionCode22**，ARM64虚拟机已安装非调试preview；K40断开，最终包未复验。修复向内捏回与双击复位，玻璃按压留白及裁切；153项横向缩略图与传统搜索菜单双向联动，按视口生成真实缩略图。删除重复胶片入口、现有底栏三项和下半区大外框，颗粒动作固定底部。99种颗粒继续内置。

虚拟机14项debug回归、最终preview 8项通过，覆盖重叠不累加。照片数值与资源保持，缩放仍基于已有预览，旧API未新增验收。

固定包：`releases/CXO-FilmPack-0.6.2-preview-linked-films-20261104.apk`，195,863,551字节；SHA-256 `3a1b76af3ce09e7bffd084ba136014a8bd238e904d3e815b71cb575dcf48bc14`。详情见Obsidian《CXO FilmPack 0.6.2 缩放修复与全胶片联动菜单》及研究目录`evidence/ui-refinement-20261104/REPORT.md`。下文保留历史状态。

> 历史交付（0.6.1／2026-11-04）：**versionCode21**，K40与ARM64虚拟机均安装非调试preview。原版Backdrop Catalog的Buttons、Toggle、Slider、Bottom tabs及其交互动画直接接入；照片改为500ms静止长按比较、1–8倍双指缩放和拖动，原图按钮删除。99种颗粒继续内置。

新增:glass-catalog本地模块，5个组件与3个交互工具文件逐字节保持；Compose1.12.0，Kotlin／Compose编译插件2.4.10。core:check通过，处理管线与颜色资产未改。两台各12项debug回归、最终preview两台各2项通过，覆盖重叠不累加。缩放是已有预览的显示变换，尚无原尺寸分块解码；旧API未新增运行验收。

固定包：`releases/CXO-FilmPack-0.6.1-preview-catalog-gestures-20261104.apk`，195,863,551字节，SHA-256 `d44746bd898cc13fe5fbc732d5e9c26fefbcd289b5a0aa7c985030d2b1bd54af`。使用与边界见Obsidian《CXO FilmPack 0.6.1 原版玻璃组件与预览手势》，完整证据：`evidence/ui-components-20261104/REPORT.md`。以下按版本保留历史状态。

> 历史交付（0.6.0／2026-11-04）：**versionCode20**。K40和ARM64虚拟机均已安装非调试preview包。99种颗粒随APK内置自动准备；暗色编辑工作台、真实胶片缩略图、搜索／收藏、按住对比、横屏与底部颗粒设置已接入。API33真实AGSL玻璃来自本地AndroidLiquidGlass算法适配；API26–32提供渐变回退，未做旧API运行验收。

固定包：`releases/CXO-FilmPack-0.6.0-preview-glass-bundled-grain-20261104.apk`（189,868,308字节）。SHA-256：`079ad3a2e72fdd3ff5bda870041548947f3085d44a5cd8c82dadf7993ae74c41`。沿用debug签名便于升级，不是正式release发布。K40 11项、虚拟机7项debug回归及最终preview两台各1项界面验收通过，测试存在重叠。

当前RAW已进入机身标定的camera-linear链，缺机身／胶片规则明确拒绝；585个profile完成getter核验，584个可由机型别名查询，不代表584台真实RAW实拍验收。本轮数学核心与原1,531份assets未改；0.5.6性能数字没有在新UI重测。Obsidian《CXO FilmPack 0.6.0 液态玻璃界面与内置颗粒》提供使用说明；研究证据：`evidence/ui-glass-20261104/REPORT.md`。下文按日期保留历史状态。

非调试构建／匹配测试包：

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' ./gradlew --offline -PcxoTestBuildType=preview :app:assemblePreview :app:assemblePreviewAndroidTest :app:lintPreview
```

> 历史交付（2026-11-03）：0.5.1-preview / versionCode14已安装K40。修复Sony ARW在入口被跳过的问题；实际A7 IV的DSC01452.ARW导入、竖拍颗粒预览、原尺寸保存及松下回归5项通过。A7 IV目前用LibRaw→sRGB16基础显影，DxO机身专属色彩尚未接入。松下S1RII／S9的153项0–200 RAW链与code13验收保留。固定包 releases/CXO-FilmPack-0.5.1-preview-arw-import-20261103.apk；证据 evidence/sony-arw-import-20261103/REPORT.md（研究目录），Obsidian《CXO FilmPack Sony ARW导入修复与基础显影边界》。下文各日期章节为历史快照。

以Kotlin为主的Android胶片引擎重建项目，目标覆盖DxO FilmPack 8全部胶片。当前153项色彩类型＝104传统胶片（97固定＋7实验）＋43数码＋6电影；99种颗粒资源从0.6.0起随APK内置。工程运行不依赖原版macOS动态库。

## 最近真机交付（历史）：0.5.0-preview

新增真实Panasonic RW2输入：K40上S1RII 6张14-bit、S9 1张12-bit成功显影、胶片预览／比较及原尺寸JPEG保存。LibRaw0.22.3显影到编码sRGB16磁盘缓存，直接接现有胶片入口，导出最终才量化8位；预览仍为8位缩图。采用成熟替代显影，不声称DxO RAW色彩已对齐，既有RGB验收不自动扩大为RAW。

S1RII有效裁切8144×5424，S9为6000×4000；竖拍保存方向EXIF。首次显影＋普通预览约4.00–4.25／2.37秒，切换胶片复用底图。三款代表无颗粒RAW导出约15.5–18.1秒，S9 Portra400带颗粒17.15秒，均为K40私有缓存测量。内存预检及输入／像素限制已接入，但LibRaw仍有全图原生内存，不宣称低内存设备全面支持。

完整core38项verify、四ABI构建、lint0错误12警告通过；4项RAW管线＋18项界面／旧功能回归通过，4份RAW导出经Pillow完整解码。包versionCode8、55,528,093字节；SHA256 `fd326d36d6d4dfb7a83dc544498a280391d1e1618f0f15ac1b2d7c4369675b8c`。同签名覆盖安装，99颗粒包保留，模拟器未更新。源码许可包见releases/CXO-FilmPack-0.5.0-LibRaw-source.tar.gz，CDDL1.0选择及声明随APK分发，对外分享APK需一起提供对应源码。

Obsidian入口《CXO FilmPack 0.5 RAW首版与松下RW2验收》；研究证据 `/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-decoder-20261102/REPORT.md`。K40“下载／CXO-RAW-Samples-0.5.0”已有两机3张试用副本。下一阶段先按实际RAW照片里的亮度、白平衡、高光和稳定性问题迭代，性能专项后置。

## 历史交付：0.4.2-preview

K40／骁龙870／API33已覆盖安装versionCode7。DSCF8671.JPG（8256×6192）、Fujifilm黑白红滤镜100／无颗粒：原生四线程11.170／11.256秒，最终安装包复核11.284秒，预览render约1.04秒。本轮纯Kotlin四线程19.139／19.179秒；六线程在两种后端下均慢约9%，继续默认四线程。计时为私有缓存导出，不代表任意机型、胶片或文件提供器。

应用主体保留Kotlin，新增ARM64 NEON入口插值和libjpeg-turbo流式编码。仅换入口、保留旧JPEG时完整51MP输出逐字节一致；启用新JPEG后编码字节变化，Pillow解码新旧JPEG差异RMSE0.3436/255、最大4码。继续原尺寸、JPEG98／4:4:4及方向EXIF，没有GPU。其它ABI及加载不可用时回退Kotlin；其它ABI尚无运行验收。

修复51MP开启颗粒后预览失败：level0..2保持，预算提高到4MP／长边4096，真实照片使用2064×1548预览；界面开启颗粒及8256×6192完整保存通过。24MP预览仍为1500×1000。颗粒默认关闭，99资源包保留。

完整core:check（38个verify任务）、两类APK、app lint通过（0错误／12Warning）；K40最终30项回归＋单独51MP界面颗粒测试1项通过，22个回归输出独立解码检查。签名／16KiB对齐检查及设备APK hash匹配通过，未在16KiB页设备实测。模拟器仍为0.4，本轮未更新。测试包及临时文件已清理，原照片未覆盖。

安装包：`releases/CXO-FilmPack-0.4.2-preview.apk`（debug签名，50,170,538字节）。SHA256：`399f7e8fdd47c6e7b4d0b292fffe9f73b6ccbfb7ccce0e132060dc0526ef2fca`。

用户入口为Obsidian《CXO FilmPack 0.4.2 原生加速与大图颗粒修复》与《DXO FilmPack 8 研究进展与验证清单》。证据：`/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/k40-native-20261102/REPORT.md`。旧版本和历史证据保留，不把本轮照片性能扩大为全部模型逐款验收。

## 色彩与颗粒证据边界

146个公开固定模型有受控原版色块对照，最大ROI均值RMSE约0.00451430/255；另7个实验模型只验证特定上下文。照片已保留0.3.1的WideGamut→sRGB修正，4款5组无颗粒及4款5组颗粒有原版参考，不能说153项或99颗粒已逐款照片验收。基础类型不等于367个完整组合预设。

99颗粒包为`releases/native-grain8-v1.cxograinpack`，129,743,465字节，完整SHA校验后原子替换；0.6.0起随APK内置自动准备，已有正确包升级复用。提供跟随胶片／明确选择、独立0..200强度、自动／手动尺寸及稳定种子。JPEG／PNG可按原图level0..2预览并重新准备level0流式保存，全部8种方向和24MP／51MP连接有测试；Android预览重采样不声称等于原版金字塔。5个Generic/direct未猜测颗粒alias。

## 构建

| 模块 | 职责 |
|---|---|
| core | Kotlin色彩、曲线、目录、固定ICC、颗粒、TIFF／JPEG参考实现 |
| android-adapter | Bitmap、方向、tile、F16等Android边界 |
| app | 照片界面、原尺寸流式保存、颗粒包、可选ARM64原生后端 |

AGP9.4内建Kotlin，Java17，compile/target37，minSdk26，NDK30.0.16248370／r30，CMake3.31.6。使用Android Studio JBR；不要额外应用Kotlin Android插件，不并行启动多个Gradle。首次需通过SDK工具安装指定NDK／CMake，之后vendor构建不联网下载源码。

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' ./gradlew --offline --no-daemon -Pkotlin.incremental=false -Dorg.gradle.jvmargs='-Xmx2048m -XX:MaxMetaspaceSize=1024m' :core:check :app:assembleDebug :app:assembleDebugAndroidTest :app:lintDebug
```

第三方许可在THIRD_PARTY_NOTICES并随APK分发。This software is based in part on the work of the Independent JPEG Group. libjpeg-turbo源码与pin见app/src/main/cpp/VENDOR.md。

工程：`/Users/chenyixuan/AndroidStudioProjects/CXO FilmPack`；研究：`/Users/chenyixuan/ReverseWork/DxOFilmPack`；research-link.json与研究根android-project.json互相定位，根src/local-data是core兼容符号链接。

## 后续与历史

下一步优先RAW实际体验和可见差异；GPU及终局性能专项后置。RW2已实现首版，完整DxO RAW分支、其它RAW格式、产品16位、所有ICC、其它空间效果、持久后台任务、API26和其它真机仍未全面完成。旧研究runner含硬编码安装／恢复旧版本，勿直接用于当前设备。

历史0.4.1同图18.583／18.620秒、完整JPEG与旧串行45.603／45.815秒结果一致；当时尚无NDK。本轮之前README与专题保存在研究证据before目录。历史安装包及hash见releases/README.md和SHA256SUMS.txt，完整算法及验收以Obsidian为入口。

## 2026-11-03 夜间 RAW 续接

共享 HSV RAW 目录已扩展为 143 条：125 条 shared 加 18 条独立 R/G/B 曲线。三曲线动态 bake、signed-saturation 实验路由和 `FujiSuperia200E6` Android 回归测试已接入；JVM 的 `:core:verifyRawThreeCurveBake` 对 18 条均通过。该检查不代表原版 RAW 逐位一致，仍需 K40 真机和原版三曲线 RAW 对照。

K40 测试包安装仍被 MIUI 的 `INSTALL_FAILED_USER_RESTRICTED` 拦截。允许“通过 USB 安装”后，可在工程根目录执行 `./tools/run_k40_raw_tests.sh`，脚本会安装两个 APK、推入 7 张 RW2 并运行 `:app:connectedDebugAndroidTest`。

## 2026-11-03 S1RII RGB／红外黑白 RAW 接入

RAW camera-linear 路由由80增至85项；新增Kodak EIR、Kodak HIE、Cine Teal and Pink、Kodak HIE Filtre、Rollei IR400。RGB三款使用独立CXORRGB1上下文参数，provider黑白两款使用各自捕获的CXORBAKE1上下文；未改动原148条shared catalog。两类参数都来自原版捕获，不是色卡拟合。RGB资产不存网格；provider BW沿用包含验证网格的CXORBAKE1格式，但产品重新bake。EIR/HIE/TealAndPink六个原版17³ full-output context在产品Kotlin bake/render链最大差7 Q15；两款provider黑白四组重建/native-grid应用最大差1 Q15；其中两组真实native full17输出也最大1 Q15。

RGB 65³ renderer按最多两个上下文缓存，避免导出分块时重复bake及复制大网格。K40 Android 13 上新增的五款预览测试覆盖4199K与5485K两锚点，通过；两次相同输入行输出一致，HIE、HIE Filtre、Rollei IR400在output CMM前输出中性灰。完整RawPipelineTest没有跑完：旧颗粒用例要求设备已安装99项颗粒包，本次干净测试安装读到0项而失败；逐款全尺寸export测试非常耗时，已在单项新路径复跑后停止。另一个整套UI自动化在Android 13后台启动Activity时被系统拒绝，不影响新RAW用例。

离线`:core:check`、主包／测试包、lint、unit-test任务通过（app unit-test为NO-SOURCE）。当前主APK SHA-256 `e39c9fe963cdd967a9b75ebe09a0b94c3620064898184d8bfbc9b51593e146b5`；测试APK `7854a7e84734bd8724edd97eed81b8b062a975ef88191d13497feba20ca52f6b`。构建包已安装到K40，新增单项 instrumentation通过。

暂时无需用户操作。下一工程节点继续恢复68项负EV provider的相机前矩阵／曝光构造；须通过原版静态/双WB数据闭环后才扩大路由。K40上可手动抽查两种WB照片的五款效果，但这不是本轮必须阻塞项。颗粒回归需先导入`releases/native-grain8-v1.cxograinpack`再测。

## 2026-11-03 模型名匹配修正

LibRaw Android bridge 返回 S1RII 的真实模型名为 `DC-S1RM2`。`RawRenderPipeline` 已同时匹配 `S1RII` 与 `DC-S1RM2`，避免 camera-linear RAW 分支因名称不一致而误回退旧 sRGB 路径。

本轮 `:core:verifyRawThreeCurveBake`、单元测试任务、主包／测试包构建和 `:app:lintDebug` 均通过。当前构建包 SHA256：主包 `df4a0261dcc8d98eadc0a54377de1ac4cc0646a780e0444cab9335ebf4272fc1`，测试包 `08ccd490bcbaada34cfbecae0659007d0173448d3391daa3bd8c95556df71325`。K40 覆盖安装仍被 MIUI 返回 `INSTALL_FAILED_USER_RESTRICTED`，所以该修正尚未有新的真机 instrumentation 结论；允许“通过 USB 安装”后执行 `./tools/run_k40_raw_tests.sh`。

## 2026-11-03 RAW 颗粒导出修正

S1RII 强度100的 RAW 导出现在无论是否开启颗粒，都会先走 camera-linear 胶片路径；颗粒在 encoded working RGB 与 output CMM 之间叠加。此前开启颗粒会退回旧 sRGB RAW 路径，现已修正。主包、测试包、Kotlin 编译和 lint 均通过；K40 真机 instrumentation 仍待允许“通过 USB 安装”。本轮主包 SHA256：`893d74c00ec358da2fe98bd45b32b65c2beac3cc6bf781f9a9cbb41c94ce84b6`。

## 2026-11-03 RAW 颗粒预览修正

主界面现在即使开启颗粒也保留 `rawUri`，预览通过 camera-linear RAW 胶片结果后再叠加当前 preview level 的颗粒，并处理竖拍坐标。主包、测试包、Kotlin 编译和 lint 通过；K40 真机验收仍待允许“通过 USB 安装”。本轮主包 SHA256：`ff02f126577330accd4581808a51179117f3b48d6b3e40188da6307594949268`。

## 2026-11-03 RAW repair A/B 边界

JVM-only A/B 已覆盖 3 个 S1RII context、125 条 shared HSV 胶片和 Elite golden。完全跳过末端 saturation clamp 会让 Elite golden 最大漂移从 `0.0004578` 增至 `0.2643652`，因此生产继续保留原 repair。`repair` 后仅执行 saturation 非负保护可消除 Portra 等实验条目的负节点，同时不改变 89 条 `runtimeSafe` 和 Elite；它会改变 36 条实验胶片的有符号 saturation 语义，当前只保留为显式 fallback，未静默启用。证据：`/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-repair-ab-20261103/README.md`。

## 2026-11-03 RAW 参数来源修正

离线 512 点原版边界回归发现 camera-linear 分支曾误用传感器验证器默认值 `EV=0.84`、`highlight=1`。DxO S1RII P1040758 的实际 `RenderingData` 是 `EV=0.60`、`highlight=0`：旧值完整 sensor＋Elite reference RMSE 为 `8.8277/255`，修正后为 `0.0001986/255`，最大差从 `2639 Q15` 降到 `1 Q15`。`RawRenderPipeline` 现在从 RAW bake context 读取 EV，并使用受控上下文的 highlight 参数；这不是色卡拟合。`:core:check`、Kotlin 编译、测试 APK 和 lint 已通过；当前主 APK SHA-256 为 `344af0faed50fff7f8c13f9b253078133a71ba9f61b9a3ea1ea0edd4249b3e1d`，测试 APK SHA-256 为 `08ccd490bcbaada34cfbecae0659007d0173448d3391daa3bd8c95556df71325`。K40 真机仍待允许 MIUI“通过 USB 安装”。

证据：`/Users/chenyixuan/ReverseWork/DxOFilmPack/evidence/raw-fidelity-20261103/reference/raw-tone-parameter-regression-20261103.json`。

## 2026-11-03 夜间续接：S1RII Astia RAW 实验接入

在两张不同场景的 S1RII RW2（P1040758、P1176257）上，使用安装版 DxO FilmPack 8 受控捕获了 Astia 的 RAW 显影上下文。两次的有效 film curve、post matrix、额外矩阵和 EV=0.60 逐 Float32 一致；独立重建 invalid mask 一致，17³ 网格最大差 1 Q15。这个结果支持把 Astia 作为 S1RII 的实验性 camera-linear RAW 胶片接入，而不是把单张照片的颜色常数硬编码成通用 profile。

当前 `core/local-data/raw-films/catalog.json` 为 144 条：原有 125 条 shared HSV、18 条 R/G/B 曲线，加上 Astia 1 条 `experimental_dynamic_s1rii`。`RawRenderPipeline` 允许 `model=direct` 的 Astia 进入 RAW shared-HSV bake；JPEG 侧 dynamic-direct 路径不变。Astia 尚未做原版整图 TIFF 与 CXO 照片级对照，因此不宣称 parity。

本轮离线校验：`:core:check`、主包／测试包构建、`:app:lintDebug`、`:app:testDebugUnitTest` 均通过。K40 真机测试在安装测试辅助 APK 时被 MIUI 拦截：`INSTALL_FAILED_USER_RESTRICTED: Install canceled by user`；醒后允许一次“通过 USB 安装”再运行 `tools/run_k40_raw_tests.sh`。

详细证据：`evidence/raw-fidelity-20261103/reference/raw-astia-s1rii-20261103.md`。

当前 S1RII RAW catalog 身份覆盖已复盘为 144/153（94.12%）；缺口 9 条为 `Ekta100VS`、`Kod64`、`KodakEIR`、`KodakHIE`、`KodakHIEFiltre`、`Provia`、`RolleiIR400`、`Velvia`、`CineTealAndPink`。这是可装配目录覆盖，不是逐款原版 RAW parity。

补充：Astia 的 RAW `pre/extra/EV` 上下文与 Elite 不同，本轮已额外打包 `s1rii-astia.cxorbake` 与 `s1rii-astia-landscape.cxorbake`，按 S1RII WB 温度锚点选择；不会把 Elite 的相机前段矩阵套到 Astia。最新主 APK SHA-256：`1d5d255884f76087b08a3629c93a51e092088a39d8c089b433732cd6a81eff3`。
