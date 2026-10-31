package com.nxwa.nxconnect.net

import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.util.Locale

/** Incremental AVI reader for RVF's zero-length RIFF live response. No whole-stream buffering. */
class RiffVideoReader(private val input: InputStream) {
    enum class Codec { H264, MJPG }
    data class Format(
        val codec: Codec,
        val width: Int,
        val height: Int,
        val streamId: String,
        val codecSpecificData: List<ByteArray> = emptyList(),
    )
    class FormatException(message: String) : IOException(message)

    companion object {
        const val MAX_FRAME_BYTES = 4 * 1024 * 1024
        private const val MAX_CHUNK_BYTES = 8 * 1024 * 1024
        private const val MAX_HEADER_BYTES = 1024 * 1024
        private const val MAX_FORMAT_BYTES = 64 * 1024
        private const val MAX_NALS = 256
        private const val MAX_NESTING = 8
    }

    private data class Container(val kind: String, val end: Long?, val padded: Boolean)
    private data class StreamHeader(
        val index: Int,
        var video: Boolean = false,
        var handler: String = "",
        var bitmap: ByteArray? = null,
    )
    private var offset = 0L
    private val containers = mutableListOf<Container>()
    private var stream: StreamHeader? = null
    private var streamCount = 0
    private var selected: Format? = null
    private var nalLengthBytes = 4
    private var avcConfiguration = false
    private var inMovi = false
    private val scratch = ByteArray(8192)

    fun readHeader(): Format {
        selected?.takeIf { inMovi }?.let { return it }
        if (offset != 0L) throw FormatException("AVI header is incomplete")
        if (fourcc(readExact(4)) != "RIFF") throw FormatException("RVF response is not RIFF")
        readExact(4) // The camera announces zero or a placeholder total length.
        if (fourcc(readExact(4)) != "AVI ") throw FormatException("RIFF response is not AVI")
        while (true) {
            if (offset > MAX_HEADER_BYTES) throw FormatException("AVI header exceeds limit")
            finishContainers()
            val id = readId() ?: throw EOFException("AVI ended before movi")
            val size = readU32()
            if (id == "LIST") {
                val kind = readListType(size)
                if (kind == "movi") {
                    finishStream()
                    val format = selected ?: throw FormatException("AVI has no supported video stream")
                    containers.clear() // Unknown header LIST lengths do not delimit the live media.
                    pushContainer(kind, size)
                    inMovi = true
                    return format
                }
                if (kind == "strl") {
                    finishStream()
                    stream = StreamHeader(streamCount++)
                    // Some live headers leave LIST sizes unset, too.
                    while (containers.lastOrNull()?.let { it.kind == "strl" && it.end == null } == true) {
                        containers.removeAt(containers.lastIndex)
                    }
                }
                if (size > MAX_HEADER_BYTES && size != 0xffffffffL) {
                    throw FormatException("AVI header LIST exceeds limit")
                }
                pushContainer(kind, size)
                continue
            }
            checkChunk(size, MAX_FORMAT_BYTES)
            checkWithinContainer(size + (size and 1))
            when (id) {
                "strh" -> {
                    val data = readExact(size.toInt())
                    if (data.size < 8) throw FormatException("Truncated AVI strh")
                    val current = stream ?: throw FormatException("AVI strh outside strl")
                    current.video = fourcc(data, 0) == "vids"
                    current.handler = fourcc(data, 4)
                }
                "strf" -> {
                    val current = stream ?: throw FormatException("AVI strf outside strl")
                    current.bitmap = readExact(size.toInt())
                }
                else -> skip(size)
            }
            if ((size and 1) != 0L) skip(1)
        }
    }

    /** Returns one compressed video packet; H264 packets are validated Annex B access units. */
    fun nextFrame(): ByteArray? {
        val format = readHeader()
        val started = offset
        while (true) {
            if (offset - started > MAX_CHUNK_BYTES * 2L) {
                throw FormatException("AVI has no video packet within scan limit")
            }
            finishContainers()
            if (containers.isEmpty()) return null // A finite movi cannot contain later file chunks.
            val id = readId() ?: return null
            val size = readU32()
            if (id == "LIST") {
                val kind = readListType(size)
                if (kind != "rec " && kind != "movi") {
                    checkChunk(size, MAX_CHUNK_BYTES)
                    if (size < 4) throw FormatException("Unknown unbounded AVI LIST")
                    skip(size - 4 + (size and 1))
                } else pushContainer(kind, size)
                continue
            }
            checkChunk(size, MAX_CHUNK_BYTES)
            checkWithinContainer(size + (size and 1))
            if (id == "${format.streamId}dc" || id == "${format.streamId}db") {
                checkChunk(size, MAX_FRAME_BYTES)
                val data = readExact(size.toInt())
                if ((size and 1) != 0L) skip(1)
                if (data.isEmpty()) continue
                return when (format.codec) {
                    Codec.H264 -> normalizeH264(data)
                    Codec.MJPG -> {
                        if (data.size < 4 || data[0] != 0xff.toByte() || data[1] != 0xd8.toByte() ||
                            data[data.lastIndex - 1] != 0xff.toByte() || data.last() != 0xd9.toByte()) {
                            throw FormatException("MJPG packet is not a complete JPEG")
                        }
                        data
                    }
                }
            }
            // Audio, JUNK, and indexes are discarded in bounded scratch-buffer reads.
            skip(size + (size and 1))
        }
    }

    private fun finishStream() {
        val current = stream ?: return
        stream = null
        if (!current.video || selected != null) return
        val bitmap = current.bitmap ?: throw FormatException("Video stream has no strf")
        if (bitmap.size < 40) throw FormatException("Truncated AVI BITMAPINFOHEADER")
        val headerSize = u32(bitmap, 0)
        if (headerSize !in 40..bitmap.size.toLong()) throw FormatException("Unsupported AVI bitmap header")
        val width = signed32(bitmap, 4)
        val rawHeight = signed32(bitmap, 8)
        val height = if (rawHeight == Int.MIN_VALUE) 0 else kotlin.math.abs(rawHeight)
        if (width !in 1..4096 || height !in 1..4096) throw FormatException("Unsupported AVI video dimensions")
        val handler = codecName(current.handler)
        val compressionRaw = fourcc(bitmap, 16)
        val compression = codecName(compressionRaw)
        val codec = handler ?: compression ?: throw FormatException(
            "Unsupported AVI video codec: ${current.handler.trim()} / ${compressionRaw.trim()}")
        if (handler != null && compression != null && handler != compression) {
            throw FormatException("Conflicting AVI codec headers")
        }
        if (compression == null && bitmap.sliceArray(16..19).any { it != 0.toByte() }) {
            throw FormatException("Unsupported AVI compression: ${compressionRaw.trim()}")
        }
        val extra = bitmap.copyOfRange(headerSize.toInt(), bitmap.size)
        val csd = if (codec == Codec.H264 && extra.isNotEmpty()) parseCodecData(extra) else emptyList()
        if (current.index > 99) throw FormatException("AVI video stream index exceeds supported range")
        selected = Format(codec, width, height, current.index.toString().padStart(2, '0'), csd)
    }

    private fun codecName(raw: String): Codec? = when (raw.trim().uppercase(Locale.ROOT)) {
        "H264", "X264", "AVC1" -> Codec.H264
        "MJPG" -> Codec.MJPG
        else -> null
    }

    private fun parseCodecData(extra: ByteArray): List<ByteArray> {
        val units = if (extra[0] == 1.toByte()) {
            if (extra.size < 7) throw FormatException("Truncated AVC configuration")
            avcConfiguration = true
            nalLengthBytes = (extra[4].toInt() and 3) + 1
            if (nalLengthBytes == 3) throw FormatException("Invalid AVC NAL length size")
            var at = 6
            val result = mutableListOf<ByteArray>()
            fun readUnits(count: Int) {
                if (count > 32) throw FormatException("Too many AVC parameter sets")
                repeat(count) {
                    if (at + 2 > extra.size) throw FormatException("Truncated AVC parameter set")
                    val n = ((extra[at].toInt() and 255) shl 8) or (extra[at + 1].toInt() and 255)
                    at += 2
                    if (n == 0 || at + n > extra.size) throw FormatException("Invalid AVC parameter set size")
                    result += extra.copyOfRange(at, at + n)
                    at += n
                }
            }
            readUnits(extra[5].toInt() and 31)
            if (at >= extra.size) throw FormatException("AVC configuration has no PPS count")
            readUnits(extra[at++].toInt() and 255)
            result
        } else annexUnits(extra) ?: throw FormatException("Unsupported H264 codec configuration")
        units.forEach(::validateNal)
        val sps = units.filter { (it[0].toInt() and 31) == 7 }
        val pps = units.filter { (it[0].toInt() and 31) == 8 }
        if (sps.isEmpty() || pps.isEmpty()) throw FormatException("H264 configuration lacks SPS/PPS")
        return listOf(joinAnnex(sps), joinAnnex(pps))
    }

    private fun normalizeH264(data: ByteArray): ByteArray {
        // An avcC length such as 00 00 01 65 also looks like an Annex B start code.
        // Prefer the declared length format, while retaining Annex B compatibility.
        val units = if (avcConfiguration) {
            try { lengthUnits(data) } catch (e: FormatException) { annexUnits(data) ?: throw e }
        } else annexUnits(data) ?: lengthUnits(data)
        if (units.isEmpty()) throw FormatException("Empty H264 packet")
        units.forEach(::validateNal)
        return joinAnnex(units)
    }

    private fun lengthUnits(data: ByteArray): List<ByteArray> {
        var at = 0
        val nals = mutableListOf<ByteArray>()
        while (at < data.size) {
            if (at + nalLengthBytes > data.size) throw FormatException("Truncated H264 NAL length")
            var size = 0L
            repeat(nalLengthBytes) { size = (size shl 8) or (data[at++].toLong() and 255) }
            if (size !in 1..(data.size - at).toLong()) throw FormatException("Invalid H264 NAL length")
            if (nals.size == MAX_NALS) throw FormatException("Too many H264 NAL units")
            nals += data.copyOfRange(at, at + size.toInt())
            at += size.toInt()
        }
        return nals
    }

    private fun annexUnits(data: ByteArray): List<ByteArray>? {
        fun startCode(at: Int): Int = when {
            at + 3 <= data.size && data[at] == 0.toByte() && data[at + 1] == 0.toByte() && data[at + 2] == 1.toByte() -> 3
            at + 4 <= data.size && data[at] == 0.toByte() && data[at + 1] == 0.toByte() &&
                data[at + 2] == 0.toByte() && data[at + 3] == 1.toByte() -> 4
            else -> 0
        }
        val first = startCode(0)
        if (first == 0) return null
        val result = mutableListOf<ByteArray>()
        var begin = first
        var at = begin
        while (at < data.size) {
            val code = startCode(at)
            if (code != 0) {
                if (at == begin) throw FormatException("Empty Annex B NAL")
                if (result.size == MAX_NALS) throw FormatException("Too many H264 NAL units")
                result += data.copyOfRange(begin, at)
                at += code
                begin = at
            } else at++
        }
        if (begin == data.size) throw FormatException("Truncated Annex B NAL")
        if (result.size == MAX_NALS) throw FormatException("Too many H264 NAL units")
        result += data.copyOfRange(begin, data.size)
        return result
    }

    private fun validateNal(unit: ByteArray) {
        if (unit.isEmpty() || (unit[0].toInt() and 128) != 0 || (unit[0].toInt() and 31) !in 1..23) {
            throw FormatException("Invalid or unsupported H264 NAL header")
        }
    }

    private fun joinAnnex(units: List<ByteArray>): ByteArray {
        val size = units.sumOf { it.size.toLong() + 4 }
        if (size > MAX_FRAME_BYTES) throw FormatException("Normalized H264 packet exceeds frame limit")
        val result = ByteArray(size.toInt())
        var at = 0
        units.forEach {
            result[at + 3] = 1
            at += 4
            it.copyInto(result, at)
            at += it.size
        }
        return result
    }

    private fun pushContainer(kind: String, size: Long) {
        if (containers.size >= MAX_NESTING) throw FormatException("AVI LIST nesting exceeds limit")
        val end = if (size == 0L || size == 0xffffffffL) null else offset + size - 4
        if (end != null) checkWithinContainer(size - 4 + (size and 1))
        containers += Container(kind, end, (size and 1) != 0L && end != null)
    }

    private fun finishContainers() {
        while (containers.isNotEmpty()) {
            val last = containers.last()
            val end = last.end ?: return
            if (offset < end) return
            if (offset > end) throw FormatException("AVI chunk crosses LIST boundary")
            containers.removeAt(containers.lastIndex)
            if (last.padded) skip(1)
        }
    }

    private fun checkWithinContainer(size: Long) {
        containers.mapNotNull { it.end }.minOrNull()?.let { end ->
            if (offset + size > end) throw FormatException("AVI chunk exceeds LIST boundary")
        }
    }

    private fun checkChunk(size: Long, limit: Int) {
        if (size > limit) throw FormatException("AVI chunk exceeds $limit byte limit")
    }

    private fun readId(): String? {
        checkWithinContainer(8) // The chunk header itself must fit, even for an empty payload.
        val first = input.read()
        if (first < 0) {
            if (containers.any { it.end != null && offset < it.end }) throw EOFException("Truncated AVI LIST at byte $offset")
            return null
        }
        offset++
        val rest = readExact(3)
        return first.toChar() + String(rest, Charsets.ISO_8859_1)
    }

    private fun readListType(size: Long): String {
        if (size in 1..3) throw FormatException("Invalid AVI LIST size")
        checkWithinContainer(if (size == 0L || size == 0xffffffffL) 4 else size + (size and 1))
        return fourcc(readExact(4))
    }

    private fun readExact(size: Int): ByteArray = ByteArray(size).also { data ->
        var at = 0
        while (at < size) {
            val n = input.read(data, at, size - at)
            if (n < 0) throw EOFException("Truncated AVI at byte $offset")
            if (n == 0) continue
            at += n
            offset += n
        }
    }

    private fun skip(size: Long) {
        var left = size
        while (left > 0) {
            val n = input.read(scratch, 0, minOf(left, scratch.size.toLong()).toInt())
            if (n < 0) throw EOFException("Truncated AVI at byte $offset")
            if (n == 0) continue
            left -= n
            offset += n
        }
    }

    private fun readU32(): Long = u32(readExact(4), 0)
    private fun fourcc(data: ByteArray, at: Int = 0): String = String(data, at, 4, Charsets.ISO_8859_1)
    private fun signed32(data: ByteArray, at: Int): Int = u32(data, at).toInt()
    private fun u32(data: ByteArray, at: Int): Long = (0..3).fold(0L) { value, i ->
        value or ((data[at + i].toLong() and 255) shl (8 * i))
    }
}
