// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import java.io.File
import java.io.RandomAccessFile
import java.math.BigDecimal
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** NX300 TIFF SRW copies only. Existing image/MakerNote bytes and TIFF offsets never move. */
internal object SrwExifEditor {
    private const val UINT_MAX = 0xffffffffL
    private val tags = mapOf("LensMake" to 0xa433, "LensModel" to 0xa434,
        "FocalLength" to 0x920a, "FNumber" to 0x829d, "FocalLengthIn35mmFilm" to 0xa405,
        "DateTimeOriginal" to 0x9003, "UserComment" to 0x9286)
    private data class Entry(val tag: Int, val type: Int, val count: Long, val bytes: ByteArray)
    private data class Layout(val order: ByteOrder, val pointerAt: Long, val exif: List<Entry>, val next: Int)
    private data class Value(val type: Int, val count: Int, val bytes: ByteArray)

    private fun buffer(bytes: ByteArray, order: ByteOrder) = ByteBuffer.wrap(bytes).order(order)
    private fun read(file: RandomAccessFile, offset: Long, count: Int): ByteArray {
        require(offset >= 0 && count >= 0 && offset <= file.length() - count) { "SRW 元数据范围无效" }
        return ByteArray(count).also { file.seek(offset); file.readFully(it) }
    }
    private fun uint(file: RandomAccessFile, offset: Long, order: ByteOrder) =
        buffer(read(file, offset, 4), order).int.toLong() and UINT_MAX
    private fun entries(file: RandomAccessFile, offset: Long, order: ByteOrder): Pair<List<Entry>, Int> {
        require(offset >= 8) { "SRW 目录偏移无效" }
        val size = buffer(read(file, offset, 2), order).short.toInt() and 0xffff
        require(size in 1..4096) { "SRW 目录大小无效" }
        val data = read(file, offset + 2, size * 12 + 4)
        val rows = (0 until size).map { i ->
            val bytes = data.copyOfRange(i * 12, i * 12 + 12)
            val row = buffer(bytes, order)
            Entry(row.short.toInt() and 0xffff, row.short.toInt() and 0xffff,
                row.int.toLong() and UINT_MAX, bytes)
        }
        require(rows.map { it.tag }.distinct().size == rows.size) { "SRW 目录存在重复标签" }
        return rows to buffer(data.copyOfRange(size * 12, data.size), order).int
    }
    private fun value(file: RandomAccessFile, entry: Entry, order: ByteOrder): ByteArray {
        val width = when (entry.type) { 1, 2, 7 -> 1; 3 -> 2; 4, 9 -> 4; 5, 10 -> 8; else -> error("不支持的 SRW 元数据类型") }
        val count = entry.count * width
        require(count in 0..65536) { "SRW 元数据长度无效" }
        return if (count <= 4) entry.bytes.copyOfRange(8, 8 + count.toInt())
        else read(file, buffer(entry.bytes, order).getInt(8).toLong() and UINT_MAX, count.toInt())
    }
    private fun layout(file: RandomAccessFile): Layout {
        require(file.length() in 8..UINT_MAX) { "不支持的 SRW 文件大小" }
        val header = read(file, 0, 8)
        val order = when (String(header, 0, 2, Charsets.US_ASCII)) {
            "II" -> ByteOrder.LITTLE_ENDIAN; "MM" -> ByteOrder.BIG_ENDIAN
            else -> error("SRW 不是 TIFF 格式")
        }
        require(buffer(header, order).getShort(2).toInt() == 42) { "不支持的 SRW TIFF 版本" }
        val rootAt = buffer(header, order).getInt(4).toLong() and UINT_MAX
        val root = entries(file, rootAt, order).first
        fun text(tag: Int) = root.firstOrNull { it.tag == tag }?.let {
            require(it.type == 2) { "SRW 相机信息格式无效" }
            String(value(file, it, order), Charsets.US_ASCII).trimEnd('\u0000', ' ')
        }.orEmpty()
        val model = text(0x110).uppercase().replace(Regex("[^A-Z0-9]"), "")
        require(text(0x10f).uppercase().startsWith("SAMSUNG") && model in setOf("NX300", "NX300M", "SAMSUNGNX300", "SAMSUNGNX300M")) {
            "当前仅支持 NX300／NX300M 的 SRW 镜头信息写入"
        }
        val index = root.indexOfFirst { it.tag == 0x8769 }
        require(index >= 0 && root[index].type == 4 && root[index].count == 1L) { "SRW 缺少有效 EXIF 目录" }
        val pointerAt = rootAt + 2 + index * 12 + 8
        val (exif, next) = entries(file, uint(file, pointerAt, order), order)
        return Layout(order, pointerAt, exif, next)
    }
    private fun encode(tag: String, text: String, order: ByteOrder): Value = when (tag) {
        "LensMake", "LensModel", "DateTimeOriginal" -> {
            require(text.length <= 1024 && text.all { it.code in 32..126 }) { "镜头信息须使用英文、数字及常用符号" }
            if (tag == "DateTimeOriginal") {
                require(text.matches(Regex("\\d{4}:\\d{2}:\\d{2} \\d{2}:\\d{2}:\\d{2}"))) { "拍摄日期格式无效" }
                java.text.SimpleDateFormat("yyyy:MM:dd HH:mm:ss", java.util.Locale.US).apply { isLenient = false }.parse(text)
            }
            val bytes = text.toByteArray(Charsets.US_ASCII) + byteArrayOf(0)
            Value(2, bytes.size, bytes)
        }
        "FocalLength", "FNumber" -> {
            val number = BigDecimal(text).stripTrailingZeros()
            require(number > BigDecimal.ZERO && number <= BigDecimal(65535) && number.scale() <= 9) { "焦距或光圈无效" }
            var denominator = BigInteger.TEN.pow(number.scale().coerceAtLeast(0))
            var numerator = number.multiply(BigDecimal(denominator)).toBigIntegerExact()
            val gcd = numerator.gcd(denominator)
            numerator /= gcd; denominator /= gcd
            require(numerator.bitLength() <= 32 && denominator.bitLength() <= 32) { "焦距或光圈精度过高" }
            Value(5, 1, ByteBuffer.allocate(8).order(order).putInt(numerator.toLong().toInt()).putInt(denominator.toLong().toInt()).array())
        }
        "FocalLengthIn35mmFilm" -> {
            val number = BigDecimal(text).intValueExact()
            require(number in 1..65535) { "等效焦距无效" }
            Value(3, 1, ByteBuffer.allocate(2).order(order).putShort(number.toShort()).array())
        }
        "UserComment" -> {
            require(text.length <= 4096) { "备注过长" }
            val bytes = "UNICODE\u0000".toByteArray(Charsets.US_ASCII) + byteArrayOf(0xfe.toByte(), 0xff.toByte()) + text.toByteArray(Charsets.UTF_16BE)
            Value(7, bytes.size, bytes)
        }
        else -> error("不支持的 SRW 修改字段：$tag")
    }

    fun write(file: File, changes: Map<String, String?>) {
        require(file.extension.equals("srw", true)) { "仅支持 SRW 副本" }
        require(changes.keys.all { it in tags }) { "含不支持的 SRW 修改字段" }
        if (changes.isEmpty()) return
        RandomAccessFile(file, "rw").use { raw ->
            val layout = layout(raw)
            val updates = changes.mapKeys { tags.getValue(it.key) }.mapValues { (id, text) ->
                text?.let { encode(tags.entries.first { it.value == id }.key, it, layout.order) }
            }
            val remaining = layout.exif.filter { it.tag !in updates }
            val ids = (remaining.map { it.tag } + updates.filterValues { it != null }.keys).sorted()
            require(ids.size in 1..4096) { "SRW EXIF 标签数量无效" }
            val start = (raw.length() + 1L) and -2L
            val tableSize = 2 + ids.size * 12 + 4
            var payloadAt = start + tableSize
            val table = ByteBuffer.allocate(tableSize).order(layout.order).putShort(ids.size.toShort())
            val payload = java.io.ByteArrayOutputStream()
            for (id in ids) {
                val update = updates[id]
                if (update == null) table.put(remaining.first { it.tag == id }.bytes)
                else {
                    table.putShort(id.toShort()).putShort(update.type.toShort()).putInt(update.count)
                    if (update.bytes.size <= 4) table.put(update.bytes).put(ByteArray(4 - update.bytes.size))
                    else {
                        require(payloadAt <= UINT_MAX) { "SRW 文件超过 TIFF 范围" }
                        table.putInt(payloadAt.toInt())
                        payload.write(update.bytes)
                        payloadAt += update.bytes.size
                        if (payloadAt % 2L != 0L) { payload.write(0); payloadAt++ }
                    }
                }
            }
            require(payloadAt <= UINT_MAX) { "SRW 文件超过 TIFF 范围" }
            table.putInt(layout.next)
            // Publish the new EXIF directory only after its entire payload has reached the copy.
            raw.seek(raw.length())
            if (raw.filePointer < start) raw.write(0)
            raw.write(table.array()); raw.write(payload.toByteArray()); raw.fd.sync()
            val previous = read(raw, layout.pointerAt, 4)
            try {
                raw.seek(layout.pointerAt)
                raw.write(ByteBuffer.allocate(4).order(layout.order).putInt(start.toInt()).array())
                raw.fd.sync()
                verify(raw, changes)
            } catch (failure: Exception) {
                runCatching { raw.seek(layout.pointerAt); raw.write(previous); raw.fd.sync() }
                throw failure
            }
        }
    }

    fun verify(file: File, changes: Map<String, String?>) = RandomAccessFile(file, "r").use { verify(it, changes) }
    private fun verify(raw: RandomAccessFile, changes: Map<String, String?>) {
        val layout = layout(raw)
        changes.forEach { (tag, text) ->
            val row = layout.exif.firstOrNull { it.tag == tags.getValue(tag) }
            if (text == null) check(row == null) { "SRW 字段未清除：$tag" }
            else {
                val expected = encode(tag, text, layout.order)
                check(row != null && row.type == expected.type && row.count == expected.count.toLong() && value(raw, row, layout.order).contentEquals(expected.bytes)) {
                    "SRW 信息保存后读回不一致：$tag"
                }
            }
        }
    }
}
