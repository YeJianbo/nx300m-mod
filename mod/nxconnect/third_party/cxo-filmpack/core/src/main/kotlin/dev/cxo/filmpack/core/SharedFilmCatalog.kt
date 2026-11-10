package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.Collections

/** One fixed HSV preset, with a distinct asset and explicit evidence state.
 * IDs/enums describe internal native identities, not a verified UI film list.
 */
class SharedFilmPreset internal constructor(
    val id: String,
    val renderingTypes: List<Int>,
    val exposureDelta: Float,
    val assetFile: String,
    val assetSha256: String,
    val hasNativeHeldoutComparison: Boolean,
    val repairBwType: Boolean,
)

/** Fixed Custom RGB context: WB2, feature5false, camera base0, strength1,
 * no grain/optional filters. V1 is TC0/nonBW; V2 is TC1/typeBW/runtimeBW.
 * V3 is TC0/nonBW with three channel postcurves. The historical class name is retained.
 * Assets are baked/repaired outside Kotlin; runtime gates are not inferred here.
 * Loads one film on demand; a resolver owns its storage, Android assets or files.
 */
class SharedFilmCatalog private constructor(
    presets: List<SharedFilmPreset>,
    val jpegToneMode: Int,
    val runtimeBw: Boolean,
    val postCurveChannels: Int,
) {
    val presets: List<SharedFilmPreset> = Collections.unmodifiableList(ArrayList(presets))
    private val byId = this.presets.associateBy { it.id }
    private val byType = this.presets.flatMap { p -> p.renderingTypes.map { it to p } }.toMap()

    fun find(id: String): SharedFilmPreset? = byId[id]
    fun findByRenderingType(type: Int): SharedFilmPreset? = byType[type]

    fun load(id: String, openAsset: (String) -> InputStream): HsvFilmLut {
        val preset = requireNotNull(byId[id]) { "Unsupported shared HSV preset: $id" }
        val bytes = openAsset(preset.assetFile).use { stream ->
            val data = ByteArray(if (postCurveChannels == 3) 878192
                else if (preset.repairBwType) ASSET_BYTES + 12 else ASSET_BYTES)
            DataInputStream(stream).readFully(data)
            require(stream.read() == -1) { "Trailing bytes in film asset" }
            data
        }
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes).hex()
        require(digest == preset.assetSha256) { "Film asset hash mismatch: $id" }
        val film = HsvFilmLutIO.read(bytes.inputStream())
        require(film.jpegToneMode == jpegToneMode && film.runtimeBw == runtimeBw &&
            film.repairBwType == preset.repairBwType && film.postCurveChannels == postCurveChannels) {
            "Film/catalog context mismatch"
        }
        return film
    }

    companion object {
        private const val ASSET_BYTES = 616024
        private val ID = Regex("ColorMode[A-Za-z0-9_]{1,100}")
        private val SHA = Regex("[0-9a-f]{64}")

        /** Research manifest CXOCAT01/02/03. V2/V3 add type-BW UInt32 after native evidence field. */
        fun read(source: InputStream): SharedFilmCatalog {
            val input = DataInputStream(source)
            fun int() = Integer.reverseBytes(input.readInt())
            fun string(): String {
                val n = int(); require(n in 1..160) { "Invalid catalog string length" }
                val bytes = ByteArray(n); input.readFully(bytes)
                require(bytes.all { (it.toInt() and 255) in 32..126 }) { "ASCII catalog field required" }
                return bytes.toString(Charsets.US_ASCII)
            }
            val magic = ByteArray(8); input.readFully(magic)
            val version2 = magic.contentEquals("CXOCAT02".toByteArray(Charsets.US_ASCII))
            val version3 = magic.contentEquals("CXOCAT03".toByteArray(Charsets.US_ASCII))
            require(version2 || version3 || magic.contentEquals("CXOCAT01".toByteArray(Charsets.US_ASCII)))
            val context = IntArray(8) { int() }
            // TC0, WB2, feature5false, cameraBase0, strength1, BWfalse, optionalfalse, sharedKind0.
            val expectedContext = if (version2) intArrayOf(1, 2, 0, 0, 1, 1, 0, 0)
                else intArrayOf(0, 2, 0, 0, 1, 0, 0, if (version3) 1 else 0)
            require(context.contentEquals(expectedContext)) { "Unsupported catalog context" }
            val count = int(); require(count in 1..(if (version2) 58 else if (version3) 18 else 68)) { "Invalid HSV catalog size" }
            val ids = HashSet<String>(); val types = HashSet<Int>()
            val rows = ArrayList<SharedFilmPreset>(count)
            repeat(count) {
                val id = string(); require(ID.matches(id) && ids.add(id)) { "Invalid/duplicate film ID" }
                val file = string(); require(file == "$id.cxohsv") { "Invalid asset filename" }
                val shaBytes = ByteArray(32); input.readFully(shaBytes)
                val hash = shaBytes.hex(); require(SHA.matches(hash))
                val ev = Float.fromBits(int()); require(ev.isFinite() && ev in -1.5f..0f)
                val native = int(); require(native == 0 || native == 1)
                require(native == 0 || id == "ColorModeArgentiqueKodakPortra400") {
                    "Unexpected native acceptance claim"
                }
                val repairBw = if (version2 || version3) int() else 0
                require(repairBw == if (version2) 1 else 0) { "Unsupported type-BW repair flag" }
                require(!(version2 || version3) || native == 0) { "No native heldout acceptance for this catalog" }
                val aliases = int(); require(aliases in 1..8)
                val enums = List(aliases) {
                    val type = int(); require(type in 0..65535 && types.add(type)) { "Invalid/duplicate native enum" }
                    require(NativeRenderingSelector.colorModelForRenderingType(type) ==
                        NativeRenderingSelector.ColorModel.HSV) { "RGB type in HSV catalog" }
                    type
                }
                rows.add(SharedFilmPreset(id, Collections.unmodifiableList(enums), ev, file, hash, native == 1, repairBw == 1))
            }
            require(input.read() == -1) { "Trailing catalog bytes" }
            return SharedFilmCatalog(rows, context[0], context[5] == 1, if (version3) 3 else 1)
        }

        private fun ByteArray.hex() = joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
