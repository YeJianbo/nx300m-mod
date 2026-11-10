# 首页设备照片

`app/src/main/res/drawable-nodpi/home_device_photo.jpg` 使用三星官方发布的 NX300 白色机身正面产品照片，保留原始 Samsung Electronics LiVE 水印。照片中的设备是 NX300；首页设备名称仍按实际连接或已保存的型号显示。

- 来源：Samsung Newsroom Korea。
- 文章：〈”모던한 감성의 레트로 디자인!” NX300 디자이너 스토리〉，2013-01-07。
- 页面：https://news.samsung.com/kr/3848
- 原图：https://img.kr.news.samsung.com/kr/wp-content/uploads/2013/01/NX300_14.jpg
- 核对日期：2026-10-23。

产品照片及 Samsung 标识的权利归三星电子及相关权利人。本项目未将该第三方照片标为原创或 AGPL 授权素材。Samsung、NX300 和 NX300M 名称用于说明兼容设备。

首页实际使用 `home_device_cutout.png`：2026-10-23 通过内置 imagegen 编辑上述照片，去除白色背景、地面倒影及机身轮廓之外的水印，输出透明背景。编辑要求保留正面机身、镜头、材质与标记；该衍生图片不属于未修改的原始照片。原始 JPEG 保留供来源核对。

# CXO FilmPack

RAW 显影的胶片色彩引擎与胶片资产来自 [Macchen666816/CXO-FilmPack](https://github.com/Macchen666816/CXO-FilmPack)，固定为 `v1.0.1`、提交 `4ae863da62ae435ed6fd4c72fde02af9271717c4`。NXConnect 保留其 Kotlin 色彩核心，新增 Android RAW 适配、分类选择、强度、分块渲染、取消与配方持久化。

CXO FilmPack 使用非商业许可，允许非商业使用、修改和分发，要求保留版权与许可证并标明修改。许可证全文位于 APK 资产的 `cxo-filmpack/LICENSE.txt`，第三方声明位于 `cxo-filmpack/THIRD_PARTY_NOTICES/`。胶片名称、数据、商标和其他第三方素材仍受各自权利约束；该集成不表示 DxO 或相关品牌授权或背书。
