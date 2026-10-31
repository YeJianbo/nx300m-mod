# 原厂 app 加载候选

当前候选包含九个 rootfs 原生模块：六个核心 hook、`service-loader.so`、`gallery-share.so` 和 `camera_firmware_mod.so`。原厂 `hib2.sh` 的 app 调用与 camera desktop 的 `Exec` 共用 `/usr/lib/nx300m-mod/preload.sh`，后者通过新增 `/usr/bin/nx300m-camera-launch` 启动原 app。服务加载模块在真实 camera 进程的 Ecore 主循环中检查 SD 资源，调用 `/mnt/mmc/nx300m-mod/start.sh` 进入现有服务启动流程。原厂更新后快照与直接恢复的静态证据、早期六／七模块验证保留在下文；当前候选的模块数与字节以 `persistent-candidate/report.json` 为准。没有刷写实体相机或执行快照生成。

## 2026-10-23 部署整合

`tools/deployment_boot_chain.py --persistent-only` 生成 `outputs/deployment/app-loader/boot-chain/persistent-candidate/`，其中 `rootfs/` 是安装候选，`original/` 保存两份被修改启动文件的原厂原文。当前默认 `stock` 不增加项目 preload，支持 services／controls／color／full 分组；只检查选中组，缺项时保持原环境执行原命令。SD 资源路径固定为 `/mnt/mmc/nx300m-mod`。配置、DISABLED 标记、下次 exec 与旧快照的区别见 [LAUNCH_PROFILES.md](LAUNCH_PROFILES.md)。下文七／九模块全载及旧 VM 记录是此前验证，本轮策略结果以更新后的分派和服务定时器报告为准。

SD 服务必须包含 `start.sh`、`stop.sh`、`common.sh`、`service.sh`、`bin/nx-remote-controller.arm`、`bin/nx-quickshare-armv7` 和 `web_root/index.html`。仅放面板不能启动服务。remote 由 `service-loader → start.sh → service.sh` 唯一管理；采用这条自动启动链时，不再额外直接派生另一个 remote。

此前九模块通过原厂 ARM loader／Ecore 隔离共载，实际映射掩码 `511`，子进程主循环和析构退出为 `0`，见 `co-load/lazy-report.json`。当前分组与停用策略的分派结果见 `dispatch-probe/report.json`；只执行候选的 app 分派片段，没有运行完整 `hib2.sh`。

`tools/probe_installed_candidate.py` 将完整 rootfs 候选、完整 SD 包和明确标注的实验室硬件 shim 送入独立 ARM VM，使用真实 `/usr/bin/nx300m-camera-launch` 启动未修改的原厂 app。它复用既有服务生命周期验证，另外检查九项实际进程映射、新固件桥 owner 和每阶段只有一个 remote，并对本次生成的 JPEG 执行两次上传、首次备份保持和字节级还原。`prepare` 只生成包；`install` 尝试向空闲 VM 串口传输，当前大包曾触发发送超时；`boot` 显式重启这台一次性 VM、预置候选 CPIO，原 initramfs 保存在 `work/arm-vm/initramfs-before-installed-candidate.cpio.gz`；`run` 执行验证。结果位于 `outputs/deployment/app-loader/installed-vm/`，是否通过以其中 `probe-report.json` 为准。该工具不修改主模拟器或实体相机。

`tools/build_install_candidate.py` 将生产用 rootfs 增量、原厂恢复文件、配套完整 SD 包和当前部署说明汇总到 `outputs/deployment/install-candidate/` 及 `outputs/deployment/nx300m-mod-install-candidate.tar.gz`。这是开发安装候选，不包含 VM 硬件 shim，也不生成或安装新的 SLP。

最新独立 ARM VM 集成通过：同一未修改原厂 app PID 330 映射最终九模块，firmware bridge marker 属于该 app；service-loader 固定 root 为 `/mnt/mmc/nx300m-mod`，只尝试一次、child 已回收、退出码 0。自动启动及重复 start 使用同一 remote；手动 stop 后没有自动重启，后续 start／stop 正常，身份检查时始终只有一个 remote。

上传验证保留了一个真实失败：BusyBox wget 的 `--post-file` 将包含 NUL 的 634 字节 JPEG 截为 4 字节，原 run 停在上传比较。随后保持同一 app，换入最终 remote，使用 `nc` 加准确 Content-Length 继续未完成部分；4 字节截断体得到 HTTP 400，两个完整 JPEG 逐字节写入、首次备份保持不变，恢复后与原 JPEG 字节相同。`installed-vm/probe-report.json` 明确列出初始证据 run 与独立 continuation run，旧失败未改写。媒体目录及 fsync 证据范围为 ARM VM rootfs，未覆盖实体 SD 或物理休眠恢复。

### 停用、卸载与恢复

临时停止服务使用 SD 包的 `stop.sh`；它会记录本次开机的手动停止状态，挂载变化不会立即重启服务。重新运行 `start.sh` 才清除此状态。

完整恢复时，先停止服务并结束使用候选 SO 的相机进程，再将 `original/usr/bin/hib2.sh`、`original/opt/share/applications/com.samsung.di-camera-app-nx300.desktop` 恢复到对应 rootfs 路径，保留 `report.json` 中的原厂模式和属主。随后移除本项目新增的 `/usr/bin/nx300m-camera-launch` 与 `/usr/lib/nx300m-mod/`；SD 上只移除 `nx300m-mod` 资源目录，照片所在的 `DCIM` 不属于卸载范围。模拟器在下次新会话使用恢复后的 rootfs；这段说明没有执行这些操作。

休眠镜像仍保存旧进程映射。安装、更新或卸载内置 SO 后，旧快照都不能作为新配置已生效的证据；应通过对应机型已确认的快照重建流程处理，不能在仍恢复旧映射时删除其 backing 文件。本轮没有执行 `hib2.sh`、擦除快照或模拟物理休眠恢复。

## 已确认的入口与边界

| 路径 | 原厂证据 | 当前结论 |
|---|---|---|
| `/sbin/init` | rootfs TAR 中链接到 `../bin/busybox` | TAR 无 `/etc/inittab`；历史 `/etc/~inittab` 引用的 `/etc/rc.d/rc.sysinit` 也不在 TAR 中。不能据此历史文件推定当前启动顺序。 |
| `/etc/init.d/rcS` | 先挂载原厂数据区；发现 `/mnt/ubi1/fw/snapshot` 时设置环境并调用 `hib2.sh` | 已确认快照创建分支。普通分支在末尾只设置 `MMF_DEBUG`，没有启动 app 的命令。 |
| `/usr/bin/hib2.sh` | `HIB=a`；最后用 `which`、`readlink` 解析 `di-camera-app` 后执行 `$app &` | 静态确认的 app 启动点，用于创建快启快照。它还包含 mtd9 擦除、swap 和 remount 操作，不是验证命令。 |
| `/usr/bin/go.sh` | `startx`、`sleep 1`、`di-camera-app`、`sleep 1`、`vconftool ...` | app 命令明确，实际调用方未知。不能声称修改它就能覆盖正常开机。 |
| camera desktop | 原厂 `Exec=/usr/bin/di-camera-app`，没有参数或字段占位符 | 当前候选仅改为 wrapper；wrapper 将收到的参数原样传给原 app。桌面入口已覆盖，开机自动请求仍未确认。 |
| `/usr/bin/di-camera-app` | 链接到 `di-camera-app-nx300` | 两个候选均保留原来的命令解析方式。 |
| SD 挂载 | `/etc/fstab` 中两个 `/mnt/mmc` 条目均为 `noauto` | 未确认 app 启动前 SD 已挂载；候选不自行挂载、不等待。 |
| `/etc/rc.d/rc.leavesleep` | 只设置 boot-animation 的 vconf 项 | 该文件没有 app 的 exec，也未证明它属于此机型正常快启路径。 |

另对固件内核做了只读提取：`vImage` 的内嵌 initramfs `/init` 执行 `check_card`、`fw_upgrade nx300m.bin`，最终 `exec sh`。这是升级环境脚本，未提供普通 app 启动入口。提取链为固件偏移 `144` → 镜像内 gzip 偏移 `21952` → 解压内核内 gzip 偏移 `4583060` → newc `/init`。`uImage` 本次未发现内嵌 gzip/newc；编译入的命令行字符串只有 `console=ttyAMA0,115200n8 mem=256M@0xC0000000`，实际 bootloader 传入的启动参数仍未知。

## 正常启动链的定向追踪

后续只读追踪保存在 `outputs/deployment/app-loader/boot-chain/report.json`，源码和原厂脚本副本在同目录的 `gpl/`、`rootfs/`。工具为 `tools/deployment_boot_chain.py`。GPL 包仅按启动相关路径读取并保存命中文件，另外精确补取 `params.c`、`config.h`、两份 bootloader Makefile；没有全包展开，也没有重新读取升级用 `vImage`。正常内核复用已有的 `outputs/quickshare/stock-1.15-kernel-50ac20.bin`。

### BusyBox、shell 与 X 的后继路径

原厂 BusyBox 字符串标识为 `v1.20.2 (2014-05-15 21:35:21 KST)`。GPL 的 `busybox/init/init.c:648–675` 明确规定：配置禁用 inittab 或 `/etc/inittab` 不存在时，注册 `/etc/init.d/rcS` 为 `SYSINIT`，随后注册 askfirst login shell；`1156–1174` 执行这些动作。askfirst 在 `483–503` 等待控制台输入，不等于自动运行相机 app，也不等于先运行 `login`。这条默认路径不遍历 SysV runlevel 的 `S*` 链接。

`/etc/init.d/rcS` 的注释虽写着执行 `S??*`，实际正文没有对应遍历循环；其中直接调用的系统服务和 snapshot 分支才有脚本证据。因此，不能仅凭存在 `/etc/rc3.d/S02rc.local` 链接，就认为默认 init 已执行 `rc.local`。原厂 `/etc/rc.local` 自身也只有 `exit 0` 模板。

已检查原厂 `/etc/profile`、`/etc/bash.bashrc`、root 的 `.profile`/`.bashrc`、`/usr/bin/startx` 和实际后继 `/usr/etc/X11/xinitrc`。这些文件没有执行 `go.sh` 或 app。`startx` 最终启动 X 和 `enlightenment_start`；原厂 root 的 E17 startup `.order` 为空。GPL `ash.c:13176–13195` 显示 login shell 读取 `/etc/profile` 与 `.profile`，交互 shell 还可读取 `ENV` 指定文件；当前 TAR 没有 `/.profile`、`/.ashrc` 或 root `.ashrc`。未获得实际启动环境中的 `ENV` 值，不能据此扩展为完整的动态调用链。

本轮沿 `xinitrc` 的 launcher 后继精确补查：当 `/tmp/alaunch` 不存在时，它调用 `/etc/rc.d/init.d/launchpad_run`。TAR 中 `/etc/rc.d/init.d` 实际链接到 `/etc/init.d`，目标脚本存在，后台启动 `launchpad_preloading_preinitializing_daemon` 与 `ac_daemon`。原厂 `opt/share/applications/com.samsung.di-camera-app-nx300.desktop` 则明确写着 `Exec=/usr/bin/di-camera-app`，绕过 `go.sh`。因此，即使后续证实普通开机由此 launcher 请求 camera，也不能用修改 `go.sh` 证明覆盖了该入口。

这个桌面注册条目只确定了被请求启动时的目标，没有证明开机时谁发出 camera 启动请求。原厂 `/opt/share/preload_list.txt` 仅列出 appcore-efl、appcore-common 和 ISF IMF 模块，不含 camera；复用的实际正常 kernel 中也没有 `go.sh` 或 `di-camera-app` 直接命令名。这些结果没有补成自动启动链。当前最具体的缺失已收窄到：**非更新冷启动中，谁向 launcher 发出 camera 请求，或谁直接执行 camera**。

这些精确文件、链接关系与后述 ARM 调用顺序保存在 `boot-chain/cold-entry/report.json`，分析工具仅运行以下定向分支，不重做旧范围扫描或重新生成候选：

```powershell
python -X utf8 tools\deployment_boot_chain.py --startup-gap
```

GPL 机型目录中 `/etc/profile`、`rcS`、`go.sh`、`hib2.sh` 与 1.15 TAR 对应文件均逐字节一致。GPL `image/env.sh` 选择 `slp-nx300m` 并列出相机 app 包，但未给出新的正常 app 调用命令。另一个 `imagedev/etc/inittab` 是 SysV 配置，存在 runlevel/getty 项；它不在当前原厂 TAR 的 `/etc/inittab` 位置，不能替代当前机身启动证据。

### Bootloader 与恢复机制

GPL `bootloader/Makefile.pnlbl:54–66` 只有 DEMO/DEV 构建分支定义 `CONFIG_FASTBOOT_TEST`。`Main.c:1410–1434` 的普通分支选择 `BOOTLOADER_SNAPSHOT_BOOT`，`1114–1210` 读取快照头：签名不符、显式 cold mode 或读取失败转到装载普通 kernel；有效快照继续恢复。恢复分支在 `1320` 调用 `jump_to_resume(sw_hdr->resume)`，冷启动分支在 `1337` 调用 `set_param_linux(bootmode)`。源码机制说明：成功快照恢复不重新经过 BusyBox → shell → app exec，因此原有候选函数不会在每次恢复时检查 SD。

`params.c:338–349` 为 `COLD_BOOT_NORESUME` 添加 `noresume`，其他 cold mode 使用不带该项的参数；两者都指定 UBIFS root、`ubi.mtd=4`、`console=ttyAMA0,115200n8`、`mem=512M`，未指定 `init=`。GPL 机型 defconfig 启用 `CONFIG_HIBERNATION`、`CONFIG_SCORE_FAST_BOOT`、`CONFIG_BOOTLOADER_SNAPSHOT`，默认恢复分区是 `/dev/mtdblock9`；`hibernate.c:894` 起还保留 kernel 侧 `software_resume`，会受 `noresume` 控制。

对实际 1.15 固件读取的 `D4_PNLBL.bin` 分区位于固件偏移 `5201412`，大小 `63580` 字节：分区偏移 `0xe7e6`、`0xe841` 分别含 cold/snapshot 诊断文本，`0xeab8`、`0xeb73` 分别含不带／带 `noresume` 的 rootfs 参数前缀。后续已对直接恢复分支完成下述字节级核对。复用的实际正常 kernel 含 `/sbin/init`、`/dev/mtdblock9` 和恢复失败诊断。未获取实机 ATAG、`/proc/cmdline` 或启动日志；源码 defconfig 仍不能当作 1.15 kernel 的完整实际配置。

### 当前可连接的边界

普通 app exec 的请求发起者和 exec 前的 SD 挂载完成点尚未确认。原厂 `fstab` 为 SD 标记 `noauto`，`rcS` 的 `mount -a` 不会因此挂载 SD；检查的 mdev 配置和 udev 规则也未提供挂载完成到 app exec 的脚本连接。当前内置 SO 候选覆盖已确认的更新后快照 app 调用和 camera desktop 注册入口；`go.sh` 保持原厂内容，其调用方仍未知。

冷启动缺少正常 app 的父进程／exec 记录，以及该次启动的 bootloader 参数；快启缺少实际镜像生成与恢复日志、恢复后进程和 SD 映射生命周期证据。后续最小证据应是一次已授权开机期间的启动日志、`/proc/cmdline`、app 父进程和 SD mount 时间点。仅继续改 profile、rc.local 或快照创建脚本，无法从当前证据推出自动加载与缺卡回退已经覆盖。

## 更新后快照与内置 SO 候选

### 原厂更新的确切连接

原厂 `fw_upgrade` 的静态分区操作表从 ELF 地址 `0x156b0` 开始，共七项。`snapshot_img` 项位于 `0x15728`，指定 mtd9、无固件输入 image（索引为 -1），启用操作且不写 image。实际循环在 `0xab0c → 0xab40 → 0xb02c–0xb038` 将 9 传给 `flash_erase /dev/mtd%d 0 0`，即清除旧 snapshot；不是仅创建请求文件却继续保留旧快照。`main` 在 `0xb454` 调用更新函数，`0xb458–0xb45c` 要求返回 0，才在 `0xb460` 调用请求函数 `0xa6a4`。

请求函数依次创建 `/mnt/ubi1/fw/snapshot`（`0xa744`）、执行 `echo 0 > /mnt/ubi1/fw/snapshot; sync; sync;`（`0xa764`），卸载/分离数据区后 `reboot -f`（`0xa7b4`）。旧快照已擦除，因此这次开机需要走冷启动；原厂 `rcS` 挂载 ubi1 后发现请求。其计数为 10 时会先删除请求；初始值 0 则在后面的 snapshot 分支递增到 1、设置环境并调用 `hib2.sh`。失败重试与计数删除是原厂行为，没有修改。

`hib2.sh` 设置 `HIB=a`，准备 swap/快照分区，按原顺序将 rootfs remount 为只读、运行 `startx` 和 `hib_exjob -r`，然后 `which di-camera-app`、`readlink`，最终 `$app &`，不附加 app 参数。动态加载器会在进入 app 的 `main` 前处理此处的 `LD_PRELOAD`。

实际 app 的 `main@0x1717d8` 在无参数分支设置全局 `0x4dbb60=1`；注册的 create callback `0x171604` 在 `0x17166c` 调用 `UI_Camera_Init`。该全局使其进入 `0x154528`，申请并向 `hib_register_exmem` 注册 60 MiB 区域；返回值非负才在 `0x154600` 调用 `hib()`，失败则跳过。另有一个 app 分支在 `0x153f60` 调用 `hib()`，未将这些分支混成无条件休眠。

`libhibernation-slpcam-0.so:hib@0x15d8` 在 `0x1618–0x1624` 检查 `getenv("HIB")`：只判断是否为 NULL，不要求值恰为 `a`。变量存在时，依次设置 `/sys/power/disk` 为 `reboot`、执行 `umount /mnt/mmc 2>/dev/null`（返回值没有用于取消休眠）、打开 `/sys/power/state`、卸载并分离 ubi1，然后在 `0x17bc` 写入 `disk` 四字节。打开或写入失败有原厂错误分支；不能把到达 app 入口写成快照必然完成。

正常恢复会返回原来的休眠调用点，库在 `0x188c` 运行 `rtc_s`，恢复原厂 pref 挂载/数据，然后 app 继续执行，没有重新 exec。app 仅当 common-menu DB 项 `0xfa` 非零、`hib_has_extradata()` 和 `hib_has_hibdata()` 均成立时调用 `fw_clear_snapshot_request`。该函数原厂实现会删除请求，创建 `/mnt/ubi1/fw/upgrade_done` 并 sync，设置 `PCACHE=REFRESH`。另有 `hib_post()` 在后续 app 分支关闭 swap。

这些实际 ELF 的定向反汇编、常量重建和分区表均位于 `boot-chain/snapshot/`。分析没有执行其中任何升级或休眠命令。

### 快照调用点与预载映射的条件

新增原厂 ELF 调用顺序确认：app `main` 在 `0x1717ec–0x1717f4` 调用 `daemon(1,1)`；原厂 libc 的 `daemon@0xc12f0` 在 `0xc1300` fork，父进程在 `0xc13e0` 退出，子进程 setsid 后继续，没有 exec 替换。它会继承此前 loader 建立的映射，不能把这次 daemon fork 当作 preload 丢失点。

app 随后在 `0x171834–0x17183c` 将 create callback `0x171604` 写入 ops 的 offset 4，`0x171924` 调用 `appcore_efl_main`。原厂 `libappcore-efl.so.1.1` 在 `0x1a64` 读取 ops+4，`0x1a74: blx r3` 同步调用 create；该回调经 `0x17166c → UI_Camera_Init → 0x153dc0 → 0x154528`，成功注册 exmem 后到达 `0x154600: hib@plt`。create 返回后，appcore 到 `0x1b18` 才调用 `elm_run`；原厂 `libelementary.so.1.0.99:elm_run@0xa442c` 直接跳转到 `ecore_main_loop_begin@plt`。这些位置的实际反汇编在 `boot-chain/cold-entry/*.asm`。

由此可以连接必要条件：候选 preload 若成功进入此次 app，SO 在 main 前已映射，daemon 子进程继承它们；休眠调用位于 appcore 外层主循环之前。共载探针的 loop ready 和服务 timer 状态因此不能当作快照创建时已保存同样状态的证据。这里确定的是外层调用顺序，没有据此排除所有未追踪库调用中的嵌套循环。

尚未读取实际新快照，因而未确认其 VMA、已保存代码页、定时器状态或恢复后缺页行为。旧快照也不会因 rootfs 脚本改变而自动增加 preload 映射；需要实际执行已确认的旧快照清除与重建路径。原厂 SLP 接受结果只说明已测试输入通过解析，不等于升级和快照创建已经发生。内置 SO 的稳定 backing 条件仍按下节处理。

### 实际 bootloader 的直接恢复跳转

`D4_PNLBL-direct-resume.asm` 使用分区相对偏移，运行地址为 `0x70200000 + 偏移`。`0x97dc–0x97f0` 的签名比较成功才进入 snapshot 分支；冷启动和失败路径跳向 `0x97f4`。恢复前的 `0x99d4–0x99f0` 从快照头 `0xd0000000 + 0xfd8` 的四个字节组合出 resume 地址，与原厂 `swsusp_header.resume` 布局一致。

`0x99f4` 调用 `0xa2dc`，后者在 `0xa2e4` 调用 `0x109c`。该入口先 `mov sp,r0` 保留 resume 地址，完成缓存/MMU准备后在 `0x10d0` 执行 `mov r3,sp`、`0x10d4` 执行 `mov pc,r3`。外层还保留 `0xa340: mov pc,r4`。因此“有效快照直接恢复既有执行上下文”已有原厂二进制控制流依据；不是通过启动脚本重新设置进程环境。

### 文件映射为何需要持久 backing

厂商 Linux 3.5 源码中 `snapshot.c:953–976` 保存当时仍驻留、非 free/forbidden 等范围的页，并没有统一排除所有干净文件页。因此，已保存的 SO 页可能在恢复后立即可用；不能反过来说所有只读 SO 都不进入快照。

但 `hibernate.c:435–473` 会在拍快照前回收内存，`vmscan.c:2879` 允许 unmap；`vmscan.c:757` 起能移除文件映射页。未触及的代码页也可能从未驻留。`filemap.c:1603` 起的缺页处理沿原 `vm_file->f_mapping` 查页缓存，缺页或非 uptodate 页仍读取 backing，失败可能产生 `VM_FAULT_SIGBUS`。恢复后的正常内存回收同样不能保证所有 SO 页永远驻留。

厂商扩展 `extended_snapshot.c:799` 起的 `snapshot_readahead` 使用记录的文件名重新 `filp_open(..., O_RDONLY)`、`do_readahead`；它记录路径与页范围，不会把 SD 文件变成独立的持久文件替代物。一次 preload、一次快照或一次预读，都不能据此保证恢复及后续运行不再需要 SD backing。所需条件是原映射对应的 backing 可访问，且内容和文件系统状态与保存上下文一致；同名路径下换一个文件并不能建立该条件。

原厂休眠库还会主动卸载 SD。新的候选因此将可执行 SO 放在 `/usr/lib/nx300m-mod/`，固定 `NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod` 用于资源与服务启动入口。内置 backing 应在该快照的整个使用期间保持不变；修改 SO 或 rootfs 后必须重新生成与之匹配的快照。这个设计消除了 hook 代码对 SD 文件映射的依赖，未声称已经验证所有资源在拔卡时的行为或七个模块在机身上共同工作。

### 当前候选产物

共享源片段 `mod/deployment/app-loader/preload-persistent.sh` 安装为 `/usr/lib/nx300m-mod/preload.sh`。它只检查内置七项 SO，全项为可读、非空普通文件才为原 app 命令设置固定 root 和 preload，并保留已有 preload。缺少任一项时直接 `exec` 原命令与原环境。它不以 SD 是否存在决定装载代码。

camera desktop 只将原 `Exec=/usr/bin/di-camera-app` 改为 `Exec=/usr/bin/nx300m-camera-launch`；原字段没有参数或 `%` 占位符，其余字节不变。新增 wrapper 检查并 source 共享 helper，然后将 `"$@"` 原样传给 `/usr/bin/di-camera-app`；helper 缺失时直接执行原 app。原 app 二进制及 `di-camera-app` symlink 均未修改。

`hib2.sh` 仅将末尾唯一 `$app &` 替换为 helper 存在检查、source 和 `nx300m_launch_persistent_app $app &`；缺 helper 时保留 `$app &`。原 `which/readlink` 解析、未加引号的 `$app` 展开方式及后台 `&` 都保留，其他原厂字节可还原为原件。共享函数自身直接 `exec`；桌面 wrapper 不额外保留等待 shell，hib2 则由原后台调用隔离子进程环境。

第七项 `service-loader.so` 仅在真实 camera 可执行文件的 Ecore 主循环入口注册定时器。SD 根目录及 `start.sh` 出现时，它通过 `/bin/sh` 执行该脚本，交给已有 `service.sh` 管理服务；子进程不继承相机的 preload。资源缺失时等待，启动失败按有限退避重试；同一资源身份成功启动后不反复拉起，观察到移除或资源身份变化后重新判断。它不自行挂载 SD，也不把其他工具进程变成服务启动入口。生产 guard 和定时器行为的专属证据位于 `outputs/deployment/service-loader/`，与下节共载探针的非 camera 范围分开记录。

构建命令：

```powershell
python -X utf8 tools\deployment_boot_chain.py --persistent-only
```

`outputs/deployment/app-loader/boot-chain/persistent-candidate/` 包含：

- `rootfs/usr/bin/hib2.sh`：从原厂文件派生，仅替换最后的 app 分派片段，其他原厂字节保留。
- `rootfs/opt/share/applications/com.samsung.di-camera-app-nx300.desktop`：仅替换 `Exec` 的命令路径。
- `rootfs/usr/bin/nx300m-camera-launch`、`rootfs/usr/lib/nx300m-mod/preload.sh`：桌面 wrapper 与两入口共享 helper。
- `rootfs/usr/lib/nx300m-mod/`：六个核心 hook 加服务加载模块，共七个原生 ARM `ET_DYN` SO，没有模拟硬件模块。
- 两份 `.patch` 与 `report.json`：差异、11 个 rootfs 文件的来源/模式/属主元数据及实际语法检查。

相对原厂的四项启动文件如下；七个冻结 SO 的路径与来源另逐项列在 `report.json:rootfs_changes` 中，字节与已有共载通过版本一致。

| rootfs 路径 | 变化与来源 | 模式 / uid:gid / mtime |
|---|---|---|
| `/usr/bin/hib2.sh` | 修改；原厂 TAR + `hib2-app-call.sh`，2517 字节 | 保留 `0555 / 0:0 / 1403327571` |
| `/opt/share/applications/com.samsung.di-camera-app-nx300.desktop` | 修改；原厂 TAR 的唯一 Exec 字段，122 字节 | 保留 `0444 / 0:0 / 1403327570` |
| `/usr/bin/nx300m-camera-launch` | 新增；同名模板，282 字节 | `0755 / 0:0 / 1403327571` |
| `/usr/lib/nx300m-mod/preload.sh` | 新增；`preload-persistent.sh`，714 字节 | `0644 / 0:0 / 1403327571` |

候选 helper、wrapper 和 `hib2.sh` 均通过 host 与原厂 BusyBox 的 `sh -n`，退出码为 0。实际运行 `tools/probe_persistent_dispatch.py` 也退出 0：原厂 ARM BusyBox 在全新隔离 root 中执行完整 wrapper/helper，以及从候选 hib2 提取的准确分派片段；没有执行其余 hib2 命令。12 项检查覆盖两入口齐全/缺 helper 回退、七个 SO 各自缺失、hib2 后台分派和父环境保持，所有目标均原样返回 37。桌面参数中的空格、空字符串、字面 `*`、中文、`-n` 和字面 `$()` 均保留。齐全时仅为目标设置七项 preload 加已有 `/prior.so`，缺项时保留 `/prior-root` 与 `/prior.so`；SD 资源目录不存在也不阻止内置代码的分派。

这轮目标是诚实名静态 host 记录器，实际 `/proc/self/exe=/dispatch-recorder`；它只记录 ARM SO 路径和环境，不加载这些 SO。报告及原始日志在 `outputs/deployment/app-loader/dispatch-probe/`，各案 stderr 为空。七项 ARM 模块的真实 loader/Ecore 共载证据沿用下节，不重复执行。桌面框架完整启动、实际快照生成及机身功能仍待对应环境验证。

### 原厂 loader/Ecore 共载验收

`tools/probe_persistent_hooks.py` 直接读取 `PERSISTENT_HOOKS` 七项清单，从核验过的原厂 `rootfs.tar` 提取独立依赖，使用原厂 ARM loader、glibc、libdl、libgcc_s、Ecore 和 UDD，加载当前七个候选 SO；每项候选均与现有构建输出逐字节一致，服务加载模块为最终的 68736 字节版本。`runtime/persistent_hooks_probe.c` 已实际重编，在构造函数完成后 fork，由子进程运行真实 Ecore 的 0.35 秒 timer、读取 ready 标记、退出主循环并正常 shutdown。探针保留 `/persistent-hooks-probe.arm` 名称，没有伪装 camera cmdline 或 executable identity、定义 camera 成功桩或创建 camera 对象。

运行环境为新 mount/net/IPC/PID namespace、私有 tmpfs root、uid/gid65534 下的 ARM 用户态 QEMU，仅绑定 `/dev/null` 和 `/dev/urandom`。未运行原厂 app、`hib2.sh`、升级器或休眠，未修改主运行 rootfs 和已有 namespace。汇总及原始证据位于 `outputs/deployment/app-loader/co-load/`；`verification-summary.json` 引用各次报告，不覆盖失败记录。

| 绑定方式／对照 | 实际结果 | 证据与解释 |
|---|---|---|
| 修复前候选的默认 lazy 模式，不设置 `LD_BIND_NOW` | 退出码 0；父 PID18 的六模块映射掩码为 63；子 PID20 `ecore_init=1`，执行 loop callback，`ecore_shutdown=0`、`probe_result=0`、wait status=0 | `lazy-report.json`、`lazy.stdout.log`、`lazy.stderr.log`；PW／曝光／快门／RGB ready 均记录 PID20，子进程退出后 RGB marker 为 -1，正常析构完成清理。 |
| 修复前候选强制 `LD_BIND_NOW=1` | 外层退出 29；六模块到达 main，映射掩码 63；子进程在 `ecore_init` 内 SIGSEGV，wait status=139，未进入 loop | 原始失败已归档到 `original-eager/report.json`、`original-eager/probe.stderr.log`、`original-eager/trace/probe.qemu.log`；stderr 为 `uncaught target signal 11 (Segmentation fault)`。 |
| 修复后六核心 SO 强制 `LD_BIND_NOW=1` | 退出码 0，映射掩码 63；子 PID20 四组 ready；`ecore_init=1`、`ecore_shutdown=0`、`probe_result=0`、wait status=0，退出后 RGB marker=-1 | 历史结果及对应 fixture 源码、ARM binary、工具、日志和 trace 均归档在 `fixed-six/`，报告为 `fixed-six/report.json`。 |
| 当前七模块强制 `LD_BIND_NOW=1` | 退出码 0，父 PID18 与子 PID20 的映射掩码均为 127；四组 ready 均为 PID20；`ecore_init=1`、`ecore_shutdown=0`、`probe_result=0`、wait status=0，退出后 RGB marker=-1 | 当前 `report.json` 明确记录 `hook_count=7`、`expected_hook_mask=127` 及七项 `bytes_equal=true`；原始日志和 trace 位于当前 `probe.*.log` 与 `trace/probe.qemu.log`。 |
| 无 preload 的原厂 Ecore 对照 | `ecore_init=1`，实际 timer callback 执行，`ecore_shutdown=0`；映射掩码 0 | `control-report.json`；fixture 仍断言六个 hook 的映射和 ready，因此返回 `probe_result=22`、子 wait status=5632、外层退出 29。这一退出不是 Ecore 崩溃。 |

当前七模块运行的 LUT marker 为 -1，`service_loader_status_present=false`，符合诚实名非 camera fixture 的 guard 预期：LUT 不产生 camera ready，服务模块不注册服务定时器或执行 SD `start.sh`。七模块映射确认了两个模块在 preload 链中共载及原 Ecore 链完整；它不替代这两个模块的 camera 入口验证。PW／曝光／快门／RGB ready 证明其 Ecore timer 已挂接，并在 fork 后使用实际子进程身份；不等于相机数据接口或成像功能已经通过。服务模块的专属生产 guard 和定时器状态流验证由 `outputs/deployment/service-loader/` 单独记录。

强制 eager 模式的 trace 将异常边界收窄到 `ui_mod.so:ecore_event_handler_add`（ELF offset `0x11184`）调用 `dlsym@PLT`，经过 `.plt`（`0x11750`）解析跳转后访问 NULL；没有执行后面的 `RTLD_NEXT` 原函数间接调用。新增 GOT 采样确认：进入 main、fork 子进程前后以及 `dlopen("libudd.so", RTLD_NOW)` 前后，UI 的 `dlsym` 槽始终等于 `base+0x11750`，resolver 槽为 0；`fopen/getpid/fprintf` 槽则已正常解析。因此错误在 fork/dlopen 之前已经形成。

根因是旧 loader 与 LLD 的重定位布局兼容问题。UI 的 `.rel.dyn` 为 `0x318..0x330`，`.rel.plt` 从 `0x368` 开始，中间隔着 `.ARM.exidx`，且 `DT_RELCOUNT=1`；RGB 模块同样不连续，计数为 2。上游 glibc 2.13 的 `dynamic-link.h` 将这类布局分为两个非 lazy range，`do-rel.h` 每次都从对象读取 `DT_RELCOUNT`，因而会把第二个 range 的前若干 PLT 项也当作 `RELATIVE`；lazy 路径不使用该计数。所存源码是上游版本，原厂 loader 的实际错误 GOT 与下面的最小改动对照支持这一机制。

独立诊断只把 UI/RGB 副本的 `DT_RELCOUNT` 值分别从 1/2 改为 0，其他字节不变，原 fixture 在 `LD_BIND_NOW=1` 下即退出 0。随后保持 C 源码和原构建参数，仅增加 `-Wl,-z,nocombreloc` 重编这两个 SO：不再输出 `DT_RELCOUNT`，UI 为 8960 字节、RGB 为 9804 字节；与其余四个原 SO 共载时同样退出 0，映射掩码 63、子 PID20 的四组 ready、`ecore_init=1`、`ecore_shutdown=0`、wait status=0，RGB marker 在退出后清理。仅改 `dlsym` 的符号类型或显式链接原厂 libdl/libc 的先前对照仍失败，不足以修复该问题。

构建层面的修复已落地：`tools/build_runtime_shim.py`、`tools/build_color_style.py`、`tools/build_lcd_capture.py` 的 LLD 命令均加入该参数，核心 hook 的 C 逻辑未变。六核心模块修复后的 eager 通过证据保存在 `co-load/fixed-six/`，修复前失败保存在 `co-load/original-eager/`。当前七模块持久候选的实际 eager 主检查同样通过，`co-load/report.json` 记录其最终结果。

诊断汇总在 `outputs/deployment/elf-binding/review-summary.json`，实际产物、命令和日志分别位于 `review-got/`、`review-relcount-zero/`、`review-nocombreloc/`；上游源码位于 `review-source/`。独立诊断副本与原失败均保留，正式生产候选的验收以当前 `co-load/report.json` 为准。

此次没有加载两组原厂 camera provider：`libcapture-fw-slpcam-nx300.so` 中的 `CDataManagementService::{GetInstance,GetUserData,SetUserData,GetData,SetData}`，以及 `libiq-fw-slpcam.so` 中的 `CIQDataRepeater::{GetInstance,GetParameter}`、`CIpcIfInfo::{GetColorTone,GetSaturation,GetSharpness,GetContrast}`、`CAeInfoIF::calcTvMaxCapture`、`CIpcParameterNode::{Update,Reset}`、`CIpcs3DLutD4` vtable 和 `m_sRLut`。精确 mangled symbol 和静态 provider 路径见各报告的 `dynamic_symbol_observations` 及汇总；hook 自己导出的同名符号不计为原厂 API 可用。原厂 UDD 的 `ipcs_3d_lut`、`ipcs_color_correction_param`、`ipcm_color_correction_param` 在已加载原厂 scope 中存在，但探针没有调用硬件 setters。因此本节验收止于共载、有限 Ecore 生命周期和 marker 清理，不认证 camera API、成像、快照生成或恢复。

复现已通过的修复后生产候选 eager 主检查：

```powershell
python -X utf8 tools\probe_persistent_hooks.py --trace
```

探针组装时的两个已解决失败也保留了日志：首次编译因 `__aeabi_d2lz` 隐藏符号被 DSO 引用而退出 1，显式链接原厂 `libgcc_s.so.1` 后编译完成；首次 loader 因缺少 `libudd.so` SONAME alias 而退出 127，专属 staging 补齐原厂别名后完成后续运行。日志分别为 `compile-initial.stderr.log` 和 `missing-soname.stderr.log`，不属于上述最终 lazy 结果。

## 早期 SD 候选与函数验证

公共源片段为 `mod/deployment/app-loader/preload-launch.sh`，构建时直接插入两个原厂脚本，避免额外的机内 helper 文件依赖。仅将原厂 app 命令包装为 `nx300m_launch_app 原命令`，`hib2.sh` 原有的 `&` 保留。函数在子 shell 中用 `exec "$@"` 执行原命令，保留传入参数、退出状态和信号目标；原脚本的其余命令保持原字节。

固定检查 `/mnt/mmc/nx300m-mod/lib/` 下全部六个非空、可读普通文件：

- `lut_port.so`
- `ui_mod.so`
- `exposure_controls.so`
- `shutter_limit.so`
- `color-style.so`
- `lcd-capture.so`

全部存在时，仅为 app 及其子进程设置 `NX300M_MOD_ROOT=/mnt/mmc/nx300m-mod`，将上述固定白名单加入 `LD_PRELOAD` 前部，并保留已有 `LD_PRELOAD`。缺卡或任意一项缺失时，直接执行原 app 命令，保留原环境。函数不执行 SD 卡上的脚本、不扫描任意 SO、不改变同一脚本的 `startx`、`hib_exjob` 或后续工具的环境。

这项回退只覆盖**执行 app 当时**的文件状态。已经运行或从快照恢复的 app 不会重新经过这段函数；创建快照时映射进来的 SD SO 在恢复、拔卡后的行为没有验证。因此 `hib2.sh` 候选不能作为已解决快启与拔卡回退的安装方案。

## 产物和重建

运行：

```powershell
python -X utf8 tools\build_stock_app_loader.py
```

所有生成文件位于 `outputs/deployment/app-loader/`：

| 路径 | 内容 |
|---|---|
| `candidate-rootfs/usr/bin/go.sh`、`hib2.sh` | 从本地原厂 TAR 派生的完整候选脚本 |
| `patches/go.sh.patch`、`hib2.sh.patch` | 可逐行审查的原厂差异 |
| `evidence/rootfs/` | 原厂脚本、fstab 与历史 inittab 的原样副本 |
| `evidence/vImage-initramfs/init` | 原厂升级环境 `/init` 原文 |
| `sd-fragment/nx300m-mod/lib/` | 只复制上述六个现有原生 SO；未混入模拟硬件替代模块 |
| `build-report.json` | 原始成员属性、补丁范围、SO 来源/大小/ELF 类型、内核提取位置与语法检查结果 |
| `probe/probe-report.json` | 三个实际函数分支的参数、环境与退出码记录 |

构建器不重新编译公共 runtime，不改动原厂 TAR、UBIFS、固件包、现有 namespace 或运行 rootfs。它读取现有 SO 并检查为 little-endian ELF32 ARM `ET_DYN`；此检查不代表对六个模块共同加载、所有动态依赖或机身功能完成验证。

## 本轮验证与待解决项

实际运行构建器退出码为 0。公共片段和两个候选脚本均通过 Debian `/bin/sh -n`，退出码均为 0、stderr 为空。构建器还验证：去除插入的函数并还原唯一 app 调用后，候选与原厂脚本逐字节一致。

另外实际调用公共函数验证了三个分支。每次在全新的 mount/net/IPC namespace 中，对专属输出内的最小 host chroot 的 `/mnt/mmc` 挂载私有 tmpfs；没有创建或写入 WSL 全局 `/mnt/mmc`。替身为静态 host ELF，记录参数和环境并返回 37；六个存在项使用有效 host 共享库副本。此验证检查函数分支，不加载原生 ARM hook。

| 分支 | 实际 root | 实际 preload | 退出码 / stderr |
|---|---|---|---|
| 根目录不存在 | 保留 `/prior-root` | 保留 `/prior.so` | 37 / 空 |
| 缺少 `lcd-capture.so` | 保留 `/prior-root` | 保留 `/prior.so` | 37 / 空 |
| 六项齐全 | `/mnt/mmc/nx300m-mod` | 六项固定路径，末尾保留 `/prior.so` | 37 / 空 |

三个分支均原样保留 `two words`、空字符串、字面 `star*literal` 和 `中文` 参数。probe runner 最终退出码为 0，三个 `passed` 均为 true。首次准备调用因 Windows 文本 stdin 转换为 CRLF，使 host shell 在第一行 `set` 报 `Illegal option`、退出码 2；尚未创建 fixture 或调用函数，改为传入 UTF-8 字节后已完成上述验证。重现命令为：

```powershell
python -X utf8 outputs\deployment\app-loader\probe\run_probe.py
```

上述早期验证没有执行 `go.sh`、`hib2.sh` 或原厂 app，也未接触运行 rootfs 和已有 namespace。更新后快照路径的连接及直接 resume 已由后续静态核对补齐；普通非更新冷启动的调用方仍未知。内置 SO 候选的实际快照生成、恢复和资源行为尚未运行验证。
