# LUT 免刷机候选

面向已有 MFW、支持 `autoexec.sh` 的 NX300M 1.15。使用独立的 RAM 加载器，为正在运行的原厂相机接入 still 3D LUT 调用。不写 rootfs、内核、bootloader、分区或快照，不停止或重启原厂相机，不复用旧曝光注入器。服务启动时不注入 LUT；首次切换风格或打开 LUT 面板时加载，也可以通过下面的命令手动加载。

## 实现

实时取景使用独立的 RAM 观察模块，接入位置、命令和 ARM VM 结果见 [LUT_LIVEVIEW.md](LUT_LIVEVIEW.md)。目前该模块透传原厂画面，尚未加入取景 LUT 像素处理。

`lut_live_loader.arm` 核对原厂 IQ、UDD、libc 文件和进程身份，停驻并核对全部线程。仅借用已核验的原厂 `svc` 指令执行 `mmap2`、ARM cacheflush 和 `mprotect` 系统调用；不调用目标的 `dlopen`、malloc 或其他 C 函数。寄存器恢复后，只替换 LUT 专属 Update 虚表项和 `ipcs_3d_lut` GOT 两个可写数据槽。

`lut_ram_blob.bin` 是自包含 ARM 代码与状态，当前 52 KiB（含可选调用诊断）。代码页 RX、数据页 RW，无 libc、动态加载、TLS 或运行时重定位。每次原厂 LUT Update 使用独立线程上下文，快照本次选择和强度；已经在途的旧 SetParam 没有该上下文，继续透传。上下文不足时透传，不等待其他线程持有的锁。

静态原厂表分支使用 `table-stock3d.d4lut`，其他表源使用 `table.d4lut`。非零强度按实际原厂输入表混合；0% 直接把原始 source 指针传给原厂，不读目标表、不生成 identity 或混合副本。只失效 LUT 自身缓存，不修改 AE、ISO、快门或 Gamma 参数。本地库现有 20 项风格，包含 Hard Mono。实机曾拍出纯黑白的 SAM_5291.JPG；理光颜色和完整 Lightroom 风格仍需校准。

## 手动入口

### 选择与保存

App、机身面板和 Custom 短按轮换共用选择后端。成功暂存后，将风格和强度保存到卡上的 `.state/lut-selection`；相同选择不重复写卡。下次激活 LUT 时，后端核对目录和颜色表，再恢复选择。恢复原样会保存原样状态。

写卡失败时，当前 RAM 选择仍可使用，界面提示“未保存到卡，仅本次有效”。切换提示框只显示反馈，不再次加载模块或改动正式面板的撤销备份。

App 0.20.30 在选择后短时自动读取写表状态，最长 12 秒；离开页面、断开连接或发起新操作就取消。未收到写表确认时显示等待状态，可半按快门后手动刷新。“已交给相机成像链”表示原厂写表回调已经接收本次选择，照片效果仍以实拍为准。

在相机正常取景、没有拍摄或传输任务的情况下，从已有机身 shell 执行。以下是候选入口，须先阅读本轮实际 VM 报告和未验证项。

```sh
/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh status
/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh apply
/bin/sh /mnt/mmc/nx300m-mod/lut-panel.sh
```

脚本先把加载器和 blob 复制、核对到独立 tmpfs 目录，再运行。默认 `status` 只检查目标；`apply` 才接入。LUT 数据根仍为 SD 上的 `nx300m-mod`，因此不能据此拔卡。加载器遇到未知模块、型号、版本、线程或映射状态时拒绝。

配置结构保持 196 字节。构建身份写入代码页的 blob header 预留字段，由源代码内容生成。加载后 CLI 和 HTTP 共用 PID、启动时间、根目录、匿名 RX/RW 映射、blob header/config 及两个真实槽的核验；状态 JSON 仅记录调用结果，删除或被拍照更新不会改变就绪判断。面板使用同一 CLI 就绪检查，不再要求存在 `lut_port.so` 文件映射。网页和 NXConnect 支持强度设置；选中并确认后才暂存，滑动不连续发请求。

原厂写表 GOT 有两种正常状态：首次调用前指向 IQ 的 ARM PLT0（偏移 `0x573a8`），解析后指向 UDD 的 `ipcs_3d_lut`（偏移 `0x955c8`）。加载器核验这两种状态；回调直接调用已核验的 UDD 函数。2026-10-31 的失败日志中，Update 仍为原厂函数，GOT 也仍为原厂 PLT0，旧判断却将其误报为残留 Hook。ARM 模拟现已覆盖首次写表前加载，避免先拍基线照片、完成懒绑定后才测试。

成片处理源码已从 SAM_5291.JPG 拍出黑白后紧接着的完整源码读取记录还原，按原构建参数重编译，与保存的 `7aa12b0c849114c7` 模块逐字节一致。当前成片模块沿用这份代码，整个模块仅 header 中 4 字节构建身份不同。复原源码、原始模块及比对结果位于 `outputs/deployment/lut-repair/working-1756/`。

```sh
export NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod
# 使用实际 RAM 副本中的 engine，或已有服务的 RAM runtime：
/path/to/ram/lut_engine.arm apply ricoh-negative-exact 50
/path/to/ram/lut_engine.arm stock
/bin/sh /mnt/mmc/nx300m-mod/lut-load.sh restore
```

`stock` 清除风格选择，等待下一次原厂 still LUT 调用恢复表。`restore` 停止建立新上下文，待旧上下文排空后还原调用槽；仍保留 RAM 映射，避免在途调用访问已释放代码。若返回需要排空，应等待当前拍摄结束后重试。不要通过手工杀进程、删除事务文件或卸载映射绕过。

重复 `apply` 对已安装同版本映射幂等；`restore` 后再次 `apply` 复用已禁用、上下文为空且代码逐字一致的同一映射，不反复分配 RAM。不同代码版本通过验证后的恢复流程升级。状态文件丢失时，加载器只在两个调用指针、原厂函数地址、数据根和保存的旧代码全部逐字匹配后恢复原厂指针；旧映射保留，等待在途调用返回。未匹配的 Hook 输出诊断并停止。

## 构建与检查

使用项目 Conda Python，依次构建最新 blob、loader、engine/backend 和面板，再打包：

```powershell
python -X utf8 tools/build_lut_ram_blob.py
python -X utf8 tools/build_lut_live_loader.py
python -X utf8 tools/build_lut_mod.py
```

输出 `outputs/deployment/lut-mod/nx300m-lut-mod.zip`，为现有连接服务的增量包。保留原服务文件清单，不包含根 autoexec，不会自动加载、重启服务或写卡。原有 SO 仍供受控模拟启动使用；正常 MFW 首次加载使用 RAM blob。

`--include-diagnostics` 另生成 `outputs/deployment/lut-mod-diagnostics/nx300m-lut-mod.zip`，包含相同模块及 5 项原生坐标探针，合计 25 项。默认包为 20 项，保留 Hard Mono；诊断不会写入主 LUT 源目录。调用日志开关、实机最小次序和 NX500 编码参考的适用边界见 [LUT_CALIBRATION.md](LUT_CALIBRATION.md)。

完整 ARM VM 检查工具为 `tools/probe_lut_live_loader_vm.py`，结果位于 `outputs/deployment/lut-live-loader/arm-vm/`。目标为明确命名的 fixture，执行真实原厂 IQ/UDD 与真实 ARM ptrace，MMIO 使用合成 RAM。最新 `report.json` 为 `passed:true`，guest/host 均退出 0：首次写表前从原厂 PLT0 状态加载、首次回调 LUT 输出及还原均通过；direct/composed 各 0/50/100 六组的 2190 字全部匹配，两种来源的 0% 三路原指针均保持；11 份调用记录的 Update/writer 同线程、顺序 `[0,1,2]`。已装重复 apply 不增加映射，restore 后同映射 reapply、原厂表输出及再次 restore 通过。同参数缓存重载、清选择恢复原厂及共享绑定核验通过，测试的双线程和启动时间保持不变，最终无 tracer。原始证据归档为同目录 `verification-evidence.zip`，该 VM 已关闭。

fixture 的还原通过改变原厂参数索引触发重载，没有伪造真实 app 的 processor/still 私有链。因此生产缓存链定位与失效仍须实机验证，不能将调用槽已还原写成实体成像已恢复。

此前独立原厂拍摄会话已用录制 Bayer、明确的软件 ISP、50% LUT 保存 5472×3648 JPEG，记录为 `outputs/firmware-runtime/capture-lut-mod/final/capture.json`。该次使用启动预加载，不能替代本 RAM 加载器的验证。

## 实体与恢复边界

仍需实机检查首次加载、正常取景/拍摄、选择与恢复、休眠恢复及持续运行。真实传感器颜色、硬件 ISP 像素结果与理光校色未完成；原厂 liveview 使用其他节点，此路径先覆盖 still，不能宣称取景滤镜已完成。

MFW 可能恢复休眠快照，普通关机、拔电池或移走 SD 不能被当作已验证的 RAM 清除方法。停用后续脚本不等于卸载已加载模块；恢复出厂设置也不会还原固件文件或保证清除旧快照。恢复路径与救援调查见 `RECOVERY_AND_DEPLOYMENT.md`。
