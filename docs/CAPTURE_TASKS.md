# 可取消单张拍摄任务：接口与完成证据

## Ecore 线程按需状态读回（2026-10-21）

`capture-observer.so` 现提供按需只读快照，复用原厂 Ecore 主循环。它从已有
singleton 槽取得对象，验证 vtable 与可读范围后调用原厂 getter；不创建
对象，不改变拍摄状态。请求包含随机 ID 和单调时钟期限，响应通过原子
重命名发布，过期请求不调用 getter。多字段读取期间其他工作线程仍可能
推进，不能把它视为整个 ISP 的原子快照。

独立真实原厂会话 namespace18022、guest PID83 验证通过：capture=idle，
capture_requested=false，RAW/PP/YCC/编码四项 work_end 均可读且为 true。
此时没有触发快门；这些初始状态不证明照片已保存，响应保持
`photo_completion_proven=false`。所有对象槽、vtable、getter 与字段读取
可用。独立会话已停止，正常主会话身份保持不变。

证据：`outputs/firmware-runtime/capture-status/status.json`、`report.json`。
模块构建及原有观察 ABI 检查退出 0。请求关联、写卡完成确认和定时调度
仍待实现，本次状态读回只提供其所需的原厂状态输入。

开发预设 `tools/simulator.py --profile development` 默认加载观察库；
单独启动可加 `tools/run_stock.py --capture-observer`。正常主会话已实际重启
加载最新写闭观察版本，namespace8119、guest PID91 的读回见同目录 `main-status.json`，
画面仍正常。`/status` 通过当前 guest PID 核对 `capture_status_ready`。
已运行会话可用：

```powershell
python -X utf8 tools/capture_status.py
# 独立会话使用 --session-file <其 session.json>
```

客户端持同名 flock，核对 ready 和响应的原厂 guest PID、请求 ID 与期限。
未加载观察库或主循环未响应时返回错误，不用旧快照代替。
请求文件为 `/var/run/nx300m-capture-status-request`，内容为
`<uint32 request_id> <uint64 deadline_monotonic_ms>`；响应为同前缀的
`-response` JSON，`-ready` 保存当前 guest PID。

## 原厂相机会话集成（2026-10-21）

新增 `run_stock.py --capture-observer`，按需加载已构建的观察库；
`probe_capture_color.py --capture-observer` 在独立会话中收集原子日志。
实际运行记录位于 `outputs/firmware-runtime/capture-lifecycle/`，namespace23832。
原厂进程guest PID73在调用者偏移 `0x23f864` 触发两次
`backend-completion`，drive_id均为0，completion_arg依次为1、0，
原函数返回值均为0。两次均透传至 `/usr/lib/libcapture-fw-slpcam.so`。
没有 `write-chunks` 记录或新媒体文件，最终命中原厂JPEG空缓冲断言。

因此实际调用路径已经验证，保存成功路径尚未到达。结束前只读快照显示
capture=idle、编码计数0/0，仍明确标记 `photo_completion_proven=false`。
这次运行不关联用户请求，也不宣称回调参数0表示照片保存。
采集工具退出0仅表示观察及清理完成；独立会话已停止，主会话28897身份不变。

本页记录 NX300M 1.15 原厂接口及当前实现边界。定时调度、请求关联和
拍摄成功判定尚未实现；`tools/capture_status.py` 提供 Ecore 线程按需读回，
`tools/pipeline_state.py` 保留外部只读诊断快照。
以下地址均为原厂 ELF 虚拟地址，调用时优先按导出符号解析，不能当作
运行进程的绝对地址。分析未操作主相机。

## 就绪状态与只读 ABI

库为 `/usr/lib/libcapture-fw-slpcam-nx300.so`。以下实例方法在 ARM ABI 中
以 `r0=this` 传入对象，返回值在 r0。

| 接口 | ELF 地址 | 已确认语义 |
|---|---|---|
| `_ZNK13CStateManager15GetCaptureStateEv` | `0x25f780` | 返回对象 `+0x18` 的 int 状态；0=off、1=idle、2=capture、3=movie，构造初始值为-1 |
| `_ZN9SingletonI13CStateManagerE11m_pInstanceE` | `0x4a7274` | 4-byte 对象指针槽；尚未构造时为0 |
| `_ZTV13CStateManager` | `0x384188` | 实例 vptr 为加载偏移加 `0x384190`，即符号地址+8 |
| `_ZNK18CInformationBureau18IsCaptureRequestedEv` | `0x230af4` | 返回对象 `+0x22` 的 byte 状态 |
| `_ZN9SingletonI18CInformationBureauE11m_pInstanceE` | `0x4a70f8` | 4-byte 对象指针槽 |
| `_ZTV18CInformationBureau` | `0x382798` | 实例 vptr 为加载偏移加符号地址+8 |

`CStateManager::GetInstance`（`0x25f5a8`）和 `Instance`（`0x25f590`）
都调用 `Singleton::getInstance`（`0x25f9e8`）；后者在槽为空时执行 create。
只读桥应先读取已导出的对象槽并检查 vtable，再对已存在对象调用 getter，
不要用查询触发相机对象创建。

现有 `runtime/exposure_controls.c`、`runtime/pw_control.h` 使用原厂
`ecore_timer_add`，在相机 Ecore 线程读取请求并调用原厂 API。拍摄状态
读回可沿用该机制；不要在 Ecore 回调中阻塞等待拍摄或调用带 sleep 的
shell 循环。`pipeline_state.py` 的外部内存读取是非原子快照，不是完成通知。

**idle 不是照片完成。** `CDriveSingle::OnRawCaptureDone(int)`
（`0x2504c8`）在 `0x250578..0x250588` 调用
`CStateManager::SetCaptureState(1)`，随后继续后端相关处理。因此从
capture 回到 idle 不能证明 JPEG 编码、写卡或文件保存完成。

## 原厂处理计数器

`_ZN9SingletonI15CCaptureCounterE11m_pInstanceE` 位于 `0x4a716c`，
是4-byte对象指针槽；`_ZTV15CCaptureCounter` 位于 `0x384138`，
实例 vptr 为加载偏移加 `0x384140`。现有 `pipeline_state.py` 已加入这些
对象与字段的只读检查；它们只描述原厂处理状态。

| getter | ELF 地址 | 比较的对象字段 |
|---|---|---|
| `_ZN15CCaptureCounter12isRawWorkEndEv` | `0x25d3dc` | `+0x04 == +0x0c` |
| `_ZN15CCaptureCounter11isPPWorkEndEv` | `0x25dda0` | `+0x10 == +0x14` |
| `_ZN15CCaptureCounter12isYccWorkEndEv` | `0x25d4dc` | `+0x1c == +0x24` |
| `_ZN15CCaptureCounter12isEncWorkEndEv` | `0x25d51c` | `+0x28 == +0x2c` |

上述 getter 返回原厂 bool。`increaseEncWorkDoneCounter`
（`0x25d214`）在 `0x25d230..0x25d23c` 把 `+0x28` 加1；
`increaseTotalWorkCounter`（`0x25d138`）分别把 `+0x08`、`+0x20`、
`+0x2c` 加1，其中 `+0x2c` 是编码总工作数。`clearAllCounter`
（`0x25d250`）会清理各阶段计数器。因此初始0=0、错误后的清理或前次
任务的相等计数都不能证明本次请求完成。轮询可能漏掉很短的非零阶段，
不能仅要求曾观察到一次 capture 状态来关联所有结果。

## 后端完成观察入口

优先使用一个透传观察入口：

```c
/* _ZN10CDriveMode17BackEndCompletionEii, ELF 0x24c320 */
int BackEndCompletion(void *self, int drive_id, int completion_arg);
/* ARM: r0=self, r1=drive_id, r2=completion_arg; 原返回值保留。 */
```

`CResBackEnd::Run`（`0x23f680`）处理 event=7，保存其参数到对象 `+0xc`，
再调用 `onCompletion`（`0x23f7d8`）。后者在 `0x23f860` 经 PLT 调用上述
`CDriveMode::BackEndCompletion`，提供了不依赖各 drive 子类 vtable 替换
的统一观察位置。统一入口再按 drive_id 取得 handler，经 vtable+0x2c
调用该 drive 的 `OnBackEndCompletion(int)`。

已确认的参数分支为：

- `CResBackEnd::onCompletion` 在 `0x23f80c..0x23f828` 仅对
  `completion_arg==0` 增加 YCC work-done 计数。
- 单张模式 `CDriveSingle::OnBackEndCompletion`（`0x250d20`）在
  `0x250f38..0x250f54` 仅对该参数为0时弹出前端参数队列。
- 该单张方法随后可能调用 `clearAllCounter`，其自身返回0也不能反推
  输入参数或文件保存结果。

观察记录应保留原始 completion_arg，并可标注“原厂参数为0的正常完成
分支”。目前未确认完整错误码枚举；不能把非零参数统一翻译成某个具体
错误，也不能把参数为0命名为“照片已保存”。该入口本身没有文件路径。
后续观察器只记录并透传原函数，不能主动调用此入口制造完成。

## 文件保存完成通知的反例

`libsif.so::DscFileHandler::TaskMain`（`0x27048`）在 `0x270b4` 调用
`WriteChunks`。返回 false 时只经过错误日志分支，随后仍在
`0x27124..0x27148` 调用保存描述符的同一个完成 callback，传入
`(image_buffer, 0, 0)`。因此这个 callback 的到达及两个零参数不能证明
写卡成功。

`_ZN14DscFileHandler11WriteChunksEv`（`0x274a4`）的 ABI 为
`bool WriteChunks(void *self)`，r0传 this、r0返回 bool。它的结果更接近
实际写入，但仍不能单独充当照片成功判据：

| 原厂分支 | 结果与依据 |
|---|---|
| 空文件路径 | `0x27500..0x2750c` 直接返回 true，没有写文件 |
| 保存被 Stop | `self+0x70==2` 时，`0x2763c..0x27674` 或 `0x27784..0x277bc` 关闭文件并返回 true，可能尚未完成全部块 |
| Make／Create／Write 失败 | 相应失败分支返回 false，例如 `0x27870..0x278b0` |
| 正常块循环结束 | `0x27a40..0x27a7c` 关闭文件后返回 true；这里没有检查 Close 的返回值 |

该函数使用的关联链为 `self+0x64 -> image_buffer`，
`image_buffer+0x2c -> save_descriptor`，路径位于 `save_descriptor+0x0c`
的内联字符串；`0x275d4..0x275f0` 将该地址传给 Make，
`0x27678..0x27698` 传给 Create。完成 callback 位于描述符 `+0x40`。
路径和描述符会被后续 MemoryRelease 释放，若未来增加保存观察，应在
原函数执行期间复制所需的有界元数据，不能保存裸指针后延迟解引用。
现有被动观察器已记录这条路径链的有界副本，尚未用实际新照片验证请求关联。

## 写入和关闭的定点判据

`tools/inspect_capture_save.py` 只分析已核验的原厂 `libsif.so`，输出
`outputs/firmware-runtime/capture-save/evidence.json` 与反汇编。
`WriteChunks` 的正常 Close 调用点为 `0x27a48`（返回地址 `0x27a4c`），
Stop 分支分别位于 `0x2766c`、`0x277b4`。Create、Write、Close 和其内
fflush/fclose 均通过 PLT，可保留原调用并观察。

`DscOsalFile::Close` 丢弃 fflush 结果，其 r0 也不是可靠的成功返回值。
新观察使用线程局部的嵌套作用域，匹配本次 descriptor、OSAL 对象与
FILE 指针，并核对原厂调用位置。只有预期 chunk 字节数可读且大于0、
所有 Write 完整、实际累计字节相等、走到正常 Close、无 Stop、无流错误、
fflush 与 fclose 各成功一次，才设置顶层 `write_completion_confirmed=true`。
空路径、Stop、短写、刷新失败或无法完整观察都不能满足该判据。

该值只证明这次原厂 WriteChunks 的 stdio 写入和关闭成功；不证明断电
持久化，不证明文件来自某次拍摄请求，也不替代 JPEG/SRW 内容有效性检查。
观察器不额外调用刷新或关闭，日志自身的 fclose 不参与此判定。

隔离原厂调用验证见 `outputs/firmware-runtime/capture-save/probe/report.json`：
使用未修改的原厂 WriteChunks、合法布局及两段共21字节生成文本。

| 场景 | 原厂返回值 | 实际观察 | 写闭完成 |
|---|---|---|---|
| 普通生成文件 | true | 两次 Write 共21字节，正常 Close，fflush/fclose均返回0，文件字节一致 | true |
| 开始前 Stop | true | 无 Create、无 Write | false |
| 私有 `/dev/full` 字符设备1:7 | true | Write共21字节，fflush返回-1、errno=28，fclose返回0 | false |

第三项证明不能只信原厂返回值或 Write 的字节数。fclose成功后保留的
errno=28是原始残留值，不据此认定fclose失败。测试设备位于独立mount
namespace的tmpfs `/dev`，未覆盖共享rootfs或用户媒体。
ARM构建、原有观察ABI检查及以上三个定点场景均退出0；最后的字段更名
只重跑这三个场景。测试没有启动相机，也没有生成照片。

## 已实现的被动观察器

`runtime/capture_observer.c` 构建为
`outputs/firmware-runtime/capture-observer.so`，包装本页确认的
`CDriveMode::BackEndCompletion(this,int,int)`、
`DscFileHandler::WriteChunks(this)`，并在当前 WriteChunks 作用域内关联
`DscOsalFile::Create/Write/Close` 及 Close 内的 `fflush/fclose`。
它调用 `RTLD_NEXT` 的相同入口，
传入原参数，并保留原返回位值及调用后的 errno；记录读取、元数据查询、
日志写入失败均不抑制已解析的原函数调用。它不创建原厂 singleton，
不主动调用回调、不修改 Stop 或输入状态，也不负责定时调度。

每个相机进程使用 `mkdtemp` 创建
`/var/run/nx300m-capture-observer-<pid>-<unique>/`，目录权限0700。
`ready.json` 的 kind 为 `observer-ready`、`completion_event=false`，仅证明
观察库已初始化。其余事件分别为 `backend-completion`、`write-chunks`，
临时文件写完并关闭后原子 rename 为 `event-<sequence>.json`。
最多预留128个事件；争用时可跳过记录，写入失败也会留下序号缺口。
sequence 表示 hook 进入次序，不保证并发调用的返回顺序。子进程按自己的
PID 创建目录，不继续使用父进程的记录锁或目录。

事件保留 `guest_pid`、原始返回值／errno、原函数 provider 与相对地址、
调用方相对地址、原调用前后单调时间。self、image_buffer、save_descriptor
指针只用于当前进程内的观察关联，不能当作跨拍摄任务的唯一 ID。
单调时间为0明确表示不可用。

WriteChunks 的 before／after 各自记录：

- `self+0x70` 的原始 Stop 值及读取是否成功。
- `self+0x64 -> image_buffer+0x2c -> descriptor+0x0c` 路径链。
  使用私有 pipe 复制可选指针数据，路径最多读取52 bytes；另记是否在此
  范围内发现 NUL。不可读、空路径或未终止路径不进行文件元数据查询。
- `file_metadata_observation` 只记录 `access(F_OK)` 的存在性观察、
  只读非阻塞且不跟随 symlink 的 open 是否成功，以及 `lseek64(SEEK_END)`
  观察到的大小和错误。权限等不确定错误对应 `exists=null`，未能取得
  大小时为 `size_bytes=null`。这些元数据不证明写入已结束；
  `metadata_proves_write_completion` 恒为 false。实际写闭判据独立记录在事件顶层。

路径字段名为 `path_bytes_latin1`。JSON 中控制字符、引号和反斜杠正确
转义，所有 >=127 的字节写为 `\u00xx`；
`path_string_encoding="byte-values-u00xx; recover via latin1 bytes"`
明确规定按 Latin-1 恢复原始路径字节，不能将解码后的字符直接当作 UTF-8
路径原文。观察器没有 `saved_success` 字段，所有事件均标记
`observation_only=true`、`request_associated=false`。

加载环境必须能提供原厂函数及 libc／libdl／原厂 librt 的依赖。
若 `RTLD_NEXT` 无法解析目标，BackEndCompletion 返回-1、WriteChunks
返回0，errno=ENOSYS；这属于缺少可转发目标的装载边界，不能称为所有
加载情景均保持原行为。正常已解析目标下的观察失败则继续原调用。

### 构建及隔离验证

```powershell
python -X utf8 tools\build_capture_observer.py
```

构建工具核对实际原厂 ELF 的入口和路径／Stop／callback 指令锚点。
ARMv7 soft-float 库及 probe 通过 `-Wall -Wextra -Werror`；保留
SysV hash 与 `-z nocombreloc` 以兼容原厂 glibc 2.13。
`outputs/firmware-runtime/capture-observer-build.json` 的实际构建及 probe
退出码均为0。验证在独立 mount／network／IPC namespace 中运行，没有
启动相机或进入主相机会话：

- 未修改的原厂 `libsif.so::WriteChunks` 在明确分配的空路径对象布局上
  执行，返回 true 且未写文件；直接调用与观察包装的返回和 errno 一致。
  该案例不构造 singleton，也不制造后端完成回调。
- `capture_observer_fixture.c` 是明确的替代 provider，仅用于检查透传
  ABI、原返回值／errno、路径字节转义、before／after Stop与文件元数据。
  它写的是13-byte文本 fixture，随后返回 false，不是相机照片。
- 不可读的可选对象、故意使事件临时文件无法打开、以及超过128次记录
  的调用仍到达 fixture 原入口。最终收集127个事件，第4号是故意记录
  失败留下的缺口；其余事件到128号后停止增长。

probe 的原始输出和 JSON 在 `outputs/metol/capture-observer-probe/`。
原厂实拍集成与本次请求／新文件关联尚待独立验证；隔离通过不代表照片
保存成功。

## 单张任务的最小成功条件与调度边界

2026-10-21 定点追踪明确了剩余关联缺口，见
`outputs/firmware-runtime/capture-association/findings.md` 与 `evidence.json`。
文件侧已确认 JPEG 输入 → Encode(input,output) → 输出入队 → WriteChunks →
FreeBuffer 的生命周期链，但尚未动态建立对象代次记录。前端仍缺
“本次输入被原厂接受后的 CProcessParameter/CCapImage 代次 → 某一个
CaptureDataReceiver 队列条目”的共同身份。通知参数0、固定标签3/0、
按秒播种可能碰撞的 RandomKey，以及跨队列复制前的裸指针均不能填补这一跳。
此次只读三个相关ELF的21个函数，未用时间窗口推断请求归属。

后续应先实现状态读回和完成观察，再接定时调度。单张任务至少分别记录：

1. 本次请求身份与原厂进程身份，以及本次输入是否已投递、是否收到原厂
   拍摄开始／处理事件。原始开始发布入口
   `CCapturePublisher::NotifyCaptureStart`（`0x257070`）发布事件
   `0x01010001`；需要将本次事件与请求关联，而不是接受前次遗留状态。
2. 本次后端完成事件及原始参数、处理计数器与可再次接受输入的状态。
   这些用于描述任务阶段、失败或等待状态，不替代文件证据。
3. 与本次请求关联的实际新输出路径，文件确实存在、非空、可读，并有
   写入已结束的证据。应排除前次已有文件、空路径和 Stop 提前返回。
   新文件出现或大小暂时稳定都不能单独证明写完成；不能用固定 sleep
   把等待状态改成成功。上述关联及写完成确认尚未实现。

即使相机已经 idle，后端事件或文件证据未满足时仍应报告“处理结束待确认”
或相应失败／超时，不增加“已保存”张数。定时到点而上一张未完成时不应
重叠触发下一张；采用何种间隔起算方式属于后续调度设计。

## S1／S2 与取消释放

现有 `tools/input_stock.py` 和 `docs/HARDWARE.md` 已使用原厂映射：
`Super_L` 为 S1 半按，`Super_R` 为 S2 全按。模拟器同时提供私有 board
输入 `d4keys-polled/shutter`：0=释放、1=半按、2=全按；原厂 Ecore
处理器与 `device_get_shutter` 仍自行处理输入。XTEST 返回及 XSync 只证明
事件已投递给 X server，不能证明相机接受拍摄或生成照片。

旧 NX300 实现位于
`outputs/deployment/upstream-nx300-hacks/src/intervalometer/intervalometer.c`：
非零间隔使用后台 shell 循环 `push s1 -> click s2 -> release s1 -> sleep`；
零间隔直接保持 S1／S2。窗口的 Cancel 只退出窗口，不能取消已经启动的
后台循环，且没有原厂完成检查。它只能提供历史按键顺序参考。

可取消单张任务应跟踪自身注入的按下状态，在取消、错误、超时和正常
触发收尾中停止后续触发，按 S2 release、S1 release 顺序释放自身持有的
输入。模拟器还需恢复其私有 shutter level=0。不要在取消时再补发一次
S2，不要把清理释放表述为当前照片已完成或曝光已中止。

`CCaptureStateCapture::stopCapture`（`0x260f98`）确实调用
`CCaptureController::StopCapture(0)`，但本轮未核对它在全部曝光、Bulb、
写卡阶段的取消语义。因此当前可确认的取消边界是“撤销后续定时触发并
释放本任务输入”；中止已经进入原厂处理的单张、Bulb或连续拍摄尚未确认。
用户真实按键与本任务合成按键的并发仲裁也尚未实现。
