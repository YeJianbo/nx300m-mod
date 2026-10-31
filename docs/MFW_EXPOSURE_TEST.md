# MFW 只读曝光启动候选

本包用于下一阶段核对 NX300M 1.15 的原厂 ISO 偏好、IQ 上限和 AE 状态。已在独立 QEMU 会话运行真实原厂程序，验证对象读取与拒写；硬件接口使用电脑替代实现，没有向实体相机加载或重启程序。

## 加载方式

审计的 MFW 1.1 `libmisc.so` 通过线程执行 `system(autoexec.sh)`；脚本的环境变量不会返回已经运行的原厂程序。现有模块依赖程序启动时的符号替换，不能靠复制文件或启动独立面板生效。2026-10-24 已采集机内实际库，8140 字节，与本地审计的 MFW 1.1 样本逐字节一致，确认本机使用该加载方式。

**实体加载暂停：本轮 SD 交叉占簇损坏了服务脚本及 remote 二进制。** 已备份并按用户授权通过读卡器修复文件系统，复查通过，20 个照片文件与备份相同；修复目录／分配关系不会恢复已被覆盖的程序字节，不能继续使用卡上的旧候选。RAM 服务候选仅在电脑准备，详见 [SD 修复结果](MFW_SERVICES.md#读卡器复核与电脑副本修复)。当前未执行原厂 app 退出、SO 加载、刷机或快照操作。

本候选只为一次新的原厂 app 启动设置 `LD_PRELOAD`，不改内部 rootfs、启动文件或休眠快照。没有将它挂到 autoexec、Fn 或 App 按钮。不要把 `launch` 加到现有 autoexec 中。

## 文件与行为

- `lib/exposure_status.so`：从原厂已创建的对象读取；没有 SetParameter hook，不调用 SetUserData 或 GetInstance。写请求固定返回 `-120`，不改变曝光、测光、快门或色彩；没有 LUT 和旧曝光补丁。
- `exposure-launch.sh`：默认 `inspect` 只打印型号、版本、相机 PID 和模块是否存在。显式 `launch` 要求 NX300M 1.15、root、ARMv7、可执行 tmpfs 和没有正在运行的相机 app。父监视器最多等待约 20 秒，独立发现 daemon 后的唯一真实 PID，核对 exe、inode、starttime、临时 SO 映射和新生成的 ready／readonly 标记后才返回成功；失败也不停止进程。
- 启动前复制只读模块到唯一 `/tmp/nx300m-exposure.*`，用 `cmp` 核对；仅相机子进程设置单库 preload，并清除 `HIB` 和库搜索覆盖。锁不传入相机；父监视器不继承该 preload。日志写到该临时目录的 `camera.log` 和 `launch.log`。

源码构建：

```powershell
python -X utf8 tools/build_exposure_controls.py --read-only
python -X utf8 tools/build_mfw_exposure_test.py
```

## 当前可做与尚缺的步骤

将目录放到 SD 仅准备文件。在相机已有命令入口中运行以下命令，只检查，不启动或停止相机：

```sh
/bin/sh /mnt/mmc/nx300m-exposure-test/exposure-launch.sh inspect
```

正常工作中的相机应打印实际 PID 和 `launch_allowed=no`。即使显式调用 `launch`，此时也应退出 1，提示拒绝第二实例，且不发送信号。

**当前没有经过实机验证的原厂程序优雅退出、硬件资源释放和重新初始化流程。** 在该流程确定前，不执行 `launch`，也不以 kill、SIGSTOP 或并行启动替代。还须确认系统不会自动补拉旧程序；脚本的扫描不能原子阻止系统监督进程启动相机。

离线反汇编已找到 `/usr/bin/aul_test term_pid <PID>`，它调用 `aul_terminate_pid` 并发送 opcode 4，与 `aul_kill_pid` 的 opcode 8 不同。但 launchpad 还含终止包发送失败后使用 SIGKILL 的分支信息，不能认定该入口一定温和。`/usr/bin/kill_app` 明确调用 `kill(PID, 9)`，不作为候选。本包不执行这些命令。

继续追踪原厂 ELF 后，已确认正常窗口关闭可走 `delete,request → elm_exit → appcore terminate → UI_Camera_Quit → main 返回`，capture 库也注册了 liveview、sensor、shutter 和 GPIO 相关析构，不能把“terminate 回调没有直接硬件调用”解释为“退出没有硬件清理”。完整关机的 `UI_Manage_PowerOff` 另有 `mm_photo_end`、停止 liveview 和 camera ioctl 链；`UI_Manage_App_Relaunch` 则在同一进程内结束／重新初始化照片服务，没有启动新 ELF。因此这些入口仍未证明实机新进程可以再次初始化硬件。详细静态证据见 `outputs/deployment/mfw-exposure-test/lifecycle-audit.md`。

已核验的 `go.sh`、`hib2.sh` 单次启动，无重试循环；launchpad／ac_daemon 已追踪的死亡处理也没有补拉。其他事件订阅者仍未排除，不能承诺全系统绝无补拉；机内 MFW 库则已与审计样本核对一致。证据见同目录 `startup-supervision.md`。

待具备受控启动条件后，再从相机命令入口显式执行 `launch`。原厂 app 会 daemonize，启动父进程退出 0 不能证明成功，不能以 `$!` 作为最终 app PID。

## 独立原厂程序验证

`tools/run_stock.py --exposure-read-only` 新增独立只读选项，与 `--exposure-controls` 互斥。`tools/probe_exposure_readonly.py` 启动独立 mount／PID／IPC／网络命名空间与私有 Xvfb、偏好和运行目录；不使用主模拟器，不加载其他曝光／色彩控制模块。

最终运行退出码 0，报告为 `outputs/deployment/mfw-exposure-test/runtime/report.json`：

- 三项 GetUserData 读取成功；该私有会话为三分之一档、ISO 扩展开启、Auto ISO 上限 3200，IQ／AE 上限也读回 3200。这些数值来自私有偏好，不是实体机当前设置。
- 直接 operation 1 返回 `-120`，`applied=0`、`notified=0`；随后三项原厂值未变。
- 原生 remote 的能力为 `exposure_status=true`、`iso_controls=false`；GET 返回 200 和 `read_only=true`，POST 返回 403。
- 初轮验证器误用了外层 procfs，次轮有 Python HTTP 名称遮蔽，两次 worker 均退出 1；修复后通过，原失败报告仍保留在 runtime 目录。它们不是原厂程序崩溃。

这次没有执行原厂 app 退出／重新启动实验；传感器、真实曝光和硬件资源释放未验证。

随后使用 `tools/probe_exposure_launcher_vm.py` 在独立真实 ARM Linux VM 执行未改写的启动脚本与原厂 app，报告为 `outputs/deployment/mfw-exposure-test/launcher-vm/report.json`。脚本成功识别 daemonize 后的唯一 app，核对 exe／inode／starttime、RAM 模块和同 PID 的 ready／readonly 标记；原厂 app 存在时再次启动被拒绝，既有进程身份保持。直接写请求被只读模块拒绝，读取值未变。运行退出 0，报告 `passed=true`，私有 VM 已关闭。

该验证通过测试专用 execve 桥为原厂程序追加硬件替身库；没有替换启动脚本、原厂 app、身份或 ready 判断。它验证脚本监视与拒写流程，不验证传感器，也没有执行已有原厂 app 的退出或硬件重初始化。首次引导超时的日志保留在 `launcher-vm/boot-timeout-1/`；延长测试引导等待后完成上述检查，生产脚本未因此修改。

## 后续最小实机检查

1. 按 `/proc/PID/exe`、可执行文件 inode、starttime 确认唯一真实 app，检查 maps 中仅新增本只读库。
2. `/var/run/nx300m-exposure-ready` 与 `nx300m-exposure-readonly` 均应记录该 app PID，日志出现 `ready`。
3. 使用配套新版 remote 查询 `GET /api/v1/camera/info`：应有 `exposure_status=true`、`iso_controls=false`。`GET /api/v1/exposure/iso` 应含 `read_only=true`，数值须与机身核对；原厂对象尚未创建时应报告失败，不构造替代对象。
4. 原厂屏幕显示、正常测光和拍摄仍须实机确认。读取的 AE 数值不能证明传感器实际增益正确。

临时库及日志只存 RAM；正常断电后消失。脚本不提供回滚快照、不承诺程序重启或关开机一定恢复硬件。重新开机仍走原有 MFW 服务入口，本候选不自动加载。
