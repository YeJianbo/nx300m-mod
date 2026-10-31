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
import com.nxwa.nxconnect.net.NxOsdValidator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream

/**
 * OSD **接入层校验**的单元测试。
 *
 * 背景：这段校验原来是写在 Android 的 `OsdView` 里的，93 个 JVM 单测覆盖不到它。
 * 而"坏数据导致越界崩溃"正是本项目历史上真实出过的事故
 * （NV12 解码平面尺寸理解错 → `ArrayIndexOutOfBoundsException`），
 * 所以这部分逻辑必须有 JVM 覆盖。
 *
 * 所有长度断言都用**真机实测值**：
 * - 全量 108 块 = 1,383,276 字节
 * - 增量 4 块 = 51,244 字节
 */
class NxOsdValidatorTest {

    private val fw = NxOsdValidator.FRAME_W
    private val fh = NxOsdValidator.FRAME_H
    private val bw = NxOsdValidator.BLOCK_W
    private val bh = NxOsdValidator.BLOCK_H

    // ------------------------------------------------------------------
    // 尺寸公式：这是整个校验的地基
    // ------------------------------------------------------------------

    @Test
    fun `记录长度等于 8 字节头加块像素`() {
        assertEquals(12_808, NxOsdValidator.RECORD_BYTES)
        assertEquals(80 * 40 * 4, NxOsdValidator.BLOCK_BYTES)
        assertEquals(9, NxOsdValidator.COLUMNS)
        assertEquals(12, NxOsdValidator.ROWS)
        assertEquals(108, NxOsdValidator.BLOCK_COUNT)
    }

    @Test
    fun `真机实测的两个长度都能被整除`() {
        // 全量 108 块
        assertEquals(1_383_276, NxOsdValidator.expectedSizeForBlocks(108))
        assertEquals(0, (1_383_276 - 12) % NxOsdValidator.RECORD_BYTES)
        // 增量 4 块
        assertEquals(51_244, NxOsdValidator.expectedSizeForBlocks(4))
        assertEquals(0, (51_244 - 12) % NxOsdValidator.RECORD_BYTES)
    }

    // ------------------------------------------------------------------
    // 合法输入
    // ------------------------------------------------------------------

    @Test
    fun `零块响应（无变化）是合法的`() {
        val raw = NxOsd.buildFrame(fw, fh, bw, bh, emptyList())
        assertEquals(NxOsdValidator.MIN_BYTES, raw.size)
        val f = NxOsdValidator.parseStrict(raw)
        assertEquals(0, f.blocks.size)
        assertEquals(108, f.blockCount)
    }

    @Test
    fun `单块响应通过校验并保留像素`() {
        val raw = NxOsd.buildFrame(
            fw, fh, bw, bh,
            listOf(NxOsd.solidBlock(160, 80, 0x1234, bw, bh, 10, 20, 30)),
        )
        val f = NxOsdValidator.parseStrict(raw)
        assertEquals(1, f.blocks.size)
        val b = f.blocks.values.first()
        assertEquals(160, b.x)
        assertEquals(80, b.y)
        assertEquals(12_800, b.rgba.size)
    }

    @Test
    fun `满 108 块响应通过校验`() {
        val blocks = (0 until 108).map { i ->
            NxOsd.solidBlock(
                (i % 9) * bw, (i / 9) * bh, i + 1, bw, bh, i, i, i,
            )
        }
        val raw = NxOsd.buildFrame(fw, fh, bw, bh, blocks)
        assertEquals(1_383_276, raw.size)
        val f = NxOsdValidator.parseStrict(raw)
        assertEquals(108, f.blocks.size)
    }

    // ------------------------------------------------------------------
    // 坏输入：每一类都必须被拒（这些是真实的攻击面/崩溃面）
    // ------------------------------------------------------------------

    @Test
    fun `过短响应被拒`() {
        for (n in intArrayOf(0, 1, 7, 11)) {
            assertThrows(NxOsd.OsdParseException::class.java) {
                NxOsdValidator.parseStrict(ByteArray(n))
            }
        }
    }

    @Test
    fun `帧头尺寸不符被拒`() {
        // 正确帧头 720/480/80/40；把帧宽改成 640
        val bad = byteArrayOf(
            0x02, 0x80.toByte(),              // 640（应为 720）
            0x01, 0xE0.toByte(),     // 480
            0x00, 0x50,              // 80
            0x00, 0x28,              // 40
            0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(),
        )
        val e = assertThrows(NxOsd.OsdParseException::class.java) {
            NxOsdValidator.parseStrict(bad)
        }
        assertTrue(e.message!!.contains("帧头"))
    }

    @Test
    fun `块尺寸不符被拒`() {
        val bad = byteArrayOf(
            0x02, 0xD0.toByte(), 0x01, 0xE0.toByte(),
            0x00, 0x40,              // 块宽 64（应为 80）
            0x00, 0x28,
            0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(),
        )
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.parseStrict(bad) }
    }

    @Test
    fun `长度不能被记录大小整除时被拒（截断或多余字节）`() {
        val ok = NxOsd.buildFrame(
            fw, fh, bw, bh,
            listOf(NxOsd.solidBlock(0, 0, 1, bw, bh, 1, 1, 1)),
        )
        // 砍掉一半像素 -> 长度不再整除
        val cut = ok.copyOfRange(0, ok.size - 6_400)
        val e = assertThrows(NxOsd.OsdParseException::class.java) {
            NxOsdValidator.parseStrict(cut)
        }
        assertTrue("应提示截断或多余字节：${e.message}", e.message!!.contains("截断") || e.message!!.contains("多余"))
    }

    @Test
    fun `多余尾部字节被拒`() {
        val ok = NxOsd.buildFrame(fw, fh, bw, bh, emptyList())
        val extra = ok + byteArrayOf(0, 0, 0, 0)
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.parseStrict(extra) }
    }

    @Test
    fun `缺少结束标记被拒`() {
        val ok = NxOsd.buildFrame(fw, fh, bw, bh, emptyList())
        val noEnd = ok.copyOfRange(0, ok.size - 4) + byteArrayOf(0x12, 0x34, 0x56, 0x78)
        val e = assertThrows(NxOsd.OsdParseException::class.java) {
            NxOsdValidator.parseStrict(noEnd)
        }
        assertTrue(e.message!!.contains("结束标记"))
    }

    @Test
    fun `坐标越界被拒`() {
        // x = 720 超出（最大合法 640）
        val raw = frameWithRawFirstBlock(x = 720, y = 0)
        val e = assertThrows(NxOsd.OsdParseException::class.java) {
            NxOsdValidator.parseStrict(raw)
        }
        assertTrue(e.message!!.contains("越界"))
    }

    @Test
    fun `坐标未对齐被拒`() {
        // x = 8 不是块宽 80 的整数倍
        val raw = frameWithRawFirstBlock(x = 8, y = 0)
        val e = assertThrows(NxOsd.OsdParseException::class.java) {
            NxOsdValidator.parseStrict(raw)
        }
        assertTrue(e.message!!.contains("未对齐"))
    }

    @Test
    fun `y 越界被拒`() {
        val raw = frameWithRawFirstBlock(x = 0, y = 480)
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.parseStrict(raw) }
    }

    @Test
    fun `validatePosition 的边界值`() {
        // 合法：右下角最后一个块的起点
        NxOsdValidator.validatePosition(640, 440)
        // 合法：左上角
        NxOsdValidator.validatePosition(0, 0)
        // 非法：刚好超出
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.validatePosition(720, 0) }
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.validatePosition(0, 480) }
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.validatePosition(-80, 0) }
        // 非法：未对齐
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.validatePosition(1, 0) }
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.validatePosition(0, 1) }
    }

    @Test
    fun `接缝上的坐标判定`() {
        // 640 + 80 = 720，正好到边界：640 合法、720 非法（块会超出帧）
        NxOsdValidator.validatePosition(640, 440)
        assertThrows(NxOsd.OsdParseException::class.java) { NxOsdValidator.validatePosition(640, 480) }
    }

    // ------------------------------------------------------------------
    // 与解析器的配合：合法输入必须给出与 NxOsd.parse 一致的结果
    // ------------------------------------------------------------------

    @Test
    fun `严格校验后的结果与原解析器一致`() {
        val blocks = listOf(
            NxOsd.solidBlock(0, 0, 0xAA, bw, bh, 1, 2, 3),
            NxOsd.solidBlock(640, 440, 0xBB, bw, bh, 4, 5, 6),
        )
        val raw = NxOsd.buildFrame(fw, fh, bw, bh, blocks)
        val strict = NxOsdValidator.parseStrict(raw)
        val plain = NxOsd.parse(raw)
        assertEquals(plain.blocks.keys, strict.blocks.keys)
        assertEquals(plain.frameW, strict.frameW)
        assertEquals(plain.blockW, strict.blockW)
    }

    /**
     * 造一个"第一块坐标可以指定成任意值"的响应，用来测坐标校验。
     * 这里直接手写字节，不做合法性过滤 —— 否则测不了非法坐标。
     */
    private fun frameWithRawFirstBlock(x: Int, y: Int): ByteArray {
        val out = ByteArrayOutputStream()
        // write() 接受 Int，直接传运算结果即可；不要加 .toByte()（会变成 Byte 重载）。
        fun u16(v: Int) {
            out.write(v ushr 8 and 0xFF)
            out.write(v and 0xFF)
        }
        fun u32(v: Int) {
            out.write(v ushr 24 and 0xFF)
            out.write(v ushr 16 and 0xFF)
            out.write(v ushr 8 and 0xFF)
            out.write(v and 0xFF)
        }
        u16(fw); u16(fh); u16(bw); u16(bh)
        u16(x); u16(y); u32(0x1234)
        out.write(ByteArray(NxOsdValidator.BLOCK_BYTES))   // 12800 字节像素
        u16(0xFFFF); u16(0xFFFF)
        return out.toByteArray()
    }
}
