# 2026-11-07 ZCode 定时修复工作记录

执行:ZCode(Start Plan / GLM-5.3-Flash / high),接续主会话。范围:`docs/ZCODE_TASK_2026-11-07.md` 第一优先(LUT 总开关/独立预览偏好/实际后端三态生命周期)+ 问题表可离线项核对。全程离线/ARM VM;未上机、未写卡、未安装手机、未推送发布。修改前备份:`outputs/review/2026-11-07-zcode/backup-pre/`(8 文件,修改前 SHA 与 Codex 验收后状态一致)。

## 1. 第一优先:LUT / 预览偏好 / 实际后端三态生命周期

### 缺陷(任务书确认的四处)

- wrapper `stop|restore` 一律 `save-preview 0`,把 LUT 关闭触发的运行态暂停误存为用户关闭偏好。
- engine `boot` 的 stock 分支强写偏好 0。
- `nx_lut_sync_preview()` 只在 backend marker 仍为 smartfilter 时同步;marker 缺失后即使偏好为开也不会恢复。
- UI `nx_preview_state()` 缺"LUT 关闭,预览暂挂"语义。

### 修改

1. **wrapper(`smartfilter-live-preview.sh`)**:新增 `pause` 命令——与 stop 相同的原生排空(loader restore 映射、guard 放行),但**不写偏好**;`stop|restore` 保持为用户动作写 0。`$HERE` 语义不变。
2. **engine(`lut_engine.c`)**:`disable` 改调 `pause`;boot stock 分支删除偏好强写(stock 开机≠用户关预览)。
3. **engine 共享头(`lut_engine_shared.h`)**:新增 `nx_lut_preview_pref_value()`;`nx_lut_sync_preview()` 三态化——backend 在→同步新表;backend 停+偏好开→**detach 触发既有 `lut-boot.sh` 恢复所有者**(boot 锁持有期间 UI 显示"预览中/恢复中",worker 幂等重放刚暂存的 selection,失败记"预览未恢复"且偏好不变;fd 重定向 /dev/null,菜单管道及时 EOF,引擎不等 30s+ 控制链);偏好关→不启动任何硬件。
4. **UI(`nxui_preview_state.h`/`nxui.c`/`nxui_lut_panel.h`)**:新增 `NX_PREVIEW_SUSPENDED`(LUT 关+偏好开,标签"预览随 LUT 暂停");`nx_preview_action` 增加 LUT 上下文——LUT 关时预览切换发新动作 `preview-preference`(panel-actions 经引擎 `save-preview` 翻转偏好,不走预览链),满足"LUT 关时单独改预览只存偏好"。
5. **锁序核查结论**(未发现递归死锁,有证据):wrapper 对引擎的唯一调用是 `save-preview`,而它在 `lut_engine.c main` 于取锁**之前**早退(源码行序确认);loader 不回调引擎。因此 sync→detach worker→wrapper enable-s2→save-preview 无环。引擎锁 fd CLOEXEC(前轮已加)保持。
6. **安装器合同**:`installer_backend.py` 的 `smartfilter-preview.sh` hash 更新为新 wrapper(`22848bb1…`);打包映射(package_smartfilter_live.py)不变。未覆盖任何已发布附件。

### 定向验证(全部读取真实日志)

`tools/probe_lut_lifecycle_vm.py`(基于既有 lut-off VM 骨架,隔离 ABI7 ARM VM + fixture 相机;输出 `outputs/review/2026-11-07-zcode/lut-lifecycle-vm-v12/`,报告 `passed=true`,6 断言):

| 规则行 | VM 证据 |
| --- | --- |
| LUT 开+预览开→关闭 | backend marker 消失、lut-choice=0、**lut-preview 保持 1**、日志 `operation=pause phase=end rc=0`、无 open-export 附着 |
| 再开 LUT(偏好开) | detach worker 轨迹 `LUT_BOOT restored preview=1`、backend marker 恢复 smartfilter、引擎快速返回(apply rc=0,不等链) |
| 单独关预览→再切换/关开 LUT | stop 写偏好 0;后续 apply 全程无 marker、无硬件链(`preview-sync rc=0` 即返回) |
| stock 开机 | `boot` 后偏好仍 1、无 selection 写回 |
| 原生 Still/Session | `lut_live_loader status` rc=2 + `rw_slots_original:true`、目标 TracerPid=0 且可响应(与上卡候选一致的收尾验证) |

迭代过程(v1–v11)的失败均为测试装配问题,已逐一注明并保留:基础镜像旧 wrapper 无 pause(overlay 注入当前版)、loader 不识 pause(映射补齐)、25s/40s 外层预算内 enable 链未完(改为 detach 所有者架构——这一步同时修正了"引擎同步等待 30s 链"的生命周期缺陷)、trampoline 破坏 HERE(改 exec RUNTIME 副本)、native 槽检查次序(移到 Off 后)。

### 回归(最终二进制 `7cf9c74f…`/`0706fc74…`)

- `state-exit` 4/4(MENU 排队/在途退出 flush 后按 target1/2 退出、Fn-alone 不退出)——契约更新为 Codex 双槽 NXTP1 预设存储(`lut-preset-<id>.0/.1`),旧 `lut-tune-<id>` 文件名断言已过时,旧契约运行结果保留在 `state-exit-oldcontract/`。
- 主 state probe 7/7(--require-clean,exit 0)。
- fw_controls 14 断言、draft 18 断言全部 passed(SHA=最终二进制)。
- 两 shell `sh -n` 通过;engine/ui `--ui-only` 构建 exit 0。

## 2. 问题表逐项分类(本轮后)

| # | 问题 | 状态 |
| --- | --- | --- |
| 1 | 关闭 LUT 卡住 | 候选已上卡(前序);本轮生命周期改动消除"引擎同步等恢复链"新风险;**需实体** |
| 2 | 关闭/重开后预览状态改变 | **本轮已修**(三态生命周期,VM 6 断言);实体验证待做 |
| 3 | 手动镜头分享 | 资源已补齐(前序);**需机身复测** |
| 4 | RVF 401 | 未修;**需实体** |
| 5 | 图库删除误开 LUT | 未修;缺可靠上下文来源,不盲改 |
| 6 | 息屏 | 未接;需原厂电源管理 ABI |
| 7 | 拍照闪彩色 | **需实体** |
| 8 | 冷启动慢 | 与 #2 交叉已改善(恢复走 boot worker,UI 有 恢复中/失败 态);实体首帧待测 |
| 9 | 写卡慢/掉帧 | **需实体** |
| 10 | 可靠保存 | Codex 双槽 NXTP1 已落地(本轮核实);lut-choice 原地写+读回校验保留,FAT rename 风险已记录,不强行迁移 |
| 11 | 微调缓存回收 | 未修(需引用安全设计) |
| 12 | Still 发布去重 | 未修(先统计频率) |
| 13 | 预览/JPEG 色彩 | **需实体标定** |

## 3. 备份与命令退出码

- 备份:`outputs/review/2026-11-07-zcode/backup-pre/`(nxui.c、nxui_lut_panel.h、lut_engine.c、lut_engine_shared.h、nxui_preview_state.h、smartfilter-live-preview.sh、panel-actions.sh、installer_backend.py)。
- 构建 `build_metol.py --ui-only` exit 0(68 配方);shell `sh -n` exit 0;各 probe 退出码见 `result.json` checks(全部 0)。
- 临时失败日志(v1–v11 迭代)保留于 `lut-lifecycle-vm-v1..v11/`,不删除。

## 3b. 拨盘锁部署阻塞项关闭(离线证据,追加于当日收尾)

用户指出拨盘锁此前已成功实现——核实属实:桥/实验室物理验证/installed-VM 九模块共载/UI 开关+开机重放全在。真正缺口是 persistent rootfs 候选标注的 "cold-boot caller unconfirmed"。本轮以 `work/stock-1.15/rootfs.tar` 离线证据关闭:rcS 全文(else 分支不启动 app、snapshot 分支走 hib2)+ 全 rootfs 字符串扫描(启动器仅 desktop 与 hib2 两处,均已被候选补丁覆盖;go.sh 为无调用方遗留脚本,其余命中为监控/资源串)。分析全文:`docs/BOOT_CALLER_ANALYSIS_2026-11-07.md`。残余:rootfs 候选安装为固件级操作需授权执行;经验确认可用下次读卡 `/mnt/ubi1/fw/log/hib.txt` 佐证。拨盘锁状态由"未完整接入"改为"**部署待授权**(软件链闭环,含本证据)"。

## 4. 剩余与最小上机清单(仅本轮改动相关)

1. 关闭 LUT(预览开):原厂取景/成片恢复;`logs/fn-key.log` 出现 `operation=pause … rc=0`;`.state/lut-preview` 保持 1;面板显示"预览随 LUT 暂停"。
2. 重新 OK 应用同款:预览自动恢复("预览 开"),无需手动开预览。
3. LUT 关闭时在面板切预览开关:仅偏好变化,无"正在更新预览"链;再次应用后按偏好生效。
4. 冷启动(LUT 开+预览开):显示"预览恢复中"→"预览 开";人为失败路径显示"预览未恢复"且偏好不变。
5. OK/Fn 语义、关闭 LUT 后正常关机(第 1 项候选修复的既有待验项)。

无线 P0、RVF 401、息屏、缓存回收、发布去重未关闭,均已在 `result.json` remaining 中附原因与证据缺口。


## 6. 续作(同日第二轮,"没做的先做好")

### 问题11 · 微调缓存有界回收 — 已修
`lut_tune_cache.h` 新增 `nx_tune_cache_evict()`:每次成功合成新缓存目录后触发(控制事件,非帧路径);上限 `NX_TUNE_CACHE_MAX=24`;保护=运行 selection、持久 lut-choice、刚合成目录(三处路径名提取);60 秒宽限避免在飞行中合成下对手;`.nx-tune-*` 临时目录不碰;仅删文件+rmdir,失败留给下次。fresh 目录计入总量但不作牺牲者(防突发突破上限)。
探针 `tools/probe_tune_cache_evict.py`(真实 ARM 引擎+隔离 namespace):31 次合成→回收至 24,运行选择/持久选择/新目录幸存,boot 从持久选择恢复正常。报告 `outputs/review/2026-11-07-zcode/tune-cache-evict-v8/report.json`(passed=true)。

### 问题12 · Still 状态发布 — 第一步(计数)落地,去重留待数据
`runtime/lut_ram_blob.c`:per-context 计数器 stat_updates/stat_ipcs_completes/stat_publishes(常开,内存写);status JSON 增加 additive `stats` 字段;trace 门控行为不变。上下文 stride 升 `NX_LUT_RAM_CONTEXT_STRIDE_STATS_V3=4732`(lut_ram_blob.h/ld + engine 校验三处同步),不改 config ABI;blob/live_loader/remote 全部重建并通过构建合同。
实证:`lut_live_loader` VM 重跑 passed=true(报告 outputs/deployment/lut-live-loader/arm-vm/report.json),新 blob status 含 `"stats":{"updates":1,"ipcs_completes":1,"publishes":1}`(旧 legacy blob 无该字段,符合预期)。去重本身按交接要求等自然频率读卡数据。

### 问题6 · 菜单活动息屏 — ABI 取证闭环 + 软件接入(值语义待实机)
离线取证(pmctrl/power_manager/stock app rootfs 二进制):power_manager 经 `vconf_notify_key_changed(memory/pwrmgr/trigger)`(pmControlEventHandler)喂活动;stock 相机 app 自身引用 `memory/pwrmgr/trigger`(16 处)——即原厂活动通道就是该 vconf,NXUI 面板的 X 输入不会进它的 evdev 读者,故菜单内息屏。
实现(nxui.c):真实输入(按键/触摸/滚轮批)时,节流 2s、单在途子进程,fork `/usr/bin/vconftool set -t int memory/pwrmgr/trigger <0/1 翻转> -i`(输出 /dev/null);VM 无 vconftool → access 失败即 60s 冷却跳过,probe 行为零影响;父循环 WNOHANG 收割。**假设**:trigger 变更即重置计时(翻转保证变更);取值语义待实机确认(上机步骤:面板操作 6 分钟不息屏;退出/停止操作后按原厂超时正常息屏;若不生效,用 `vconftool -g memory/pwrmgr/trigger` 读回并对照 stock app 行为)。

### 工具与回归维护
- `lut_tune_store.h`:fstat 在 -std=c99 老 ARM glibc 不内联 → `__fxstat` 守卫(nxui 既有模式);fixture/toolchain 双路径保留。
- `probe_lut_live_loader_vm.py`:loader 新输出格式(worker 日志经 stdout relay,含 admission/stop-window 前缀)→ `loader_json()` 取末个 JSON 行;VM 重跑 passed=true。
- `probe_lut_review_state_arm.py`:真实引擎目录 fsync 注入在双槽存储下的真实产物是 per-look PERSIST-WARNING(旧槽完好,"未保存"语义诚实),断言更新为接受两标记,UI 契约(留页保草稿)不变。

### 最终回归(全部真实日志)
state-exit 4/4、主 state 7/7(--require-clean)、fw_controls passed、draft 18 断言 passed、cache-evict passed、live-loader VM passed(全部在最终二进制 nxui `b361435c…` / lut_engine `63415aa2…` / blob `702a7552…` 上)。

### 本轮后仍缺
- 实体:生命周期 5 步、关闭卡死复测、手动镜头分享、息屏 trigger 值语义、色彩/写卡性能。
- 软件:图库删除上下文(证据缺口维持)、发布去重(等频率数据)、lut-choice 双槽迁移(FAT 风险评估后)、RVF 401。
