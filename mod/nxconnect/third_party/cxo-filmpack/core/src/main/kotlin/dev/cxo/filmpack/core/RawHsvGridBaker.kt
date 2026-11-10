package dev.cxo.filmpack.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sqrt

/** Independent RAW HSV-grid baker transcribed from the captured NEON bake path.
 *
 * Used for runtime and asset generation. It does not call native code or fit
 * output pixels. Curves contain Float Q15 codes (0..32767), with
 * 32768 samples and one guard sample. Matrices are row-major RGB column-vector
 * matrices. The grid is packed S17,H91,V12,C3.
 */
data class RawHsvBakeParameters(
    val ev: Float,
    val renderingEv: Float,
    val preMatrix: FloatArray,
    val postMatrix: FloatArray,
    val extraMatrix: FloatArray,
    val filmCurves: Array<FloatArray>,
    val composedInverseCurves: Array<FloatArray>,
    val bw: Boolean = false,
) {
    init {
        require(ev.isFinite() && renderingEv.isFinite())
        require(preMatrix.size == 9 && postMatrix.size == 9 && extraMatrix.size == 9)
        require(preMatrix.all(Float::isFinite) && postMatrix.all(Float::isFinite) && extraMatrix.all(Float::isFinite))
        require(filmCurves.size == 1 || filmCurves.size == 3)
        require(composedInverseCurves.size == filmCurves.size)
        require(filmCurves.all { it.size == CURVE_SAMPLES && it.all(Float::isFinite) })
        require(composedInverseCurves.all { it.size == CURVE_SAMPLES && it.all(Float::isFinite) })
    }

    companion object { const val CURVE_SAMPLES = 32769 }
}

data class RawHsvBakeResult(
    val grid: FloatArray,
    val validNodes: Int,
    val invalidNodes: Int,
    val clampInvalidNodes: Int,
)

object RawHsvGridBaker {
    const val GRID_FLOATS = 17 * 91 * 12 * 3
    private const val Q15 = 32767f
    private const val EPS = 1.1920929E-7f

    fun bake(parameters: RawHsvBakeParameters, nodes: FloatArray = hsvNodes()): RawHsvBakeResult {
        require(nodes.size == 17 * 91 * 12 * 3)
        val valid = BooleanArray(17 * 91 * 12) { true }
        var clampInvalid = 0
        val rgb = FloatArray(3)
        val out = FloatArray(GRID_FLOATS)
        for (s in 0 until 17) for (h in 0 until 91) for (v in 0 until 12) {
            val node = ((s * 91 + h) * 12 + v)
            val base = node * 3
            hsvToRgb(nodes[base], nodes[base + 1], nodes[base + 2], rgb)
            shoulder(rgb, exp2(0.84f - parameters.ev))
            matrix(parameters.preMatrix, rgb)
            clamp(rgb) {
                if (valid[node]) clampInvalid++
                valid[node] = false
            }
            shoulder(rgb, exp2(parameters.ev - parameters.renderingEv))
            applyIndependentCurves(rgb, parameters.filmCurves)
            matrix(parameters.postMatrix, rgb)
            clamp(rgb) {
                if (valid[node]) clampInvalid++
                valid[node] = false
            }
            for (c in 0..2) rgb[c] = rgb[c].coerceAtLeast(0f).pow(2.2f)
            matrix(parameters.extraMatrix, rgb)
            for (c in 0..2) {
                require(rgb[c] >= 0f) { "extra matrix produced a negative pow input at node $node" }
                rgb[c] = rgb[c].pow(.45454544f)
            }
            nakaInverse(rgb)
            applyInverseCurves(rgb, parameters.composedInverseCurves)
            val hsv = rgbToHsv(rgb)
            val s0 = nodes[base + 1]; val v0 = nodes[base + 2]
            val special = s0 == 0f || v0 == 0f
            val invalid = !valid[node] || v0 > .95f
            if (invalid) valid[node] = false
            out[base] = if (invalid) -1000f else if (special) 0f else hueDelta(hsv[0], nodes[base])
            out[base + 1] = if (invalid) -1f else if (special) if (parameters.bw) 0f else 1f else hsv[1] / s0
            out[base + 2] = if (invalid) -1f else if (special) 1f else hsv[2] / v0
        }
        val validCount = valid.count { it }
        return RawHsvBakeResult(out, validCount, valid.size - validCount, clampInvalid)
    }

    fun hsvNodes(): FloatArray {
        val out = FloatArray(GRID_FLOATS)
        var at = 0
        for (s in 0 until 17) for (h in 0 until 91) for (v in 0 until 12) {
            out[at++] = h * 4f
            out[at++] = min(s, 15) * .06666667f
            out[at++] = max(.01f, min(v, 10) * .1f)
        }
        return out
    }

    /** Native repair transcription. It is intentionally separate from bake. */
    fun repair(input: FloatArray, skipSaturationClamp: Boolean = false): FloatArray {
        require(input.size == GRID_FLOATS)
        val a = input.copyOf()
        fun invalid(s: Int, h: Int, v: Int) = a[index(s, h, v)] == -1000f

        var first = -1
        for (v in 0..9) {
            if ((0..90).all { h -> !invalid(0, h, v) }) {
                first = v
                break
            }
        }
        require(first >= 0) { "No gray-valid V plane" }

        var lastInitial = first
        for (v in first..9) {
            if ((0..90).all { h -> !invalid(0, h, v) }) lastInitial = v else break
        }
        var last = lastInitial
        for (v in first..lastInitial) {
            if ((0..90).all { h -> !invalid(5, h, v) }) last = v
        }

        for (s in 0 until 17) {
            for (h in 0 until 91) {
                for (v in 0 until first) copyNode(a, s, h, v, s, h, first)
            }
        }

        val source = a.copyOf()
        val sat = FloatArray(17) { min(it, 15) * (1f / 15f) }
        val invSat = FloatArray(17)
        for (s in 1 until 17) invSat[s] = 1f / sat[s]
        val hueNeighbors = IntArray(93)
        hueNeighbors[0] = 89
        for (h in 0..90) hueNeighbors[h + 1] = h
        hueNeighbors[92] = 1

        for (v in 0..last) {
            for (h in 0..90) {
                for (s in 1..16) {
                    val center = index(s, h, v)
                    if (source[center] == -1000f) continue
                    val sums = FloatArray(3)
                    var count = 0
                    for (nv in max(v, 1) - 1..min(v, 8) + 1) {
                        for (ni in h..h + 2) {
                            for (ns in s - 1..min(s, 15) + 1) {
                                val q = index(ns, hueNeighbors[ni], nv)
                                if (source[q] != -1000f) {
                                    for (c in 0..2) sums[c] += source[q + c]
                                    count++
                                }
                            }
                        }
                    }
                    if (count != 0) {
                        for (c in 0..2) a[center + c] = sums[c] / count
                        a[center + 1] = min(a[center + 1], invSat[s])
                    }
                }
            }
        }

        val satCaps = FloatArray(17) { s ->
            if (s == 0) 0f else (1f / sat[s]).pow(2f / 3f)
        }
        for (v in 0..last) {
            for (h in 0..90) {
                var firstInvalidS = -1
                for (s in 0 until 17) {
                    if (invalid(s, h, v)) {
                        firstInvalidS = s
                        break
                    }
                }
                if (firstInvalidS < 0) continue
                require(firstInvalidS > 0)
                val anchor = FloatArray(3) { c -> a[index(firstInvalidS - 1, h, v) + c] }
                for (s in firstInvalidS until 17) {
                    val target = index(s, h, v)
                    for (c in 0..2) a[target + c] = anchor[c]
                    a[target + 1] = min(anchor[1], satCaps[s])
                }
            }
        }

        for (v in last + 1 until 12) {
            val value = min(v, 10) * .1f
            val cap = (1f / value).pow(2f / 3f)
            for (s in 0 until 17) {
                for (h in 0..90) {
                    val target = index(s, h, v)
                    val sourceNode = index(s, h, last)
                    for (c in 0..2) a[target + c] = a[sourceNode + c]
                    a[target + 2] = min(a[target + 2], cap)
                }
            }
        }

        if (!skipSaturationClamp) {
            for (v in 3 until 12) {
                val value = min(v, 10) * .1f
                val delta = .020000001f / value
                val lowerFactor = delta + .9f
                val upperFactor = 1.1f - delta
                for (h in 0..90) {
                    for (s in 1..16) {
                        val target = index(s, h, v)
                        val reference = index(s, h, 2)
                        val referenceS = a[reference + 1]
                        val referenceV = a[reference + 2]
                        val current = a[target + 1]
                        val ratio = referenceV / a[target + 2]
                        val difference = invSat[s] - referenceS
                        val upper = invSat[s] - upperFactor * ratio * difference
                        val lower = invSat[s] - lowerFactor * ratio * difference
                        a[target + 1] = when {
                            upper > current -> upper
                            current > lower -> lower
                            else -> current
                        }
                    }
                }
            }
        }
        return a
    }

    private fun index(s: Int, h: Int, v: Int) = ((s * 91 + h) * 12 + v) * 3
    private fun copyNode(a: FloatArray, ds: Int, dh: Int, dv: Int, ss: Int, sh: Int, sv: Int) {
        val destination = index(ds, dh, dv)
        val source = index(ss, sh, sv)
        for (c in 0..2) a[destination + c] = a[source + c]
    }

    private fun exp2(x: Float) = 2.0.pow(x.toDouble()).toFloat()

    private fun matrix(m: FloatArray, x: FloatArray) {
        val r = x[0]; val g = x[1]; val b = x[2]
        for (row in 0..2) {
            var value = FiniteFloatFma.unchecked(r, m[row * 3], 0f)
            value = FiniteFloatFma.unchecked(g, m[row * 3 + 1], value)
            x[row] = FiniteFloatFma.unchecked(b, m[row * 3 + 2], value)
        }
    }

    private fun clamp(x: FloatArray, invalid: () -> Unit) {
        var outOfRange = false
        for (i in 0..2) {
            val value = x[i].coerceIn(0f, 1f)
            outOfRange = outOfRange || x[i] != value
            x[i] = value
        }
        if (outOfRange) invalid()
    }

    private fun shoulder(x: FloatArray, gain: Float) {
        val upper = min(1f / gain, 1f)
        val value = min(gain, 1f)
        val knee = .3f * upper
        val base = .3f * value
        val upperRatio = (1f - upper) / (1f - knee)
        val valueRatio = (1f - value) / (1f - knee)

        fun map(a: Float): Float {
            val distance = a - knee
            return if (a < knee) gain * a else {
                val numerator = FiniteFloatFma.unchecked(distance, valueRatio, value) - base
                val denominator = FiniteFloatFma.unchecked(distance, upperRatio, upper) - knee
                base + distance * numerator / denominator
            }
        }

        val low = min(x[0], min(x[1], x[2]))
        val high = max(x[0], max(x[1], x[2]))
        val mappedLow = map(low)
        val mappedHigh = map(high)
        val range = max(high - low, EPS)
        for (i in 0..2) {
            val channel = x[i]
            // Native sorting retains mapped extrema; only the middle channel
            // uses FDIV followed by FMLA. A reciprocal then multiply changes
            // clamp validity at real grid nodes near the post-matrix boundary.
            x[i] = when (channel) {
                low -> mappedLow
                high -> mappedHigh
                else -> FiniteFloatFma.unchecked(mappedHigh - mappedLow, (channel - low) / range, mappedLow)
            }
        }
    }

    private fun sample(x: Float, table: FloatArray): Float {
        val position = x.coerceIn(0f, 1f) * Q15
        val index = position.toInt()
        return FiniteFloatFma.unchecked(position - index, table[index + 1] - table[index], table[index]) * (1f / Q15)
    }

    private fun applyIndependentCurves(x: FloatArray, tables: Array<FloatArray>) {
        if (tables.size == 1) {
            for (i in 0..2) x[i] = sample(x[i], tables[0])
        } else {
            for (i in 0..2) x[i] = sample(x[i], tables[i])
        }
    }

    private fun applyInverseCurves(x: FloatArray, tables: Array<FloatArray>) {
        if (tables.size == 1) commonCurve(x, tables[0])
        else for (i in 0..2) x[i] = sample(x[i], tables[i])
    }

    private fun commonCurve(x: FloatArray, table: FloatArray) {
        val order = (0..2).sortedWith(compareBy { x[it] })
        val low = order[0]; val middle = order[1]; val high = order[2]
        val lowInput = x[low]; val middleInput = x[middle]; val highInput = x[high]
        val lowOutput = sample(lowInput, table)
        val highOutput = sample(highInput, table)
        x[low] = lowOutput
        x[middle] = FiniteFloatFma.unchecked(highOutput - lowOutput,
            (middleInput - lowInput) / max(highInput - lowInput, EPS), lowOutput)
        x[high] = highOutput
    }

    private fun nakaInverse(x: FloatArray) {
        val order = (0..2).sortedWith(compareBy { x[it] })
        val low = order[0]; val middle = order[1]; val high = order[2]
        val lowInput = x[low]; val highInput = x[high]
        val lowOutput = lowInput * .09090909f / FiniteFloatFma.unchecked(-lowInput, .9090909f, 1f)
        val highOutput = highInput * .09090909f / FiniteFloatFma.unchecked(-highInput, .9090909f, 1f)
        val rootLow = sqrt(lowOutput)
        val middleRoot = FiniteFloatFma.unchecked((x[middle] - lowInput) / max(highInput - lowInput, EPS),
            sqrt(highOutput) - rootLow, rootLow)
        x[low] = lowOutput
        x[middle] = middleRoot * middleRoot
        x[high] = highOutput
    }

    private fun hsvToRgb(h0: Float, s: Float, v: Float, x: FloatArray) {
        // Native RAW grid nodes span 0..360 degrees, including the hue guard.
        val hue = h0 / 60f
        val distanceGreen = abs(hue - 2f)
        val distanceBlue = abs(hue - 4f)
        fun triangle(distance: Float) = when {
            distance < 1f -> 1f
            distance < 2f -> 2f - distance
            else -> 0f
        }
        val green = triangle(distanceGreen)
        val blue = triangle(distanceBlue)
        val selected = when {
            hue >= 4f -> distanceBlue
            hue >= 2f -> distanceGreen
            else -> hue
        }
        val red = (((1f - abs(selected - 1f)) + 1f) - green) - blue
        val chroma = s * v
        val low = v - chroma
        x[0] = FiniteFloatFma.unchecked(chroma, red, low)
        x[1] = FiniteFloatFma.unchecked(chroma, green, low)
        x[2] = FiniteFloatFma.unchecked(chroma, blue, low)
    }

    private fun rgbToHsv(x: FloatArray): FloatArray {
        val high = max(x[0], max(x[1], x[2]))
        val low = min(x[0], min(x[1], x[2]))
        val delta = high - low
        val safeDelta = if (delta == 0f) 1f else delta
        val hue = if (high == x[0]) {
            (x[1] - x[2]) / safeDelta * 60f
        } else if (high == x[1]) {
            (2f + (x[2] - x[0]) / safeDelta) * 60f
        } else {
            (4f + (x[0] - x[1]) / safeDelta) * 60f
        }
        return floatArrayOf(
            if (hue < 0f) hue + 360f else hue,
            delta / if (high == 0f) 1f else high,
            high.coerceIn(0f, 1f),
        )
    }

    private fun hueDelta(a: Float, b: Float): Float {
        val delta = (a - b + 180f) % 360f
        return ((delta + 360f) % 360f) - 180f
    }
}
