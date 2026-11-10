# 2026-11-07 拨盘锁部署前置:冷启动调用方证据闭环

对象:persistent app-loader 候选(`outputs/deployment/app-loader/boot-chain/persistent-candidate/`)长期标注的未确认项——"automatic non-update cold-boot request/exec source"。该项是拨盘锁/动画/Auto ISO(preload `camera_firmware_mod.so`,controls profile)进入部署评估前的最后阻塞。本轮以离线证据关闭;方法与结论如下,全部来自 `work/stock-1.15/rootfs.tar`(各构建共用的已核对 1.15 rootfs)。

## 证据

1. **`etc/init.d/rcS` 全文**(唯一 init 链,已读):
   - `/mnt/ubi1/fw/snapshot` 存在 → 设置环境 → `hib2.sh`(candidate 的 hib2 补丁覆盖)。
   - else 分支仅 `echo off > /sys/power/led`,**不启动 X、不启动任何 app**。
   - snapshot 计数满 10 清空发生在 snapshot 分支内,不产生无主启动路径。
2. **全 rootfs 文件字符串扫描**(每个文件找 `di-camera-app`,16 个命中逐一核上下文):
   - `opt/share/applications/com.samsung.di-camera-app-nx300.desktop` — X 会话注册启动器,candidate 的 desktop 补丁已改指向 `nx300m-camera-launch`(hook 覆盖)。
   - `usr/bin/hib2.sh` — 休眠恢复/快照开机路径,hib2 补丁覆盖。
   - `usr/bin/go.sh` — 内容 `startx; di-camera-app;…`,但**全 rootfs 无任何调用方**(唯一字面命中是 libxkbfile.so 的 `logo.shape_ndx` 误匹配);判定为原厂遗留死脚本,不构成未覆盖路径。
   - `usr/bin/stackinfo.sh`(pidof 调试)、`usr/lib/libtms-agent.so`("di-camera-app exist" 存活监控)、`usr/bin/smart-wifi-app-nx300` / `usr/lib/libgui.so`(进程名/资源字符串)——均非启动器。
3. NX300 的"开机"实际是快照恢复(hib2)路径;真断电冷启动的 rcS else 分支无 app 启动,恢复后首次 app 启动走 desktop 注册器。

## 结论

候选的两处 rootfs 补丁(hib2.sh、desktop → nx300m-camera-launch)已覆盖 rootfs 内**全部** app 启动调用方;`preload.sh` + `nx300m_launch_persistent_app`(controls/full profile)在两条路径都会生效。"cold-boot caller unconfirmed" 可以关闭。

残余边界(如实):
- 假设 rootfs.tar 即完整 rootfs(所有构建共用的核对源;专有 initrd 不在其中,但其不启动用户态 app 的 X 会话)。
- 经验性确认只需一次读卡:rcS 在 snapshot 分支写 `/mnt/ubi1/fw/log/hib.txt`;用户正常开关机若干次后读该文件有新记录,即实证 hib2 就是日常开机路径。
- rootfs 候选部署本身是固件级操作(候选 report 明示 review-only),仍需按候选文档走受控安装;本轮不改发布产物。

## 对拨盘锁的意义

软件链全绿:桥(`camera_firmware_mod.c`,操作 4)→ 实验室原厂会话物理拨盘拦截验证 → installed-VM 九模块共载 → UI feature 页开关+偏好+开机重放(本会话)。此前唯一缺口是部署链;本证据关闭后,下一步为(均需授权后执行):受控安装 persistent rootfs 候选 → `launch-profile=controls` → 卡上 UI 设置页出现可用拨盘锁开关(feature_ready 三重校验通过后启用)。
