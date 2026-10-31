# 隔离 ARM Linux VM

`tools/arm_vm.py` 使用工作区独立解包的 `qemu-system-arm`、官方 Debian armhf 内核和自建 initramfs。guest 由 ARM Linux 内核直接执行原厂 NX300M 1.15 BusyBox、shell 和加载器，不经过 QEMU-user、binfmt 或 `/proc` 身份转换。

这是通用 `virt` Linux 用户态验证环境，不模拟 DRIMe4 设备或原厂内核。基础镜像不含 camera app，不生成相机画面或服务 ready 标记。现有生产 SD 脚本和主模拟器不受修改。

## 基础使用

Windows 使用已有 Conda 环境，Linux worker 使用 WSL Debian 的系统 Python。所有下载、解包、initramfs 和运行记录位于 `work/arm-vm/`、`outputs/arm-vm/`，不执行全局 apt 安装或 binfmt 注册。

```powershell
python -X utf8 tools/arm_vm.py host
python -X utf8 tools/arm_vm.py prepare
python -X utf8 tools/arm_vm.py start
python -X utf8 tools/arm_vm.py status
python -X utf8 tools/arm_vm.py probe
python -X utf8 tools/arm_vm.py exec 'uname -a; readlink /proc/1/exe'
python -X utf8 tools/arm_vm.py stop
```

`start`返回后内核继续启动；待`outputs/arm-vm/serial.log`出现管理shell提示符再运行命令，基础镜像还会输出`ARM_VM_SHELL_READY`。完整原厂环境以实际串口命令返回确认就绪，同时检查内核没有initramfs解包失败。QEMU脱离启动它的短命WSL客户端，PID、完整参数及两个socket路径保存在`handle.json`。启动器拒绝覆盖正在运行的VM，停止通过现有QMP socket的`quit`完成。VM没有网卡，guest只有loopback；没有TCP/LAN暴露。串口和QMP为当前WSL用户可访问的Unix socket。

`start --memory-mib 1024`可为Xvfb包环境分配1GiB内存。默认512MiB仅用于基础镜像；加入本次79个包后，initramfs压缩85,802,439字节、展开227,499,588字节，512MiB启动时rootfs限额仅197820KiB，内核实际报`Initramfs unpacking failed: write error`。这会留下能进入shell但依赖缺失的不完整根目录，不能仅凭shell ready认定解包成功。该次SD检查退出1，原始记录保存在`sd-bundle/vm-probe/xvfb-unpack-failure-*`。

`exec` 的参数是一条 shell 命令字符串，返回 JSON `{exit_code, output}`，工具进程退出码等于 guest 命令退出码。仅允许一条串口命令在运行；超时只结束客户端，不会重启 VM 或假定 guest 命令已经停止。串口记录保存实际命令回显和输出。基础 `probe` 读回真实子进程 `/proc/PID/exe`，比较路径及 `stat -L -c '%d:%i'`，并检查原厂 shell 跨 exec；结果写入 `base-probe.json`。

Windows→WSL间的exec命令以base64传输，在Linux worker解码后交给guest shell，避免WSL传参提前展开`$name`等表达式。它保留原始shell语义，不修改guest脚本或进程身份。生产SD证据收集已实际使用含变量的循环通过该通道。

## 传入真实 ARM 服务

`prepare --overlay <目录>` 将指定目录内容放入 guest 根文件系统。该目录是显式选择的输入，不会向 VM 共享工作区或 Windows 文件系统。Windows 普通文件按 ELF magic 或 `#!` 识别可执行文件；其余文件为 0644。

```powershell
python -X utf8 tools/arm_vm.py prepare --overlay outputs/deployment/sd-bundle/vm-probe/overlay
```

overlay 中可放 `/arm-vm/post-boot.sh`：初始化 mount、loopback 和基础 probe 完成后，init 使用原厂 `/bin/sh` 执行该脚本，再提供命令 shell。后台服务需要在脚本中自行后台启动。Xvfb 集成使用 `DISPLAY=:97`，例如在该脚本中运行真实 ARM `/usr/bin/Xvfb :97 -screen 0 480x800x24 -nolisten tcp -ac`。

官方 ARM Debian 包可单独下载和直接叠加：

```powershell
python -X utf8 tools/arm_vm.py packages xvfb --dependencies
Copy-Item outputs/arm-vm/arm-package-sources.json outputs/deployment/sd-bundle/vm-probe/xvfb-package-sources.json
$vmPackages = Get-Content outputs/deployment/sd-bundle/vm-probe/xvfb-package-sources.json -Raw | ConvertFrom-Json
$vmArguments = @('-X', 'utf8', 'tools/arm_vm.py', 'prepare', '--overlay', 'outputs/deployment/sd-bundle/vm-probe/overlay')
foreach ($vmPackage in $vmPackages.PSObject.Properties.Value) {
    $vmArguments += @('--deb-overlay', ('work/arm-vm/downloads/' + ($vmPackage.Filename -split '/')[-1]))
}
python @vmArguments
```

`--deb-overlay` 可重复使用，支持 glob。上例按本次下载清单选择精确包名，避免混入缓存中的宿主包；先保存清单，因为`prepare`准备rescue BusyBox时会更新通用下载清单。已有VM运行时先`stop`，准备完成后再`start`。包通过 `dpkg-deb --fsys-tarfile` 直接解析，保留包内权限、软链和硬链，不依赖 Windows 对 NTFS 软链的解释。Debian 包先叠加，显式 overlay 后叠加；不要放入 QEMU-user wrapper 或替换 `/bin/sh`。merged-usr armhf 加载器自动补充 `/lib/ld-linux-armhf.so.3` 链接；原厂 softfloat `/lib/ld-linux.so.3` 保持独立。

基础 guest 的 `/bin` applet 链接指向真实原厂 BusyBox。需要较新 BusyBox 功能时，可显式调用官方静态 ARM `/rescue/busybox`，例如使用其 `wget` 请求 guest 本地 HTTP。父级工具应读取服务实际输出及生产 PID 文件；VM 本身不生成服务归属记录。

## 来源与输出

Debian 内核、Packages.xz 和软件包从 `https://deb.debian.org/debian/` 下载；包文件与官方 Packages 中 SHA256 比较。下载使用经过 `Content-Range` 校验的小块传输，避免当前网络全流响应停滞。原厂 loader、BusyBox 和依赖从已核验固件对应的 `work/stock-1.15/rootfs.tar` 提取；构建时重新核对原始固件 SHA256。

- `runtime-provenance.json`：内核来源/身份、原厂各文件来源及 overlay 路径。
- `host-package-sources.json`：独立 QEMU 和缺少的宿主依赖包元数据。
- `arm-package-sources.json`：最近一次官方 ARM 包下载的完整元数据。
- `serial.log`、`qemu.log`、`handle.json`：当前 VM 控制台、宿主错误和运行句柄。
- `base-probe.json`：基础原生跨 exec 与真实进程身份核对结果。

运行镜像位于 initramfs，guest 写入在停止后丢失。需要保存的业务检查结果应通过 `exec` 输出到调用方，或在停止前读取相应 guest 文件。通用内核下的成功不代表相机成像、DRIMe4 ioctl、原厂启动顺序或物理拔卡已经验证。

## 已读取的基础结果

2026-10-20，基础 VM 启动的真实内核为 `6.12.107+deb13-armmp`、`armv7l`。启动 probe 和串口 CLI probe 均通过：原厂 `/bin/sh` 成功直接执行 ARM 子程序；子进程 `/proc/PID/exe` 读回 `/bin/busybox`，真实二进制和进程 exe 的设备号/inode 均为 `2:21`。CLI probe 退出 0，`base-probe.json` 中 `arm_kernel_native_exec=true`。另以 `exec 'exit 7'` 读取到实际 guest 返回码 7 及输出。

基础VM随后通过QMP `quit`停止，实际确认原PID15461已不存在；基础串口与来源记录保存在`base-serial.log`与`base-runtime-provenance.json`。1GiB独立SD验证阶段的运行来源、串口及句柄已保存到`sd-bundle/vm-probe/verified-vm-*`；当前使用下述2GiB原厂app环境。

## 原样 SD 服务结果

`tools/probe_sd_vm.py prepare`准备生产SD包overlay；镜像加入保存清单中的79个ARM Debian包后，以`start --memory-mib 1024`启动，再执行`python tools/probe_sd_vm.py run`。原厂BusyBox直接exec真实ARM remote，启动与HTTP就绪、真实exe路径及inode、重复启动同PID、stop后记录移除与子进程回收全部通过。guest四个生产脚本与准备输入逐字节一致；17项LUT目录一致，camera hook ready为false。结果见`outputs/deployment/sd-bundle/vm-probe/probe-report.json`与`guest-results/`。

首次512MiB镜像解包失败的SD检查退出1。升至1GiB后生命周期实际退出0；收集器随后遇到WSL变量展开问题，修复传输后用`python tools/probe_sd_vm.py collect`重新读取同一次guest结果，最终报告`passed=true`。该命令只用于成功生命周期之后重新收集证据，不执行新一轮启停。本轮没有camera app、伪造ready、QEMU-user或实体相机。

## 原厂 app 自动启动结果

`tools/probe_app_vm.py prepare`生成专属overlay，包含既有GPIO/device/capture-boot硬件替代模块、生产`service-loader.so`和未改的四个SD脚本。`arm_vm.py prepare --stock-root --overlay outputs/deployment/app-vm/overlay`直接读取完整原厂TAR，保留动态Evas/Elementary资源、文件模式和链接；同时传入两个已保存包清单的并集：`sd-bundle/vm-probe/xvfb-package-sources.json`与`app-vm/xvfb-extra-package-sources.json`，共83包。构建后用`start --memory-mib 2048`启动，再执行`python tools/probe_app_vm.py run`。

完整原厂TAR普通文件508,177,299字节；最终initramfs压缩302,663,533字节、展开735,153,964字节，gzip完整读取通过，2GiB启动完整解包。首次完整环境的Xvfb因缺少现代EGL/GLES而加载原厂CoreGL并SIGSEGV；已补入`libegl1`、`libegl-mesa0`、`libgles2`和`libwayland-client0`。启动脚本将VM私有的旧`/etc/ld.so.cache`移至`/arm-vm/stock-ld.so.cache`，让两个加载器分别使用其默认目录，不向原厂shell或app传入hardfloat库路径。原始故障跟踪见`app-vm/xvfb-coregl-failure.json`，修复后Xvfb socket就绪且日志为空。原厂TAR未改。

2026-10-20实际检查退出0，用时68.557秒：未修改原厂app的SHA与准备输入一致，真实app PID325进入Ecore，生产加载器一次启动成功（owner=325、attempts=1、child=0、last_exit=0、exit_source=ecore-event），自动remote PID439。app与remote的真实exe、inode和进程starttime均核对通过；HTTP给出17项一致LUT。重复start保持PID439；stop后没有自动拉起，原样手动重启得到PID559，最终stop成功且app仍存活。证据为`outputs/deployment/app-vm/probe-report.json`与`runs/20261020T065132Z-d2006ce0/`。完整app日志保留在guest，宿主导出每流末64KiB和实际长度。

原厂BusyBox交互输入会截断过长命令。首次组合检查停在未闭合引号，112.209秒后宿主退出1，未进入app脚本。现在长命令通过256字符base64分段暂存，完整交给原厂`sh -c`，解码／写入失败返回125；该通道已用于上述成功检查。只有确认控制台停在未完成输入时才显式用`reset-console`发送中断和EOF恢复提示符，观察超时不会自动中断guest。镜像构建也改为临时文件写完后替换，避免取消压缩时截断上次可启动镜像。

本环境使用通用ARM内核和显式实验室硬件替代模块，不含照片回放或生产`lcd-capture.so`，没有伪造ready或进程身份；原厂`prefman`实际初始化偏好，私有sysmmap配置沿用已知单点修正。该结果验证app→加载器→SD服务调用链，未执行`rcS/hib2.sh`，未证明完整ISP/JPEG或原厂快照恢复。
