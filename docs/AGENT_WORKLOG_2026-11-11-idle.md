# 2026-11-11 闲时修复:缓存回收、选择持久化与应用等待分段

执行:ZCode(闲时任务 `offpeak-4dc04d32-75a2-46e3-84a4-e02caa963d51`,用户授权一次性执行)。任务书:`docs/IDLE_TASK_2026-11-11-performance-state.md`;验收依据:`docs/ACCEPTANCE_2026-11-11-performance.md` + `outputs/review/2026-11-11-performance-acceptance/result.json`。全程离线:未上机、未写卡、未安装、未推送、未发布;未改硬件 loader/blob、Reset、Still IQ、Session、拍照仲裁、RAW、LCD DMA。

备份:`outputs/review/2026-11-11-idle/backup/`(全部受影响文件改前副本)。

## 0. 三项任务结果一览

| 任务 | 结果 | 关键证据 |
| --- | --- | --- |
| 一、LUT 缓存回收 | **离线通过** | `choice-store-vm/report.json`(PASSED,30 项断言)+ `tune-cache-evict-v9/report.json`(PASSED) |
| 二、lut-choice 可靠保存与统一读取 | **离线通过** | 同上:迁移/中断/损坏/Off/强度/空文件全部覆盖 |
| 三、应用等待分段 | **分段完成** | `sync-split-vm/report.json`(PASSED,12 次校验运行);硬链接复用经实测证据否决 |

构建:官方入口 `tools/build_metol.py --ui-only` 退出 0(nxui `ef8cb06b…`、lut_engine `6e4a4848…`,含全部本轮修改)。过程编译错误(见 §5)均已修复并重编。

## 1. 任务一:LUT 缓存回收重写(`mod/metol/lut_tune_cache.h`)

针对验收确认的三个缺陷:

1. **链接跟随**(`stat`→改 `lstat64`):`looks/nx-tune-*` 符号链接(活/悬空)永不跟随、永不删除,只计为 `links` 上报。删除循环中用 `open(O_DIRECTORY|O_NOFOLLOW)`+`fdopendir`,杜绝 lstat 与 opendir 之间被换成链接的窗口。
2. **扫描不完整**(64 条上限→取消):候选一次性收集(动态内存,硬上限 `NX_TUNE_SCAN_LIMIT=4096`,超限如实上报 `truncated=1`),按 mtime 排序后从最老开始回收。实测 100 个过期缓存从 107 清到恰好 24(上一轮同样输入清到 60)。
3. **删除失败仍计成功**:只有"全部生成器自有文件(`tune.txt`/`table.d4lut`/`table-stock3d.d4lut`)unlink 成功 **且** rmdir 成功"才算回收;任何失败(如自有名被目录占据,EISDIR)将该目录记为 `failed` 并保留,继续下一个候选,不减少 remaining。内部出现未知文件 → 整目录记 `anomalous` 保留。

保护与上报:RAM 当前选择、统一读取的已保存选择(含 legacy 回退)、刚生成的目录;新合成保留 60 秒宽限并计入总数。每次回收输出一行 `TUNE_CACHE_EVICT target/before/after/deleted/protected/fresh/anomalous/failed/links/truncated`(控制事件频率,非逐帧),受保护目录使总数高于目标时**如实上报**而非伪装达标。

## 2. 任务二:双槽 lut-choice 与统一读取

新增 `mod/metol/lut_choice_store.h`(复用 `lut_tune_store.h` 的双槽模式):

- **记录**:`.state/lut-choice.0/.1`,内容 `NXCH1 <槽代数> <校验和> <体>`;体为 `0\n`(明确 Off)或 `<RAM代数> <表路径> <强度>\n`(与旧格式同语法)。只重写较旧槽,ftruncate+写全量+fsync+回读校验,校验通过后才删除 legacy 镜像——写入中断时上一份有效记录必然仍在。
- **读取**(全链路唯一入口 `nx_lut_choice_read_root_full`):两槽取最高有效代数 → legacy `lut-choice` → 更旧 `lut-selection`;`recorded` 标志区分"从未记录"与"存在但损坏"。空文件、截断、校验和不符一律无效——**绝不读成已应用,也绝不读成 Off**。
- **写入端**(`lut_engine_shared.h`):`nx_lut_persist_selection` 不再 O_TRUNC 覆盖唯一文件;无 RAM 选择时写入明确 Off 记录。`nx_lut_selection_persisted` 按统一读取比较。
- **读取端统一**(全部改走同一 reader):
  - 引擎 CLI `boot`(`lut_engine.c`)与共享 `nx_lut_restore_selection`;
  - 菜单 `nxui.c` 启动选择恢复(legacy 文件继续可读,迁移自动发生)与 `lut_saved_config` 判定;
  - 预览状态 `nxui_preview_state.h:nx_preview_lut_applied`(修复"空文件 EOF 判已应用":缺失/损坏一律 0);
  - `lut-boot.sh` 启动门禁同时检查槽与 legacy;
  - 缓存回收的保护引用(任务一)。
  - 另增 `lut_engine.arm choice-status` 诊断动作(与既有 `persist-status` 同风格),打印统一 reader 的判定结果。
- 兼容性检查函数:`__lxstat64`/`__fxstat64`+`struct stat64`(与 nxui.c 既有模式一致;32 位 `struct stat` 变体在本项目文件系统组合上不可靠,诊断过程见 §5)。

Fn 只保存当前款不应用不退出、OK 保存应用后退出、MENU/跨风格草稿语义:**相关代码路径未改动**;引擎级 apply/off/persist 行为由探针覆盖(见未完成项 §6 的 UI 级缺口)。

### 验证(`tools/probe_lut_choice_store.py`,真实引擎 fixture,unshare 隔离 mount/PID namespace,tmpfs /var/run,qemu-arm)

PASSED,30 项断言全过,要点:legacy(含强度 80)读取与 boot 恢复;apply 后写槽并移除 legacy;新槽截断一半(模拟短写)→ 回落旧槽并恢复旧选择、旧槽字节不变;双槽损坏 → legacy(强度 55)回退并恢复;全部损坏 → `valid=0 recorded=1`、boot 拒绝(rc=5)不误报开启;空 legacy 文件 → 预览判定"未应用"(旧行为会误报已应用);`stock` 写入 Off → 模拟重启(清 RAM)后 boot 仍 Off、仍记录、未应用;强度 37 保存→重启→按 37 恢复;回收场景(107 候选→24,保护 2、异常 1、失败 1、链接 2,哨兵文件/双链接/未知文件/不可删除目录全部保留,RAM 与保存引用目录存活,回收后 boot 仍恢复)。

既有回归 `tools/probe_tune_cache_evict.py` 更新到新存储后 v9 全过(31→24,保护存活,boot 恢复);v8 原始证据保留未动。

## 3. 任务三:应用等待分段(`smartfilter-live-preview.sh` + `outputs/review/2026-11-11-idle/prep_sync_split.py`)

- **分段埋点**:生产 wrapper 增加 `sf_stage()`(读 /proc/uptime 的重定向 + printf 内建,**零 fork**),6 个低频阶段行:`preamble/identity/job-ready/table-ready/launcher/output`。昨晚的 fork 裁剪保持不变。
- **测量夹具**:复用验收轮 `verify_sync.py` 的成功路径夹具(检查型 loader stub:校验 apply 参数与 4380 字节表;每次运行同时验证 `fixture_apply_reached` 与 `rc=0`,不再出现"被测程序失败却计为成功"),单私有 ARM VM,before/current 交替 12 次。
- **结果**(VM 软件口径,非实体):12/12 通过;分段中位数 identity 140ms、job-ready 335ms、table-ready 350ms、launcher 60ms、output 10ms,分段合计 ~910ms / 总 ~993ms。埋点开销在噪声内(多次运行 current 反而更快,VM 抖动 ±300ms)。
- **评估后否决的优化**:RAM 资源硬链接复用(lut-panel.sh 同款)。实测证据:两个 tmpfs 挂载之间 `ln` 失败(EXDEV,隔离 namespace 复现)——相机上 staging 与 /tmp 是不同 tmpfs,硬链接必然回落到 cp 还多一次 stat/文件。已在代码注释中记录该结论,保持 cp+cmp。
- **未做的**:硬件 loader 事务、安全超时、身份检查、锁与 guard 生命周期全部未动。

## 4. 边界遵守

静态参考图、菜单实时取景、防息屏、电源 trigger 一律未恢复;未生成任何刷机产物;探针全部在隔离 mount/PID namespace 或私有 VM;所有自启 VM 已停(`private_vm_stopped`/无 qemu 残留),可再生成的大型 initramfs 已清理,小型原始证据保留。

## 5. 过程记录(如实)

- 传输层把 heredoc 中 `\\n` 折叠导致三处字符串字面量损坏(PERSIST-WARNING/SF_STAGE/`"\\r\\n"`),`sh -n`/编译捕获后逐一修复,最终 diff 复核确认。
- 实现过程中发现并修复自身引入的缺陷:布尔 helper 语义反转两处、`sscanf` 转换计数、`__fxstat` 32 位结构在部分文件系统上不可靠(改 `__fxstat64`)、探针 apply 参数多传一个 tune 值。诊断程序已删除,过程结论保留在本节。
- 菜单侧行为回归(§6)三次运行因 VM 时序超时,放宽 timeout 后前 6 个变体全部通过,`tune` 段停滞(见下)。

## 6. 未完成项与最小实机步骤

1. **`probe_lut_selection_vm.py` 的 tune/original/groups 段未通过**(本轮与历史记录均无完整通过记录;本轮在 Fn 自动应用等待处停滞,面板日志仅两条 `LUT_VIEW`,无 `LUT_CHOICE confirm`)。证据:`outputs/review/2026-11-11-idle/lut-selection-vm/`(前 6 变体: saved-mono、keys-from-off、tap-from-off、browse-mono、direct-saved、direct-off 在 set -e 下全部通过——legacy 文件经统一 reader 读取正确)。需要单独一轮定位(键盘注入时序或 UI 状态机),**不阻塞本轮两项修复**;Fn"只保存当前款"的 UI 级行为因此仍缺端到端覆盖(引擎级保存/恢复已覆盖)。
2. **同步等待的实体分段**:SF_STAGE 行已进生产 wrapper;最小实机步骤——正常应用一次 LUT 后从卡日志 `logs/fn-key.log`(或 stderr 记录)提取 `SF_STAGE` 行,把卡上 1.6~3.4s 的 preview-sync 按阶段归因;identity/job-ready 段若在实体上仍占大头,再评估同挂载点私有目录方案(本轮已证明跨 tmpfs 硬链接不可行)。
3. **Still Hook publish 计数采样**(沿昨晚结论):预览运行时读 `/var/run/nx300m-lut-status.json` 的 `stats.publishes/updates/ipcs_completes` 10 秒。
4. 双槽记录的实体掉电可靠性:文件系统夹具已验证中断/短写恢复语义,不宣称实体 SD 掉电已证明;首次上机后可对 `.state/lut-choice.*` 做一次读回校验(`lut_engine.arm choice-status`)。
