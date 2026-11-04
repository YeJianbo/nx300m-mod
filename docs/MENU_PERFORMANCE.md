# NX300M MOD 启动与菜单性能审计

## 2026-11-04：开启预览卡死后的局部回退

用户在新版开启预览后报告卡死。卡日志已备份到 `outputs/review/preview-enable-freeze/`；本次开机最后一条持久日志只有 `ram stage begin`，没有保存本次预览事务现场，尚不能确定阻塞函数。此前 VM 通过不足以证明实体原厂控制线程重建链的全部状态已经覆盖。

撤回加载器在暂停目标线程期间主动发送 `ChangeUserInfo(0x39)` 的新逻辑，恢复由已有原厂回调发起刷新。文件哈希缓存、Session、逐帧 Reset/IQ 恢复和成片 Hook 保留。首次预览可能仍需原厂状态变化才接入，不能再宣称此回退版已经解决立即生效。下面关于主动通知的段落记录被撤回候选的设计与测量。

回退候选的 ARM/MFW 整链执行退出 0，验证通过且 VM 已关闭。新增检查确认 loader 返回后没有主动排入原厂刷新消息；原有回调刷新、连续 Mono/CC 切换、关闭恢复及 11 项故障检查通过，Open/Close 为 207/207、held=0。104 个部署文件回读通过，仅 loader 与服务重载标记变化；实体卡死是否消失仍未验证。报告为 `outputs/review/preview-enable-freeze/result.json`。首次夹具链接退出 1，因工具链将 msgctl 引用改写为原厂 libc 不提供的 __msgctl64；改用既有 msgrcv 非阻塞接口检查空队列后构建和完整执行通过。

## 2026-11-04：预览切换延迟与首次启用

实体日志 `outputs/review/lut-switch-delay/20261104T021840Z/fn-key.log` 确认运行预览 build 3392396409。两次切换的后端耗时分别为 9534、14664 ms，其中原厂可执行文件及七个库的完整哈希耗时 5182、10424 ms。这部分发生在暂停相机线程之前，却让每次切换都等待数秒。初次启用的即时快照为 `s2_path=0`、`frames=0`、`refresh_requests=0`；用户反馈需要拍照后预览才出现。

加载器现在将成功核验的文件身份保存在 `/var/run`。同一 boot ID、进程启动时间及模块 build 下，文件的设备、inode、大小、mtime、ctime 均未变时复用哈希结果；进程映射、原厂代码和 GOT 槽位仍逐次检查。首次使用、文件身份变化或缓存不可用时执行完整哈希。

首次插入及关闭节点时，加载器在配置事务末尾发送一次原厂 `ChangeUserInfo(0x39)` 格式的非阻塞通知。原厂控制线程继续负责停流、排空及重建，不再等第一个已挂钩帧来发起通知。节点已经存在时更换风格只更新表，不为每款风格重建取景链。队列发送失败会回滚本次配置。每帧 Reset、IQ 恢复、Still LUT 算法及 196 字节 ABI 保持原实现。

验证使用 `tools/probe_ipcs_session_vm.py`：新增首次无帧通知检查，保留 Mono→富士 CC→Mono、关闭恢复、资源交接及故障场景。结果位于 `outputs/deployment/smartfilter-session/arm-vm/report.json`；模拟器耗时与实体速度分别报告。

同一轮 ARM/MFW 记录中，首次文件准入 573 ms（七个库完整哈希），后续命令 47 ms（七个身份命中、零文件重哈希），新进程 553 ms（重新完整哈希）。首次无帧启动、Mono→CC→Mono、关闭恢复和 11 个故障场景通过，Open/Close 为 208/208。这里只比较准入检查阶段，实体切换总耗时待新版本上机记录。

## 2026-11-04：精简默认目录与模拟器增量部署

完整源库保留 143 款；`looks/catalog.json` 选择日常 23 款。模拟器 `/looks`、LUT 面板、SD 打包与个人库导出目录共用该清单；离线 CLI 和评测仍能访问完整源库。未改 LUT 表、编译数学或运行时处理链。

模拟器原来每次打开面板都用 shell 对 143 款执行 mkdir/cp/mv。现在由一个 Python 批量发布器在原有引擎 flock 下比较已部署内容，相同文件不复制，变化文件原子替换。保留源表格式/哈希检查；普通打开不触发拟合，正式显式同步仍默认 fit，可选 legacy。

同一 MFW 1.15 原厂 ARM 相机会话的结果：

| 路径 | 打开调用返回 | 观察到 nxui 首帧 | nxui 自身首帧 |
| --- | ---: | ---: | ---: |
| 旧版 143 款全量部署 | 4569ms | 4799ms | 70ms |
| 新版 23 款，更新目录 | 554ms | 782ms | 70ms |
| 新版重复打开 | 563ms | 812ms | 73ms |

新版第一次复用 46 个文件、只更新目录文件；第二次复用全部 47 个文件，写入数为 0。这里测的是电脑上的部署与打开链路，各一条样本，包含 Windows/WSL/QEMU 开销；实体相机不走这套部署路径，不能将加速比例直接当作机身首开提升。机身菜单缩为 23 款，实际耗时尚未重测。

证据与截图：`outputs/review/runtime-locks/catalog-speed.json`、`catalog-selected-23.png`；独立发布检查 `catalog-copy-check.json` 验证首次复制、重复零写入与单文件变更更新。当前精简目录 ZIP 复用并校验已发布表，未触发重新拟合。源码备份为 `outputs/review/runtime-locks/before-catalog-speed/`，接手前的完整目录包仍在 `codex-final` 检查点内。

## 2026-11-03：主菜单直接进入 LUT 列表

点击「LUT 滤镜」现在在同一进程、同一 X11 窗口内切页，复用字体和输入焦点。返回主菜单也不重启程序。列表打开只读取目录和选择记录；确认选择时才由引擎检查、加载适配器并同步已开启的预览。直接调用 `lut-panel.sh` 同样不在首帧前执行 ready、apply 或 restore-selection。

过去这一步会退出 nxui，启动 Shell，复制程序到新的 RAM 目录，执行适配器检查和选择恢复，再重新初始化字体与窗口。选择恢复还可能等待相机的预览控制事务。保存的选择现在只作为「上次选择」显示，打开列表不会把它重新写入相机。

同一 ARM/Xvfb VM 交替运行旧版与新版各 3 次，测量从 X11 确认键发送到列表完成绘制的时间：

| 版本 | 三次耗时（ms） | 中位数（ms） |
| --- | --- | ---: |
| 修改前 | 1000、888、916 | 916 |
| 同窗口切页 | 107、87、94 | 94 |

本组中位数减少 89.7%。旧版的适配器由立即返回的记录夹具替代，没有人为添加延时，也不包含实机 ptrace 或预览同步等待。窗口与字体使用真实 ARM 程序、原厂库和字体，X 服务为 ARM Xvfb。此处测的是模拟环境的菜单交互。

检查包括：全目录键盘与触屏选择、浏览期间不提交、仅确认最后选择、返回再进入、不调用适配器的直接入口、保存为开／关时的首屏，以及后台等待期间不空转。报告和截图位于 `outputs/performance/lut-menu-entry/`，复现工具为 `tools/probe_lut_menu_entry_vm.py`。

成片参数链的同环境对照见 [LUT 参数更新开销](LUT_CAPTURE_PERFORMANCE.md)。

部署：已写入测试卡 `LOCAL_REDACTED`，275 个文件回读一致，7 个文件更新，971 个无关文件的元数据未变。启动入口与快捷键配置保留，覆盖文件已备份。记录位于 `outputs/deployment/smartfilter-session/diagnostic-card/reader/card-LOCAL_REDACTED/lut-switch-20261103T124824Z/report.json`。

构建时首次链接退出 1，报 `undefined reference to stat/lstat`；改用原厂 glibc 2.13 导出的 `__xstat64/__lxstat64` 后构建通过。后续构建只有非加载 `.strtab` 的临时对象名不同，运行段逐字节相同；最终打包沿用交互测量时的同一份 ARM 文件，入口、选择和后台等待报告均匹配该文件。

## 此前优化记录

2026-11-03：修复 LUT 面板按确定后隐藏窗口、等待后台期间的 CPU 空转。窗口已经隐藏且不再绘制，但原先仍把未清除的 dirty 状态和过期帧期限计入 select，导致等待期限持续为零。现在隐藏状态只等待输入或后台任务，退出已提交时也不再进入等待。

同一 ARM/Xvfb VM、相同两秒后台夹具：修改前后总等待为 2210／2196 ms，唤醒 4055／23 次，首帧之后的本进程 CPU 为 2107.934／58.134 ms。后台任务本身没有加速；此修改消除等待期间抢占 CPU 的空转。演示模式确定后 171 ms 正常退出，没有新增长等待。实际机身响应仍需上机观察。

当前检查：`tools/probe_panel_idle_vm.py` 退出 0，`outputs/performance/menu-event-loop/report.json` 为 passed，私有 VM 已关闭。该夹具使用实际 ARM nxui、原厂字体、X11 和两秒 Shell 后台任务，不模拟相机 ISP 或 SD 保存速度。

2026-11-02：已优化服务、LUT、FilmLab 共用的 CPU 绘制后端。字符宽度按字体缓存；纯色矩形逐行写入；透明像素直接跳过；图像旋转改用分块转置。开屏不再自动查询服务状态，重绘间隔从 100 ms 改为 33 ms，空闲仍等待输入。

本轮真机日志中，第一次首屏后自动服务查询占用 1742 ms；后续绘制出现 1289–3488 ms 及显示超时。记录在 `outputs/deployment/smartfilter-preview/camera-ui-slow-20261102T113236Z/fn-key.log`。

当时使用的临时预览测试面板为 560×240，默认选中“关闭预览并保存采样”；另有黑白短采样、完整菜单和退出。该历史方案曾在打开面板时自动请求 restore；当前入口已恢复正常主菜单，停止预览由用户显式操作。

ARM 用户态 QEMU 中，12 次同一工作负载的平均 CPU 绘制及旋转为 47.174 → 15.166 ms；像素、字体宽度和旋转输出一致。此数值不含 X11 上传和实体 LCD 等待。独立 ARM Xvfb VM 的 21 项菜单与入口检查通过，含无 X11、无 nxui 可执行文件时的软停分流；硬件软停本体沿用上一版验证过的模块。

本轮报告：`outputs/performance/menu-common-20261102/raster-report.json`、`outputs/performance/menu-common-20261102/controls-vm/report.json`。本轮只用 `build_metol.py --menu-only` 编译界面，JPEG LUT 引擎及 SmartFilter RAM 模块保持原字节。

实机跟进：小面板首开 trigger_elapsed_ms=380、重开=237。之后两个 restore 返回 rc=5，约数十秒内长按均记录为 `panel pid alive`；最后一次 restore 成功。对应入口修复与“慢任务、失败、重开、显式重试”模拟记录位于 `outputs/performance/menu-common-20261102/softstop-ramlog-vm/`。139 条实机样本已独立备份到 `outputs/deployment/smartfilter-preview/camera-sampled-20261102/`。

## 此前审计记录

最新修订：原版弹框恢复后实机仍未弹出，原厂 Fn 菜单正常。Fn 已移出屏幕抓取的 X11 队列，改为旁听 evdev 按下／松开；一次解析 Fn 映射后不再依赖每 100 ms 的 XQueryKeymap 及 500 ms 连续采样条件。保留既有原版 Xft UI；本轮不会把模拟结果当作实体显示成功。

2026-10-31 最新决定：按用户要求回到最初长按 Fn 弹框，MENU/Custom 监听关闭。`--fn-shortcut` 包使用首次部署的 461588 B Xft 程序与六项菜单，独立于当前 LUT 界面；没有把下面优化版的模拟性能数值用于此历史程序。Fn 启动脚本使用原厂字体和 RAM 缓存，不传递 Shell 启动锁。实机显示与响应尚待本次装卡确认；下方“当前卡使用 MENU”的描述为此前审计状态。

当前卡使用长按 MENU 松键的非独占 evdev 入口，Custom/Fn 关闭。CPU XImage 候选实机仍不显示；最新反馈补充“按快门能恢复”。本地新增原厂子窗口、RevertToParent 及可见性／焦点交接修复；尚未再次写卡，也未取得本次 RAM 日志。下方旧性能样本不能当作当前实体响应或修复成功的证据；最新状态见 DELIVERY.md 顶部。

2026-10-31：当前优化版在同一独立 ARM VM 中，Shell 入口至首帧从首次 **1061 → 266 ms**、重开 **326 → 212 ms**；新旧重开截图逐像素一致。此次按用户要求优化模拟器，不等待或连接实体相机。没有写卡、刷固件、重启主模拟器或改原厂相机程序。

## 实际入口

```text
已有 MFW autoexec
  autoexec-services.sh → nice 19 后台 staging
    ram-stage.sh → 校验/发布 RAM 资源 → services.sh → start.sh/service.sh
      nx-remote-controller → Custom 独立 X11 helper
        完整按下/释放 + 原厂 preview/焦点/S1 检查
          socket 通知 → HTTP 主循环分派 → fork → /bin/sh mfw-panel.sh
            资源检查 → exec nxui --mfw-services
              原生锁 → X11 → 直接读取原厂字体
                配置读取/离屏首帧 → 映射/焦点 → 按需执行操作
                  LUT 列表：同窗口切页 → 本地浏览 → 确认后提交
```

必须区分三条路线：

- `autoexec-connected.sh` 明确设置 `NX_REMOTE_CUSTOM_KEY=0`、`NX_REMOTE_PANEL_SHORTCUT=0`；合并包还带 `NO_PANEL_SHORTCUT`。这里没有菜单监听，按键无反应不能归因于慢。本轮没有恢复监听或删除标记。
- MFW 独立服务默认 Custom，旧 Fn 轮询必须显式启用。Custom 是一次完整按下/释放，不是长按 Fn。
- `lut-panel.sh` 是独立入口，仍将 ARM 文件复制到 RAM 后运行，但已移除首帧前的适配器加载和选择恢复。主菜单进入 LUT 时直接切页，不再经过该脚本。

## 静态发现与处理

| 范围 | 代码事实及影响 | 本轮处理 |
| --- | --- | --- |
| 启动暂存 | 按清单复制、比较、发布资源，逐项创建外部进程；重复入口仍比较已有资源。正常按键开菜单不重走 staging | 保留来源身份、RAM 容量和字节比较，不通过跳过校验提速 |
| 按键监听 | Custom helper `poll(200)` 监听 X11/socket，约 5 s 同步检查窗口树/相机身份；200 ms 不是每键固定等待 | 单键异步接管；原厂状态检查移至松键后，不再依赖 ReplayKeyboard 放行冻结的键盘 |
| 主进程分派 | `mg_mgr_poll(50)` 不监听 Custom socket，通知等下一轮；仅该等待可增加约 0～50 ms，其他处理还可加时 | 沿用 trigger/sent/received/fork 日志定位，不为此提高全服务轮询频率 |
| 失败重试 | Custom 初始化期限 2 s，失败按 2/4/8/16/32/60 s 退避；不可用期间按键执行原厂功能 | 属于初始化失败，不是每次菜单等待；先读失败阶段，不盲目缩短退避 |
| Shell 创建 | 旧脚本每次准备字体链接/配置，并调用 flock 进程检查两把锁 | 当前脚本保留路径与资源检查；两把既有锁交由 nxui 在 X11 前获取，并设 CLOEXEC；去掉字体链接、缓存配置及所有 shell flock 进程 |
| UI 初始化 | 原先按 216 高度创建窗口/pixmap，MFW 加载菜单后改为 464，立即重建缓冲 | 已按实际入口布局一次分配，尺寸未变时不再发 resize 请求 |
| 字体加载 | 原厂固定字体走名称匹配，冷开扫描字符集；单独改为按文件打开又会触发 Xft 的字符集重算和全局配置初始化 | 构建时用原厂 ARM Fontconfig 生成 8274 B 字符覆盖表，运行时直接打开原字体并带入 FC_CHARSET；字形仍按需加载，不新增常驻 UI |
| 菜单配置 | MFW 直接读预置 `gui_mfw.NX300M`，无动态生成；完整菜单的旧码率/延时生成器仅在进入相应子菜单时运行 | 保留惰性生成、后台执行，不重新引入全局 sync |
| X11 首帧 | 离屏 draw 后一次 XSync，map/blit/focus 后又一次；同连接请求已有顺序保证 | 去掉映射前等待，保留最终 fence，先准备首帧再覆盖原厂画面 |
| X11 后端 | CLIPBOARD/TARGETS/TEXT/UTF8_STRING 分别同步查询 | 改为一次 XInternAtoms；不删除焦点或 S1 顺序保障 |
| 按键映射 | Nuklear 每个 press/release 都 `XGetKeyboardMapping`，有同步请求和临时分配 | 改用 Xlib 的 `XLookupKeysym` 缓存，`MappingNotify` 时刷新；共享 Quick Share 面板同步受益 |
| 绘制 | dirty 才重排，Expose 复用 pixmap，最多 10 Hz，不抓取/重画取景帧；旋转 blit 仍每次创建/释放两个 XRender Picture | 保留绘制上限；无主导耗时证据前不新增 Picture/字形缓存生命周期 |
| 焦点交接 | 保存原焦点，S1/关闭先隐藏并还焦点；Captured S1 只转发一次；evdev 不独占设备 | 保留交接处 XSync，不能为减少调用数删除顺序和错误处理保障 |
| 功能调用 | 状态/网络在首帧后低优先级子进程执行，管道非阻塞，12 s 任务期限，关闭只读任务可取消 | 保留异步机制，不让状态完成成为首帧前置条件 |
| CPU 优先级 | 服务及启动 Shell 为 nice 19，nxui 入口明确恢复到 0；后端子任务再降到 19 | 本轮保留此策略；VM 未运行原厂实时取景，不能证明该策略对实机拍摄无影响 |

状态读取仍遍历 `/proc`、为各 PID 运行 readlink，并读取来源卡身份。它不挡首帧，但会竞争资源，存储阻塞时可能久不完成。应单独看 `BACKEND elapsed_ms`，不能用“正在读取”判断首帧未出。

另有两项未在本轮改变：`read_output()` 连续读至 EAGAIN，无限输出的异常后端可能拖延输入；旧 `/tmp/nxui-close` 标记只在循环醒来时检查，100 ms 检查节流不保证空闲时 100 ms 内响应，空闲 select 可等待 60 s。当前主关闭路径是 X11/evdev，不能把备用文件标记当成已保证时限的快门通道。

## 等待与轮询

| 位置 | 当前值 | 含义 |
| --- | --- | --- |
| legacy Fn | 持有 1200 ms 后释放；约 100 ms 请求节流 | 识别规则，默认关闭 |
| Custom helper | poll 200 ms，约 250 ms 心跳检查 | X11 fd 可提前唤醒 |
| HTTP 主循环 | poll 50 ms | Custom 分派可能多等一轮 |
| UI | 100 ms 绘制间隔，每批最多 64 个 X 事件 | 保护相机 CPU，可能推迟视觉反馈；不是空闲持续 10 Hz 重绘 |
| 服务页面 | 约 1 s，只在相关页可见时 | 状态不变不重绘 |
| 子任务 | 100 ms 回收检查，管道可提前唤醒 | 不在首帧前 |
| service/start/operation lock | sleep 1 的有界就绪/锁等待，常驻监督约 1 s | 不在正常按键热路径，保留；不应每次开菜单重启服务 |

## 分段计时

保留旧日志兼容读取工具，新增以下记录。默认不为每轮 poll 写日志。

| 记录 | 测量范围 |
| --- | --- |
| `NX_CUSTOM_TRIGGER checks_ms` | helper 处理 release 到完成焦点/preview/快门检查；不含物理按键到客户端事件排队 |
| `NX_CUSTOM_DISPATCH elapsed_ms` | helper 触发到父进程收到通知 |
| `NX_PANEL_START` / `NX_PANEL_EXEC` | fork、关闭继承 FD、执行 shell 前的时间 |
| `PANEL_SCRIPT` / `PANEL_STAGE resources` | shell 入口到路径、资源和锁检查结束 |
| `PANEL_FONT mode/source` | 当前字体按文件还是按名称打开、实际资源路径；旧版脚本的字体准备记录为 PANEL_STAGE fonts / PANEL_FONT_CONFIG |
| `PANEL_INIT` | recipes、XOpenDisplay、window、font、backend、menu、focus_snapshot、draw_submit、map_focus_fence 九段，互不重叠 |
| `FIRST_FRAME` | 程序入口到最终 XSync；另记 trigger/fork/script/prep 到该点的时间及 PID |
| `PANEL_RESOURCE` | first-frame/exit 的本进程累计 user/system CPU、最大 RSS、上下文切换；不含子进程及相机 CPU |
| `BACKEND` / `EXIT` | 任务结束、总绘制/唤醒次数、最大绘制提交耗时 |

`FIRST_FRAME` 只表示 X server 已处理此前请求，不证明 LCD 已扫描显示、取景未丢帧或用户看到的实际时延。`draw_submit_ms` 只测请求提交，服务端工作可能在最后 fence 体现。旧 `INIT font_ms/map_ms` 混有窗口/绘制工作，新版应使用 `PANEL_INIT`，不能把旧 map_ms 当纯映射时间。

C elapsed 用 CLOCK_MONOTONIC；shell 用 `/proc/uptime`，以 CLOCK_BOOTTIME 对齐，分辨率取决于内核（通常约 10 ms）。不混减两种绝对时间；按单次启动 PID/时间段关联，休眠/快照前后不能合并采样。

`EXIT` 和 exit 资源记录位于 UI 主循环结束，后续释放图形资源、LUT 恢复和最终退出仍可能耗时；不能把它当整个进程已退出的回执。

只在一次显式菜单启动设置 `NX_PANEL_TRACE=1`，可增加 `PANEL_FRAME dequeued_ms/submitted_ms/events/painted/exposed`。它测客户端取事件到提交，不含取事件前的排队，更不是物理按键到屏幕时延；平时保持关闭，避免日志竞争。

## 证据与构建

已读旧独立 ARM VM 的 `ram-runtime/arm-vm/guest/mfw-results/panel-first.log`、`panel-second.log`：程序至首帧 1060/198 ms，旧 font_ms 为 621/44 ms，shell 至首帧 1343/503 ms。它们说明该 VM 冷/热差异显著，也提示应拆开 shell 时间；不是新版结果或实机指标。

- `tools/build_metol.py --ui-only` 最终退出 0。首次编译提示已有 LUT 路径 `%s` 潜在截断，已按 key 容量限定长度并检查结果；最终无该警告。
- `tools/build_gallery_share.py --panel-only` 退出 0，重建共享后端使用者，未重建 gallery hook 或固件。
- Shell 语法通过；原厂 ARM BusyBox 在仓库 QEMU 下验证配置命中、路径/模板变化、额外尾部内容的失效处理，退出 0。首次 PATH 未找到 qemu-arm-static（退出 1，`No such file or directory`），改用仓库程序后完成。原始记录见 [shell-check.json](../outputs/performance/menu-audit/shell-check.json)。

直接产物为 `outputs/metol/nxui.arm` 与 `outputs/quickshare/gallery/quickshare-panel.arm`。没有重打旧 SD ZIP，避免将历史整包 VM 验证误标为覆盖新文件；卡和 RAM 进程不会因源码/单个 ARM 重建自动升级。

## 当前版同一 VM 对比

报告为 [menu-font-coverage/report.json](../outputs/performance/menu-font-coverage/report.json)，汇总及像素比较为 [comparison.json](../outputs/performance/menu-font-coverage/comparison.json)。同一 VM 内先运行留存的旧版，再运行当前版，每个版本各首次/重开一次；状态目录独立。两者均在 X11 首个客户端初始化后开始，避免把服务端首次初始化全部计入先运行的版本；没有预热字体或菜单。

| 分段（ms） | 旧版首次 | 当前首次 | 旧版重开 | 当前重开 |
| --- | ---: | ---: | ---: | ---: |
| Shell 入口至首帧 | 1061 | 266 | 326 | 212 |
| 程序入口至首帧 | 816 | 196 | 153 | 144 |
| 两者之差：Shell/加载等 | 245 | 70 | 173 | 68 |
| XOpenDisplay | 21 | 16 | 14 | 10 |
| 字体初始化 | 537 | 54 | 38 | 40 |
| 后端初始化 | 62 | 45 | 33 | 28 |
| 菜单读取 | 3 | 2 | 1 | 2 |
| 首帧绘制请求提交 | 38 | 23 | 14 | 14 |
| 映射/焦点/最终 fence | 139 | 45 | 47 | 42 |

样本中首次减少 795 ms，重开减少 114 ms。首次本进程 maxrss 从 13188 → 6456 KiB。每个版本仅一对样本，运行顺序也会影响库和文件页缓存；这些是本次配对观察，不是统计意义上的固定收益或实体相机承诺。原厂相机程序、Custom helper 和 ISP 未在该 VM 中运行。

四次打开、Menu 关闭和重开均成功，guest 退出 0；私有 VM 已退出。四份真实 X11 像素截图与日志保留在同目录，`baseline-warm.png` 与 `current-warm.png` 全图逐像素一致（464×720 原始旋转方向）。字体/布局未因这次优化改变。状态仍在首帧后异步读取，当前模拟器样本后端约 1.8 s 完成，属于信息填充时间。

### 字体覆盖表的作用

上游 Xft 2.1.14 的 `XftFontOpenInfo` 在输入 pattern 没有 `FC_CHARSET` 时，执行 `FcFreeTypeCharSet(face, FcConfigGetBlanks(NULL))`，源码明确说明此计算昂贵。本项目运行的是原厂库；实际对照中，只有 file/pixelsize 的候选首次字体 3370 ms、重开 455 ms，整段启动 3559/626 ms，证明简单按文件打开会退化（记录见 `menu-direct-font-final/`）。

当前 [panel_font_metadata.c](../tools/panel_font_metadata.c) 在构建阶段用**原厂 ARM** `FcFreeTypeQuery` 提取 face 0 的覆盖范围，再用同库序列化。产物 `outputs/metol/mfw-font.charset` 为 8274 B，描述 44426 个码点；不包含字形图像，也不修改原厂字体。nxui 验证字体长度、元数据格式、码点数量并构造带覆盖表的 Xft pattern，再取当前显示的渲染默认值。模板损坏或字体长度不符时不继续打开。长度和码点数量是兼容性检查，不是文件真实性认证。

MFW launcher 现在需要 `bin/nxui.arm` 与 `fonts/mfw-font.charset` 同步升级。`build_mfw_services.py` 已将覆盖表加入资源映射；此次只更新源码、独立 ARM 产物和模拟器 overlay，没有重打旧 SD ZIP。原字体仍位于 `/usr/share/fonts/SDIC_GP_US_20120720.ttf`，没有复制其 10.7 MB 内容。

### 构建和夹具记录

Metol UI/后端最终构建退出 0，Quick Share 面板的共享 X11 后端重建退出 0。第一次新增字体长度检查时，现代头文件引用原厂 libc 未导出的 `stat`，链接退出 1；改用原厂 stdio 的 fseek/ftell 后成功，未更换 libc。早期一组菜单 guest 已退出 0，但串口中的 hrtimer 内核提示插入 base64 导致采集工具退出 1；现夹具降低串口内核日志级别后完成采集，失败证据保留在 `menu-direct-font/`。

## 首轮分段测量（历史）

使用 [probe_menu_startup_vm.py](../tools/probe_menu_startup_vm.py) 单独启动无网络 ARM VM，运行原厂 ARM 图形库、ARM Xvfb、当前 nxui 和原样 MFW launcher。没有运行原厂相机程序或 Custom helper，也没有模拟传感器、ISP、LCD 扫描和整机内存压力。测试协调器等待日志的 sleep 在被测进程之外；关闭发生在首帧之后。

最终结果见 [report.json](../outputs/performance/menu-startup-vm-lowpriority/report.json)，原始日志同目录 `cold.log`、`warm.log`。启动器通过 setpriority 使用 nice 19；UI 日志确认自身恢复为 0，与当前源码一致。

| 分段（ms） | 首次 | 重开 |
| --- | ---: | ---: |
| Shell 入口至首帧 | 1794 | 335 |
| 程序入口至首帧 | 1483 | 154 |
| 两者之差：Shell 准备及动态加载等 | 311 | 181 |
| XOpenDisplay | 601 | 12 |
| 窗口准备 | 10 | 4 |
| Xft 字体打开 | 604 | 34 |
| Nuklear 后端初始化 | 61 | 35 |
| 菜单读取 | 4 | 2 |
| 焦点快照 | 7 | 3 |
| 首帧绘制请求提交 | 42 | 15 |
| 映射／焦点／最终 fence | 149 | 47 |

首次字体配置 `reused=0`，重开 `reused=1`。两次 Menu 输入关闭、进程退出、再次启动成功；状态任务均在首帧后开始，关闭时被取消。私有 VM 已退出。上述时间不包含实体按键、Custom 检查和父服务分派，不能称为完整按键延迟。

去掉冗余 unlock 进程前的一组独立测量为 1514/347 ms，程序初始化 1190/153 ms，字体 591/39 ms（见 [先前结果](../outputs/performance/menu-startup-vm/report.json)）。两组环境启动时序和启动器优先级不同、每组仅一对样本，不能据此声称提速百分比。共同现象是冷开主要耗在字体及 X11，重开 Shell／加载开销仍占较大部分，菜单文件读取不是主因。

本轮夹具曾退出 1：精简 rescue BusyBox 不带 nice applet，未启动菜单；记录保留在 `menu-startup-vm-final/`。改用 ARM setpriority 启动器后 guest 退出 0。随后分析器误要求 UI 保持 nice 19，而源码主动恢复到 0；修正分析器后直接重新分析保留日志通过，没有为此再运行菜单。误判原报告保留为 `menu-startup-vm-lowpriority/initial-analysis.json`。各组保留日志、输入 overlay 和报告，已移除可由缓存 base + overlay 重建的约 950 MB 拼接临时镜像。

实体读取使用原记录地址 `http://192.168.107.1:8781`，首个只读 GET `/api/v1/camera/info` 超时，收集工具退出 1；没有继续请求或写入相机。记录见 [连接报告](../outputs/deployment/mfw-services/camera/runtime/20261031T005636Z/report.json)。因此当前仍缺实机 Custom → dispatch → Shell 的前半段数据，不能把模拟器结果归因于实机某个检查。

## 最小实机测量

明确部署正确入口后，沿用 RAM 日志，不在 SD 上持续写时序日志：

1. 确认 RAM 运行版本、Custom enabled/ready、`NO_PANEL_SHORTCUT` 和媒体暂停状态。入口禁用时先解决入口选择，不测无效延迟。
2. 正常开机测首次打开、再次打开各一次，读取完整链路；有波动再增加样本。不要清整机缓存制造冷启动。
3. 空闲约 5 s，对照资源和 renders/wakeups，确认无持续无变化重绘；再测方向键、触屏、关闭、重开。
4. 检查 S1 关闭/焦点恢复、原厂菜单按键；回放/设置界面的 Custom 应执行原厂操作。用正常拍摄确认，不以 X11 返回值代替拍摄验收。
5. 同场景对照不开/打开菜单时的取景与拍摄，记录相机 CPU、响应和按键遗漏。未取得结果前不提高绘制频率、不启用常驻预热。

当前模拟器入口优化已完成本次配对测量；仍未改动的后端输出读取预算和备用关闭标记见上文。后续按具体慢段选择，不统一增加 sleep 或为此反复重启相机。

参考本地固定 Metol `ddbe8b119e046798e557add13837834f5e8d90ac` 的 [NXUI 说明](../work/reference/metol/test_server/nxui/README.md)，复用事件驱动、Expose 缓冲、任务串行、配置惰性生成经验。未采用 NX500 ISP 地址、capdtm 命令或 DRIMe V 控制逻辑；其中真机数据属于 NX500 作者记录。

## 2026-11-04 菜单绘制与切换准备

本次实体卡日志的两次 LUT 面板首帧绘制为 564／881 ms，风格提交总耗时 6817／7255 ms，其中预览同步为 6152／5935 ms。这是优化前的实机记录，不是新版性能。

本轮改动集中在菜单和控制事件：

- CPU 菜单保留完整绘制缓冲，仅将变化像素的包围矩形上传到 X11。首帧、尺寸变化和 Expose 仍完整上传；原厂取景缓冲不变。
- 菜单使用 ARMv7 VFPv3-D16 的 softfp 构建，保留原有软浮点参数传递 ABI，不启用 fast-math。单独更换该选项的 VM 对照没有显示整体绘制收益，不以此声称实机提速。
- 从只读发布的 RAM 资源创建面板私有硬链接，代替每次打开时复制并回读。引用保留原 inode，运行目录替换后仍有效；直接从卡启动或跨文件系统时保留原复制校验。
- 预览控制由现有 launcher 一次原生遍历 `/proc`，替代对每个进程分别启动 `readlink`。仍校验完整可执行文件路径、唯一进程和启动时间，loader 原有身份校验不变。IPCS RAM blob、loader、Reset、IQ 恢复及拍照仲裁均未修改。

同一 ARM guest、同一输入序列各取 50 个操作帧，改动前后 P50 为 **94.5 → 62.5 ms**，P95 为 **120 → 93 ms**；同一序列上传像素减少 **59.4%**。局部上传前后的微调界面截图逐像素一致。该耗时为输入出队至绘制请求提交，不包含实体按键链路和 LCD 扫描。

按键、触摸、滚轮、Fn 保存留在列表、OK 保存退出、原图微调及预览／Still 控制回归通过。独立检查还确认私有 RAM 引用在路径替换后仍有效、缺失目标进程被拒绝；模拟器中原生扫描 67 个进程耗时 36 ms。原生扫描没有与实机旧扫描配对计时，不能将该数值折算为实际保存节省量。

证据见 [本轮汇总](../outputs/review/menu-toggle-width/performance-pass/result.json)、[菜单 ARM 检查](../outputs/performance/lut-menu-entry/damage-selection-vm/report.json) 和 [Session 控制检查](../outputs/deployment/smartfilter-session/arm-vm/report.json)。新版实体菜单手感、LUT 保存总耗时及拍照写卡耗时尚未测量。
# 2026-11-04 预览入口、分组导航与字号修复

实体 `fn-key.log` 显示 LUT 页预览按钮执行了 SD 副本，被 RAM 运行检查拒绝；关闭 LUT 的恢复还报 `camera identity scanner unavailable`。前者来自面板把 LUT 数据根当成服务根，后者来自直接执行被 `ram-stage.sh` 设为 0400 的诊断 launcher。

面板动作现在优先绑定 `NX300M_MFW_RAM_BASE/runtime` 和同代 state，子进程保留原 SD 数据身份。只读进程身份扫描固定使用正常 `bin/` 下的可执行 launcher；诊断 loader、blob 仍按原机制复制到私有 RAM 作业。没有绕过 RAM／身份检查，也没有改变硬件处理后端。

主字体由 22 增到 28，辅助字体由 18 增到 22，并扩大上一款／下一款和百分比数值区域。列表上下键移动风格，第一项再按上进入分组，左右键／转盘切组，OK 或下键回列表；分组焦点有浅蓝边框。Fn 微调和保存回列表、OK 保存退出、MENU 返回保持既有行为。

针对性 ARM X11 检查使用真实菜单程序、触摸坐标与按键，验证直接 LUT 入口和继承 Fn 环境时均转发到 RAM 服务；服务调用在该检查中为记录器。列表中上键不能直接跳分组的回归检查位于 `outputs/performance/lut-menu-entry/list-up-navigation-vm/`，同时检查首项边界和下键／OK 回列表。Session 检查按生产布局把诊断 scanner 设为 0400，运行真实 wrapper、ARM loader 和模拟硬件后端，启停、选择同步、拍照让路和异常恢复通过，Open／Close 为 218／218、残留引用为 0。完整交互与 Session 报告分别位于 `outputs/performance/lut-menu-entry/preview-navigation-validated-vm/` 与 `outputs/deployment/smartfilter-session/preview-navigation-vm/`。这些检查不测量实体 LCD FPS 或实体色彩。
