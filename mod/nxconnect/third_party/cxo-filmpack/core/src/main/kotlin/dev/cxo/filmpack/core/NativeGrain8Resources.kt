package dev.cxo.filmpack.core

import java.io.ByteArrayInputStream
import java.io.Closeable
import java.io.DataInputStream
import java.io.File
import java.security.MessageDigest
import java.util.zip.InflaterInputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipFile

/** External source-only pack, never an archive of native prepared/output pixels.
 * Catalog/CMM hashes are fixed; each RGB source is checked on demand. No archive
 * member is extracted to a caller-controlled path or retained as a full catalog. */
class NativeGrain8Resources private constructor(
    private val zip: ZipFile,
    val catalog: NativeGrain8Catalog,
    private val cmm: ByteArray,
) : Closeable {
    private val definitions = catalog.definitions().associateBy { it.grainEnum }
    private var lut: NativeGrain8ColorLut? = null
    private var closed = false

    @Synchronized
    fun colorLut(): NativeGrain8ColorLut {
        check(!closed) { "Grain resource pack is closed" }
        return lut ?: NativeGrain8ColorLutFactory.fromQ15Transform(LcmsOptimizedQ15Transform.read(cmm))
            .also { lut = it }
    }

    /** Return one caller-owned 1000-square RGB8 source, bounded before hashing. */
    @Synchronized
    fun readSource(grainEnum: Int): ByteArray {
        check(!closed) { "Grain resource pack is closed" }
        val definition = requireNotNull(definitions[grainEnum]) { "Unknown grain resource enum" }
        val compressed = readMember(zip,"sources/$grainEnum.rgbz",3_100_000)
        val raw = InflaterInputStream(ByteArrayInputStream(compressed)).use { stream ->
            ByteArray(3_000_000).also { bytes ->
                DataInputStream(stream).readFully(bytes)
                require(stream.read() == -1) { "Grain source exceeds expected decoded size" }
            }
        }
        require(resourceDigest(raw) == definition.packedRgbSha256) { "Grain source hash mismatch" }
        return raw
    }

    @Synchronized
    override fun close() {
        if (!closed) { closed = true; lut = null; zip.close() }
    }

    companion object {
        private const val CATALOG_SHA = "5d93fe7b42b13e75a8f2e96c7b56703715070247b01845b7cc0e05076bbcf9fd"
        private const val CMM_SHA = "c683352cbd746d15ef50970bae45609438f51ef053bb5c3520b0cc97a25703e8"

        fun open(file: File): NativeGrain8Resources {
            require(file.isFile && file.length() in 1..160_000_000L) { "Invalid grain resource pack size" }
            val zip = ZipFile(file)
            try {
                val names = mutableSetOf<String>()
                val members = zip.entries()
                while (members.hasMoreElements()) {
                    val entry = members.nextElement()
                    require(names.size < 101 && names.add(entry.name) && !entry.isDirectory && entry.method == ZipEntry.STORED) {
                        "Invalid grain resource pack directory"
                    }
                }
                val catalogBytes = readMember(zip,"catalog.cxograin",65_536)
                require(resourceDigest(catalogBytes) == CATALOG_SHA) { "Grain catalog hash mismatch" }
                val catalog = NativeGrain8Catalog.read(catalogBytes)
                val expected = catalog.definitions().map { "sources/${it.grainEnum}.rgbz" }.toSet() +
                    setOf("catalog.cxograin","adobe-to-widegamut.lcmsbin")
                require(names == expected) { "Grain resource pack file set mismatch" }
                val cmm = readMember(zip,"adobe-to-widegamut.lcmsbin",1_000_000)
                require(resourceDigest(cmm) == CMM_SHA) { "Grain CMM hash mismatch" }
                return NativeGrain8Resources(zip,catalog,cmm)
            } catch (failure: Throwable) {
                zip.close(); throw failure
            }
        }

        private fun readMember(zip: ZipFile, name: String, maximum: Int): ByteArray {
            val entry = requireNotNull(zip.getEntry(name)) { "Missing grain resource member" }
            require(entry.method == ZipEntry.STORED && entry.size in 1..maximum.toLong() && entry.compressedSize == entry.size) {
                "Grain resource member exceeds format bounds"
            }
            return zip.getInputStream(entry).use { stream ->
                ByteArray(entry.size.toInt()).also { bytes ->
                    DataInputStream(stream).readFully(bytes)
                    require(stream.read() == -1) { "Unexpected grain resource member extent" }
                }
            }
        }
    }
}

private fun resourceDigest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
