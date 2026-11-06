# NX300M MOD：离线复现与约三小时接续任务

> 2026-11-06 已完成后续离线验收，结果为部分通过，新增失败复现和继续修复入口见 [验收报告](ACCEPTANCE_2026-11-06.md)。本文件保留原任务与初始失败基线；不要将夜间已有通过结果理解为全部边界已完成。

本文件是当前接手入口，更新至 2026-11-05 本轮离线测试结束。需求全集仍见 [11月4日交接](AGENT_HANDOFF_2026-11-04.md)，今天上午已完成内容见 [工作记录](AGENT_WORKLOG_2026-11-05.md)。旧交接的“待实现”状态已部分过期，不能逐条重新开发。

用户最新要求：**现在先测一部分，保留日志给接手 Agent 分析；中间不上机。** 本工作包全程本地/ARM 模拟，不写卡、不连接实体相机、不推送、不发 Release。约三小时是任务估时，不要求等待或靠重复测试凑时长。

## 1. 当前范围与基线

- 权威源码：`.`；根目录没有 `.git`。`outputs/github-source/nx300m-mod` 是公开导出副本，不替代权威目录。
- Python 使用 Conda：`D:/conda/envs/nx300m-mod/python.exe -X utf8`。不要新建 venv。
- 本轮只新增两个诊断工具、运行离线测试并更新交接；**没有修复下面的新发现，也没有更换部署产物**。
- 当前真实 UI/引擎源码、脚本及诊断工具备份：`outputs/review/2026-11-05-handoff/source-baseline/`；文件身份：同级 `source-manifest.json`。接手修改前仅备份新增受影响文件，不复制整个固件/照片目录。
- 上午已实现每款草稿、不可变提交快照、原子微调/收藏写入、保存失败留页、满槽保护、目录同步未确认标记，以及拨盘/动画/6400 开关接线与开机恢复。以下是这些实现的剩余缺口，不是要求重做。
- 胶片模拟分类与简化名已经加入。当前10组：全部、收藏、富士、奥巴、宾得、松下、理光、索尼、自制、胶片模拟；保留旧分类编号，胶片为9。六款 Metol 胶片及柯达/伊尔福/CineStill 已归组。目录144项（含原图微调），122个显示名已简化；ID、源 cube、原生表未更改。
- 保持原生 9³/10-bit、Session、Reset、Still IQ、拍照仲裁；不跨帧持通道锁、不伪造引用、不写未知寄存器、不改 LCD DMA 或 RAW。不增加逐帧 CPU 图像处理。

## 2. 现在已跑出的证据

证据根目录：`outputs/review/2026-11-05-handoff/`。两组诊断共8个契约检查：4个符合预期，4个暴露缺陷。`diagnostic_completed=true` 表示诊断完成；**两个报告的 `contracts_passed=false`，不能写成产品回归通过**。

### A. 实际 ARM UI 完成逻辑：5个检查，3个失败

最终报告：`state-baseline-v3/report.json`；原始事件：`stderr.log`；场景结果：`stdout.log`；可核查的 C harness 和编译/执行命令：`probe.c`、`run.sh`。v2也保留；v3仅将模拟引擎的写入对象与UI内部草稿槽位解耦，避免未来合法的提前flush方案破坏测试准备，两次结果一致。

该 harness 包含实际 `mod/metol/nxui.c`，ARM 交叉编译后用原厂 libc/QEMU 用户态执行真实 `confirm_lut()`、草稿和 `poll_child()`；子进程结果为有限文本/退出码替身。没有 X11、实际 LUT 引擎执行或 ISP 事务。目录 fsync 故障在真实写入辅助函数中注入；引擎完成标记仍是替身。

| 检查 | 实际结果 | 含义 |
| --- | --- | --- |
| A/B各改参数，直接保存B并退出 | `quit=1, drafts_remaining=1`，A无配置文件；失败 | 顶部保存/OK会遗漏别款草稿 |
| 目录fsync失败后收到 `PERSIST-UNCONFIRMED` | `quit=1, drafts_remaining=0, submit_failed=0`；失败 | UI把未确认保存当成功，并清除草稿 |
| 收到 `PERSIST-WARNING` | `quit=0, drafts_remaining=1, submit_failed=2`；符合预期 | 正对照：现有已知警告确实能留页 |
| 引擎退出1并输出 `REJECT` | `quit=0, drafts_remaining=1, submit_failed=1`；符合预期 | 正对照：普通失败不退出、不丢草稿 |
| 前置诊断 + `FW_MOD PERSIST-WARNING`，退出75 | `retry_cached=0`；失败 | 第一行匹配限制导致同值重试未缓存 |

最初 `state-baseline/` 编译退出1：POSIX200809 下 `usleep` 未声明；尚未执行场景。只把 harness 改为 `nanosleep`，随后 v2 编译/执行退出0。保留初始错误，不将其算作产品缺陷或测试通过。

### B. 私有 ARM VM 实际 Shell：3个检查，1个失败

报告：`shell-baseline/report.json`；实际结果：`outcome.log`；三个场景：`normal.log`、`target-directory.log`、`temp-directory.log`；完整客体执行返回：`execution.json`；内核/启动：`serial.log`。

执行的是原厂 ARM Shell、`mv`、实际 `common.sh` 和 `panel-actions.sh`；只有 `camera_control.sh` 被替换为固定26字段成功响应，不验证固件驱动。VM使用 `-nic none`，没有实体相机或卡；`private_vm_stopped=true`，已正常退出，保留小型overlay。

| 场景 | 实际结果 | 含义 |
| --- | --- | --- |
| 正常保存 | `NORMAL_RC=0, NORMAL_FILE_VALID=1` | 正常对照成立 |
| 正式配置路径已是目录 | `TARGET_DIRECTORY_RC=0, TARGET_REMAINS_DIRECTORY=1` | 失败：临时文件被移入目录，脚本仍声称已保存 |
| 临时路径已是目录，写入失败 | `TEMP_DIRECTORY_RC=75`，存在正确警告 | 失败处理退出码正确，但实际诊断在结果标记前 |

真实输出片段：

```text
/perf/mod/mfw-panel-actions.sh: line 99: can't create .../fw-mode-lock.tmp.175: Is a directory
FW_MOD PERSIST-WARNING: 拨盘锁定已开启 · 原厂读回 P（已应用、未保存）
```

这为A组的“前置诊断”提供了实际 Shell 触发路径，不再只是臆造输出。目录异常为受控故障注入，不表示用户卡当前就有该目录。

## 3. 本轮 Review：优先修复的具体问题

### H1 · P1：直接保存绕开全部草稿的持久化

位置：`nxui_lut_panel.h::lut_key()` / 顶部保存按钮；`nxui.c::confirm_lut()`、`poll_child()`。

当前 `confirm_lut(1)` 只 stash 当前款并创建当前提交；成功回调只 drop 在途款，随后直接 `quit=1`。MENU退出对话框中的 `lut_leave_pick(0)` 才调用 `lut_drafts_flush()`。两条退出路径语义不一致，A组已复现。

最小改法：统一退出意图为1/2的“保存所有未存草稿、仅应用当前请求”路径；Fn的0仍只保存当前款并留页。不要在每次浏览时写SD，不应用所有浏览过的风格。保存部分失败时保留其余草稿并留页；成功过的款可清除，但不得丢失败项或错误地退出。同时核对独立“关闭LUT”入口，不能关闭硬件却顺带丢其他草稿。

验收：A/B编辑后顶部保存/列表OK/微调OK各走相同语义；Fn只保存当前；失败留页可重试；退出回主菜单与关闭面板都覆盖；实际后端argv仍来自不可变请求。

### H2 · P1：持久化未确认的协议漏接

位置：`lut_engine.c` 约268行；`nxui.c` 约1033–1065行。

引擎在 `nx_tune_save()<0` 时输出 `PERSIST-UNCONFIRMED`，但处理本身可能退出0。UI仅检查 `PERSIST-WARNING`，从而误报保存并清除草稿。目录rename已完成，不得显示“完全未写入”或“已回滚”。

最小改法：统一解析已应用/保存成功/保存失败/写入但耐久未确认，后两者保留对应草稿与同值重试意图，不退出。不要为一个标记改动重写整个引擎。若改用退出码，核查其他调用者，不能把已应用误记为根本没执行。生产引擎的标记与UI要一致，不只改fixture。

验收：A组全部通过；另做一次真实引擎到UI的有限持久化失败传递，避免只测人工标记。目录同步成功的同值重试才清除 pending。旧 `PERSIST-WARNING` 和普通失败两个对照仍需保留。

### H3 · P1：固件保存失败重试依赖输出第一行

位置：`nxui.c::poll_child()` 约989行 `!strncmp(output,"FW_MOD ",7)`。

退出75且有效结果行前有Shell诊断时，`mfw_fw_retry` 不写入；下次点击可能反向切换已成功应用的值。A/B两组证据已连通。

最小改法：按完整结果行解析既有固定协议，保留诊断；结合退出码和原请求动作判断“已应用未保存”，不要简单把任意诊断子串都当成功。成功重试相同值后才清理retry。原厂读回状态与保存状态分开。

验收：前置噪声/无噪声两条75路径均重试相同值；真正未应用失败不伪报；正常成功仍更新原厂读回。注意修复后测试可能进入 `feature_page()`；当前无X11 harness将 `mfw_preview` 固定为0，若扩场景需真实VM，不为fixture改生产分支。

### H4 · P1：Shell配置目标目录导致误报成功

位置：`mod/deployment/mfw/panel-actions.sh` 的 fw-* 偏好发布。

`mv "$tmp" "$data/.state/$pref"` 在目标是目录时成功，但没有发布期望的普通文件。B组已用原厂Shell复现。处理现有非普通路径和符号链接，失败返回75并保留硬件已应用的读回；不要递归删用户目录。沿用实际原厂命令能力，不假定有现代 `mv -T`。仍有并发目标变化时应明确安全边界，不能把一次前置判断称为完整原子替换。

验收：B组3个检查全部符合预期；旧配置正常保存兼容；异常目录/链接不被覆盖或误认成功。Shell当前没有完整目录fsync协议，不能因为rename成功宣称已完成断电耐久保证。

### H5 · P2：失败反馈与收藏的未确认状态

已静态确认但本轮未跑UI像素验证：

- `lut_leave_pick()` 失败只 `message()`，对话框继续遮盖，弹窗固定内容没有显示status。
- 满草稿/保存失败消息未统一设置 `lut_submit_failed`，普通页footer也可能不显示原因。
- `lut_toggle_favorite()` 的 `saved<0` 只写日志、翻转内存状态；没有可见未确认状态，同一操作重试可能反转收藏。

只做局部交互修正：显示可读且不被弹窗遮挡的原因；保留用户编辑；收藏记录期望的目标状态并同值重试，不能用“再点一次”撤销上次操作。不改总体布局、不增加动画。

### H6 · P0研究/修复：超时不等于恢复完成

已有源码证据：`outputs/review/2026-11-05-agy/parent-timeout-audit.md`。

- `lut_engine_shared.h::nx_lut_sync_preview()` 15秒、`lut_engine.c::service_control()` 20秒，超时向Shell PID发TERM/KILL。
- `smartfilter-live-preview.sh` 内层 launcher 预算30秒；launcher和loader对不确定恢复已有专门所有者/guard，不能杀掉它们来缩短等待。
- 仅收割Shell不能证明loader/恢复worker完成。共享 `nx_lut_lock()` fd未设CLOEXEC，sync子进程继承，而service_control会关闭继承fd；需梳理这两条路径的锁释放所有权，尚未证明一定死锁。

要求先画明最小软件状态机：idle→requested→running→draining/recovering→confirmed/failed/uncertain；UI可返回不等于资源已释放。最小修复复用现有身份记录/guard，明确谁确认恢复、谁持有和释放锁、谁拒绝重入。**不只是把超时加到31秒，不杀进程组、不强杀相机/tracer、不改Reset/Session。**

离线验收用有限替身：正常、迟于外层预算完成、脚本退出而恢复worker还活着、恢复完成后可重试、身份已变/重复请求。只验证协议/进程与锁，不执行真实ptrace或IPCS。若无法在时间内安全实现，交付状态图、具体未闭合分支及有限失败日志，保留候选隔离，不冒充无线修复。

## 4. 约180分钟任务分配

当前线程执行即可；没有额外授权不自行并行启动多个Agent。私有VM也必须串行，已有工具共用socket。

| 时段 | 工作 | 交付/停止条件 |
| --- | --- | --- |
| 0–10分钟 | 读本文和两组失败日志，备份将修改文件 | 能复述4个已复现缺陷；不泛读全部逆向资料 |
| 10–55分钟 | H1/H2：统一退出保存范围与未确认协议 | 修实际C代码；当前ARM状态诊断过关；必要补当前引擎标记接线验证 |
| 55–85分钟 | H3/H4：同值重试、正式配置路径检查 | ARM Shell诊断过关；正常路径与失败提示不回退 |
| 85–130分钟 | H6：恢复所有者/超时/锁生命周期 | 小范围状态图+有限故障日志，条件足够才修；无线实机结果仍留空 |
| 130–155分钟 | H5：保存错误可见、收藏未确认同值重试 | 实际ARM/X11截图/事件日志；顺带核对新胶片分类、方向键与滚轮 |
| 155–170分钟 | 下节选一个有证据的补充任务 | 只做一个，不引入逐帧I/O/硬件优化 |
| 170–180分钟 | 受影响构建、汇总报告与剩余项 | 记录最终源码/二进制身份；只复跑改动影响的检查 |

这是估时而非硬性到点合并。H6若消耗更多时间，优先压缩补充项；不得为凑任务时长重复构建或测试。修复先做实，不用“文档建议”替代已能完成的代码修改。

### 补充任务：按实际证据选一个

1. **菜单连续输入的CPU开销**：看 `nxui.c` 的 `next_frame=now_ms()+33` 与既有 `docs/MENU_PERFORMANCE.md`。只对拖动/连转输入合并绘制，单次按键尽快响应、输入不丢、最终状态必画。先用同输入序列保留前后日志；不要直接把所有页面一律限15Hz，也不做复杂全局增量渲染。
2. **Still Hook状态发布计数**：`runtime/lut_ram_blob.c::publish()` / `forwarded==7`。先计真实调用模型的软件次数及不变/变化状态，测后再决定内容去重；保持实际写表与错误发布，状态文件丢失要补发。若改blob大小/ABI，要走对应构建核查；不能拿ARM VM毫秒数冒充硬件收益。
3. **Auto ISO接线审计**：`exposure_controls.c` 的12800/25600与 `camera_firmware_mod.py` / `panel-actions.sh` / feature页目前0/6400是两套不同接口。先明确哪个capability和生产消费者可用，给出最小接线补丁或明确缺口；不要把6400枚举直接改成任意ISO，未验证功能不显示已生效。

## 5. 可以直接复制的测试命令

以下从权威根目录运行；示例候选目录不存在时使用。每次改用新后缀，不覆盖本轮baseline证据。诊断工具**不负责自动修复代码**。

```powershell
# 先查看本轮真实结果，不必为知道失败再次启动模拟器。
Get-Content outputs/review/2026-11-05-handoff/state-baseline-v3/report.json
Get-Content outputs/review/2026-11-05-handoff/state-baseline-v3/stderr.log
Get-Content outputs/review/2026-11-05-handoff/shell-baseline/report.json
Get-Content outputs/review/2026-11-05-handoff/shell-baseline/temp-directory.log

# 修复后：编译实际ARM UI并调用完成逻辑；不需启动全系统VM。
python -X utf8 tools/probe_lut_review_state_arm.py --output-dir outputs/review/2026-11-05-handoff/state-candidate-1 --require-clean

# 修复Shell后：私有ARM VM，与上面/其他私有VM串行。
python -X utf8 tools/probe_fw_persistence_vm.py --output-dir outputs/review/2026-11-05-handoff/shell-candidate-1 --require-clean

# 只改UI时，受影响构建：
python -X utf8 tools/build_metol.py --menu-only
# 同时修改引擎或其共享头时，用这一条代替menu-only，不必连续跑两遍：
python -X utf8 tools/build_metol.py --ui-only
```

两诊断默认退出0表示“诊断完成”，允许报告已知缺陷；加 `--require-clean` 后，契约未全部满足则退出1。编译、启动、客体执行等工具失败会返回非零/异常且 `diagnostic_completed` 不为true。候选通过条件必须同时是：命令退出0、`diagnostic_completed=true`、`contracts_passed=true`、检查数仍分别为5/3，VM报告已退出。不通过就读stderr/场景日志，不能改掉断言掩盖缺陷。

本轮实际执行以上两诊断未加 `--require-clean`，最终输出分别在 `state-baseline-v3`、`shell-baseline`；不是候选已通过。候选改动若扩展契约，应保留原有检查，按新的实际检查数记结果。

只有改到下列路径才补对应一项，避免一套套重复启动VM：

```powershell
# 真正修改设置桥/重试交互时：真实ARM菜单+脚本，固件桥仍是替身。
python -X utf8 tools/probe_fw_controls_vm.py --output-dir outputs/review/2026-11-05-handoff/fw-candidate-1
# 修改分组/列表/返回等交互时：复用此probe并只扩本次缺口。
python -X utf8 tools/probe_lut_menu_entry_vm.py --output-dir outputs/review/2026-11-05-handoff/menu-candidate-1
# 选择了绘制优化时：相同输入的前后操作帧日志，不拿它当实体FPS。
python -X utf8 tools/probe_menu_ximage_vm.py --output-dir outputs/review/2026-11-05-handoff/draw-candidate-1
```

`probe_fw_controls_vm.py` 的bridge初始 `animation=0` 是fixture，并非证明真实固件默认0；原厂MOD默认策略需读实际模块。它的 `passed=true` 仅是UI/协议软件证据。新诊断没有运行实际固件桥、无线、Session或LCD，不必重跑整个Session套件。

`probe_lut_menu_entry_vm.py` 现有fixture从历史zip取目录，不会自动覆盖最新胶片分类。要验胶片/收藏/分组导航，先把该probe的目录输入接到当前 `outputs/metol/looks.txt` 或本轮 `2026-11-05-agy/catalog/looks.txt`，保留引擎替身与原有导航断言；不能原样跑旧目录就声称新分类已验收。绘制probe也须确认实际输入覆盖本次改动的页面，不以首页截图替代LUT错误弹窗。

UI截图需要实际ARM/X11 probe抓取，不能用静态效果图替代错误反馈验证。测试新增的H6状态机尚没有现成“一条命令通过”的工具，实施者需交付最小可运行命令、退出标准与原始日志；本文件没有把它写成已测。

## 6. 无线故障及未完成需求的真实边界

用户反馈仍是“手机直接连接失败，进入相关界面后不能关机”。当前不能确定究竟哪个原厂无线入口卡住；没有故障线程栈/TracerPid，不预设Session/驱动/合成按键为根因。

已确认MOD手机页只是 `network-info`，不会开启热点/关联/DHCP。历史卡日志的daemon监听 `0.0.0.0:8781`，不能归因为127.0.0.1；daemon ready也不代表WLAN通。已有准入补丁日志早于最近故障，不能当同次启动证据。详见 `outputs/review/2026-11-05-agy/wireless/parent-result.md`。AGY无线任务超时，没有成功交付报告，不要把主线程分析写成Gemini已修好。

本三小时包先做恢复所有权与入口软件证据，真实关联/功耗/物理关机留待以后一次集中验收。**不在中间要求用户上机、反复卡死或开Shell。** 后续确需上机时最小验收：正常拍摄→进入连接→失败可返回→原厂拍照仍正常→正常关机；传图另验相机身份、缩略图、单张原图与重连。未完成这些不能关闭无线P0。

以前要求仍需继续，但不能把所有功能塞进三小时并承诺完成：

| 功能 | 当前状态 | 接续方向 |
| --- | --- | --- |
| 顶部拨盘锁、模式动画、软件切P/A/S/M | 菜单/桥/偏好与开机重放已接，ARM替身通过 | 修本轮同值重试缺口；实体动作以后验，指令拨轮保持原厂 |
| Auto ISO6400 | 菜单/桥已接，实体新版本待验证 | 修持久化；12800/25600另审曝光模块接口 |
| A/P独立最快/最慢快门区间 | 完整约束尚未完成 | 基于AE单位/消费者，S/M不介入；不能只改EXIF |
| 手动镜头泛绿 | RAM候选仍未满足生产开放条件 | 分析原厂CS默认及对象并发，不默认启用 |
| 镜头选择/记忆/焦距 | 已有机内基础 | App镜头库同步；拍摄时快照ID/焦距 |
| EXIF自动关联 | 未接拍摄事件 | 关联照片对应快照，在下载副本补信息，不在保存热路径重编码 |
| 无镜头Wi-Fi与普通拍摄中传图 | 有历史候选，当前无线P0未关闭 | 核对真实启动链/恢复/路由，NFC不作为普通连接前提 |
| LUT fit/legacy与校色 | fit已进CLI/正式构建基础，保留legacy | 查调用点和多风格证据；硬件色域/插值与预览-成片标定未完成 |
| 拍摄保存、开机/休眠性能 | 既有部分优化，最新实体分段数据不足 | 软件可测先测，不再盲动Reset/IQ或增加JPEG缓存工程 |

保持现在的800×480双栏和大触摸目标；MENU返回，OK保存成功回取景；Fn微调返回列表保存当前且不退出；转盘/方向键兼顾；“色调”措辞保留。新胶片分类不是再复制一套LUT，加载仍按需。不要恢复半按才生效或重复“应用预览”按钮。

## 7. 接手最终交付

更新本文件或同日工作记录：实际修改文件、为何能消除失败、最终构建退出码、各诊断真实字段、剩余失败及实机缺口。保留baseline和candidate日志，不把旧SHA的通过结果挂给新二进制。失败命令写清退出码及关键stderr。

本轮已把可复现的软件问题交到手；接手应修代码并跑必要回归，不止再写一轮建议。不修改发布包、测试卡或GitHub；交付候选与日志供之后统一评估和部署。
