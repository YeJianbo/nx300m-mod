# NX300M 原厂 SLP 读取与 CRC 验证

2026-10-20 的本地结果：原厂 `fw_upgrade` 的 SLP 读取函数在启用 CRC 时接受已核对身份的 NX300M 1.15 文件；错误型号与首分区 CRC 损坏两个负对照均拒绝。执行的是独立副本中的只读 harness，原厂升级 `main` 未执行。

包含desktop与`hib2.sh`共用加载入口、七个原生模块的本地SLP候选已封装完成，331140200字节，见 `outputs/deployment/firmware-candidate/build-report.json`。原厂解析器已接受该候选，返回0、进程退出0，耗时24.669秒；原parser、CRC stream和CRC update均实际到达。汇总见同目录 `parser-report.json`，完整证据保存在 `firmware-validation/desktop-hib2-candidate-*`。

| 输入 | 型号参数 | 原函数返回 | 进程退出码 | 原 CRC 指令到达 |
|---|---|---:|---:|---|
| 原厂完整 `nx300m.bin` | `NX300M` | 0 | 0 | 是 |
| 同一原厂完整文件 | `NX300M_WRONG` | -1 | 255 | 否，型号处拒绝 |
| 完整文件，仅首分区 CRC 字段翻转一位 | `NX300M` | -1 | 255 | 是 |
| 此前hib2-only七模块候选 | `NX300M` | 0 | 0 | 是 |
| 当前desktop与hib2共用加载入口候选 | `NX300M` | 0 | 0 | 是 |

错误型号的原始诊断：

```text
[FW] [PROJECT] Set: NX300M_WRONG, Fw: NX300M
```

CRC 负对照的原始诊断：

```text
[FW] 0: [IMG-CRC32] Set: 0x2b188808, Fw: 0x2b188809
```

三个案例的标准输出均包含原厂版本提示：

```text
ver_full = NX300M_DSP_X_NX300MGLUANL1
```

## 输入身份与副本

- 默认输入为 `work/stock-1.15/nx300m.bin`，331,140,200 字节。
- 执行前后均核对 `work/stock-1.15/source.json` 中的本地 SHA-256：`75ededdc1e191ea3cdf8fb1201faced001d47b164224601a21150f75f8f7922f`。
- 此身份记录包含官方 ZIP 元数据与完整 ZIP entry CRC 的既有核验结果；这里的 SHA-256 是本地文件身份，并非三星发布的摘要。
- 原厂 ELF 为 `outputs/deployment/stock/usr/sbin/fw_upgrade`，逐字节核对等于 `rootfs.tar` 内 `usr/sbin/fw_upgrade`。其 SHA-256 为 `4683e697f8ad1134823825c3a8a8431b7d47a2400424a1dab8d15b921cd83c1d`。
- 损坏 fixture 保留完整文件长度及全部数据，只在文件偏移 68（`0x44`）由 `08` 改为 `09`，即首分区 CRC 从 `0x2b188808` 改为 `0x2b188809`。全文件比较确认恰好一个字节差异，未修改原厂输入。

## 静态调用范围

原厂 ELF 为 ARM `ET_EXEC`，首映射基址 `0x8000`，入口 `0x8d30`。CRT 在 `0x8d50` 从 `0x8d64` 读取原 `main=0xb33c`。

审查的本地函数范围为半开区间：

| 函数 | 原地址范围 | 作用 |
|---|---|---|
| 读取版本头 | `0x96b8–0x9818` | `open64(path, 0)`、读取 64 字节、关闭 |
| 生成版本提示 | `0x9838–0x991c` | 调用头读取函数及字符串操作 |
| SLP 读取器 | `0x9b68–0xa0a0` | 参数、文件头、型号、分区标识与 CRC 比较 |
| CRC 更新 | `0xa0e4–0xa120` | 原厂 CRC 表逐字节更新 |
| CRC 流读取 | `0xa128–0xa1f4` | 每次最多读取 4096 字节并调用 CRC 更新 |

`0x9b68` 参数为 `(路径, 型号字符串, 是否校验CRC)`，harness 固定最后一个参数为 1。其 `fopen64` 模式字面量经 PIC 地址解析确认为 `rb`。CRC 初值为 `0xffffffff`，返回值未作最终补码；与已有布局分析一致，字段对应 `zlib.crc32(payload) ^ 0xffffffff`。

这组函数的传递调用只有只读文件流、字符串/内存操作及标准输出/错误输出。静态检查约束所有本地调用必须落到表中入口，所有普通分支必须留在所属函数内；未发现间接调用、设备操作、系统命令或擦除调用。ELF 的其他代码虽然导入了 `system`、`ioctl` 等符号，但这些导入不在本次解析调用闭包中。

CRT 也已审查：`.init` 调用可选 `__gmon_start__`，该弱符号未定义；`.init_array` 只有 `0x8dac` 的 `frame_dummy`，`.jcr` 对象和可选间接注册目标均为 0；`.fini_array` 只有普通完成标记函数 `0x8d90`。其汇编保存在 `crt-review.asm`。

## Harness 的唯一修改

独立副本 `runtime-files/slp-readonly.arm` 只覆盖原 `main` 开头 36 字节，文件偏移 `0x333c–0x3360`，虚拟地址 `0xb33c–0xb360`。全文件比较记录 36 个字节差异，均在该范围内；ELF 大小、入口、解析函数、CRC 指令、字面量池及 CRC 表均保留原始内容。

```asm
0000b33c cmp      r0, #3
0000b340 movne    r0, #0x40
0000b344 bxne     lr
0000b348 push     {r4, lr}
0000b34c ldr      r0, [r1, #4]
0000b350 ldr      r1, [r1, #8]
0000b354 mov      r2, #1
0000b358 bl       #0x9b68
0000b35c pop      {r4, pc}
```

参数数量错误时返回 64；正常调用保持 ARM AAPCS 的 8 字节栈对齐，直接返回原解析器结果。没有成功结果替身，也不调用原升级流程。原 ELF 未被覆盖。

## 运行隔离与实际日志

每个案例通过 Debian WSL 的 `unshare --mount --net --ipc --pid --fork --mount-proc` 启动，挂载传播设为 private。在独立 tmpfs chroot 中仅放置：

- 已审查的只读 harness 与现有静态 `qemu-arm-static`。
- 来自原厂 TAR 的 loader、`libc.so.6`、`libpthread.so.0`、`libgcc_s.so.1`。
- 只读 bind 的 `/input/firmware.bin`；仅 `/dev/null` 与 `/dev/urandom` 两个设备。

chroot 根挂载为只读，执行 UID/GID 为 `65534:65534`，清空继承环境，未放置 `/proc`、`/sys`、MTD 设备、shell 或升级工具。目标 QEMU 使用 `-strace -d in_asm -E LD_BIND_NOW=1`，原始系统调用与指令日志完整保存。

实际 trace 中，原厂 ELF 的执行地址均落在所审查解析函数、CRT/PLT 与新 main stub 内；未到达原 main 后续内容或其他升级函数。所有文件打开均为 `O_RDONLY`；全部 `write` 目标仅为 fd 1/2。原厂正常案例记录 80,929 条目标系统调用行，错误型号 86 条，CRC 负对照 1,354 条。三者均未出现 `ioctl`、`execve`、设备写入或网络连接。

最初两次隔离准备失败发生于 ARM harness 启动前，已修复并保留：一次 `chmod` 尝试修改已只读 bind 的输入（退出 1，`Read-only file system`），一次清空环境后 PATH 缺少 `chroot`（退出 127，`No such file or directory`）。它们分别保存在 `setup-failure*` 与 `setup-path-failure*` 文件中，不计入上表有效案例。

## 复现与证据位置

在项目目录执行：

```powershell
D:/conda/envs/nx300m-mod/python.exe -X utf8 tools/probe_stock_slp.py
```

后续已有本地候选可增加 `--firmware <本地路径>`，保留原厂基线及两个负对照，并追加候选的同一解析器检查；候选拒绝时工具返回其非零退出码。该参数不会生成 SLP，也不会启动升级流程。

独立封装命令如下；它只替换platform、重算对应CRC和pcache偏移，并回读全部结构及五个未修改分区的字节：

```powershell
D:/conda/envs/nx300m-mod/python.exe -X utf8 tools/build_slp_candidate.py --platform outputs/deployment/ubifs/mod/platform-mod-candidate.ubi
```

输出为 `outputs/deployment/firmware-candidate/nx300m-mod-candidate.slp`。本轮候选检查复用了逐项重新核对身份的原harness、四个原厂依赖和三个基线结果，只调用 `run_case('desktop-hib2-candidate', ...)`；没有重复三个已通过案例。候选执行前后身份一致，全部open为只读；80929条目标系统调用中没有原升级main后续、审查范围外代码或禁用调用，唯一write为stdout。此前hib2-only候选的39.818秒成功结果保留于`mod-candidate-parser-*`，其封装与解析报告备份于`firmware-candidate/hib2-only-evidence.json`。

首次调用因Windows外层等待105秒超时而退出1，未取得解析器返回；随后独立根目录 `/bin/true` 也在20秒后超时，确认WSL调度故障。用户授权重启Debian后，`/bin/true`退出0，干净进程列表确认没有旧解析任务；随后唯一一次候选重试取得上述成功结果。故障和恢复保存在 `candidate-timeout-diagnosis.json`。工具现保留准备、依赖复制、ARM开始和结束阶段，并分别限制ARM90秒、Linux总120秒、Windows135秒；失败时也保存部分日志。

所有输出位于 `outputs/deployment/firmware-validation/`：

- `report.json`：有效案例、实际返回与证据边界。
- `harness.json`：输入身份、依赖来源、完整逐字节修改列表、静态调用边。
- `parser-callgraph.asm`、`crt-review.asm`、`main-stub.asm`、`main-stub.bin`：原函数和精确 stub。
- `*-host.sh`、`*.stdout.log`、`*.stderr.log`、`*-execution.json`、`*-result.json`：执行命令、完整原始日志、退出码及分析。
- `first-partition-bad-crc.fixture.bin`：完整负对照，不是可安装包。

读取器按声明长度顺序读取分区，未检查分区头的 offset 字段，也不要求最后恰好到达 EOF。因此它的接受结果也不代表完整 SLP 结构有效。

基线结果证明上述原厂 SLP 读取器及 CRC 路径在 user-mode QEMU 下的实际行为。原厂升级 main、完整升级状态机、签名策略、bootloader 接受、刷写和实机启动均未执行或验证。已生成本地开发候选；没有连接相机或刷写。
