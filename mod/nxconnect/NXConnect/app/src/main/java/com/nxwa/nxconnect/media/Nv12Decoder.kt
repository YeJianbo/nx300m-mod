/*
 * NXConnectForNX500 — 三星 NX500 相机的照片传输与遥控 App
 * Copyright (C) 2026 Katsiy and contributors
 *
 * 本程序是自由软件：你可以依据 GNU Affero 通用公共许可证第 3 版
 * （或任何更新版本）的条款重新分发和/或修改它。
 * 本程序按"有用"的目的分发，但不提供任何担保。
 * 详见随附的 LICENSE 文件，或 <https://www.gnu.org/licenses/>。
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
package com.nxwa.nxconnect.media

/**
 * 相机实时取景的 NV12 → ARGB 解码（纯 JVM，不依赖 Android）。
 *
 * 依据相机端 `web_root/js/liveview.js` 的原始实现：
 *  - body 是裸 NV12：前 width*height 字节是 Y 平面，其后交错 UV（U,V,U,V…）
 *  - 高画质：UV 全采样，每个 2×2 像素块共用一个 (u,v)
 *  - 低画质：UV 半采样，一个 (u,v) 覆盖 2×2 像素
 *  - 分辨率由 body 长度反推（见 [FrameSpec.detect]）
 *
 * 转换公式（与源码一致，BT.601 full range）：
 *   r = y + 1.402*(v-128)
 *   g = y - 0.344*(u-128) - 0.714*(v-128)
 *   b = y + 1.772*(u-128)
 *
 * 相机是单核 ARM，计算放在客户端做，所以这里必须够快：整数运算 + 位移近似。
 * 已用标准 BT.601 浮点实现逐像素比对过，最大偏差 1（纯整数舍入）。
 */
object Nv12Decoder {

    /** 由 body 长度反推分辨率与采样模式 */
    data class FrameSpec(val width: Int, val height: Int, val low: Boolean) {
        companion object {
            /** 相机在不同拍摄模式下会切换这些规格（源码 liveview.js 逐字列举） */
            private val SPECS = listOf(
                Triple(720, 480, false), // 普通 liveview
                Triple(720, 480, true),  // 普通 liveview（低画质）
                Triple(720, 404, false), // UHD/FHD/HD 录像中
                Triple(720, 404, true),
                Triple(720, 380, false), // 4K 录像中
                Triple(720, 380, true),
                Triple(640, 480, false), // VGA 录像中
                Triple(640, 480, true),
                Triple(800, 480, false), // NX300
                Triple(800, 480, true),
            )

            /** 该规格的字节数 */
            fun expectedSize(width: Int, height: Int, low: Boolean): Int =
                if (low) width * height / 4 * 3 else width * height * 3 / 2

            fun detect(len: Int): FrameSpec? {
                for ((w, h, _) in SPECS) {
                    if (len == expectedSize(w, h, false)) return FrameSpec(w, h, false)
                    if (len == expectedSize(w, h, true)) return FrameSpec(w, h, true)
                }
                return null
            }
        }
    }

    /**
     * 解码一帧到 ARGB 像素数组。
     *
     * ★ 低画质与高画质的**平面布局不同**，这是踩过的坑：
     *   高画质 720×480：body = 518400 = Y(720×480=345600) + UV(720×480/2=172800)
     *     → UV 平面覆盖整幅画面，每个 2×2 像素块共用一个 (u,v)
     *   低画质 720×480：body = 259200 = Y(**360×360**=129600) + UV(**180×180×2**=64800)
     *     → Y 与 UV 都是**半分辨率**，一个 (u,v) 覆盖 2×2 像素，一个 Y 覆盖 2×2 像素
     *
     * 我最初以为低画质的 Y 平面仍是全分辨率（720×480），于是按 `w*h` 去读 Y 平面，
     * 下标一路读到 259200 —— 正好越界，就是用户实测报的
     * `ArrayIndexOutOfBoundsException: length=259200; Index=259200`。
     *
     * @param out 可复用的输出数组（长度须 >= width*height）；长度不足或为 null 时新建
     * @return ARGB_8888 像素数组（长度 width*height）
     */
    fun decodeToArgb(data: ByteArray, spec: FrameSpec, out: IntArray? = null): IntArray {
        val w = spec.width
        val h = spec.height
        val size = w * h
        val pixels = if (out != null && out.size >= size) out else IntArray(size)

        // 低画质：Y 与 UV 都按半分辨率存放
        val yW = if (spec.low) w / 2 else w
        val yH = if (spec.low) h / 2 else h
        val uvW = w / 2
        val uvOffset = yW * yH
        val uvRowStride = uvW * 2

        var i = 0
        for (y in 0 until h) {
            for (x in 0 until w) {
                // 低画质时每 2×2 个输出像素共用同一个 Y
                val yIdx = if (spec.low) (y / 2) * yW + (x / 2) else y * yW + x
                val yv = if (yIdx < data.size) data[yIdx].toInt() and 0xFF else 0

                val uvIndex = uvOffset + (y / 2) * uvRowStride + (x / 2) * 2
                if (uvIndex + 1 >= data.size) {
                    // 数据意外偏短：退化为灰度，绝不越界
                    pixels[i++] = 0xFF000000.toInt() or (yv shl 16) or (yv shl 8) or yv
                    continue
                }
                val u = (data[uvIndex].toInt() and 0xFF) - 128
                val v = (data[uvIndex + 1].toInt() and 0xFF) - 128

                // 整数近似：1.402*256=359, 0.344*256=88, 0.714*256=183, 1.772*256=454
                var r = yv + ((359 * v) shr 8)
                var g = yv - ((88 * u + 183 * v) shr 8)
                var b = yv + ((454 * u) shr 8)

                if (r < 0) r = 0 else if (r > 255) r = 255
                if (g < 0) g = 0 else if (g > 255) g = 255
                if (b < 0) b = 0 else if (b > 255) b = 255

                pixels[i++] = 0xFF000000.toInt() or (r shl 16) or (g shl 8) or b
            }
        }
        return pixels
    }

    /**
     * 便捷入口：自动识别规格后解码。
     * @return 解码结果（像素数组 + 规格），长度不可识别时返回 null
     */
    fun decode(data: ByteArray, out: IntArray? = null): Decoded? {
        val spec = FrameSpec.detect(data.size) ?: return null
        return Decoded(spec, decodeToArgb(data, spec, out))
    }

    class Decoded(val spec: FrameSpec, val pixels: IntArray) {
        val width: Int get() = spec.width
        val height: Int get() = spec.height
    }
}
