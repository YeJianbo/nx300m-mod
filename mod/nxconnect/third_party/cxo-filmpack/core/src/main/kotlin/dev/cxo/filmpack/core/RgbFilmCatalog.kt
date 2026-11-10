package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.Collections

/** Fixed-context RGB0 presets. Native export acceptance is deliberately separate. */
class RgbFilmPreset internal constructor(
    val id: String, val renderingTypes: List<Int>, val exposureDelta: Float,
    val assetFile: String, val assetSha256: String,
    val jpegToneMode: Int, val typeBw: Boolean, val runtimeBw: Boolean,
) { val hasNativeHeldoutComparison: Boolean = false }

/** Custom base0, WB2, feature5false, strength1, no optional filters.
 * TC0/nonBW for EIR and TealAndPink; TC1/runtimeBW for HIE is a selected
 * reconstruction context, not an observation of native runtime flags.
 * Assets are parameter-baked outside Kotlin. Loads one asset on demand.
 */
class RgbFilmCatalog private constructor(rows: List<RgbFilmPreset>) {
    val presets: List<RgbFilmPreset> = Collections.unmodifiableList(ArrayList(rows))
    private val byId = presets.associateBy { it.id }
    private val byType = presets.flatMap { p -> p.renderingTypes.map { it to p } }.toMap()
    fun find(id: String): RgbFilmPreset? = byId[id]
    fun findByRenderingType(type: Int): RgbFilmPreset? = byType[type]
    fun load(id: String, openAsset: (String) -> InputStream): RgbFilmLut {
        val p = requireNotNull(find(id)) { "Unsupported RGB preset: $id" }
        val bytes = openAsset(p.assetFile).use { stream ->
            val bytes = ByteArray(4656192)
            DataInputStream(stream).readFully(bytes)
            require(stream.read() == -1) { "Trailing RGB asset bytes" }
            bytes
        }
        val hash = MessageDigest.getInstance("SHA-256").digest(bytes).hex()
        require(hash == p.assetSha256) { "RGB asset hash mismatch" }
        return RgbFilmLutIO.read(bytes.inputStream()).also {
            require(it.jpegToneMode == p.jpegToneMode && it.typeBw == p.typeBw && it.runtimeBw == p.runtimeBw) {
                "RGB catalog/asset context mismatch"
            }
        }
    }

    companion object {
        private val identities = mapOf(0x3fe to "ColorModeArgentiqueKodakEIR",
            0x6e7 to "ColorModeArgentiqueKodakHIE", 0x772 to "ColorModeCineTealAndPink")
        /** CXORBC01, WB/feature5/base/strength/optional/count; row strings/hash,
         * EV float32, TC/typeBW/runtimeBW/nativeEvidence/enumCount, enum UInt32s.
         */
        fun read(source: InputStream): RgbFilmCatalog {
            val input = DataInputStream(source)
            fun int() = Integer.reverseBytes(input.readInt())
            fun string(): String {
                val n = int(); require(n in 1..160)
                val bytes = ByteArray(n); input.readFully(bytes)
                require(bytes.all { (it.toInt() and 255) in 32..126 })
                return bytes.toString(Charsets.US_ASCII)
            }
            val magic = ByteArray(8); input.readFully(magic)
            require(magic.contentEquals("CXORBC01".toByteArray(Charsets.US_ASCII)))
            require(IntArray(5) { int() }.contentEquals(intArrayOf(2,0,0,1,0))) { "Unsupported RGB context" }
            val count = int(); require(count in 1..3)
            val ids = HashSet<String>(); val types = HashSet<Int>()
            val rows = List(count) {
                val id = string(); require(id in identities.values && ids.add(id))
                val file = string(); require(file == "$id.cxorgb")
                val hash = ByteArray(32); input.readFully(hash)
                val ev = Float.fromBits(int()); val tc = int(); val bw = int(); val runtime = int()
                val native = int(); require(native == 0) { "No RGB native acceptance in this catalog" }
                val n = int(); require(n == 1)
                val enums = List(n) {
                    val type = int(); require(types.add(type) && identities[type] == id)
                    require(NativeRenderingSelector.colorModelForRenderingType(type) == NativeRenderingSelector.ColorModel.RGB)
                    type
                }
                val isHie = id == "ColorModeArgentiqueKodakHIE"
                val flag = if (isHie) 1 else 0
                require(tc == flag && bw == flag && runtime == flag)
                require(ev == if (id == "ColorModeCineTealAndPink") -.4f else 0f)
                RgbFilmPreset(id, Collections.unmodifiableList(enums), ev, file, hash.hex(), tc, bw == 1, runtime == 1)
            }
            require(input.read() == -1) { "Trailing RGB catalog bytes" }
            return RgbFilmCatalog(rows)
        }
        private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
