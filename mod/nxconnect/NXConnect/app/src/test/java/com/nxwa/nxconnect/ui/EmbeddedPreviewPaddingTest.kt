// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

class EmbeddedPreviewPaddingTest {
    @Test
    fun `measured padding keeps all 108 content rows`() {
        val pixels = IntArray(160 * 120) { index ->
            if (index / 160 in 6..113) 0xff709090.toInt() else 0xff090909.toInt()
        }
        assertEquals(6, EmbeddedPreviewPadding.topInset(160, 120, pixels))
        pixels[0] = 0xff606060.toInt()
        assertEquals(0, EmbeddedPreviewPadding.topInset(160, 120, pixels))
    }

    @Test
    fun `dark images other sizes and uncertain boundaries stay intact`() {
        assertEquals(0, EmbeddedPreviewPadding.topInset(160, 120, IntArray(160 * 120)))
        assertEquals(0, EmbeddedPreviewPadding.topInset(320, 240, IntArray(320 * 240)))
        val pixels = IntArray(160 * 120) { if (it / 160 in 7..112) 0xff808080.toInt() else 0 }
        assertEquals(0, EmbeddedPreviewPadding.topInset(160, 120, pixels))
    }

    @Test
    fun `actual local JPEG and SRW embedded previews have six padding rows`() {
        // Optional local evidence; user photographs are not distributed as test fixtures.
        val samples = System.getenv("NX_PREVIEW_SAMPLE_DIR")
        assumeTrue("Local photo samples were not supplied", samples != null)
        for (name in listOf("jpg-embedded.rgb", "srw-embedded.rgb")) {
            // Decode the local JPEGs to RGB with the inspection tool first;
            // the Android unit-test compiler has no java.desktop image decoder.
            val rgb = File(samples!!, name).readBytes()
            assertEquals(160 * 120 * 3, rgb.size)
            val pixels = IntArray(160 * 120) { i ->
                ((rgb[i * 3].toInt() and 255) shl 16) or
                    ((rgb[i * 3 + 1].toInt() and 255) shl 8) or (rgb[i * 3 + 2].toInt() and 255)
            }
            assertEquals(name, 6, EmbeddedPreviewPadding.topInset(160, 120, pixels))
        }
    }
}
