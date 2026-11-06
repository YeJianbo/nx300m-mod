# NX300M MOD 夜间无人值守工作记录 · 2026-11-05(第二班)

> 后续验收补充（2026-11-06）：[后续离线验收记录](ACCEPTANCE_2026-11-06.md)确认常规修复和原有通过范围，但补测仍发现Fn保存期间退出丢其他草稿、悬空链接检查遗漏等问题。本日志下方保留实施者原始记录；最终完成度以验收报告为准。本次验收由主线程完成，没有委派独立代理。

执行者:闲时自动化 Agent(ZCode offpeak 免费档)。依据 `docs/AGENT_HANDOFF_2026-11-05.md` 与同日主线程工作记录;用户要求:H1–H6 全部修完、Auto ISO 实现 25600、性能优化、全程模拟器验证与 review 修正。未写卡、未发布、未连实体相机、未推送。动手前备份:目录 `outputs/review/2026-11-05-handoff/night-backup-pre/`(轮换后缀,基线 SHA 与 source-manifest.json 一致,无漂移)。

最终身份:nxui.arm `71ce95e5…`、lut_engine.arm `901eff8f…`;源码 SHA 见 §5。所有"通过"均指最终二进制上的复跑,不挂旧 SHA。

## 1. H1–H5 缺陷修复(交接 §3)

### H1 顶部保存绕开其他款草稿(P1)
`nxui.c::confirm_lut()`:退出意图 1/2 的确认在冻结请求前先 `lut_drafts_flush()` 全部未存草稿;部分失败→`lut_submit_failed=1`、留页可重试、不提交。Fn(意图 0)仍只保存当前款。**语义补全**:WARNING/普通失败完成时对在途款重建草稿(`lut_draft_mark_unconfirmed`),保证"当前款草稿保留到引擎确认"的正对照不变——A 组契约 1/3/4 全过。

### H2 PERSIST-UNCONFIRMED 协议漏接(P1)
`nxui.c::poll_child()` lut 完成段新增第四态解析:已应用+已存(原样)/PERSIST-WARNING(未保存)/PERSIST-UNCONFIRMED(rename 已完成、目录耐久未证)/普通失败。UNCONFIRMED:保留该款草稿并标 `durability_pending`(同值重试执行真实原子写)、更新已应用读回(origin)、`lut_saved_config=0`、不退出、不显示"已回滚/完全未写入"。引擎标记(`lut_engine.c:268-269`)本就区分两种返回,未改语义。
**真实引擎传递验收**(H2 明确要求):`tools/probe_lut_review_state_arm.py` 扩展第 6 检查——真 `lut_engine.c` 以仅 `/.state` 目录 fsync 失败的包装重编(表发布路径不受影响),QEMU 真跑 `apply nx300-hard-mono 100 3 …`,真实输出(含 PERSIST-WARNING selection + PERSIST-UNCONFIRMED tune,exit 0)喂给 UI 完成逻辑 → 草稿保留、留页。工具调试中修正三处装配问题(fsync 宏须先于共享头、hook 宏须后于共享头、fsync 失败须限定目录 fd),均属测试侧。

### H3 FW_MOD 重试依赖首行(P1)
`poll_child` busy_kind==5 段:`!strncmp(output,"FW_MOD ",7)` 首行匹配改为 `strstr` 全文定位固定协议行;exit 75 + `FW_MOD`+`PERSIST-WARNING` 组合 → 写 `mfw_fw_retry[同槽]`,同值重试成功才清;成功重建页面同样按 strstr。前置 Shell 诊断不再漏判,真未应用失败(rc≠75/无标记对)不伪报。

### H4 Shell 目标目录误报成功(P1)
`panel-actions.sh` fw-* 偏好发布:`.state` 须为真目录非符号链接;目标须"不存在或普通文件且非符号链接"才允许 tmp→mv,mv 后再校验结果确为普通文件;否则走既有 75+`FW_MOD PERSIST-WARNING(已应用、未保存)` 路径。不递归删目录、不假定 `mv -T`;check-then-mv 的并发边界在注释中明示。25600 新分支(§2)复用同套防护。

### H5 失败反馈可见与收藏同值重试(P2)
- 离开对话框第二行在 `lut_submit_failed` 且有 status 时显示失败原因(不再被弹窗遮盖);`lut_leave_pick` 失败补 `LUT_LEAVE_FLUSH_FAILED` 日志。
- 收藏 `saved<0`:内存翻转到期望态、显示"收藏已写入,耐久未确认 · 再点一次确认"、记录 `lut_favorite_pending_key/add`;同款再点=**同值重试**(不再反向),确认成功才清 pending。saved==0 仍为旧失败路径。
- X11 证据:draft VM 场景 7(封锁 .state → 对话框保存失败 → 留页 + 截图 `draft-s7-dialog.ppm.gz`);同值重试在 state probe 第 7 检查(注入目录 fsync 失败)。

## 2. Auto ISO 25600(用户明确要求)

审计确认:`runtime/exposure_controls.c` 具备 12800/25600 能力(`0x003b0010/11`),`auto-iso-policy-probe.json` 有 25600 原厂消费者证据,`camera_control.sh auto-iso` 已映射 3200/6400/12800/25600 并校验 `$4` 读回——缺口仅在 UI/菜单接线。最小接线(不把 6400 防回弹锁枚举改成任意值,两者分开呈现):
- `panel-actions.sh` 新增 `fw-auto-iso-max-25600`:经曝光桥(非固件桥)设置,校验 15 字段响应与 `003b0011` 读回,成功原子写偏好 `fw-auto-iso-max`;失败走 75+PERSIST-WARNING 同值重试(与 H3/H4 同协议)。
- `nxui.c`:白名单 + `mfw_fw_retry` 扩为 4 槽(max 独立槽 3);`nxui_feature_pages.h`:曝光页"Auto ISO 25600 上限"行,值来自偏好文件(未设置如实显示),模块检查用 `feature_iso_loaded`(曝光模块,非固件模块);修掉了首版漏分发调用的错误(回归中发现)。
- `firmware-mod-restore.sh`:新增 `fw-auto-iso-max`(合法值 3200/6400/12800/25600)开机重放,等待 **exposure** 桥 ready。
- 回归:`probe_fw_controls_vm` fixture 桥扩展为双桥(固件 26 字段 + 曝光 15 字段),新增曝光页 25600 场景与恢复重放场景(途中修正主菜单导航序——曝光是第 3 项;删除超时的冗余 pass)。**未验证项**:25600 在低照度/可行域不足场景的实际曝光表现、实体 ISO 行为——标注待实机;机内不提供"恢复未知默认值"的写入。

## 3. H6 恢复所有权/超时/锁生命周期(P0 研究+最小修复)

读 `outputs/review/2026-11-05-agy/parent-timeout-audit.md` 后确认比审计更糟的一点:昨晚加的外层 15/20s 超时会**先于**内层 launcher 30s 预算杀掉 shell,使 launcher 来不及发布 uncertainty guard(`/tmp/nx300m-smartfilter-preview.uncertain`),恢复 worker 成孤儿且失去重入保护。

**状态机**(已写入两处代码注释):`idle → requested(fork) → running(外层预算内) → draining/recovering(超出外层预算:launcher/loader 以自有 30s+/SIGUSR1 协作预算持有恢复) → confirmed / failed / uncertain`。uncertain ≠ failed ≠ recovered;UI 可返回 ≠ 资源已释放。

最小修复(`lut_engine.c::service_control`、`lut_engine_shared.h::nx_lut_sync_preview`、`nx_lut_lock`):
1. 外层超时改为**放弃等待,不发信号**:链留给其恢复所有者(launcher 会在它自己的 30s 发布 guard;脚本层已有 guard 存在即拒绝重武装);引擎返回原失败码但日志明确 `phase=timeout-uncertain` / `PREVIEW-UNCERTAIN`,sync 非零退出时若 guard 已存在则如实报告。未把超时改成 31s、未杀进程组、未动 Reset/Session/tracer。
2. `nx_lut_lock` fd 加 `O_CLOEXEC`:锁所有权归引擎进程本身;service_control 子进程本就自关 fd,sync 子进程不再跨 exec 继承(放弃的链不会在引擎退出后持锁)。
3. 预算可用 `NX_LUT_CONTROL_BUDGET_MS`/`NX_LUT_SYNC_BUDGET_MS` 覆盖(默认 20000/15000 不变),仅供有限替身测试,生产无行为差异。

**离线验收**:新增 `tools/probe_lut_recovery_protocol.py`(真实 ARM `service_control`/`nx_lut_sync_preview`/`nx_lut_lock` + tmpfs 运行时 + fixture 脚本):8/8 通过——锁 CLOEXEC、持锁时重复请求拒绝、正常完成、超预算迟完成(链被放弃且**事后完成**,marker 证实未被杀)、launcher 式 guard 发布(worker 存活时 exit 124 → 引擎如实报 uncertain)、guard 解决后可重试、service_control 正常/放弃两态;`PREVIEW-UNCERTAIN`/`CONTROL-UNCERTAIN` 日志证据在报告 `evidence` 字段。
**未闭合分支**(如实):UI 仍把 uncertain 显示为通用"预览更新失败/可重试"(重试会被脚本层 guard 以明确文案拒绝,链路诚实但未做 UI 专属文案);预览链之外的孤儿 shell 若真挂死仍需未来 janitor;无线实机取证不变(§6 边界)。

## 4. 性能优化(=交接补充任务 1)

`nxui.c` 主循环:连续拖动/滚轮突发(单帧批内 ≥2 个连续事件且无离散事件)时栅格化合并到 ~15Hz(`lut_paint_hold=+66ms`);输入仍每 33ms 批处理不丢;任何离散按键立即取消保持当帧绘制;突发结束下一帧必画终值;Expose 保留图像重绘不受影响。不做全局限频、不做增量渲染重构。
**前后对比**(新增 `tools/probe_menu_coalesce_vm.py`:同一 VM 同一轮转序列×6 作用于改前 `095f00c4…` 与改后二进制,`NX_PANEL_TRACE` 帧日志):painted 15→9(-40%),interpaint P50 520→714ms(合并生效),尾随单键 latency 71ms(阈值内)。ARM VM 软件栅格化数据,不代表实体 LCD FPS。工具调试中修正:基座 key 驱动仅支持单键,补多键 burst 分支(测试侧)。

## 5. 回归总表(全部最终二进制 `71ce95e5…`/引擎 `901eff8f…`)

| 诊断/回归 | 结果 | 报告 |
| --- | --- | --- |
| `probe_lut_review_state_arm --require-clean`(7 检查,扩展真实引擎传递+收藏重试) | exit 0,contracts_passed=true | `state-candidate-9/` |
| `probe_fw_persistence_vm --require-clean`(3 检查) | exit 0,contracts_passed=true | `shell-candidate-2/` |
| `probe_fw_controls_vm`(14 断言,含 25600 读回/恢复重放/同值重试) | passed=true | `fw-candidate-6/` |
| `probe_lut_draft_vm`(13 断言,R1/R3 状态机 + 当前 144 项目录 + 胶片分组导航 + 对话框失败截图) | passed=true | `draft-candidate-4/` |
| `probe_lut_recovery_protocol`(H6,8 检查) | contracts_passed=true | `recovery-candidate-4/`(引擎此后未变) |
| `probe_menu_coalesce_vm`(性能前后对比) | passed=true | `draw-candidate-3/` |
| `probe_lut_tune`(引擎持久化契约,lut_cycle sha 不变) | 通过 | `outputs/review/menu-toggle-width/tuning-arm/` |
| `probe_lut_selection_vm`(全目录导航/收藏/直启/RAM 路由) | passed=true | `selection-final/` |

迭代中间证据(state-candidate-1..8、fw-candidate-1..5、draft-candidate-1..3、recovery-candidate-1..3、draw-candidate-1..2)全部保留;失败均为测试侧装配/断言问题(已逐一注明),生产代码仅两处因此修正:25600 漏分发调用、对话框路径补日志。

最终源码 SHA256:nxui.c `aec0ad55…`、nxui_lut_panel.h `51e4b5b2…`、lut_engine.c `c106a30d…`、lut_engine_shared.h `c238300b…`、nxui_feature_pages.h `1adfa8e3…`、panel-actions.sh `2512e213…`、firmware-mod-restore.sh `e54418c6…`。构建:`--ui-only` 与多次 `--menu-only` 均 exit 0。两 Shell 脚本 `sh -n` 通过。

## 6. Review 自查与剩余项

自查覆盖:退出路径语义(H1 与正对照的兼容)、四态标记解析互斥性(双标记输出以 UNCONFIRMED 收尾,已由真实引擎检查覆盖)、Shell 边界(目录/符号链接/临时路径/写后校验)、锁所有权(CLOEXEC+放弃不杀)、重试槽位互不串扰(25600 独立槽)。

剩余(不冒充完成):无线 P0 实机清单(进连接页→失败可返回→原厂拍照→正常关机)不变;A/P 自动快门区间、泛绿、EXIF 关联、12800 菜单接线(桥已支持,UI 仅 25600)未做;H6 两条未闭合分支见 §3;25600 实机曝光表现待验;probe_lut_menu_entry_vm/probe_fn_panel_vm 的既有期望串漂移仍未修(与本轮无关,证据在前晚记录)。未写卡、未发布、公开副本未动。
