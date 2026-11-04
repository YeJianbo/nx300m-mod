# MFW 遥控与相册服务

服务通过 MFW 的 SD 脚本入口启动，提供相机状态、相册、下载、屏幕和按键等接口，供 NXConnect 使用。照片缩略图和照片传输已有实机使用反馈；功能和接口见 [遥控与相册](REMOTE.md)。

## 启动

SD 卡根目录包含 `autoexec.sh` 和 `nx300m-mod` 目录。启动脚本将服务程序、网页、面板资源和状态目录放到 `/tmp/nx300m-mfw`；照片仍从 SD 卡读取。更新前备份已有入口和照片，安装与停用方法见 [安装与启动](INSTALL.md)。

构建工具和参数以仓库 `tools/build_mfw_services.py` 及其帮助信息为准。已有机身 shell 时，可手动启动 RAM 服务：

```sh
/bin/sh /mnt/mmc/nx300m-mod/ram-start.sh --service
```

## 状态与诊断

- `GET /api/v1/health`：检查服务是否响应。
- `GET /api/v1/network/status`：查看网络接口和发现服务状态。
- `GET /api/v1/diagnostics`：查看服务日志。
- `GET /api/v1/storage/status` 和 `GET /api/v1/media/status`：查看存储观察与媒体暂停状态。

服务退出及 RAM 资源的管理由服务脚本负责，停止方法见 [安装与启动](INSTALL.md)。媒体暂停只表示本服务释放自己的媒体操作，不能代替原厂系统的存储交接。

## 机身面板

短按 Custom 轮换收藏或常用 LUT，长按打开 MOD 主菜单。主菜单包含 LUT、镜头管理、曝光增强、手机连接、系统设置五个入口，支持转盘选择、OK 进入、MENU 返回；调试位于系统设置。未接入或未验证的增强项目显示不可用。

LUT 页顶部的实时预览开关只控制取景效果，关闭后照片继续套当前 LUT；独立红色「关闭 LUT」关闭两条链路。Fn 进入微调，再按 Fn 保存并返回列表，保存不会退出面板。打开面板不会自动停止预览。完整操作见 [LUT 面板](LUT_PANEL.md)，字体直读和后台事件等待的实现见 [菜单性能](MENU_PERFORMANCE.md)。
