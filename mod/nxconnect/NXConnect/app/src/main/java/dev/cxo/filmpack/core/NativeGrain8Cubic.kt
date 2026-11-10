package dev.cxo.filmpack.core

import java.math.BigDecimal
import kotlin.math.floor

/**
 * Recovered macOS ARM64 RGB8 cubic enlargement used by grain preparation.
 * Mirrors its 11-bit coefficients, 8-pixel float/FMA prefix and integer tail.
 * Input and output are packed interleaved RGB8, independent of image stride.
 * This is the raw resize stage, before the patch's ICC/color-LUT conversion.
 */
object NativeGrain8Cubic {
    /** Each destination axis must be at least its source size. Identity copies. */
    fun resize(
        source: ByteArray,
        srcWidth: Int,
        srcHeight: Int,
        dstWidth: Int,
        dstHeight: Int,
    ): ByteArray {
        require(srcWidth > 0 && srcHeight > 0 && dstWidth > 0 && dstHeight > 0) {
            "dimensions must be positive"
        }
        require(source.size == checkedBytes(srcWidth, srcHeight)) { "source must be packed RGB8" }
        val destinationBytes = checkedBytes(dstWidth, dstHeight)
        require(dstWidth >= srcWidth && dstHeight >= srcHeight) { "cubic resize only supports enlargement or identity" }
        if (dstWidth == srcWidth && dstHeight == srcHeight) return source.copyOf()

        val x = axis(srcWidth, dstWidth)
        val y = axis(srcHeight, dstHeight)
        val output = ByteArray(destinationBytes)
        // At most four distinct source rows are needed. Access is monotonic.
        val rows = LinkedHashMap<Int, IntArray>()
        fun horizontal(sourceY: Int): IntArray = rows.getOrPut(sourceY) {
            IntArray(dstWidth * 3).also { row ->
                for (dx in 0 until dstWidth) {
                    for (channel in 0..2) {
                        var sum = 0
                        for (k in 0..3) {
                            val value = source[(sourceY * srcWidth + x.index[k][dx]) * 3 + channel].toInt() and 255
                            sum += value * x.weight[k][dx]
                        }
                        row[dx * 3 + channel] = sum
                    }
                }
            }
        }
        val vectorEnd = dstWidth / 8 * 8
        for (dy in 0 until dstHeight) {
            val needed = IntArray(4) { y.index[it][dy] }
            rows.keys.retainAll(needed.toSet())
            val h = Array(4) { horizontal(needed[it]) }
            val beta = FloatArray(4) { y.weight[it][dy].toFloat() / 4194304f }
            for (dx in 0 until dstWidth) {
                for (channel in 0..2) {
                    val at = dx * 3 + channel
                    val value = if (dx < vectorEnd) {
                        var sum = h[3][at].toFloat() * beta[3]
                        sum = FiniteFloatFma.unchecked(h[2][at].toFloat(), beta[2], sum)
                        sum = FiniteFloatFma.unchecked(h[1][at].toFloat(), beta[1], sum)
                        sum = FiniteFloatFma.unchecked(h[0][at].toFloat(), beta[0], sum)
                        Math.rint(sum.toDouble()).toInt()
                    } else {
                        var sum = 1L shl 21
                        for (k in 0..3) sum += h[k][at].toLong() * y.weight[k][dy]
                        (sum shr 22).toInt()
                    }
                    output[(dy * dstWidth + dx) * 3 + channel] = value.coerceIn(0, 255).toByte()
                }
            }
        }
        return output
    }

    private fun checkedBytes(width: Int, height: Int): Int {
        val pixels = width.toLong() * height.toLong()
        require(pixels <= Int.MAX_VALUE.toLong() / 3) { "image buffer is too large" }
        return (pixels * 3).toInt()
    }

    private class Axis(size: Int) {
        val index = Array(4) { IntArray(size) }
        val weight = Array(4) { IntArray(size) }
    }

    private fun axis(sourceSize: Int, destinationSize: Int): Axis {
        val scale = 1.0 / (destinationSize.toDouble() / sourceSize.toDouble())
        val result = Axis(destinationSize)
        for (i in 0 until destinationSize) {
            val coordinate = coordinate(i, scale)
            val origin = floor(coordinate.toDouble()).toInt()
            val x = coordinate - origin.toFloat()
            val t = x + 1f
            val y = 1f - x
            val a = fma(t, fma(t, fma(t, -0.75f, 3.75f), -6f), 3f)
            val b = fma(x, x * fma(x, 1.25f, -2.25f), 1f)
            val c = fma(y, y * fma(y, 1.25f, -2.25f), 1f)
            val d = ((1f - a) - b) - c
            val coefficients = floatArrayOf(a, b, c, d)
            for (k in 0..3) {
                result.index[k][i] = (origin + k - 1).coerceIn(0, sourceSize - 1)
                result.weight[k][i] = Math.rint((coefficients[k] * 2048f).toDouble()).toInt()
                    .coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
            }
        }
        return result
    }

    /**
     * Native performs Double FMADD then casts to Float. BigDecimal(Double)
     * preserves each binary operand exactly; the exact product/addition is
     * rounded to Double before Float, including the rare double-round case.
     * This runs only while building axis coefficients, not per image pixel.
     */
    internal fun coordinate(destination: Int, scale: Double): Float =
        BigDecimal(destination.toDouble() + 0.5).multiply(BigDecimal(scale))
            .subtract(HALF).toDouble().toFloat()

    private fun fma(a: Float, b: Float, c: Float) = FiniteFloatFma.unchecked(a, b, c)
    private val HALF = BigDecimal("0.5")
}
