# 单槽 LUT 预览候选包

原厂 Miniature 槽位提供取景节点、工作区和完成回调，MOD 独立选择 LUT 与 0–100% 强度。其他原厂滤镜保持原功能。成片继续使用已有 JPEG LUT 模块。

## 安装内容

这是现有 MFW 1.15 MOD 的增量包。先备份将被覆盖的同名文件，再把包内 `nx300m-mod` 合并到卡上的同名目录。保留现有 `looks`、`looks.txt`、黑白风格和开机脚本。包内不含固件、`autoexec.sh` 或新的自动注入入口。

`bin/lut_engine.arm` 和 `bin/nx-remote-controller.arm` 增加选择同步；预览加载器、RAM 模块和脚本负责启用、切换及恢复。机身 MOD 菜单增加“取景预览”子菜单。写卡工具保留开机脚本，并更新已有服务重载标记，让下次启动加载新版菜单；开机不会自动启用预览。

## 首次验证

全程在相机上操作，不需要手机、电脑连接或 Shell。

首次测试先临时将 **画质设为仅 JPEG（超高画质，不选 RAW 或 RAW+JPEG）**，图片向导选标准，驱动模式选单张。当前候选仍保留原厂 SmartFilter 的 RAW 菜单限制；这一步用于进入宿主槽位验证取景，不代表已支持 RAW 与该预览同时使用。如果“智能滤镜”仍呈灰色，先停在菜单检查当前设置，不要反复启用 MOD。原厂中文菜单将 Miniature 称为“微型”。图片向导与智能滤镜不能同时设置，见 [NX300M 使用说明书第 101 页](https://manualzilla.com/doc/6848545/samsung-nx300m-%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C)。

1. 安全弹出卡，装回相机开机。
2. 长按 **Custom** 约 1.2 秒后松开，在 MOD 菜单选择 **取景预览 → 启用预览 · 原样**。上下左右选择，OK 确认，也可以触摸。操作成功后面板自动关闭。
3. 在原厂智能滤镜中选择 **微型（Miniature）**。先确认取景和按键正常，本轮先不同时拍照。
4. 再长按 Custom，选择 **取景预览 → 单位表测试**。返回取景后，画面应基本保持原样。
5. 再进入同一菜单，选择 **黑白测试**。返回取景后，画面应变成黑白，不应偏绿、偏紫、花屏或冻结。
6. 基础检查通过后，选择 **跟随当前 LUT**。通过已有 LUT 菜单或短按 Custom 切换风格，观察取景是否跟随。Miniature 槽位要保持选中。

操作失败时面板保留并显示错误，可用 MENU 退出；不要连续重复触发。**预览状态**可查看请求和处理代次：`requested_generation` 表示已选择，`applied_generation` 追上它才表示后端完成了帧处理，计数本身不能证明真实像素正确。

恢复时长按 Custom，选择 **取景预览 → 关闭预览并恢复**，返回取景后关闭原厂智能滤镜。该操作恢复五个原厂调用槽，关闭后续选择同步；保留 RAM 映射供已取到旧函数地址的调用返回。不要手动删除驻留状态文件。

## 开发诊断命令

以下为已有相机 ARM Shell 的调试命令，机身测试不需要输入：

```sh
cd /mnt/mmc/nx300m-mod
sh smartfilter-preview.sh enable
sh smartfilter-preview.sh stock
sh smartfilter-preview.sh status
```

然后在原厂相机选择 Miniature 槽位。原厂菜单目前仍显示 Miniature。

包内测试表与 ARM 模拟使用同一份文件：

```sh
sh smartfilter-preview.sh test-identity
sh smartfilter-preview.sh test-monochrome
sh smartfilter-preview.sh sync
```

测试命令只改变预览；`sync` 恢复跟随当前 MOD 选择。原样或 0% 应透传，单位表应尽量保持原图，黑白应去掉色度且不出现绿／紫色偏。`status` 的 `requested_generation` 表示已选择，`applied_generation` 追上它才表示后端完成了帧处理；这个计数本身不能证明真实像素正确。

完成基础检查后，通过已有 MOD 面板选择风格和强度，或短按 Custom 轮换。

切换也可显式执行：

```sh
sh lut-with-preview.sh apply LOOK_ID 100
sh lut-with-preview.sh stock
```

`LOOK_ID` 使用现有 `looks.txt` 第一列，预览读取对应直接 `table.d4lut`。它不使用含原厂映射的 `table-stock3d.d4lut`。

## 恢复

```sh
sh smartfilter-preview.sh restore
```

该命令恢复五个原厂调用槽，并关闭后续选择同步；保留 RAM 映射，供可能已取到旧函数地址的调用安全返回。再把原厂滤镜选回关闭即可。相同构建可以重新 `enable`。不要手动删除驻留状态文件来跳过加载器检查。

## 当前验证范围

当前源码通过原厂 ARM 用户态控制流和真实 ptrace／PLT 装入模拟，使用核对过的 MFW `libmisc.so`。像素设备边界由明确的软件参考后端代替，未模拟 DRIMe IV ISP。新增机身菜单另在 ARM Xvfb 中检查按键导航、六个动作的派发、失败提示、自动关闭和原厂焦点恢复；该菜单检查的预览加载命令使用替代后端。

尚需实机确认真实 YCC 颜色域、3D 插值、cache 同步、DMA 完成及错误恢复。原厂 SmartFilter 的 RAW 菜单限制仍存在；本包没有修改 RAW Bayer 数据，也未放开该菜单限制。先完成上述顺序像素检查，再单独验证拍照时的资源交接和原厂参数恢复。

调用链、实现和模拟证据见项目 `docs/SMARTFILTER_LUT_PREVIEW.md`；具体写卡结果和备份位置以 `latest-card-deployment.json` 指向的报告为准。
