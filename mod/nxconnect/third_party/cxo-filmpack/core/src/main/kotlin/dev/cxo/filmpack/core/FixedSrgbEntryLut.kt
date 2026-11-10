package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import kotlin.math.floor

/** Application of the fixed, profile-bound Pix65 ICC entry LUT.
 *
 * Input: unsigned16 code values encoded in the recorded sRGB ICC. Output:
 * normalized Q15 RGB in the recorded internal/export ICC. This applies a
 * generated static grid; it does not parse ICC or implement a general CMM.
 * The decoder bridge uses unsigned16 >>1, an explicit reconstruction choice.
 * Grid construction uses separate installed LCMS and is not performed here.
 * Grid packing is [B65,G65,R65,C4], R fastest; C4 padding must be zero.
 */
class FixedSrgbEntryLut(
    nodes: FloatArray,
    val sourceIccSha256: String,
    val targetIccSha256: String,
    val generationLcmsVersion: Int,
    val generationFlags: Int,
    val renderingIntent: Int,
) {
    private val grid: FloatArray
    init {
        require(sourceIccSha256 == SOURCE_ICC_SHA256) { "Unsupported fixed source profile" }
        require(targetIccSha256 == TARGET_ICC_SHA256) { "Unsupported fixed target profile" }
        require(generationLcmsVersion > 0 && generationFlags == 0x40 && renderingIntent == 0)
        require(nodes.size == GRID_FLOATS) { "Expected65³float4 entry LUT" }
        grid = nodes.copyOf()
        for (i in grid.indices) {
            require(grid[i].isFinite() && grid[i] in 0f..32767f) { "Unbounded entry node" }
            if (i % 4 == 3) require(grid[i] == 0f) { "Nonzero entry padding" }
        }
    }

    /** Independent immutable-owner copy for optional platform accelerators. */
    fun copyGrid(): FloatArray = grid.copyOf()

    fun sampleSrgb16(r: Int, g: Int, b: Int, out: FloatArray, offset: Int = 0) {
        require(offset >= 0 && offset <= out.size - 3)
        require(r in 0..65535 && g in 0..65535 && b in 0..65535) { "Unsigned16 RGB required" }
        applyUnchecked(r, g, b, out, offset)
    }

    /** Packed unsigned16 RGB carried as Short bits, no Bitmap or ICC metadata handling. */
    fun renderRgb16(source: ShortArray, destination: FloatArray) {
        require(source.size % 3 == 0 && source.size == destination.size)
        for (i in source.indices step 3) {
            applyUnchecked(source[i].toInt() and 65535, source[i + 1].toInt() and 65535,
                source[i + 2].toInt() and 65535, destination, i)
        }
    }

    /**
     * Research candidate for NEON sampling0 planar signed16 (0x77124).
     *
     * This method is deliberately separate from the established Pix model:
     * the native worker multiplies the signed Q15 carrier directly by the
     * binary32 constant below, then evaluates B/G/R trilinear stages with
     * float FMA. `lane` identifies x modulo four in the native four-pixel
     * block; lane zero has the instruction-level reversed B00 association.
     * The method is separate from the legacy no-width API so callers must
     * explicitly opt into the recovered native layout contract.
     */
    fun sampleSrgb16ExactSampling0(
        r: Int, g: Int, b: Int, out: FloatArray, offset: Int = 0, lane: Int = 0,
    ) {
        require(offset >= 0 && offset <= out.size - 3)
        require(r in 0..65535 && g in 0..65535 && b in 0..65535)
        require(lane in 0..3)
        val rr = (r ushr 1).coerceIn(0, 32767)
        val gg = (g ushr 1).coerceIn(0, 32767)
        val bb = (b ushr 1).coerceIn(0, 32767)
        val xr = rr.toFloat() * SAMPLING0_COORD_SCALE
        val xg = gg.toFloat() * SAMPLING0_COORD_SCALE
        val xb = bb.toFloat() * SAMPLING0_COORD_SCALE
        val ir = floor(xr).toInt(); val ig = floor(xg).toInt(); val ib = floor(xb).toInt()
        val fr = xr - ir.toFloat(); val fg = xg - ig.toFloat(); val fb = xb - ib.toFloat()
        val lr = 1f - fr; val lg = 1f - fg; val lb = 1f - fb
        val base = ((ib * 65 + ig) * 65 + ir) * 4
        for (c in 0..2) {
            fun n(dr: Int, dg: Int, db: Int): Float = grid[base + db * 16900 + dg * 260 + dr * 4 + c]
            val b00 = if (lane == 0) FiniteFloatFma.unchecked(n(0, 0, 1), fb, n(0, 0, 0) * lb)
            else FiniteFloatFma.unchecked(lb, n(0, 0, 0), n(0, 0, 1) * fb)
            val b01 = FiniteFloatFma.unchecked(lb, n(0, 1, 0), n(0, 1, 1) * fb)
            val b10 = FiniteFloatFma.unchecked(lb, n(1, 0, 0), n(1, 0, 1) * fb)
            val b11 = FiniteFloatFma.unchecked(lb, n(1, 1, 0), n(1, 1, 1) * fb)
            val g0 = FiniteFloatFma.unchecked(lg, b00, b01 * fg)
            val g1 = FiniteFloatFma.unchecked(lg, b10, b11 * fg)
            val q15 = FiniteFloatFma.unchecked(lr, g0, g1 * fr)
            out[offset + c] = Math.rint(q15.toDouble()).toFloat().coerceIn(-32768f, 32767f) / 32767f
        }
    }

    /** Candidate full-image bridge; `width` supplies native x modulo-four lanes. */
    fun renderRgb16ExactSampling0(source: ShortArray, destination: FloatArray, width: Int) =
        renderRgb16ExactSampling0(source, destination, width, 0)

    /**
     * Tile bridge for sampling0. `width` is the packed tile row width and
     * `xOffset` is the tile's image-space x origin, so four-pixel lanes remain
     * continuous when a tile starts in the middle of a native block.
     */
    fun renderRgb16ExactSampling0(
        source: ShortArray, destination: FloatArray, width: Int, xOffset: Int,
    ) {
        require(source.size % 3 == 0 && source.size == destination.size && width > 0 && xOffset >= 0)
        for (i in source.indices step 3) {
            val pixel = i / 3
            val lane = (xOffset + pixel % width) and 3
            sampleSrgb16ExactSampling0(source[i].toInt() and 65535, source[i + 1].toInt() and 65535,
                source[i + 2].toInt() and 65535, destination, i, lane)
        }
    }

    private fun applyUnchecked(r: Int, g: Int, b: Int, out: FloatArray, offset: Int) {
        val xr = (r ushr 1).toFloat() / 32767f * 63f
        val xg = (g ushr 1).toFloat() / 32767f * 63f
        val xb = (b ushr 1).toFloat() / 32767f * 63f
        val ir = floor(xr).toInt(); val ig = floor(xg).toInt(); val ib = floor(xb).toInt()
        val fr = xr - ir.toFloat(); val fg = xg - ig.toFloat(); val fb = xb - ib.toFloat()
        val loR = 1f - fr; val loG = 1f - fg; val loB = 1f - fb
        val base = ((ib * 65 + ig) * 65 + ir) * 4
        for (c in 0..2) {
            val a0 = loR * grid[base + c] + fr * grid[base + 4 + c]
            val a1 = loR * grid[base + 260 + c] + fr * grid[base + 264 + c]
            val a = loG * a0 + fg * a1
            val b0 = loR * grid[base + 16900 + c] + fr * grid[base + 16904 + c]
            val b1 = loR * grid[base + 17160 + c] + fr * grid[base + 17164 + c]
            val bValue = loG * b0 + fg * b1
            val q15 = loB * a + fb * bValue
            out[offset + c] = Math.rint(q15.toDouble()).toFloat().coerceIn(-32768f, 32767f) / 32767f
        }
    }

    companion object {
        const val GRID_FLOATS = 65 * 65 * 65 * 4
        const val SOURCE_ICC_SHA256 = "046968ab6359293e109e90316890a3394161911a255d41da5f1629a792ba10f7"
        const val TARGET_ICC_SHA256 = "832f32f712011d64f6e1b1fd8c9c8072cb9eb0602cbef8bfded3e72a1c75672b"
        private val SAMPLING0_COORD_SCALE = Float.fromBits(0x3afc01f8)
    }
}

/** Profile-bound entry application followed by an injected HSV film application.
 * Intensity construction is limited to WideGamut; grain/optional filters and I/O are separate.
 */
class FixedSrgbFilmPipeline(
    private val entry: FixedSrgbEntryLut,
    private val film: HsvFilmLut,
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
        if (whiteBalance != null) whiteBalance.apply(r, g, b, balanced)
        if (nativeWhiteBalance != null) nativeWhiteBalance.applySrgb16Carrier(r, g, b, balanced)
        val internal = FloatArray(3)
        if (whiteBalance == null && nativeWhiteBalance == null) entry.sampleSrgb16(r, g, b, internal)
        else entry.sampleSrgb16(balanced[0], balanced[1], balanced[2], internal)
        film.sample(internal[0], internal[1], internal[2], out, offset, renderingStrength)
    }
    fun renderRgb16(source: ShortArray, destination: FloatArray,
                    whiteBalance: WhiteBalanceRgb? = null) {
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

    /** Exact sampling0 entry candidate with native four-pixel lane selection. */
    fun renderRgb16ExactSampling0(
        source: ShortArray, destination: FloatArray, width: Int,
        whiteBalance: WhiteBalanceRgb? = null,
    ) {
        renderRgb16ExactSampling0(source, destination, width, 0, whiteBalance)
    }

    /** Tile variant preserving native x modulo-four lanes across adapter tiles. */
    fun renderRgb16ExactSampling0(
        source: ShortArray, destination: FloatArray, width: Int, xOffset: Int,
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
            entry: FixedSrgbEntryLut, film: HsvFilmLut, base: FilmRgbBaseScale, intensity: Int,
        ): FixedSrgbFilmPipeline {
            require(intensity in 0..200) { "FilmPack intensity must be in 0..200" }
            return FixedSrgbFilmPipeline(entry, film,
                NativeWhiteBalanceRgb.create(base, intensity = intensity),
                minOf(intensity.toFloat() / 100f, 1f))
        }
    }

}

/** Research container CXOENT01, not a native DxO format or a generic ICC profile.
 * magic8; eightLE UInt32:65,65,65,4,sampling0,LCMSversion,flags0x40,intent0;
 * source/target SHA256 raw32bytes each; then65³float4 LE nodes inB/G/R/C order.
 */
object FixedSrgbEntryLutIO {
    fun read(source: InputStream): FixedSrgbEntryLut {
        val input = DataInputStream(source)
        val magic = ByteArray(8); input.readFully(magic)
        require(magic.contentEquals("CXOENT01".toByteArray(Charsets.US_ASCII)))
        val header = IntArray(8) { Integer.reverseBytes(input.readInt()) }
        require(header.copyOfRange(0, 5).contentEquals(intArrayOf(65, 65, 65, 4, 0)))
        fun sha(): String {
            val bytes = ByteArray(32); input.readFully(bytes)
            return bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
        }
        val sourceHash = sha(); val targetHash = sha()
        val nodes = FloatArray(FixedSrgbEntryLut.GRID_FLOATS) { Float.fromBits(Integer.reverseBytes(input.readInt())) }
        require(input.read() == -1) { "Trailing entry asset bytes" }
        return FixedSrgbEntryLut(nodes, sourceHash, targetHash, header[5], header[6], header[7])
    }
}
