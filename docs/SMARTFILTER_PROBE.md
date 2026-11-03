# S2 单次单位表探针（ABI4 / timing schema2）

**这是定位卡顿的受控诊断包，不是性能修复版，也不是持续 LUT 预览版。** 本轮只做本地构建与 VM 验证，不写卡，尚未上机验证。此前 ABI2 S2 实机验收未通过，ABI3 持续单位表也再次导致菜单超时且未取得分段日志；因此本版改为单次准入与独立存证，不能据此宣称相机已经安全、恢复正常或性能改善。

## 独立交付与版本边界

- 新包：`nx300m-smartfilter-s2-probe.zip`；报告：`probe-package-report.json`；本地叠加目录：`sd-probe`。
- 包含原诊断包的 15 个路径，再加入 `nx300m-mod/bin/smartfilter_probe_launcher.arm` 与 `nx300m-mod/smartfilter-probe-collector.sh`。包内 `README.md` 使用本说明；部署时映射为 `nx300m-mod/SMARTFILTER-PROBE-START.md`。
- 旧 ABI3 的 `nx300m-smartfilter-s2-timing.zip`、`timing-package-report.json` 和 `sd-timing` 保留，不用它们为 ABI4 新字节背书。旧 S1/S2 CLI 回归路径仍存在，但探针机身菜单不提供持续非零 LUT、跟随 LUT 或黑白入口。
- 必须使用同一构建的 ABI4 loader/blob。驻留旧 ABI 时不能只重载 MOD 服务、删除 state 文件或热切换以绕过检查；将来若另行授权部署，需正常关机、干净启动相机进程。
- S1 五槽仅用于回归，机身实验用 S2 七槽。不改原厂代码段、固件、RAW Bayer、JPEG LUT hook、风格库或开机自动注入。

## “一次”具体指什么

每次明确发起单位表探针，只允许一个受预算保护的 frame 最多领取一次 **begin 准入尝试**。固定 S2、内建 identity、强度 100、诊断开启；不是外部 LUT 测试，也不接受外部 table/look/strength 覆盖。

- 非阻塞信号量竞争返回 busy、拍摄竞争、控制冲突、render/restore/unlock 错误也会耗尽这次机会。**busy 不是等待下一帧再试；失败不自动重试，不重新 apply。**
- `RETURNED` 只表示外层 frame 已提交，不等于 `SUCCESS`。fallback 的 rc=0 不能掩盖 begin/render/restore/unlock 错误。
- 诊断未开启、冻结、缺少有效 trace、身份不符等会被阻止，报告 `BLOCKED`；不以诊断缺失为由继续操作硬件。
- 预算耗尽或被阻止后，后续节点回到 CPU copy + 输出 Flush 的透传路径，不再持续提交单位 LUT。reason11/12 是预算耗尽/诊断阻止，不是硬件 busy（reason6）。
- 保留 capture lease、准入双检查、原有信号量顺序和 Reset / Still IQ initialize / IQ process / close / unlock(post)。不跳恢复、不跨帧占用资源、不用超时强拆正在恢复的硬件。

**一次调用若不返回，单次预算也不保证相机解卡。** 预算只阻止后续重复准入，不中断已进入的 YCC、IQ 或原厂等待。后台化只隔离 UI/服务生命周期，不能证明 ISP 或拍摄资源已释放。

## 机身入口与明确的存证契约

探针菜单只保留 S2 原样、单位表单帧、状态、严格门禁导出、软停，以及返回/关闭。实际入口以当前已验证的菜单字节为准，不按固定菜单项数判断版本。

将来获得单独上机/部署授权后，单位探针入口标为 **“单位表单帧 · 灯灭勿拍照／自动存证”**。按下即是一次明确的受控试验及存证请求：

1. 先确认机身写卡指示灯已经完全熄灭，之前拍摄已结束；从按下起至明确得到任务结果，勿拍照、勿半途切换相机进程/存储介质，不拔卡、不切断仍在写入的电源。
2. 可先选择 **“S2 原样 · 不处理 LUT”** 检查原样路径。没有节点、没有 attempt 就如实报告未尝试；不要用额外快门、反复注入或强制调用原厂重建去“激活”。
3. 按一次单位表入口，离开面板等待结果。不要连续重复按，也不要在观察窗口轮询状态或反复导出。
4. 菜单短时确认 `READY` 的 nonce/PID/starttime 后即可返回取景，**不等 apply、snapshot 或保存完成**。`accepted/READY` 只是独立任务已确认接收，不表示硬件调用已返回或证据已落盘。
5. 任务先预创建唯一空目标，再仅 apply 一次；有限延迟后仅导出一次、保存一次。无 resident/无快照应报告 `no_data`，不能冒充“已保存”。错误或超时不重新 apply，不杀恢复 tracer。

### 为什么 pending 可以自动存证

这是独立的 **`idle-no-capture` 合作式契约**，不是放宽常规导出规则：

- 独立 job 使用不随服务代次删除的私有 RAM launcher/collector/loader/blob 副本，先 setsid、脱离 UI 进程组、清除 PDEATHSIG/继承 FD，并把标准输入输出重定向到 RAM。
- job 自行非阻塞获取预览操作锁和 media-io 协调锁，不继承 UI 锁；会复核 media pause、固定 source 目录 dev:ino、相机 PID/starttime 及预创建目标身份。有限等待或未决命令不会靠杀死恢复进程来“解决”。
- token 为 `pid:start:build:window:generation`。导出/保存必须绑定同一 token；相机重启、配置代次变化、source 身份变化均不能被当成同一次试验。
- `probe-save` 只复制已冻结且校验通过的原始文件，不重新编码活动记录；要求 Prepare/Finish 仍等于武装基线、captures=0。**Prepare 或 Finish 任何变化都取消写入，保留 RAM 证据**，不能将“现在 captures=0”替代整个试验期间未拍照。
- 在这些条件与用户“灯灭、勿拍照”承诺成立时，即使后端 frame 仍 pending，也可把冻结证据保存到预创建的独立文件。回执显式保留 `backend_pending`、`observed_capture_unchanged`、`probe_contract` 和 `persisted`。**pending 不会被改写成 `safe_to_persist=true`。**
- source-id、media lock 和 captures 基线不证明原厂全部磁盘 I/O 已空闲；它们只验证参与该协议的组件。此例外只供这个明确入口的受控试验，不适用于后台常开或自动拍摄。

生产目标固定为 `/mnt/mmc/nx300m-mod/logs/sf-probe-<32位小写hex>.ndjson`；VM fixture 使用 `/perf/sf-probe-<32位小写hex>.ndjson`。目标必须预先存在、为空、普通文件且非 symlink；最多 1 MiB，一次写入、fsync、读回核验。重复保存只允许核验同一 marker/旧范围，不追加一个新的 attempt。

普通 **“导出现有诊断 · 严格空闲门禁”** 仍遵守原门禁，不能借此按钮把 pending 例外扩展为一般可写卡。软停只停止新处理、保留旧节点完成回调和恢复路径，不保证立刻结束已挂起的调用，不等同强拆/卸载；完全退出依赖正常关机重启。

## 结果、失败与证据位置

- `persisted=true` 才是本次专用文件已保存并读回核对；仅 `READY`、`snapshot_created=true` 或 RAM 路径均不够。
- RAM 任务目录形如 `/tmp/nx300m-sf-job.*`，包含 collector 日志、阶段回执、token 和结果。冻结原始 NDJSON 位于 loader 回执指明的 RAM 路径。
- `pending` 表示调用尚未完成，只能给当前阶段和有效快照下的下界；`BLOCKED`、未尝试、busy/错误、取消、`no_data`、超时未决要分别保留，不能统称成功。
- 发生 Prepare/Finish/source/token/media-pause 变化时取消专用保存；预创建的空文件不算证据，RAM 数据在正常关机后会消失。
- 超时/未决保留 RAM 和保护状态，不自动重试、不删保护文件绕门禁、不杀恢复 tracer、不承诺自动恢复 UI/拍摄。先记录已有结果并停止叠加试验；仍有写卡活动时不得拔卡或强制断电。
- 本版没有自动完成弹窗；RAM `result` 与真实保存文件才是结果。默认 3 秒只是快照前延迟，30 秒是单条命令观察期限，都不是“等待这么久就能拔卡”的保证。
- 若面板再次无法打开，不要求先回菜单导出，也不要按快门激活或反复重试。仍有写卡灯活动时继续等待；灯完全熄灭后可尝试正常关机，再取卡读取专用文件。自动保存是否成功只能由文件及完整性校验确认；空文件不算证据，未落盘的 RAM 记录在关机后会丢失。

### 保存失败的具体诊断

普通 `diag-export` 与受控 `probe-save` 的文件错误回执会带有 `error_stage`、`error_operation`、`error_reason`、`error_errno`、`error_errno_valid` 和 `error_errno_text`，分别表示失败阶段、文件操作、稳定原因、原始系统错误码及说明。只保留首次失败；后续清理不覆盖它。短读、短写、类型/owner/内容不符等逻辑失败使用 `error_errno=0`、`error_errno_valid=false`，不拿上一次操作的错误码冒充原因。

- 普通导出失败时，面板保留具体回执，而不是只显示通用失败提示。
- 独立任务仍只确认受理，不等待异步保存；具体错误会留在 RAM `result` 的白名单字段以及原始 `diag.json/diag.stderr`、`save.json/save.stderr`。这不新增自动完成弹窗或额外写卡，RAM 信息仍可能在关机后丢失。
- `write_attempted` 表示是否尝试过目标数据写入；`destination_may_have_changed` 提示目标可能已有改动；`destination_bytes_verified` 表示目标数据已完成读回比对。三者都不代替 `persisted`。
- 即使目标数据已核验，后续 marker 失败仍按原规则返回失败、`persisted=false`，不会自动重试、补写、截断或删除目标。不要把失败回执理解成“完全没写”，也不要据此重复提交。
- 缺失、重复或非法错误字段不会被采集器猜测为成功；原始回执与 stderr 会保留。2026-11-02 的真实手动导出回执已定位 `destination/validate/owner_mismatch`；这不等于已确认同轮后台任务的失败阶段，也不能倒推所有历史失败均相同。

### 固定 SD 介质校验

原厂挂载可为 FAT/exFAT 合成并非当前进程的 UID，不能把 `卡文件 owner == 保存进程 euid` 当作通用条件。本版只在固定 `/mnt/mmc` 生产路径、实际打开的介质/目录/目标 FD 均通过设备和文件系统身份核对时，使用 FAT/exFAT 介质策略；不硬编码 UID，也不因 root 身份单独放行。`/perf` 和任意其他 FAT 路径不获得此例外。

目录链逐级 `openat`、nofollow，probe 的 source dev:ino 绑定至同一个实际使用的 root FD。普通 Linux 文件系统保留各调用点原 owner/权限检查；RAM 快照、SHA、marker、job、握手及控制锁不放宽。prepare 成功仍返回“路径 dev:ino”，失败则保留结构化首错；原始 `prepare.json/prepare.stderr` 与后续回执一并保留。

这不创造 FAT 上的 POSIX 权限隔离，不证明介质未被离线修改，也不能原子阻止拔卡或特权挂载变更。collector 已有目标 dev:ino 预检查继续保留，但本轮未增加目标 ID CLI，不能宣称已闭合预检查至 loader 打开间的同名空目标替换窗口。单次写、文件 fsync、读回及 marker 成功语义没有改变，不扩展为所有掉电场景的耐久保证。

## 电脑端只读分析

在另行授权的试验完成、正常关机并复制证据到电脑后，对本地副本运行：

```bash
python -I -B tools/analyze_smartfilter_timing.py sf-probe-<nonce>.ndjson --json probe-analysis.json --markdown probe-analysis.md
```

解析器兼容旧 schema1，严格校验 schema2 probe、BEGIN/END、序号、字节数与原始 payload SHA256。新的 `probe_attempts` 单独展示一次 wall 和各阶段、状态、原始 rc、阶段错误、FIRST/COLD/SELECT 及控制 epoch/overlap 污染。

- FIRST 冷帧是本探针的重要观测，不是稳态样本；单次结果不生成/混入 steady p95、入口间隔或屏幕/传感器 FPS。
- preview/effect/ensure/initialize/下层阶段可能嵌套，不相加、不拼残差，不把阶段 p95 相加。
- Create 的 initialize 是独立记录，仅按同 dump/generation 关联，不证明嵌套，不加到探针 frame wall。
- `slot` 是 frames[] 索引，不是导出 seq。冻结恰落在 CLAIMED 的 CAS 与 slot/flag 发布之间时，可合法没有 flag256 记录，不能据此推断一次成功耗时。
- VM 的 `measurement_source=vm` 不能当作相机性能；`camera` 也只是导出方声明，不是独立硬件成功证明。返回0不证明底层所有 tile 等待和像素正确。

## 本地构建、打包和部署检查门禁

主线程统一运行真实本地 VM 和打包。打包器自身不运行 VM、不构建二进制、不访问卡，缺门禁时直接拒绝：

- `arm-vm`、`ram-vm`、`s2-vm`、`probe-vm`、`collector-vm` 的真实 report 均须 passed 且 private_vm_stopped。
- S1 五槽 / S2 七槽；ABI4 构建报告、blob 头和大小一致。ram/s2/probe/collector VM 保存的 loader/blob 必须与最终候选逐字节相同。
- probe/collector checks 非空且全部为 true；collector 的原始 launcher/collector 也必须与其 source-files 逐字节相同，不拿旧报告替代新字节。
- `controls-menu-latest` 指向已完成的真实菜单 VM 报告，所有候选菜单文件 hash 仍匹配；不硬编码菜单项数。
- `media-fs/report.json` 必须证明私有 namespace/隔离根内的真实 FAT32、挂载 UID 与进程 euid 不一致、两种权限合成设置、生产保存代码/launcher、读回及幂等，且本测试挂载和 loop 已释放。source/候选摘要与 ARM 构建输入必须一致；skip、模拟 FS 或旧候选报告不能通过。
- 真正 FAT 层使用 native 生产保存代码与 launcher，并非完整 ARM ptrace/ISP 链；现有 ARM kernel VM 的 `/perf` 是 POSIX 文件系统。两层各自验证，不能互相冒充。exFAT 没有格式化工具时明确 `not_tested`，magic 单元测试不等于相机厂商驱动实测，不自动安装依赖。

门禁全部真实完成后由主线程执行 `python tools/package_smartfilter_probe.py`，只生成独立 probe 交付。离线检查使用：

```bash
python tools/deploy_smartfilter_card.py --s2 --probe --check-only
```

`--probe` 必须显式搭配 `--s2`，与 `--timing` 互斥；只选择 ABI4 / diagnostic / probe / schema2 的新报告和压缩包。`--check-only` 不打开卡卷、不要求 serial、不调用部署器；它不会开始后台任务或触发快门。实际写卡仍需另行明确授权、备份、读回和 FAT 状态核验，**不属于本轮工作**。
