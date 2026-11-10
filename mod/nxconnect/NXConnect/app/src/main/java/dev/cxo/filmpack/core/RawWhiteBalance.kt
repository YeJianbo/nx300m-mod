package dev.cxo.filmpack.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** Camera WB multipliers -> temperature/tint from a supplied calibrated resource grid.
 * Recovered from DxOFramework FindTempTintFromScales0x23e0cc / search0x23dcc4.
 * No image pixels, camera constants or camera identification are involved.
 * Nodes are [temperature,tint,RGB scale], RGB fastest, NOT reciprocal lambda scales.
 * The calibrated grid is copied. Its first/last integer Kelvin values bound the search;
 * the validated native resource spans2000..50000K. There is no extrapolation.
 */
class RawWhiteBalance(temperatures: FloatArray, tints: FloatArray, rgbScales: FloatArray) {
    private val temperatures = temperatures.copyOf()
    private val tints = tints.copyOf()
    private val scales = rgbScales.copyOf()

    data class Estimate(val temperatureKelvin: Float, val tint: Float, val scaleErrorL1: Float)

    init {
        require(this.temperatures.size >= 2 && this.tints.size >= 2)
        require(this.temperatures.all { it.isFinite() && it > 0f })
        require(this.tints.all { it.isFinite() })
        require((1 until this.temperatures.size).all { this.temperatures[it] > this.temperatures[it - 1] })
        require((1 until this.tints.size).all { this.tints[it] > this.tints[it - 1] })
        val first = this.temperatures.first()
        val last = this.temperatures.last()
        require(first == first.toInt().toFloat() && last == last.toInt().toFloat() && last - first <= 100000f)
        require(this.tints.size.toLong() * this.temperatures.size * 3 == scales.size.toLong())
        require(scales.all { it.isFinite() && it >= .001f }) { "Expected positive calibrated forward RGB WB scales" }
    }

    /** LibRaw-style camera WB R,G,B multipliers; a fourth green channel may follow.
     * Multipliers need only share a common scale. Source ratios outside the calibrated
     * grid return the nearest bounded estimate plus a nonzero error, not extrapolation.
     * This is a profile-dependent estimate; arbitrary RAW metadata formats are not decoded here.
     */
    fun estimate(camMul: FloatArray): Estimate {
        require(camMul.size == 3 || camMul.size == 4)
        require((0..2).all { camMul[it].isFinite() && camMul[it] >= .001f })
        val red = camMul[0] / camMul[1]
        val blue = camMul[2] / camMul[1]
        require(red.isFinite() && blue.isFinite())
        val table = FloatArray(tints.size * 2)
        val low = temperatures.first().toInt()
        val high = temperatures.last().toInt()
        val coarse = search(red, blue, low, high, 100, table)
        return search(red, blue, max(low, coarse.temperatureKelvin.toInt() - 100),
            min(high, coarse.temperatureKelvin.toInt() + 100), 1, table)
    }

    private fun search(red: Float, blue: Float, low: Int, high: Int, step: Int, table: FloatArray): Estimate {
        var bestError = Float.MAX_VALUE
        var bestTemperature = low.toFloat()
        var bestTint = tints.first()
        var temperature = low
        while (temperature <= high) {
            interpolateTemperature(temperature.toFloat(), table)
            for (i in 0 until tints.size - 1) {
                val lowTint = tints[i]
                val highTint = tints[i + 1]
                val slopeRed = (table[(i + 1) * 2] - table[i * 2]) / (highTint - lowTint)
                val slopeBlue = (table[(i + 1) * 2 + 1] - table[i * 2 + 1]) / (highTint - lowTint)
                val interceptRed = table[i * 2] - slopeRed * lowTint
                val interceptBlue = table[i * 2 + 1] - slopeBlue * lowTint
                for (channel in 0..1) {
                    val slope = if (channel == 0) slopeRed else slopeBlue
                    val intercept = if (channel == 0) interceptRed else interceptBlue
                    val target = if (channel == 0) red else blue
                    val tint = if (slope == 0f) lowTint else ((target - intercept) / slope).coerceIn(lowTint, highTint)
                    val error = abs(interceptRed + slopeRed * tint - red) + abs(interceptBlue + slopeBlue * tint - blue)
                    if (error < bestError) {
                        bestError = error
                        bestTemperature = temperature.toFloat()
                        bestTint = tint
                    }
                }
            }
            temperature += step
        }
        return Estimate(bestTemperature, bestTint, bestError)
    }

    private fun interpolateTemperature(temperature: Float, destination: FloatArray) {
        var upper = 0
        while (upper < temperatures.lastIndex && temperatures[upper] < temperature) upper++
        val lower = if (temperatures[upper] == temperature) upper else max(upper - 1, 0)
        val t0 = temperatures[lower]
        val t1 = temperatures[upper]
        for (i in tints.indices) {
            val a = (lower * tints.size + i) * 3
            val b = (upper * tints.size + i) * 3
            val red: Float
            val green: Float
            val blue: Float
            if (lower == upper) {
                red = scales[a]; green = scales[a + 1]; blue = scales[a + 2]
            } else {
                val right = temperature - t0
                val left = t1 - temperature
                val width = t1 - t0
                red = (scales[b] * right + scales[a] * left) / width
                green = (scales[b + 1] * right + scales[a + 1] * left) / width
                blue = (scales[b + 2] * right + scales[a + 2] * left) / width
            }
            destination[i * 2] = red / green
            destination[i * 2 + 1] = blue / green
        }
    }
}
