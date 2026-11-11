# 2026-11-11 夜间性能任务验收

验收结果：离线修改通过，原交付报告有两项结论需要更正。未写卡、未上机、未推送或发布。本报告不关闭实体响应、无线授权和历史拍照问题。

## 实际执行与构建

原生 ZCode 任务于 2026-11-10 23:30:20（UTC+8）开始；对应 turn 为 `turn_4266febf-31e4-4e0b-a058-57ae41bb8889`，状态 completed，模型为 Start Plan / GLM-5.3-Flash / max，当前无排队输入。依据：`outputs/review/2026-11-11-performance-acceptance/native-status.json`。

实际改动为参考图未混合颜色缓存、场景切换失效、按键 trace 日志门控，以及两个 Shell 入口减少 dirname/id 进程。重新执行 `tools/build_metol.py --menu-only` 退出 0。重新构建产物与昨晚测试产物只有未加载的 ELF `.strtab` 不同，全部可加载节逐字节一致。19 个 SmartFilter 源文件与昨晚推送的基线一致；未恢复菜单实时取景或防息屏调用。

## 参考图与交互：通过

- 独立重新计算原始日志：1033 次转盘输入完整，最终 field=7、strength=100、tune=[3,0,0,0,0,0,0]，连续输入期间有中间绘制。
- 独立读取 20 张原始截图，10 对画面逐像素相同。
- 补测实际修改前/修改后的 `lut_update_reference()` 函数：两张不同的合成彩色 LUT、原图状态、三张参考场景、正负微调、0～100 每 5 一档强度及重复切换，共 3402 个组合逐像素一致。该补测为宿主编译的软件函数对照，不能替代实体成像。
- 最终 ARM VM 原始记录的首帧后 CPU 为 898.593ms → 790.191ms，减少约 12.1%；不采用前一不同构建的 25.2% 作为最终收益，也不将 CPU 降幅当作实体 FPS 提升。

依据：`outputs/review/2026-11-11-performance-acceptance/reference-result.json`，原始日志与截图在上一轮 `refmix-vm/`。

## 同步脚本：补测通过，原性能数字撤回

原来的 sync A/B 24 个样本均在 fixture 来源校验处返回 rc=1：其 NX300M_MOD_SOURCE_ROOT=/perf/mod，不符合生产 wrapper 的显式 fixture=/perf 契约。计时脚本忽略被测程序的非零退出码，不能以 report.passed 证明正常同步提速；原成本分解的 stub 样本也有同样问题。

本次新建独立、无网络 ARM VM，用正确来源身份、backend marker 和检查 apply 参数/4380 字节表的 loader stub 跑完整 Shell 路径，未修改生产检查。12 次成功运行均明确读到 apply marker、rc=0；轮流交换 before/current 的执行顺序。before 中位数 983.4385ms，current 855.425ms，这是有 stub 的软件包装成本，不是实体预览等待。8 个正常/悬空符号链接拒绝用例均保留异常目标，未决 guard 拒绝准入且未被删除。VM 已停止，大型临时 initramfs 已清理。

依据：`outputs/review/2026-11-11-performance-acceptance/sync-vm/report.json`、`sync-check.log`。这能支持当前简化 Shell 的正确性和软件收益，不能关闭卡上 1.6～3.4 秒 preview-sync 的剩余瓶颈。

## Still Hook 静态判断：更正

原记录称 `forwarded` 只在初始化清零，不成立。`nx_lut_ram_update()` 每次都调用 `selection(s)`，后者执行 `s->forwarded=0`。因此不能从当前代码推出“第一次三通道完成后，未来所有 Update 都变成逐通道发布”。同一个 Update 内额外重复通道与逐 Update 重复发布仍可另行测量，但不得根据原错误判断直接加一行复位。本次未改 Hook。

## 新发现与后续

1. 当前 LUT 选择仍通过 O_TRUNC 原地覆盖；预览 UI 的选择判断只读一个字符，空文件 EOF 也被 `first!='0'` 算成已应用。需要统一可靠选择记录与所有读取入口。
2. 缓存回收使用 stat/opendir 跟随目录符号链接；受控夹具证实可删除缓存目录外的文件（夹具文件都在本次验收目录内，没有触碰用户数据）。扫描只收集 64 个旧目录，100 个旧缓存一次只清到 60 个，未达到 24 的目标。具体修复与验证写入下一轮任务书。

依据：`outputs/review/2026-11-11-performance-acceptance/cache-evict-findings.json`；汇总结果为同目录 `result.json`。后续任务见 `IDLE_TASK_2026-11-11-performance-state.md`。
