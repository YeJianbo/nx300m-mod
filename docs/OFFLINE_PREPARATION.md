# 首次接机的离线准备

**2026-10-24 更新：已有 MFW 的 `autoexec.sh` 入口已通过实体诊断，脚本以root执行并生成完成日志。** 结果见 [MFW_ENTRY.md](MFW_ENTRY.md)；未启动服务或加载功能模块，不重复刷固件。下文 USB 识别是当时的实际结果，原厂 1.15 的执行限制不再代表当前机内没有脚本入口。

当前准备工作只在电脑完成。原厂 NX300M 1.15 的免刷机临时程序执行入口尚未打通，九模块完整候选仍未完成实体相机安装、休眠恢复和救援验证。

## 已备材料

| 文件 | 用途 | 相机侧操作 |
| --- | --- | --- |
| `tools/inspect_camera_usb.ps1` | 枚举 Windows 当前 Samsung USB／WPD 设备与驱动记录 | 无 USB 协议请求；只在电脑写报告 |
| `tools/inspect_stock_usb_entry.py` | 从已核验原厂 TAR 复现 USB 命令路径的静态分析 | 无 |
| `outputs/deployment/mtp-host-entry-report.json` | 记录14处相关直接分支、命令缓冲区和剩余问题 | 无 |
| `tools/camera_readonly_preflight.sh` | 取得已核对 shell 后采集内核、挂载、进程及映射模块 | 只读取并输出；本轮未在相机执行 |
| `outputs/deployment/install-candidate/original/` | 保留两份被候选修改的原厂启动文件 | 仅保留恢复原文，尚无实体救援保证 |
| `outputs/deployment/sd-bundle/nx300m-mod-sd.tar.gz` | 后续独立服务和面板的资源 | 仅准备文件；复制到卡不等于可自动运行 |

## 接机时先做什么

相机正常开机、USB连接电脑后，在项目目录运行：

```powershell
powershell.exe -NoProfile -File .\tools\inspect_camera_usb.ps1
```

预期在电脑生成 `outputs/deployment/camera-usb.json`，包含设备名、硬件ID、驱动服务和INF。候选列表可能包含三星手机；NX名称缺失也不代表相机不存在，需要结合硬件ID和设备模式判断。此步骤不更换驱动、不启动服务、不修改相机。

当前原厂 `start_script` 是空实现；不要将SD资源包或内置候选当成可直接安装的免刷机包。USB／MTP可访问照片并不能执行这里的 shell 脚本。

取得经过核对的 shell 后，先运行 `camera_readonly_preflight.sh`，将标准输出保存到电脑。预期获得当前相机进程、真实挂载点和已加载模块信息。脚本不重启相机app、不设置 `LD_PRELOAD`、不启动remote、不读取照片内容，也不读取Wi-Fi口令。路径不存在、进程退出或权限不足会显示缺失项，不能据此断言机身功能损坏。

首次检查不发送 `0x1011` SelfTest、TMS或调整请求，不创建 `info.tgw`／`ptpconnect.txt`，不执行休眠或快照脚本。当前静态分析已经确认生产接口会进入解释器，并可能一直等待完成；它还不具备已验证的首次探测条件。

## 2026-10-24 首次 USB 只读识别

相机开机连接后，Windows 枚举到 `USB\VID_04E8&PID_1402`，总线描述为 `NX300`，驱动为 `USBSTOR / usbstor.inf`，状态正常。通用设备名是 `USB Mass Storage Device`；识别脚本已兼看总线描述，避免漏计 NX 设备。这个描述确认 NX 系列 USB 身份，不单独区分 NX300 与 NX300M。

Windows 磁盘关联确认 Samsung UMS Composite 的首个逻辑单元对应当次 `H:`，exFAT，总容量 64,055,410,688 字节。仅列出根目录名称，看到 `DCIM`、`MISC`、`SYSTEM` 和 `System Volume Information`；未读取照片内容、未修改 SD 文件、未发送生产或控制请求。第二逻辑单元报告容量 0，没有盘符。盘符和设备状态以每次重新枚举为准。

这次已确认大容量存储文件通道，尚未获得机身 shell 或可运行 Mod 的入口。未执行预检 shell、服务启动、模块加载或刷写。

## 电脑端复现分析

```powershell
python -X utf8 tools\inspect_stock_usb_entry.py
```

输出位于 `outputs/deployment/mtp-host-entry-report.json` 和对应的 `mtp-operation-dispatch.asm`、`mtp-command-validation.asm`、`mtp-command-receive.asm`。分析仅解释本项目原厂1.15文件的ARM地址，不推断其他固件版本或实际设备已进入生产模式。

离线结果能支持下一步选择连接与加载方式；实体相机能否稳定启动、拍摄、休眠和恢复，仍需后续逐项确认。完整安装候选及原厂恢复文件的范围见 [INSTALL.md](INSTALL.md)。
