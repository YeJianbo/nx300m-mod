# LUT 免刷机候选

面向已有 MFW、支持 `autoexec.sh` 的 NX300M 1.15。使用独立的 RAM 加载器，为正在运行的原厂相机接入 still 3D LUT 调用。不写 rootfs、内核、bootloader、分区或快照，不停止或重启原厂相机，不复用旧曝光注入器。没有自动启动项；手动加载后才允许选择风格。

## 实现

`lut_live_loader.arm` 核对原厂 IQ、UDD、libc 文件和进程身份，停驻并核对全部线程。仅借用已核验的原厂 `svc` 指令执行 `mmap2`、ARM cacheflush 和 `mprotect` 系统调用；不调用目标的 `dlopen`、malloc 或其他 C 函数。寄存器恢复后，只替换 LUT 专属 Update 虚表项和 `ipcs_3d_lut` GOT 两个可写数据槽。

`lut_ram_blob.bin` 是自包含 ARM 代码与状态，当前 52 KiB（含可选调用诊断）。代码页 RX、数据页 RW，无 libc、动态加载、TLS 或运行时重定位。每次原厂 LUT Update 使用独立线程上下文，快照本次选择和强度；已经在途的旧 SetParam 没有该上下文，继续透传。上下文不足时透传，不等待其他线程持有的锁。

静态原厂表分支使用 `table-stock3d.d4lut`，其他表源使用 `table.d4lut`。非零强度按实际原厂输入表混合；0% 直接把原始 source 指针传给原厂，不读目标表、不生成 identity 或混合副本。只失效 LUT 自身缓存，不修改 AE、ISO、快门或 Gamma 参数。校色尚未完成，已有 19 项风格不代表理光完整渲染或三星 ISP 标定。

## 手动入口

在相机正常取景、没有拍摄或传输任务的情况下，从已有机身 shell 执行。以下是候选入口，须先阅读本轮实际 VM 报告和未验证项。

```sh
/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh status
/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh apply
/bin/sh /mnt/mmc/nx300m-mod/lut-panel.sh
```

脚本先把加载器和 blob 复制、核对到独立 tmpfs 目录，再运行。默认 `status` 只检查目标；`apply` 才接入。LUT 数据根仍为 SD 上的 `nx300m-mod`，因此不能据此拔卡。加载器遇到未知模块、型号、版本、线程或映射状态时拒绝。

加载后 CLI 和 HTTP 共用 PID、启动时间、根目录、匿名 RX/RW 映射、blob header/config 及两个真实槽的核验。面板使用同一 CLI 就绪检查，不再要求存在 `lut_port.so` 文件映射。网页和 NXConnect 支持强度设置；选中并确认后才暂存，滑动不连续发请求。

```sh
export NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod
# 使用实际 RAM 副本中的 engine，或已有服务的 RAM runtime：
/path/to/ram/lut_engine.arm apply ricoh-negative-exact 50
/path/to/ram/lut_engine.arm stock
/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh restore
```

`stock` 清除风格选择，等待下一次原厂 still LUT 调用恢复表。`restore` 停止建立新上下文，待旧上下文排空后还原调用槽；仍保留 RAM 映射，避免在途调用访问已释放代码。若返回需要排空，应等待当前拍摄结束后重试。不要通过手工杀进程、删除事务文件或卸载映射绕过。

重复 `apply` 对已安装同版本映射幂等；`restore` 后再次 `apply` 复用已禁用、上下文为空且代码逐字一致的同一映射，不反复分配 RAM。不同 root、不同 blob 或未知 hook 会拒绝，不覆盖旧代码。

## 构建与检查

使用项目 Conda Python，依次构建最新 blob、loader、engine/backend 和面板，再打包：

```powershell
python -X utf8 tools/build_lut_ram_blob.py
python -X utf8 tools/build_lut_live_loader.py
python -X utf8 tools/build_lut_mod.py
```

输出 `outputs/deployment/lut-mod/nx300m-lut-mod.zip`，为现有连接服务的增量包。保留原服务文件清单，不包含根 autoexec，不会自动加载、重启服务或写卡。原有 SO 仍供受控模拟启动使用；正常 MFW 首次加载使用 RAM blob。

`--include-diagnostics` 另生成 `outputs/deployment/lut-mod-diagnostics/nx300m-lut-mod.zip`，包含相同模块及 5 项原生坐标探针，合计 24 项。默认包仍为 19 项；诊断不会写入主 LUT 源目录。调用日志开关、实机最小次序和 NX500 编码参考的适用边界见 [LUT_CALIBRATION.md](LUT_CALIBRATION.md)。

完整 ARM VM 检查工具为 `tools/probe_lut_live_loader_vm.py`，结果位于 `outputs/deployment/lut-live-loader/arm-vm/`。目标为明确命名的 fixture，执行真实原厂 IQ/UDD 与真实 ARM ptrace，MMIO 使用合成 RAM。最新 `report.json` 为 `passed:true`，guest/host 均退出 0：direct/composed 各 0/50/100 六组的 2190 字全部匹配，两种来源的 0% 三路原指针均保持；10 份调用记录的 Update/writer 同线程、顺序 `[0,1,2]`。已装重复 apply 不增加映射，restore 后同映射 reapply、原厂表输出及再次 restore 通过。同参数缓存重载、清选择恢复原厂及共享绑定核验通过，测试的双线程和启动时间保持不变，最终无 tracer。原始证据归档为同目录 `verification-evidence.zip`，该 VM 已关闭。

fixture 的还原通过改变原厂参数索引触发重载，没有伪造真实 app 的 processor/still 私有链。因此生产缓存链定位与失效仍须实机验证，不能将调用槽已还原写成实体成像已恢复。

此前独立原厂拍摄会话已用录制 Bayer、明确的软件 ISP、50% LUT 保存 5472×3648 JPEG，记录为 `outputs/firmware-runtime/capture-lut-mod/final/capture.json`。该次使用启动预加载，不能替代本 RAM 加载器的验证。

## 实体与恢复边界

仍需实机检查首次加载、正常取景/拍摄、选择与恢复、休眠恢复及持续运行。真实传感器颜色、硬件 ISP 像素结果与理光校色未完成；原厂 liveview 使用其他节点，此路径先覆盖 still，不能宣称取景滤镜已完成。

MFW 可能恢复休眠快照，普通关机、拔电池或移走 SD 不能被当作已验证的 RAM 清除方法。停用后续脚本不等于卸载已加载模块；恢复出厂设置也不会还原固件文件或保证清除旧快照。当前包不提供黑屏救砖保证，相关调查见 `RECOVERY_AND_DEPLOYMENT.md`。
