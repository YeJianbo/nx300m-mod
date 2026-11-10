package dev.cxo.filmpack.core

import java.util.concurrent.CancellationException
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow

/** Native RAW scales/filter scene intent. Input is WB-adjusted, normalized
 * camera-linear signed Q15 BEFORE RawSensorToWorking or film processing.
 * The estimator uses the camera RawFidelityProfile.sensorToWorking matrix and
 * camera sensorExposureBiasEv - .84f, independent of the selected film's EV.
 */
object RawColorIntentEstimator {
    data class SampledImage(val width: Int, val height: Int, val pixels: ShortArray)

    /** Original CreateSubsampledImage policy, preserving size below 320 pixels. */
    fun sampledDimensions(width: Int, height: Int): Pair<Int, Int> {
        require(width > 0 && height > 0)
        val longest = max(320, max(width, height))
        val outWidth = (width.toLong() * 320L / longest).toInt()
        val outHeight = (height.toLong() * 320L / longest).toInt()
        require(outWidth > 0 && outHeight > 0) { "Source aspect ratio is too extreme" }
        return outWidth to outHeight
    }

    /** Native nonweighted mean windows, including noninteger scaling ratios.
     * readRow fills a reusable interleaved RGB Q15 row. No full-frame buffer is allocated.
     */
    fun meanDownsampleQ15(
        width: Int,
        height: Int,
        readRow: (row: Int, destination: ShortArray) -> Unit,
        isCancelled: () -> Boolean = { false },
    ): SampledImage {
        val (ow, oh) = sampledDimensions(width, height)
        val xs = windows(width, ow)
        val ys = windows(height, oh)
        val sourceRow = ShortArray(Math.multiplyExact(width, 3))
        val sums = LongArray(ow * 3)
        val result = ShortArray(ow * oh * 3)
        for (y in 0 until oh) {
            if (isCancelled()) throw CancellationException("RAW color intent cancelled")
            sums.fill(0)
            for (sy in ys[y * 2] until ys[y * 2 + 1]) {
                if (isCancelled()) throw CancellationException("RAW color intent cancelled")
                readRow(sy, sourceRow)
                require(sourceRow.all { it >= 0 }) { "Expected nonnegative camera-linear Q15" }
                for (x in 0 until ow) {
                    var sr = 0L; var sg = 0L; var sb = 0L
                    for (sx in xs[x * 2] until xs[x * 2 + 1]) {
                        val i = sx * 3
                        sr += sourceRow[i]; sg += sourceRow[i + 1]; sb += sourceRow[i + 2]
                    }
                    sums[x * 3] += sr; sums[x * 3 + 1] += sg; sums[x * 3 + 2] += sb
                }
            }
            for (x in 0 until ow) {
                val count = (xs[x * 2 + 1] - xs[x * 2]).toLong() * (ys[y * 2 + 1] - ys[y * 2])
                for (c in 0..2) result[(y * ow + x) * 3 + c] = (sums[x * 3 + c] / count).toShort()
            }
        }
        return SampledImage(ow, oh, result)
    }

    fun meanDownsampleQ15(
        source: ShortArray,
        width: Int,
        height: Int,
        isCancelled: () -> Boolean = { false },
    ): SampledImage {
        require(source.size.toLong() == width.toLong() * height * 3)
        return meanDownsampleQ15(width, height, { y, row ->
            source.copyInto(row, 0, y * width * 3, (y + 1) * width * 3)
        }, isCancelled)
    }

    /** No subsampling here: supply the original meanDownsampleQ15 result.
     * nativeExposureEv is the exponent passed to exp2, not the film rendering EV.
     * Result is the native fraction 0..1, before its integer-percent dictionary roundtrip.
     */
    fun estimateSampledQ15(
        source: ShortArray,
        sensorMatrix: FloatArray,
        nativeExposureEv: Float,
        scaleMatrix: FloatArray,
        isCancelled: () -> Boolean = { false },
    ): Float = requireNotNull(estimateIntentsSampledQ15(
        source, sensorMatrix, nativeExposureEv, scaleMatrix, false, isCancelled,
    ).scalesAndFilter)

    data class Intents(val rendering: Float?, val scalesAndFilter: Float?)

    /** Original pair of optional estimates, sharing exposure/sensor work.
     * rendering is the camera gamut/highlight protection statistic. Original
     * (rendering disabled) does not request it; native BW may calculate it but
     * ColorRendering ignores it and uses highlight=0. Callers decide activation.
     * Fractions are before throughNativePercent, which must be applied before
     * supplying RawSensorToWorking.highlightControl or the scales/filter stage.
     */
    fun estimateIntentsSampledQ15(
        source: ShortArray,
        sensorMatrix: FloatArray,
        nativeExposureEv: Float,
        scaleMatrix: FloatArray? = null,
        estimateRenderingIntent: Boolean = true,
        isCancelled: () -> Boolean = { false },
    ): Intents {
        require(source.size % 3 == 0 && source.size / 3 >= 10)
        require(source.all { it >= 0 }) { "Expected nonnegative camera-linear Q15" }
        require(sensorMatrix.size == 9 && sensorMatrix.all(Float::isFinite))
        require(scaleMatrix == null || (scaleMatrix.size == 9 && scaleMatrix.all(Float::isFinite)))
        require(nativeExposureEv.isFinite())
        val gain = 2.0.pow(nativeExposureEv.toDouble()).toFloat()
        require(gain.isFinite() && gain > 0f && (1f / gain).isFinite())
        val u = min(1f / gain, 1f); val v = min(gain, 1f)
        val knee = u * .3f; val lower = v * .3f
        val ratioU = (1f - u) / (1f - knee); val ratioV = (1f - v) / (1f - knee)
        fun exposure(x: Float): Float {
            if (x < knee) return x * gain
            val d = x - knee
            return lower + d * ((v + d * ratioV) - lower) / ((u + d * ratioU) - knee)
        }
        val renderingHistogram = if (estimateRenderingIntent) IntArray(200) else null
        val scalesHistogram = if (scaleMatrix != null) IntArray(200) else null
        fun bin(r: Float, g: Float, b: Float, epsilonGain: Float): Int {
            val gray = ((r * .34375f + 0f) + g * .40625f) + b * .25f
            val epsilon = gray * epsilonGain + .00390625f
            val negative = -1f / (gray + epsilon)
            val positive = 1f / ((1f - gray) + epsilon)
            val dr = r - gray; val dg = g - gray; val db = b - gray
            val nr = dr * if (dr < 0f) negative else positive
            val ng = dg * if (dg < 0f) negative else positive
            val nb = db * if (db < 0f) negative else positive
            return (max(nr, max(ng, nb)) * 100f).coerceIn(0f, 199f).toInt()
        }
        if (renderingHistogram == null && scalesHistogram == null) return Intents(null, null)
        for (i in source.indices step 3) {
            if (i % 3072 == 0 && isCancelled()) throw CancellationException("RAW color intent cancelled")
            val r = source[i] * (1f / 32767f); val g = source[i + 1] * (1f / 32767f); val b = source[i + 2] * (1f / 32767f)
            val lo = min(r, min(g, b)); val hi = max(r, max(g, b))
            val a = exposure(lo); val extent = exposure(hi) - a
            val span = max(hi - lo, 1.1920928955078125e-7f)
            val er = a + extent * ((r - lo) / span)
            val eg = a + extent * ((g - lo) / span)
            val eb = a + extent * ((b - lo) / span)
            val rawR = ((er * sensorMatrix[0] + 0f) + eg * sensorMatrix[1]) + eb * sensorMatrix[2]
            val rawG = ((er * sensorMatrix[3] + 0f) + eg * sensorMatrix[4]) + eb * sensorMatrix[5]
            val rawB = ((er * sensorMatrix[6] + 0f) + eg * sensorMatrix[7]) + eb * sensorMatrix[8]
            // First intent sees unbounded sensor RGB, with no scales transform.
            if (renderingHistogram != null) renderingHistogram[bin(rawR, rawG, rawB, .03125f)]++
            if (scaleMatrix != null && scalesHistogram != null) {
                val mr = rawR.coerceIn(0f, 1f); val mg = rawG.coerceIn(0f, 1f); val mb = rawB.coerceIn(0f, 1f)
                val cr = ((mr * scaleMatrix[0] + 0f) + mg * scaleMatrix[1]) + mb * scaleMatrix[2]
                val cg = ((mr * scaleMatrix[3] + 0f) + mg * scaleMatrix[4]) + mb * scaleMatrix[5]
                val cb = ((mr * scaleMatrix[6] + 0f) + mg * scaleMatrix[7]) + mb * scaleMatrix[8]
                scalesHistogram[bin(cr, cg, cb, .125f)]++
            }
        }
        fun select(histogram: IntArray?): Float? {
            if (histogram == null) return null
            var count = 0
            for (bin in 199 downTo 0) {
                count += histogram[bin]
                if (count >= 10) return ((bin - 99).toFloat() / 100f + .1f).coerceIn(0f, 1f)
            }
            error("Expected at least ten input pixels")
        }
        return Intents(select(renderingHistogram), select(scalesHistogram))
    }

    /** Native estimator output is truncated to percent before ColorRendering reads it. */
    fun throughNativePercent(intent: Float): Float {
        require(intent.isFinite() && intent in 0f..1f)
        return (intent * 100f).toInt().toFloat() / 100f
    }

    private fun windows(source: Int, output: Int): IntArray {
        val ratio = source.toFloat() / output.toFloat()
        val span = ceil(ratio.toDouble()).toInt()
        val offset = (ratio * .5f).toInt() - span / 2
        return IntArray(output * 2).also { bounds ->
            for (i in 0 until output) {
                val start = max(0, (ratio * i + .5f).toInt() + offset)
                bounds[i * 2] = start
                bounds[i * 2 + 1] = min(source, start + span)
            }
        }
    }
}
