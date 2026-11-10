// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

/** NX300M's small EXIF preview has a 160x108 image inside a 160x120 JPEG.
 * Only recognize that measured format; never infer a crop for original photos.
 */
internal object EmbeddedPreviewPadding {
    fun topInset(width: Int, height: Int, pixels: IntArray): Int {
        if (width != 160 || height != 120 || pixels.size != width * height) return 0
        fun bright(pixel: Int, threshold: Int): Boolean =
            ((pixel ushr 16) and 255) > threshold ||
                ((pixel ushr 8) and 255) > threshold || (pixel and 255) > threshold
        fun contentPixels(row: Int, threshold: Int): Int =
            (0 until width).count { bright(pixels[row * width + it], threshold) }
        // JPEG ringing in the measured padding reaches 9; require both complete
        // bands and a clear transition on each side. Dark/uncertain frames stay intact.
        for (row in 0 until 6) {
            if (contentPixels(row, 12) != 0 || contentPixels(height - 1 - row, 12) != 0) return 0
        }
        if (contentPixels(6, 24) < width / 8 || contentPixels(height - 7, 24) < width / 8) return 0
        return 6
    }
}
