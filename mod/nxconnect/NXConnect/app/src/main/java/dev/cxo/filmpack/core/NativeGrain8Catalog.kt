package dev.cxo.filmpack.core

import java.io.ByteArrayInputStream
import java.io.DataInputStream
import java.security.MessageDigest

/** Original nonempty grain table entries. This catalog defines no direct-name aliases. */
class NativeGrain8Catalog private constructor(definitions: List<Definition>) {
    private val byName = definitions.associateBy { it.identifier }
    private val ordered = definitions.toList()

    val size: Int get() = ordered.size
    fun definitions(): List<Definition> = ordered.toList()
    operator fun get(identifier: String): Definition? = byName[identifier]

    class Definition internal constructor(
        val grainEnum: Int,
        val identifier: String,
        val nativePatchName: String,
        val coefficient: Int,
        val sourcePngSha256: String,
        val packedRgbSha256: String,
        amplitudeBytes: ByteArray,
    ) {
        private val table = amplitudeBytes.copyOf()

        fun amplitude(intensity: Int): IntArray = NativeGrain8Amplitude.build(table, coefficient, intensity)

        /** Caller supplies the decoded 1000-square RGB8 patch bound to this entry. */
        fun prepare(
            sourceRgb: ByteArray, scale: Float, colorLut: NativeGrain8ColorLut, seed: Long = 0,
            nativeOutputOrientation: Int = 0,
        ): NativeGrain8Prepare.Result {
            require(sourceRgb.size == 1000 * 1000 * 3) { "Expected packed 1000-square RGB8" }
            val actual = MessageDigest.getInstance("SHA-256").digest(sourceRgb).hex()
            require(actual == packedRgbSha256) { "Source patch does not match $identifier" }
            return NativeGrain8Prepare.prepare(sourceRgb, scale, colorLut, seed, nativeOutputOrientation)
        }
    }

    companion object {
        /** Read the source table/PNG manifest. Native prepared pixels are not in this resource. */
        fun read(bytes: ByteArray): NativeGrain8Catalog {
            DataInputStream(ByteArrayInputStream(bytes)).use { input ->
                fun data(n: Int): ByteArray = ByteArray(n).also(input::readFully)
                fun i32(): Int = Integer.reverseBytes(input.readInt())
                fun name(): String {
                    val n = i32(); require(n in 1..256) { "Invalid catalog string length" }
                    val raw = data(n); val value = String(raw, Charsets.UTF_8)
                    require(value.toByteArray(Charsets.UTF_8).contentEquals(raw) && value.none { it.isISOControl() })
                    return value
                }
                require(String(data(8), Charsets.US_ASCII) == "CXOGCAT1") { "Invalid grain catalog" }
                require(i32() == 1) { "Unsupported grain catalog version" }
                val count = i32(); require(count == 99) { "Expected 99 nonempty grain definitions" }
                val definitions = List(count) {
                    val grainEnum = i32(); val coefficient = i32()
                    require(grainEnum > 0 && coefficient in 1..60)
                    val identifier = name(); val patchName = name()
                    val table = data(256); val pngHash = data(32).hex(); val rgbHash = data(32).hex()
                    Definition(grainEnum, identifier, patchName, coefficient, pngHash, rgbHash, table)
                }
                require(definitions.map { it.identifier }.toSet().size == count) { "Duplicate grain name" }
                require(definitions.map { it.grainEnum }.toSet().size == count) { "Duplicate grain enum" }
                require(input.read() == -1) { "Trailing grain catalog data" }
                return NativeGrain8Catalog(definitions)
            }
        }
    }
}

private fun ByteArray.hex(): String {
    val digits = "0123456789abcdef"
    return buildString(size * 2) {
        for (byte in this@hex) {
            val value = byte.toInt() and 255
            append(digits[value ushr 4]); append(digits[value and 15])
        }
    }
}
