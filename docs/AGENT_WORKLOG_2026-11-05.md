# 2026-11-05 实施与 AGY 监督记录

权威目录内迭代，保留11月4日Zcode修改。此前备份和本轮调用记录：`outputs/review/2026-11-05-agy/`。未写实体卡、未推送GitHub、未更新Release。

## 完成

- Gemini 3.8 Flash High实际修改两个Shell文件：显式保存的拨盘锁/动画/ISO值（包括0）按合法范围在开机重放；固件控制已应用但偏好写入失败返回75并报告“已应用、未保存”。缺失配置仍跳过。
- 主线程补齐UI与草稿：保存失败留页；64槽满且写盘失败时保留全部草稿、当前编辑与分类，阻止不完整提交；rename后目录同步未确认保留durability_pending，即使读盘内容相同也不能误清；同值保存也执行真实原子写以确认重试。控制已应用未保存时刷新读回状态，按同一项重试相同值，不反向切换。
- 新增“胶片模拟”分类，原有分类编号保留；六款Metol胶片配方进入常用目录，柯达及CineStill条目也按名称归组，按需读取单款表。
- 统一简化122个LUT显示名，保留ID、源cube和原生LUT表；去CR/LR/Metol及冗余描述，避免重名。更名前后清单：`look-name-changes.json`。可重复运行 `tools/simplify_look_names.py`。当前完整显示目录：`catalog/looks.txt`（144项，含原图微调）。既有zip包不覆盖。

## 验证

- 受影响ARM UI及引擎构建退出0；最后分类修改后菜单构建退出0。
- 私有ARM VM设置链路报告 `fw-controls/report.json` passed=true，含“控制已应用、写卡失败、重试同值成功”和显式动画0恢复；原厂控制桥是替身，不证明物理拨盘或ISO行为。报告记录了实际被测SHA；之后新增分类，最终二进制SHA见result.json，不把旧SHA回归冒充为最终全套实测。
- `draft-failure.c` 包含实际nxui实现，在ARM用户态故障注入验证保存失败留页、满表不丢编辑/不提交/不换分类、目录fsync失败跨浏览保留、重试同步成功后清除、胶片分类按条目归组。`draft-failure.log` 为ALL_PASSED。只模拟目录fsync失败，不替代实体断电耐久测试。
- 名称更新前检测唯一性，生成目录正常；只改显示元数据，不触发重新编译LUT。

## AGY 调用结果与限制

不是只读/审批权限阻止：配置为always-proceed，随后实际Shell编辑成功。前两次大任务仅反复读取/分析，分别由主线程终止；无线只读任务10分钟DEADLINE_EXCEEDED且未交付报告。未把这些调用计作完成。最后限定两文件后约两分钟完成，具体责任见shell-result.md；其余C逻辑与验证由主线程完成。

## 未解决项

无线“手机直接连不上、机身进入界面不能关机”尚未定位完整根因，不能标修复。主线程核查见 `wireless/parent-result.md`。连接页只是network-info；daemon ready不证明WLAN成功。保留现有Session/Reset/Still IQ与拍照仲裁，没有新增实机并发测试。外层15/20s杀shell与内层30s恢复预算不一致的风险已确认、未变更，后续应建立安全完成协议，禁止简单杀进程组/恢复tracer。

此前交接的A/P快门范围、Auto ISO 12800/25600菜单接线、泛绿实机修复、拍摄镜头EXIF自动关联仍未因本轮而完成，继续以11月4日交接任务和实际代码为准。

## 下午补充 Review 与离线交接

用户要求先跑部分离线测试、保留日志给晚间Agent，期间不上机。新增接手入口：[`AGENT_HANDOFF_2026-11-05.md`](AGENT_HANDOFF_2026-11-05.md)，包含约180分钟任务估时、精确命令、报告判据、现有/未完成需求和实机边界。

- 新增 `tools/probe_lut_review_state_arm.py`：实际ARM UI完成逻辑、有限子进程返回替身，五项检查。三个暴露缺陷：直接保存退出遗漏其他款草稿、忽略PERSIST-UNCONFIRMED、前置诊断导致exit75同值重试漏记；PERSIST-WARNING与退出1两个对照符合预期。没有运行实际ISP或LUT引擎事务。
- 新增 `tools/probe_fw_persistence_vm.py`：无网络私有ARM VM，用原厂Shell和实际panel-actions/common，控制桥仅固定响应。三项检查：正常保存及临时文件写入失败的exit75符合预期；正式配置目标为目录时仍exit0误报成功。真实Shell诊断会出现在FW_MOD标记之前，支持上条缺陷的触发路径。
- 两组共八项检查，四项暴露缺陷；报告均为 `diagnostic_completed=true, contracts_passed=false`，不是产品已通过。VM已退出，未写卡、未连接实体相机、未推送或发布。
- 首次ARM harness编译因POSIX200809下usleep未声明退出1；改为nanosleep后执行完成。后续只将模拟引擎写入的fixture与UI草稿槽位解耦，便于修复提前flush后仍使用相同契约；各次日志保留。
- 静态补查：保存失败提示可能被弹窗遮挡、收藏目录同步未确认没有可见同值重试；超时/恢复所有者与锁继承协议仍待修，不能认定它就是实体无线卡死根因。

证据根：`outputs/review/2026-11-05-handoff/`；baseline源码备份与manifest在同目录。本轮仅创建诊断工具和文档，没有顺手修掉待交接的生产缺陷。
