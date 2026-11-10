package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream
import java.util.Collections

/** Shipped per-film RGB scales, distinct from optional user white balance. */
class FilmRgbBaseScale internal constructor(
    val id: String,
    val renderingType: Int,
    val red: Float,
    val green: Float,
    val blue: Float,
) {
    val isIdentity: Boolean get() = red == 1f && green == 1f && blue == 1f
}

/** Fixed 147 class/factory bindings. Unknown IDs/types remain unsupported.
 * This catalog supplies scale metadata only; it does not infer intensity,
 * optional color/temperature factors or integer image application.
 */
class FilmRgbBaseScaleCatalog private constructor(rows: List<FilmRgbBaseScale>) {
    val presets: List<FilmRgbBaseScale> = Collections.unmodifiableList(ArrayList(rows))
    private val byId = presets.associateBy { it.id }
    private val byType = presets.associateBy { it.renderingType }

    fun find(id: String): FilmRgbBaseScale? = byId[id]
    fun findByRenderingType(type: Int): FilmRgbBaseScale? = byType[type]

    companion object {
        fun read(source: InputStream): FilmRgbBaseScaleCatalog {
            val input = DataInputStream(source)
            fun int() = Integer.reverseBytes(input.readInt())
            val magic = ByteArray(8).also { input.readFully(it) }
            require(magic.contentEquals("CXOWBS01".toByteArray(Charsets.US_ASCII)))
            val count = int(); require(count == 147) { "Expected fixed 147 scale bindings" }
            val ids = HashSet<String>(); val types = HashSet<Int>()
            val rows = List(count) {
                val length = int(); require(length in 1..160)
                val bytes = ByteArray(length).also { input.readFully(it) }
                require(bytes.all { it.toInt() in 32..126 })
                val id = bytes.toString(Charsets.US_ASCII)
                require(id.matches(Regex("ColorMode[A-Za-z0-9_]{1,100}")) && ids.add(id))
                val type = int(); require(type in 0..65535 && types.add(type))
                fun gain() = Float.fromBits(int()).also { require(it.isFinite() && it >= 0f) }
                FilmRgbBaseScale(id, type, gain(), gain(), gain())
            }
            require(input.read() == -1) { "Trailing scale catalog bytes" }
            return FilmRgbBaseScaleCatalog(rows)
        }
    }
}
