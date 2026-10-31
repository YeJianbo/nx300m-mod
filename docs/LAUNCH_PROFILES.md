# 分阶段加载与停用

当前开发候选把九个 SO 放在内置 `/usr/lib/nx300m-mod`，默认配置为 `stock`，不增加本项目的 preload。包装器保留已有环境；它不会清除其他来源的 LD_PRELOAD。下面是候选代码的配置约定，机身临时执行入口、安装和安全重启流程仍待实机确认。仅放入 SD 文件不会安装模块，也不会重新启动相机 app。

| 配置 | 加载模块 | 用途 |
| --- | --- | --- |
| `stock` | 无 | 原命令、原环境 |
| `services` | service-loader | 仅启动 SD 服务；屏幕采集和进程内控制不可用 |
| `controls` | exposure_controls、shutter_limit、lcd-capture、service-loader、camera_firmware_mod | 曝光、屏幕及机身控制；不含 LUT 和相册分享 hook |
| `color` | lut_port、ui_mod、color-style、service-loader | 配方与 LUT；不含屏幕和相册分享 hook |
| `full` | 全部九项 | 完整集成；保留已验证的模块顺序 |

内置配置文件是 `/usr/lib/nx300m-mod/launch-profile`。SD 上存在 `/mnt/mmc/nx300m-mod/launch-profile` 时优先读取 SD 配置。第一行必须是表中一个小写名称，不带空格或 BOM；其余行不参与配置。未知值、空内容、不可读文件回到 `stock`。配置按数据读取，不执行 shell 内容，不扫描额外 SO。所选组缺少可读非空普通 SO 时整组不加载，保留原命令和环境；其他组缺文件不妨碍本组。

在电脑准备 SD 时可用纯文本编辑器写入该文件，例如第一行 `services`。PowerShell `Set-Content -LiteralPath 'X:\nx300m-mod\launch-profile' -Value services -Encoding ascii` 中的盘符必须替换为实际 SD 盘符。确认最低组正常后再选择需要的组；这只是配置准备，不是已经通过实体测试的安装顺序。

以下任一标记存在时，包装器跳过项目 preload：

- `/usr/lib/nx300m-mod/DISABLED`
- `/mnt/mmc/nx300m-mod/DISABLED`，可直接在电脑上创建空文件。

标记优先于所有 profile，目录和符号链接也按停用处理。不删除模块文件，以保留旧映射所需的 backing。

新版本 service-loader 的 Ecore 定时器同样检查这两个标记，抑制之后的服务启动尝试，包括资源变化后的重试。它仍回收已经派生的启动子进程，不终止已运行的 remote，不卸载 app 内模块；需要停止服务时使用现有 `stop.sh`。移除标记只让未成功的启动尝试继续，保留既有手动停止语义，不重复启动已成功的服务。

配置和停用标记对 app 模块的作用点是**下一次 exec**。原厂快照恢复跳过包装器；如果快照里是旧版 service-loader，也不会获得这次新增的停用检查。标记不能修复构造函数崩溃、卡住的主循环或坏快照，不能作为救砖保证。更新内置 SO 或卸载时仍须保留 backing，并按已确认的机型流程处理快照；此候选不生成、擦除或回滚快照。

`GET /api/v1/mods/status` 的 `launch_policy.configured_profile` 表示配置请求。`selected_files_complete` 和 `unavailable_modules` 检查选中组的普通文件、非空和可读条件；`loader_available` 检查共用 helper。停用、缺 helper 或缺组内文件时，`next_exec_profile` 返回 stock，否则返回所选组。这个结果只对应查询时的文件检查，不证明稍后加载成功，也不检查动态依赖和构造函数。旧服务未提供这些字段时，App 保留未知提示。

当前实际模块仍看 `modules[].mapped`，无法确认时是 `null`；`snapshot_rollback_supported` 保持 false。只加载服务组时，相机 PID 可以从 loader 状态取得，但仍核对真实相机可执行文件身份。NXConnect 的 Mod 页分别显示配置、下次启动检查、当前加载数量与缺失模块；导入候选时显示包内默认分组，并核对它与清单一致。LUT 选择和配方恢复绑定于显示状态的相机连接，弹窗期间切换连接后需要刷新，防止沿用旧目录或确认操作。

离线验证复用 `tools/probe_persistent_dispatch.py` 和 `tools/probe_service_loader.py --timer-only`。前者使用原厂 ARM BusyBox 检查两入口、分组、停用、缺项、参数和环境；目标为静态 host recorder，不加载 SO。后者使用原厂 ARM loader/libc/Ecore 直接注册测试定时器，验证停用抑制、移除后继续、有限重试和子进程回收；不冒充真实 camera 进程或实体休眠恢复。
