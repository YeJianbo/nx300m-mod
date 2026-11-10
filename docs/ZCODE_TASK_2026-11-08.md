# 2026-11-08 ZCode：免刷机原厂增强接入

用户授权：将本轮已逆向的接口交给 ZCode 实际实现，回复后约十分钟执行一次。模型沿用 Start Plan / GLM-5.3-Flash / high。精确触发时间及原生任务 ID 以 `outputs/review/2026-11-08-runtime-controls/schedule-result.json` 为准。

## 工作区和边界

权威工作区 `.`。根目录没有 `.git`；`outputs/github-source/nx300m-mod` 是发布镜像，不可作为工作副本。先检查当前文件及适用规范，保留已有修改；对受影响文件做本地备份，不整树回退。沿现有 NX300M 会话串行完成，不再创建竞争源码写入任务。

先读：

1. `docs/RUNTIME_CONTROL_INTERFACES.md`。
2. `outputs/controls/runtime-interfaces/contracts.json`、`interfaces.json` 及任务相关的 `.asm`。
3. `docs/DEVICE_STATUS_2026-11-07.md`、`docs/SETTINGS_DEPLOYMENT_2026-11-07.md`、`docs/ISSUES_2026-11-07.md`。
4. Auto ISO 的 `docs/CONTROLS.md`、A/P 的 `docs/SHUTTER_LIMIT.md`、CS 的 `docs/MANUAL_LENS_CS_RAM.md`。

用户要求**不刷固件**。不要生成刷机方案或修改 NAND、磁盘中的原厂 ELF，不复用旧 V0（该方案有曝光问题）。可参考旧逆向证据，但实现从当前独立模块继续。保持原厂 Reset、Still IQ、Session、拍照仲裁、RAW、LCD DMA；不做视频 LUT，不扩展 LUT UI 或混合功能。

本次不连接实体、不写卡、不安装手机、不推送、不发布。只用指定模型，不自行换账号或供应商。遇额度限制记录实际错误及续作位置，不冒充完成。

## 1. 最先打通正常原厂线程的免刷机执行入口

Ecore 已确认：库 `libecore.so.1.2.0` 的 select 回调槽 ELF VA=`0x21bb4`；set=`0xdb30`、get=`0xdb4c`；内部等待 `0xc88c` 在 `0xca8c` 间接调用。主循环自然经过这一等待路径。磁盘槽值为零，重定位为 select；运行时可能已经被 glib 包装。

基于当前受控 RAM 加载器机制做最小实现和隔离 ARM VM 验证：一次性 wrapper 在原厂自然 Ecore 线程完成必要注册，完整保留当前 select 的参数、返回值、errno 和行为。不能 Hook 公共 iterate 就认定覆盖 begin。不能从 ptrace 随机暂停线程直接调用会分配内存、获取原厂锁或操作 Ecore 的函数。`thread_safe_call_async` 是进程内 API，首次执行入口仍须解决。

必须处理已有阻塞的唤醒、重复安装、当前回调身份变化、卸载与异常恢复，不让一次控制请求永久替换原厂等待语义。不要为了完成任务移除身份校验。先用无硬件副作用的回调确认线程、一次性注册及恢复，再接下面功能。

若这条槽方案因运行证据不可用，应定位明确原因并继续查原厂已有安全派发点；不要让界面伪装 ready。独立已验证路径可继续完善，涉及未证明线程的操作不得默认启用。

## 2. 取景专属 Custom 键，解决图库删除误开 LUT

关键新证据不是同名导出符号：

```text
UI_Event_Key_Manager → UI_Get_State(2)
state_table @0x26a6e0
state=1 → UI_Key_Capture_Liveview @0x17bc98
  → 原厂过滤器 @0x17d128
  → key_table @0x261590
  → key=12 的槽 @0x2615c0 → handler @0x17c748
```

`gUI_Key_Liveview_Custom @0x17d468` 只是包装函数，真实按键表绕过它。不能再仅 Hook 包装符号就报告实体入口接入。取景表在 `.rodata`，正确处理权限与恢复；事件已定位 key@+4、action@+8，不猜整个结构大小。

在已确认的取景上下文处理自定义键；保留前置过滤、action 规则及原处理回退。回放状态 18/19/20/21 走自己的派发，Lock3A 状态9也不可被全局接管。检查并处理 `remote/menu_key.h` 的旧被动 Custom/KP_Delete 启动路径，避免双入口和继续误触；新入口尚未 ready 时不可一刀切移除当前能用的菜单入口。

必要验证限于当前明确故障：真实取景事件能开面板且只开一次；回放删除不启动面板；其他正常按键和 MENU/OK/Fn 语义保留；身份不符与未安装面板回退原厂。复用既有映射测试，不重复无关整套测试。

## 3. 菜单真实用户活动与电源计时

`UI_Set_Power_Timer(bool) @0x1d0f88`：**true 停 timer0x28/0x29/0x2c，false 按原厂门禁及偏好重启**。原厂触摸 down/up 正是 true/false，不要弄反。

0x28 callback 的日志身份是 Display_Save；0x29 是 Power_Save；0x2c 含义仍未完整确认。`pm_set_trigger` 仅发 `/tmp/pm_sock` 数据报，不会重置 app 私有 Ecore timers，发送成功没有业务 ack。

通过正常线程和现有控制服务接真实菜单输入活动；既支持触摸也支持按键/滚轮。退出、无活动和进程异常结束后，应恢复原厂正常超时；不永久持有电源锁，不用模拟按键或单独 XResetScreenSaver 冒充修复。按原厂定时器行为确定合理的活动节流，避免每个输入都产生进程冷启动或高频文件写入。

针对上述已知息屏回归，仅验证活动时计时处理、停止活动后恢复、正常退出与异常结束恢复。VM 不能证明实体功耗、实际息屏时间或关机安全。

## 4. 接入独立动画和 Auto ISO，菜单反映真实状态

动画现有独立候选：`runtime/settings_animation*`、`tools/build_settings_animation.py`、`mod/deployment/animation-restore.sh`。已有 ARM 构建与反汇编检查，尚未运行新加载器；新菜单不能套用上一轮旧二进制的 VM 报告。补当前候选的必要运行验证，保留模式图标最终状态和原厂正常回退；不引入曝光 Hook。

Auto ISO：IQ 参数37，`SetParameter @0x318bd0`，`GetISOMaxSetting @0x318334`，singleton槽=`0x4616b8`。原厂 UserData0x3b 最高有效编码仍6400=`0x003b000f`，12800/25600须走现有 IQ 扩展，不伪造原厂索引。真实 AE 消费者 `0x2f8b50` 还有模式限幅。

在正常执行入口与对象生命周期确认后，接现有模块、菜单、保存和开机重放。区分“偏好已保存”“模块已加载”“参数读回”“当前模式有效上限”，不要仅凭菜单值报告25600已生效。复用 `outputs/controls/auto-iso-policy-probe.json` 有效证据，仅对新接入与失败信号补有限验证。不得修改当前 ISO、Lock3A 或测光函数来伪造效果。

## 5. A/P 快门上下限：从联合求解继续

前四项完成后推进当前缺失的范围控制。先查实际模式门禁，确认 UI P/A/S/M=2/3/4/5、IQ DIALMODE=1/2/3/4、AE capture=0/1/2/3 不混用。A 求解器还被微距复用，特殊场景可能回落 AE0；S/M、视频、3D和其他场景维持原厂行为。

现已确认 `AeMathCalcProgramLine @0x2ce900` 按端点数读取 `(Av16,Tv16,Sv16,bias16)`；A两点、P五点；之后 `calcSvshiftForIsoAuto @0x3018a0` 还会同时偏移Tv/Sv。`+0xf5e` 是Sv基准，**不是慢快门**；`+0xf64/+0xf66` 是Tv上下界；慢边界刷新时由常量-80写入。

不能只截最终Tv，不能只改端点就声称完整控制。设计并实现有明确曝光配平及边界不足处理的最小独立方案，A/P范围分别保存；无足够ISO或光圈余量时真实报告限制，保留原厂求解行为的可恢复路径。先构建；由于曾有曝光bug，必要时用真实原厂数学/AE消费者做有限离线边界验证。完整模式门禁或曝光关系未证明时保留候选，不纳入默认启动包。

## 6. CS 泛绿与镜头关联的后续接口缺口

CS已有 Init/Update/Wait，但PP硬件提交串行化与attach/detach时序未证明。只继续定位锁、调用线程和等待完成协议，记录可实现方案；不能仅因存在Wait函数就任意线程调用Init，更不能默认启用泛绿生产补丁。保持现有CS加载门禁。

前项无可继续的软件工作时，再定位镜头拍摄快照与EXIF的可靠关联点；不要修改RAW或JPEG像素链，也不要只补菜单就报告EXIF完成。无需为填满时长继续扩展功能。

## 构建、模拟与交付

Python仅 `D:/conda/envs/nx300m-mod/python.exe -X utf8`，禁止venv。先构建受影响模块，再仅为上述明确回归/线程入口做必要的有限定向验证。用户要求映射在模拟器中验证，不能用符号存在或编译通过代替入口运行。复用现有有效测试，不反复重跑已通过且未受影响的工作。私有VM串行；mount/PID命名空间隔离，禁止在共享WSL直接mount `/var/run`，不得杀其他项目进程。

修改部署资源时检查首装/升级生成器及安装器锁定资源合同，生成本轮本地候选；不覆盖已上卡文件与公开发布附件。不应为本次接入重写已经稳定的LUT UI或图像硬件后端。

实际完成后输出：

- `docs/AGENT_WORKLOG_2026-11-08-runtime-controls.md`：改了什么、原因、备份、命令及退出码、真实日志、未完成项与续作位置。
- `outputs/review/2026-11-08-runtime-controls/result.json`：`checked_at`、`scope`、`source_identity`、`changes`、`checks`（命令/退出码/证据）、`remaining`（状态/具体原因/证据）、`device_verification_required`。
- 更新现有状态文档，只修改本轮实际改变的事实。静态、VM、实体证据分开；未运行、超时、额度不足均不能写通过。
- 最小上机清单及预期：取景快捷键、回放删除、菜单活动/退出后的息屏、动画、Auto ISO开关及重启恢复。对本轮未完成项标明不可用，不要求用户中途上机才能推进其他离线部分。

用户要的是实现，不能仅交计划。存在局部证据缺口时继续其他可独立工作，最终准确列出候选与未验证项；不自动另建定时任务。
