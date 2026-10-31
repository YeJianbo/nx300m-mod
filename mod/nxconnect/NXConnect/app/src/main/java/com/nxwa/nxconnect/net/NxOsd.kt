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
package com.nxwa.nxconnect.net

/**
 * OSD（相机屏幕镜像）协议客户端。
 *
 * 纯 JVM 实现（不依赖 Android），因此可以被 JVM 单元测试覆盖。
 *
 * ## 协议（真机实测，NX500 固件 1.12）
 *
 * 这是本项目里**唯一一个 POST 接口**，用 GET 会 404 —— 这是个常见误判点。
 *
 * - **请求**：`POST /api/v1/osd/get`，表单体 `hashs=<108 个 8 位十六进制哈希拼接>`
 * - **语义**：客户端把**自己当前持有的每个块的哈希**发上去，服务端**只回变化的块**
 * - 哈希填 `00000000` 表示"我没有这个块"，等价于请求该块的全量数据
 *
 * ## 响应格式（大端 u16 序列）
 *
 * ```
 * u16 frameWidth      // 实测 720
 * u16 frameHeight     // 实测 480
 * u16 blockWidth      // 实测 80
 * u16 blockHeight     // 实测 40
 * 重复若干块：
 *   u16 x, u16 y      // 块的左上角坐标
 *   u32 hash          // 该块内容的哈希
 *   byte[bw*bh*4]     // RGBA 像素，按块宽块高排列
 * u16 0xFFFF, u16 0xFFFF   // 结束标记
 * ```
 *
 * ## 实测性能
 *
 * | 场景 | 数据量 | 耗时 |
 * |---|---|---|
 * | 全量（哈希全 0） | 1,383,276 字节 | 815 ms（约 1.2 fps） |
 * | 增量（同画面） | 51,244 字节 | 50 ms |
 * | 增量轮询（10 次平均） | 153,708 字节 | 108 ms → **约 9.2 fps** |
 *
 * 结论：**首帧之后必须走增量**，否则只能跑到 1.2 fps。
 *
 * ## 像素格式
 *
 * **RGBA**，每像素 4 字节。实测直接按 RGBA 解释即为正确画面
 * （渲染出的相机屏幕截图见整理夹 `OSD界面样例-AF模式面板.png`）。
 */
object NxOsd {

    /** 相机端一帧的块布局（实测值；解析时以响应头为准，这里只作默认值与自检用） */
    const val EXPECTED_FRAME_W = 720
    const val EXPECTED_FRAME_H = 480
    const val EXPECTED_BLOCK_W = 80
    const val EXPECTED_BLOCK_H = 40

    /** 块总数上限：mod 的网页端硬编码 120，实测 9x12 = 108 */
    const val MAX_BLOCKS = 120

    /** 结束标记 */
    private const val END_MARK = 0xFFFF

    /** 一帧 OSD（只含**本次变化的**块） */
    data class Frame(
        val frameW: Int,
        val frameH: Int,
        val blockW: Int,
        val blockH: Int,
        /** 键为块索引 `row * columns + col` */
        val blocks: Map<Int, Block>,
    ) {
        val columns: Int get() = frameW / blockW
        val rows: Int get() = frameH / blockH
        val blockCount: Int get() = columns * rows

        /** 把本帧的变化块画进整帧 RGBA 缓冲区（长度须为 frameW*frameH*4） */
        fun applyTo(canvas: ByteArray) {
            require(canvas.size >= frameW * frameH * 4) {
                "画布太小：需要 ${frameW * frameH * 4}，实际 ${canvas.size}"
            }
            for (b in blocks.values) blitBlock(b, canvas)
        }

        /** 用本帧的新哈希更新客户端哈希表，供下一次增量请求使用 */
        fun updateHashes(hashes: IntArray) {
            for ((idx, b) in blocks) if (idx in hashes.indices) hashes[idx] = b.hash
        }

        private fun blitBlock(b: Block, canvas: ByteArray) {
            val blockRowBytes = blockW * 4
            for (j in 0 until blockH) {
                val dy = b.y + j
                if (dy < 0 || dy >= frameH) continue
                val dst = (dy * frameW + b.x) * 4
                val src = j * blockRowBytes
                if (b.x < 0 || b.x + blockW > frameW) continue
                if (src + blockRowBytes > b.rgba.size) continue
                System.arraycopy(b.rgba, src, canvas, dst, blockRowBytes)
            }
        }
    }

    /** 单个块：位置 + 内容哈希 + RGBA 像素 */
    data class Block(val x: Int, val y: Int, val hash: Int, val rgba: ByteArray) {
        override fun equals(other: Any?): Boolean =
            other is Block && x == other.x && y == other.y && hash == other.hash &&
                rgba.contentEquals(other.rgba)

        override fun hashCode(): Int =
            ((x * 31 + y) * 31 + hash) * 31 + rgba.contentHashCode()
    }

    /** 响应不是合法 OSD 帧 */
    class OsdParseException(message: String) : Exception(message)

    /**
     * 解析一次 OSD 响应。
     * @throws OsdParseException 帧头非法、或块数据被截断
     */
    fun parse(raw: ByteArray): Frame {
        if (raw.size < 8) throw OsdParseException("响应过短（${raw.size} 字节），连帧头都不完整")

        val frameW = u16(raw, 0)
        val frameH = u16(raw, 2)
        val blockW = u16(raw, 4)
        val blockH = u16(raw, 6)

        if (frameW <= 0 || frameH <= 0 || blockW <= 0 || blockH <= 0) {
            throw OsdParseException("帧头非法：${frameW}x$frameH 块 ${blockW}x$blockH")
        }
        if (frameW % blockW != 0 || frameH % blockH != 0) {
            throw OsdParseException("帧尺寸 ${frameW}x$frameH 不能被块 ${blockW}x$blockH 整除")
        }

        val columns = frameW / blockW
        val perBlock = blockW * blockH * 4
        val blocks = LinkedHashMap<Int, Block>()
        var idx = 8

        while (true) {
            if (idx + 4 > raw.size) throw OsdParseException("块头被截断于偏移 $idx")
            val x = u16(raw, idx)
            val y = u16(raw, idx + 2)
            idx += 4
            if (x == END_MARK && y == END_MARK) break

            if (idx + 4 > raw.size) throw OsdParseException("块($x,$y) 的哈希被截断于偏移 $idx")
            val hash = u32(raw, idx)
            idx += 4

            if (idx + perBlock > raw.size) {
                throw OsdParseException(
                    "块($x,$y) 像素被截断：需要 $perBlock 字节，仅剩 ${raw.size - idx}",
                )
            }
            val px = raw.copyOfRange(idx, idx + perBlock)
            idx += perBlock

            val blockIndex = (y / blockH) * columns + (x / blockW)
            blocks[blockIndex] = Block(x, y, hash, px)
        }

        return Frame(frameW, frameH, blockW, blockH, blocks)
    }

    /**
     * 拼出下一次请求要发的 `hashs` 表单值。
     *
     * @param hashes 当前持有的各块哈希；不足 [blockCount] 的位置按 0（= 请求全量）补齐
     */
    fun buildHashsParam(hashes: IntArray, blockCount: Int = hashes.size): String {
        val n = minOf(blockCount, MAX_BLOCKS)
        val sb = StringBuilder(n * 8)
        for (i in 0 until n) {
            val h = if (i < hashes.size) hashes[i] else 0
            sb.append(HEX8[h ushr 28 and 0xF])
            sb.append(HEX8[h ushr 24 and 0xF])
            sb.append(HEX8[h ushr 20 and 0xF])
            sb.append(HEX8[h ushr 16 and 0xF])
            sb.append(HEX8[h ushr 12 and 0xF])
            sb.append(HEX8[h ushr 8 and 0xF])
            sb.append(HEX8[h ushr 4 and 0xF])
            sb.append(HEX8[h and 0xF])
        }
        return sb.toString()
    }

    /** 新建一块全透明的 RGBA 画布 */
    fun newCanvas(frameW: Int = EXPECTED_FRAME_W, frameH: Int = EXPECTED_FRAME_H): ByteArray =
        ByteArray(frameW * frameH * 4)

    /** 把 RGBA 画布的第 (x,y) 个像素读成 0xRRGGBB */
    fun pixelRgb(canvas: ByteArray, frameW: Int, x: Int, y: Int): Int {
        val o = (y * frameW + x) * 4
        return ((canvas[o].toInt() and 0xFF) shl 16) or
            ((canvas[o + 1].toInt() and 0xFF) shl 8) or
            (canvas[o + 2].toInt() and 0xFF)
    }

    private val HEX8 = "0123456789abcdef".toCharArray()

    private fun u16(b: ByteArray, i: Int): Int =
        ((b[i].toInt() and 0xFF) shl 8) or (b[i + 1].toInt() and 0xFF)

    private fun u32(b: ByteArray, i: Int): Int =
        ((b[i].toInt() and 0xFF) shl 24) or ((b[i + 1].toInt() and 0xFF) shl 16) or
            ((b[i + 2].toInt() and 0xFF) shl 8) or (b[i + 3].toInt() and 0xFF)

    // ------------------------------------------------------------------
    // 供测试与 PC 端复用的构造工具
    // ------------------------------------------------------------------

    /**
     * 按协议格式**造**一个 OSD 响应（用于单元测试与 mock 相机）。
     *
     * @param blocks 要写入的块：`(x, y, hash, rgba)`；rgba 长度须为 blockW*blockH*4
     */
    fun buildFrame(
        frameW: Int = EXPECTED_FRAME_W,
        frameH: Int = EXPECTED_FRAME_H,
        blockW: Int = EXPECTED_BLOCK_W,
        blockH: Int = EXPECTED_BLOCK_H,
        blocks: List<Block>,
    ): ByteArray {
        require(frameW % blockW == 0 && frameH % blockH == 0) { "帧尺寸必须能被块整除" }
        val perBlock = blockW * blockH * 4
        val out = java.io.ByteArrayOutputStream()
        out.write(u16Bytes(frameW))
        out.write(u16Bytes(frameH))
        out.write(u16Bytes(blockW))
        out.write(u16Bytes(blockH))
        for (b in blocks) {
            require(b.rgba.size == perBlock) { "块(${b.x},${b.y}) 像素长度 ${b.rgba.size} != $perBlock" }
            out.write(u16Bytes(b.x))
            out.write(u16Bytes(b.y))
            out.write(u32Bytes(b.hash))
            out.write(b.rgba)
        }
        out.write(u16Bytes(END_MARK))
        out.write(u16Bytes(END_MARK))
        return out.toByteArray()
    }

    /** 造一块纯色 RGBA */
    fun solidBlock(x: Int, y: Int, hash: Int, blockW: Int, blockH: Int, r: Int, g: Int, b: Int): Block {
        val px = ByteArray(blockW * blockH * 4)
        var i = 0
        repeat(blockW * blockH) {
            px[i++] = r.toByte()
            px[i++] = g.toByte()
            px[i++] = b.toByte()
            px[i++] = 0xFF.toByte()
        }
        return Block(x, y, hash, px)
    }

    private fun u16Bytes(v: Int) = byteArrayOf((v ushr 8 and 0xFF).toByte(), (v and 0xFF).toByte())

    private fun u32Bytes(v: Int) = byteArrayOf(
        (v ushr 24 and 0xFF).toByte(), (v ushr 16 and 0xFF).toByte(),
        (v ushr 8 and 0xFF).toByte(), (v and 0xFF).toByte(),
    )
}
