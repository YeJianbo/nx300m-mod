package dev.cxo.filmpack.core

import kotlin.math.min
import kotlin.math.pow

/** Recovered postcurve preparation, distinct from the original film-curve storage helpers.
 * Engine 0x4bbe04 copies the last Float sample from the preceding sample; 0x4b2dc0
 * adds 0.5f and truncates the first 32768 samples, applies MakeLUTIncreasing, then
 * calls InverseLutAndExtend. This bounded transcription rejects non-finite values
 * and signed16 overflow instead of reproducing native invalid/overflow behavior.
 * Static-reference agreement does not establish native or Android rendering parity.
 */
object NativePostCurves {
    private const val Q15_MAX = 32767f
    private const val SAMPLE_COUNT = 32768

    /**
     * Build the RAW forward postcurve from a normalized Q15 film curve.
     *
     * The native RAW worker applies the scalar RenderingData-EV shoulder,
     * samples the 32769-entry film curve, then undoes its scalar Naka encoding.
     * The returned array keeps the native guard sample. This method is a
     * parameter builder; it does not fit a curve from output pixels.
     */
    fun buildForwardPostCurve(filmCurve: FloatArray, renderingEv: Float): FloatArray {
        require(filmCurve.size == NativeCurves.SAMPLE_COUNT + 1)
        require(renderingEv.isFinite() && filmCurve.all(Float::isFinite))
        val gain = 2.0.pow((.84f - renderingEv).toDouble()).toFloat()
        val u = min(1f / gain, 1f)
        val v = min(gain, 1f)
        val k = .3f * u
        val b = .3f * v
        val ru = (1f - u) / (1f - k)
        val rv = (1f - v) / (1f - k)
        val out = FloatArray(SAMPLE_COUNT + 1)
        for (i in 0 until SAMPLE_COUNT) {
            // Native f6560 uses FMUL for the input domain, then FDIV after
            // fused film-curve interpolation. Preserve both rounding sites.
            val input = i.toFloat() * (1f / Q15_MAX)
            val d = input - k
            val shoulder = if (input < k) gain * input else {
                val numerator = FiniteFloatFma.unchecked(d, rv, v) - b
                val denominator = FiniteFloatFma.unchecked(d, ru, u) - k
                b + d * numerator / denominator
            }
            val position = shoulder.coerceIn(0f, 1f) * Q15_MAX
            val low = position.toInt().coerceIn(0, SAMPLE_COUNT - 1)
            val fraction = position - low.toFloat()
            val film = FiniteFloatFma.unchecked(fraction, filmCurve[low + 1] - filmCurve[low], filmCurve[low]) / Q15_MAX
            out[i] = (film * .09090909f / FiniteFloatFma.unchecked(-film, .9090909f, 1f)) * Q15_MAX
        }
        out[SAMPLE_COUNT] = out[SAMPLE_COUNT - 1]
        return out
    }

    /** Three-channel RAW post builder. Unlike the shared branch, the neutral
     * input triplet passes the film post matrix and extra gamma/matrix stage
     * before joint Naka inverse. Forward channel curves may be non-monotonic;
     * only the composed-inverse builder applies MakeLUTIncreasing.
     */
    fun buildChannelForwardPostCurves(
        filmCurves: Array<FloatArray>, renderingEv: Float,
        postMatrix: FloatArray, extraMatrix: FloatArray,
    ): Array<FloatArray> {
        require(filmCurves.size == 3 && filmCurves.all { it.size == SAMPLE_COUNT + 1 && it.all(Float::isFinite) })
        require(renderingEv.isFinite() && postMatrix.size == 9 && extraMatrix.size == 9)
        require(postMatrix.all(Float::isFinite) && extraMatrix.all(Float::isFinite))
        val gain = 2.0.pow((.84f - renderingEv).toDouble()).toFloat()
        val u = min(1f / gain, 1f); val v = min(gain, 1f)
        val k = .3f * u; val b = .3f * v
        val ru = (1f - u) / (1f - k); val rv = (1f - v) / (1f - k)
        val out = Array(3) { FloatArray(SAMPLE_COUNT + 1) }
        val rgb = FloatArray(3)
        fun matrix(m: FloatArray) {
            val r = rgb[0]; val g = rgb[1]; val blue = rgb[2]
            for (row in 0..2) {
                var value = FiniteFloatFma.unchecked(r, m[row * 3], 0f)
                value = FiniteFloatFma.unchecked(g, m[row * 3 + 1], value)
                rgb[row] = FiniteFloatFma.unchecked(blue, m[row * 3 + 2], value)
            }
        }
        for (i in 0 until SAMPLE_COUNT) {
            val input = i.toFloat() * (1f / Q15_MAX)
            val d = input - k
            val shoulder = if (input < k) gain * input else {
                val numerator = FiniteFloatFma.unchecked(d, rv, v) - b
                val denominator = FiniteFloatFma.unchecked(d, ru, u) - k
                b + d * numerator / denominator
            }
            val position = shoulder.coerceIn(0f, 1f) * Q15_MAX
            val low = position.toInt().coerceIn(0, SAMPLE_COUNT - 1)
            for (c in 0..2) rgb[c] = FiniteFloatFma.unchecked(position - low,
                filmCurves[c][low + 1] - filmCurves[c][low], filmCurves[c][low]) / Q15_MAX
            matrix(postMatrix)
            for (c in 0..2) rgb[c] = rgb[c].coerceIn(0f, 1f).pow(2.2f)
            matrix(extraMatrix)
            for (c in 0..2) {
                require(rgb[c] >= 0f)
                // The remaining native custom-pow approximation can differ
                // by a few ULPs; it is separate from these FMLA/FMLS sites.
                rgb[c] = rgb[c].pow(.45454544f)
            }
            val lo = minOf(rgb[0], rgb[1], rgb[2]); val hi = maxOf(rgb[0], rgb[1], rgb[2])
            val a = lo * .09090909f / FiniteFloatFma.unchecked(-lo, .9090909f, 1f)
            val z = hi * .09090909f / FiniteFloatFma.unchecked(-hi, .9090909f, 1f)
            val rootLow = kotlin.math.sqrt(a)
            val rootExtent = kotlin.math.sqrt(z) - rootLow
            for (c in 0..2) {
                val result = when (rgb[c]) {
                    lo -> a
                    hi -> z
                    else -> {
                        val middle = FiniteFloatFma.unchecked((rgb[c] - lo) / maxOf(hi - lo, 1.1920929E-7f), rootExtent, rootLow)
                        middle * middle
                    }
                }
                out[c][i] = (result * Q15_MAX).coerceIn(0f, Q15_MAX)
            }
        }
        for (c in 0..2) out[c][SAMPLE_COUNT] = out[c][SAMPLE_COUNT - 1]
        return out
    }

    /** Build the composed inverse carrier used by RAW HSV grid baking. */
    fun buildComposedInverse(filmCurve: FloatArray, renderingEv: Float): FloatArray =
        inverseComposedPostCurve(buildForwardPostCurve(filmCurve, renderingEv))

    /** Preserve size and all earlier raw Float bits. Return an independently owned copy. */
    fun copyPenultimateToLast(values: FloatArray): FloatArray {
        require(values.size >= 2) { "Tail copy requires at least two samples" }
        return values.copyOf().also { it[it.lastIndex] = it[it.lastIndex - 1] }
    }

    /** FADD Float(value, 0.5f), FCVTZS toward zero, then signed16 storage.
     * The name half-up describes nonnegative LUT samples only: negative samples
     * still use truncation of (value + 0.5f), not symmetric rounding or floor.
     * Samples beyond the first 32768 are not read or validated by this operation.
     */
    fun quantizeFirst32768HalfUp(values: FloatArray): ShortArray {
        require(values.size >= NativeCurves.SAMPLE_COUNT) { "Quantization requires at least 32768 samples" }
        return ShortArray(NativeCurves.SAMPLE_COUNT) { i ->
            val value = values[i]
            require(value.isFinite()) { "Non-finite postcurve sample at $i" }
            val shifted = value + 0.5f
            require(shifted.isFinite()) { "Non-finite shifted postcurve sample at $i" }
            val quantized = shifted.toInt()
            require(quantized in Short.MIN_VALUE.toInt()..Short.MAX_VALUE.toInt()) {
                "Postcurve signed16 overflow at $i"
            }
            quantized.toShort()
        }
    }

    /** Framework 0xa24a20, SampleType2 branch: signed16 prefix maximum.
     * This repairs a generated postcurve; it must not silently replace original film data.
     */
    fun makeIncreasing(values: ShortArray): ShortArray {
        require(values.isNotEmpty()) { "Cannot make an empty LUT increasing" }
        val out = values.copyOf()
        for (i in 1 until out.size) if (out[i] < out[i - 1]) out[i] = out[i - 1]
        return out
    }

    /** Apply the caller's tail copy before quantization, monotonic preparation and inverse.
     * NativeCurves performs its existing nonnegative Q15 inverse checks unchanged.
     * A 32768-element input changes its final quantized sample; a 32769-element input
     * copies only its guard. Larger arrays retain that same size-dependent behavior.
     */
    fun inverseComposedPostCurve(values: FloatArray): FloatArray {
        val tailCopied = copyPenultimateToLast(values)
        val increasing = makeIncreasing(quantizeFirst32768HalfUp(tailCopied))
        return NativeCurves.inverseInt16LutAndExtend(increasing)
    }
}
