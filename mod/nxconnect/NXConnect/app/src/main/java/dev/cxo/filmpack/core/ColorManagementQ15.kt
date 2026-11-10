package dev.cxo.filmpack.core

/**
 * The numeric boundary observed in DxOFramework::Color::Convert.
 *
 * This class deliberately does not implement an ICC engine. The caller owns
 * the profile transform; this wrapper makes the observed float/int16 boundary
 * explicit and testable before an Android or native CMM is selected.
 */
object ColorManagementQ15 {
    const val SCALE: Float = 32767f
    const val MIN_INT16: Int = -32768
    const val MAX_INT16: Int = 32767
    // ARM64 Color::Convert 0x34260 loads these exact bits and multiplies.
    // Float division differs for 768 of the 32768 nonnegative codes.
    val INVERSE_SCALE: Float = Float.fromBits(0x38000100)

    /** Generic saturating truncation; this is NOT Color::Convert's input rounding. */
    fun toInt16(value: Float): Short {
        require(value.isFinite()) { "Color value must be finite" }
        return value.coerceIn(MIN_INT16.toFloat(), MAX_INT16.toFloat()).toInt().toShort()
    }

    /** FRINTX with the observed default FPCR nearest-even, followed by FCVTZS. */
    fun quantizeNormalized(value: Float): Short {
        require(value.isFinite()) { "Color value must be finite" }
        val scaled = (value * SCALE).coerceIn(MIN_INT16.toFloat(), MAX_INT16.toFloat())
        return Math.rint(scaled.toDouble()).toInt().toShort()
    }

    fun fromInt16(value: Short): Float = value.toInt().toFloat() * INVERSE_SCALE

    /** Quantize one RGB triple, invoke the supplied profile transform, decode. */
    fun convert(rgb: FloatArray, transform: (ShortArray) -> ShortArray): FloatArray {
        require(rgb.size == 3) { "Expected RGB triplet" }
        val encoded = ShortArray(3) { i -> quantizeNormalized(rgb[i]) }
        val transformed = transform(encoded)
        require(transformed.size == 3) { "Profile transform must return RGB triplet" }
        return FloatArray(3) { i -> fromInt16(transformed[i]) }
    }
}
