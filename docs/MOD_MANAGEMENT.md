# App 的 Mod 与固件管理

首页“Mod 与固件”进入 `ModManagerActivity`，使用当前相机连接。模块文件、相机进程映射和效果启用分别显示；文件存在不代表已加载，映射存在也不代表 LUT 或配方已应用。

当前可执行的操作是选择 LUT、关闭 LUT 并选择原厂表、恢复配方七项原厂中性值。这些动作复用已有接口和读回检查，不卸载进程内 SO。LUT 只报告“已选择”或“已转发”，拍摄成像结果仍以对应链路证据为准。

## 状态与准备接口

- `GET /api/v1/mods/status`：返回型号、当前固件、资源根、七项必要 SD 服务资源是否齐备和九模块列表。每个模块区分 `installed_file`、`mapped` 与 `ready`；无法确认映射或没有独立 ready 接口时返回 `null`。
- `launch_policy` 返回配置分组、停用标记、所选组缺失文件、helper 可用性和查询时对下一次 exec 的文件检查结果；它不代表当前映射，也不检查动态加载成功。当前默认 stock，字段和快照边界见 [LAUNCH_PROFILES.md](LAUNCH_PROFILES.md)。接口仍只读，不写配置、不热卸载。
- `GET /api/v1/mods/install-plan`：返回实际 SD 资源状态、`/mnt/mmc/nx300m.bin` 是否存在，以及 Mod 和原厂固件分别需要的准备步骤。存在固件文件不代表内容或来源已经核验。

两个接口只读，POST 返回 405。ready PID 复用现有相机身份校验，包括实际命令、进程、namespace／root 和 guest PID；映射取自对应进程的 `/proc/PID/maps`。实验室 8765／8782 适配器通用转发这些接口，没有单独制造演示模块状态。

`remote_install_supported` 与 `hot_unload_supported` 当前均为 false：没有经过验证的远程 rootfs 写入或刷机执行器，App 不提供假安装、热卸载或失去恢复通道的服务自停操作。

## 检查安装文件

“检查 Mod 安装候选”只读取完整统一候选 tar.gz，检查 TAR 头、路径、条目类型、文件清单、九模块以及两份原厂恢复文件和配套 SD 包。它不解压执行文件、不上传相机，也不把本地检查叫作机身安装。

NXConnect 0.20.4 增加启动配置卡片，分别展示已配置分组、下次启动的文件检查与当前实际加载项。候选导入同时显示默认分组，声明了加载策略的包还需核对包内 launch-profile 与清单相符。旧服务和旧候选缺少策略字段时显示未提供，不伪装为 stock。刷新开始清空旧 LUT 状态；部分读取失败不会报告全部成功，失败后不保留可写的旧 LUT 目录。LUT/配方操作前后校验连接身份，选择和确认弹窗绑定原连接，连接改变时不向新相机提交旧操作。

“检查 NX300M 固件文件”只读取 SLP 容器，核对型号、分区边界、准确 EOF 和原有补码 CRC。结构与 CRC 一致不证明固件来自三星；修改过的候选也可能有正确 CRC。原厂升级仍应从对应型号支持页取得未修改原包，按三星说明准备 SD、电池和机身菜单更新。

App 不替代机身更新程序。九模块 rootfs 增量及其休眠快照处理仍需要已验证的机身安装入口，不应将普通 SD 资源复制等同于完成内置模块安装。

## 验证

ARM 后端使用 `tools/build_remote.py --backend-only` 构建。只读状态和准备接口由 `tools/probe_mod_management_vm.py` 在现有独立 ARM VM 验证；它仅更新 VM 的 remote，不重启主模拟器，不安装固件。实际结果见 `outputs/deployment/mod-management-vm/report.json`。Android 构建由主线程统一执行。

文件检查器的实际 Kotlin 源码还在纯 JVM 读取了统一安装候选与原厂 1.15 SLP，并拒绝截断 GZIP；结果见 `outputs/deployment/mod-management-jvm/report.json`。

主模拟会话启用相册集成时，remote 来自 `outputs/quickshare/gallery`。更新后端先执行 `tools/build_gallery_share.py --remote-only`，再运行 `tools/update_stock_remote.py`：核对 namespace、相机与 remote 进程身份，只终止旧 remote，调用已有启动入口部署新服务，并检查相机 PID 与启动时间不变以及管理接口可读。该操作不更新相机已映射的 SO，新 SO 仍需在适用的启动／快照流程加载。
