# EP MnF Bayer split 软件执行

当前实现支持原厂 NX300M 1.15 拍摄后台实际请求的 `EP_STATE_MNF_STANDALONE`、`MNF_OPMODE_BAYER_SPLIT`（20）：将交错排列的 12-bit Bayer 样本按横纵坐标奇偶拆成四个平面。它没有去马赛克、颜色处理、滤波或缩放。

## 原厂证据与执行条件

`libcapture-fw-slpcam-nx300.so!CDDWrapperMnF::PrepareOperation` 的 mode 20 调用 `ep_udd_mnf_bayer_one_channel_setup`。原厂 `libudd.so.3.1` 保留配置及启动流程：

1. `MnF_bayer_one_channel` → `MnF_set_bayer_spliter` 配置 EP DMA RDMA `+0x40`、WDMA `+0x140`。
2. `ep_udd_path_block_start` 初始化 core 10 和 DMA 5 等待队列。
3. `ep_top_start_frame` 向 EP top `+0x40` 写入 MnF start bit 8。
4. 原厂等待 core 10（MnF operation finish），然后 DMA 5（MnF WDMA bus end）。

开发拍摄保存的 `outputs/firmware-runtime/ep-mnf-config-bank.bin` 是退出后的寄存器快照，不是原子快照。与原厂 setup 参数、反汇编一致的内容包括：

| 参数 | 捕获值 |
| --- | --- |
| 工作尺寸 | 5624 × 3696 |
| 输入及输出位深 | 12 bit，两像素占三字节，低位在前 |
| 输入行间距 | 8448 字节 |
| 输入 RDMA 同奇偶场间距 | 16896 字节 |
| 输出各平面同奇偶场间距 | 4224 字节 |
| 输入 RDMA 物理首址 | `d1fb0000`、`d1fb2100` |
| 输出四平面物理首址 | `d3db9000`、`d4534080`、`d4caf100`、`d542a180` |

输出端口 0 对应偶数列、端口 1 对应奇数列，各端口的 even/odd 首址对应偶数行和奇数行。平面内部坐标为 `(x >> 1, y >> 1)`。平面首址、间距直接来自原厂 DMA 寄存器，不以颜色名称重新解释或排序；原厂 `GetCapImageToMnfImage` 已依据 raw head color 分配这些地址。

`runtime/ep_mnf_pixels.h` 转发 `ep_top_start_frame(command)` 至原厂函数，保留原始参数和寄存器写入。原厂调用返回后，仅对本次真实 command 的 MnF start bit 执行该受支持配置，验证全部源、目标映射，随后执行全部像素写入。仅写入成功后将完成事件加入 core 10、DMA 5 队列。无 start、未映射内存、重叠目标或不支持的模式均不产生完成事件。它不写原厂应用成功标志、不调用原厂完成回调。直接观察转发函数的 command，避免 LVR 另一线程覆盖共享的 top frame-start 字后漏读 MnF 启动。

## 已验证

运行：

```powershell
python -X utf8 tools\probe_ep_mnf.py
```

该命令仅定向构建 `ep-mnf-device.so` 和隔离 probe，不覆盖正常模拟器的 `device_preview.so`。WSL 另建 mount/network/IPC namespace，调用未修改的原厂 libudd setup/start/wait，并使用 `pp-active-bayer-fixture.bin` 中录制的完整尺寸 SRW mosaic 输入。地址模式在 probe 中显式改为物理地址，以使用隔离缓冲；实际相机程序仍由原厂虚实地址转换配置 DMA。

`outputs/firmware-runtime/ep-mnf-probe.json` 的实际结果：

- probe 退出码 0，原厂 block start 返回 2000。
- 独立 NumPy 解包、奇偶切片和重新打包，与输出的 31,375,872 字节全部相同，覆盖 20,786,304 个有效像素；行内填充和未写的末尾行保持原值。
- 没有第二次 start 的重复等待返回原厂 `-2`、`errno=110`。
- 不支持的 color effect 配置返回 `-2`、`errno=110`，没有虚构完成。

## 尚未验证或实现

尚无物理 MnF 输出对照，因此全字节相同证明的是软件拆分与独立坐标变换一致，不证明硬件 bit-exact。输入 fixture 含未实测的常量零边框，也不是传感器、D4C 或 PP 硬件等效。

完整拍摄开发会话 `ep-cs-grids` 已实际记录 `EP MnF software Bayer split`，随后进入第二段 PP 内存处理；对应日志及快照索引在 `outputs/firmware-runtime/ep-cs-grids-development.json`。第二段 PP 像素路径仍未实现，原厂随后因 JPEG 缓冲为空触发断言，没有生成照片。这不验证 IPCS、JPEG 或实体机部署。其他 MnF 模式、非 12-bit 格式、逆序、重叠、色效、逐 tile 操作尚未支持。

反汇编证据位于 `outputs/firmware-runtime/ep-mnf-{setup,split,dma,start,path-start,path-frame,image,address,pixel-getter,operation}.asm`。
