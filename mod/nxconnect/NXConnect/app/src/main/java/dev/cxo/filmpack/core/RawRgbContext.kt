package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.EOFException
import java.io.InputStream

/** Owned, film-specific RAW RGB bake parameters from a validated context asset.
 *
 * No sampled output grid or fitted pixel transform is stored. The context is
 * immutable to callers; [bakeParameters] returns independently owned matrices
 * and curves suitable for [RawRgbGridBaker]. Rendering still requires the
 * matching camera-linear sensor stage and output color management.
 */
class RawRgbContext internal constructor(
    val ev: Float,
    val renderingEv: Float,
    val runtimeBw: Boolean,
    preMatrix: FloatArray,
    postMatrix: FloatArray,
    extraMatrix: FloatArray,
    filmCurves: Array<FloatArray>,
) {
    private val pre = preMatrix.copyOf()
    private val post = postMatrix.copyOf()
    private val extra = extraMatrix.copyOf()
    private val curves = Array(filmCurves.size) { filmCurves[it].copyOf() }
    val curveCount: Int get() = curves.size

    fun bakeParameters(): RawRgbBakeParameters = RawRgbBakeParameters(
        ev, renderingEv, pre.copyOf(), post.copyOf(), extra.copyOf(),
        Array(curves.size) { curves[it].copyOf() },
    )
}

/** Reader for CXORRGB1 version 1, little endian.
 *
 * Header144: magic8, version/flags/curveCount/sampleCount/encoding UInt32,
 * EV/renderingEV Float32, then pre/post/extra row-major 3x3 Float32 matrices.
 * Encoding1 stores 1 or3 curves of32769 signed-int16 Q15 samples, including
 * the native guard. Float32 samples are restored exactly. Flags bit0 is BW;
 * all other bits and encodings are rejected. No allocation trusts file counts
 * until they match the bounded supported shapes. The caller owns the stream.
 */
object RawRgbContextIO {
    const val VERSION = 1
    const val HEADER_BYTES = 144
    const val CURVE_SAMPLES = 32769
    private const val Q15_I16_ENCODING = 1

    fun read(source: InputStream): RawRgbContext {
        val input = DataInputStream(source)
        try {
            val magic = ByteArray(8).also(input::readFully)
            require(magic.contentEquals("CXORRGB1".toByteArray(Charsets.US_ASCII))) { "Unsupported RAW RGB context magic" }
            fun intLE() = Integer.reverseBytes(input.readInt())
            fun floatLE() = Float.fromBits(intLE())
            require(intLE() == VERSION) { "Unsupported RAW RGB context version" }
            val flags = intLE()
            require(flags == 0 || flags == 1) { "Unsupported RAW RGB context flags" }
            val count = intLE()
            require(count == 1 || count == 3) { "Expected one or three RAW RGB curves" }
            require(intLE() == CURVE_SAMPLES) { "Unexpected RAW RGB curve sample count" }
            require(intLE() == Q15_I16_ENCODING) { "Unsupported RAW RGB curve encoding" }
            val ev = floatLE(); val renderingEv = floatLE()
            require(ev.isFinite() && renderingEv.isFinite()) { "Non-finite RAW RGB exposure" }
            val matrices = FloatArray(27) { floatLE() }
            require(matrices.all(Float::isFinite)) { "Non-finite RAW RGB matrix" }
            val curves = Array(count) {
                FloatArray(CURVE_SAMPLES) {
                    java.lang.Short.reverseBytes(input.readShort()).toInt().also { code ->
                        require(code >= 0) { "Signed-negative RAW RGB film sample" }
                    }.toFloat()
                }.also { curve ->
                    require(curve.last().toRawBits() == curve[curve.lastIndex - 1].toRawBits()) { "Missing RAW RGB curve guard" }
                }
            }
            require(input.read() == -1) { "Trailing bytes after RAW RGB context" }
            return RawRgbContext(
                ev, renderingEv, flags == 1,
                matrices.copyOfRange(0, 9), matrices.copyOfRange(9, 18), matrices.copyOfRange(18, 27), curves,
            )
        } catch (error: EOFException) {
            throw IllegalArgumentException("Truncated RAW RGB context", error)
        }
    }
}
