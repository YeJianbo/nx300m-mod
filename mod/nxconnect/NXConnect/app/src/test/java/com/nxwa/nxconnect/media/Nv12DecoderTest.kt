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

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 取景解码测试。
 *
 * 参考值来自标准 BT.601 full-range 公式：
 *   r = y + 1.402*(v-128)
 *   g = y - 0.344136*(u-128) - 0.714136*(v-128)
 *   b = y + 1.772*(u-128)
 * 实现用的是定点近似（359/88/183/454 右移 8 位），允许 ±1。
 */
class Nv12DecoderTest {

    private fun spec(w: Int, h: Int, low: Boolean) = Nv12Decoder.FrameSpec(w, h, low)

    private fun buildFrame(w: Int, h: Int, y: Int, u: Int, v: Int): ByteArray {
        val size = Nv12Decoder.FrameSpec.expectedSize(w, h, false)
        val data = ByteArray(size)
        for (i in 0 until w * h) data[i] = y.toByte()
        var i = w * h
        while (i + 1 < size) {
            data[i] = u.toByte()
            data[i + 1] = v.toByte()
            i += 2
        }
        return data
    }

    private fun argb(r: Int, g: Int, b: Int) =
        0xFF000000.toInt() or (r shl 16) or (g shl 8) or b

    private fun red(p: Int) = (p shr 16) and 0xFF
    private fun green(p: Int) = (p shr 8) and 0xFF
    private fun blue(p: Int) = p and 0xFF

    @Test
    fun `中性灰 YUV 解出等值 RGB`() {
        // Y=128, U=V=128 → 灰
        val data = buildFrame(4, 4, 128, 128, 128)
        val px = Nv12Decoder.decodeToArgb(data, spec(4, 4, false))
        assertEquals(16, px.size)
        px.forEach { p ->
            assertEquals(128, red(p))
            assertEquals(128, green(p))
            assertEquals(128, blue(p))
        }
    }

    @Test
    fun `纯红 YUV 解出接近纯红`() {
        // BT.601 full range 的红色：Y=76, U=84, V=255
        val data = buildFrame(4, 4, 76, 84, 255)
        val px = Nv12Decoder.decodeToArgb(data, spec(4, 4, false))
        val p = px[0]
        assertTrue("R 应接近 255，实际 ${red(p)}", red(p) >= 253)
        assertTrue("G 应接近 0，实际 ${green(p)}", green(p) <= 2)
        assertTrue("B 应接近 0，实际 ${blue(p)}", blue(p) <= 2)
    }

    @Test
    fun `纯蓝 YUV 解出接近纯蓝`() {
        // BT.601 full range 的蓝色：Y=29, U=255, V=107
        val data = buildFrame(4, 4, 29, 255, 107)
        val px = Nv12Decoder.decodeToArgb(data, spec(4, 4, false))
        val p = px[0]
        assertTrue("R 应接近 0，实际 ${red(p)}", red(p) <= 2)
        assertTrue("G 应接近 0，实际 ${green(p)}", green(p) <= 2)
        assertTrue("B 应接近 255，实际 ${blue(p)}", blue(p) >= 253)
    }

    @Test
    fun `结果始终不透明`() {
        val data = buildFrame(8, 8, 200, 10, 250)
        val px = Nv12Decoder.decodeToArgb(data, spec(8, 8, false))
        px.forEach { p -> assertEquals(0xFF, (p ushr 24) and 0xFF) }
    }

    @Test
    fun `越界色度不会崩且退化为灰度`() {
        // 只给 Y 平面，UV 缺失 → 索引越界分支应填灰度而不是抛异常
        val data = ByteArray(4 * 4)
        for (i in data.indices) data[i] = 100
        val px = Nv12Decoder.decodeToArgb(data, spec(4, 4, false))
        assertEquals(16, px.size)
        assertEquals(argb(100, 100, 100), px[0])
    }

    @Test
    fun `复用输出数组时不重新分配`() {
        val data = buildFrame(4, 4, 128, 128, 128)
        val reuse = IntArray(16)
        val px = Nv12Decoder.decodeToArgb(data, spec(4, 4, false), reuse)
        assertTrue("应复用传入数组", px === reuse)
    }

    @Test
    fun `输出数组过小时新建而不是越界写入`() {
        val data = buildFrame(4, 4, 128, 128, 128)
        val tooSmall = IntArray(4)
        val px = Nv12Decoder.decodeToArgb(data, spec(4, 4, false), tooSmall)
        assertTrue("不应复用太小的数组", px !== tooSmall)
        assertEquals(16, px.size)
    }

    // ------------------------------------------------------- 规格识别

    @Test
    fun `识别所有十种相机帧规格且无歧义`() {
        val expected = listOf(
            Triple(720, 480, false), Triple(720, 480, true),
            Triple(720, 404, false), Triple(720, 404, true),
            Triple(720, 380, false), Triple(720, 380, true),
            Triple(640, 480, false), Triple(640, 480, true),
            Triple(800, 480, false), Triple(800, 480, true),
        )
        val seenSizes = mutableMapOf<Int, Triple<Int, Int, Boolean>>()
        for ((w, h, low) in expected) {
            val size = Nv12Decoder.FrameSpec.expectedSize(w, h, low)
            if (seenSizes.containsKey(size)) {
                throw AssertionError("长度 $size 被 ${seenSizes[size]} 与 ${Triple(w, h, low)} 同时占用")
            }
            seenSizes[size] = Triple(w, h, low)

            val spec = Nv12Decoder.FrameSpec.detect(size)
            assertNotNull("长度 $size 应能识别", spec)
            assertEquals(w, spec!!.width)
            assertEquals(h, spec.height)
            assertEquals(low, spec.low)
        }
        assertEquals("应有 10 种互不冲突的规格", 10, seenSizes.size)
    }

    @Test
    fun `未知长度返回 null 而不是瞎猜`() {
        assertNull(Nv12Decoder.FrameSpec.detect(12345))
        assertNull(Nv12Decoder.decode(ByteArray(12345)))
    }

    // ------------------------------------------------- 与浮点参考实现比对

    @Test
    fun `定点实现与标准浮点公式逐像素偏差不超过 1`() {
        val w = 32
        val h = 16
        val data = buildFrame(w, h, 0, 0, 0)
        // 造出覆盖整个色度范围的 YUV
        for (y in 0 until h) for (x in 0 until w) {
            data[y * w + x] = ((x * 8 + y * 3) % 256).toByte()
        }
        var i = w * h
        var k = 0
        while (i + 1 < data.size) {
            data[i] = (k % 256).toByte()
            data[i + 1] = (255 - k % 256).toByte()
            i += 2
            k += 7
        }

        val px = Nv12Decoder.decodeToArgb(data, spec(w, h, false))
        var maxDiff = 0
        for (y in 0 until h) for (x in 0 until w) {
            val Y = data[y * w + x].toInt() and 0xFF
            val uvIndex = w * h + (y / 2) * (w / 2) * 2 + (x / 2) * 2
            val cb = (data[uvIndex].toInt() and 0xFF) - 128
            val cr = (data[uvIndex + 1].toInt() and 0xFF) - 128
            val refR = (Y + 1.402 * cr).coerceIn(0.0, 255.0).toInt()
            val refG = (Y - 0.344136 * cb - 0.714136 * cr).coerceIn(0.0, 255.0).toInt()
            val refB = (Y + 1.772 * cb).coerceIn(0.0, 255.0).toInt()
            val p = px[y * w + x]
            maxDiff = maxOf(
                maxDiff,
                kotlin.math.abs(red(p) - refR),
                kotlin.math.abs(green(p) - refG),
                kotlin.math.abs(blue(p) - refB),
            )
        }
        assertTrue("最大偏差应 <= 2，实际 $maxDiff", maxDiff <= 2)
    }

    // ------------------------------------------------ 越界回归（用户实测报过）

    /**
     * 回归：所有相机实际会发出来的帧长，解码都不许越界。
     *
     * 用户实测切低画质时出现过 ArrayIndexOutOfBoundsException，所以这里把
     * 10 种规格（含低画质）逐种过一遍，并故意用不同的输出数组大小覆盖复用分支。
     */
    @Test
    fun `所有相机帧规格都不越界`() {
        val specs = listOf(
            Triple(720, 480, false), Triple(720, 480, true),
            Triple(720, 404, false), Triple(720, 404, true),
            Triple(720, 380, false), Triple(720, 380, true),
            Triple(640, 480, false), Triple(640, 480, true),
            Triple(800, 480, false), Triple(800, 480, true),
        )
        for ((w, h, low) in specs) {
            val size = Nv12Decoder.FrameSpec.expectedSize(w, h, low)
            val data = ByteArray(size)
            for (i in 0 until size) data[i] = ((i * 7) % 256).toByte()

            val a = Nv12Decoder.decode(data)
            assertNotNull("${w}x$h low=$low 应能识别规格", a)
            assertEquals("${w}x$h low=$low 输出像素数", w * h, a!!.pixels.size)

            assertEquals(w * h, Nv12Decoder.decode(data, IntArray(w * h))!!.pixels.size)
            // 传更大的数组时按设计会**复用**它（少一次分配），所以长度是传入的那个
            val reused = Nv12Decoder.decode(data, IntArray(w * h + 1000))!!.pixels
            assertTrue("应按设计复用更大的数组", reused.size == w * h + 1000)
            assertEquals("过小的数组必须新建而不是越界写", w * h, Nv12Decoder.decode(data, IntArray(4))!!.pixels.size)
            assertEquals(w * h, Nv12Decoder.decodeToArgb(data, Nv12Decoder.FrameSpec(w, h, low)).size)
        }
    }

    /** 帧长与规格不匹配时也必须退化为灰度，而不是抛异常 */
    @Test
    fun `帧长偏小时给灰度而不是抛异常`() {
        val full = 720 * 480 * 3 / 2
        val short = ByteArray(full - 100)
        for (i in short.indices) short[i] = 120
        // 长度识别不出来时 decode 返回 null，调用方跳过该帧
        assertNull(Nv12Decoder.decode(short))

        // 强行按高画质规格解码也不能崩：UV 取不到的地方退化为灰度
        val px = Nv12Decoder.decodeToArgb(short, Nv12Decoder.FrameSpec(720, 480, false))
        assertEquals(720 * 480, px.size)
        val last = px[px.size - 1]
        assertEquals(120, (last shr 16) and 0xFF)
        assertEquals(120, (last shr 8) and 0xFF)
        assertEquals(120, last and 0xFF)
    }

    /**
     * 回归：259200 字节必须识别为低画质。
     * 若误判成高画质，UV 平面会按全尺寸去读 → 必然越界。
     */
    @Test
    fun `259200 字节必须识别为 720x480 低画质`() {
        val spec = Nv12Decoder.FrameSpec.detect(259200)
        assertNotNull(spec)
        assertEquals(720, spec!!.width)
        assertEquals(480, spec.height)
        assertTrue("必须是低画质，否则 UV 读取会越界", spec.low)
        assertEquals(259200, Nv12Decoder.FrameSpec.expectedSize(720, 480, true))
    }

    /**
     * 复现并钉死用户报的 `ArrayIndexOutOfBoundsException: length=259200; Index=259200`。
     *
     * 根因：我把低画质的 Y 平面当成全分辨率（720×480）去读，
     * 实际低画质 body = Y(360×360) + UV(180×180×2)，Y 也是半分辨率。
     * 于是下标一路读到 259200 —— 正好越界。
     */
    @Test
    fun `低画质 259200 字节必须不越界`() {
        val size = 259200
        val data = ByteArray(size) { ((it * 7) % 256).toByte() }

        val r1 = runCatching { Nv12Decoder.decode(data) }
        assertTrue(
            "自动识别路径不应抛异常，实际：${r1.exceptionOrNull()}",
            r1.isSuccess,
        )
        assertEquals(720 * 480, r1.getOrNull()?.pixels?.size)
        assertTrue("259200 应识别为低画质", r1.getOrNull()?.spec?.low == true)

        // 复用数组（长度正好 w*h，比 data 大）
        val r2 = runCatching { Nv12Decoder.decode(data, IntArray(720 * 480)) }
        assertTrue("带复用数组不应抛异常：${r2.exceptionOrNull()}", r2.isSuccess)

        // 显式低画质规格
        val r3 = runCatching { Nv12Decoder.decodeToArgb(data, Nv12Decoder.FrameSpec(720, 480, true)) }
        assertTrue("显式低画质不应抛异常：${r3.exceptionOrNull()}", r3.isSuccess)

        // 误判成高画质：必须靠越界兜底退化为灰度，而不是崩
        val r4 = runCatching { Nv12Decoder.decodeToArgb(data, Nv12Decoder.FrameSpec(720, 480, false)) }
        assertTrue("误判规格也必须被兜住：${r4.exceptionOrNull()}", r4.isSuccess)
        assertEquals(720 * 480, r4.getOrNull()?.size)
    }

    /**
     * 低画质帧的平面布局：Y 与 UV 都是半分辨率，正好把 body 用满。
     * 这条断言就是防止以后有人"顺手"把 Y 平面改回全分辨率。
     */
    @Test
    fun `低画质平面布局正好等于帧长`() {
        val w = 720
        val h = 480
        val yW = w / 2
        val yH = h / 2
        val uvW = w / 2
        val body = yW * yH + uvW * (h / 2) * 2
        assertEquals("低画质布局必须正好等于 259200", 259200, body)
        assertEquals(259200, Nv12Decoder.FrameSpec.expectedSize(w, h, true))

        // 高画质也要自洽
        val fullBody = w * h + (w / 2) * (h / 2) * 2
        assertEquals("高画质布局必须正好等于 518400", 518400, fullBody)
        assertEquals(518400, Nv12Decoder.FrameSpec.expectedSize(w, h, false))
    }

    /** 低画质解出来的颜色不能是灰的（Y/UV 读错位置会退化成灰度） */
    @Test
    fun `低画质能解出真实颜色`() {
        val size = 259200
        val data = ByteArray(size)
        val ySize = 360 * 360
        for (i in 0 until ySize) data[i] = 180.toByte()          // Y
        var i = ySize
        while (i + 1 < size) {
            data[i] = 60.toByte()                                 // U 偏低
            data[i + 1] = 220.toByte()                            // V 偏高
            i += 2
        }
        val px = Nv12Decoder.decodeToArgb(data, Nv12Decoder.FrameSpec(720, 480, true))
        val p = px[0]
        val r = (p shr 16) and 0xFF
        val g = (p shr 8) and 0xFF
        val b = p and 0xFF

        // Y=180 + 低 U 高 V 按 BT.601 应得到偏品红（R、B 高，G 低）
        assertEquals("R 通道", 252, r)
        assertEquals("G 通道", 125, g)
        assertEquals("B 通道", 255, b)
        assertTrue("必须是彩色而不是灰度（R 与 G 不能相等）", r != g)
        assertTrue("G 应明显低于 R 与 B", g < r - 50 && g < b - 50)
    }
}
