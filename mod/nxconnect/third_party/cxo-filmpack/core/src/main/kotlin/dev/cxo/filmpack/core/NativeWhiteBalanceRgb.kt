package dev.cxo.filmpack.core

/** Recovered WB prepare + signed16 ScaleBuffer scalar arithmetic.
 * Input/output are internal signed Q15 codes, not unsigned16 TIFF codes.
 * Negative factors/shift domains outside current verified source images fail
 * explicitly. No hypothetical [0,2] gain clamp is applied.
 */
class NativeWhiteBalanceRgb private constructor(
    val redGain: Float,
    val greenGain: Float,
    val blueGain: Float,
    val commonShift: Int,
    coefficients: IntArray,
) {
    private val scales = coefficients.copyOf()
    private val reduced = IntArray(3)
    private val shifts = IntArray(3)

    init {
        for (channel in 0..2) {
            var coefficient = scales[channel]
            var shift = commonShift
            while (coefficient != coefficient.toShort().toInt()) {
                coefficient = coefficient shr 1
                shift--
            }
            require(shift in 0..31) { "Unverified WB integer shift domain" }
            reduced[channel] = coefficient
            shifts[channel] = shift
        }
    }

    fun integerCoefficients(): IntArray = scales.copyOf()

    /** Signed product fits Int32; arithmetic shift floors negative values. */
    fun applyQ15(r: Int, g: Int, b: Int, out: IntArray, offset: Int = 0) {
        require(offset >= 0 && offset <= out.size - 3)
        require(r in -32768..32767 && g in -32768..32767 && b in -32768..32767)
        out[offset] = scale(r, 0)
        out[offset + 1] = scale(g, 1)
        out[offset + 2] = scale(b, 2)
    }

    fun applyQ15(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && source.size == destination.size)
        for (i in source.indices step 3) {
            destination[i] = scale(source[i].toInt(), 0).toShort()
            destination[i + 1] = scale(source[i + 1].toInt(), 1).toShort()
            destination[i + 2] = scale(source[i + 2].toInt(), 2).toShort()
        }
    }

    private fun scale(value: Int, channel: Int): Int =
        ((value * reduced[channel]) shr shifts[channel]).coerceIn(-32768, 32767)

    /** TIFF/source code boundary for the verified unsigned16 >>1 decoder.
     * The result is an even unsigned16 carrier whose >>1 recovers the exact
     * WB Q15 code. This is not a TIFF encoder or a generic source decoder.
     */
    fun applySrgb16Carrier(r: Int, g: Int, b: Int, out: IntArray, offset: Int = 0) {
        require(r in 0..65535 && g in 0..65535 && b in 0..65535)
        applyQ15(r ushr 1, g ushr 1, b ushr 1, out, offset)
        for (c in 0..2) out[offset + c] = out[offset + c] shl 1
    }

    fun applySrgb16Carrier(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && source.size == destination.size)
        for (i in source.indices step 3) {
            destination[i] = (scale((source[i].toInt() and 65535) ushr 1, 0) shl 1).toShort()
            destination[i + 1] = (scale((source[i + 1].toInt() and 65535) ushr 1, 1) shl 1).toShort()
            destination[i + 2] = (scale((source[i + 2].toInt() and 65535) ushr 1, 2) shl 1).toShort()
        }
    }

    companion object {
        /** Color/temperature factors are selected by caller before composition.
         * Native base intensity interpolation uses t=intensity/100, even above
         * 100; the film core's own strength has separate semantics.
         */
        fun create(
            base: FilmRgbBaseScale,
            intensity: Int = 100,
            colorFactors: FloatArray = floatArrayOf(1f, 1f, 1f),
            renderingActive: Boolean = true,
        ): NativeWhiteBalanceRgb = fromGains(
            compose(base.red, colorFactors, 0, intensity, renderingActive),
            compose(base.green, colorFactors, 1, intensity, renderingActive),
            compose(base.blue, colorFactors, 2, intensity, renderingActive),
        )

        private fun compose(base: Float, factors: FloatArray, c: Int, intensity: Int, active: Boolean): Float {
            require(factors.size == 3 && factors.all { it.isFinite() && it >= 0f })
            val t = intensity.toFloat() / 100f
            return if (active) factors[c] * ((1f - t) + t * base) else factors[c]
        }

        fun fromGains(red: Float, green: Float, blue: Float): NativeWhiteBalanceRgb {
            val gains = floatArrayOf(red, green, blue)
            require(gains.all { it.isFinite() && it >= 0f && it < 1073741824f })
            var doubled = maxOf(red, green, blue)
            var shift = 30
            for (candidate in 0 until 30) {
                doubled += doubled
                if (doubled > 1073741824f) { shift = candidate; break }
            }
            val scale = (1 shl shift).toFloat()
            val coefficients = IntArray(3) { (gains[it] * scale).toInt() }
            return NativeWhiteBalanceRgb(red, green, blue, shift, coefficients)
        }
    }
}
