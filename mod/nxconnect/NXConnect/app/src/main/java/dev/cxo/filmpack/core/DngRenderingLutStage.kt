package dev.cxo.filmpack.core

import kotlin.math.min

/** DNG useCase=1 final profile LUT, sampled with native Naka coordinates.
 * The grid must be generated for the current DNG profile, never captured from a
 * reference photograph. B65,G65,R65,C4 values use the floating Q15 domain.
 * Input has passed DngColorMatrixStage and any context extra matrix.
 */
class DngRenderingLutStage(gridBgr: FloatArray) {
    private val grid = gridBgr.copyOf()

    init {
        require(grid.size == 65 * 65 * 65 * 4)
        for (i in grid.indices step 4) {
            require(grid[i].isFinite() && grid[i + 1].isFinite() && grid[i + 2].isFinite())
            require(grid[i + 3] == 0f)
        }
    }

    fun renderQ15(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 })
        var i = 0
        while (i < source.size) {
            val r = coordinate(source[i])
            val g = coordinate(source[i + 1])
            val b = coordinate(source[i + 2])
            val ri = min(r.toInt(), 63)
            val gi = min(g.toInt(), 63)
            val bi = min(b.toInt(), 63)
            for (c in 0..2) {
                val value = interpolate(ri, gi, bi, r - ri, g - gi, b - bi, c)
                destination[i + c] = Math.rint(value.coerceIn(0f, 32767f).toDouble()).toInt().toShort()
            }
            i += 3
        }
    }

    private fun coordinate(code: Short): Float {
        val x = code.toFloat() * (1f / 32767f)
        return x * 11f / (1f + x * 10f) * 63f
    }

    private fun interpolate(
        red: Int,
        green: Int,
        blue: Int,
        redFraction: Float,
        greenFraction: Float,
        blueFraction: Float,
        channel: Int,
    ): Float {
        val b00 = at(red, green, blue, channel) * (1f - blueFraction) +
            at(red, green, blue + 1, channel) * blueFraction
        val b01 = at(red, green + 1, blue + 1, channel) * blueFraction +
            (1f - blueFraction) * at(red, green + 1, blue, channel)
        val b10 = at(red + 1, green, blue + 1, channel) * blueFraction +
            (1f - blueFraction) * at(red + 1, green, blue, channel)
        val b11 = at(red + 1, green + 1, blue + 1, channel) * blueFraction +
            (1f - blueFraction) * at(red + 1, green + 1, blue, channel)
        val greenAtRedOne = b11 * greenFraction + (1f - greenFraction) * b10
        val greenAtRedZero = b01 * greenFraction + (1f - greenFraction) * b00
        return greenAtRedOne * redFraction + (1f - redFraction) * greenAtRedZero
    }

    private fun at(red: Int, green: Int, blue: Int, channel: Int): Float =
        grid[((blue * 65 + green) * 65 + red) * 4 + channel]

}
