# 无线、Telnet 与收藏任务验收

本轮离线验收未通过。热点/Telnet 已有可执行实现，历史正常链路的证据有效，但新增短测复现四处边界故障；菜单还有崩溃与凭据日志问题，收藏可靠保存任务没有实施。

验收范围为 `OVERNIGHT_2026-11-11-wireless-telnet.md`（含后补第 4 节）与 `NIGHT_TASK_2026-11-11-2300-wireless-telnet.md`。全程未连接相机/手机、未读写卡、未刷机、未推送发布。未修改生产源码；重建菜单产物及验收目录内登录工具，网络短测只在独立 mount/PID/net/IPC namespace 中执行。

## 已核实的完成部分

- 菜单本轮重新编译退出 0，但产生新的格式类型警告，见下方 P1-2。登录工具按源码头部构建方案重建退出 0，依赖为原厂 `libc.so.6`、`libcrypt.so.1`。两份构建日志在验收目录。
- 原 shell 报告确实有 `SUMMARY pass=133 fail=0`；本轮阅读其真实生产脚本与命令替身测试实现，复用正常用例结果，另补四个边界。
- 原 ARM VM 的 `guest/perf/telnet/results.txt` 确实有 32 个 PASS；原始 `login.raw` 含 `Login OK`、命令回显与对应执行输出，错误密码与禁用记录也保留。两份生产 Shell 的当前完整 SHA 与 VM `inputs.json` 一致。原协议正常链路结果可以保留。
- 服务 kit ZIP CRC 通过、共 59 文件，热点/Telnet 脚本、busybox、登录工具、debug 菜单都在包内；`.state/` 未随 kit 打包。此项仅核对独立 kit，完整 Windows/SD 发布链仍需同步新文件。
- 外部任务列表当前为空；工作日志记载本轮在执行会话中直接完成，定时创建曾被拒。这里确认磁盘交付与原始证据，没有把调度投递当成完成依据。

## 必须修复的项目

| 编号 | 优先级 | 问题与证据 | 修复与复验条件 |
| --- | --- | --- | --- |
| P1-1 | P1 | 收藏可靠保存未做。`nxui_lut_panel.h:182` 仍读单个 `lut-favorites`，`:332` 仍调用 `nx_atomic_text_write`；安装器 `favorite_keys/set_favorites` 也仍走单记录。此前卡上 26 条同名记录的恢复仅恢复可见内容。 | 统一菜单、安装器及其他入口的有效记录读取，采用已有可靠双槽模式；覆盖 27 款加载、修改、重开、短写/损坏回退、迁移和同值重试。遵守离线约束。 |
| P1-2 | P1 | 热点失败页存在无效字符串指针。`nxui_feature_pages.h:183` 将 `*feature_hotspot_state`（int）传入 `%s`。本轮真实菜单编译产生 `format '%s' expects ... char * ... int` 警告；失败状态首字符会被当作地址。 | 改正参数，并用实际菜单失败状态复验，不能仅重编。补键盘/转盘/触控导航与 MENU 返回。当前尚未运行完整 X11 崩溃探针。 |
| P1-3 | P1 | 密码不是“仅屏显”。`nxui.c:1085` 的通用 BACKEND 日志无条件记录完整子进程 `output`；`hotspot-credentials`、`telnet-password` 恰好通过该管道输出明文。另 `telnet_toggle.sh:119` 将密码作为 `mkpasswd` argv；隔离短测实际捕获该瞬时 argv，未打印密码内容。 | 对敏感操作分别处理屏显与日志；哈希生成不把明文放 argv。复验完整 UI→Shell 链，并在生成瞬间监测 argv，不能只在服务启动后扫描。 |
| P1-4 | P1 | Telnet stop 误杀非本服务实例。`:143–148` 按相同 BusyBox exe + 任意含 `telnetd` 的 cmdline 全局扫杀。短测在无 owner 时启动同一二进制的 2323 端口实例，执行生产 `disable` 后 `foreign_before=1 foreign_after=0`。 | 记录并核验自己的监听、进程实例及会话归属，只回收拥有的资源；补同二进制其他端口/接口和 PID 复用的保留测试。 |
| P1-5 | P1 | hostapd 启动后没有 PID 文件时仍残留进程。生产脚本在 `write_owner` 之前就调用 `stop_service`，清理器找不到未登记的新进程。本轮命令替身仅模拟“不写 pidfile”，真实 Shell 返回 `failed:hostapd-pid` 后仍有 1 个 hostapd 进程。 | 在启动阶段可靠取得新进程身份，或使用可收场的受控启动方式；不靠按名字杀进程。对 AP/DHCP 的无 pidfile、延迟 pidfile、异常退出逐项验证无残留。 |
| P2-1 | P2 | 热点状态假报开启。启动后杀死已拥有的 AP，生产 `status` 仍 rc=0 且 `state=on`。`report_status` 仅读状态文件，没有确认两个 worker 和接口状态。菜单也仅读状态文件。 | 状态查询反映实际服务；进程或地址丢失时显示退化/失败并允许重试。异常显示修复要与 P1-2 一起回归。 |
| P2-2 | P2 | 菜单三种输入测试未做，`probe_panel_idle_vm.py` 只改断言。现有构建与字符串检查漏掉了 P1-2。 | 复用已有菜单 VM 框架补三路动作、开启/关闭、密码仅屏显、失败可见、后台请求与 MENU 退出。 |
| P2-3 | P2 | 候选包菜单与当前重建不一致。kit 菜单为 `effec4b831b2cd1b…`，当前源码按官方入口重建为 `416d97e3523ff129…`。两个脚本与登录工具在 kit 中对应当前产物。 | 查明菜单构建输入差异；修复后重新构建/打包，记录源文件与包内产物的完整身份，重新验收实际候选。不要直接发布当前 kit。 |
| P2-4 | P2 | 连接说明错误：worklog §7 称 Telnet 使用热点密码；实际是独立生成的 `telnet-password`，登录工具直接提示 Password，不先询问 root 用户名。 | 说明改为先手动开启 Telnet，再在调试页读取独立 Telnet 密码；按真实提示连接，IP 使用实际显示值。 |
| P2-5 | P2 | BusyBox 随包许可证只有 1110 字节 Debian notice，引用了 `/usr/share/common-licenses/GPL-2`；未随功能交付完整 GPL-2 文本和明确的对应源码/构建取得说明。 | 补齐发行所需的许可证与相应源码取得/构建资料，在完整公开包路径验证；不要仅以名为 GPL-2.0 的 notice 算完成。 |

上表 P1-2、P1-3 的 UI 路径来自当前源码与真实编译诊断；四条 shell 边界有本轮运行复现。没有把尚未运行的完整菜单 VM 写成已通过。

## 四个新增短测

复用原隔离 harness 的环境、实际生产 Shell 与记录型命令替身；只在验收目录复制替身，并补两个故障注入条件（不写 PID、放慢 mkpasswd 以观察瞬时 argv）。生产脚本不加测试钩子。执行：

```text
Conda Python -X utf8 outputs/review/2026-11-12-wireless-telnet-acceptance/prepare_checks.py
gcc stub.c -o stubs/wireless_stub
timeout 75 unshare --mount --pid --net --ipc --fork --mount-proc --propagation private /bin/bash ./checks.sh
```

Shell 语法检查两份脚本均退出 0；短测执行退出 0，意味着复现脚本完整执行，不意味着产品通过。输出：

```text
stale-hotspot: rc=0, state=on despite dead AP
hostapd-no-pid: rc=1, orphan_hostapd=1
foreign-telnet-disable: rc=0, foreign_before=1, foreign_after=0
hash-argv: enable_rc=0, plaintext_password_seen_in_argv=1
TARGETED_CHECKS_COMPLETE
```

完整日志为 `outputs/review/2026-11-12-wireless-telnet-acceptance/report.txt`，分项输出位于 `cases/`。namespace 已退出；后续进程检查未发现这次短测或私有 ARM VM 残留。一次进程检查命令因 PowerShell→Shell 管道引号传递错误退出 1，改用直接调用 `ps` 后检查完成；未将该失败当成产品故障。

## 性能口径修正

历史四组数字是单次虚拟环境记录。本轮未重复整套性能测试。G3 的 `background_session` 只建立连接并等待，没有完成登录；`our_pids` 仅统计指定三个可执行文件，排除了独立登录工具和原厂 shell。因此 1016 kB 只能描述被计数进程，不能作为完整已登录 Telnet 会话的内存成本。

`boot_path_extra_workers` 的运行采样位于一整轮测试清理后，不是冷启动采样。默认不自动启动新功能可通过启动链静态检查保留，但“冷启动零新增 worker”的动态结论需单独测。真实功耗和射频仍按用户要求排除在本轮验收之外，不构成离线工作阻塞。

## 接续顺序

先修收藏可靠保存和菜单崩溃，再处理敏感输出、Telnet 所有权及 AP 无 PID 回收；补真实菜单三路短测和状态退化测试。最后同步候选包与连接说明。修复使用当前源码原位迭代，不动已稳定的 IPCS/拍照路径；全部离线完成后再验收，不先写卡。

机器可读结果、构建日志、源文件身份和包核查见 `outputs/review/2026-11-12-wireless-telnet-acceptance/`。原夜间报告与失败证据保留。
