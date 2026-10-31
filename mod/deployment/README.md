# NX300M SD 包

将本目录放在 SD 卡根目录，对应 `/mnt/mmc/nx300m-mod`。完整服务入口先复制并逐字节核对 RAM 资源，再从可执行 tmpfs 启动；旧 SD 服务仍存活时拒绝迁移。已有授权的机身 shell 可使用：

```sh
/bin/sh /mnt/mmc/nx300m-mod/ram-start.sh --service
NX300M_MOD_ROOT=/tmp/nx300m-mfw/runtime NX_STATE_DIR=/tmp/nx300m-mfw/state /bin/sh /tmp/nx300m-mfw/runtime/stop.sh
```

配方和 LUT 应用需要相机已加载对应 hooks；未就绪时面板仅供浏览。只复制 SD 文件不会自动注入 hooks，也不会自行建立自动启动入口。

常驻服务的 executable、脚本、cwd、锁、日志、网页和面板资源位于解析后的 `/tmp/nx300m-mfw`。原厂 app 的 `NX300M_MOD_ROOT` 与 LUT 源路径保持原来的 SD 语义，不代表全 app 或原厂存储已迁移；照片仍从 SD 读取。图库 Quick Share 只在唯一 RAM remote 的 ready、boot ID、starttime 和 executable inode 核验成功后派生使用同一 RAM root/state 的面板；失败保留原厂分享。手动重启也使用 RAM runtime 的 start.sh，并设置同一 root/state。

新版内置加载候选默认 `stock`，不注入项目模块。可在本目录的 `launch-profile` 第一行写入 `services`、`controls`、`color` 或 `full` 请求下一次 app exec 的加载分组；创建空文件 `DISABLED` 可优先停用。SD 包本身不提供这两个文件，以免覆盖既有选择。配置不能撤销旧快照里的模块；新版 service-loader 仅用停用标记抑制新的服务启动，已运行的服务仍需 `stop.sh` 停止。完整分组和边界见安装候选的 `docs/LAUNCH_PROFILES.md`。

此包尚未进行实体 NX300M 安装验证。
