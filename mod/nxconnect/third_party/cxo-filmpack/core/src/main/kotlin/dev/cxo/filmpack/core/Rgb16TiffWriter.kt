package dev.cxo.filmpack.core

import java.io.OutputStream

/** One packed RGB/RGBA row of unsigned16 little-endian carrier samples. */
fun interface Rgb16TiffRowProvider {
    fun readRow(y: Int, destination: ShortArray)
}

/**
 * Small, dependency-free baseline TIFF writer for the Android export boundary.
 * It writes one contiguous, little-endian RGB or RGBA strip with optional ICC.
 * The row callback keeps the temporary pixel buffer bounded by one image row.
 */
object Rgb16TiffWriter {
    fun write(
        output: OutputStream,
        width: Int,
        height: Int,
        channels: Int = 3,
        iccProfile: ByteArray? = null,
        software: String = "CXO FilmPack",
        rows: Rgb16TiffRowProvider,
    ) {
        require(width > 0 && height > 0) { "TIFF dimensions must be positive" }
        require(channels == 3 || channels == 4) { "Only RGB or RGBA TIFF is supported" }
        require(software.isNotEmpty() && '\u0000' !in software) { "Invalid TIFF software string" }
        val bytesPerRow = Math.multiplyExact(Math.multiplyExact(width, channels), 2)
        val pixelBytes = Math.multiplyExact(bytesPerRow, height)
        val softwareBytes = (software + "\u0000").toByteArray(Charsets.US_ASCII)
        val bitsBytes = ByteArray(channels * 2)
        for (i in 0 until channels) {
            bitsBytes[i * 2] = 16
        }
        val entries = ArrayList<Entry>(16)
        val entryCount = 14 +
            (if (channels == 4) 1 else 0) +
            (if (iccProfile != null) 1 else 0)
        val ifdEnd = 8 + 2 + entryCount * 12 + 4
        var extraOffset = alignEven(ifdEnd)
        val bitsOffset = extraOffset
        extraOffset += bitsBytes.size
        val xResolutionOffset = extraOffset
        extraOffset += 8
        val yResolutionOffset = extraOffset
        extraOffset += 8
        val softwareOffset = extraOffset
        extraOffset += softwareBytes.size
        val iccOffset = if (iccProfile != null) extraOffset else 0
        if (iccProfile != null) extraOffset += iccProfile.size
        val pixelOffset = alignEven(extraOffset)
        check(pixelOffset.toLong() + pixelBytes <= 0xffff_ffffL) { "Classic TIFF exceeds 4 GiB" }

        entries += Entry(256, 4, 1, width.toLong())
        entries += Entry(257, 4, 1, height.toLong())
        entries += Entry(258, 3, channels.toLong(), bitsOffset.toLong())
        entries += Entry(259, 3, 1, 1)
        entries += Entry(262, 3, 1, 2)
        entries += Entry(273, 4, 1, pixelOffset.toLong())
        entries += Entry(277, 3, 1, channels.toLong())
        entries += Entry(278, 4, 1, height.toLong())
        entries += Entry(279, 4, 1, pixelBytes.toLong())
        entries += Entry(282, 5, 1, xResolutionOffset.toLong())
        entries += Entry(283, 5, 1, yResolutionOffset.toLong())
        entries += Entry(284, 3, 1, 1)
        entries += Entry(296, 3, 1, 3)
        entries += Entry(305, 2, softwareBytes.size.toLong(), inlineOrOffset(softwareBytes, softwareOffset))
        if (channels == 4) entries += Entry(338, 3, 1, 2)
        if (iccProfile != null) entries += Entry(34675, 7, iccProfile.size.toLong(), inlineOrOffset(iccProfile, iccOffset))
        entries.sortBy { it.tag }

        val header = LittleEndianBuffer(pixelOffset)
        header.bytes('I'.code, 'I'.code)
        header.short(42)
        header.long(8)
        header.short(entries.size)
        for (entry in entries) header.entry(entry)
        header.long(0)
        header.padTo(bitsOffset)
        header.bytes(*bitsBytes.map { it.toInt() and 255 }.toIntArray())
        header.rationalAt(xResolutionOffset, 72, 1)
        header.rationalAt(yResolutionOffset, 72, 1)
        header.bytesAt(softwareOffset, softwareBytes)
        if (iccProfile != null) header.bytesAt(iccOffset, iccProfile)
        header.positionAt(pixelOffset)
        output.write(header.array())

        val row = ShortArray(width * channels)
        for (y in 0 until height) {
            rows.readRow(y, row)
            val rowBytes = ByteArray(bytesPerRow)
            for (i in row.indices) {
                val value = row[i].toInt() and 0xffff
                rowBytes[i * 2] = value.toByte()
                rowBytes[i * 2 + 1] = (value ushr 8).toByte()
            }
            output.write(rowBytes)
        }
    }

    fun write(
        output: OutputStream,
        width: Int,
        height: Int,
        pixels: ShortArray,
        channels: Int = 3,
        iccProfile: ByteArray? = null,
        software: String = "CXO FilmPack",
    ) {
        require(pixels.size == width * height * channels) { "Packed pixel length mismatch" }
        write(output, width, height, channels, iccProfile, software) { y, row ->
            pixels.copyInto(row, 0, y * row.size, (y + 1) * row.size)
        }
    }

    private data class Entry(val tag: Int, val type: Int, val count: Long, val value: Long)

    private class LittleEndianBuffer(capacity: Int) {
        private val data = ByteArray(capacity)
        private var position = 0
        fun array(): ByteArray = data
        fun byte(value: Int) { data[position++] = value.toByte() }
        fun bytes(vararg values: Int) { for (value in values) byte(value) }
        fun bytesAt(offset: Int, values: ByteArray) {
            val saved = position
            position = offset
            for (value in values) byte(value.toInt() and 255)
            position = saved
        }
        fun short(value: Int) { byte(value); byte(value ushr 8) }
        fun long(value: Long) { short(value.toInt()); short((value ushr 16).toInt()) }
        fun entry(entry: Entry) { short(entry.tag); short(entry.type); long(entry.count); value(entry) }
        private fun value(entry: Entry) {
            if (entry.type == 3 && entry.count == 1L) { short(entry.value.toInt()); short(0) }
            else long(entry.value)
        }
        fun rationalAt(offset: Int, numerator: Int, denominator: Int) {
            val saved = position; position = offset; long(numerator.toLong()); long(denominator.toLong()); position = saved
        }
        fun padTo(offset: Int) { while (position < offset) byte(0) }
        fun positionAt(offset: Int) {
            require(offset >= position) { "Cannot move TIFF cursor backwards" }
            position = offset
        }
    }

    private fun alignEven(value: Int) = (value + 1) and -2

    // TIFF stores payloads of at most four bytes inside the value field.
    private fun inlineOrOffset(bytes: ByteArray, offset: Int): Long =
        if (bytes.size > 4) offset.toLong() else bytes.indices.fold(0L) { value, i ->
            value or ((bytes[i].toLong() and 255L) shl (i * 8))
        }
}
