# NX300M 手动镜头调试卡

此入口用于已有 MFW／autoexec 支持的 NX300M，开机后后台采集三次有限状态。默认只读；只有存在与本轮 RUN_ID 匹配的 `ENABLE_NO_LENS_RELEASE` 请求时，才执行一次无镜头释放设置并读回确认，成功后删除该请求。它不启动旧 NXMOD、NXConnect 服务、Custom／Fn 监听或任何进程内补丁，也不刷固件、改快照、改校准及照片。

v5 用于先验证无镜头释放：关机时去掉此前人为压开关的纸片，安装手动镜头，装卡、断开 USB 正常开机。等待约 15 秒后按用户日常的 S 挡尝试拍一张，开机总计停留约 45 秒。此次无需再重复进入无线菜单；Wi-Fi 修复仍是未加载的进程内候选。随后正常关机，取卡接读卡器，让开发端先读取 `nx300m-lens-debug/entry.log`，再读取 `nx300m-lens-debug/logs/<RUN_ID>/`。入口版本为 5；`RUN_ID` 由开发端写入卡上同名文件。同一 RAM 会话内同一 RUN_ID 最多执行一轮，非循环常驻任务；下一轮由开发端换新标识，不需要清除休眠快照。

本轮已完成：round-05 回收确认偏好0→1且读回成功、一次性请求已删除，用户确认 S 挡能拍。SAM_5065／5066 的 RAW＋JPEG 已回收校验，EXIF 为 S 挡1/125秒。无需重复此项测试。用户仍反馈 Wi-Fi 不可用；当前入口没有加载无线补丁，后续工作是实际加载路径，不是再次采集相同门禁状态。证据：`outputs/deployment/manual-lens-debug/card-LOCAL_REDACTED/camera/round-05/report.json`。

`entry.log` 在前置检查前写入，记录进入、启动后台任务和失败原因；若日志仍不存在，只能确认没有留下入口标记，不能仅凭这一点认定固件不支持脚本。后台任务退出时保存已采集的日志，即使采样未全部成功。缺少 boot_id 不再阻断采集。

`session.txt` 记录各样本退出码。`complete.txt` 只说明采集／复制步骤完成，不代表每次读取成功或颜色正常。若进程不可读、型号／字节不符，保留失败原因，不尝试注入、停止或重启相机。

2026-10-27 回收 v2：入口确实执行，三次采样都找到 PID 265 并通过全部 10 项原厂代码核对，但在 singleton 读取处退出 3。原探针对库路径的限定漏掉了匿名 BSS 尾页；两个 singleton 均在 `libiq-fw-slpcam.so` 的 `PT_LOAD` BSS 范围内。修正后仅允许原厂 ELF `p_memsz` 范围内的可读匿名 BSS 尾页，并排除页尾填充、其他库映射及不可读内存。ARM 编译和 9 项合成映射检查通过；尚待下一轮实体采样确认。v3 另记录原厂进程 maps、status 及筛选后的 `LD_PRELOAD`／`NX300M_` 环境设置，用于核对加载条件，不采集全部环境。

同日回收 v3：三次均读到 IQ 和 PP 对象，进程身份与 singleton 指针复核通过；GPIO88=0（检测开关闭合），IQ ready=0、镜头数组计数为0、shading指针为空。用户说明装着手动镜头，并塞了卫生纸压开关，不确定是否生效；此读数确认采样时开关为闭合状态，不能据此说电子镜头已识别。CS 源位于 `/dev/d4_bma` 映射，外部读取返回 EIO，不能确认硬件表内容。原厂进程未映射项目 SO，筛选的 loader 环境为空。两轮相同 boot_id、PID 和 start ticks 不能证明重新执行过原厂 app。

v4 不再读取设备映射的 CS 表，返回 `device_cs_buffer_not_observed`（退出4，部分观察），继续保留 IQ／PP 与身份核对结果。新增只读 `wifi_lens_cache`，来自已核对原厂 getters 对应的三个 UI 缓存：Kmount=`0x4db140`、Lensconnection=`0x4db144`、Lenslock=`0x4db148`。缓存不是实时 driver ioctl 结果，尤其 Kmount 可能尚未刷新。另执行候选释放脚本的 `status` 分支，保存当前无镜头释放偏好；不执行 enable／disable。三次采样约位于启动后0／10／30秒，有限开销后可能略延迟。

v4 实体回收三次均为 Kmount=1、Lensconnection=1、Lenslock=0，无镜头释放=0，14项原厂字节核对全部通过。与原厂分支对应后，Kmount 拒绝块是目前明确的无线拦截条件。v5 不再重复采集相同场景，按用户已有无镜头释放需求准备一次 `enable`；脚本先读合法bool、按需写1，再读回。`no-lens-release.txt` 保留原值／结果，`session.txt` 保留操作及退出码。成功删除请求后，之后启动恢复为只读；失败保留请求和错误，不伪报已开启。该偏好不能单独跳过无线 Kmount 检查。

后续可分别记录“原厂电子镜头关机后换手动镜头”与冷启状态，比较镜头检测、IQ 缓存和 PP 状态。内存快照未加原厂对象锁，不能当作同一瞬间的原子状态；CPU 表也不能代替传感器、DMA 或照片像素。

`candidates/` 里的颜色补丁仅准备在卡上，本入口不会加载 SO。无镜头释放脚本按上述显式请求执行 `enable`，否则只读 `status`；不调用偏好 reset／init／save。颜色及无线候选需要可靠的进程启动加载条件，单独复制 SO 不会生效。详细边界见项目 `docs/MANUAL_LENSES.md`。

停用调试入口可将根目录 `autoexec.sh` 改名，或创建 `nx300m-lens-debug/DISABLED`。此前原有文件已备份到电脑；根目录旧 `nx300m.bin` 已移入 `.nx300m-debug-originals/`，避免被当成当前升级包。不要从该旧文件执行升级。
