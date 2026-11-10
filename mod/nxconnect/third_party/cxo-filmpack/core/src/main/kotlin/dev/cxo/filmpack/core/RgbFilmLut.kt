package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.sqrt

/** Application of a static 65^3 RGB film grid, independent of the HSV model.
 * Grid packing is [B65,G65,R65,C4], with R fastest. Grid values are normalized;
 * inputs and outputs exposed by this class are normalized Q15 values.
 */
class RgbFilmLut(
    nodes: FloatArray,
    jpegInverse: FloatArray,
    jpegForward: FloatArray,
    val jpegToneMode: Int = 0,
    val typeBw: Boolean = false,
    val runtimeBw: Boolean = false,
) {
    private val grid = checkedGrid(nodes)
    private val inverse = checkedCurve(jpegInverse)
    private val forward = checkedCurve(jpegForward)

    init { require(jpegToneMode == 0 || jpegToneMode == 1) { "Unsupported JPEG tone mode" } }

    fun sample(r: Float, g: Float, b: Float, out: FloatArray, offset: Int = 0, strength: Float = 1f) {
        require(offset >= 0 && offset <= out.size - 3) { "Invalid output range" }
        checkStrength(strength)
        checkRgb(r, g, b)
        applyUnchecked(r, g, b, out, offset, FloatArray(9), strength)
    }

    /** Packed normalized RGB, including in-place rendering. */
    fun renderRgb(source: FloatArray, destination: FloatArray, strength: Float = 1f) {
        require(source.size % 3 == 0 && source.size == destination.size)
        checkStrength(strength)
        source.forEach { require(it.isFinite() && it in 0f..1f) { "RGB outside Q15 domain" } }
        val scratch = FloatArray(9)
        for (i in source.indices step 3) applyUnchecked(source[i], source[i + 1], source[i + 2], destination, i, scratch, strength)
    }

    private fun applyUnchecked(r: Float, g: Float, b: Float, out: FloatArray, offset: Int, scratch: FloatArray,
                               strength: Float) {
        val x = scratch
        x[0] = r; x[1] = g; x[2] = b
        if (jpegToneMode == 1) {
            x[0] = q15Input(r); x[1] = q15Input(g); x[2] = q15Input(b)
            rational(x, true)
        } else {
            x[0] = inverse[q15Index(r)] * INV_Q15
            x[1] = inverse[q15Index(g)] * INV_Q15
            x[2] = inverse[q15Index(b)] * INV_Q15
        }
        x[3] = x[0]; x[4] = x[1]; x[5] = x[2]
        trilinear(x[0], x[1], x[2], x, 6)
        for (c in 0..2) x[c] = (x[3 + c] + (x[6 + c] - x[3 + c]) * strength).coerceIn(0f, 1f)
        if (runtimeBw) {
            val gray = x[0] * .390625f + x[1] * .546875f + x[2] * .0625f
            x[0] = gray; x[1] = gray; x[2] = gray
        }
        if (jpegToneMode == 1) {
            rational(x, false)
        }
        for (c in 0..2) {
            val sample = if (jpegToneMode == 0) rawCurve(x[c] * Q15, forward) else x[c] * Q15
            out[offset + c] = Math.rint(sample.toDouble()).toFloat().coerceIn(-32768f, Q15) / Q15
        }
    }

    private fun trilinear(r: Float, g: Float, b: Float, result: FloatArray, offset: Int) {
        val xr = r * 63f; val xg = g * 63f; val xb = b * 63f
        val ir = floor(xr).toInt().coerceIn(0, 63); val ig = floor(xg).toInt().coerceIn(0, 63); val ib = floor(xb).toInt().coerceIn(0, 63)
        val fr = xr - ir; val fg = xg - ig; val fb = xb - ib
        for (c in 0..2) {
            fun node(dr: Int, dg: Int, db: Int): Float = grid[(((ib + db) * 65 + (ig + dg)) * 65 + (ir + dr)) * 4 + c]
            // Cxyz denotes R/G/B offsets, independently of storage order.
            val c000 = node(0, 0, 0); val c001 = node(0, 0, 1)
            val c010 = node(0, 1, 0); val c011 = node(0, 1, 1)
            val c100 = node(1, 0, 0); val c101 = node(1, 0, 1)
            val c110 = node(1, 1, 0); val c111 = node(1, 1, 1)
            val b00 = c000 * (1f - fb) + c001 * fb
            val b01 = c011 * fb + (1f - fb) * c010
            val b10 = c101 * fb + (1f - fb) * c100
            val b11 = c111 * fb + (1f - fb) * c110
            val g0 = b01 * fg + (1f - fg) * b00
            val g1 = b11 * fg + (1f - fg) * b10
            result[offset + c] = g1 * fr + (1f - fr) * g0
        }
    }

    private fun rational(x: FloatArray, inverseMode: Boolean) {
        var lo = 0; var mid = 1; var hi = 2
        if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
        if (x[mid] > x[hi]) { val t = mid; mid = hi; hi = t }
        if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
        val low = x[lo]; val middle = x[mid]; val high = x[hi]
        val a: Float; val b: Float; val mapped: Float
        if (inverseMode) {
            val ten = Float.fromBits(0x3f68ba2e)
            val scale = Float.fromBits(0x3dba2e8c)
            a = (low * scale) / (1f - low * ten); b = (high * scale) / (1f - high * ten)
            val t = (middle - low) / max(high - low, EPSILON)
            val root = sqrt(a) + (sqrt(b) - sqrt(a)) * t; mapped = root * root
        } else {
            a = 11f * low / (1f + 10f * low); b = 11f * high / (1f + 10f * high)
            val t = (sqrt(middle) - sqrt(low)) / max(sqrt(high) - sqrt(low), EPSILON)
            mapped = a + (b - a) * t
        }
        x[lo] = a; x[mid] = mapped; x[hi] = b
    }

    private fun rawCurve(p: Float, table: FloatArray): Float {
        val q = p.coerceIn(0f, Q15); val i = q.toInt()
        return table[i] + (q - i) * (table[i + 1] - table[i])
    }

    companion object {
        private fun checkStrength(strength: Float) = require(strength.isFinite() && strength in 0f..1f) {
            "Expected internal WideGamut rendering strength in [0,1]"
        }
        const val GRID_FLOATS = 65 * 65 * 65 * 4
        const val CURVE_SAMPLES = 32769
        const val Q15 = 32767f
        private val INV_Q15 = Float.fromBits(0x38000100)
        private const val EPSILON = 1.1920929e-7f
        private fun q15Input(value: Float) = Math.rint((value * Q15).toDouble()).toFloat() * INV_Q15
        private fun q15Index(value: Float) = Math.rint((value * Q15).toDouble()).toInt().coerceIn(0, 32767)
        private fun checkRgb(r: Float, g: Float, b: Float) = require(r.isFinite() && g.isFinite() && b.isFinite() && r in 0f..1f && g in 0f..1f && b in 0f..1f)
        private fun checkedGrid(values: FloatArray): FloatArray {
            require(values.size == GRID_FLOATS) { "Expected 65^3 float4 RGB grid" }; val copy = values.copyOf()
            for (i in copy.indices) { require(copy[i].isFinite()) { "Nonfinite RGB grid node" }; if (i % 4 == 3) require(copy[i] == 0f) { "Nonzero RGB padding" } }
            for (b in 0..64) for (g in 0..64) for (r in 0..64) for (c in 0..3) {
                if (b == 64 || g == 64 || r == 64) {
                    val guard = (((b * 65 + g) * 65 + r) * 4 + c)
                    val ref = (((minOf(b, 63) * 65 + minOf(g, 63)) * 65 + minOf(r, 63)) * 4 + c)
                    require(copy[guard].toRawBits() == copy[ref].toRawBits()) { "RGB guard mismatch" }
                }
            }
            return copy
        }
        private fun checkedCurve(values: FloatArray): FloatArray { require(values.size == CURVE_SAMPLES); val copy = values.copyOf(); require(copy.all { it.isFinite() && it in 0f..Q15 }); return copy }
    }
}

class FixedSrgbRgbFilmPipeline(
    private val entry: FixedSrgbEntryLut,
    private val film: RgbFilmLut,
    private val nativeWhiteBalance: NativeWhiteBalanceRgb? = null,
    private val renderingStrength: Float = 1f,
    // Optional bulk platform entry; the Kotlin evaluator remains the reference.
    private val exactEntry: ((ShortArray,FloatArray,Int,Int) -> Unit)? = null,
) {
    init { require(renderingStrength.isFinite() && renderingStrength in 0f..1f) }
    fun sampleSrgb16(r: Int, g: Int, b: Int, out: FloatArray, offset: Int = 0,
                    whiteBalance: WhiteBalanceRgb? = null) {
        require(offset >= 0 && offset <= out.size - 3)
        require(nativeWhiteBalance == null || whiteBalance == null) { "Choose native or manual WB, not both" }
        val balanced = IntArray(3)
        if (whiteBalance != null) whiteBalance.apply(r, g, b, balanced, 0)
        if (nativeWhiteBalance != null) nativeWhiteBalance.applySrgb16Carrier(r, g, b, balanced)
        val internal = FloatArray(3)
        if (whiteBalance == null && nativeWhiteBalance == null) entry.sampleSrgb16(r, g, b, internal)
        else entry.sampleSrgb16(balanced[0], balanced[1], balanced[2], internal)
        film.sample(internal[0], internal[1], internal[2], out, offset, renderingStrength)
    }
    fun renderRgb16(source: ShortArray, destination: FloatArray, whiteBalance: WhiteBalanceRgb? = null) {
        require(source.size % 3 == 0 && source.size == destination.size)
        require(nativeWhiteBalance == null || whiteBalance == null) { "Choose native or manual WB, not both" }
        val balanced = when {
            nativeWhiteBalance != null -> ShortArray(source.size).also { nativeWhiteBalance.applySrgb16Carrier(source, it) }
            whiteBalance != null -> ShortArray(source.size).also { whiteBalance.apply(source, it) }
            else -> source
        }
        val internal = FloatArray(source.size)
        entry.renderRgb16(balanced, internal)
        film.renderRgb(internal, destination, renderingStrength)
    }

    /** Exact sampling0 entry path with native four-pixel lane selection. */
    fun renderRgb16ExactSampling0(
        source: ShortArray, destination: FloatArray, width: Int, xOffset: Int = 0,
        whiteBalance: WhiteBalanceRgb? = null,
    ) {
        require(source.size % 3 == 0 && source.size == destination.size && width > 0 && xOffset >= 0)
        require(nativeWhiteBalance == null || whiteBalance == null) { "Choose native or manual WB, not both" }
        val balanced = when {
            nativeWhiteBalance != null -> ShortArray(source.size).also { nativeWhiteBalance.applySrgb16Carrier(source, it) }
            whiteBalance != null -> ShortArray(source.size).also { whiteBalance.apply(source, it) }
            else -> source
        }
        val internal = FloatArray(source.size)
        if (exactEntry == null) entry.renderRgb16ExactSampling0(balanced, internal, width, xOffset)
        else exactEntry.invoke(balanced,internal,width,xOffset)
        film.renderRgb(internal, destination, renderingStrength)
    }

    companion object {
        /** Controlled WideGamut path: independent WB-base interpolation and film blend.
         * Caller supplies the matching fixed-context asset and static per-film base.
         * This does not select a Classic pipeline or infer optional filter settings.
         */
        fun withWideGamutIntensity(
            entry: FixedSrgbEntryLut, film: RgbFilmLut, base: FilmRgbBaseScale, intensity: Int,
        ): FixedSrgbRgbFilmPipeline {
            require(intensity in 0..200) { "FilmPack intensity must be in 0..200" }
            return FixedSrgbRgbFilmPipeline(entry, film,
                NativeWhiteBalanceRgb.create(base, intensity = intensity),
                minOf(intensity.toFloat() / 100f, 1f))
        }
    }

}

/** CXORGB01: dimensions, curveLength, TC/type-BW/runtime-BW flags, then grid and curves. */
object RgbFilmLutIO {
    fun read(source: InputStream): RgbFilmLut {
        val input = DataInputStream(source); val magic = ByteArray(8); input.readFully(magic)
        require(magic.contentEquals("CXORGB01".toByteArray(Charsets.US_ASCII)))
        val header = IntArray(8) { Integer.reverseBytes(input.readInt()) }
        require(header.copyOfRange(0, 5).contentEquals(intArrayOf(65, 65, 65, 4, 32769)))
        require(header[5] == 0 || header[5] == 1); require(header[6] == 0 || header[6] == 1); require(header[7] == 0 || header[7] == 1)
        fun floats(n: Int) = FloatArray(n) { Float.fromBits(Integer.reverseBytes(input.readInt())) }
        val grid = floats(RgbFilmLut.GRID_FLOATS); val inverse = floats(RgbFilmLut.CURVE_SAMPLES); val forward = floats(RgbFilmLut.CURVE_SAMPLES)
        require(input.read() == -1) { "Trailing bytes after RGB asset" }
        return RgbFilmLut(grid, inverse, forward, header[5], header[6] == 1, header[7] == 1)
    }
}
