package dev.cxo.filmpack.core

import kotlin.math.abs
import kotlin.math.pow
import kotlin.math.sign

/**
 * Minimal ICC matrix/TRC reader for the RGB profiles used by the recovered
 * DxO boundary. It deliberately rejects LUT based profiles instead of
 * silently treating them as matrix shapers.
 *
 * ICC tag numbers are big endian. The RGB matrix is already the profile's
 * PCS (normally D50) matrix, so no second chromatic adaptation is applied.
 */
class IccMatrixTrcProfile private constructor(
    val matrix: Matrix3,
    private val inverseMatrix: Matrix3,
    private val curves: Array<Curve>,
) {
    fun toXyz(encoded: DoubleArray, out: DoubleArray = DoubleArray(3)) {
        require(encoded.size == 3 && out.size >= 3)
        require(encoded.all { it.isFinite() })
        val linear = DoubleArray(3) { i -> curves[i].decode(encoded[i]) }
        matrix.transform(linear[0], linear[1], linear[2], out)
    }

    fun fromXyz(xyz: DoubleArray, out: DoubleArray = DoubleArray(3)) {
        require(xyz.size == 3 && out.size >= 3)
        require(xyz.all { it.isFinite() })
        val linear = DoubleArray(3)
        inverseMatrix.transform(xyz[0], xyz[1], xyz[2], linear)
        for (i in 0..2) out[i] = curves[i].encode(linear[i])
    }

    private sealed interface Curve {
        fun decode(x: Double): Double
        fun encode(y: Double): Double

        class Parametric(private val kind: Int, private val p: DoubleArray) : Curve {
            override fun decode(x: Double): Double {
                val g = p[0]
                return when (kind) {
                    0 -> signedPow(x, g)
                    1 -> if (x >= -p[2] / p[1]) maxPow(p[1] * x + p[2], g) else 0.0
                    2 -> if (x >= -p[2] / p[1]) maxPow(p[1] * x + p[2], g) + p[3] else p[3]
                    3 -> if (x >= p[4]) maxPow(p[1] * x + p[2], g) else p[3] * x
                    4 -> if (x >= p[4]) maxPow(p[1] * x + p[2], g) + p[5] else p[3] * x + p[6]
                    else -> error("Unsupported ICC parametric curve $kind")
                }
            }

            override fun encode(y: Double): Double {
                val g = p[0]
                return when (kind) {
                    0 -> signedPow(y, 1.0 / g)
                    1 -> if (y > 0.0) (maxPow(y, 1.0 / g) - p[2]) / p[1] else -p[2] / p[1]
                    2 -> if (y > p[3]) {
                        (maxPow(y - p[3], 1.0 / g) - p[2]) / p[1]
                    } else -p[2] / p[1]
                    3 -> if (y >= p[4] * p[3]) {
                        (maxPow(y, 1.0 / g) - p[2]) / p[1]
                    } else y / p[3]
                    4 -> if (y >= p[3] * p[4] + p[6]) {
                        (maxPow(y - p[5], 1.0 / g) - p[2]) / p[1]
                    } else (y - p[6]) / p[3]
                    else -> error("Unsupported ICC parametric curve $kind")
                }
            }
        }

        class Table(private val values: DoubleArray) : Curve {
            override fun decode(x: Double): Double = sample(values, x)
            override fun encode(y: Double): Double = inverseSample(values, y)
        }

        data object Linear : Curve {
            override fun decode(x: Double) = x
            override fun encode(y: Double) = y
        }
    }

    companion object {
        private const val RGB_XYZ = "RGB XYZ "

        fun read(bytes: ByteArray): IccMatrixTrcProfile {
            require(bytes.size >= 132) { "ICC profile is truncated" }
            require(u32(bytes, 0) == bytes.size && String(bytes, 36, 4, Charsets.US_ASCII) == "acsp") {
                "Invalid ICC header or declared size"
            }
            require(String(bytes, 16, 8, Charsets.US_ASCII) == RGB_XYZ) {
                "Only RGB matrix/TRC ICC profiles are supported"
            }
            val tags = HashMap<String, ByteArray>()
            val count = u32(bytes, 128)
            require(count <= 4096 && 132L + count * 12L <= bytes.size) { "Invalid ICC tag table" }
            repeat(count) { i ->
                val base = 132 + i * 12
                val name = String(bytes, base, 4, Charsets.US_ASCII)
                val offset = u32(bytes, base + 4)
                val size = u32(bytes, base + 8)
                require(offset >= 132 + count * 12 && size >= 8 && offset.toLong() + size <= bytes.size) { "Invalid ICC tag range" }
                require(name !in tags) { "Duplicate ICC tag signature" }
                tags[name] = bytes.copyOfRange(offset, offset + size)
            }
            require(tags.keys.none { it.startsWith("A2B") || it.startsWith("B2A") }) {
                "LUT-based ICC profiles are outside the matrix/TRC bridge"
            }
            val matrix = Matrix3(doubleArrayOf(
                xyz(tags, "rXYZ", 0), xyz(tags, "gXYZ", 0), xyz(tags, "bXYZ", 0),
                xyz(tags, "rXYZ", 1), xyz(tags, "gXYZ", 1), xyz(tags, "bXYZ", 1),
                xyz(tags, "rXYZ", 2), xyz(tags, "gXYZ", 2), xyz(tags, "bXYZ", 2),
            ))
            val curves = arrayOf(
                curve(tags.getValue("rTRC")), curve(tags.getValue("gTRC")), curve(tags.getValue("bTRC")),
            )
            return IccMatrixTrcProfile(matrix, matrix.inverse(), curves)
        }

        private fun xyz(tags: Map<String, ByteArray>, tag: String, row: Int): Double {
            val data = tags.getValue(tag)
            require(data.size >= 20) { "Truncated $tag" }
            require(String(data, 0, 4, Charsets.US_ASCII) == "XYZ ") { "$tag is not XYZ" }
            return s15Fixed16(data, 8 + row * 4)
        }

        private fun curve(data: ByteArray): Curve {
            return when (String(data, 0, 4, Charsets.US_ASCII)) {
                "para" -> {
                    require(data.size >= 12) { "Truncated parametric TRC" }
                    val kind = u16(data, 8)
                    val count = when (kind) { 0 -> 1; 1 -> 3; 2 -> 4; 3 -> 5; 4 -> 7; else -> error("Unsupported ICC curve $kind") }
                    require(data.size >= 12 + count * 4) { "Truncated parametric TRC parameters" }
                    val p = DoubleArray(count) { i -> s15Fixed16(data, 12 + i * 4) }
                    require(p[0] > 0.0 && (kind == 0 || p[1] > 0.0)) { "Unsupported noninvertible TRC" }
                    require(kind < 3 || p[3] > 0.0) { "Unsupported noninvertible linear TRC segment" }
                    Curve.Parametric(kind, p)
                }
                "curv" -> {
                    require(data.size >= 12) { "Truncated curve TRC" }
                    val count = u32(data, 8)
                    require(count <= (data.size - 12) / 2) { "TRC table exceeds tag size" }
                    when (count) {
                        0 -> Curve.Linear
                        1 -> {
                            val gamma = u16(data, 12) / 256.0
                            require(gamma > 0.0)
                            Curve.Parametric(0, doubleArrayOf(gamma))
                        }
                        else -> {
                            val values = DoubleArray(count) { i -> u16(data, 12 + i * 2) / 65535.0 }
                            require((1 until count).all { values[it] >= values[it - 1] } && values.first() < values.last()) {
                                "Unsupported nonmonotonic or constant TRC table"
                            }
                            Curve.Table(values)
                        }
                    }
                }
                else -> error("Unsupported ICC TRC tag")
            }
        }

        private fun u16(b: ByteArray, o: Int): Int =
            ((b[o].toInt() and 255) shl 8) or (b[o + 1].toInt() and 255)

        private fun u32(b: ByteArray, o: Int): Int {
            val value = ((b[o].toLong() and 255) shl 24) or
                ((b[o + 1].toLong() and 255) shl 16) or
                ((b[o + 2].toLong() and 255) shl 8) or (b[o + 3].toLong() and 255)
            require(value <= Int.MAX_VALUE) { "ICC field exceeds supported size" }
            return value.toInt()
        }

        private fun s15Fixed16(b: ByteArray, o: Int): Double =
            (((b[o].toInt() shl 24) or ((b[o + 1].toInt() and 255) shl 16) or
                ((b[o + 2].toInt() and 255) shl 8) or (b[o + 3].toInt() and 255)).toDouble() / 65536.0)

        private fun signedPow(value: Double, exponent: Double): Double = sign(value) * abs(value).pow(exponent)
        private fun maxPow(value: Double, exponent: Double): Double = value.coerceAtLeast(0.0).pow(exponent)

        private fun sample(values: DoubleArray, x: Double): Double {
            if (x <= 0.0) return values.first()
            if (x >= 1.0) return values.last()
            val p = x * (values.size - 1)
            val i = p.toInt()
            return values[i] + (values[i + 1] - values[i]) * (p - i)
        }

        private fun inverseSample(values: DoubleArray, y: Double): Double {
            if (y <= values.first()) return 0.0
            if (y >= values.last()) return 1.0
            var hi = 1
            while (hi < values.size && values[hi] < y) hi++
            val lo = hi - 1
            val dy = values[hi] - values[lo]
            return if (dy == 0.0) lo.toDouble() / (values.size - 1) else
                (lo + (y - values[lo]) / dy) / (values.size - 1)
        }
    }
}

/** Analytical matrix/TRC transform at the recovered Q15 boundary.
 * This is an independently implemented reference, NOT an equivalent native
 * CMM: rendering intent, media-white adaptation, and native table behavior
 * are not reproduced here. Keep it out of the production DxO WB path until
 * the native comparison is closed.
 */
class AnalyticalIccQ15Transform(
    private val source: IccMatrixTrcProfile,
    private val target: IccMatrixTrcProfile,
) {
    fun transform(input: ShortArray, output: ShortArray = ShortArray(3)): ShortArray {
        require(input.size == 3 && output.size >= 3)
        val encoded = DoubleArray(3) { i -> ColorManagementQ15.fromInt16(input[i]).toDouble() }
        val xyz = DoubleArray(3); source.toXyz(encoded, xyz)
        val targetEncoded = DoubleArray(3); target.fromXyz(xyz, targetEncoded)
        for (i in 0..2) output[i] = ColorManagementQ15.toInt16((targetEncoded[i] * ColorManagementQ15.SCALE).toFloat())
        return output
    }

    fun convert(rgb: FloatArray): FloatArray = ColorManagementQ15.convert(rgb) { transform(it) }
}
