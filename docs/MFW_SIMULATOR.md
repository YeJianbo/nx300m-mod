# MFW 调试环境

查看器默认使用 NX300M 原厂 1.15 用户程序，叠加从这台相机导出的 MFW 脚本入口库。页面标题显示“NX300M MFW 调试”；`/status` 和 `/simulator/info` 返回实际运行配置。相机程序本身没有被换成其他机型。

```powershell
python -X utf8 tools/simulator.py --firmware mfw
```

原厂对照：停止当前会话和查看器后，以 `--firmware stock` 重新启动。已有其他配置会话运行时，不会自动替换。

## 使用的 MFW 文件

`outputs/mfw-runtime/libmisc.so` 来自 2026-10-24 的机身导出，与已审计的 MFW 1.1 脚本库一致。它以 NX300M 1.15 为基底；这里没有完整 MFW 固件镜像。

- MFW 库：8140 字节，SHA256 `188d1c695b06703d2cd52be3d81425dd8978a6934fc2ad8bb94d1d212cee9b59`。
- 原厂库：7340 字节，SHA256 `fe98d99729da83ae425cfb07dcbfc8ae27b0c99186a58a2d6c9c0503932d2f14`。

运行器只在独立挂载命名空间内只读覆盖 `/usr/lib/libmisc.so`。解压的原厂 rootfs 保留，退出该会话即取消覆盖。原有用户权限、网络和进程隔离保持不变。

## 脚本入口验证与调试

查看器采用 QEMU user mode。本机没有 ARM binfmt，MFW 创建的原生 ARM shell 子进程不能依赖这种模式执行；需要检查真正的 `start_script → autoexec.sh` 时，使用独立 ARM VM：

```powershell
python -X utf8 tools/probe_mfw_entry_vm.py
```

探针加载真实 MFW 库并调用其 `start_script`，由库自行创建线程、执行 `system()` 和 `/mnt/mmc/autoexec.sh`。脚本只向 RAM 写一行标记。原厂库作为对照：调用成功但不执行脚本。只有读到完整标记且原厂对照不产生标记，检查才通过。

结果保存在 `outputs/mfw-runtime/entry-vm/result.json`，串口原始输出在同目录 `serial.log`。测试结束自动关闭这台独立 VM。

2026-10-31 已验证：原厂对照不执行脚本，MFW 入口执行脚本并写出完整标记，探针退出码为 0。查看器运行中的库映射及哈希也已核对，命名空间外的原厂库保持原哈希。

保留交互式 VM：

```powershell
python -X utf8 tools/probe_mfw_entry_vm.py start
python -X utf8 tools/probe_mfw_entry_vm.py exec 'uname -a; /arm-vm/mfw-entry-probe.arm /usr/lib/libmisc.so 1'
python -X utf8 tools/probe_mfw_entry_vm.py stop
```

`start` 返回后需等待串口日志出现 `MFW_ENTRY_VM_READY` 再执行命令。脚本、库和探针从当前文件组装，不从旧服务 ZIP 提取。

## 实时 LUT 预览调试范围

这套环境可检查 MFW 库加载、脚本执行、用户态调用和 MOD 逻辑。ARM VM 使用 Debian 内核及 QEMU virt 硬件；查看器使用替代硬件接口。相机的休眠快照、真实 ISP 取景管线和实时像素效果仍需另外验证。现有成片 LUT 的 loader/blob、196 字节配置 ABI 和颜色编码没有因切换模拟配置而改变。

本工具不连接实体卡、不写 NAND、不刷机，也不启用 ISP 配置寄存器实验。
