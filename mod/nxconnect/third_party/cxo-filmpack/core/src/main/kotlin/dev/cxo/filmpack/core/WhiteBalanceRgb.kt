package dev.cxo.filmpack.core

/**
 * Per-channel RGB white-balance stage observed before ColorRendering.
 *
 * Manual gain application for experiments. Gains are explicitly supplied;
 * native processing also composes per-film base scales and uses integer Q15
 * ScaleImage, which this uint16 Float-rounding helper does not implement.
 * Color/temperature factor construction is independently native-verified.
 */
class WhiteBalanceRgb(
    val redGain: Float,
    val greenGain: Float,
    val blueGain: Float,
) {
    init {
        require(redGain.isFinite() && greenGain.isFinite() && blueGain.isFinite())
        require(redGain >= 0f && greenGain >= 0f && blueGain >= 0f)
    }

    fun apply(r: Int, g: Int, b: Int, out: IntArray, offset: Int = 0) {
        require(offset >= 0 && offset <= out.size - 3)
        require(r in 0..65535 && g in 0..65535 && b in 0..65535)
        out[offset] = scaled(r, redGain)
        out[offset + 1] = scaled(g, greenGain)
        out[offset + 2] = scaled(b, blueGain)
    }

    fun apply(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && source.size == destination.size)
        val out = IntArray(3)
        for (i in source.indices step 3) {
            apply(source[i].toInt() and 0xffff, source[i + 1].toInt() and 0xffff,
                source[i + 2].toInt() and 0xffff, out)
            destination[i] = out[0].toShort()
            destination[i + 1] = out[1].toShort()
            destination[i + 2] = out[2].toShort()
        }
    }

    private fun scaled(value: Int, gain: Float): Int =
        Math.rint((value.toFloat() * gain).toDouble()).toInt().coerceIn(0, 65535)

    companion object {
        /**
         * The target-space result of DxO's AdobeRGB Color::Convert call.
         * Keeping this as a function type makes the CMM/profile choice an
         * explicit dependency of the image adapter instead of hiding an
         * empirical matrix in the core.
         */
        fun interface ColorConverter {
            fun convert(adobeRgb: FloatArray): FloatArray
        }

        /** Native color factors after AdobeRGB -> source Image.ColorSpace.
         * The converter must use the source image's actual profile, not assume
         * built-in sRGB or the film's internal WideGamut. Verified against
         * 5,516 direct native helper calls; this only constructs factors and
         * does not include base film scales or ScaleImage integer application.
         */
        fun colorFactors(
            adobeRgb: FloatArray,
            converter: ColorConverter,
        ): FloatArray {
            require(adobeRgb.size == 3) { "Expected an Adobe RGB triplet" }
            require(adobeRgb.all { it.isFinite() }) { "Adobe RGB values must be finite" }
            val target = converter.convert(adobeRgb.copyOf())
            require(target.size == 3) { "Color converter must return RGB triplet" }
            require(target.all { it.isFinite() }) { "Converted RGB values must be finite" }
            val y = target[0] * 0.299f + target[1] * 0.587f + target[2] * 0.114f
            fun factor(channel: Float): Float = if (channel == 0f) 1f else y / channel
            return floatArrayOf(factor(target[0]), factor(target[1]), factor(target[2]))
        }

        /**
         * Experimental ratio model with an explicitly hypothetical [0,2] cap.
         * This is not established as the native WhiteBalanceRGBColor branch.
         * Kept for reproducible diagnostic comparisons, never the default path.
         */
        fun experimentalFromColorRatios(
            adobeRgb: FloatArray,
            converter: ColorConverter,
            baseRedGain: Float = 1f,
            baseGreenGain: Float = 1f,
            baseBlueGain: Float = 1f,
        ): WhiteBalanceRgb {
            val base = floatArrayOf(baseRedGain, baseGreenGain, baseBlueGain)
            require(base.all { it.isFinite() && it >= 0f }) { "Base gains must be finite and non-negative" }
            val factors = colorFactors(adobeRgb, converter)
            return WhiteBalanceRgb(
                clampGain(base[0] * factors[0]),
                clampGain(base[1] * factors[1]),
                clampGain(base[2] * factors[2]),
            )
        }

        /**
         * Build a manual gain stage from the native temperature table and
         * explicit base gains. Native base film lookup/integer application
         * is a separate boundary.
         * The color override has its own verified colorFactors() constructor;
         * this helper only covers the temperature branch.
         */
        fun fromTemperature(
            temperatureK: Float,
            baseRedGain: Float = 1f,
            baseGreenGain: Float = 1f,
            baseBlueGain: Float = 1f,
        ): WhiteBalanceRgb {
            val factors = temperatureFactors(temperatureK)
            return WhiteBalanceRgb(
                baseRedGain * factors[0],
                baseGreenGain * factors[1],
                baseBlueGain * factors[2],
            )
        }

        /** Native 0x1bcd7e4 table, linearly interpolated between rows. */
        fun temperatureFactors(temperatureK: Float): FloatArray {
            require(temperatureK.isFinite() && temperatureK in 2000f..10000f)
            var hi = 1
            while (hi < TEMPERATURE_TABLE.size && temperatureK > TEMPERATURE_TABLE[hi][0]) hi++
            if (hi == TEMPERATURE_TABLE.size) hi = TEMPERATURE_TABLE.lastIndex
            val lo = (hi - 1).coerceAtLeast(0)
            val a = TEMPERATURE_TABLE[lo]
            val b = TEMPERATURE_TABLE[hi]
            // Native computes a weighted sum, then multiplies by the shared
            // reciprocal. a + (b-a)*t changes rounding (359/810 native fixture
            // components), including table knots. Keep these Float operations.
            val inverseSpan = 1f / (b[0] - a[0])
            val leftWeight = b[0] - temperatureK
            val rightWeight = temperatureK - a[0]
            return FloatArray(3) { c ->
                (leftWeight * a[c + 1] + rightWeight * b[c + 1]) * inverseSpan
            }
        }

        private val TEMPERATURE_TABLE = arrayOf(
            floatArrayOf(2000f, .65567213f, 1.27938282f, 2.79425025f),
            floatArrayOf(2800f, .76846492f, 1.18854272f, 2.08084416f),
            floatArrayOf(3000f, .79102343f, 1.17037463f, 1.93816292f),
            floatArrayOf(3100f, .79986835f, 1.15970111f, 1.86349940f),
            floatArrayOf(3200f, .80846786f, 1.14901245f, 1.79201531f),
            floatArrayOf(4000f, .89837521f, 1.06503057f, 1.32509518f),
            floatArrayOf(5000f, .98105294f, .99999404f, 1.02269363f),
            floatArrayOf(5200f, 1f, 1f, 1f),
            floatArrayOf(6000f, 1.07578838f, 1.00002384f, .90922552f),
            floatArrayOf(7000f, 1.15579247f, 1.00007737f, .83526409f),
            floatArrayOf(8000f, 1.21506679f, 1.00014365f, .79186928f),
            floatArrayOf(9000f, 1.24317026f, 1.00017166f, .75823635f),
            floatArrayOf(10000f, 1.27127361f, 1.00019956f, .72460335f),
        )

        private fun clampGain(value: Float): Float = value.coerceIn(0f, 2f)
    }
}

