# 无线入口与 NXConnect 合并启动

2026-10-27。手动镜头的无线准入补丁已在实体核验，但此前调试卡根 autoexec 只启动该补丁，没有继续启动独立 HTTP 服务。Wi-Fi 关联成功不等于 App 服务存在；这是本轮相机端修复的对象。

`mod/deployment/mfw/autoexec-connected.sh` 同时调用两个已有入口：

- `nx300m-lens-debug/autoexec-wifi.sh`：有限的原厂核验及两处无线条件字节修复。
- `nx300m-mod/mfw-service-entry.sh`：原有逐文件比对的 RAM 暂存入口，再启动独立服务，监听 `0.0.0.0:8781`。服务按当前实际 IPv4 接口发送 UDP 5681 广播，原厂 Wi-Fi 在启动后才获得地址也可被发现。

两个入口均使用原来的低优先级后台机制。Wi-Fi 工具未找到原厂进程或核验失败时，仍会尝试启动 HTTP 服务。合并入口明确关闭 Custom／Fn 快捷键监听，不加载颜色、曝光或其他进程内模块，不重启原厂程序，不修改固件。服务程序和50项 RAM 清单资产沿用已有 ARM 虚拟机通过的逐字节快照。

构建命令：

```powershell
python -X utf8 tools/build_connected_mfw.py
```

产物为 `outputs/deployment/mfw-connected/nx300m-connected.zip`。合并入口的专用 ARM 检查输出在同目录 `arm-vm/`；部署器要求通过报告与 ZIP 摘要一致。虚拟机没有真实相机进程，不能将该环境下 Wi-Fi 工具拒绝修改解释为机身失败，也不能据此宣布手机配对已通过。

安装命令使用当前读卡器 H: 的实际序列：

```powershell
python -X utf8 tools/prepare_connected_mfw_card.py --serial LOCAL_REDACTED
```

部署时备份将替换的文件；旧 SD 分配通过重命名保留，根 autoexec 最后发布。仅生成新的已拥有 RAM 服务重启请求，原有身份核验限制继续生效，不能停止未知进程或原厂相机程序。旧的独立无线一次性请求和禁用标记会保留到旧文件目录，避免覆盖本次配置。照片不参与写入；部署报告区分非目标文件元数据检查与照片内容哈希。

Android 0.20.26 同时修正：标准80／8781端口可兼容识别，建连与缓存使用实际识别出的端口；手动 IP 按目标 IPv4 路由选择 Wi-Fi；网关跳过0.0.0.0；指定网络的 UDP 绑定失败不改走默认蜂窝路由。自定义端口仍按输入使用。

实机最小检查：安全弹出读卡器、关机装卡、断开USB后开机；进入遥控取景器，手机连相机 Wi-Fi 后在新版 App 自动查找。若需手动输入，使用手机当前 Wi-Fi 的真实网关地址；独立服务端口为8781，集成相册也在8781。旧的 `192.168.107.1` 仅在当前网关仍为该地址时适用。

服务运行日志在相机 `/tmp/nx300m-mfw/bootstrap.log` 与 `state/`。连接后可查询 `/api/v1/camera/info`、`/api/v1/network/status`；后者的接口地址、广播状态和发送计数用于区分服务与无线发现问题。原有 USB 媒体暂停不会被这次修改自动清除；它不妨碍读取连接诊断，但相册是否可读仍须核对媒体状态。

## 本轮结果

Android 0.20.26／74 release 构建退出0，APK为 `outputs/nxconnect/NXConnect-NX300M-release.apk`，7,477,062字节。

发布ZIP的独立 ARM 检查退出0，`arm-vm/report.json` 的 passed=true：实际根入口启动HTTP，health和camera/info成功，真实监听0.0.0.0:8781；重复入口仍是同一个服务PID且无X11快捷键helper。虚拟机没有原厂相机进程，无线工具真实返回exit2、camera_process_not_found、零写入，未阻断HTTP。私有VM随后正常退出。NIC模块下载失败：urllib退出1（TLS UNEXPECTED_EOF_WHILE_READING），限时curl命令退出1，stderr为 `curl: (28) Operation timed out after 60014 milliseconds with 175444 out of 25985449 bytes received`。因此使用已有无网卡VM，未验证非回环HTTP和UDP5681，真实广播计数为0，没有将其记为通过。

合并包已写入当前 H:／LOCAL_REDACTED 64GB卡，58项（57个包文件加已拥有RAM服务重启请求）全部回读一致，6项实际变化。备份和报告为 `outputs/deployment/mfw-connected/reader-deployment/card-LOCAL_REDACTED/connected-20261027-01/`；317个非目标文件元数据未变，旧目标文件分配保留于卡上 `.nx300m-before-connected-20261027-01/`。本轮没有写固件、内部存储或快照，也没有在部署过程中执行相机程序。手机连接与广播仍待这次装卡复测。
