package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sqrt

/** RAW camera-linear Q15 -> linear working RGB Q15. This is not a film or display transform.
 * Recovered from NEON 0xf4d34 / 0x11d2d0; no camera profile is selected implicitly.
 * Input has already undergone demosaic, camera WB and ADC/black-level normalization.
 * Kept separate from the JPEG entry path until the complete RAW pipeline is verified.
 */
class RawSensorToWorking(matrix: FloatArray, exposureEv: Float, highlightControl: Float) {
    // .84 is the neutral reference used by the recovered shoulder formula,
    // not a product default. Callers must pass the EV/highlight values from
    // the selected RAW RenderingData context.
    private val m = matrix.copyOf()
    private val gain: Float
    private val u: Float
    private val v: Float
    private val knee: Float
    private val lower: Float
    private val ratioU: Float
    private val ratioV: Float
    private val low: Float
    private val high: Float
    private val p: Float
    private val q: Float
    private val s: Float

    init {
        require(m.size == 9 && m.all { it.isFinite() })
        require(exposureEv.isFinite() && highlightControl.isFinite())
        gain = 2.0.pow((exposureEv - .84f).toDouble()).toFloat()
        require(gain.isFinite() && gain > 0f && (1f / gain).isFinite())
        u = min(1f / gain, 1f)
        v = min(gain, 1f)
        knee = u * .3f
        lower = v * .3f
        ratioU = (1f - u) / (1f - knee)
        ratioV = (1f - v) / (1f - knee)
        val a = (highlightControl.coerceIn(0f, 1f) + .01f) / 1.01f
        low = 1f - a
        high = 1f + a
        p = -.25f / a
        q = p * high * -2f
        s = high * high * p + 1f
    }

    /** Interleaved RGB; supports exact in-place use, without per-pixel allocations.
     * Codes must be nonnegative signed Q15 (0..32767), not RGB16 unsigned carriers.
     */
    fun renderQ15(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 }) { "Expected camera-linear Q15, not unsigned RGB16" }
        var i = 0
        while (i < source.size) {
            val r = source[i].toFloat() * (1f / 32767f)
            val g = source[i + 1].toFloat() * (1f / 32767f)
            val b = source[i + 2].toFloat() * (1f / 32767f)
            val first = min(r, min(g, b))
            val last = max(r, max(g, b))
            val eFirst = exposure(first)
            val range = exposure(last) - eFirst
            val divisor = max(last - first, 1.1920928955078125e-7f)
            val er = eFirst + range * ((r - first) / divisor)
            val eg = eFirst + range * ((g - first) / divisor)
            val eb = eFirst + range * ((b - first) / divisor)
            // Preserve scalar Float operation order; don't contract into FMA.
            val mr = ((er * m[0] + 0f) + eg * m[1]) + eb * m[2]
            val mg = ((er * m[3] + 0f) + eg * m[4]) + eb * m[5]
            val mb = ((er * m[6] + 0f) + eg * m[7]) + eb * m[8]
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
            destination[i] = q15(gray + dr * factor)
            destination[i + 1] = q15(gray + dg * factor)
            destination[i + 2] = q15(gray + db * factor)
            i += 3
        }
    }

    private fun exposure(x: Float): Float {
        if (x < knee) return x * gain
        val d = x - knee
        return lower + d * ((v + d * ratioV) - lower) / ((u + d * ratioU) - knee)
    }

    private fun q15(value: Float): Short =
        Math.rint((value.coerceIn(0f, 1f) * 32767f).toDouble()).toInt().toShort()
}
