# 2026-11-10 ZCode 菜单与 LUT 控制性能优化工作记录

执行:ZCode(Start Plan / GLM-5.3-Flash / max)。任务:`docs/ZCODE_TASK_2026-11-10-performance.md`(调度回执 `outputs/review/2026-11-10-performance/schedule-result.json`,native scheduler,23:30 Asia/Kuala_Lumpur,一次性)。离线源码与 ARM VM 工作:未上机、未写卡、未安装、未推送、未发布。

备份:`outputs/review/2026-11-10-performance/backup/`(nxui_lut_panel.h、lut_reference_interp.h、lut_tune.h、nxui_ximage.h、smartfilter-live-preview.sh、lut-panel.sh、baseline-nxui-from-overlay.arm)。受影响文件均小范围备份,未备份全树。

## 0. 基线与口径

- before 菜单二进制 = 今晚 23:30 时基线:从 `outputs/performance/menu-damage/arm-vm/overlay.cpio.gz` 里恢复的 `perf/mod/bin/nxui.arm`(588608 字节,SHA-256 `0691a533612ef959bbdd44c9c143b6996fa03fd13a52c57b774f2847602f9b9c`),存为 `outputs/review/2026-11-10-performance/before-nxui.arm`。**不是**旧的局部重绘前版本。
- after 菜单二进制 = 本轮构建 `outputs/metol/nxui.arm`(SHA-256 见 result.json)。
- 性能数字区分:qemu-user 微基准(相对值)、ARM VM(软件口径)、实机(本轮无)。软件测试结果不宣称实体 FPS 提升。

## 1. 任务 1:参考图计算

### 1.1 成本测量(先测后改)

`outputs/review/2026-11-10-performance/refcost.c` → `refcost.arm`(ARM 交叉编译,qemu-user 运行,数据与面板一致:21504 像素 × 3 通道):

| 阶段 | 单次成本 |
| --- | --- |
| `nx_tune_table`(每次有效微调) | 2.475 ms |
| 全量更新(每次有效输入,原实现) | 11.10 ms |
| 其中未混合 LUT 颜色计算 | 9.58 ms |
| 仅强度混合 | 0.367 ms |
| 无 LUT(拷贝) | 0.588 ms |

结论:强度拨盘(每次 +5/−5)不需要重新插值——未混合颜色只依赖 (场景, 微调参数, LUT),与强度无关。位级一致性先在基准里验证(`bit-exact mix path: OK`)。

### 1.2 实现

`mod/metol/nxui_lut_panel.h`:

- 新增 `lut_reference_unmixed[NX_REF_W*NX_REF_H*3]`(21504×3 字节 ≈ 64.5KB,**单一缓冲区共享所有场景/款式**,不是每款缓存三张大图)和 `lut_reference_unmixed_dirty`。
- 未混合颜色只在 (场景切换、微调参数变化、LUT 有效性变化) 时重算;强度变化与按住原图只重做混合。
- 插值公式、舍入 `(v*255+511)/1023`、混合公式 `(color*strength+src*(100-strength)+50)/100`、黑白参数与微调顺序完全不变;`strength==100`(默认值)走等价快速路径。
- 场景切换的两处入口(键盘 space、触摸释放)都置 dirty。

### 1.3 VM 前后对照

`outputs/review/2026-11-10-performance/verify_refmix.py`(适配自 menu-damage 的 verify.py),一个私有 ARM VM 内串行跑 before/current,输入完全相同:

- 10 个阶段快照逐像素比较:列表页、微调页初始、813 次转盘突发+尾随换栏、场景切换 ×3、按住原图(≥300ms,按住中截图)、释放回参考、强度页初始、200 步强度突发(到 0)、20 步恢复(100)。**全部一致(10/10)**。
- 终态一致:field=7、strength=100、tune=[3,0,...];1033 次转盘全部消费;持续输入中保持绘制(full_batches≥20 且其中≥5 帧实际绘制)。
- 首帧后 CPU,两次独立运行(同 before/current,VM 调度差异较大):
  - 首轮(未混合缓存构建):1087.557ms → 812.677ms(−25.2%);
  - **最终回归(发布产物,含 PANEL_KEY 门控,sha 05cf2f7a…):898.593ms → 790.191ms(−12.1%)**;强度段的结构性收益(仅混合 vs 全量插值)在两次运行中一致,总差值随 VM 调度波动,以最终回归为准,软件口径,不宣称实体 FPS。
- 原始日志、20 张快照与逐阶段对比在同目录 `refmix-vm/`(report.json 含逐阶段 identical 布尔与哈希)。

## 2. 任务 2:LUT 应用控制等待

### 2.1 现有阶段细分(离线可测部分)

`outputs/review/2026-11-10-performance/sync-cost-vm/`(fixture 模式,RAM staged,私有 VM):

- fork+exec 地板价(shell -c exit 0):**22.8 ms**;id+uname 前置:122.0 ms。
- 完整 sync 包装(stub launcher,纯 shell 路径)中位 343.5 ms;真实 launcher 在无相机时于身份扫描处失败(43.6 ms)。
- 卡上实测(用户提供):preview-sync 1640/1851 ms、BACKEND 2198/2149 ms、早期 preview-sync 3422 ms——其中 loader 附加/停止窗口是硬件路径,本轮不改;card-persist 的 fsync(FAT)与 nx_tune_cache 首次合成的 5 次 fsync 均为崩溃一致性设计,不削弱。

### 2.2 有证据的重复准备削减(fork 裁剪)

包装脚本每 fork+exec ≈ 23-60ms(VM 实测),sync 路径有 3 次可消除的重复 exec:

- `id -u` 调 3 次 → `uid=$(id -u)` 一次,三处属主检查复用;
- `HERE=$(... $(dirname "$0") ...)` → `case $0 in */*) here_dir=${0%/*};; *) here_dir=.;; esac` + 原有 `cd -P && pwd -P`(符号链接解析语义不变)。

**先 A/B 后改生产**:`sync-a-b.py` 在同一 VM 配对跑 12 组(部署版 vs 候选版),中位 124.5ms → 98.5ms,**−26.0ms(−20.9%),12 组全部更快**;`outputs/review/2026-11-10-performance/sync-ab-vm/`。之后同样的 3 处编辑应用到生产 `smartfilter-live-preview.sh`(备份在 backup/),`sh -n` 语法通过;差异恰好 3 处,属主检查、符号链接拒绝、来源身份、锁与 guard 全部保留。

`lut-panel.sh` 的 `dirname` 同样裁剪(面板打开路径,每次打开省 1 次 exec),备份同上。

### 2.3 明确不改的

- 硬件 loader/blob/Session(attach、停止窗口)——本轮不改。
- `nx_lut_persist_selection` 的 fsync 与 `nx_tune_cache` 首次合成的 fsync——崩溃一致性语义,不能移出关键路径;重复选择已被 `nx_lut_selection_persisted()` 短路,无冗余 persist 可省(路径内容寻址:同参数同路径,persist 自动跳过;强度变化必须重写记录)。
- `ck`/`pt`/generation 语义、"已请求 ≠ 已应用"、Fn 只保存当前款、OK 保存应用并退出——全部未动。

## 3. 任务 3:剩余时间排查

### 3.1 已做:PANEL_KEY 日志门控

`nxui.c` 的 `PANEL_KEY` 行**未被 trace 开关门控**,生产中每个按键 press+release 各写一行 stderr。现改为 `trace_frames` 门控(与 PANEL_FRAME 一致);所有 VM 探针显式设 `NX_PANEL_TRACE=1`,断言不受影响(回归运行确认)。低频状态行(LUT_CHOICE/BACKEND/EXIT 等)保留。

### 3.2 已做:Still LUT Hook publish 频率(测量结论,不改 blob)

静态读 `runtime/lut_ram_blob.c`:`s->forwarded` 仅在上下文初始化(行 188)清零,**publish 后不复位**;`forwarded==7` 一旦成立,后续任何单通道 IPCS 写都会再触发 publish——静态语义从「每个完整三通道周期一次」退化为「每通道写一次」。但实际频率取决于原厂流水线调用 IPCS 的节奏(帧级?参数级?),离线无法确认;11-07 轮已加的 `stat_updates/stat_ipcs_completes/stat_publishes` 计数器正是为该测量准备。本轮不改 blob;最小实机步骤见 §5。

### 3.3 已查不改:菜单打开脚本链

卡上 FIRST_FRAME 分解(trigger 951ms / first_draw 300ms):进程内 300ms,其余为 fork + panel.sh 链 + SD 上 exec 588KB 二进制 + 动态链接——panel.sh 自身仅 2-3 次 fork(cd -P/pwd ×2),已很精简;`lut-panel.sh` 的 RAM 硬链接已避免重复拷贝执行体。剩余为硬件成本,无安全 shell 端收益。

## 4. 构建与测试退出码

- `tools/build_metol.py --menu-only`:0(两次:未混合缓存后、PANEL_KEY 门控后;最终产物 SHA 见 result.json)。
- `refcost.arm` 构建:0;位级一致 OK。
- `verify_refmix.py`:0(passed=true,见 §1.3;最终回归以最终产物重跑,结果见 result.json 的 refmix 字段)。
- `sync-a-b.py`(部署版 vs 优化版):0,passed=true。
- 过程失败(保留):首次 verify_refmix 直连 exec 超 120s 通道上限两次 → 改为后台启动+轮询 `.ppm.gz` 完成标记;首轮 gzip 文件名 marker 错误(gzip 成功无输出)→ 改为探测文件存在。均记录在 run log。

## 5. 未完成项与最小实机步骤

1. **Still LUT Hook publish 频率**:上机读取 `/var/run/nx300m-lut-status.json` 的 `stats.publishes/updates/ipcs_completes`,在预览运行时采样 10s。若 publishes ≫ ipcs_completes 的期望比值,在 publish 内复位 `s->forwarded=0`(一行),用现有 blob VM 探针回归。
2. **card-persist / loader 阶段的实机占比**:卡上已有 `LUT_CONTROL phase=... elapsed_ms` 日志;把最近两次 BACKEND/preview-sync 的日志与 ram-selection/card-persist/preview-sync 各阶段行一起保存,即可确认 fsync 占比,决定是否需要进一步工作。
3. **参考图缓存的实体收益**:卡上更新 nxui 后对比连续强度拨盘的流畅度(软件口径 −25% 是 VM CPU,不是实体 FPS)。
4. 部署链打包验证:`build_menu_release.py` 从 `mod/deployment/mfw/` 取 wrapper;下次打包自动带上本轮脚本修改,无需额外步骤。

## 6. 边界

未修改原厂 Reset、Still IQ、IPCS Session、拍照仲裁、RAW、LCD DMA 或运行时硬件补丁;未做视频或新功能;未恢复菜单实时取景/防息屏旧链路;未用移除检查、缩短超时、提前成功或异步隐藏伪造提速;不上机、不写卡、不安装、不推送、不发布。VM 用后已停止,可再生成的大型 initramfs 已清理,小型证据保留。
