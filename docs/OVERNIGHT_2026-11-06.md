# 2026-11-06 夜间修复与验收

时间均为 UTC+8，仅执行一次：01:30 ZCode 修复 → 04:30 Codex 验收 → 05:30 ZCode 按验收结果续修。

## 执行范围

权威目录为 `.`。先读 `ACCEPTANCE_2026-11-06.md`、`AGENT_HANDOFF_2026-11-05.md` 和 `AGENT_WORKLOG_2026-11-05-night.md`。当前结论为部分通过，既有成功路径保留，不重写菜单或硬件后端。

只用 ZCode Start Plan 的 `GLM-5.3-Flash`，high；不可自行换付费供应商、账号或模型。先做受影响源码和配置的本地备份，记录目录及修改文件；保留其他工作，不整树回退。实现修复、构建、自查及必要的离线失败回归，不止交计划。写入 `docs/AGENT_WORKLOG_2026-11-06-night.md` 和 `outputs/review/2026-11-06-night/result.json`，保留原始日志和非零退出码。

不上机、不写卡、不安装手机、不推送或发布。保持原厂 Reset、Still IQ、Session、拍照仲裁、RAW 和 LCD DMA 流程。Python 使用 Conda：`D:/conda/envs/nx300m-mod/python.exe -X utf8`。

## 01:30 修复清单与验收条件

1. **Fn 保存期间退出遗漏草稿**：`lut_request_leave()` 在已有 child 或排队时只升级 `exit_after`，漏保存其他款。修复为当前任务完成后处理剩余草稿，全部成功才按 target1 退出或 target2 回主菜单，失败留页；target0 Fn 仍仅保存当前款并留页。复用 `outputs/review/2026-11-06-acceptance/run_state_acceptance.py` 的真实 ARM UI 逻辑，覆盖排队/执行中 MENU 和两种退出目标，同时保留正常 OK、Fn 不退出及失败留页契约。
2. **保存目标悬空链接**：`panel-actions.sh` 两处检查先拒绝 `-L`，再判断不存在或普通文件。拒绝普通/悬空链接和目录、保留原目标并返回75；普通保存成功。复用原厂 ARM Shell 检查，不假定支持 `mv -T`，不删除异常目标，不扩大为无关重构。
3. **收藏未确认提示不可见**：普通 LUT footer 没画出 `message()` 的状态。增加明确通知状态和可见反馈，不能伪装成 LUT 提交失败；保留同值重试。读取真实 ARM 截图核对，既有保存失败弹窗无需重做。
4. **持续输入绘制调度**：检查 `nxui.c` 中到期再次延长 `lut_paint_hold` 及等待截止时间的逻辑。连续输入应合并为最新状态，但期间仍定期绘制、输入无丢失、终值准确、停手及时显示。不能仅用少画帧证明流畅。此前 draw-sustained 输入无效、v2 VM 超时，均不能当性能结论。恢复环境后只做一次有意义的短对照；失败如实记录，不循环重启失败 VM。
5. **H6 恢复协议**：在隔离 mount/PID namespace 补真实脚本与恢复所有者配合验证：外层退出后恢复 worker 仍活跃时重复请求、身份变化、worker 完成后释放 guard；不要提前 unlink 当作恢复成功。保留超时不杀链、引擎锁 CLOEXEC 和已有 preview flock。修复测试 `printf: %l` 错误。不能在共享 WSL 原样运行会 mount `/var/run` 的旧 probe。
6. **剩余既有范围**：上述完成后核对交接中未完成的曝光恢复、Auto ISO 12800/25600菜单及状态真实性；只在当前授权边界内完成软件可确认部分，不声称实际 ISO 或实体无线已验收。无线“手机连接失败且无法关机”为P0未关闭，先分析现有日志和软件生命周期，不能为验收要求用户中间上机。

构建优先 `tools/build_metol.py --ui-only`；只对受影响模块补必要构建。Shell/绘制私有 VM 串行，不占用或杀死其他项目进程。诊断退出0不代表产品契约通过，读取 `contracts_passed` 与实际日志。所有静态、VM、实体结论分开。

## 04:30 Codex 验收输出

核查 ZCode 原生任务的实际运行状态和源码、构建、原始日志；仍在运行时只读验收，避免两方同时写文件。输出 `docs/ACCEPTANCE_2026-11-06-0430.md` 和 `outputs/review/2026-11-06-0430/result.json`。

JSON 字段：`checked_at`、`scope`、`all_actionable_passed`、`remaining`、`zcode_status`、`source_identity`。只有本轮全部离线可验收项通过才将 `all_actionable_passed` 设 true；环境阻塞、未完成、缺证据均为 false。实体无线、色彩、LCD FPS、功耗及实际 ISO 单列未验证，不伪称通过。

## 05:30 条件续修

先读04:30两份结果及当前源码/工作记录。若 `all_actionable_passed=true` 且无仍可执行项，记录跳过并结束，不重复改代码。若未通过或验收文件缺失，依据原始失败证据完成剩余项，自查并更新夜间结果；缺验收文件不等于通过。

沿同一 ZCode 会话续接，若已有轮次仍在运行由原生会话队列串行处理，不另开并发写入任务；不得重做已通过项。保留具体外部阻塞，不把尝试或计划写成修复完成。最终列出完成项、命令退出码、证据路径和仍未解决项。

调度实际ID、启用状态和运行时间见 `outputs/review/2026-11-06-acceptance/schedule-result.json`；模型当前目录检查见 `schedule-preflight.json`。任务登记不等于模型已执行。
