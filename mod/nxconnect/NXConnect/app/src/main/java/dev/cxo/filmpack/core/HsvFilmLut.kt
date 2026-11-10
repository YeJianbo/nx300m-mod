package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** Application of an injected, already baked/repaired HSV grid and postcurves.
 *
 * Input/output are normalized internal encoded RGB. This class does not decode an ICC,
 * bake or repair the grid, apply grain/optional filters, or provide Android image I/O.
 * One shared or three channel postcurves; application strength is in the internal domain.
 * Grid packing is
 * [S17,H91,V12,C3] in row-major order: (((s*91+h)*12+v)*3+c).
 * Components are hue offset in degrees, saturation ratio and value ratio.
 *
 * Float arithmetic transcribes the bounded Python static model. Agreement with that
 * model is separate from native FilmPack fidelity and Android execution evidence.
 */
class HsvFilmLut(
    grid: FloatArray,
    jpegInverse: FloatArray,
    postCurve: FloatArray,
    jpegForward: FloatArray,
    val jpegToneMode: Int = 0,
    val repairBwType: Boolean = false,
    val runtimeBw: Boolean = false,
    channelPostCurves: Array<FloatArray>? = null,
    private val allowEndpoint32768: Boolean = false,
) {
    init { require(jpegToneMode == 0 || jpegToneMode == 1) }
    val postCurveChannels: Int = if (channelPostCurves == null) 1 else 3
    private val nodes = checkedGrid(grid, postCurveChannels == 3)
    private val inverse = checkedCurve(jpegInverse, allowEndpoint32768)
    private val post = checkedCurve(postCurve, allowEndpoint32768)
    private val forward = checkedCurve(jpegForward, allowEndpoint32768)
    private val channelPost = channelPostCurves?.let { tables ->
        require(tables.size == 3) { "Expected three postcurves in R/G/B order" }
        Array(3) { checkedCurve(tables[it], allowEndpoint32768) }
    }

    fun sample(r: Float, g: Float, b: Float, out: FloatArray, offset: Int = 0, strength: Float = 1f) {
        require(offset >= 0 && offset <= out.size - 3) { "Invalid output range" }
        checkStrength(strength)
        checkRgb(r, g, b)
        applyUnchecked(r, g, b, out, offset, FloatArray(9), strength)
    }

    /** Packed RGB, including in-place rendering. Validates input before changing output. */
    fun renderRgb(source: FloatArray, destination: FloatArray, strength: Float = 1f) {
        require(source.size % 3 == 0 && source.size == destination.size)
        checkStrength(strength)
        for (v in source) require(v.isFinite() && v in 0f..1f) { "RGB outside internal encoded domain" }
        val scratch = FloatArray(9)
        for (i in source.indices step 3) {
            applyUnchecked(source[i], source[i + 1], source[i + 2], destination, i, scratch, strength)
        }
    }

    private fun applyUnchecked(r: Float, g: Float, b: Float, out: FloatArray, offset: Int, x: FloatArray,
                               strength: Float) {
        x[0] = q15Input(r); x[1] = q15Input(g); x[2] = q15Input(b)
        if (jpegToneMode == 1) {
            rational(x, true)
        } else for (c in 0..2) x[c] = curve(x[c], inverse)
        val originalR = x[0]; val originalG = x[1]; val originalB = x[2]
        rgbToHsv(x)
        val h = x[0]; val s = x[1]; val v = x[2]
        val cs = s * 15f; val ch = h * .25f; val cv = v * 10f
        val si = floor(cs).toInt().coerceIn(0, 15)
        val hi = floor(ch).toInt().coerceIn(0, 89)
        val vi = floor(cv).toInt().coerceIn(0, 10)
        val fs = cs - si.toFloat(); val fh = ch - hi.toFloat(); val fv = cv - vi.toFloat()
        x[3] = 0f; x[4] = 0f; x[5] = 0f
        for (ds in 0..1) for (dh in 0..1) for (dv in 0..1) {
            val weight = (if (ds == 0) 1f - fs else fs) *
                (if (dh == 0) 1f - fh else fh) * (if (dv == 0) 1f - fv else fv)
            val base = (((si + ds) * 91 + hi + dh) * 12 + vi + dv) * 3
            for (c in 0..2) x[3 + c] += nodes[base + c] * weight
        }
        x[0] = h + x[3]; x[1] = s * x[4]; x[2] = v * x[5]
        hsvToRgb(x)
        for (c in 0..2) x[c] = x[c].coerceIn(0f, 1f)
        if (channelPost == null) commonCurve(x, post)
        else for (c in 0..2) x[c] = curve(x[c], channelPost[c])
        for (c in 0..2) x[c] = x[c].coerceIn(0f, 1f)
        // NEON application blends after postcurves, before runtime BW and JF.
        // Preserve the established full-strength asset application bit for bit.
        if (strength != 1f) {
            x[0] = (originalR + (x[0] - originalR) * strength).coerceIn(0f, 1f)
            x[1] = (originalG + (x[1] - originalG) * strength).coerceIn(0f, 1f)
            x[2] = (originalB + (x[2] - originalB) * strength).coerceIn(0f, 1f)
        }
        if (runtimeBw) {
            val gray = (x[0] * .390625f + 0f + x[1] * .546875f) + x[2] * .0625f
            x[0] = gray; x[1] = gray; x[2] = gray
        }
        if (jpegToneMode == 1) rational(x, false)
        for (c in 0..2) {
            val encoded = if (jpegToneMode == 0) curve(x[c], forward) else x[c]
            out[offset + c] = Math.rint((encoded * Q15).toDouble()).toFloat()
                .coerceIn(-32768f, Q15) / Q15
        }
    }

    private fun rational(x: FloatArray, inverseMode: Boolean) {
        var lo = 0; var mid = 1; var hi = 2
        if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
        if (x[mid] > x[hi]) { val t = mid; mid = hi; hi = t }
        if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
        val low = x[lo]; val middle = x[mid]; val high = x[hi]
        val a: Float; val b: Float; val mappedMid: Float
        if (inverseMode) {
            a = low / (11f - 10f * low); b = high / (11f - 10f * high)
            val t = (middle - low) / max(high - low, EPSILON)
            val root = sqrt(a) + (sqrt(b) - sqrt(a)) * t
            mappedMid = root * root
        } else {
            a = 11f * low / (1f + 10f * low); b = 11f * high / (1f + 10f * high)
            val t = (sqrt(middle) - sqrt(low)) / max(sqrt(high) - sqrt(low), EPSILON)
            mappedMid = a + (b - a) * t
        }
        x[lo] = a; x[mid] = mappedMid; x[hi] = b
    }

    private fun commonCurve(x: FloatArray, table: FloatArray) {
        // Stable sort of channel indices, matching numpy.argsort(kind="stable").
        var lo = 0; var mid = 1; var hi = 2
        if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
        if (x[mid] > x[hi]) { val t = mid; mid = hi; hi = t }
        if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
        val a = x[lo]; val b = x[mid]; val c = x[hi]
        val mappedLo = curve(a, table); val mappedHi = curve(c, table)
        val mappedMid = mappedLo + (mappedHi - mappedLo) * ((b - a) / max(c - a, EPSILON))
        x[lo] = mappedLo; x[mid] = mappedMid; x[hi] = mappedHi
    }

    private fun curve(value: Float, table: FloatArray): Float {
        val p = value.coerceIn(0f, 1f) * Q15
        val i = p.toInt()
        return (table[i] + (p - i.toFloat()) * (table[i + 1] - table[i])) * INV_Q15
    }

    private fun rgbToHsv(x: FloatArray) {
        val r = x[0]; val g = x[1]; val b = x[2]
        val hi = max(r, max(g, b)); val lo = min(r, min(g, b))
        val delta = hi - lo; val safe = max(delta, EPSILON)
        val sector = if (hi == r) (g - b) / safe
            else if (hi == g) (b - r) / safe + 2f else (r - g) / safe + 4f
        x[0] = if (delta > 0f) modulo360(sector * 60f) else 0f
        x[1] = if (hi > 0f) delta / max(hi, EPSILON) else 0f
        x[2] = hi
    }

    private fun hsvToRgb(x: FloatArray) {
        val h = modulo360(x[0]) / 60f; val s = x[1]; val v = x[2]
        val i = floor(h).toInt(); val f = h - i.toFloat()
        val p = v * (1f - s); val q = v * (1f - s * f); val t = v * (1f - s * (1f - f))
        when (i % 6) {
            0 -> { x[0] = v; x[1] = t; x[2] = p }
            1 -> { x[0] = q; x[1] = v; x[2] = p }
            2 -> { x[0] = p; x[1] = v; x[2] = t }
            3 -> { x[0] = p; x[1] = q; x[2] = v }
            4 -> { x[0] = t; x[1] = p; x[2] = v }
            else -> { x[0] = v; x[1] = p; x[2] = q }
        }
    }

    private fun modulo360(value: Float): Float {
        val remainder = value % 360f
        return if (remainder < 0f) remainder + 360f else remainder
    }

    companion object {
        private fun checkStrength(strength: Float) = require(strength.isFinite() && strength in 0f..1f) {
            "Expected internal WideGamut rendering strength in [0,1]"
        }
        const val GRID_FLOATS = 17 * 91 * 12 * 3
        const val CURVE_SAMPLES = 32769
        private const val Q15 = 32767f
        private val INV_Q15 = (1.0 / 32767.0).toFloat()
        private val EPSILON = Float.fromBits(0x34000000) //2^-23

        private fun q15Input(value: Float): Float = Math.rint((value * Q15).toDouble()).toFloat() / Q15
        private fun checkRgb(r: Float, g: Float, b: Float) {
            require(r.isFinite() && g.isFinite() && b.isFinite() &&
                r in 0f..1f && g in 0f..1f && b in 0f..1f) { "RGB outside internal encoded domain" }
        }
        private fun checkedGrid(values: FloatArray, signedSaturation: Boolean): FloatArray {
            require(values.size == GRID_FLOATS) { "Expected S17H91V12C3 grid" }
            val copied = values.copyOf()
            for (i in copied.indices) {
                require(copied[i].isFinite()) { "Nonfinite HSV grid node" }
                when (i % 3) {
                    0 -> require(copied[i] != -1000f) { "Unrepaired HSV node" }
                    // Native repair's final S bound has no max(0). Some threecurve
                    // modes produce signed saturation. Legacy shared assets keep
                    // their narrower nonnegative contract.
                    1 -> require(signedSaturation || copied[i] >= 0f) { "Negative shared HSV ratio" }
                    2 -> require(copied[i] >= 0f) { "Negative HSV value ratio" }
                }
            }
            return copied
        }
        private fun checkedCurve(values: FloatArray, allowEndpoint32768: Boolean): FloatArray {
            require(values.size == CURVE_SAMPLES) { "Expected extended32769 Q15 curve" }
            val copied = values.copyOf()
            val upper = if (allowEndpoint32768) Q15 + 1f else Q15
            require(copied.all { it.isFinite() && it in 0f..upper }) { "Curve outside bounded Q15 domain" }
            return copied
        }
    }
}

/** Research-only static-grid container CXOHSV01/02/03, little-endian Float32.
 * Header: magic8, S/H/V/C/curveLength five UInt32 values; then grid in declared
 * S/H/V/C packing, JPEG inverse, shared post and JPEG forward. Not a DxO format.
 * V02 adds JPEGTC, type-BW repair provenance and runtime-BW UInt32 fields after dimensions.
 * Type-BW records how nodes were repaired; it does not switch the runtime operation.
 * V03 adds a fourth UInt32 (postcurve count3), with three R/G/B postcurves in place
 * of one shared curve. V03 permits signed saturation ratios after native repair.
 * File provenance is carried by the external fixture manifest, not this header.
 */
object HsvFilmLutIO {
    fun read(source: InputStream, allowEndpoint32768: Boolean = false): HsvFilmLut {
        val input = DataInputStream(source)
        val magic = ByteArray(8); input.readFully(magic)
        val version2 = magic.contentEquals("CXOHSV02".toByteArray(Charsets.US_ASCII))
        val version3 = magic.contentEquals("CXOHSV03".toByteArray(Charsets.US_ASCII))
        require(version2 || version3 || magic.contentEquals("CXOHSV01".toByteArray(Charsets.US_ASCII)))
        val dimensions = IntArray(5) { Integer.reverseBytes(input.readInt()) }
        require(dimensions.contentEquals(intArrayOf(17, 91, 12, 3, HsvFilmLut.CURVE_SAMPLES)))
        val flags = if (version2 || version3) IntArray(3) { Integer.reverseBytes(input.readInt()) } else intArrayOf(0, 0, 0)
        require(flags.all { it == 0 || it == 1 }) { "Unsupported HSV context" }
        if (version3) require(Integer.reverseBytes(input.readInt()) == 3) { "Unsupported postcurve count" }
        fun floats(n: Int) = FloatArray(n) { Float.fromBits(Integer.reverseBytes(input.readInt())) }
        val grid = floats(HsvFilmLut.GRID_FLOATS)
        val inverse = floats(HsvFilmLut.CURVE_SAMPLES)
        val post = floats(HsvFilmLut.CURVE_SAMPLES)
        val postChannels = if (version3) arrayOf(post, floats(HsvFilmLut.CURVE_SAMPLES), floats(HsvFilmLut.CURVE_SAMPLES)) else null
        val forward = floats(HsvFilmLut.CURVE_SAMPLES)
        require(input.read() == -1) { "Trailing bytes after HSV asset" }
        return HsvFilmLut(grid, inverse, post, forward, flags[0], flags[1] == 1, flags[2] == 1, postChannels,
            allowEndpoint32768)
    }
}
