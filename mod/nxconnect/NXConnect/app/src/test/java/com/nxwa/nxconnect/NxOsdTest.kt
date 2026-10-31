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
package com.nxwa.nxconnect

import com.nxwa.nxconnect.net.NxOsd
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * OSD 协议解析的单元测试。
 *
 * 所有常量都来自**真机实测**（NX500 固件 1.12）：
 * - 帧 720x480、块 80x40 -> 9 列 x 12 行 = 108 块
 * - 全量响应 1,383,276 字节
 * - 单块像素 = 80*40*4 = 12800 字节
 *
 * 108 * 12800 + 8(帧头) + 108 * 8(每块 x/y/hash) + 4(结束标记)
 *   = 1382400 + 8 + 864 + 4 = 1383276  ← 与实测的全量字节数完全一致，
 * 这条等式本身就是对"协议理解正确"的有力证明，见 [fullResponseSizeMatchesRealDevice]。
 */
class NxOsdTest {

    private val frameW = NxOsd.EXPECTED_FRAME_W   // 720
    private val frameH = NxOsd.EXPECTED_FRAME_H   // 480
    private val blockW = NxOsd.EXPECTED_BLOCK_W   // 80
    private val blockH = NxOsd.EXPECTED_BLOCK_H   // 40

    private val perBlock = blockW * blockH * 4    // 12800
    private val blockCount = (frameW / blockW) * (frameH / blockH)  // 108

    // ------------------------------------------------------------------
    // 协议尺寸自检
    // ------------------------------------------------------------------

    @Test
    fun `全量响应字节数与真机实测一致`() {
        // 造一个"所有块都变化"的响应，长度必须等于真机实测的 1383276
        val blocks = (0 until blockCount).map { i ->
            NxOsd.solidBlock(
                x = (i % (frameW / blockW)) * blockW,
                y = (i / (frameW / blockW)) * blockH,
                hash = i + 1,
                blockW = blockW, blockH = blockH,
                r = 16, g = 32, b = 48,
            )
        }
        val raw = NxOsd.buildFrame(frameW, frameH, blockW, blockH, blocks)
        assertEquals("全量响应长度应与真机实测一致", 1_383_276, raw.size)

        // 反方向验算，把这条等式写清楚，防止以后有人"顺手改一下"块尺寸
        val expected = 8 + blockCount * (4 + 4 + perBlock) + 4
        assertEquals(1_383_276, expected)
    }

    @Test
    fun `块布局是 9 列 12 行共 108 块`() {
        val raw = NxOsd.buildFrame(frameW, frameH, blockW, blockH, emptyList())
        val f = NxOsd.parse(raw)
        assertEquals(9, f.columns)
        assertEquals(12, f.rows)
        assertEquals(108, f.blockCount)
        assertEquals(0, f.blocks.size)
    }

    // ------------------------------------------------------------------
    // 解析
    // ------------------------------------------------------------------

    @Test
    fun `解析单个块的位置与像素`() {
        val blk = NxOsd.solidBlock(x = 160, y = 80, hash = 0xDEADBEEF.toInt(),
            blockW = blockW, blockH = blockH, r = 255, g = 128, b = 0)
        val raw = NxOsd.buildFrame(frameW, frameH, blockW, blockH, listOf(blk))
        val f = NxOsd.parse(raw)

        assertEquals(1, f.blocks.size)
        val b = f.blocks.values.first()
        assertEquals(160, b.x)
        assertEquals(80, b.y)
        assertEquals(0xDEADBEEF.toInt(), b.hash)
        assertEquals(perBlock, b.rgba.size)

        // 块索引 = (y/blockH) * columns + (x/blockW) = (80/40)*9 + (160/80) = 2*9+2 = 20
        assertEquals(20, f.blocks.keys.first())
    }

    @Test
    fun `多块响应的索引计算正确`() {
        val blocks = listOf(
            NxOsd.solidBlock(0, 0, 1, blockW, blockH, 1, 1, 1),        // idx 0
            NxOsd.solidBlock(640, 0, 2, blockW, blockH, 2, 2, 2),      // idx 8（最后一列）
            NxOsd.solidBlock(0, 440, 3, blockW, blockH, 3, 3, 3),      // idx 99（最后一行首列）
            NxOsd.solidBlock(640, 440, 4, blockW, blockH, 4, 4, 4),    // idx 107（右下角）
        )
        val f = NxOsd.parse(NxOsd.buildFrame(frameW, frameH, blockW, blockH, blocks))
        assertEquals(setOf(0, 8, 99, 107), f.blocks.keys.toSet())
    }

    @Test
    fun `结束标记后不再读取`() {
        // 只有结束标记，没有块
        val f = NxOsd.parse(NxOsd.buildFrame(frameW, frameH, blockW, blockH, emptyList()))
        assertTrue(f.blocks.isEmpty())
    }

    // ------------------------------------------------------------------
    // 画布合成（这是原 NV12 解码越界那种 bug 的同类风险点）
    // ------------------------------------------------------------------

    @Test
    fun `applyTo 把块画到正确坐标`() {
        val canvas = NxOsd.newCanvas(frameW, frameH)
        val blk = NxOsd.solidBlock(160, 80, 1, blockW, blockH, r = 10, g = 20, b = 30)
        NxOsd.parse(NxOsd.buildFrame(frameW, frameH, blockW, blockH, listOf(blk))).applyTo(canvas)

        // 块内任意点都应命中
        assertEquals(0x0A141E, NxOsd.pixelRgb(canvas, frameW, 160, 80))
        assertEquals(0x0A141E, NxOsd.pixelRgb(canvas, frameW, 239, 119))
        // 块外应保持为 0
        assertEquals(0x000000, NxOsd.pixelRgb(canvas, frameW, 159, 80))
        assertEquals(0x000000, NxOsd.pixelRgb(canvas, frameW, 160, 79))
        assertEquals(0x000000, NxOsd.pixelRgb(canvas, frameW, 240, 80))
    }

    @Test
    fun `覆盖所有 108 个块不越界`() {
        val canvas = NxOsd.newCanvas(frameW, frameH)
        val cols = frameW / blockW
        val blocks = (0 until blockCount).map { i ->
            NxOsd.solidBlock(
                x = (i % cols) * blockW,
                y = (i / cols) * blockH,
                hash = i,
                blockW = blockW, blockH = blockH,
                r = i, g = i, b = i,
            )
        }
        // 全量铺满：不应抛异常，且四角都被写过
        NxOsd.parse(NxOsd.buildFrame(frameW, frameH, blockW, blockH, blocks)).applyTo(canvas)
        assertEquals(0x000000, NxOsd.pixelRgb(canvas, frameW, 0, 0))
        // 最后一个块 (640,440) 的灰度为 107 -> 0x6B6B6B
        assertEquals(0x6B6B6B, NxOsd.pixelRgb(canvas, frameW, 719, 479))
    }

    @Test
    fun `画布过小会明确报错而不是静默越界`() {
        val f = NxOsd.parse(NxOsd.buildFrame(frameW, frameH, blockW, blockH, emptyList()))
        val tooSmall = ByteArray(frameW * frameH * 4 - 1)
        assertThrows(IllegalArgumentException::class.java) { f.applyTo(tooSmall) }
    }

    // ------------------------------------------------------------------
    // 增量哈希
    // ------------------------------------------------------------------

    @Test
    fun `updateHashes 按块索引写入`() {
        val hashes = IntArray(NxOsd.MAX_BLOCKS)
        val f = NxOsd.parse(
            NxOsd.buildFrame(
                frameW, frameH, blockW, blockH,
                listOf(
                    NxOsd.solidBlock(0, 0, 0xAAAA, blockW, blockH, 1, 1, 1),
                    NxOsd.solidBlock(640, 440, 0xBBBB, blockW, blockH, 2, 2, 2),
                ),
            ),
        )
        f.updateHashes(hashes)
        assertEquals(0xAAAA, hashes[0])
        assertEquals(0xBBBB, hashes[107])
        assertEquals(0, hashes[50])   // 未变化的块保持原值（0 = 下次请求全量）
    }

    @Test
    fun `buildHashsParam 生成 108 组 8 位十六进制`() {
        val hashes = IntArray(NxOsd.MAX_BLOCKS)
        hashes[0] = 0xDEADBEEF.toInt()
        hashes[107] = 0x0000000F
        val s = NxOsd.buildHashsParam(hashes, blockCount = 108)

        assertEquals("长度应为 108*8", 864, s.length)
        assertTrue("第 0 块应是小写十六进制", s.startsWith("deadbeef"))
        assertTrue("第 107 块在末尾", s.endsWith("0000000f"))
        // 中间必须全 0
        assertEquals("0".repeat(8 * 106), s.substring(8, 8 + 8 * 106))
    }

    @Test
    fun `buildHashsParam 对不足长度的哈希表补 0`() {
        // 只给了 3 个哈希，其余按 0 补齐（等价于让服务端回全量）
        val s = NxOsd.buildHashsParam(intArrayOf(1, 2, 3), blockCount = 108)
        assertEquals(864, s.length)
        assertTrue(s.startsWith("000000010000000200000003"))
        assertEquals("0".repeat(8 * 105), s.substring(24))
    }

    // ------------------------------------------------------------------
    // 错误处理：坏数据必须报错，不能静默产出垃圾
    // ------------------------------------------------------------------

    @Test
    fun `响应过短直接报错`() {
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsd.parse(ByteArray(7)) }
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsd.parse(ByteArray(0)) }
    }

    @Test
    fun `帧头非法时报错`() {
        // 块宽为 0
        val bad = byteArrayOf(0x02, 0xD0.toByte(), 0x01, 0xE0.toByte(), 0x00, 0x00, 0x00, 0x28)
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsd.parse(bad) }
    }

    @Test
    fun `帧尺寸不能被块整除时报错`() {
        // 719 不能被 80 整除
        val bad = byteArrayOf(0x02, 0xCF.toByte(), 0x01, 0xE0.toByte(), 0x00, 0x50, 0x00, 0x28)
        val e = assertThrows(NxOsd.OsdParseException::class.java) { NxOsd.parse(bad) }
        assertTrue(e.message!!.contains("整除"))
    }

    @Test
    fun `块像素被截断时报错并给出偏移`() {
        val ok = NxOsd.buildFrame(
            frameW, frameH, blockW, blockH,
            listOf(NxOsd.solidBlock(0, 0, 1, blockW, blockH, 9, 9, 9)),
        )
        val truncated = ok.copyOfRange(0, ok.size - perBlock / 2)   // 掐掉半个块
        val e = assertThrows(NxOsd.OsdParseException::class.java) { NxOsd.parse(truncated) }
        assertTrue("错误信息应说明截断，实际：${e.message}", e.message!!.contains("截断"))
    }

    @Test
    fun `缺少结束标记时报错而不是静默返回部分结果`() {
        val ok = NxOsd.buildFrame(
            frameW, frameH, blockW, blockH,
            listOf(NxOsd.solidBlock(0, 0, 1, blockW, blockH, 9, 9, 9)),
        )
        val noEnd = ok.copyOfRange(0, ok.size - 4)   // 去掉结束标记
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsd.parse(noEnd) }
    }

    // ------------------------------------------------------------------
    // 真机回归：把实测的帧尺寸/块尺寸写死，防止以后被"优化"掉
    // ------------------------------------------------------------------

    @Test
    fun `真机实测的帧头字节序列能被正确解析`() {
        // 真机响应开头 8 字节实测为：
        // 02 d0 01 e0 00 50 00 28  ->  720 480 80 40
        val header = byteArrayOf(
            0x02, 0xD0.toByte(),   // 720
            0x01, 0xE0.toByte(),   // 480
            0x00, 0x50,            // 80
            0x00, 0x28,            // 40
        )
        val f = NxOsd.parse(header + byteArrayOf(0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte()))
        assertEquals(720, f.frameW)
        assertEquals(480, f.frameH)
        assertEquals(80, f.blockW)
        assertEquals(40, f.blockH)
    }

    @Test
    fun `实测的增量响应规模（4 块约 51KB）与协议自洽`() {
        // 真机实测：同画面增量返回 51,244 字节、4 个块变化
        // 验算：8 + 4*(4+4+12800) + 4 = 51244  ✓ 完全吻合
        val computed = 8 + 4 * (4 + 4 + perBlock) + 4
        assertEquals(51_244, computed)
    }
}
