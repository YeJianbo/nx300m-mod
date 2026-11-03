# S2：RAW / RAW+JPEG 的 LUT 取景候选包

**2026-11-01：ABI2 S2 首轮实机验收未通过，存在严重卡顿及拍摄后长时间迟滞，不应继续作为可用预览使用。**

本文保留原 ABI2 实验背景；新的独立计时诊断版见 [SMARTFILTER_TIMING.md](SMARTFILTER_TIMING.md)，它不是已完成的性能修复。

S2 在原厂智能滤镜保持关闭时，按精确调用点借用普通取景 SmartFilter 节点，并提供独立工作区。原厂 RAW 菜单限制并未修改：因为不需要打开原厂滤镜，RAW 选择与合成预览才有机会同时工作。JPEG 继续使用原有 LUT 引擎，RAW Bayer 文件不做处理。

本轮不优化卡顿，不改原厂代码段、不刷固件、不增加开机自动注入。

## 安装与版本边界

- 测试包为 `nx300m-smartfilter-s2-preview.zip`，需要现有 NX300M 1.15 MFW MOD。
- ABI2 同时保留旧 S1 的五槽方式及独立 S2 七槽方式，但**同一相机进程不能在 S1/S2 安装类型之间切换**。
- 安装前备份覆盖文件；正常关机后换卡，再正常启动。重载 MOD 服务不能替代相机进程重启。
- 若提示驻留模块/版本不匹配，不要删除 `/var/run` 状态来绕过检查。使用匹配的旧工具处理旧模块，再正常重启。
- 不覆盖 `looks`、`looks.txt` 或 `autoexec.sh`；预览默认不启用。
- 原 ABI2 包已在备份后写卡并完成首轮测试，但实机未通过；ABI3 诊断版尚未写卡。任何后续写卡仍须先备份，读回核对哈希并检查 FAT dirty 位。

## S2 机身测试顺序

1. 原厂 **智能滤镜 → 关闭**，普通照片取景、常规画幅、单张驱动。画质先选 RAW+JPEG，图片向导标准。
2. 长按 Custom 打开 MOD，进入 **取景预览**，选择末尾的 **启用 S2 · RAW 预览**。不要先选旧的“启用预览 · 原样”，后者首次安装的是 S1。
3. 先检查取景和按键正常，再打开 **预览状态**。`profile=1` 是 S2，`insert_enabled=1` 表示允许插入；这两项不能证明当前管线已经接管。
4. 选择 **单位表测试**，再选 **黑白测试**。黑白测试只改变取景；确认没有偏绿/偏紫、花屏、冻结。
5. **启用发生在当前取景管线建立之后，可能尚未生效。** 可先正常进入回放再返回取景。若没有接管且机身提供焦点峰值开关，可只切换一次并恢复原设置：静态代码在焦点峰值状态变化时会请求重建，实际菜单能否触发尚待上机确认。若 `s2_frames` 始终为 0、`mode_forged[0]` 为 0，记录状态并停止测试，不强制调用原厂重建入口、不反复注入。
6. 接管且颜色正常后，分别在 RAW+JPEG、RAW 下单张拍摄，确认保存、返回取景、相机内 RAW 显影正常。不要把 RAW 内嵌 JPEG 的黑白当作 Bayer 数据被改写的证明。
7. 最后测试 **跟随当前 LUT**，短按 Custom 或原有面板切换风格/强度。原厂智能滤镜始终保持关闭。

本阶段只支持验证普通照片取景路径。Wide、视频、放大取景、连续拍摄和切换真实滤镜尚未完成硬件验证，不作为本轮测试范围。

## 关闭：S2 是软停，不是热卸载

选择 **关闭预览并恢复** 后：

- LUT 和新的节点插入停止，选择同步关闭；
- 已存在或已经排队的节点仍走原样直通并完成原厂回调；
- 七个钩子、RAM 模块和工作区保留至相机进程结束；
- `installed=true`、`enabled=false`、`routing_retained=true` 是预期状态，不代表关闭失败；
- **正常关机再开机才是完整退出。不要手动删驻留状态，也不要在取景运行时解除映射。**

S1 仍保留原来的五槽恢复方式；不能把 S1 的立即恢复槽位套用到 S2。

## 状态与日志

重点记录 `profile`、`enabled`、`insert_enabled`、`mode_forged`、`s2_path`、`s2_frames`、`s2_fallback`、`s2_fault`、`work_base`、`work_size`、`last_error` 和拍摄相关计数。

- `mode_forged` 顺序：normal Create、worker execute、normal B2Y、normal LDC。
- `s2_path` 是最近一次普通 Create 的合成路由决策，不是硬件显示正确的证明。
- `s2_frames` 是生产桥接观察到的合成来源帧，不能冒充已完成回调或硬件像素验证。
- `requested_generation` 是请求代次；`applied_generation` 追上后仍需肉眼检查图像。
- `s2_fallback` 包括软停或故障的安全直通；原有 `passthrough` 计数不包括这里的所有直接回退。
- `s2_fault` 或错误出现时先软停，再正常关机，回传 `nx300m-mod/logs/fn-key.log` 和对应照片文件名。

## 已有 ARM Shell 的开发命令

机身菜单测试不需要 Shell。以下命令仅供已有授权调试连接使用：

```bash
sh /mnt/mmc/nx300m-mod/smartfilter-preview.sh enable-s2-stock
```

```bash
sh /mnt/mmc/nx300m-mod/smartfilter-preview.sh test-identity
```

```bash
sh /mnt/mmc/nx300m-mod/smartfilter-preview.sh test-monochrome
```

```bash
sh /mnt/mmc/nx300m-mod/smartfilter-preview.sh sync
```

```bash
sh /mnt/mmc/nx300m-mod/smartfilter-preview.sh status
```

```bash
sh /mnt/mmc/nx300m-mod/smartfilter-preview.sh restore
```

`enable-s2` 会直接跟随当前 LUT；`enable-s2-stock` 先原样。旧 `enable` / `enable-stock` 可用于干净进程中的 S1。预览读直接 `table.d4lut`，不读 JPEG 合成表 `table-stock3d.d4lut`。

## 验证范围与限制

VM 使用真实 ptrace loader、七个真实 GOT 槽、原厂模式 getter、Prepare/Execute/worker、ADE 构造及既有 IPCS/IQ 适配器。设备/缓存及图像像素边界是明确的软件替身，不是 DRIMe IV ISP 仿真。

路径白名单检查执行原厂 Create/B2Y/LDC 的前缀，在 getter 后的下一个环境调用处退出，因此能验证精确 LR 和合成路由决策，**不能声称完成真实硬件管线重建**。原厂格式菜单及传感器 RAW 数据也没有在 VM 中模拟。

独立匿名工作区为 `0x168000` 字节、页对齐。已接管的 YCC 适配器只提交输入/输出平面，不显式提交该工作平面；初始化失败和软停时禁止落回原厂微型算法。其完整 DMA/物理地址/缓存契约、真实并发节点、硬件颜色、RAW 保存和自然重建仍须上机验证。

原有 S1 包及匹配恢复工具保存在本地 `outputs/deployment/smartfilter-preview/s1-before-s2`，不混入 S2 安装包。
