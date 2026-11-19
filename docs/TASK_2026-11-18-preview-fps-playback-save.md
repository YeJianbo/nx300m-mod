# NX300M 任务书：取景帧率设置与回放换 LUT

基线为 GitHub `v0.1.0-preview.11`（`adcf8f1a`）及 11 月 18 日测试卡。以根目录的源码为工作区。先修回放保存问题，再接帧率设置，两项分开验收。离线工作可直接进行，写卡、刷机和发布另行安排。

## P1 · 回放页长按 Custom 换 LUT 后显示“保存失败”

**复现**：开启 LUT 实时预览，进入相册，长按 Custom，从打开的 MOD 面板进入 LUT，选另一款并按 OK。测试卡 `logs/fn-key.log` 第 16684–16812 行有两次同值尝试。长按启动的是 `mfw-panel.sh`，随后在同一个进程内进入 LUT 页面。

```text
LUT_CONTROL phase=ram-selection elapsed_ms=1
LUT_CONTROL phase=card-persist elapsed_ms=8
SmartFilter Liveview admission waiting: state=004c9bbc native_state=0 videosrc=0 udd_intr_handle=1 status=-1.
SmartFilter Liveview admission timed out after 12000 ms; no camera thread attached.
PREVIEW-WARNING SmartFilter preview sync failed; still selection retained
LUT_CONTROL phase=selection-complete rc=6 elapsed_ms=12270
BACKEND kind=1 ok=0
```

卡端已核对两个选择槽和两个微调槽的 FNV 校验，均通过。最新选择槽 164 指向 `nx-tune-a02c43d75a10488b`，强度 90%，两个 LUT 表各 4380 字节，微调槽也已保存对应风格和参数。随后的实时预览同步发生在没有取景流的回放状态，12 秒后返回 75，外层将其折为 6，面板最终显示“保存失败”。旧问题“回到取景后画面定格”仍须单独复测。

**实现入口**

- 已核对 `mod/metol/nxui.c` 的入口。进程首次显示前仅在 `live=1` 时调用 `panel_liveview_ready_before_show()`。长按 Custom 启动服务面板时是 `live=0`，随后 `open_mfw_lut()` 在同一进程设置 `live=1`，这条路径不会再执行首次显示的检查。现有 `tools/test_liveview_return_vm.py` 的独立面板检查不能代替服务面板切入 LUT 再按 OK 的回归。
- 检查 `mod/metol/lut_engine_shared.h` 的 `nx_lut_stage_with_strength()` 与 `nx_lut_sync_preview()`。独立记录配置持久化、预览待同步、预览失败和控制结果不确定。回放中完成保存后，应交还原厂焦点，让原厂恢复取景，并在取景就绪后同步最新一次 LUT。优先复用现有的就绪检测、控制锁和恢复机制。
- 保留已验证的相机身份检查、超时与不确定状态 guard。不要通过禁用相册入口、强制关闭预览、放宽 loader 准入或只换提示文字解决。

**验收**

1. 用离线夹具跑长按 Custom → 服务面板 → LUT → OK，回放期间持续设为 `videosrc=0`。保存成功后面板及时退出，12 秒同步等待不占用面板。
2. 模拟原厂恢复取景后，后端取得最新已保存的风格、强度与参数。覆盖连续选择、旧任务未完成时提交新值、关闭预览、取景一直不恢复、真实保存失败，分别核对落盘和同步状态。
3. 保留相册短按的原厂删除、Fn 保存当前款并留页、MENU 返回和拍摄页直接换 LUT。实机只需复测一次相册换 LUT 并确认回到取景后画面继续更新、滤镜正确、能拍照和关机。

## P2 · 设置增加“LUT 取景帧率”

设置页提供 **30 / 45 / 60 fps**，旧配置默认 **30 fps**。数值是启用 S2 实时 LUT 时的取景帧率上限，通过触摸和实体方向键/OK 都能选择。45 fps 在实机验证稳定输出前保留为测试选项，默认值仍是 30。

- UI 从 `mod/metol/nxui_feature_pages.h` 的设置页接入。沿用现有卡端偏好读写，区分保存失败和配置已选。旧卡缺项按 30 处理，对错误值使用安全回退。
- 从 `mod/deployment/mfw/smartfilter-live-preview.sh` 跟到 `runtime/smartfilter_loader.c` 与 `runtime/smartfilter_ram.c` 的 `sync_sensor_fps()`。配置只在控制阶段读取，参数通过现有校验链进入 ABI7。修改运行中帧率时走原厂的安全切换点，拍照或编码期间延后处理。
- 高于设定上限才降低帧率。原厂本来比上限低时保留原厂值。关 LUT 实时预览后恢复进入预览前的原厂值；覆盖 `30 → 45 → 60 → 关`，确认中途切换没有把 30 或 45 错当成原厂值。ABI 布局或构建号发生变化时，同步核对 blob、loader、launcher 和打包识别。

**验收**：先编译，离线覆盖缺省值、重启读取、无效值、三档互切、原厂低帧率、拍照期间切换和关预览恢复；复用已有相册返回与拍摄交接回归。实机固定场景、曝光和 LUT，比 30 与 45 的实际帧间隔中位数/P95、掉帧、拍后取景恢复，以及快速按第二次快门。60→30 理论少 50% 逐帧工作，60→45 理论少 25%，这个比例仅供对照，不能用来代替实机输出或 RAW 写卡数据。

## P3 · 按 OK 应用 LUT 的等待

范围是从确认键到原厂取景恢复、新 LUT 首帧出现。卡上两条正常取景的实机记录（`logs/fn-key.log` 第 16436–16457、16534–16555 行）显示，后台分别耗时 1153、1241 ms，其中取景同步耗时 986、999 ms，持久化分别是 21、11 ms。每次同步有三次 `Target stop window`，分别为 104/117/248 ms 和 130/121/257 ms。记录足以确定同步是主耗时，成功日志过滤了暂停原因，还需把每次重试对应到具体分支。

- 先在 `smartfilter_loader.c` 的 `supervise()` 及 `execute()` 里量化暂停/恢复原因，区分拍摄仍在进行、预览帧正在处理、Session 引用未释放、线程仍在模块代码内。对齐脚本准备、准入、暂停总时长、排空等待、配置提交、原厂第一帧和新 LUT 第一帧。保持有界排空和不确定状态的恢复协议。
- 单独核对已安装、已启用且取景拓扑不变时的换表路径。相同表/强度优先检查能否省掉重复控制，同拓扑不同表评估由常驻后端在安全帧边界接收最新一份完整请求；首次安装、真正开关预览、故障恢复仍走完整控制。请求须绑定相机身份和 generation，新表必须在完整校验后发布，拍摄繁忙时保留最新一次。不能单纯把后台操作挪走然后报新滤镜已显示。
- AGY 的 X11 `MIT-SHM` 是面板像素上传，不参与这里的应用链。若考虑共享内存传递 LUT，先验证 4380 字节表的传输成本及是否能实质减少暂停/重试；共享内存本身不处理帧边界、拍摄交接或版本确认。

**验收**：同一模型、同一张卡和 LUT，分别对照首次启用、热切换、同值确认、刚拍完换 LUT 和回放换 LUT。记录 OK 到面板退出、原厂首帧、新 LUT 首帧及相机累计暂停的中位数/P95。校验实际生效 generation、落盘选择、连续拍摄和正常关机。
