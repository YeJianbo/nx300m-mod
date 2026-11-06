# 2026-11-06 离线验收

验收对象：[夜间工作记录](AGENT_WORKLOG_2026-11-05-night.md)所述H1–H6、Auto ISO 25600接线及菜单绘制优化。要求依据：[交接与验收条件](AGENT_HANDOFF_2026-11-05.md)。权威目录内核查，未连接相机、未写卡、未发布、未推送；本轮不修改生产源码。

**结论：部分通过，不能按“H1–H6全部完成”验收。** 常规路径确实修复，但补测再次复现Fn保存期间退出丢其他草稿；Shell异常路径检查仍漏悬空符号链接。连续输入调度的验收见下文与最终汇总JSON。

证据：`outputs/review/2026-11-06-acceptance/`。命令退出0不等于契约通过，分别查看报告中的 `diagnostic_completed`、`contracts_passed`。

## 1. 已确认的有效工作

- H1普通顶部保存/OK先保存全部草稿；H2接收 `PERSIST-UNCONFIRMED` 并留页；H3不再要求结果标记在首行；H4阻止正式目标目录导致的保存误报。这些是实际源码修改，不是只有文档。
- `state-candidate-9` 的7项检查、`shell-candidate-2` 的3项检查均记录通过；本轮对照了源码身份及关键原始日志，没有因新增失败否定这些已覆盖路径。
- `draft-candidate-4` 的真实ARM/X11菜单回归覆盖跨款草稿、请求快照、胶片分组和错误弹窗。查看了 `draft-s7-dialog.ppm.gz` 的实际图像，保存失败原因能显示在对话框内；转换副本为本轮 `dialog.png`。
- `fw-candidate-6` 的14项软件断言和 `selection-final` 导航报告对应夜间菜单SHA，固件桥/引擎仍有替身，不能据此声称物理ISO、LCD色彩或拨盘实测通过。
- Auto ISO 25600新增的是曝光桥接线与偏好重放，没有把6400防回弹锁错误扩展成任意枚举。12800菜单仍未接，25600缺少完整的关闭/恢复操作；状态栏来自保存的偏好，不是持续硬件读回。它属于已接线、待完整功能验收。
- H6移除了外层超时对Shell的TERM/KILL，并使引擎锁CLOEXEC；原厂Reset、Still IQ、Session本轮未作为优化对象更改。

## 2. 不通过：Fn保存期间MENU退出仍丢草稿 · P1

位置：`mod/metol/nxui_lut_panel.h:19` 的 `lut_request_leave()`；`lut_key()`忙碌分支；`nxui.c`成功提交的退出回调。

普通 `confirm_lut(1/2)` 新增了全部草稿flush，但 `lut_request_leave()` 在已有child或排队提交时，只把当前请求的 `exit_after` 改为1/2，没有走全部草稿保存。真实按键入口允许在忙碌时按MENU触发这条分支。

本轮运行实际ARM UI C完成逻辑，使用有限子进程完成替身，两个场景均失败：

1. A有未保存编辑，切B，Fn保存B；B执行期间按MENU；B成功后 `quit=1, drafts_remaining=1`，A无配置文件。
2. 相同编辑状态，在Fn任务尚排队时按MENU；之后B成功，同样退出并遗漏A。

日志：`state-exit/report.json`、`stdout.log`、`stderr.log`，诊断编译/运行退出0，`contracts_passed=false`。这里直接调用真实 `lut_key(XK_Menu)`，没有绕开忙碌入口伪造退出结果。

修复方向：在排队/在途任务升级退出意图时，安排独立的“完成当前任务后处理所有剩余草稿”阶段；失败留页。不要在按MENU时丢弃当前请求、应用其他浏览风格或强杀后端。target0的Fn本身仍只保存当前款，不要为了补这个缺口改成每次Fn都写所有款。

改变验收结论的条件：上述两场景均不带未存草稿退出，且Fn不退出、正常OK退出、保存失败留页三个原契约仍成立。还需覆盖回主菜单的target2，不能只补target1。

## 3. 不通过：悬空符号链接绕过正式目标检查 · P2

位置：`mod/deployment/mfw/panel-actions.sh:66` 与117附近，两处均有：

```sh
if [ ! -e "$target" ] || { [ -f "$target" ] && [ ! -L "$target" ]; }; then
```

`-e`对悬空符号链接为假，导致第一项直接放行；没有检查 `! -L` 的“不存在”分支。原厂ARM Shell补测：

```text
DANGLING_RC=0
DANGLING_LINK_PRESERVED=0
FW_MOD 拨盘锁定已开启 · 原厂读回 P
```

实际结果是链接被普通配置文件替换，并报告成功。原有三个Shell检查仍通过，新增边界失败，不应把它写成“所有非普通路径/符号链接已覆盖”。故障为受控注入，没有检查或修改实体卡。

证据：`shell-targets/acceptance.json`、`outcome.log`、`normal.log`中的 `DANGLING_CASE_OUTPUT`；基础三个检查仍在 `report.json`。私有VM已退出。

修复方向：两处分支都先拒绝链接，再判断不存在/普通文件；不删除异常目录和链接。临时文件仍采用可预测路径直接重定向，若声称完整的原子/防链接写入，还需复用已有安全写入机制，不能扩大当前有限Shell检查的保证。

改变验收结论的条件：悬空链接保留且返回75、普通保存成功、目标目录失败、正常符号链接拒绝；用原厂命令验证，不假定支持 `mv -T`。

## 4. 菜单连续输入：原性能验收不足

原 `draw-candidate-3` 显示painted 15→9，但只断言少画帧和最后一次输入延迟≤150ms；没有断言持续输入中仍定期绘制，也没有核对最终参数。少画帧不能单独证明交互改善。

源码疑点：`nxui.c:1824` 在输入批满足条件且到达 `lut_paint_hold` 时，先把截止时间重新推后66ms；1854才检查是否绘制。输入一直积压时可能不断延期，形成停手后才更新；1893的等待逻辑也未把hold时间作为新的唤醒依据，存在空等风险。

本轮第一组 `draw-sustained` 受测试脚本set-x输出1500个参数影响，当前菜单没有有效收到滚轮输入；其 `acceptance.json` 为 `diagnostic_completed=false`，**不能将该轮作为产品性能失败证据**。保留日志，随后仅关闭Shell trace并缩小为800次有限输入，对相同两个二进制重测，输出为 `draw-sustained-v2`。不增加新的常驻服务或实际LUT处理。

修正后的v2未完成：180秒启动期限到达，命令退出1（`TimeoutError: Coalesce VM startup`）；finally中QMP停止又返回 `ConnectionRefusedError`，导致原probe未写出report。核查handle PID 27910已经不存在，未强杀其他进程。本轮补存明确标注环境失败的 `draw-sustained-v2/acceptance.json`、工具stderr摘录和进程核查结果。

因此本轮**没有有效的新增连续输入性能对照**，不能采用首轮输出的218ms等数值作产品结论，也不能认为原来的40%少画帧已经满足持续交互验收。源码中的反复延期问题仍需修正/证明；下一轮只在恢复离线环境后做一次覆盖持续输入与终值的短测，不重复当前失败启动。测试修正仅在本轮输出目录，不修改生产probe或产品代码。

## 5. H5/H6仍有未闭合项

**收藏未确认提示尚不能按“用户可见”验收。** `lut_toggle_favorite()`调用 `message()`写status，并保留同值重试；但普通LUT页footer只有 `lut_submit_failed!=0` 才显示status，收藏分支没有置相应可见通知。状态机的同值重试已测，不等于提示已经画出来。这一项为源码确认，未另外注入X11像素故障。修复应使用明确的通知状态，不把收藏失败伪装成LUT提交失败。

**H6仅部分通过。** “超时不杀链”和CLOEXEC有实际实现；仍未完成交接要求的身份变化、恢复未结束时重复请求、真实脚本与恢复所有者的完整配合验收。现有8项fixture测试中，重复请求检查只是在引擎锁被显式持有时再取锁；随后提前关闭锁才测试恢复，不能覆盖“外层已退出、恢复仍活跃”的同一场景。guard场景后直接unlink来代表已恢复，也没有验证worker确实退出。原始stderr还有 `printf: %l: invalid directive`，表明guard替身的输出并不完全如记录所称有效。

生产预览脚本本身另有preview flock和guard检查；本轮核查到了这些保护，**没有据此认定存在可并发写硬件的实测漏洞**。同样不能反向把有限fixture通过写成“恢复重入与无线卡死全部修好”。补测应隔离mount/PID命名空间；现有用户态probe直接mount `/var/run`，不宜在共享WSL环境原样反复运行。

无线“连接失败且不能关机”没有新实体证据，保持P0未关闭。按本次要求，中间不连接实体设备，也不要求用户重现卡死。

## 6. 构建、身份与复现命令

本轮运行 `tools/build_metol.py --ui-only`，退出0；完整输出为 `build.log`，退出码为 `build.exit`。此后没有更改生产源码。

夜间被测菜单：`71ce95e5000e72597c9a4d8018427201783151f1b8995458a0701803ede0b33e`。

本轮重建菜单：`3319da829ee5a4c2a44f5a916514ec417935707164b676cd3275a36d05908029`；引擎：`1e14a9fc45ad2c9d2e99befa9621eab321ff1639d63c3d8ab77c380a55ea0121`。

从夜间原始overlay提取菜单，与重建菜单逐ELF section比较：仅非运行符号字符串表 `.strtab`不同，`.text`、`.rodata`等其余section一致。证据 `build-comparison.json`，原菜单另存 `producer-nxui.arm`；没有把不同SHA称为字节一致。

```powershell
# 本轮已执行的最小额外验收。各输出已存在，再运行须改新目录并保留旧证据。
python -X utf8 outputs/review/2026-11-06-acceptance/run_state_acceptance.py
python -X utf8 outputs/review/2026-11-06-acceptance/run_shell_acceptance.py
python -X utf8 outputs/review/2026-11-06-acceptance/run_draw_acceptance.py
```

Shell/绘制测试共用私有VM socket，必须串行。诊断脚本退出0表示运行完成；实际产品契约看各 `acceptance.json` / `report.json`，本轮额外失败不隐藏。原厂色彩、FPS、功耗、实际ISO、拍照仲裁和无线/关机均未由本轮软件测试替代。

后续顺序：先补Fn保存期间退出的草稿处理，再修连续输入调度与可见反馈，补Shell路径边界；最后补齐H6恢复期的真实软件协议测试。保持既有Reset、Still IQ、Session和RAW路径，暂不把当前候选当作全部验收完成的版本部署。
