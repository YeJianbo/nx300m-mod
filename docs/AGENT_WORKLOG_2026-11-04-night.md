# NX300M MOD 夜间无人值守工作记录 · 2026-11-04

执行者:闲时自动化 Agent(ZCode,offpeak 免费档)。依据 `docs/AGENT_HANDOFF_2026-11-04.md`,按"直接起步任务"顺序完成 P0 取证 + 可离线修复、R1–R3 实现与回归、D 拨盘锁/动画/Auto ISO 接线与持久化。未写卡、未发布、未改 RAW/Session/LCD DMA/IPC 架构。

## 0. 基线

- 公开副本 `outputs/github-source/nx300m-mod`:HEAD `45847812c3da...`(与交接一致),无新提交、无未提交改动。
- 修改前备份:`outputs/review/2026-11-04-night/backup/mod/metol/`(nxui.c、nxui_lut_panel.h、lut_tune_cache.h、nxui_prefs.h、remote/remote.c、remote/PORT.md)。
- 最终产物:`outputs/metol/nxui.arm` SHA256 `6298b8e6e73808e1cff365c225b2e3c7275df411368d6ca57e1d9de4a6f49b01`;`lut_engine.arm`/`recipe_engine.arm` 同轮完整构建产出。

## 1. P0 无线入口卡死/不能关机

### 1.1 取证结论(只读审计,证据为 文件:行号)

审计范围:mod/metol 全部、mod/nxconnect net 层、MFW/服务脚本。要点:

1. mod/metol 内 **无 system()/popen()**;remote 守护进程主循环 `mg_mgr_poll` 50ms 有界,X 同步调用全部隔离在 1500ms 硬超时的 x11 worker 子进程(`remote/x11_worker.h:5,158-165`)。
2. **无界等待链(已修)**:`lut_engine.c::service_control()` 与 `lut_engine_shared.h::nx_lut_sync_preview()` 原为阻塞 `waitpid` 无超时;脚本(lut-load.sh / smartfilter-preview.sh)挂死 → 引擎不退 → nxui 面板进程永不退出(`nxui.c` 对 LUT 子任务有意不 kill)→ 表现为"关不掉面板/二次操作被拒"。
3. **电源键被吞(候选,未修)**:`panel_input.h` 将 XF86PowerOff 等视作 S1 捕获并用 `XSendEvent` 合成转发;项目自身代码(`custom_key.h:231`)就忽略合成键,若原厂 EFL 同样忽略,则面板持焦期间物理电源键失效。需实机验证,本轮未改输入路径。
4. **最强嫌疑(范围外,未修)**:卡死发生在原厂 app 进程内,与 `runtime/*.c` 注入 hook(如 `ipcs_session.c` 的恢复循环)在无线模式切换管线重建时的相互作用。按交接边界,Session 无新回归证据不进入;该项列为独立实机取证任务。
5. **"手机直连失败"部署因素(静态核实)**:非 connected 的 service 安装默认监听 `127.0.0.1:8781`(`mod/deployment/service.sh:33`),任何手机网络都连不上;connected 测试卡 `autoexec.sh` 明确 `NX_REMOTE_LISTEN=0.0.0.0:8781`(已解包核实)。若用户卡是 connected 构建,则排除此项,主因回到 3/4 与手机侧链路。
6. NXConnect 手机端未发现主线程网络阻塞;Network 回调清理基本完整;8782 仅 PC 模拟器用,手机端只探 8781/80,与相机 beacon 逻辑一致。

### 1.2 已落地修复(离线可验证)

`lut_engine.c::service_control()`、`lut_engine_shared.h::nx_lut_sync_preview()`:waitpid 改为 WNOHANG 轮询 + 20s/15s 上限 → SIGTERM(1s)→ SIGKILL(1s)有界升级,超时记 `LUT_CONTROL ... phase=timeout`。脚本挂死从"面板永久滞留"变为"引擎有界失败 → UI 显示应用失败可重试"。`lut_engine_shared.h` 增加 `<signal.h>`。

### 1.3 最小实机验证清单(P0,未做,不要求用户反复重现)

1. 进手机连接页 → 无线未就绪/失败 → 可返回;
2. 退出后原厂拍照正常;
3. 面板开着时按物理电源键能关机(验证 1.3 候选;若仍被吞,改 `panel_input.h` 转发为 XTest 或调整捕获键集合,另行评审);
4. 正常关机;
5. 连接失败时读卡 `$STATE/service.state` 与 `/api/v1/network/status` 的 `http_address`,确认监听地址与卡的部署变体匹配。

## 2. R1/R2/R3 实现

### R1 每款草稿按稳定 look_id 隔离(`mod/metol/nxui.c`、`nxui_lut_panel.h`)

- 新增 `lut_look_drafts[]`(≤64 槽,仅存 tune[7]+强度+key 的小草稿,不为全部 LUT 常驻)、当前款持久化基线 `lut_base_tune/strength`。浏览选中(selected)、每款草稿、已持久化配置(基线)、最近成功提交(`lut_origin_*`,现语义=机身已接受的请求)四者分离。
- `lut_select_look()`(choose 的 lut 路径):换款先 stash 出款缓冲(等于基线则清除草稿),入款优先恢复自家草稿,否则读盘;持久化态单次读取兼作基线(SD I/O 不翻倍)。浏览零提交。草稿恢复/全部放弃打 `LUT_DRAFT_RESTORE` / `LUT_DRAFT_DISCARD_ALL` 日志。
- `lut_has_changes()` = 当前款脏或存在任一草稿;Fn 从微调回列表只确认**当前款**(`lut_current_dirty()`);离开对话框三个选项作用域明确并在文案中说明:保存并退出=全部草稿原子写卡+提交当前显示款;放弃=清全部草稿+当前款从盘重载;继续编辑。S1 半按抢占显式清全部草稿(沿既有语义)。
- MFW 菜单↔LUT 面板往返(重开 `load_lut_choices`)保留内存草稿并优先于会话值。

### R2 原子持久化(`mod/metol/nx_atomic_write.h` 新增、`lut_tune.h`、`lut_tune_cache.h`、`nxui_lut_panel.h`)

- 新增共用原子写辅助:同目录临时文件(O_EXCL|O_NOFOLLOW,pid+序号防冲突,4 次重试)→ 循环写(短写/EINTR)→ fsync/close → rename → fsync 目录;rename 前失败清理临时文件且旧文件不动;rename 后目录同步失败返回 -1(已发布未确认,不谎称回滚)。
- `nx_tune_snprint()` 加入 `lut_tune.h`,`nx_tune_print()` 改为复用它(单一格式来源,字节不变)。`nx_tune_save()` 重写为原子写,返回 1/0/-1;引擎侧 `PERSIST-WARNING`(未写入)与 `PERSIST-UNCONFIRMED`(已写入未确认)分层报告。仍写 `nx_lut_data_root()` 最终 SD 数据根。
- `lut_toggle_favorite()`:先构造候选列表原子写卡,成功才翻转内存收藏并重建分组;失败保留可重试旧状态,不再先改内存后写文件。

### R3 提交请求不可变快照(`nxui.c`)

- 新增 `struct nx_lut_request{key,tune[7],strength,exit_after,is_off,generation}`,确认时(`confirm_lut`)冻结 pending,派发时(`start_child` fork 前)拷贝为 inflight;`exec_lut_engine` 的 argv 只取 inflight,完成回写只认可 inflight(基线/origin/收藏按该 key 更新,退出意图取 inflight)。派发前校验队列索引与请求 key 一致,不一致拒绝执行。
- 在途复用仅限**参数完全相同**的请求(同款不同强度/微调=不同任务,排队等在途完成);同参数重复确认只更新退出意图。
- 忙碌处理统一:choose/lut_step/lut_set_category/lut_adjust/change_lut_strength/触屏滑条/关闭 LUT 按钮/重置/收藏全部加 `queue.commit_requested` 保护(原来仅部分路径),确认→fork 窗口与 child 运行期间行为一致。
- lut 提交失败:保留全部草稿、`queue.desired=-1` 丢弃自动重派,仅显式重试;关闭 LUT 是明确的 `__off`/disable 请求,完成回写不会被旧回调改写。
- `LUT_CHOICE confirm`/`LUT_SUBMIT` 日志现在带完整参数(strength、tune[7]、generation),满足"检查后端完整 argv 与回写"。

## 3. 构建与回归证据(全部真实执行,报告可查)

| 项 | 命令/入口 | 结果 | 报告 |
| --- | --- | --- | --- |
| 完整构建 | `D:/conda/envs/nx300m-mod/python.exe -X utf8 tools/build_metol.py` | nxui/lut_engine/recipe_engine 全过,68 配方 | `outputs/metol/*`,lut-build-report.json |
| 引擎调参/持久化 | `tools/probe_lut_tune.py` | 通过(含 `.state/lut-tune-*` 精确内容断言) | `outputs/review/menu-toggle-width/tuning-arm/` |
| 选择/导航/收藏全场景 | `tools/probe_lut_selection_vm.py --output-dir outputs/review/2026-11-04-night/lut-selection-final` | passed=true,候选 SHA=最终 SHA | 同目录 `report.json` |
| R1/R3 状态机(新增 probe) | `tools/probe_lut_draft_vm.py --output-dir outputs/review/2026-11-04-night/lut-draft-r1r3-final` | 9/9 通过:草稿往返/两款不串/浏览零提交/保存并退出全部写卡/重开状态/确认后突发输入参数冻结/同款参数分次任务/失败保留可重试/关闭=disable | 同目录 `report.json` |

模拟器边界:以上为真实 ARM(QEMU)+X11+action recorder 的软件行为回归;ISP 替身,不代表白 FPS/曝光/校色/功耗,更不宣称物理关机修复。

### Probe 基础设施修复(必要,非生产行为变更)

- `probe_fn_panel_vm.py` / `probe_menu_entry_vm.py` 源码装配清单补 `lut_tune.h`、`lut_tune_cache.h`、`nx_atomic_write.h`(及 entry 的 `lut_ram_blob_build.h`)——自调参功能合入后该清单即缺失,旧证据 `fn-entry/custom-key-run05` 本身 passed=False,属既有缺口。
- 既有失败(未修,非本轮引入):`probe_fn_panel_vm` 运行期失败(与 run05 同症状);`probe_menu_entry_vm` 期望 daemon 消息 `LUT 适配器`,该串在现源码/部署脚本中已不存在(grep 证实),属 probe 与源码漂移。两者修复需先确认现行为契约,未在本轮擅自改期望。

### 性能自查(E 的最小执行)

- 发现并消除 `lut_select_look` 对 `.state/lut-tune-*` 的双读(基线与恢复合并为单次读盘);草稿仓为 ≤64 槽线性扫描,远小于既有表 I/O;每帧路径零新增工作。R4/R5 未动(按交接,先量后改)。

## 3b. D·拨盘锁/模式动画/Auto ISO 的机内接线与持久化(第二轮完成)

原则:**复用既有已验证组件,不新造桥**。`runtime/camera_firmware_mod.c` 的请求/响应桥(操作 4=拨盘锁、5=动画、7=ISO 锁(仅 0/6400)、1=软件切档,响应 26 字段)与 `mod/deployment/camera_control.sh`(读回校验的桥客户端,P=2/A=3/S=4/M=5 有实机读回证据 `outputs/firmware-runtime/firmware-mod-runtime/mode-stage-results.json`)原样使用,本轮只做接线。

### 改动

1. **UI 页面**(`mod/metol/nxui_feature_pages.h`):`feature_snapshot` 扩展解析响应 values[3]/[13]/[14]/[16](当前模式/拨盘锁/动画/ISO 锁),未读到=−1 显示"未读回",不伪造默认;新增 `FW_STATE` 诊断日志。设置页(5):"顶部模式拨盘锁定/模式切换动画/Auto ISO 6400 上限锁定/软件切档(当前模式读回+点按 P→A→S→M 循环,SMART/BULB/iFn/Wi-Fi 不劫持)"四行接真动作,替换原 unavailable 占位;曝光页(4):Auto ISO 6400 接真动作,12800/25600 保持不可用并如实标注"控制桥仅开放 6400"/"未实机验证"。`feature_action` 的切换值一律来自桥读回状态而非按钮标签;`mfw_readonly`(截图/演示)拒绝执行。
2. **动作白名单与刷新**(`nxui.c`):`mfw_action_allowed` 增加具体 fw-* 请求;`poll_child` 成功分支检测 `FW_MOD` 输出后用最新响应重建页面 4/5(保留结果文本)——"成功后才更新 UI 的已保存状态"。
3. **动作分发**(`mod/deployment/mfw/panel-actions.sh`,打包为 mfw-panel-actions.sh):新增 `fw-mode-lock-on/off、fw-animation-0/1/2、fw-auto-iso-0/6400、fw-mode-P/A/S/M`,调用同目录 `camera_control.sh`(位置不存在则明确失败),成功后解码当前模式并原子写偏好记录(tmp+mv)到 `NX300M_MOD_SOURCE_ROOT/.state/`:`fw-mode-lock`、`fw-animation`、`fw-auto-iso-lock`。固件控制不占媒体锁(置于媒体锁块之前)。
4. **开机恢复**(新增 `mod/deployment/firmware-mod-restore.sh`):由 `mod/deployment/app-loader/preload-persistent.sh` 在相机 exec 前以后台子进程启动(清空 LD_PRELOAD、断开 stdin,exec 不等待);有界等待桥 ready(≤120s),仅重放非默认偏好(锁=1、动画=1/2、ISO=6400),一次性执行后退出并记 `.state/fw-restore.log`。文档原"运行时控制目前不跨关机保存"由本机制补齐;休眠快照路径不动。
5. **打包**:`build_sd_bundle.py` SCRIPTS 增加 `firmware-mod-restore.sh`;`build_mfw_services.py` bundle 增加 `camera_control.sh`。

### 回归证据

新增 `tools/probe_fw_controls_vm.py`(真实 ARM nxui + X11 + fixture 桥:guest `/usr/bin/di-camera-app-nx300` 位置放满足 `feature_ready` 精确身份校验(exe 路径+maps 含 camera_firmware_mod.so)的替身进程,按 26 字段契约应答):报告 `outputs/review/2026-11-04-night/fw-controls-final/report.json`,10/10 通过——锁开/关、ISO 锁开/关、动画 0→1→2 循环、软件切 P→A 且原厂读回 A、请求 op4/5/7/1 内容正确、重启后状态从响应文件恢复、恢复脚本重放非默认偏好且默认偏好零请求、最终偏好=0/0/0。修复期间纠正的 probe 自身问题:fixture 映射路径须含 `/camera_firmware_mod.so` 子串、feature 页 Down 单步进 row、autostart 类动作不触发页面重建、两处断言笔误——均为测试侧,生产代码未因此回调。

回归对齐:最终二进制 SHA `b5ce6098…`,`probe_lut_draft_vm`(R1/R3 状态机)在同一二进制复跑通过(`outputs/review/2026-11-04-night/lut-draft-after-d/report.json`)。

### 边界与剩余

- A/P 自动快门范围(shutter_limit 模式限定)未动——需要 runtime 模式判定与原厂单位确认,按交接默认顺序留在 D 之后;曝光页两行仍如实显示"尚未接入"。
- 泛绿、EXIF、无线 P0 实机项不变(见 §1.3/§4)。
- 实机验收(待部署时):设置页四行在实机 loaded/matched 掩码下操作一次,核对 FW_STATE 日志、响应读回与偏好文件;重启后确认恢复生效且 `fw-restore.log` 有 restored 记录。

## 4. 剩余项与建议起步点


1. **D·功能接线**:拨盘锁/模式动画/Auto ISO 6400/软件切档已完成(见 §3b);剩余 A/P 自动快门区间(runtime 模式限定)与 Auto ISO 12800/25600(桥未开放,需扩展 `configure_iso_lock` 合法值并实机验证)。
2. **P0 实机**:按 §1.3 清单;PowerOff 转发候选修复方案已写明(改 XTest 前先实机确认现象)。
3. **probe 漂移**:确认现 daemon 的 lut 交接日志契约后,更新 `probe_menu_entry_vm`/`probe_fn_panel_vm` 期望并恢复为可用回归。
4. **泛绿**:保持禁用,待实体取证。
5. 写卡/发布:本轮不做;若下轮发布,保留 preview.2 及其证明,新版本走既有 sanitize/export 流程。

## 5. 边界遵守情况

未触碰:RAW、原厂 Reset/Still IQ/拍照仲裁、LCD DMA、未知寄存器、IPC 整体替换、Shim 合并、Session 代码、既有正常硬件预览路径、`outputs/github-source` 公开副本。菜单交互(第 5 节)全部保留,仅离开对话框文案更明确。Python 全程 conda `-X utf8`,禁 venv。
