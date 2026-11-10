// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.StringReader
import java.nio.ByteBuffer
import java.nio.ByteOrder

class LutCardDataTest {
    @Test fun catalogRoundTripPreservesCameraOrderAndFields() {
        val raw = "base|原图微调|0|0|0|0|0|0|0\nlook-a|甲|1|2|3|4|5|6|7\n"
        val rows = CardLutFormat.parseCatalog(raw)
        assertEquals(listOf("base", "look-a"), rows.map(CardLutFormat.Row::id))
        assertEquals(raw, CardLutFormat.catalogText(rows))
        assertEquals("look-a\n", CardLutFormat.favoriteText(setOf("look-a"), rows))
    }

    @Test fun duplicateIdsAreRejectedBeforeWritingTheCard() {
        val raw = "same|甲|0|0|0|0|0|0|0\nsame|乙|0|0|0|0|0|0|0\n"
        assertThrows(IllegalArgumentException::class.java) { CardLutFormat.parseCatalog(raw) }
    }

    @Test fun identityCubeCompilesToNative4380ByteTable() {
        val text = buildString {
            appendLine("TITLE \"Identity\"")
            appendLine("LUT_3D_SIZE 2")
            for (b in 0..1) for (g in 0..1) for (r in 0..1) appendLine("$r $g $b")
        }
        val data = StringReader(text).use(CubeLut::read)
        val table = CubeLut.compileD4(data)
        assertEquals(4380, table.size)
        val words = ByteBuffer.wrap(table).order(ByteOrder.LITTLE_ENDIAN)
        for (plane in 0..2) {
            for (r in 0..8) for (g in 0..8) for (b in 0..8) {
                val expected = when (plane) { 0 -> r; 1 -> g; else -> b } * 1023 / 8
                assertEquals(expected, words.short.toInt() and 0xffff)
            }
            assertEquals(0, words.short.toInt())
        }
    }
}
