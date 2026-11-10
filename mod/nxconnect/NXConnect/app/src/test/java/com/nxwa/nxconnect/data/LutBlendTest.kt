// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.StringReader
import java.io.StringWriter
import java.security.MessageDigest

class LutBlendTest {
    private fun fixture(second: Boolean): CubeLut.Data {
        val grid = 9
        val rgb = FloatArray(grid * grid * grid * 3)
        val divisor = grid - 1f
        var offset = 0
        for (b in 0 until grid) for (g in 0 until grid) for (r in 0 until grid) {
            val red = r / divisor
            val green = g / divisor
            val blue = b / divisor
            rgb[offset++] = if (second) 1f - blue * 0.82f else red * 0.9f + blue * 0.1f
            rgb[offset++] = if (second) green * 0.74f + red * 0.2f else green * 0.85f + red * 0.1f
            rgb[offset++] = if (second) blue * 0.66f + 0.2f else blue
        }
        return CubeLut.Data(grid, if (second) "B" else "A",
            listOf(0.0, 0.0, 0.0), listOf(1.0, 1.0, 1.0), rgb)
    }

    @Test fun scannerParsesBomCommentsCrlfAndDomain() {
        val cube = "\uFEFFTITLE \"扫描测试\"\r\n" +
            "# comment\r\n" +
            "DOMAIN_MIN 0 0 0\r\n" +
            "DOMAIN_MAX 1 1 1\r\n" +
            "LUT_3D_SIZE 2\r\n" +
            "0 0 0\r\n1 0 0\r\n0 1 0\r\n1 1 0\r\n" +
            "0 0 1\r\n1 0 1\r\n0 1 1\r\n1 1 1 # last\r\n"
        val info = StringReader(cube).use(CubeLut::inspect)
        val data = StringReader(cube).use(CubeLut::read)
        assertEquals(2, info.grid)
        assertEquals("扫描测试", data.title)
        assertEquals(listOf(0.0, 0.0, 0.0), data.domainMin)
        assertEquals(listOf(1.0, 1.0, 1.0), data.domainMax)
        assertArrayEquals(CubeLut.identity(2).rgb, data.rgb, 0f)
    }

    @Test fun boundariesEqualResampledSources() {
        val a = fixture(false)
        val b = fixture(true)
        assertArrayEquals(CubeLut.resample(a).rgb,
            CubeLut.blend(a, b, 0.0).rgb, 1e-6f)
        assertArrayEquals(CubeLut.resample(b).rgb,
            CubeLut.blend(a, b, 1.0).rgb, 1e-6f)
    }

    @Test fun swappingSourcesAndInvertingRatioIsEquivalent() {
        val a = CubeLut.resample(fixture(false))
        val b = CubeLut.resample(fixture(true))
        assertArrayEquals(CubeLut.blendLattice(a, b, 0.27).rgb,
            CubeLut.blendLattice(b, a, 0.73).rgb, 1e-6f)
    }

    @Test fun sameSourcePreservesLattice() {
        val a = CubeLut.resample(fixture(false))
        assertArrayEquals(a.rgb, CubeLut.blendLattice(a, a, 0.31).rgb, 0f)
        assertArrayEquals(a.rgb, CubeLut.blendLattice(a, a, 0.83).rgb, 0f)
    }

    @Test fun cubeSerializationRoundTripsAtBlendGrid() {
        val blended = CubeLut.blend(fixture(false), fixture(true), 0.4, title = "测试混合")
        val text = StringWriter().also { writer -> CubeLut.writeCube(blended, writer) }.toString()
        val parsed = StringReader(text).use(CubeLut::read)
        assertEquals(CubeLut.BLEND_GRID, parsed.grid)
        assertEquals("测试混合", parsed.title)
        assertArrayEquals(blended.rgb, parsed.rgb, 2e-7f)
    }

    @Test fun noncanonicalDomainIsRejected() {
        val shifted = fixture(false).copy(
            domainMin = listOf(0.1, 0.0, 0.0),
        )
        assertThrows(IllegalArgumentException::class.java) {
            CubeLut.resample(shifted)
        }
    }

    @Test fun roundCompilerMatchesTheDesktopBlendContract() {
        val blended = CubeLut.blend(fixture(false), fixture(true), 0.4, title = "测试混合")
        val table = CubeLut.compileD4(blended, roundNearest = true)
        val digest = MessageDigest.getInstance("SHA-256").digest(table)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
        assertEquals("1df624a173b91c5160e8672c0ff5d882163bde5a90b6f0c61fad401dead4d7ee", digest)
    }
}
