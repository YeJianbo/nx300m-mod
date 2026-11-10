package dev.cxo.filmpack.android

import java.io.InputStream

/**
 * EXIF orientation constants and a small JPEG APP1 reader.
 *
 * This intentionally only reads orientation metadata. It does not decode JPEG,
 * inspect an ICC profile, or claim to be a general EXIF implementation. Keeping
 * this parser here avoids making the numeric core depend on AndroidX or I/O.
 */
object ExifOrientation {
    const val NORMAL = 1
    const val FLIP_HORIZONTAL = 2
    const val ROTATE_180 = 3
    const val FLIP_VERTICAL = 4
    const val TRANSPOSE = 5
    const val ROTATE_90 = 6
    const val TRANSVERSE = 7
    const val ROTATE_270 = 8

    fun validate(value: Int): Int {
        require(value in NORMAL..ROTATE_270) { "EXIF orientation must be in 1..8" }
        return value
    }

    fun swapsDimensions(value: Int): Boolean = validate(value) >= TRANSPOSE

    fun outputWidth(sourceWidth: Int, sourceHeight: Int, orientation: Int): Int =
        if (swapsDimensions(orientation)) sourceHeight else sourceWidth

    fun outputHeight(sourceWidth: Int, sourceHeight: Int, orientation: Int): Int =
        if (swapsDimensions(orientation)) sourceWidth else sourceHeight

    /**
     * Read EXIF orientation from a JPEG stream without buffering the image.
     * Non-JPEG, truncated, malformed, and orientation-less input returns NORMAL.
     */
    fun readJpeg(input: InputStream): Int {
        if (input.read() != 0xff || input.read() != 0xd8) return NORMAL
        while (true) {
            var marker = input.read()
            if (marker != 0xff) return NORMAL
            while (marker == 0xff) {
                marker = input.read()
                if (marker < 0) return NORMAL
            }
            if (marker == 0xd8 || marker == 0x01) continue
            if (marker == 0xd9 || marker == 0xda) return NORMAL
            val length = readU16BigEndian(input) ?: return NORMAL
            if (length < 2) return NORMAL
            val payloadLength = length - 2
            if (marker == 0xe1) {
                val payload = ByteArray(payloadLength)
                if (!readFully(input, payload)) return NORMAL
                parseApp1(payload)?.let { return it }
            } else if (!skipFully(input, payloadLength)) {
                return NORMAL
            }
        }
    }

    /** Parse one complete APP1 payload (including the Exif header). */
    internal fun parseApp1(payload: ByteArray): Int? {
        if (payload.size < 14 ||
            payload[0].toInt() != 'E'.code || payload[1].toInt() != 'x'.code ||
            payload[2].toInt() != 'i'.code || payload[3].toInt() != 'f'.code ||
            payload[4].toInt() != 0 || payload[5].toInt() != 0
        ) return null
        val tiff = 6
        val littleEndian = when (u16(payload, tiff, true)) {
            0x4949 -> true
            0x4d4d -> false
            else -> return null
        }
        if (u16(payload, tiff + 2, littleEndian) != 42) return null
        val ifdOffset = u32(payload, tiff + 4, littleEndian) ?: return null
        if (ifdOffset > (payload.size - tiff - 2).toLong()) return null
        val ifd = tiff + ifdOffset.toInt()
        val entryCount = u16(payload, ifd, littleEndian)
        val entries = ifd + 2
        if (entryCount > (payload.size - entries) / 12) return null
        repeat(entryCount) { index ->
            val entry = entries + index * 12
            if (u16(payload, entry, littleEndian) != 0x0112) return@repeat
            val type = u16(payload, entry + 2, littleEndian)
            val count = u32(payload, entry + 4, littleEndian) ?: return@repeat
            if (type != 3 || count != 1L) return@repeat
            val value = u16(payload, entry + 8, littleEndian)
            return if (value in NORMAL..ROTATE_270) value else null
        }
        return null
    }

    /** Map an output coordinate to its source coordinate, without allocating. */
    internal fun sourceX(sourceWidth: Int, sourceHeight: Int, orientation: Int, outX: Int, outY: Int): Int =
        when (validate(orientation)) {
            NORMAL, FLIP_VERTICAL -> outX
            FLIP_HORIZONTAL, ROTATE_180 -> sourceWidth - 1 - outX
            TRANSPOSE -> outY
            ROTATE_90 -> outY
            TRANSVERSE -> sourceWidth - 1 - outY
            ROTATE_270 -> sourceWidth - 1 - outY
            else -> error("unreachable")
        }

    internal fun sourceY(sourceWidth: Int, sourceHeight: Int, orientation: Int, outX: Int, outY: Int): Int =
        when (validate(orientation)) {
            NORMAL, FLIP_HORIZONTAL -> outY
            FLIP_VERTICAL, ROTATE_180 -> sourceHeight - 1 - outY
            TRANSPOSE -> outX
            ROTATE_90 -> sourceHeight - 1 - outX
            TRANSVERSE -> sourceHeight - 1 - outX
            ROTATE_270 -> outX
            else -> error("unreachable")
        }

    private fun u16(data: ByteArray, offset: Int, littleEndian: Boolean): Int {
        if (offset < 0 || offset + 2 > data.size) return -1
        val a = data[offset].toInt() and 0xff
        val b = data[offset + 1].toInt() and 0xff
        return if (littleEndian) a or (b shl 8) else (a shl 8) or b
    }

    private fun u32(data: ByteArray, offset: Int, littleEndian: Boolean): Long? {
        if (offset < 0 || offset + 4 > data.size) return null
        val a = data[offset].toLong() and 0xff
        val b = data[offset + 1].toLong() and 0xff
        val c = data[offset + 2].toLong() and 0xff
        val d = data[offset + 3].toLong() and 0xff
        return if (littleEndian) a or (b shl 8) or (c shl 16) or (d shl 24)
        else (a shl 24) or (b shl 16) or (c shl 8) or d
    }

    private fun readU16BigEndian(input: InputStream): Int? {
        val high = input.read()
        val low = input.read()
        return if (high < 0 || low < 0) null else (high shl 8) or low
    }

    private fun readFully(input: InputStream, buffer: ByteArray): Boolean {
        var offset = 0
        while (offset < buffer.size) {
            val read = input.read(buffer, offset, buffer.size - offset)
            if (read < 0) return false
            if (read == 0) continue
            offset += read
        }
        return true
    }

    private fun skipFully(input: InputStream, count: Int): Boolean {
        var left = count
        while (left > 0) {
            val skipped = input.skip(left.toLong()).toInt()
            if (skipped > 0) {
                left -= skipped
                continue
            }
            if (input.read() < 0) return false
            left--
        }
        return true
    }
}
