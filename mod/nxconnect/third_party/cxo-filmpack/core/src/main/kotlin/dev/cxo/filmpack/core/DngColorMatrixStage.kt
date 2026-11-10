package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.sqrt

/** DNG ColorRendering useCase=1 matrix/gamut stage (NEON 0x10648c).
 * Input is AdobeColorPart1 RGB Q15, with the matrix supplied by the current DNG
 * context. Unlike the Bayer stage this applies no exposure shoulder.
 * The DNG profile's tone/look pipeline follows this stage separately.
 */
class DngColorMatrixStage(matrix: FloatArray, private val whiteLevel: Float, highlightControl: Float) {
    private val m = matrix.copyOf()
    private val a = (highlightControl.coerceIn(0f, 1f) + .01f) / 1.01f
    private val low = 1f - a
    private val high = 1f + a
    private val p = -.25f / a
    private val q = p * high * -2f
    private val s = high * high * p + 1f

    init {
        require(m.size == 9 && m.all { it.isFinite() })
        require(whiteLevel.isFinite() && whiteLevel > 0f && highlightControl.isFinite())
    }

    fun renderQ15(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 }) { "Expected nonnegative DNG Q15" }
        val reciprocal = 1f / whiteLevel
        val pixel = FloatArray(3)
        var i = 0
        while (i < source.size) {
            val r = source[i].toFloat() * reciprocal
            val g = source[i + 1].toFloat() * reciprocal
            val b = source[i + 2].toFloat() * reciprocal
            renderNormalized(r, g, b, pixel, 0)
            destination[i] = encode(pixel[0])
            destination[i + 1] = encode(pixel[1])
            destination[i + 2] = encode(pixel[2])
            i += 3
        }
    }

    internal fun renderNormalized(r: Float, g: Float, b: Float, destination: FloatArray, offset: Int) {
        val mr = ((r * m[0] + 0f) + g * m[1]) + b * m[2]
        val mg = ((r * m[3] + 0f) + g * m[4]) + b * m[5]
        val mb = ((r * m[6] + 0f) + g * m[7]) + b * m[8]
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
            radius <= low -> 1f
            radius >= high -> 1f / radius
            else -> (q + radius * p) + (1f / radius) * s
        }
        destination[offset] = (gray + dr * factor).coerceIn(0f, 1f)
        destination[offset + 1] = (gray + dg * factor).coerceIn(0f, 1f)
        destination[offset + 2] = (gray + db * factor).coerceIn(0f, 1f)
    }

    private fun encode(value: Float): Short =
        Math.rint((value.coerceIn(0f, 1f) * 32767f).toDouble()).toInt().toShort()
}
