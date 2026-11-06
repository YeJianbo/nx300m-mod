# 2026-11-06 夜间修复接手结果

本轮可离线验收项已通过。用户随后追加“先写卡测试”，已于2026-11-06 09:03（UTC+8）写入测试卡LOCAL_REDACTED，7个文件回读核对通过，原文件已备份至电脑；未推送或发布。实体无线故障仍未关闭。机器可读结果见 [result.json](../outputs/review/2026-11-06-night/result.json)。04:30验收报告保留原状；本文件记录后续修复后的状态。

ZCode在用户解除审批后完成了部分修复，随后因Start Plan额度耗尽停止。已保留其有效修改，由当前线程完成接手、审查和缺失验证，没有再调收费替代模型。额度错误原始证据在 `outputs/review/2026-11-06-0430/session-status-index.json`。没有并行写入生产文件。

## 实际修改

- `nxui.c`、`nxui_lut_panel.h`：Fn保存排队或执行中按MENU，成功后保存其他款草稿，再按target1回取景或target2回主菜单；任何未确认写入均留页。Fn target0仍只保存当前款。请求参数快照和已有草稿机制保留。
- `panel-actions.sh`：两个偏好保存分支先拒绝符号链接，再允许不存在或普通文件。异常目标保留，返回75并报告已应用但未保存。这是目标检查，不宣称能抵抗检查与rename之间的任意并发替换。
- `nxui.c`、`nxui_lut_panel.h`：收藏失败用独立notice显示到正常footer，不冒充LUT提交失败；同值重试保留。
- `nxui.c`：连续输入合并绘制，绘制耗时包含在帧预算；修正等待期限导致的潜在空转。触摸按下／释放都立即解除延后；仅在显式帧诊断模式退出时记录一次最终参数。
- `nxui_feature_pages.h`：12800／25600／3200统一检查实际曝光模块；偏好文案明确“已存”或“未读回”，不把磁盘配置当硬件当前值。
- `firmware-mod-restore.sh`：只对存在的配置等待相应桥；曝光上限不再被无关固件桥的全局提前退出挡住。没有偏好时不做原先无条件的等待。
- 删除ZCode加入 `smartfilter-preview.sh` 的 `resolve-uncertain` 入口：它曾仅凭PID退出清guard，不能证明硬件/IQ恢复成功。该文件是探针包入口，部署候选继续使用原有continuous wrapper，没有把探针脚本误装成实时预览脚本。
- `probe_lut_recovery_protocol.py`：所有挂载和worker进入独立mount/PID namespace；使用真实引擎控制函数和真实continuous wrapper的guard分支，有限worker替代硬件，不提前删除guard。
- `probe_menu_coalesce_vm.py`、`probe_fw_controls_vm.py`：增加本次所需的持续输入终值检查和Auto ISO独立模块／恢复／异常目标短测。保留原来的其他功能检查入口。

原厂Reset、Still IQ、Session、拍照仲裁、RAW、LCD DMA没有改动。没有新增常驻线程、逐帧LUT编译或JPEG重编码。

## 构建和验证

全部命令使用 `D:/conda/envs/nx300m-mod/python.exe -X utf8`。Shell／绘制私有VM串行执行，均已停止。

| 命令／证据目录 | 退出码／实际结果 |
| --- | --- |
| `tools/build_metol.py --ui-only`，`build-codex.log` | 0；ARM菜单、引擎及字体元数据构建通过 |
| `outputs/review/2026-11-06-night/run_state_exit.py`，`state-exit/` | 0；9/9，含排队／执行中target1/2、Fn0、普通OK和保存失败留页 |
| `tools/probe_lut_recovery_protocol.py --output-dir outputs/review/2026-11-06-night/recovery-isolated` | 0；11/11，实际guard拒绝两次；超时不杀链、引擎锁释放后仍拒绝重入、身份变化拒绝、worker退出后guard仍保留 |
| `tools/probe_menu_coalesce_vm.py --output-dir outputs/review/2026-11-06-night/draw-codex --before-binary outputs/review/2026-11-06-night/nxui-before-takeover.arm` | 0；813次输入全部处理、持续中间绘制、最终+3和焦点正确；停手50ms提交 |
| `tools/probe_fw_controls_vm.py --auto-iso-only --output-dir outputs/review/2026-11-06-night/iso-targets-codex-v3` | 0；三档菜单循环／独立恢复通过，两处分支共6个异常目标保留并返回75 |
| `draft-final-v4/`复用证据 | 原候选ARM/X11通过；已实际查看 `favorite-visible.png`，可见“收藏未保存”。本次未变更相应footer和同值重试实现；它不是新SHA整套UI再验收 |

Auto ISO补测有两份失败日志，没有覆盖：`iso-targets-codex` 退出1，旧夹具多下移一行，实际选择了状态入口，循环等待FW_MOD至上限；`iso-targets-codex-v2` 退出1，三档UI及恢复已成功，但夹具刻意移除固件桥后又测试依赖它的拨盘保存，返回1。修正夹具的行号和阶段依赖后v3通过，未靠放宽75断言通过。v1中停止夹具时有X11 BadWindow清理错误，记录保留，不作为实体菜单失败归因。

性能详见 [MENU_PERFORMANCE.md](MENU_PERFORMANCE.md)：同一VM首帧后CPU累计用时846.442→636.905ms，约减24.8%；末次延迟46→50ms，没有宣称改善。以上不是实体LCD或拍照写卡性能。

## 备份与候选

接手前源文件备份在 `outputs/review/2026-11-06-night/backup-codex-takeover/`，包括manifest；ZCode原始备份在 `backup-pre/`。接手前菜单为 `nxui-before-takeover.arm`。

本地候选：`outputs/review/2026-11-06-night/on-camera-candidate/`。7个文件为两个菜单入口、LUT引擎、菜单动作、字体元数据、命名后的目录和服务重载标记。没有增加LUT表；144个目录项所需表均已在卡上。卡身份为FAT32／LOCAL_REDACTED，Still build为1078378604，卡上blob完整SHA与当前引擎构建契约一致，不需要更换blob或硬件后端。

只读检查已通过：

```powershell
python -X utf8 outputs/review/2026-11-06-night/apply_candidate.py
```

离线验收阶段遵守夜间任务“不写卡”。用户追加授权后已执行同一命令加 `--apply`，退出0：先备份，再原子替换和回读，服务重载标记最后发布。只更新清单目标，autoexec、照片和用户参数未修改。部署记录为候选目录的 `deployment-result.json`，原文件备份在 `backups/20261106T010314373286Z/`。清单中的before身份保留为部署前基线，不再原样重复执行此一次性更新。

**当前测试卡没有加载firmware-mod／exposure模块。** Auto ISO及拨盘功能会正确显示不可用；本轮没有临时绕过模块检查或改启动链启用它们。独立恢复脚本的修复放在候选 `optional-firmware-controls/`，不会随菜单更新自动启用。后续这组功能需要单独完成既有preload部署和实机验证，不能把本轮桥替身结果算作实体ISO已通过。

## 后续集中上机步骤

候选写入并正常重启后，全程用机身操作，无需相机Shell。

1. 长按Custom打开菜单；方向键／转盘连续调亮度，再触屏短点选择参数。确认不漏按、停手后显示终值，MENU可返回。
2. A款改亮度，切B款改对比度，Fn保存当前款后立刻MENU返回。重开分别查看A/B，应保留各自参数，不串款。普通OK保存应成功后回取景。
3. Mono与彩色风格各选一次，OK退出后直接看取景并各拍一张，检查无需进相册或额外拍照才生效。关闭LUT后拍摄应恢复正常；预览独立关闭时JPEG仍按选中LUT处理。
4. 拍照后再次开菜单并正常关机。UI和拍照开销与原卡手感集中比较；VM数据不能代替该项。
5. 无线单独最后验：正常拍摄→原厂无线入口→手机关联→失败可返回→拍照仍正常→关机响应。缩略图、单张原图、断开重连另验。此前“连接失败且关不了机”仍为P0，不要因为菜单通过就标记修复。

未确认硬件恢复时保持uncertain拒绝路径，不手工清guard或杀恢复worker。它需要原有确认恢复路径／正常重启，不用PID消失当恢复证据。

## 尚未完成的原始需求

无线关联及关机故障、A/P独立最快／最慢快门约束、手动镜头泛绿的生产安全条件、拍摄时镜头快照和下载副本EXIF关联、NXConnect镜头库同步仍未完成。实际Auto ISO、预览／成片校色、LCD有效FPS、休眠功耗和拍照保存分段时间也没有被本轮软件测试关闭。后续按原交接继续，不扩展本次菜单修复为已经完成这些功能。
