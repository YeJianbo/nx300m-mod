package dev.cxo.filmpack.core

import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** RAW linear working Q15 -> encoded working Q15; output ICC conversion is still required.
 *
 * This is the HSV/shared-or-channel-post branch recovered from NEON 0x13234c.
 * It does not select a camera, construct a film grid, apply JPEG entry transforms, or decode RAW.
 * Caller supplies a repaired grid in [S17,H91,V12,component3] order (V is fastest),
 * with H-delta in degrees and S/V multipliers, plus a 32769-element shared or per-channel
 * Float Q15 postcurve. Per-channel curves are applied independently to reconstructed RGB;
 * the shared curve preserves the existing extrema/interior interpolation behavior.
 * Native grid memory has S fastest and FOUR components: explicitly transpose it before calling.
 * This parameter-only renderer contains no captured photograph/camera/film-specific values.
 * Constructor accepts only the validated repaired-grid/finite-post shape; other RAW branches are not covered. BW uses the native
 * fixed-weight gray operation after postcurves/strength blending and before Naka encoding. The optional channel curves are intended for the CXOHSV03
 * three-curve branch (for example FujiSuperia200E6).
 */
class RawHsvFilmRenderer(
    gridShv: FloatArray,
    sharedPostQ15: FloatArray,
    private val allowNegativeMultipliers: Boolean = false,
    channelPostCurves: Array<FloatArray>? = null,
    private val runtimeBw: Boolean = false,
    private val colorStage: RawScaleFilterStage? = null,
) {
    private val grid = gridShv.copyOf()
    private val post = sharedPostQ15.copyOf()
    private val channelPost = channelPostCurves?.let { tables ->
        require(tables.size == 3) { "Expected three postcurves in R/G/B order" }
        Array(3) { tables[it].copyOf() }
    }

    init {
        require(grid.size == 17 * 91 * 12 * 3) { "Expected repaired S17,H91,V12,3 grid" }
        for (i in grid.indices step 3) {
            require(grid[i].isFinite() && grid[i] in -360f..360f) { "Invalid or unrepaired hue delta" }
            require(grid[i + 1].isFinite() && (allowNegativeMultipliers || grid[i + 1] >= 0f)) { "Invalid saturation multiplier" }
            require(grid[i + 2].isFinite() && (allowNegativeMultipliers || grid[i + 2] >= 0f)) { "Invalid value multiplier" }
        }
        require(post.size == 32769 && post.all { it.isFinite() && it in 0f..32767f }) {
            "Expected extended Float Q15 shared postcurve"
        }
        channelPost?.forEach { table ->
            require(table.size == 32769 && table.all { it.isFinite() && it in 0f..32767f }) {
                "Expected extended Float Q15 channel postcurve"
            }
        }
    }

    /** Interleaved RGB codes0..32767. Supports exact in-place use; no per-pixel allocation.
     * Parameters are copied at construction; rendering has no shared mutable scratch.
     * The source must be the RawSensorToWorking output, never encoded sRGB or unsigned RGB16.
     * Intensity 0..200 uses the native blend capped at 100. Any supplied colorStage
     * must have been constructed for this same intensity; intensity is not a grid parameter.
     */
    fun renderQ15(source: ShortArray, destination: ShortArray, intensity: Int = 100) {
        require(intensity in 0..200) { "Expected rendering intensity in 0..200" }
        val strength = min(intensity, 100) / 100f
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 }) { "Expected nonnegative linear working Q15" }
        val filtered = colorStage?.let { FloatArray(3) }
        var i = 0
        while (i < source.size) {
            var r = source[i].toFloat() * RECIP
            var g = source[i + 1].toFloat() * RECIP
            var b = source[i + 2].toFloat() * RECIP
            var baseR = r
            var baseG = g
            var baseB = b
            if (filtered != null) {
                // HSV reads raw matrix RGB; its separately protected version is
                // retained only as the base for the later rendering-strength blend.
                requireNotNull(colorStage).matrixPixel(r, g, b, filtered, 0)
                r = filtered[0]; g = filtered[1]; b = filtered[2]
                if (strength < 1f) {
                    colorStage.protectPixel(r, g, b, filtered, 0)
                    baseR = filtered[0]; baseG = filtered[1]; baseB = filtered[2]
                }
            }
            val hi = max(r, max(g, b))
            val lo = min(r, min(g, b))
            val delta = hi - lo
            val divisor = if (delta == 0f) 1f else delta
            var hue = when (hi) {
                r -> (g - b) / divisor
                g -> 2f + (b - r) / divisor
                else -> 4f + (r - g) / divisor
            } * 60f
            if (hue < 0f) hue += 360f
            val saturation = delta / if (hi == 0f) 1f else hi
            // Clamp lookup coordinates only; retain raw S/V for reconstruction.
            val sc = saturation.coerceIn(0f, 1f) * 15f
            val hc = hue * .25f
            val vc = hi.coerceIn(0f, 1f) * 10f
            val si = min(sc.toInt(), 15)
            val hiIndex = min(hc.toInt(), 89)
            val vi = min(vc.toInt(), 10)
            val sf = sc - si
            val hf = hc - hiIndex
            val vf = vc - vi
            val base = ((si * 91 + hiIndex) * 12 + vi) * 3
            hue += interpolate(base, sf, hf, vf, 0)
            val sat = saturation * interpolate(base, sf, hf, vf, 1)
            val value = hi * interpolate(base, sf, hf, vf, 2)
            hue %= 360f
            if (hue < 0f) hue += 360f
            val sector = hue / 60f
            val whole = floor(sector).toInt()
            val f = sector - whole
            val p = value * (1f - sat)
            val q = value * (1f - sat * f)
            val t = value * (1f - sat * (1f - f))
            val rr: Float
            val gg: Float
            val bb: Float
            when (whole % 6) {
                0 -> { rr = value; gg = t; bb = p }
                1 -> { rr = q; gg = value; bb = p }
                2 -> { rr = p; gg = value; bb = t }
                3 -> { rr = p; gg = q; bb = value }
                4 -> { rr = t; gg = p; bb = value }
                else -> { rr = value; gg = p; bb = q }
            }
            var cr = rr.coerceIn(0f, 1f)
            var cg = gg.coerceIn(0f, 1f)
            var cb = bb.coerceIn(0f, 1f)
            if (filtered != null) {
                // HSV output protection sees reconstructed, unclipped RGB.
                // Clipping first loses the chroma/brightness needed by the
                // native gamut-protection formula at saturated grid points.
                requireNotNull(colorStage).protectPixel(rr, gg, bb, filtered, 0)
                cr = filtered[0]; cg = filtered[1]; cb = filtered[2]
            }
            var pr: Float
            var pg: Float
            var pb: Float
            if (channelPost == null) {
                val first = min(cr, min(cg, cb))
                val last = max(cr, max(cg, cb))
                val a = samplePost(first)
                val z = samplePost(last)
                val extent = z - a
                val span = max(last - first, EPS)
                pr = (a + extent * ((cr - first) / span)).coerceIn(0f, 1f)
                pg = (a + extent * ((cg - first) / span)).coerceIn(0f, 1f)
                pb = (a + extent * ((cb - first) / span)).coerceIn(0f, 1f)
            } else {
                pr = samplePost(cr, channelPost[0])
                pg = samplePost(cg, channelPost[1])
                pb = samplePost(cb, channelPost[2])
            }
            // NEON 0x13234c applies the HSV postcurve before blending. The
            // runtime BW and Naka operations remain active even at intensity 0.
            if (strength < 1f) {
                pr = (baseR + (pr - baseR) * strength).coerceIn(0f, 1f)
                pg = (baseG + (pg - baseG) * strength).coerceIn(0f, 1f)
                pb = (baseB + (pb - baseB) * strength).coerceIn(0f, 1f)
            }
            if (runtimeBw) {
                val gray = (pr * .390625f + 0f + pg * .546875f) + pb * .0625f
                pr = gray; pg = gray; pb = gray
            }
            val nlo = min(pr, min(pg, pb))
            val nhi = max(pr, max(pg, pb))
            val na = nlo * 11f / (1f + nlo * 10f)
            val nz = nhi * 11f / (1f + nhi * 10f)
            val rootLow = sqrt(nlo)
            val rootSpan = max(sqrt(nhi) - rootLow, EPS)
            destination[i] = encode(na + (nz - na) * ((sqrt(pr) - rootLow) / rootSpan))
            destination[i + 1] = encode(na + (nz - na) * ((sqrt(pg) - rootLow) / rootSpan))
            destination[i + 2] = encode(na + (nz - na) * ((sqrt(pb) - rootLow) / rootSpan))
            i += 3
        }
    }

    private fun samplePost(x: Float): Float {
        return samplePost(x, post)
    }

    private fun samplePost(x: Float, table: FloatArray): Float {
        val p = x * 32767f
        val i = p.toInt()
        return (table[i] + (p - i) * (table[i + 1] - table[i])) * RECIP
    }

    private fun interpolate(base: Int, s: Float, h: Float, v: Float, c: Int): Float {
        val a = base + c
        val sh = 91 * 12 * 3
        val hh = 12 * 3
        val s0h0 = grid[a + 3] * v + grid[a] * (1f - v)
        val s0h1 = grid[a + hh + 3] * v + grid[a + hh] * (1f - v)
        val s1h0 = grid[a + sh + 3] * v + grid[a + sh] * (1f - v)
        val s1h1 = grid[a + sh + hh + 3] * v + grid[a + sh + hh] * (1f - v)
        return (s1h1 * h + s1h0 * (1f - h)) * s +
            (s0h1 * h + s0h0 * (1f - h)) * (1f - s)
    }

    private fun encode(x: Float): Short =
        Math.rint((x.coerceIn(0f, 1f) * 32767f).toDouble()).toInt().toShort()

    private companion object {
        const val RECIP = 1f / 32767f
        const val EPS = 1.1920928955078125e-7f
    }
}
