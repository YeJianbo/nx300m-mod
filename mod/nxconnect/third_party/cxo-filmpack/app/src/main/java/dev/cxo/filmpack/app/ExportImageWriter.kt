package dev.cxo.filmpack.app

import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64
import java.util.concurrent.CancellationException

/** Encodes already rendered opaque 8-bit sRGB. Film, source-size grain and post
 * editing run in [readRows], before area reduction. Orientation stays metadata.
 * The caller owns the stream; it is never closed and failures are never retried. */
internal object ExportImageWriter {
    fun write(
        output: OutputStream, width: Int, height: Int, exifOrientation: Int = 1,
        settings: ExportSettings = ExportSettings(), nativeJpegEnabled: Boolean = true,
        cancelled: () -> Boolean = { false },
        readRows: (top: Int, count: Int, argb: IntArray) -> Unit,
    ) {
        val (outWidth, outHeight) = settings.dimensions(width, height)
        require(exifOrientation in 1..8) { "EXIF orientation must be 1..8" }
        require(width <= Int.MAX_VALUE / 8 && outWidth <= Int.MAX_VALUE / 8) { "Row buffer is too wide" }
        require(width.toLong() * height <= Long.MAX_VALUE / 256) { "Image area is too large" }
        if (settings.format == ExportFormat.JPEG) {
            require(outWidth <= 65535 && outHeight <= 65535) { "JPEG dimensions must be 1..65535" }
        }
        checkCancelled(cancelled)
        val checkedRows: (Int, Int, IntArray) -> Unit = { top, count, argb ->
            checkCancelled(cancelled)
            readRows(top, count, argb)
            checkCancelled(cancelled)
        }
        val rows = if (width == outWidth && height == outHeight) checkedRows else
            AreaRows(width, height, outWidth, outHeight, cancelled, checkedRows)::read
        when (settings.format) {
            ExportFormat.JPEG -> NativeJpegWriter.write(
                output, outWidth, outHeight, settings.jpegQuality, exifOrientation,
                nativeEnabled = nativeJpegEnabled, readRows = rows,
            )
            ExportFormat.TIFF -> writeTiff(output, outWidth, outHeight, exifOrientation, cancelled, rows)
        }
        checkCancelled(cancelled)
    }

    /** Exact integer pixel-area overlap, rounded once at the 8-bit boundary.
     * Source batches are sequential eight rows (except the final batch). Memory:
     * sourceWidth*8 Ints + outputWidth*3 Longs + the codec's eight rows.
     * Averages encoded sRGB samples, after all source-size rendering. */
    private class AreaRows(
        val width: Int, val height: Int, val outWidth: Int, val outHeight: Int,
        val cancelled: () -> Boolean, val source: (Int, Int, IntArray) -> Unit,
    ) {
        val input = IntArray(width * 8)
        val sums = LongArray(Math.multiplyExact(outWidth, 3))
        var sourceTop = -8
        var nextTop = 0
        val area = width.toLong() * height

        fun read(top: Int, count: Int, argb: IntArray) {
            check(top == nextTop && count in 1..8 && top + count <= outHeight)
            for (row in 0 until count) {
                checkCancelled(cancelled)
                sums.fill(0)
                val y = top + row
                val yStart = y.toLong() * height
                val yEnd = (y + 1L) * height
                val firstY = (yStart / outHeight).toInt()
                val lastY = ((yEnd - 1) / outHeight).toInt()
                for (sy in firstY..lastY) {
                    checkCancelled(cancelled)
                    if (sy >= sourceTop + 8) {
                        sourceTop += 8
                        source(sourceTop, minOf(8, height - sourceTop), input)
                    }
                    val wy = minOf(yEnd, (sy + 1L) * outHeight) - maxOf(yStart, sy.toLong() * outHeight)
                    val base = (sy - sourceTop) * width
                    for (x in 0 until outWidth) {
                        if (x and 255 == 0) checkCancelled(cancelled)
                        val xStart = x.toLong() * width
                        val xEnd = (x + 1L) * width
                        val firstX = (xStart / outWidth).toInt()
                        val lastX = ((xEnd - 1) / outWidth).toInt()
                        var red = 0L; var green = 0L; var blue = 0L
                        for (sx in firstX..lastX) {
                            if (sx and 4095 == 0) checkCancelled(cancelled)
                            val wx = minOf(xEnd, (sx + 1L) * outWidth) - maxOf(xStart, sx.toLong() * outWidth)
                            val p = input[base + sx]
                            red += ((p ushr 16) and 255) * wx
                            green += ((p ushr 8) and 255) * wx
                            blue += (p and 255) * wx
                        }
                        sums[x * 3] += red * wy
                        sums[x * 3 + 1] += green * wy
                        sums[x * 3 + 2] += blue * wy
                    }
                }
                for (x in 0 until outWidth) {
                    fun channel(index: Int) = ((sums[x * 3 + index] + area / 2) / area).toInt()
                    argb[row * outWidth + x] = (255 shl 24) or (channel(0) shl 16) or (channel(1) shl 8) or channel(2)
                }
            }
            nextTop += count
        }
    }

    /** Baseline little-endian uncompressed RGB TIFF, explicitly 8 bits/channel.
     * The contiguous strip has known offsets before writing, so non-seekable
     * streams need no full-image copy. Classic TIFF files must be below 4 GiB. */
    private fun writeTiff(
        output: OutputStream, width: Int, height: Int, orientation: Int,
        cancelled: () -> Boolean, rows: (Int, Int, IntArray) -> Unit,
    ) {
        val icc = srgbProfile
        val software = ("CXO FilmPack RGB8" + 0.toChar()).toByteArray(Charsets.US_ASCII)
        val entryCount = 16
        val bitsOffset = 8 + 2 + entryCount * 12 + 4
        val xResolutionOffset = bitsOffset + 6
        val yResolutionOffset = xResolutionOffset + 8
        val softwareOffset = yResolutionOffset + 8
        val iccOffset = (softwareOffset + software.size + 1) and -2
        val pixelOffset = (iccOffset + icc.size + 1) and -2
        val byteCount = width.toLong() * height * 3
        require(pixelOffset + byteCount <= 0xffff_ffffL) { "Classic TIFF exceeds 4 GiB" }
        val header = ByteBuffer.allocate(pixelOffset).order(ByteOrder.LITTLE_ENDIAN)
        header.put('I'.code.toByte()).put('I'.code.toByte()).putShort(42).putInt(8).putShort(entryCount.toShort())
        fun entry(tag: Int, type: Int, count: Int, value: Long) {
            header.putShort(tag.toShort()).putShort(type.toShort()).putInt(count).putInt(value.toInt())
        }
        entry(256, 4, 1, width.toLong())
        entry(257, 4, 1, height.toLong())
        entry(258, 3, 3, bitsOffset.toLong())
        entry(259, 3, 1, 1) // Compression=None
        entry(262, 3, 1, 2) // PhotometricInterpretation=RGB
        entry(273, 4, 1, pixelOffset.toLong())
        entry(274, 3, 1, orientation.toLong())
        entry(277, 3, 1, 3)
        entry(278, 4, 1, height.toLong())
        entry(279, 4, 1, byteCount)
        entry(282, 5, 1, xResolutionOffset.toLong())
        entry(283, 5, 1, yResolutionOffset.toLong())
        entry(284, 3, 1, 1) // Chunky RGB
        entry(296, 3, 1, 2) // Inches
        entry(305, 2, software.size, softwareOffset.toLong())
        entry(34675, 7, icc.size, iccOffset.toLong())
        header.putInt(0)
        header.position(bitsOffset)
        repeat(3) { header.putShort(8) }
        header.position(xResolutionOffset); header.putInt(72).putInt(1)
        header.position(yResolutionOffset); header.putInt(72).putInt(1)
        header.position(softwareOffset); header.put(software)
        header.position(iccOffset); header.put(icc)
        checkCancelled(cancelled)
        output.write(header.array())
        val argb = IntArray(width * 8)
        val rgb = ByteArray(Math.multiplyExact(width, 3))
        var top = 0
        while (top < height) {
            checkCancelled(cancelled)
            val count = minOf(8, height - top)
            rows(top, count, argb)
            for (row in 0 until count) {
                checkCancelled(cancelled)
                for (x in 0 until width) {
                    val pixel = argb[row * width + x]
                    rgb[x * 3] = (pixel ushr 16).toByte()
                    rgb[x * 3 + 1] = (pixel ushr 8).toByte()
                    rgb[x * 3 + 2] = pixel.toByte()
                }
                output.write(rgb)
            }
            top += count
        }
        checkCancelled(cancelled)
        output.flush()
    }

    private fun checkCancelled(cancelled: () -> Boolean) {
        if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("已取消")
    }

    // sRGB profile from core/local-data/srgb-embedded.icc. Colour metadata only.
    private val srgbProfile: ByteArray by lazy { Base64.getDecoder().decode(SRGB_PROFILE) }
    private const val SRGB_PROFILE = "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"
}
