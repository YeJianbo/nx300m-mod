package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.sqrt

/** Optional RAW scales/filter matrix and gamut protection.
 * Recovered from NEON 0x13234c. Zero intent still applies the matrix/protection.
 * Matrix and scene intent must come from the selected provider and RAW context.
 */
class RawScaleFilterStage(matrix: FloatArray, intent: Float) {
    private val matrix = matrix.copyOf()
    private val lower: Float
    private val upper: Float
    private val denominator: Float

    init {
        require(matrix.size == 9 && matrix.all(Float::isFinite))
        require(intent.isFinite())
        val a = (intent.coerceIn(0f, 1f) + .01f) / 1.01f
        val a3 = (a * a) * a
        lower = 1f - a
        upper = a * 7f + 1f
        denominator = ((a * a3) * a3) * 16777216f
    }

    /** Interleaved linear working RGB. Exact in-place operation is supported. */
    fun renderLinear(source: FloatArray, destination: FloatArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all(Float::isFinite))
        for (i in source.indices step 3) {
            renderPixel(source[i], source[i + 1], source[i + 2], destination, i)
        }
    }

    /** Convenience Q15 boundary; keep Float values between this stage and HSV when possible. */
    fun renderQ15(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 }) { "Expected nonnegative signed Q15" }
        val output = FloatArray(3)
        for (i in source.indices step 3) {
            renderPixel(source[i] * (1f / 32767f), source[i + 1] * (1f / 32767f),
                source[i + 2] * (1f / 32767f), output, 0)
            for (c in 0..2) destination[i + c] =
                Math.rint((output[c] * 32767f).toDouble()).toInt().toShort()
        }
    }

    internal fun renderPixel(r: Float, g: Float, b: Float, destination: FloatArray, offset: Int) {
        matrixPixel(r, g, b, destination, offset)
        protectPixel(destination[offset], destination[offset + 1], destination[offset + 2], destination, offset)
    }

    /** The strength100 HSV branch reads these raw matrix results. Protection of
     * this earlier RGB is only the base for a later rendering-strength blend.
     */
    internal fun matrixPixel(r: Float, g: Float, b: Float, destination: FloatArray, offset: Int) {
        // Explicit Float32 operation order follows the native non-FMA row kernel.
        destination[offset] = ((r * matrix[0] + 0f) + g * matrix[1]) + b * matrix[2]
        destination[offset + 1] = ((r * matrix[3] + 0f) + g * matrix[4]) + b * matrix[5]
        destination[offset + 2] = ((r * matrix[6] + 0f) + g * matrix[7]) + b * matrix[8]
    }

    /** Protect reconstructed HSV output before post curves. The matrix is only
     * applied at the earlier input boundary, never repeated here.
     */
    internal fun protectPixel(mr: Float, mg: Float, mb: Float, destination: FloatArray, offset: Int) {
        val gray = ((mr * .34375f + 0f) + mg * .40625f) + mb * .25f
        val dr = mr - gray
        val dg = mg - gray
        val db = mb - gray
        val positive = 1f / max(1f - gray, 1.52587890625e-5f)
        val negative = -1f / max(gray, 1.52587890625e-5f)
        val nr = dr * if (dr < 0f) negative else positive
        val ng = dg * if (dg < 0f) negative else positive
        val nb = db * if (db < 0f) negative else positive
        val rr = nr * nr
        val gg = ng * ng
        val bb = nb * nb
        val radius = sqrt(sqrt((gg * gg + rr * rr) + bb * bb))
        val factor = when {
            radius <= lower -> 1f
            radius >= upper -> 1f / radius
            else -> {
                val d = radius - upper
                val d2 = d * d
                val d4 = d2 * d2
                (1f / radius) * (1f - (d4 * d4) / denominator)
            }
        }
        destination[offset] = (gray + dr * factor).coerceIn(0f, 1f)
        destination[offset + 1] = (gray + dg * factor).coerceIn(0f, 1f)
        destination[offset + 2] = (gray + db * factor).coerceIn(0f, 1f)
    }
}
