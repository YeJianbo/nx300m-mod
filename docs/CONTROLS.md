# 曝光控制功能

实时 ISO 显示已按用户修订取消。当前桥接原厂 ISO 步长、ISO 扩展和 Auto ISO 上限的 UserData 设置；自动曝光最快快门限制另由 [SHUTTER_LIMIT.md](SHUTTER_LIMIT.md) 所述模块提供。

## 原厂编码与证据

相机进程通过原厂 Ecore 线程调用 `CDataManagementService::GetUserData` / `SetUserData`。桥接层只接受已核实的值，不改固件文件：

| 控制项 | UserData ID | API 值 | 原厂编码 |
|---|---:|---|---|
| ISO 步长 | `0x37` | `one-third` / `one-stop` | `0x00370001` / `0x00370000` |
| ISO 扩展 | `0x38` | `false` / `true` | `0x00380000` / `0x00380001` |
| Auto ISO 上限 | `0x3b` | 125、160、200、250、320、400、500、640、800、1000、1250、1600、2000、2500、3200、6400 | `0x003b0000` 至 `0x003b000f`，按表中顺序递增 |

原厂 `convertUserData_ISOSTEP`、`convertUserData_ISOEXPANSION` 和 `convertUserData_ISOAUTOMAX` 指令分别保存在 [`userdata-iso-step.asm`](../outputs/metol/userdata-iso-step.asm)、[`userdata-iso-expansion.asm`](../outputs/metol/userdata-iso-expansion.asm) 与 [`userdata-iso-automax.asm`](../outputs/metol/userdata-iso-automax.asm)。Auto ISO 反向转换的 `CCapParamIf::convertParamValue` 也将索引 0–15 映射回表中的 ISO 数值，见 [`pw-conversion.asm`](../outputs/metol/pw-conversion.asm)。这核实了数据服务编码和值映射，不代表 AE 一定采用该设置。

## Auto ISO 扩展到 25600（2026-10-24）

原厂 Auto ISO 菜单最高为 **3200**。1.15 的 `UI_Set_Attr_Auto_ISO_Range`（`0x1b8160–0x1b821c`）只接受五项 200／400／800／1600／3200；`UI_Set_Attr_Auto_ISO_Range_1_3`（`0x1b8220–0x1b837c`）只接受十五项 125 至 3200。两个函数的身份由原厂错误日志中的函数名确认，最终均调用 `0x1bb164` 设置参数 `0x27`。上表包含原厂内部能编码的 6400，它没有出现在这些菜单选项中；手动 ISO 规格也不能作为 Auto ISO 菜单证据。

新增 12800、25600 两档 Mod 上限。它们不向原厂 UserData 填入未知索引：原厂偏好保持内部有效值 `0x003b000f`（6400），扩展数值通过原厂 `CIQDataRepeater::SetParameter(37, ISO)` 写入 IQ 上限。原厂 1.15 的 `GetISOMaxSetting` 在 `0x318334` 读参数偏移 `0x94`；`SetParameter` 在 `0x318bd0` 检查索引上限后写入参数数组。模块同时匹配这两个函数的完整指令，且 IQ 参数对象初始化后才开放两档。没有改 AE 测光函数、当前 ISO、Lock3A 或快门。

HTTP `controls.auto_iso_max` 增加：

- `supported_values`：当前桥可设置的数值数组；旧桥或 IQ 未初始化时只有原来的 16 档，扩展就绪时再包含 12800、25600。
- `extended_ready`：原厂函数匹配且 IQ 参数已初始化，表示扩展写入口可用；不表示当前拍摄模式允许使用该上限。当前模式有效上限看 `ae_policy.max_iso`，是否与请求一致看 `policy_matches_requested`。
- `stock_actual_raw`：原厂 UserData 的真实读回；扩展时仍为 `0x003b000f`。
- `value_source`：`stock_userdata` 或 `mod_iq_parameter`。扩展 `actual_raw` 为桥私有编码 `0x003b0010`／`0x003b0011`，不是原厂新增编码；`value_mapping_verified` 表示此桥编码可准确解码，仍须看 `ae_policy` 核对实际模式上限。

模块只对 IQ 的上限参数 37 做转交：扩展启用后，原厂重发 6400 时保留扩展；原厂写入其他上限则立即清除扩展。App／网页设回 6400 或更低也清除扩展。已有可选的“6400 防回弹”模块若阻止设置更低值，API 会报告真实读回不符，不伪造成功。IQ 对象丢失、重建或实际值不一致时，读取回落到原厂值并清除失效扩展。进程重启后扩展关闭。

`exposure_controls.so` 与原生遥控服务构建通过。隔离 ARM fixture 调用未经修改的原厂 AE 消费函数：12800→Sv192、25600→Sv208（AE Capture 模式 0–3，即普通 2D P／A／S／M），AE Capture 模式 4（普通 2D 沙滩／雪景）仍被原厂限制为 Sv128／ISO800；手动 ISO 参数始终保持 3200，原厂主动改 3200 能清除扩展，关闭扩展恢复内部 6400。针对“UserData 已是 6400、扩展为 25600、再设 6400 不发通知”的路径，桥在退出扩展时按真实原厂读回显式恢复 IQ；完整桥请求 fixture 已验证 25600→6400→12800→3200，IQ 分别读回目标值且手动 ISO 始终为 3200。证据为 `outputs/controls/auto-iso-policy-probe.json`；这是原厂策略消费者验证，尚未验证实机暗场测光、物理增益、睡眠恢复或完整拍摄。没有重启主模拟器、写入固件或运行旧曝光补丁。

## 启动桥接

实体 MFW 的独立服务不会把此模块加载到已经运行的原厂 app。本轮新增独立只读变体 `exposure_status.so`，用于后续受控启动时读取原厂对象；写请求被模块拒绝，配套 remote 也不开放可写 ISO 能力。离线包、启动限制和最小实机检查见 [MFW 只读曝光候选](MFW_EXPOSURE_TEST.md)。它尚未在实体加载，不代表 Auto ISO 扩展已可用。

在模拟器启动参数中启用 `--exposure-controls`；例如：

```powershell
python -X utf8 tools\simulator.py --exposure-controls
```

先运行 `python tools/build_runtime_shim.py` 构建模块；`tools/run_stock.py` 也接受相同开关。启动器把已构建的 `outputs/controls/exposure_controls.so` 加入原厂相机进程的 `LD_PRELOAD`。桥接成功挂到 Ecore 定时器后，在私有 `/var/run/nx300m-exposure-ready` 写入相机进程 PID；未预加载的会话不会报告 ready。

## Python API、网页与命令行

独立 Python API：

```python
from exposure_controls import read_controls, set_controls

state = read_controls()
updated = set_controls(
    iso_step="one-third",
    iso_expansion=True,
    auto_iso_max=1600,
)
```

`read_controls()` 读取三项；`set_controls()` 只设置非 `None` 参数。`iso_step` 只接受 `one-third` 或 `one-stop`，`iso_expansion` 必须是布尔值，`auto_iso_max` 必须是表中的整数 ISO 值。返回值含 `controls[name].value`、原始值、设置返回状态与通知路径状态。多个参数按顺序写入，不构成事务。

模拟器内置网页服务提供 `GET /exposure-controls` 与 `POST /exposure-controls`。POST JSON 示例：

```json
{"iso_step":"one-third","iso_expansion":true,"auto_iso_max":1600}
```

主页这两个路由现适配到同一个 ARM 服务的 `GET/POST /api/v1/exposure/iso`，没有另起 Python 后端处理设置。网页遥控和 Android 直接使用该原生接口；等待通过服务事件循环推进。`outputs/controls/native-controls-validation.json` 记录原生路径的三项写入、读回、恢复及快门夹取，均实际通过。部署和能力字段见 [REMOTE.md](REMOTE.md#机身曝光设置)。

命令行读取全部状态，或设置单项：

```powershell
python -X utf8 tools\exposure_controls.py
python -X utf8 tools\exposure_controls.py --iso-step one-third
python -X utf8 tools\exposure_controls.py --iso-expansion enabled
python -X utf8 tools\exposure_controls.py --auto-iso-max 3200
```

原有 `--iso-step` 命令仍返回旧字段，其他命令返回统一的 `controls` 映射。

## 验证边界

`tools/camera_firmware_mod.py` 现在复用上述曝光桥与 `shutter_limit.py`，仅向选定
`NX300M_SESSION_FILE` 对应的运行会话发请求，不再使用宿主 `/var/run` 或
`work/var_run` 作为替代。Auto ISO 设置返回原厂读回值，不强制伪造 getter。

可选模块需显式通过 `run_stock.py --program camera --firmware-mod` 加载，并沿用
该会话所需的显示、偏好、硬件替代参数。它使用独立的 firmware-mod RPC，
不会抢占成熟快门桥的请求文件；默认不改变测光或快门。软件切档的 eUI_MODE
枚举来自原厂表，只有实际读回目标模式才返回成功。首次直接调用 A/S/M 入口
返回 -109，随后补齐原厂要求的请求状态：`UI_Set_Value(0x3fc,1)` 与
`UI_Set_Value(0x3f2,target)`。第二次独立原厂会话实际读回
P(2)→A(3)→M(5)→P(2)，记录为
`outputs/firmware-runtime/firmware-mod-runtime/mode-stage-results.json`；没有写
当前模式项 `0x3f0` 制造成功。原厂配置读回 3200→6400→3200、快门
off→1/1000→off 已通过，记录为同目录 `runtime-results.json`。
这次快门验证覆盖配置读回，没有新增完整曝光或像素证据。

物理模式拨盘过滤、模式动画、Custom 设置入口和 Auto ISO 6400 防回弹已接入经过
完整原厂函数字节核验的运行时入口。独立原厂会话已验证物理事件拦截、锁定时软件
切档、动画瞬切与隐藏、ISO 3200 写入被保留为 6400，以及 Custom 启动真实 ARM
设置面板；最终记录见
`outputs/firmware-runtime/firmware-mod-panel-live/advanced-control-results.json`。
该记录还验证设置菜单真实开关物理拨盘锁，以及半按 S1 后退出。实现与部署条件见
[FIRMWARE_ENTRY_CONTROLS.md](FIRMWARE_ENTRY_CONTROLS.md)。QEMU user 会话的 Custom
面板需要显式完整部署及解释器桥；正常相机 ARM 环境直接使用安装文件。设置目前
不跨关机保存，实体按键、电气状态和曝光尚未实机验证。菜单中的指令拨轮锁使用既有
`ui_mod`。实时 ISO 已从模块、菜单和包中移除。构建探针只报告 ELF 导出，
`test_firmware_mod_complete.py` 保留为只读兼容入口，不再向展开的 rootfs 写标记。

统一查看器已加载桥接库，三项设置通过网页 HTTP 接口实际写入并由原厂 `GetUserData` 读回：1/3 EV→1 EV、扩展开启→关闭、Auto ISO 上限 3200→6400，随后全部恢复初值并再次核对。证据为 `outputs/controls/session-controls-validation.json`。设置结果报告原厂 `SetUserData` 的返回值、`GetUserData` 读回和是否进入值变化的通知路径。下面补充了 Auto ISO 上限进入原厂 AE 策略的核验；暗场测光、物理传感器增益与成像效果仍未验证。这些状态不会跨模拟器重启保留。

### Auto ISO 上限与当前曝光 ISO 的快照（2026-10-23）

设置上限6400不会要求当前曝光一定使用6400。为区分手动ISO、自动ISO当前值和通知未到达，曝光 GET／POST 现在返回一次性 `ae_policy` 快照，不增加实时ISO轮询或自动调节功能：

| 字段 | 来源及含义 |
|---|---|
| `iso_mode`／`iso_setting` | 原厂 `CIQDataRepeater::GetISOSetting()`；0表示自动，正数为手动ISO，未就绪为unknown／null |
| `current_iso`／`current_sv16` | 原厂 `CAeInfoIF::getAeValueSvalue_Current()`；ISO由原厂离散Sv16编码映射，未知编码为null |
| `iq_max_iso` | 原厂IQ缓存 `GetISOMaxSetting()`，用于确认通知已到参数消费者 |
| `max_iso`／`max_sv16` | 原厂 `getAeSysInfoISOMaxSetting()`，包含原厂模式限制后的有效AE策略上限 |
| `requested_max_iso` | 本次读取／设置的用户上限；普通档来自原厂 UserData，12800／25600 来自桥的有效扩展选择，并非写入原厂偏好表的新档位。本次请求未读写此项则为 null |
| `shooting_mode`／`iq_drive_mode` | 两者为同一原厂 `CIQDataRepeater::GetShootingMode()` 原始值。该 getter 读取 IQ 0x45，实际由原厂 DRIVE 设置提供；前者是兼容旧消费者保留的历史误名，不能解释成 P／A／S／M |
| `ae_capture_mode` | 原厂 `CAeInfoIF::getAeSysInfoCaptureMode()` 转换后的 AE Capture 枚举；新桥追加返回，旧桥为 null。普通 2D 的 P／A／S／M 分别为 0／1／2／3；4 对应普通 2D 沙滩／雪景 |
| `policy_matches_requested` | IQ上限和AE有效上限均等于UserData请求时为true；具备两项证据但不等时为false；证据不足为null |

`available_mask` 的位1、2、4、8、16依次表示ISO setting、IQ上限、IQ DRIVE 原始值、AE上限、AE当前Sv可用。`available`只说明至少一项可用，不等于全部可用。旧桥六字段响应仍兼容，但所有新诊断项保持未知。`ae_policy_consumption_verified=true`只代表本次原厂IQ与AE策略getter核对相符；`ae_solver_verified`和`physical_exposure_verified`继续为false。

独立原厂相机会话在上限3200→6400后，实际初始化的IQ和AE对象读到 `iq_max_iso=6400`、`max_sv16=176`、`max_iso=6400`，而 `iso_mode=auto`、`current_sv16=80`、`current_iso=100`。这直接确认了通知和策略消费者，不是只读UserData；证据在 `outputs/controls/iso-policy-camera/ae-policy-results.json`。相机使用实验室传输后端，没有物理测光输入，因此这些当前ISO值不能作为暗场自动增益或实体拍摄验收。

`tools/probe_auto_iso_policy.py` 另外执行未修改的原厂AE代码，使用明确的IQ参数和模式fixture：3200→Sv160；6400在 AE Capture 模式0／3（普通 2D P／M）→Sv176；AE Capture 模式4（普通 2D 沙滩／雪景）被原厂限制为Sv128（ISO800）。原厂 `convertUserData_ISOAUTOMAX` 在 `0x1eb6a4` 明确将6400编码为 `0x003b000f`；本次没有发现它与3200编码 `0x003b000e` 混淆。实验结果见 `outputs/controls/auto-iso-policy-probe.json`。

模式语义核对（2026-10-24）：fixture 写入的是 AE 对象 `+0x6c` 的 Capture 模式，不是 API 旧 `shooting_mode`。原厂 `getSysInfo` 在 `0x2f924c–0x2f9250` 将 `getAeSysInfoCaptureMode()` 写入该字段；`getAeSysInfoShootingMode()` 则在 `0x2f92c4–0x2f92c8` 写入另一字段 `+0x82`。原厂枚举和转换链确认 SHOOTINGMODE_PROGRAM／APERTURE／SHUTTERSPEED／MANUAL → IQ Capture 2／3／4／5 → AE Capture 0／1／2／3；SHOOTINGMODE_BEACHSNOW → IQ Capture 19 → AE Capture 4（3D 关闭）。IQ 到 AE 的跳转在 `getAeSysInfoCaptureMode` 的 `0x2f8904` 分发表及 `0x2f8970`、`0x2f8994`、`0x2f8ad0`、`0x2f8adc`、`0x2f8a10` 常量块可核对。因此不能把 fixture 的“4 限制 ISO800”说成“M 限制 ISO800”；M 对应已测的 AE Capture 3。原厂枚举证据沿用 `outputs/metol/mode-context-enums.json`。本次只读核对并修正诊断字段，没有重跑已通过的策略探针或操作主模拟器。

最快快门限制另经原厂 capture AE 上限函数实现，未使用偏好表的最低快门字段；入口、适用范围和证据见 [SHUTTER_LIMIT.md](SHUTTER_LIMIT.md)。

## 配方面板半按退出

`mod/metol/nxui.c` 与 Quick Share 面板共用 `panel_input.h`，将evdev S1和模拟器X11 `Super_L`接入各自关闭入口。输入设备加入事件等待，空闲面板不再等最长60秒超时才发现半按；输入设备关闭或出现短读时释放描述符。普通配方保留已应用状态，LUT保留当前选择，不在半按时再提交一次配方。面板收到的X11按下/释放边沿转交恢复焦点后的窗口；evdev路径只关闭，不额外注入按键。

模拟会话已验证半按及释放后面板退出、原厂程序继续运行，配方代次没有额外增长，记录见 `outputs/metol/panel-s1-session.json`。记录耗时包含两次WSL输入命令和进程/配方查询，不作为按键延迟测量。该次原厂stdout未记录按键，因此原厂接收转交边沿及实体evdev时序尚未验证。

Quick Share半按退出已通过真实协议取消验证，见 [QUICKSHARE.md](QUICKSHARE.md)。
共享输入代码在关闭期间和销毁前处理已排队S1边沿；仅收到释放时保持面板
打开和当前焦点，向先前窗口转交该释放，不补发不存在的按下或全按。


### LUT 面板半按 S1 退出与状态保持验证

针对 LUT 面板（`open_panel('lut')` / `POST /lut`）同样复用了 `mod/metol/panel_input.h` 的共享按键逻辑。
经主仿真环境真实测试：
- 打开 LUT 面板并等待首帧渲染后，注入 X11 S1（`Super_L`）按键半按与释放。
- 面板在 0.25 秒内安全退出，恢复原厂相机焦点（`metol_pids` 清空）。
- 退出后核对配方与 LUT 状态：`color_style_ready=true`、`recipe_style_ready=true`，配方代次 `generation=3` 与数值保持完全一致，未被额外提交或回滚。
- 完整验证记录见 `outputs/metol/color-lifecycle/lut-panel-s1-verify.json`。
