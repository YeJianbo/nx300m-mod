package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** RAW RGB0 65^3 grid application with native working-space strength blending.
 *
 * Input is linear working Q15 from [RawSensorToWorking]. Output is Naka-encoded
 * working Q15 and still requires output ICC conversion. The grid is native
 * B65,G65,R65,C4 packing, with R fastest and endpoint guards at index 64.
 */
class RawRgbFilmRenderer(
    gridBgr: FloatArray,
    private val runtimeBw: Boolean = false,
) {
    private val grid = gridBgr.copyOf()

    init {
        require(grid.size == RawRgbGridBaker.GRID_FLOATS) { "Expected B65,G65,R65,C4 grid" }
        for (index in grid.indices step 4) {
            require(grid[index].isFinite() && grid[index + 1].isFinite() && grid[index + 2].isFinite())
            require(grid[index + 3] == 0f) { "Expected zero alpha/padding component" }
        }
    }

    /** Interleaved nonnegative signed Q15. Exact in-place use is supported. */
    fun renderQ15(source: ShortArray, destination: ShortArray, intensity: Int = 100) {
        require(intensity in 0..200) { "Expected rendering intensity in 0..200" }
        val strength = min(intensity, 100) / 100f
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 }) { "Expected nonnegative linear working Q15" }
        var offset = 0
        while (offset < source.size) {
            val red = source[offset].toFloat() * RECIP * 63f
            val green = source[offset + 1].toFloat() * RECIP * 63f
            val blue = source[offset + 2].toFloat() * RECIP * 63f
            val redIndex = min(red.toInt(), 63)
            val greenIndex = min(green.toInt(), 63)
            val blueIndex = min(blue.toInt(), 63)
            val redFraction = red - redIndex
            val greenFraction = green - greenIndex
            val blueFraction = blue - blueIndex
            var rr = interpolate(redIndex, greenIndex, blueIndex, redFraction, greenFraction, blueFraction, 0)
            var gg = interpolate(redIndex, greenIndex, blueIndex, redFraction, greenFraction, blueFraction, 1)
            var bb = interpolate(redIndex, greenIndex, blueIndex, redFraction, greenFraction, blueFraction, 2)
            // Blend before clamping: RGB grids may legitimately overshoot.
            if (strength < 1f) {
                val baseR = source[offset].toFloat() * RECIP
                val baseG = source[offset + 1].toFloat() * RECIP
                val baseB = source[offset + 2].toFloat() * RECIP
                rr = baseR + (rr - baseR) * strength
                gg = baseG + (gg - baseG) * strength
                bb = baseB + (bb - baseB) * strength
            }
            rr = rr.coerceIn(0f, 1f)
            gg = gg.coerceIn(0f, 1f)
            bb = bb.coerceIn(0f, 1f)
            if (runtimeBw) {
                val gray = (rr * .390625f + 0f + gg * .546875f) + bb * .0625f
                rr = gray
                gg = gray
                bb = gray
            }
            val low = min(rr, min(gg, bb))
            val high = max(rr, max(gg, bb))
            val mappedLow = low * 11f / (1f + low * 10f)
            val mappedHigh = high * 11f / (1f + high * 10f)
            val rootLow = sqrt(low)
            val rootSpan = max(sqrt(high) - rootLow, EPS)
            destination[offset] = encode(mappedLow + (mappedHigh - mappedLow) * ((sqrt(rr) - rootLow) / rootSpan))
            destination[offset + 1] = encode(mappedLow + (mappedHigh - mappedLow) * ((sqrt(gg) - rootLow) / rootSpan))
            destination[offset + 2] = encode(mappedLow + (mappedHigh - mappedLow) * ((sqrt(bb) - rootLow) / rootSpan))
            offset += 3
        }
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

    private fun encode(value: Float): Short =
        Math.rint((value.coerceIn(0f, 1f) * 32767f).toDouble()).toInt().toShort()

    private companion object {
        const val RECIP = 1f / 32767f
        const val EPS = 1.1920928955078125e-7f
    }
}
