package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.Collections

/**
 * Static recovery container for the seven runtime-dependent film initializers.
 *
 * This carries branch metadata, recovered film B matrices, and both the raw
 * initializer curve and deterministic Compose2LUT curve. It intentionally does
 * not choose a WB/provider result or claim original FilmPack execution.
 */
class DynamicFilmPreset internal constructor(
    val id: String,
    val rawCurveFile: String,
    val composedCurveFile: String,
    val rawCurveSha256: String,
    val composedCurveSha256: String,
    val hasDirectBranch: Boolean,
    val curveSamples: Int,
    filmBValues: FloatArray,
) {
    private val filmBBacking: FloatArray = filmBValues.copyOf()

    /** Copy-on-read so callers cannot mutate catalog state. */
    val filmB: FloatArray get() = filmBBacking.copyOf()
}

class DynamicFilmData internal constructor(
    val preset: DynamicFilmPreset,
    rawValues: ShortArray,
    composedValues: ShortArray,
) {
    private val rawCurveBacking: ShortArray = rawValues.copyOf()
    private val composedCurveBacking: ShortArray = composedValues.copyOf()

    /** Copy-on-read so a loaded asset remains immutable to other callers. */
    val rawCurve: ShortArray get() = rawCurveBacking.copyOf()
    val composedCurve: ShortArray get() = composedCurveBacking.copyOf()
}

class DynamicFilmCatalog private constructor(rows: List<DynamicFilmPreset>) {
    val presets: List<DynamicFilmPreset> = Collections.unmodifiableList(ArrayList(rows))
    private val byId = presets.associateBy { it.id }

    fun find(id: String): DynamicFilmPreset? = byId[id]

    /** Load and hash-check both static curve assets for one initializer. */
    fun load(id: String, openAsset: (String) -> InputStream): DynamicFilmData {
        val preset = requireNotNull(byId[id]) { "Unsupported dynamic film preset: $id" }
        val rawBytes = openAsset(preset.rawCurveFile).use { readCurve(it, preset.curveSamples) }
        val composedBytes = openAsset(preset.composedCurveFile).use { readCurve(it, preset.curveSamples) }
        require(sha256(rawBytes) == preset.rawCurveSha256) { "Raw curve hash mismatch: $id" }
        require(sha256(composedBytes) == preset.composedCurveSha256) {
            "Composed curve hash mismatch: $id"
        }
        return DynamicFilmData(preset, shorts(rawBytes), shorts(composedBytes))
    }

    companion object {
        private val ID = Regex("ColorMode[A-Za-z0-9_]{1,100}")
        private val FILE = Regex("[A-Za-z0-9_.-]{1,160}")
        private val SHA = Regex("[0-9a-f]{64}")
        private const val MAGIC = "CXODYN01"
        private const val CURVE_SAMPLES = 32768

        /** Read CXODYN01, whose integer and float fields are little-endian. */
        fun read(source: InputStream): DynamicFilmCatalog {
            val input = DataInputStream(source)
            fun int() = Integer.reverseBytes(input.readInt())
            fun string(): String {
                val n = int(); require(n in 1..160) { "Invalid catalog string length" }
                val bytes = ByteArray(n); input.readFully(bytes)
                require(bytes.all { (it.toInt() and 255) in 32..126 }) { "ASCII catalog field required" }
                return bytes.toString(Charsets.US_ASCII)
            }
            val magic = ByteArray(8); input.readFully(magic)
            require(magic.contentEquals(MAGIC.toByteArray(Charsets.US_ASCII))) { "Unsupported dynamic catalog" }
            val count = int(); require(count == 7) { "Expected seven dynamic presets" }
            val ids = HashSet<String>()
            val rawFiles = HashSet<String>(); val composedFiles = HashSet<String>()
            val rows = ArrayList<DynamicFilmPreset>(count)
            repeat(count) {
                val id = string(); require(ID.matches(id) && ids.add(id)) { "Invalid/duplicate dynamic ID" }
                val raw = string(); val composed = string()
                require(FILE.matches(raw) && FILE.matches(composed))
                require(rawFiles.add(raw) && composedFiles.add(composed))
                val rawSha = ByteArray(32); input.readFully(rawSha)
                val composedSha = ByteArray(32); input.readFully(composedSha)
                val direct = int(); require(direct == 0 || direct == 1)
                val samples = int(); require(samples == CURVE_SAMPLES)
                val b = FloatArray(9) { Float.fromBits(int()) }
                require(b.all { it.isFinite() })
                rows.add(DynamicFilmPreset(id, raw, composed, rawSha.hex(), composedSha.hex(),
                    direct == 1, samples, b))
            }
            require(input.read() == -1) { "Trailing dynamic catalog bytes" }
            return DynamicFilmCatalog(rows)
        }

        private fun readCurve(input: InputStream, samples: Int): ByteArray {
            val bytes = input.readBytes()
            require(bytes.size == samples * 2) { "Unexpected dynamic curve byte count" }
            return bytes
        }

        private fun shorts(bytes: ByteArray): ShortArray = ShortArray(bytes.size / 2) {
            ((bytes[it * 2].toInt() and 255) or (bytes[it * 2 + 1].toInt() shl 8)).toShort()
        }

        private fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).hex()

        private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
