# 最快快门上限

已实现 NX300M 1.15 原厂 capture AE 的 **Tv 上界**钩子。默认关闭，启用后先执行真实原厂 getter，再返回 `min(原厂上界, 用户上界)`。没有修改最低快门偏好 `APPPREF_SHUTTER_SPEED_MIN_INDEX/AUTO`，也没有在机械快门或 PWM 阶段延长曝光。

产物为 `outputs/controls/shutter_limit.so`，源码 `runtime/shutter_limit.c`。隔离 ARM probe 已验证原厂 getter 转发、时间表转换、关闭恢复与非法枚举拒绝。普通会话挂接和完整 AE 求解消费需要由启动器整合后继续验证；没有据此声称手动、特殊模式或物理曝光已经覆盖。

## 原厂消费路径

以下地址均为核验原厂 `libiq-fw-slpcam.so` 的 ELF 虚拟地址，运行时要加实际装载基址。

1. `CAeInfoIF::calcTvMaxCapture()`，符号 `_ZN9CAeInfoIF16calcTvMaxCaptureEv`，地址 `0x2f8398`，大小 20。输入 `r0=this`，读取 signed short `[this+0xc8]`，字段为 0 返回 `0xc8`（200），非零返回 `0xd0`（208）。该标志的业务含义尚未确定，模块保留原厂分支。
2. `CAeInfoIF::AeUpdateProgramLineInfo()`，地址 `0x2fa4a8`。`0x2fa4e8` 经 PLT 调用上述 getter，随后将返回值写入 `CAeInfoIF+0x48` 和 `SAePLInfo+4`。`AeProgramLineInfo()` 地址 `0x302c5c` 返回 `CAeProgramLine+4`，因此同一上界也在 ProgramLine 对象 `+8`。
3. `CAeProgramLine::captureProgramLine_CommonCalculation1()`，地址 `0x2ffc44`。`0x2ffc4c` 从对象 `+8` 读取该值，`0x2ffc60` 写入计算状态 `+0xf64`。这一步位于曝光程序线求解之前。
4. A 模式 `calcProgramLine_Capture_AV()` 地址 `0x301dfc`，`0x301ed8` 读取 `+0xf64`，`0x301efc` 将其放入真实 `AeMathCalcProgramLine()` 的约束参数。P 模式 `calcProgramLine_CaptureProgram()` 地址 `0x302790`，`0x302850` 起读取同一上界，并用于后续程序线计算。
5. `CAeInfoIF::getAeValueTvalue_Capture()` 地址 `0x2f9bac`，取得原厂 capture 参数后，在 `0x2f9bdc` 调用 `IsOverLimitTvalue(int&, mode=1)`。普通分支在 `0x2f7c98` 读取 `CAeInfoIF+0x48`，将返回 Tv 夹到该上界。另有使用 `+0x4c` 的特殊分支，不能由普通分支推断所有模式。
6. `getAeValueShutterTime_Capture()` 地址 `0x2f9c0c`，调用上述 capture Tv getter，然后左移 4，调用原厂 `convTv256toUsec()` 转成微秒。

`AeUpdateSystemInfo()` 在 `0x2fa97c` 尾调用 `AeUpdateProgramLineInfo()`。现有 `disassemble_stock.py --callers` 只查 BL，因此会漏掉这条 B 尾调用。模块不从 Ecore 线程强行调用 AE 刷新函数，等待原厂自身的更新周期消费配置。

证据文件：`shutter-ae-limit-consumers.asm`、`shutter-programline-state.asm`、`shutter-programline-consumers.asm`、`shutter-ae-refresh.asm`，均位于 `outputs/controls`。这些文件证明静态调用和字段消费；还没有完整相机会话里的函数追踪。

## Tv16 与微秒映射

原厂 Tv 是每 EV 16 单位。`getAeValueShutterTime_Capture()` 与 `CAE::AeConvTvalueToUsec()` 都将该值左移 4，再交给 Tv256 转换函数。

`CAeSensorIF::AeChangeTvalue256ToUsec(int)` 地址 `0x2fadb4`，其原厂查表基址为 `0x363b08`，首项 `32000000`。转换行为为：

```text
adjusted = tv256 + 0x500
microseconds = table[adjusted & 255] >> (adjusted >> 8)
```

因此 Tv16=160 实际映射 976 µs，不能把它当成严格的 1/1000 秒。以下枚举把 `1/N` 当用户希望的最快上限，选择原厂表中满足 `微秒 >= ceil(1000000/N)` 的最高 Tv16，向较慢一侧取档：

| 枚举 | 用户目标 | 实际上界 Tv16 | 原厂转换 µs |
|---:|---|---:|---:|
| 0 | off | 原厂值 | 原厂值 |
| 1 | 1/60 | 94 | 17039 |
| 2 | 1/125 | 111 | 8158 |
| 3 | 1/250 | 127 | 4079 |
| 4 | 1/500 | 143 | 2039 |
| 5 | 1/1000 | 159 | 1019 |
| 6 | 1/2000 | 175 | 509 |
| 7 | 1/4000 | 191 | 254 |
| 8 | 1/6000 | 200 | 172 |
| 9 | 1/8000 | 207 | 127 |

若原厂上界本来更慢，仍保留原厂值。例如原厂返回 200 时选择 1/8000，结果仍为 200，不会扩大原厂允许的速度。完整表提取见 `shutter-tv-mapping.json`；隔离 probe 也直接执行原厂转换函数确认上述阈值，并检查下一 Tv16 已快于用户上限。

## 构建和接口

```powershell
python -X utf8 tools/build_shutter_limit.py
python -X utf8 tools/build_shutter_limit.py --probe
```

父线程启动器将共享库加入 LD_PRELOAD 后，运行时设置为：

```powershell
python -X utf8 tools/shutter_limit.py --fastest 1/1000
python -X utf8 tools/shutter_limit.py
python -X utf8 tools/shutter_limit.py --fastest off
```

`request(fastest=None)` 供统一服务导入使用。状态包含用户目标、实际 Tv16/µs、最后一次原厂输入和钩子输出、调用次数、实际夹取次数。`configured_choice_observed_at_getter` 仅表示该配置已在 getter 处被看到，不表示 AE 求解、屏幕显示或物理曝光验证完成。重启后回到 off。

导出的 C API 为 `nx_shutter_limit_configure(enum)` 和 `nx_shutter_limit_selection()`。非法枚举返回 `-102`，保持原配置。原始 getter 解析失败时明确终止，不虚构原厂上界。

模块的 `ecore_main_loop_begin` 经 RTLD_NEXT 链式转发，可与 ui_mod、exposure_controls 的同名挂接同时使用。只有 Ecore timer 添加成功后才写 `/var/run/nx300m-shutter-limit-ready`，内容为实际 guest PID；客户端要求 session 状态中的 `shutter_limit_ready`，避免未加载时等待超时。请求与响应使用该会话私有 `/var/run/nx300m-shutter-limit-{request,response}`，不改原厂永久偏好。

## 实际验证与边界

`shutter-limit-probe.json` 记录本轮 exit 0。probe 在独立 mount/net/ipc 命名空间中运行原厂 glibc 2.13 和未修改的 IQ 库；其 `/mod`、`/var/run` 为私有 tmpfs，没有改变共享相机会话。输入为明确构造的 CAeInfoIF 大小结构，只控制 getter 真正读取的 `+0xc8` 字段，不是传感器或实时 3A 状态。

20 组组合验证：两个原厂分支分别返回 200/208，9 个上限及 off 经实际 PLT 调用钩子，钩子通过 RTLD_NEXT 执行原厂 getter，最后再次 off 恢复原厂返回。程序使用真实原厂时间转换而非测试中的替代公式。

统一启动器已通过 `--shutter-limit` 加载模块，Ecore ready 和 HTTP 读取／设置实际完成。在正常原厂回放会话中设置 1/1000 后，原厂 getter 实际观察到配置，返回 Tv16=159，夹取次数增加；随后 off 恢复初始状态。证据为 `outputs/controls/session-controls-validation.json`。这证明共享会话的参数入口和真实 getter 调用链已接通。

尚未验证：普通 P/A 完整求解后的输出、拍摄元数据、实际快门动作，以及 S/M、闪光、HDR、连拍、Bulb 等路径。该模块不拦截 PWM、不写机械快门时间、不改 ISO 或光圈。后续需继续核对 capture Tv 消费，完整图像和实体曝光仍分别验证。

主页提供读取、目标选择、应用与关闭限制；HTTP 接口为 `GET /shutter-limit`、`POST /shutter-limit`，POST 例如 `{"fastest":"1/1000"}`。使用 `python tools/simulator.py --shutter-limit` 启用，可合并 `--exposure-controls` 与既有 SD 卡／回放选项。
