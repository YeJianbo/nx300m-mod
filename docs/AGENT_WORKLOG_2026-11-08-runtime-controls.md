# 2026-11-08 ZCode 免刷机原厂增强接入工作记录

执行:ZCode(Start Plan / GLM-5.3-Flash / high)。任务:`docs/ZCODE_TASK_2026-11-08.md`。基于 `docs/RUNTIME_CONTROL_INTERFACES.md` 已逆向的接口,实际实现免刷机执行入口 blob/安装器、取景 Custom 键 handler、电源计时调度。未刷固件、未写卡、未上机、未发布。

> **本文档分两段。** 第一段是 11-08 上午的首轮交付记录,**保留原文不改写**。第二段是同日针对 `docs/ACCEPTANCE_2026-11-08-ZCODE.md` 的 B1–B6 续修记录。验收结论以验收报告为准,本文不改写它。

---

# 第一部分:首轮交付(11-08 上午,已被验收判定不通过)

## 1. Ecore select 槽引导 + 请求文件通道(blob)

`runtime/settings_runtime_blob.c` — freestanding clang blob(8192 字节,无重定位):

- **select wrapper**(`nx_ecore_select_wrapper`):Ecore 内部等待 `0xc88c` 通过槽 `0x21bb4` 间接调用 select 回调。安装器读取运行时原值(不能当磁盘零值),写入 config.orig_select,槽改为 wrapper 地址。wrapper 按原 select ABI 传参、保留返回值与 errno(纯 syscall 不写 errno 全局)。首次调用写 bootstrap 标记文件(证明自然线程执行)。
- **请求文件轮询**:`/var/run/nx300m-settings-request`(stamp 比对,无更改即跳过);操作:`pt 1/0` = UI_Set_Power_Timer(true=停 0x28/0x29/0x2c,false=按原厂偏好重启);`ck 0/1` = custom key 启停;`ck-action N` = 启动边沿设置。45 秒无活动自动调 false(恢复原厂息屏)。
- **custom-key handler**(`nx_settings_custom_handler`):写入 key_table[12] 槽(`0x2615c0`,.rodata mprotect)。stock 原始 handler(`0x17c748`)始终调用(保留原厂语义);MOD 面板 fork+exec 为增量,带 2s 防抖与 action 边沿(config 可调,device verify 可调)。

## 2. ptrace 安装器(loader)

`runtime/settings_runtime.c` — 基于 settings_animation.c 模式的 ptrace 安装器:

- **production 模式**:exe=`/usr/bin/di-camera-app-nx300` + 全 stock 字节核验 + libecore/libc SHA 校验;slot 值读取(原始 select 或 glib wrapper);key table slot 必须等于 `0x17c748`(reversed contract)。
- **fixture 模式**(VM only):env `NX_SETTINGS_RUNTIME_FIXTURE=1` + `/perf/settings-fixture.json`。
- **安装流程**:remote mmap(3=RW)→ poke blob → cacheflush → mprotect(rx)→ key table mprotect(RW)→ poke wrapper → mprotect(ro)→ select slot poke。卸载恢复原始 slot 值 + 原始 key handler。
- **重复安装幂等**:region 已存在 → 更新 custom_enabled 即可。

## 3. 构建脚本

`tools/build_settings_runtime.py`:stock 地址核验(UI_Set_Power_Timer=0x1d0f88、key slot 0x2615c0=0x17c748、Ecore select_set=0xdb30/select_get=0xdb4c、slot offset 0x21bb4 三路一致);不符即中止。blob 用 clang freestanding;loader 用交叉 gcc + crt0_arm.S。

## 4. 首轮 VM 验证:失败

首轮报告把失败归因于「`/proc/<pid>/exe` readlink 在 VM tmpfs 环境返回空」。**该归因不成立**:原始日志里 readlink 对 `/perf/settings_runtime.arm`、`/rescue/busybox` 等均返回了正常路径。真实原因是夹具把函数代码地址当函数指针解引用(见第二部分 B6)。

## 5. Auto ISO 菜单接线(当时状态)

25600 已接(11-07):曝光页「Auto ISO 扩展上限」行、camera_control auto-iso 桥、偏好重放。12800/25600 的 IQ 参数 37 直写路径未接入 blob。

## 6. A/P 快门区间、CS 泛绿(当时状态)

均为逆向资料与方案,未实现。详见 `RUNTIME_CONTROL_INTERFACES.md` §5/§6。

---

# 第二部分:B1–B6 续修(11-08 晚间,实测)

## 7. B1 — 远程执行线程与应用身份

- `main_thread` 不再是 NULL:在 `attach_all` 之后按 `stops[i].tid == pid` 从实际停止集合选出;选不到就带阶段名拒绝。
- libc **两种模式都打开并校验**:`image_open(pid,"libc-2.13.so",STOCK_LIBC_SIZE,STOCK_LIBC_SHA,...)`,回退 `libc.so.6`;随后 `code_check(mem, libc.bias+STOCK_SVC, stock_svc)` 比对 32 字节 svc 指令。
- `app_bytes()` / `settings_checks` 在 `inspect()` 内**运行期执行**(verifying mapped inode/dev 属于目标可执行文件),不再是构建期表格。
- 身份未删除:production 仍要求 exe 路径 + `file_hash(STOCK_APP_SIZE, STOCK_APP_SHA)`。

## 8. B2 — 地址重定位、配置、权限

- 所有槽位写入统一为 `region + symbol`:wrapper=`region+0`、custom handler=`region+BLOB_NX_SETTINGS_CUSTOM_HANDLER`(本轮构建为 1168)、config=`region+BLOB_NX_SETTINGS_CONFIG`(4096)。`CFG(field)` 宏 = `region + BLOB_NX_SETTINGS_CONFIG + offsetof(...)`,不再漏基址。
- 配置**完整**从本地结构复制一次 `poke` 写入:magic/abi/bootstrapped/select_slot/orig_select/wrapper_addr/pt_func/key_entry/orig_key_handler/custom_enabled/launch_action/last_launch/launch_cmd/request_path/bootstrap_marker/seen 全部初始化;production 的 `launch_cmd` 写 `/mnt/mmc/nx300m-mod/panel.sh settings`。
- `cacheflush`(__ARM_NR_cacheflush = 0xf0002,region → __data_start)**实际调用**,之后 mprotect 代码区为 RX(5);key 表所在页临时 RW(3) → 写入 → 恢复 RX(5),每步 errno 检查。
- 每个槽位写入后 `match()` 从目标内存读回核对。

## 9. B3 — syscall 寄存器约束

原来用普通 `"r"` 操作数,编译器把参数放到 r6、flags 一度进 r7,随后 r7 被 syscall 号覆盖;返回后却比较 r6。改为显式绑定:

```c
static inline int sys3(unsigned nr, unsigned a, unsigned b, unsigned c) {
    register unsigned r0 __asm__("r0") = a;
    register unsigned r1 __asm__("r1") = b;
    register unsigned r2 __asm__("r2") = c;
    register unsigned r7 __asm__("r7") = nr;
    __asm__ volatile("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7) : "memory");
    return (int)r0;
}
```

另修一个只有实测才暴露的问题:guest 内核未启用 `CONFIG_COMPAT_32BIT_TIME`,旧 `clock_gettime`(263)返回 ENOSYS,导致所有时间戳为 0、防抖与 watchdog 静默失效。现在优先 `clock_gettime64`(403),失败回退 263。修复前 `custom_key_launch_once` 恒失败,修复后通过。

## 10. B4 — 重复安装 / 关闭 / 恢复 / 唤醒

- **已安装识别**:扫描匿名 `rw-p` 映射,按 4096 步进找含本 magic/abi/select_slot 的配置页(考虑映射被合并),**并且**要求 select 槽仍指向 `wrapper_addr`。第二条是实测补上的:卸载只还原槽位、映射仍在,旧逻辑会把「卸载后再装」误判为幂等更新。
- **卸载**:先恢复 select 槽、再恢复 key 槽,然后**从目标内存回读**两个槽并与记录的原值比对,打印 `restored select=%08x key=%08x`;任一不符返回 5。
- **patch_attempted** 在第一次实际写(改 key 表页权限)之前置位;失败路径同时恢复两个槽,任一恢复不完整则置 `transaction_poisoned`,最终返回 5 且**不打印成功行**。
- **阻塞唤醒**:watchdog 激活时把 select 的超时收敛——调用方传 NULL 就换成剩余秒数的本地 `timeval`,传的值比剩余更久也换成剩余;调用返回后把实际使用的时间写回调用方结构。VM 实测:NULL 等待被换成 `tv 43` 秒(45s 余量),证明被阻塞的原厂 select 能按时醒来执行 watchdog。

## 11. B5 — 请求过期语义

- `changed` 由**长度 + 逐字节**比较得出(旧代码固定初始化为 1,遗留 `pt 1` 会无限续期)。VM 实测连续两次写入相同 `pt 1`,`pt-calls` 只有一次 `1`。
- `ck-action N` 移出 `ck ` 分支,改为独立条件 `body[2]=='-'` 且完整匹配 `ck-action`,数字从第 9 字节向后跳过空白解析——该分支此前因第三个字符是 `-` 而不可达。

## 12. B6 — 夹具与探针本身

验收指出的六处全部修正:

| 缺陷 | 修正 |
| --- | --- |
| `slot = &orig_select` 再解引用机器字 | 改为可修改函数指针**变量** `static int (*select_slot)(...)=orig_select;`,manifest 发布 `&select_slot` |
| 事件 `{12,1,0}`,action 应在 event[2] | 改为 `{12,0,1}` / `{12,0,2}`,与 handler 的 `((unsigned*)event)[2]` 一致 |
| bootstrap 读 `fixture-ready`,实际 marker 是 `settings-bootstrap` | 改为按 loader 输出行 ` pid 1 1 ...` 与 blob 写的 bootstrap 标记双重判断 |
| `passed` 把初始 False 纳入自身 | 聚合只取布尔项并排除 `passed` / `physical_camera_verified` / `camera_program_running` |
| `collect()` 用菜单模块默认 socket | 本探针自带 collect,走自己的私有 socket;文件缺失或为空视为错误而非空产物 |
| Windows 分支忽略 `--output-dir`、读旧二进制 | 尊重 `--output-dir`,夹具由探针从 `FIXTURE_C` 现场构建并在同一目录运行 |
| shell 只等 done,不核对卸载槽 | loader 侧回读两个槽并与夹具记录的 `fixture-orig` 逐字符比对 |

另有两处只有跑起来才暴露的夹具问题:

1. **夹具在安装后崩溃**:loader 把 key 槽页 mprotect 成 RX,而夹具把 key 表和自己的计数器放在同一个可写页。改为 key 表独占 4096 对齐页(与原厂只读数据页语义一致)。
2. **blob 的 execve 恒返回 EFAULT(14)** 而夹具自身同样的调用成功。原因是 `static char *av[]={"sh",...}` 这类带指针初值的静态数组,链接器把**链接期绝对地址**写进 `.data`;blob 被复制到任意 mmap 区且不经过重定位,这些字指向进程外。判别证据:同一 blob 用不存在的路径 `/nonexistent-nx` 调用返回 ENOENT(2),证明 path 参数正确、只有 argv/envp 数组错。改为运行时逐元素赋值(指向 pc 相对字符串)后通过。

## 13. VM 实测结果

命令:

```
python -X utf8 tools/probe_settings_runtime_vm.py \
  --output-dir outputs/review/2026-11-08-runtime-controls/settings-runtime-vm-final6
```

证据目录:`outputs/review/2026-11-08-runtime-controls/settings-runtime-vm-final6/`
`report.json` → `passed: true`,`physical_camera_verified: false`,`camera_program_running: false`。

| 断言 | 结果 | 依据 |
| --- | --- | --- |
| 夹具存活 | 通过 | `fixture-ready` |
| 自然线程 bootstrap | 通过 | `install.log`: `162 1 1 region=b6f71000 key=00032030` |
| select 参数透传 | 通过 | `fixture-50-selects` = `ok`(调用方 5s 超时原样到达) |
| select 返回值透传 | 通过 | `abi-ok` 存在、`abi-bad` 为空 |
| 电源计时双向 | 通过 | `pt-calls` = `1\n0\n` |
| 相同请求不续期 | 通过 | `pt-calls` 中 `1` 仅一次 |
| Custom 键按下触发一次 | 通过 | `custom-launch` = `ok`(释放与 `ck 0` 后均不触发) |
| 原厂 handler 保留 | 通过 | `orig-key-calls` = `kkk`(三次全部转发) |
| 重复安装幂等 | 通过 | `REINSTALL_RC=0` |
| 卸载成功 | 通过 | `REMOVE_RC=0` |
| 槽位回读一致 | 通过 | `restored select=00010a18 key=000109dc` == `fixture-orig` |
| 阻塞唤醒 clamp | 通过 | `select-timeouts` = `tv 43` |
| 安装失败回滚 | 通过 | `fault.log`: 注入点在 key 槽已写后,输出 `refused at select-slot-patch ... exit=3`,无成功行 |
| 循环存活 | 通过 | `done` = `ok` |

guest 退出码 0;`INSTALL_RC=0`、`REINSTALL_RC=0`、`FAULT_RC=3`、`REMOVE_RC=0`。

## 14. 本轮仍未完成(不默认启用,不写成已生效)

按验收给出的续修顺序,以下项尚未完成:

1. **取景 Custom 键接入真实 key 表与真实 UI 事件** — 入口与 handler 语义已在夹具验证,但未在 `di-camera-app` 的 `0x2615c0` 槽与真实事件结构上运行,也未接部署;回放删除不触发尚未覆盖。
2. **菜单活动 → UI_Set_Power_Timer 接线** — blob 侧 `pt` 通道与 45s watchdog 已验证;`mod/metol/nxui.c` 本轮未改动(哈希与首轮相同),发送端仍是 vconftool 备选。
3. **Auto ISO 12800/25600 的 IQ 参数 37 提交与读回** — 新 blob 不含 `SetParameter(@0x318bd0)`;菜单偏好不能替代 AE 生效证据。
4. **独立动画运行验证** — 本轮未新增运行证据。
5. **A/P 独立快门范围** — 仍为逆向资料(`0x2ce900`、`calcSvshiftForIsoAuto`、`+0xf64/+0xf66`、三域模式门禁)。
6. **CS PP 串行化与 EXIF 快照取证** — `0x34316c/0x343e74/0x343de4` 已定位,串行化时序未证明。
7. **启动重放与首装/升级资源契约** — launch 命令可执行性已验证,开机重放与升级资源检查未覆盖。

## 15. 构建与产物

- `python -X utf8 tools/build_settings_runtime.py` 退出 0。
- blob 8192 字节;offset: wrapper=0、custom handler=1168、config=4096、`__data_start`=4096。
- `settings_runtime.arm` SHA-256 = `a6ba43854d8fcdea383d52b88ac03785616c02bcf104e2e73828142879d0a2f0`
- `settings_runtime_blob.bin` SHA-256 = `cd0c1ce86fcd228028296a66be133e679d777e6c45428c4c58b94f8bf1371c98`
- 夹具 `fixture.arm` SHA-256 = `2c5ed18a64f82883b4a322608a8b49136f91c02fe0d536dd323484c813653d5d`

## 16. 边界遵守

原厂 Reset、Still IQ、Session、拍照仲裁、RAW、LCD DMA 全未修改。未刷固件、未写卡、未上机、未安装手机、未推送发布、未做视频 LUT、未复用旧 V0、未移除身份校验。仅使用指定模型与指定 Conda Python。

## 17. 最小后续上机步骤(前置条件:上述第 14 节 1–3 项先完成并离线通过)

1. 取景 Custom 键 → MOD 面板打开且一次;回放删除 → 不打开;MENU/OK/Fn 语义保留
2. 菜单活动 → 息屏不触发;退出面板 → 原厂超时正常息屏
3. Auto ISO 6400/12800/25600 → 设置 + 读回 + 开关机恢复
4. 关闭 LUT 卡死复测(11-07 候选已上卡)
