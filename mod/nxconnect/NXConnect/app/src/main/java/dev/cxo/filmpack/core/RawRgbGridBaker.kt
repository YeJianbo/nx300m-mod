package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sqrt

/** Explicit parameters for the recovered RAW RGB0 baker.
 *
 * Curves contain normalized-domain, Q15-valued Float samples: 32768 entries
 * plus one guard. Matrices are row-major RGB column-vector matrices.
 */
data class RawRgbBakeParameters(
    val ev: Float,
    val renderingEv: Float,
    val preMatrix: FloatArray,
    val postMatrix: FloatArray,
    val extraMatrix: FloatArray,
    val filmCurves: Array<FloatArray>,
) {
    init {
        require(ev.isFinite() && renderingEv.isFinite())
        require(preMatrix.size == 9 && postMatrix.size == 9 && extraMatrix.size == 9)
        require(preMatrix.all(Float::isFinite) && postMatrix.all(Float::isFinite) && extraMatrix.all(Float::isFinite))
        require(filmCurves.size == 1 || filmCurves.size == 3)
        require(filmCurves.all { it.size == CURVE_SAMPLES && it.all(Float::isFinite) })
    }

    companion object { const val CURVE_SAMPLES = 32769 }
}

/** Research/asset-generation implementation of NEON ComputeRGBLUT_Raw.
 *
 * Output packing is native B65,G65,R65,C4 with R fastest. Axis 64 repeats the
 * endpoint at 63. This path ends after Naka inverse: unlike RAW HSV baking it
 * has no composed inverse curve, HSV residual encoding, sentinel, or repair.
 */
object RawRgbGridBaker {
    const val GRID_EDGE = 65
    const val GRID_FLOATS = GRID_EDGE * GRID_EDGE * GRID_EDGE * 4
    private const val Q15 = 32767f
    private const val RECIP = 1f / Q15
    private const val EPS = 1.1920928955078125e-7f

    fun bake(parameters: RawRgbBakeParameters): FloatArray {
        val grid = FloatArray(GRID_FLOATS)
        val rgb = FloatArray(3)
        val axisScale = Q15 / 63f
        for (blue in 0 until GRID_EDGE) {
            for (green in 0 until GRID_EDGE) {
                for (red in 0 until GRID_EDGE) {
                    rgb[0] = axisScale * min(red, 63) * RECIP
                    rgb[1] = axisScale * min(green, 63) * RECIP
                    rgb[2] = axisScale * min(blue, 63) * RECIP
                    shoulder(rgb, exp2(.84f - parameters.ev))
                    matrix(parameters.preMatrix, rgb)
                    clamp(rgb)
                    shoulder(rgb, exp2(parameters.ev - parameters.renderingEv))
                    applyCurves(rgb, parameters.filmCurves)
                    matrix(parameters.postMatrix, rgb)
                    clamp(rgb)
                    for (channel in 0..2) rgb[channel] = rgb[channel].pow(2.2f)
                    matrix(parameters.extraMatrix, rgb)
                    for (channel in 0..2) {
                        require(rgb[channel] >= 0f) {
                            "extra matrix produced a negative inverse-gamma input at BGR=($blue,$green,$red)"
                        }
                        rgb[channel] = rgb[channel].pow(.45454544f)
                    }
                    nakaInverse(rgb)
                    val base = ((blue * GRID_EDGE + green) * GRID_EDGE + red) * 4
                    grid[base] = rgb[0]
                    grid[base + 1] = rgb[1]
                    grid[base + 2] = rgb[2]
                    grid[base + 3] = 0f
                }
            }
        }
        return grid
    }

    private fun exp2(value: Float) = 2.0.pow(value.toDouble()).toFloat()

    private fun matrix(matrix: FloatArray, rgb: FloatArray) {
        val red = rgb[0]
        val green = rgb[1]
        val blue = rgb[2]
        rgb[0] = ((red * matrix[0] + 0f) + green * matrix[1]) + blue * matrix[2]
        rgb[1] = ((red * matrix[3] + 0f) + green * matrix[4]) + blue * matrix[5]
        rgb[2] = ((red * matrix[6] + 0f) + green * matrix[7]) + blue * matrix[8]
    }

    private fun clamp(rgb: FloatArray) {
        for (channel in 0..2) rgb[channel] = rgb[channel].coerceIn(0f, 1f)
    }

    private fun shoulder(rgb: FloatArray, gain: Float) {
        val upper = min(1f / gain, 1f)
        val value = min(gain, 1f)
        val knee = .3f * upper
        val base = .3f * value
        val upperRatio = (1f - upper) / (1f - knee)
        val valueRatio = (1f - value) / (1f - knee)

        fun map(input: Float): Float {
            val distance = input - knee
            return if (input < knee) gain * input else {
                base + distance * ((value + distance * valueRatio) - base) /
                    ((upper + distance * upperRatio) - knee)
            }
        }

        val low = min(rgb[0], min(rgb[1], rgb[2]))
        val high = max(rgb[0], max(rgb[1], rgb[2]))
        val mappedLow = map(low)
        val mappedHigh = map(high)
        val span = max(high - low, EPS)
        for (channel in 0..2) {
            rgb[channel] = mappedLow + (mappedHigh - mappedLow) * ((rgb[channel] - low) / span)
        }
    }

    private fun sample(input: Float, table: FloatArray): Float {
        val position = input.coerceIn(0f, 1f) * Q15
        val index = position.toInt()
        return (table[index] + (position - index) * (table[index + 1] - table[index])) * RECIP
    }

    private fun applyCurves(rgb: FloatArray, curves: Array<FloatArray>) {
        if (curves.size == 1) {
            for (channel in 0..2) rgb[channel] = sample(rgb[channel], curves[0])
        } else {
            for (channel in 0..2) rgb[channel] = sample(rgb[channel], curves[channel])
        }
    }

    private fun nakaInverse(rgb: FloatArray) {
        val order = intArrayOf(0, 1, 2)
        for (i in 0..1) for (j in i + 1..2) {
            if (rgb[order[j]] < rgb[order[i]]) {
                val temporary = order[i]
                order[i] = order[j]
                order[j] = temporary
            }
        }
        val lowIndex = order[0]
        val middleIndex = order[1]
        val highIndex = order[2]
        val low = rgb[lowIndex]
        val middle = rgb[middleIndex]
        val high = rgb[highIndex]
        val mappedLow = (low * .09090909f) / (1f - low * .9090909f)
        val mappedHigh = (high * .09090909f) / (1f - high * .9090909f)
        val root = sqrt(mappedLow) + ((middle - low) / max(high - low, EPS)) *
            (sqrt(mappedHigh) - sqrt(mappedLow))
        rgb[lowIndex] = mappedLow
        rgb[middleIndex] = root * root
        rgb[highIndex] = mappedHigh
    }
}
