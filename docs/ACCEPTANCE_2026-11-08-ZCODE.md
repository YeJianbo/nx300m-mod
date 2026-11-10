# 2026-11-08 ZCode 免刷机增强任务验收

结论：**不通过，当前 settings-runtime 候选不能写卡或直接上机。** 本次确认的是候选源码和构建产物存在，不能据此认定原厂增强已经接通。

验收依据为 `ZCODE_TASK_2026-11-08.md`、实际源码、重构建输出、最后一次 VM 原始日志及所测试夹具的 ELF。没有修改生产源码，没有写卡、刷固件或发布。机器可读结果及证据位于 `outputs/review/2026-11-08-runtime-controls/acceptance/`；保留 ZCode 原报告，不将原报告覆盖成验收结论。

## 已核实的结果

| 项目 | 结果 | 依据 |
| --- | --- | --- |
| 候选 blob、安装器、探针源码 | 文件存在 | 源码身份与 ZCode 报告一致 |
| 当前候选重新构建 | 通过，退出码 0 | `acceptance/build-result.json`、`build.stdout.log`、`build.stderr.log` |
| blob 与地址表 | 编译及静态检查通过 | blob 为 8192 字节，入口偏移 0，Custom 偏移 704，配置偏移 4096 |
| 自然 Ecore 线程入口 | 不通过 | 安装器存在确定性错误；原 VM 安装退出 69，整体退出 31 |
| 取景专属 Custom / 回放删除隔离 | 未完成 | 新入口未跑通，未接入部署，旧被动按键入口仍在 |
| 菜单活动电源计时 | 未完成 | NXUI 仍使用 vconftool 备选，没有向新入口发请求 |
| 独立动画候选运行验证 | 本轮未完成 | 没有当前任务新增的有效运行证据 |
| Auto ISO 12800/25600 真实接入 | 未完成 | 新 blob 没有 IQ 参数 37 提交，原有菜单偏好不能替代 AE 生效证据 |
| A/P 独立快门范围 | 未实现 | 本轮仍为逆向资料和方案 |
| CS 提交串行化、镜头拍摄快照/EXIF | 未完成 | 没有新增可关闭接口缺口的证据 |

验收重新生成的 loader 与原 VM 使用的 loader 字节身份不同，blob 身份相同。源码身份全部匹配；不把历史 VM 结果转记为重构建产物的运行结果。详情见 `acceptance/source-identity.json`。

## 阻断项

### B1：安装器的远程执行线程和 libc 入口没有初始化（P0）

`runtime/settings_runtime.c:135` 将 `main_thread` 初始化为 NULL，之后没有任何赋值，159 行直接传入 `remote_syscall()`。该函数在 `runtime/live_loader_core.h:216` 会解引用 `s->stopped`。

`inspect()` 只打开 libecore，没有打开并校验 libc，后续却使用 `t.libc.bias + STOCK_SVC`。现有 `app_bytes()`、`settings_checks` 没有接入运行期核验；构建脚本生成检查表不代表安装器执行过检查。

验收条件：从实际 attach 的线程集合选中并核验主线程；解析、校验实际 libc 映射和 svc 指令；确认原厂目标内存与可接受回调身份，再进入可恢复事务。不能通过删除身份校验绕过。

### B2：地址重定位、配置初始化和执行权限错误（P0）

`settings_runtime.c:186,195` 把 blob 符号的**偏移**直接写入原厂回调槽。构建结果中这两个值分别为 704 和 0；正确运行地址应包含 mmap 的 region。

165–178 行把配置字段写到 `region + offsetof(...)`，漏掉配置区偏移 4096，实际会覆盖 blob 指令。完整的 `t.config` 也没有复制到目标配置区；原 select、原 key handler、power-timer 函数地址等仍留在本地结构中。生产分支没有填入可用的 `launch_cmd`。

代码只 mmap 为 RW，没有把代码区切为 RX；`cacheflush()` 定义后从未调用。不能把注释中的“mmap → cacheflush → RX”当成已执行流程。

验收条件：统一 region+symbol 地址计算，完整初始化配置；验证原函数指针、面板命令、请求路径；按实际代码/数据分区设置权限并刷新指令缓存，再写入槽位。写入前后均需核对。

### B3：ARM syscall 包装不符合寄存器约束（P0）

`runtime/settings_runtime_blob.c:11–12` 使用普通 `"r"` 操作数，却没有把 syscall 参数绑定到 r0/r1/r2，也没有从 r0 正确取得返回值，未向编译器声明 r7 被修改。

已检查实际 ARM 反汇编：入口的 open 调用前，路径位于 r6，flags 一度放到 r7，随后 r7 又被 syscall 编号覆盖；调用后却比较 r6 判断 fd。实际生成的机器码没有实现声称的 open/read/write/clock 等调用。证据：`acceptance/blob-disassembly.asm`。

验收条件：使用符合 ARM EABI 的 syscall 包装并声明寄存器/内存影响。原 select 的参数、返回值和 errno 必须用有限定向运行验证，不能仅凭“纯 syscall”注释推断。

### B4：重复安装、关闭、失败恢复和唤醒未闭环（P0）

`inspect()` 清零 `t.region` 后没有识别现有 blob 的逻辑，`if(t->region)` 分支不能识别已有安装；生产分支还要求 key 槽始终等于原值，已安装后再次检查将拒绝。`CFG_OFFSET()` 也漏掉 region 基址。

`mode=0` 在 region 为零时直接成功返回；即便进入现有安装分支，也只修改 custom_enabled，没有执行工作记录声称的 select/key 槽恢复。key 槽已经写入而权限恢复失败时，`patch_attempted` 尚未置位，错误恢复会漏掉这次实际修改。

没有唤醒已经阻塞的原 select 的机制；“下一次自然进入 wrapper”不能替代阻塞唤醒、卸载和恢复协议。

验收条件：识别并校验已安装映射，重复安装不叠加；关闭/卸载语义明确并实际恢复原槽；从第一次实际写入前记录事务状态，失败后恢复原指针和权限；验证阻塞等待、身份变化与恢复中重入。

### B5：电源活动请求无法可靠过期（P1）

`settings_runtime_blob.c:83` 将 `changed` 固定初始化为 1，内容相同也一直按新请求处理。遗留 `pt 1` 文件会在每次进入 wrapper 时刷新 45 秒期限；菜单异常退出、请求文件残留时不能按声称的 watchdog 恢复。

`ck-action N` 的解析又嵌套在要求第三个字符为空格的 `ck ` 分支中，该命令第三个字符为 `-`，因此不可达。NXUI 目前没有新通道的发送端，实际电源问题仍未修复。

验收条件：有明确的请求身份/活动过期语义，相同请求不能无限续期；真实按键/触摸活动节流更新，退出和崩溃后恢复原厂定时器。等待期限必须在实际阻塞时也能得到执行。

### B6：VM 失败归因不成立，测试本身也需修正（P1）

原始 `settings-runtime-vm-run16/execution.json` 明确包含正常 readlink 返回值，例如 `/perf/settings_runtime.arm`、`/rescue/busybox`。它不支持 ZCode 报告中“readlink 返回空，包括 loader 自身”的结论。

实际夹具把 `slot` 设为 `&orig_select`（函数代码地址），随后解引用它的首个机器字并作为函数指针调用。所测试 ELF 的 `orig_select=0x10644`，首指令 `0xe3002034`；该值不在夹具可执行段中。证据：`acceptance/fixture-elf-evidence.json`。这足以解释夹具无法正常跑循环；原日志没有收集该进程的退出信号，因此不虚构具体 signal 结果。

探针还有这些直接影响结果的缺口：

- 事件写成 `{12,1,0}`，但真实 contract 和 handler 从 `event[2]` 读取 action，按下/释放事件没有对应上。
- 检查 bootstrap 时读取 `fixture-ready`，而实际 marker 是 `settings-bootstrap`，后者没有收集。
- `report['passed'] = all(...)` 又把初始 `passed=False` 纳入自身，结果永远不会为 true。
- `collect()` 调用原菜单模块的 vm.call；输出目录通过共享模块变量传入，但新 Linux 子进程重新使用菜单模块默认 socket，没有继承本探针的私有 socket。采集可能指向另一个 VM 通道，失败又被吞掉。
- Windows 主分支忽略传入的 `--output-dir`，重复 prepare；未从 FIXTURE_C 构建夹具，而是读取另一个目录的既存二进制。
- shell 只等待 done，没有读回确认卸载后的 select/key 槽；覆盖的内容不足以证明恢复。

验收条件：构建和运行同一份明确的 fixture；用可修改的函数指针变量模拟槽位；修正事件布局、marker、报告聚合和私有 VM 采集。保留失败日志，确认夹具活着之后再测真实入口，不把本次失败转嫁到实体相机上。

## 续修顺序及最小验证范围

1. 先修 fixture 和安装器 B1–B4，保留原厂身份与恢复措施。先构建，再串行执行一个隔离 ARM VM 的有限探针：自然线程、select ABI/errno、重复安装、关闭恢复、错误恢复及阻塞唤醒。
2. 入口通过后接取景 key 表和菜单活动；在同一个有限探针中覆盖取景触发一次、回放不触发、按下/释放、退出/无活动/异常结束恢复。修 B5，关闭旧入口时保留 ready 门禁。
3. 再完成独立动画运行验证、Auto ISO 参数37真实提交与读回、启动重放及首装/升级资源接入。UI 必须区分偏好和实际模块状态。
4. 之后继续 A/P 联合曝光约束和 CS 串行化取证；证据不足的项保留未完成，不默认启用。

本轮没有重复启动已知损坏的完整 VM，也没有新增大镜像。静态缺陷已足以阻断上机。待上面离线项通过，实体只验收取景/回放快捷键、菜单操作与退出后的超时行为、动画，以及 Auto ISO 不同模式和重启后的真实行为；无线、LUT 关闭、泛绿与其他既有实机问题仍单列，不能由本报告关闭。

原生调度列表已只读查询，未命中旧记录中的任务 ID；这不能证明会话已结束或任务未执行。实际源码、16 轮运行资料和交付报告证明它做过工作，但当前功能验收不通过。本轮没有重新派发任务。
