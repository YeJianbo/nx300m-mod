package dev.cxo.filmpack.core

/**
 * Unquantized reference-camera fidelity requested by a RAW film provider.
 *
 * These endpoints belong to the provider's target camera, not the camera that
 * captured the photo. The engine applies its feature-5 matrix later when it
 * builds RawRenderingParameters. This class performs neither that transform
 * nor sensor row-sum quantization.
 */
class RawTargetFidelityProfile(
    tungsten: FloatArray,
    daylight: FloatArray,
    tempLow: Float,
    tempHigh: Float,
) {
    private val tungsten = checkedMatrix(tungsten, "tungsten")
    private val daylight = checkedMatrix(daylight, "daylight")
    private val low = checkedTemperature(tempLow, "tempLow")
    private val high = checkedTemperature(tempHigh, "tempHigh")

    init {
        require(low < high) { "tempLow must be below tempHigh" }
    }

    /** Fresh row-major 3x3 matrix; endpoint clamping follows the native getter. */
    fun matrixAt(temperature: Float): FloatArray {
        checkedTemperature(temperature, "temperature")
        return when {
            temperature <= low -> tungsten.copyOf()
            temperature >= high -> daylight.copyOf()
            else -> {
                val fraction = (temperature - low) / (high - low)
                val opposite = 1f - fraction
                FloatArray(9) { i ->
                    // Native FMUL, FMUL, FADD: retain Float32 operand order.
                    val day = daylight[i] * fraction
                    val warm = tungsten[i] * opposite
                    day + warm
                }.also { result ->
                    require(result.all(Float::isFinite)) { "Target fidelity interpolation overflow" }
                }
            }
        }
    }

    private fun checkedMatrix(source: FloatArray, name: String): FloatArray {
        require(source.size == 9 && source.all(Float::isFinite)) { "$name must be a finite 3x3 matrix" }
        return source.copyOf()
    }

    private fun checkedTemperature(value: Float, name: String): Float {
        require(value.isFinite() && value > 0f) { "$name must be finite and positive" }
        return value
    }
}
