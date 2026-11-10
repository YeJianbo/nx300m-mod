package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import java.security.DigestInputStream
import java.security.MessageDigest

/** Versioned local asset format. Node order [R,G,B,channel], B fastest (not .cube order). */
object FilmLutIO {
    fun read(stream: InputStream, expectedSha256: String? = null): FilmLut {
        val digest = MessageDigest.getInstance("SHA-256")
        val input = DataInputStream(DigestInputStream(stream, digest))
        val magic = ByteArray(8); input.readFully(magic)
        require(magic.contentEquals("FPLUT001".toByteArray(Charsets.US_ASCII))) { "Invalid LUT asset magic" }
        val n = Integer.reverseBytes(input.readInt())
        require(n in 2..129) { "Unsupported LUT dimension" }
        val sourceHash = ByteArray(32); input.readFully(sourceHash)
        val nativeHash = ByteArray(32); input.readFully(nativeHash)
        val gamma = readDouble(input)
        require(gamma.isFinite() && gamma > 0)
        val matrix = DoubleArray(9) { readDouble(input) }
        val trc = DoubleArray(5) { readDouble(input) }
        require(matrix.all { it.isFinite() } && trc.all { it.isFinite() })
        require(trc[0] > 0 && trc[1] > 0 && trc[3] > 0 && trc[4] in 0.0..1.0)
        val axes = Array(3) {
            DoubleArray(n) { java.lang.Short.reverseBytes(input.readShort()).toInt().and(65535) / 65535.0 }
        }
        for (axis in axes) {
            require(axis.first() == 0.0 && axis.last() == 1.0)
            require((1 until n).all { axis[it] > axis[it - 1] })
        }
        val nodes = FloatArray(n * n * n * 3) {
            java.lang.Float.intBitsToFloat(Integer.reverseBytes(input.readInt()))
        }
        require(nodes.all { it.isFinite() && it >= 0f && it <= 1f })
        require(input.read() == -1) { "Trailing data in LUT asset" }
        if (expectedSha256 != null) require(hex(digest.digest()) == expectedSha256.lowercase()) {
            "LUT asset digest mismatch"
        }
        return FilmLut(n, hex(sourceHash), hex(nativeHash), axes, nodes, gamma, matrix, trc)
    }

    private fun readDouble(input: DataInputStream): Double =
        java.lang.Double.longBitsToDouble(java.lang.Long.reverseBytes(input.readLong()))

    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
}
