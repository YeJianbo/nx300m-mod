package dev.cxo.filmpack.core

import kotlin.math.ceil
import kotlin.math.floor

/**
 * RGB8 resampling primitives recovered from the native raw-buffer paths.
 * These operate on prepared 8-bit patch bytes only; they are not the full
 * grain preparation, seed, scale, or image lifecycle implementation.
 */
object NativeGrain8Resample {
    /** Native mean branch: dimension-changing RGB8 box footprint. */
    fun meanDownsample(
        source: ByteArray,
        srcWidth: Int,
        srcHeight: Int,
        dstWidth: Int,
        dstHeight: Int,
    ): ByteArray {
        validateDimensions(source, srcWidth, srcHeight, dstWidth, dstHeight)
        require(dstWidth <= srcWidth && dstHeight <= srcHeight &&
            (dstWidth < srcWidth || dstHeight < srcHeight)) {
            "meanDownsample only supports shrinking dimensions and requires a changed axis"
        }
        val x = meanBounds(srcWidth, dstWidth)
        val y = meanBounds(srcHeight, dstHeight)
        val output = ByteArray(checkedBytes(dstWidth, dstHeight))
        for (dy in 0 until dstHeight) {
            val yStart = y.start[dy]
            val yEnd = y.end[dy]
            for (dx in 0 until dstWidth) {
                val xStart = x.start[dx]
                val xEnd = x.end[dx]
                val count = (yEnd - yStart).toLong() * (xEnd - xStart).toLong()
                require(count > 0) { "mean footprint is empty" }
                for (channel in 0 until 3) {
                    var sum = 0L
                    for (sy in yStart until yEnd) {
                        var sourceIndex = (sy * srcWidth + xStart) * 3 + channel
                        repeat(xEnd - xStart) {
                            sum += source[sourceIndex].toInt() and 0xff
                            sourceIndex += 3
                        }
                    }
                    output[(dy * dstWidth + dx) * 3 + channel] = (sum / count).toInt().toByte()
                }
            }
        }
        return output
    }

    /**
     * Native OpenCV-variant linear resize. It supports both enlargement and
     * reduction and keeps the recovered 11-bit/two-stage integer rounding.
     */
    fun linearResize(
        source: ByteArray,
        srcWidth: Int,
        srcHeight: Int,
        dstWidth: Int,
        dstHeight: Int,
    ): ByteArray {
        validateDimensions(source, srcWidth, srcHeight, dstWidth, dstHeight)
        val x = linearAxis(dstWidth, srcWidth, horizontal = true)
        val y = linearAxis(dstHeight, srcHeight, horizontal = false)
        val output = ByteArray(checkedBytes(dstWidth, dstHeight))
        for (dy in 0 until dstHeight) {
            for (dx in 0 until dstWidth) {
                val top = y.first[dy]
                val bottom = y.second[dy]
                val left = x.first[dx]
                val right = x.second[dx]
                for (channel in 0 until 3) {
                    val hTop = (unsigned(source, (top * srcWidth + left) * 3 + channel) * x.weight0[dx] +
                        unsigned(source, (top * srcWidth + right) * 3 + channel) * x.weight1[dx])
                    val hBottom = (unsigned(source, (bottom * srcWidth + left) * 3 + channel) * x.weight0[dx] +
                        unsigned(source, (bottom * srcWidth + right) * 3 + channel) * x.weight1[dx])
                    val vertical = ((y.weight0[dy] * (hTop shr 4)) shr 16) +
                        ((y.weight1[dy] * (hBottom shr 4)) shr 16)
                    output[(dy * dstWidth + dx) * 3 + channel] = ((vertical + 2) shr 2)
                        .coerceIn(0, 255).toByte()
                }
            }
        }
        return output
    }

    private fun validateDimensions(source: ByteArray, srcWidth: Int, srcHeight: Int, dstWidth: Int, dstHeight: Int) {
        require(srcWidth > 0 && srcHeight > 0 && dstWidth > 0 && dstHeight > 0) { "dimensions must be positive" }
        require(source.size == checkedBytes(srcWidth, srcHeight)) { "source must contain packed RGB8 pixels" }
        checkedBytes(dstWidth, dstHeight)
    }

    private fun checkedBytes(width: Int, height: Int): Int {
        val pixels = width.toLong() * height.toLong()
        require(pixels <= Int.MAX_VALUE.toLong() / 3L) { "image buffer is too large" }
        return (pixels * 3L).toInt()
    }

    private fun unsigned(source: ByteArray, index: Int): Long = source[index].toLong() and 0xffL

    private data class Bounds(val start: IntArray, val end: IntArray)

    private fun meanBounds(sourceSize: Int, destinationSize: Int): Bounds {
        val scale = sourceSize.toFloat() / destinationSize.toFloat()
        val kernel = ceil(scale.toDouble()).toInt()
        val half = (scale * 0.5f).toInt()
        val start = IntArray(destinationSize)
        val end = IntArray(destinationSize)
        for (i in 0 until destinationSize) {
            val center = floor((i.toFloat() * scale + 0.5f).toDouble()).toInt()
            val candidate = center + half - kernel / 2
            start[i] = candidate.coerceIn(0, sourceSize)
            end[i] = (start[i] + kernel).coerceIn(0, sourceSize)
        }
        return Bounds(start, end)
    }

    private data class LinearAxis(
        val first: IntArray,
        val second: IntArray,
        val weight0: LongArray,
        val weight1: LongArray,
    )

    private fun linearAxis(destinationSize: Int, sourceSize: Int, horizontal: Boolean): LinearAxis {
        val scale = sourceSize.toDouble() / destinationSize.toDouble()
        val first = IntArray(destinationSize)
        val second = IntArray(destinationSize)
        val weight0 = LongArray(destinationSize)
        val weight1 = LongArray(destinationSize)
        for (i in 0 until destinationSize) {
            val coordinate = ((i.toDouble() + 0.5) * scale - 0.5).toFloat()
            var index = floor(coordinate.toDouble()).toInt()
            var fraction = coordinate - index.toFloat()
            if (horizontal && (index < 0 || index >= sourceSize - 1)) fraction = 0f
            first[i] = index.coerceIn(0, sourceSize - 1)
            second[i] = (index + 1).coerceIn(0, sourceSize - 1)
            weight0[i] = Math.rint(((1f - fraction) * 2048f).toDouble()).toLong()
            weight1[i] = Math.rint((fraction * 2048f).toDouble()).toLong()
        }
        return LinearAxis(first, second, weight0, weight1)
    }
}
