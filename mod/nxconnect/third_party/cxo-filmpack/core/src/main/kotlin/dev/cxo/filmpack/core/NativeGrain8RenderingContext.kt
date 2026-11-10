package dev.cxo.filmpack.core

import java.io.ByteArrayInputStream
import java.io.DataInputStream
import java.security.MessageDigest

/** Source-table rendering metadata. Grain identity and rendering identity are independent.
 * This catalog only resolves the no-toning context actually used by the photo app. */
class NativeGrain8RenderingContext private constructor(private val rows: Map<String, Definition>) {
    data class Definition internal constructor(val identifier: String, val nativeEnum: Int, val auxiliary: Int)
    data class Flags(val color: Boolean, val neutralizeRedBlue: Boolean)

    val size: Int get() = rows.size
    fun definition(identifier: String): Definition? = rows[identifier]

    fun resolve(renderingIdentifier: String, toningStyle: Int = 0): Flags {
        require(toningStyle == 0) { "Only the verified no-toning grain context is supported" }
        val definition = requireNotNull(rows[renderingIdentifier]) { "Unknown grain rendering context" }
        return Flags(definition.auxiliary and 1 == 0,false)
    }

    companion object {
        fun read(bytes: ByteArray): NativeGrain8RenderingContext {
            val sha = MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            require(sha == "98bf5d5cd4123f5253974ba3bb84deeddd80efac9e68343293d2bec6a918f625") {
                "Grain rendering context hash mismatch"
            }
            return DataInputStream(ByteArrayInputStream(bytes)).use { input ->
                fun data(n: Int) = ByteArray(n).also(input::readFully)
                fun i32() = Integer.reverseBytes(input.readInt())
                require(String(data(8),Charsets.US_ASCII) == "CXOGCTX1" && i32() == 1)
                val count = i32(); require(count == 153)
                val definitions = List(count) {
                    val n = i32(); require(n in 1..256)
                    val raw = data(n); val name = String(raw,Charsets.UTF_8)
                    require(name.toByteArray(Charsets.UTF_8).contentEquals(raw) && name.none { it.isISOControl() })
                    val enum = i32(); val auxiliary = i32()
                    require(enum > 0 && auxiliary in 0..1)
                    Definition(name,enum,auxiliary)
                }
                require(definitions.map { it.identifier }.toSet().size == count && definitions.none { it.identifier == "Original" })
                require(input.read() == -1)
                NativeGrain8RenderingContext(definitions.associateBy { it.identifier })
            }
        }
    }
}
