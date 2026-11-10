package dev.cxo.filmpack.core

/**
 * S1RII-compatible fidelity profile to working-matrix stage.
 *
 * The profile matrices are supplied by the camera resource adapter. This class
 * deliberately does not embed a camera JSON/resource mapping. The native path
 * blends Tungsten/Daylight fidelity endpoints, then the RAW engine applies its
 * feature-5 matrix and ForceRowSumTo1 quantization.
 */
class RawFidelityProfile(
    tungsten: FloatArray,
    daylight: FloatArray,
    tempLow: Float,
    tempHigh: Float,
    feature5Enabled: Boolean = false,
) {
    private val tungsten = checkedMatrix(tungsten, "tungsten")
    private val daylight = checkedMatrix(daylight, "daylight")
    private val low = checkedTemperature(tempLow, "tempLow")
    private val high = checkedTemperature(tempHigh, "tempHigh")
    private val feature5 = feature5Matrix(feature5Enabled)

    init {
        require(low < high) { "tempLow must be below tempHigh" }
    }

    /**
     * Returns a fresh row-major 3x3 sensor-to-working matrix.
     *
     * Temperatures outside the native interval select the corresponding
     * endpoint. Intermediate temperatures use Float32 profile blending.
     */
    fun sensorToWorking(temperature: Float): FloatArray {
        require(temperature.isFinite() && temperature > 0f) { "temperature must be finite and positive" }
        val profile = FloatArray(9)
        when {
            temperature <= low -> tungsten.copyInto(profile)
            temperature >= high -> daylight.copyInto(profile)
            else -> {
                val fraction = (temperature - low) / (high - low)
                val opposite = 1f - fraction
                for (i in profile.indices) {
                    // Keep the native BlendMatrix3x3 operand order.
                    profile[i] = daylight[i] * fraction + tungsten[i] * opposite
                }
            }
        }

        val engine = leftMultiply(feature5, profile)
        val output = FloatArray(9)
        NativeMatrixQuantization.forceRowSumsToOne(engine, output)
        return output
    }

    /** RAW engine extra matrix: quantized feature-5 transform, independent of
     * the source camera's fidelity. This is not the camera body's auxiliary
     * color matrix. Verified against S1RII and S9 default RAW interfaces.
     */
    fun engineExtraMatrix(): FloatArray = FloatArray(9).also {
        NativeMatrixQuantization.forceRowSumsToOne(feature5, it)
    }

    private fun leftMultiply(left: FloatArray, right: FloatArray): FloatArray {
        val output = FloatArray(9)
        for (row in 0..2) {
            for (column in 0..2) {
                val value = (left[row * 3] * right[column] + 0f) +
                    left[row * 3 + 1] * right[3 + column]
                output[row * 3 + column] = value + left[row * 3 + 2] * right[6 + column]
            }
        }
        return output
    }

    private fun checkedMatrix(source: FloatArray, name: String): FloatArray {
        require(source.size == 9) { "$name matrix must have 9 elements" }
        require(source.all { it.isFinite() }) { "$name matrix must be finite" }
        return source.copyOf()
    }

    private fun checkedTemperature(value: Float, name: String): Float {
        require(value.isFinite() && value > 0f) { "$name must be a finite positive temperature" }
        return value
    }

    private fun feature5Matrix(enabled: Boolean): FloatArray =
        (if (enabled) FEATURE5_ENABLED else FEATURE5_DISABLED).copyOf()

    private companion object {
        // DxOCorrectionEngine 4b31f8, FeatureToggles::Key(5) disabled.
        private val FEATURE5_DISABLED = floatArrayOf(
            0.83072674f, 0.12698068f, 0.04236905f,
            0.09652022f, 0.89108580f, 0.01238098f,
            0.02198894f, 0.03270674f, 0.94508755f,
        )

        // DxOCorrectionEngine 4b31f8, FeatureToggles::Key(5) enabled.
        private val FEATURE5_ENABLED = floatArrayOf(
            0.72188163f, 0.08861878f, 0.18949962f,
            0.13322704f, 0.83578175f, 0.03099118f,
            0.02482087f, 0.06490827f, 0.91027087f,
        )
    }
}
