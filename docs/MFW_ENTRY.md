# 已安装 MFW 的脚本入口检查

2026-10-24：用户确认相机以前已安装支持 `autoexec.sh` 的 MFW；本轮 SD 诊断日志进一步确认实体相机执行了本包脚本。准确 MFW 版本尚未读取。此前文档中“未装 MFW”是早期信息，不再作为当前部署前提；不为取得脚本入口重复刷入新固件。

本地 MFW 1.1 参考库静态检查包含 `/mnt/mmc/autoexec.sh`。这支持先验证现有入口，不能证明当前机内文件就是该版本。旧 V0 注入器存在曝光代码覆盖问题，诊断包不调用它，也不沿用旧 SD 启动脚本。

## 包中内容

- SD 根目录 `autoexec.sh`：只调用下述诊断脚本，不启动服务或加载模块。
- `nx300m-diagnostics/preflight.sh`：复用项目只读预检，记录内核、挂载、进程、启动文件是否存在及相机已映射模块；不读取照片内容或 Wi-Fi 口令。
- 输出仅为 SD 的 `nx300m-diagnostics/preflight.log`、`complete.ok` 和临时 `running` 目录。没有内部文件写入、曝光设置、相机进程重启、联网或快照操作。

脚本返回成功标记只表示预检执行完成。预检输出中的缺失路径和未知映射需要另行解释，不代表所有 Mod 或相机功能正常。现有 MFW 及已经运行的其他程序可能有自己的行为，诊断包不会卸载它们。

## 准备与最小验证

1. 相机关机后取出 SD，在电脑备份原有根目录 `autoexec.sh` 和旧 Mod 文件。若已有启动脚本，不要将本脚本追加到它末尾；测试时根目录只放本包的诊断入口，原脚本备份留在电脑。
2. 将包内 `autoexec.sh` 和整个 `nx300m-diagnostics` 目录放到 SD 根目录。文件为 UTF-8 无 BOM、LF 换行；不要同时拷贝旧 `NXMOD` 的启动入口。不需要固件 `.bin` 或 `.slp`，不进入固件更新菜单。
3. 安全弹出 SD，放回相机，USB 断开后正常开机。观察原厂界面能否正常使用；本步骤需要用户实体操作，本项目首轮结果见文末。
4. 正常关机后用读卡器检查 `nx300m-diagnostics/preflight.log` 与 `complete.ok`。预期日志含 `preflight_exit=0`、`probe complete`；据此确认本次运行过脚本，再决定是否接入独立 remote 服务。

若没有日志，保留当前文件并核对位置、换行、已有 `DISABLED`/`complete.ok`/`running` 以及开机方式；不能仅凭没有日志断定固件损坏。若日志中断，先读取错误，不加载更多模块。`complete.ok` 使下次跳过；重试时在相机关机后从电脑删除此标记和遗留的空 `running` 目录。

停止后续诊断可在相机关机后移走根目录 `autoexec.sh`，或在诊断目录创建 `DISABLED` 文件；这仅阻止以后调用，不撤销已安装的 MFW，也不卸载已映射的旧模块。此包不是救砖程序。

## 电脑端构建

在项目根目录运行：

```powershell
python -X utf8 tools\build_mfw_diagnostic.py
```

输出为 `outputs/deployment/mfw-diagnostic/nx300m-mfw-diagnostic.zip` 和同目录 `sd/`。构建器只写项目输出目录，不寻找盘符、不复制到实体卡。

完整 LUT、曝光、LCD 和相册分享模块需要进入原厂 app 进程。`autoexec.sh` 能执行只是前提，子进程里的 `LD_PRELOAD` 不会改变已运行的相机 app；本包不做注入或启动文件持久修改。

## 2026-10-24 实机结果

已由 Windows 磁盘关联确认 Samsung UMS Composite 首逻辑单元对应 H:，再写入诊断入口。放入前根目录不存在 `autoexec.sh` 或诊断目录；两个写入文件均回读与准备文件一致。未写固件、相机内部文件或照片。

用户完成断开USB和重开机后，再次连接读取到 `preflight.log`（4884字节）和 `complete.ok`（44字节）。原始日志保存在项目 `outputs/deployment/mfw-diagnostic/camera/`：

- 日志头为本项目诊断 v1；执行身份 `uid=0(root) gid=0(root)`，结尾 `preflight_exit=0`、`probe complete`。本次证明已有 MFW 脚本入口能执行所放脚本。
- 内核 `Linux 3.5.0 armv7l`；根文件系统为只读UBIFS，SD为 `/dev/mmcblk0p1` 挂载到 `/mnt/mmc`，可写exFAT。
- 原厂相机进程 PID 265，执行文件 `/usr/bin/di-camera-app-nx300`。没有 `/usr/lib/nx300m-mod`、启动包装器或完整SD服务目录，已读maps中没有匹配项目九模块的路径。
- maps筛选不检查代码页原字节，不能排除旧 ptrace 注入器此前改写内存；没有在本次日志里进行曝光或成像验证。

这改变了执行入口状态，不改变完整功能与救砖的证据边界：尚未启动remote、联网、加载SO、重启app或处理快照。后续可先接入独立服务，不以本次预检成功为依据跳到九模块持久安装。SD 的完成标记保留，现有诊断入口下次自动跳过。
