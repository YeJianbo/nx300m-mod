# 录制 Bayer 的软件成像适配

`runtime/software_isp.h` 在原厂 `DscJfif::JpegEncode` 任务边界运行一条明确的软件成像模型。输入是已解码保存的真实 GRBG 12bit mosaic；不读取 `photo-reference.jpg`，也不注入外部渲染的 RGB/YCC 样本。

## 输入与启动

先用 Conda Python 建立同一 RAW 的白平衡和几何配置：

```powershell
python tools/prepare_software_isp.py --raw work/reference-media/SAM_4262.SRW
python tools/build_jpeg_encode.py --reference-raw work/reference-media/SAM_4262.SRW
python tools/probe_capture_color.py --software-isp-source outputs/firmware-runtime/software-isp/source.bin --capture-observer --startup-seconds 15 --capture-seconds 240 --output-dir outputs/firmware-runtime/capture-software-isp-new
```

准备程序将录制 packet 的可见区域与恢复 RAW 的全部 20,461,056 个 mosaic 像素逐点核对。当前源为 5624×3714、stride 8448；有效裁切 `[78,18,5536,3696]`，左上为 GRBG。白平衡来自同一 RAW：`[11792,4096,7120,4096]`，黑位全为 0，白位 4095。几何和白平衡配置不含已渲染像素。

`run_stock.py --software-isp-source PATH` 要求显式 Bayer replay、独立输出目录和专用卡 `/opt/nx300m-lab/media/nx300m-software-isp.img`，与外部样本诊断模式分开。

## 执行链

1. 原厂读出流程实际转移完整 Bayer 行后，`pp_replay` 增加传输 generation。没有实际转移的源不能启动软件编码。
2. 软件模型在有效区域内镜像处理边界，在私有 Bayer 副本上做同色像素的可分 `[1,2,1]` 低通，再做双线性 CFA 插值与同 RAW 白平衡。该软件滤波用于压低未经硬件降噪的 RAW 噪声；未过滤版本的主图压缩曾返回错误，而其缩略图成功。滤波后的同 C 核原生主机诊断在原厂 quality0 量化表下编码为 7,398,806 字节，低于原厂主图容量 11,977,113 字节；这一主机诊断本身不证明原厂任务已保存。
3. 使用真实原厂 setter 转发后的 CCM、8192 项 Gamma、三通道 730 项 3D LUT、RGB2YCC 矩阵；LUT adapter 在 preload 顺序中位于软件 ISP 之前，因而捕获的是其最终转发表。
4. 生成原厂任务请求大小的私有 progressive NV16 平面。小预览与缩略图在插值前按同色像素做重复2×面积缩小，避免点采样混叠与 JPEG 超容量；主图不进入面积缩小分支。当前按可见 RAW 区域缩放到对应 JPEG 大小，尚未复现原厂各画幅精确裁切与缩放算法。
5. 现有 stock `libjpeg.so.8` 与原厂量化表压缩；只在 Bayer generation 不变、原厂输出 allocation generation 不变时提交编码字节。
6. 原厂继续处理 EXIF/MPO、文件写出和拍摄状态通知。软件模块不伪造 PP/IPCS 硬件完成 IRQ，也不回写原始照片。

## 模型与边界

当前模型选择 Q7 CCM、12bit→13bit Gamma 索引、三线性 9³ LUT、Q8 全范围 RGB2YCC，输出支持本次实际遇到的 progressive NV16 4:2:2 任务。未知布局拒绝编码，不回退外部样本。

这些选择可让真实原厂参数驱动真实 Bayer 像素，但不等于硬件像素等价：PP 的 DPC/CS/DAMP、硬件 CFA、ISP 算子顺序及裁切/舍入未复现；目前还未按原厂所有 enable 位执行等价开关。LUT 使用已记录的三通道表，缺任一必要参数拒绝执行。

该输出域是上述原厂参数的软件模型域，尚未证明为经过完整相机校准的线性 sRGB，不应直接送进要求完成 HueSatMap 的 `transform_linear`，也不能对已过 Gamma 的 JPEG 再套完整 DCP。

完整 DCP 参考独立使用 `tools/bayer_dcp.py` 的 packet→DNG 和 `convert_camera_look.render_raw`。它与此轻量 ARM 软件模型的用途不同：前者用于完整色彩参考，后者验证原厂拍摄参数、真实 mosaic 与原厂保存流程的连接。

## 实际保存结果

最后一轮 `outputs/firmware-runtime/capture-software-isp-area/capture.json` 已完成真实原厂保存：

| 原厂任务 | 编码字节 | 原厂容量 | 结果 |
|---|---:|---:|---|
| 缩略图160×120 | 13,322 | 19,200 | 成功 |
| 主图5472×3648 | 6,944,642 | 11,977,113 | 成功 |
| 预览1616×1080 | 976,557 | 1,047,168 | 成功 |

三个任务都消费同一次实际 Bayer 转移 generation=1 和原厂参数 revision=6。原厂 `WriteChunks` 随后返回，生成 [SAM_0001.JPG](../outputs/firmware-runtime/capture-software-isp-area/media/DCIM/100PHOTO/SAM_0001.JPG)，7,975,085字节。独立 Windows Pillow 完整解码5472×3648、4:2:2，EXIF为SAMSUNG/NX300M/1.15；[保存结果预览](../outputs/firmware-runtime/capture-software-isp-area/saved-photo-preview.png) 已目视检查，未出现旧半幅绿色错误，下半部RGB均值约 `[143.03,144.19,148.58]`。

本轮无外部渲染 RGB/YCC 输入，软件模型没有改写原始 RAW/packet。独立 namespace15235已停止，受保护的主会话30352在本次检查前后身份一致。QEMU下整个软件处理需要约两分钟，尚不是实时处理方案。

失败证据也保留：`capture-software-isp` 的未滤波主图压缩失败；`capture-software-isp-filtered` 在旧120秒总观察截止前仅完成缩略图；`capture-software-isp-optimized` 已完成主图，但未做面积缩小的预览触发 `JERR_BUFFER_SIZE`。最终修复保留所有原厂任务容量，没有扩大输出 allocation 或回退外部样本。
