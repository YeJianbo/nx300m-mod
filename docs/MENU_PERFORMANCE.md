# NX300M MOD 启动与菜单性能审计

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
              原生锁 → X11 → 原厂字体 + 预生成字符覆盖表
                配置读取/离屏首帧 → 映射/焦点 → 状态子任务
```

必须区分三条路线：

- `autoexec-connected.sh` 明确设置 `NX_REMOTE_CUSTOM_KEY=0`、`NX_REMOTE_PANEL_SHORTCUT=0`；合并包还带 `NO_PANEL_SHORTCUT`。这里没有菜单监听，按键无反应不能归因于慢。本轮没有恢复监听或删除标记。
- MFW 独立服务默认 Custom，旧 Fn 轮询必须显式启用。Custom 是一次完整按下/释放，不是长按 Fn。
- `lut-panel.sh` 是手动入口，另有每次复制 ARM 文件到独立 RAM 目录、检查 ready/begin 的前置工作。本轮未将它改为常驻或合并进按键路径。

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
