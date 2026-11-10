package dev.cxo.filmpack.core

/** Static provider RGB scales with rendering intensity, and no color filter.
 * Recovered from GetRenderingScales and the engine's default normalization.
 * This only constructs the matrix; scene intent and the film pipeline are separate.
 */
class RawRenderingScales(red: Float, green: Float, blue: Float) {
    private val scales = floatArrayOf(red, green, blue)

    init {
        require(scales.all { it.isFinite() && it > 0f })
    }

    val requiresColorStage: Boolean get() = scales.any { it != 1f }

    fun defaultMatrix(intensity: Int = 100): FloatArray {
        require(intensity in 0..200) { "Expected rendering intensity in 0..200" }
        val strength = minOf(intensity, 100) / 100f
        val red = (1f - strength) + scales[0] * strength
        val green = (1f - strength) + scales[1] * strength
        val blue = (1f - strength) + scales[2] * strength
        // Preserve ARM64 instruction order; decompiled expressions can reorder
        // these additions and change the normalized scales by one Float ULP.
        val luminance = ((red * .34375f + 0f) + green * .40625f) + blue * .25f
        val gain = 1f / luminance
        return floatArrayOf(
            red * gain, 0f, 0f,
            0f, green * gain, 0f,
            0f, 0f, blue * gain,
        )
    }
}
