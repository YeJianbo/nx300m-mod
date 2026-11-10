package dev.cxo.filmpack.core

import kotlin.math.pow

enum class Interpolation { TRILINEAR, TETRAHEDRAL }
enum class OutputSpace { NATIVE, SRGB }

/** Fixed measured transform; RGB input is encoded in the recorded source sRGB ICC.
 * No strength, RAW development, grain, alpha, orientation or Android I/O is implied.
 */
class FilmLut internal constructor(
    val size: Int,
    val sourceIccSha256: String,
    val nativeIccSha256: String,
    private val axes: Array<DoubleArray>,
    private val nodes: FloatArray,
    private val nativeGamma: Double,
    private val nativeLinearToSrgbLinear: DoubleArray,
    private val srgbTrc: DoubleArray,
) {
    /** Writes to caller-owned storage; no per-sample temporary array allocation. */
    fun sampleNative(
        r: Double, g: Double, b: Double, out: DoubleArray, offset: Int = 0,
        interpolation: Interpolation = Interpolation.TETRAHEDRAL,
    ) {
        require(offset >= 0 && offset + 3 <= out.size)
        require(r.isFinite() && g.isFinite() && b.isFinite())
        require(r in 0.0..1.0 && g in 0.0..1.0 && b in 0.0..1.0) {
            "Input lies outside the measured encoded-sRGB domain"
        }
        val ri = lowerCell(axes[0], r); val gi = lowerCell(axes[1], g)
        val bi = lowerCell(axes[2], b)
        val fr = (r - axes[0][ri]) / (axes[0][ri + 1] - axes[0][ri])
        val fg = (g - axes[1][gi]) / (axes[1][gi + 1] - axes[1][gi])
        val fb = (b - axes[2][bi]) / (axes[2][bi + 1] - axes[2][bi])
        val rs = size * size * 3; val gs = size * 3; val bs = 3
        val base = ((ri * size + gi) * size + bi) * 3
        if (interpolation == Interpolation.TRILINEAR) {
            for (c in 0..2) {
                var result = 0.0
                for (cr in 0..1) for (cg in 0..1) for (cb in 0..1) {
                    val w = (if (cr == 0) 1 - fr else fr) *
                        (if (cg == 0) 1 - fg else fg) * (if (cb == 0) 1 - fb else fb)
                    result += nodes[base + cr * rs + cg * gs + cb * bs + c].toDouble() * w
                }
                out[offset + c] = result
            }
        } else {
            val first: Int; val second: Int
            val high: Double; val middle: Double; val low: Double
            if (fr >= fg) {
                if (fg >= fb) {
                    first = rs; second = gs; high = fr; middle = fg; low = fb
                } else if (fr >= fb) {
                    first = rs; second = bs; high = fr; middle = fb; low = fg
                } else {
                    first = bs; second = rs; high = fb; middle = fr; low = fg
                }
            } else {
                if (fr >= fb) {
                    first = gs; second = rs; high = fg; middle = fr; low = fb
                } else if (fg >= fb) {
                    first = gs; second = bs; high = fg; middle = fb; low = fr
                } else {
                    first = bs; second = gs; high = fb; middle = fg; low = fr
                }
            }
            for (c in 0..2) {
                out[offset + c] = nodes[base + c].toDouble() * (1 - high) +
                    nodes[base + first + c].toDouble() * (high - middle) +
                    nodes[base + first + second + c].toDouble() * (middle - low) +
                    nodes[base + rs + gs + bs + c].toDouble() * low
            }
        }
    }

    /** Matrix/TRC pair recovered from the recorded profiles, then optional final clipping.
     * This is a specific profile pair, not a generic ICC parser or Android ColorSpace adapter.
     */
    fun nativeToSrgb(rgb: DoubleArray, offset: Int = 0, clip: Boolean = true) {
        require(offset >= 0 && offset + 3 <= rgb.size)
        val r = signedPow(rgb[offset], nativeGamma)
        val g = signedPow(rgb[offset + 1], nativeGamma)
        val b = signedPow(rgb[offset + 2], nativeGamma)
        for (c in 0..2) {
            val i = c * 3
            val linear = nativeLinearToSrgbLinear[i] * r +
                nativeLinearToSrgbLinear[i + 1] * g + nativeLinearToSrgbLinear[i + 2] * b
            val encoded = if (linear >= srgbTrc[3] * srgbTrc[4]) {
                (linear.coerceAtLeast(0.0).pow(1.0 / srgbTrc[0]) - srgbTrc[2]) / srgbTrc[1]
            } else linear / srgbTrc[3]
            rgb[offset + c] = if (clip) encoded.coerceIn(0.0, 1.0) else encoded
        }
    }

    /** Packed unpremultiplied RGB floats. One reusable scratch vector per invocation.
     * Call from a worker; no Android thread, cancellation or image metadata handling lives here.
     */
    fun renderRgb(
        source: FloatArray, destination: FloatArray,
        outputSpace: OutputSpace = OutputSpace.SRGB,
        interpolation: Interpolation = Interpolation.TETRAHEDRAL,
    ) {
        require(source.size % 3 == 0 && destination.size == source.size)
        renderRgb(source, destination, source.size / 3, outputSpace, interpolation)
    }

    /**
     * Render the first [pixelCount] packed RGB pixels in caller-owned buffers.
     * This overload lets bounded-memory adapters reuse a maximum-size tile
     * buffer for edge tiles without copying or allocating a smaller array.
     */
    fun renderRgb(
        source: FloatArray, destination: FloatArray, pixelCount: Int,
        outputSpace: OutputSpace = OutputSpace.SRGB,
        interpolation: Interpolation = Interpolation.TETRAHEDRAL,
    ) {
        require(pixelCount >= 0 && pixelCount <= source.size / 3)
        require(destination.size >= pixelCount * 3)
        val scratch = DoubleArray(3)
        var i = 0
        while (i < pixelCount * 3) {
            sampleNative(source[i].toDouble(), source[i + 1].toDouble(), source[i + 2].toDouble(),
                scratch, interpolation = interpolation)
            if (outputSpace == OutputSpace.SRGB) nativeToSrgb(scratch)
            destination[i] = scratch[0].toFloat()
            destination[i + 1] = scratch[1].toFloat()
            destination[i + 2] = scratch[2].toFloat()
            i += 3
        }
    }

    private fun lowerCell(axis: DoubleArray, x: Double): Int {
        var lo = 0; var hi = size - 1
        while (hi - lo > 1) {
            val mid = (lo + hi) ushr 1
            if (x < axis[mid]) hi = mid else lo = mid
        }
        return lo
    }

    private fun signedPow(x: Double, exponent: Double): Double =
        if (x >= 0) x.pow(exponent) else -(-x).pow(exponent)
}
