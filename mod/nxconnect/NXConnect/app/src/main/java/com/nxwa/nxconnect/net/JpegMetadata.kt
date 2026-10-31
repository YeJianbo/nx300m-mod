package com.nxwa.nxconnect.net

import java.io.ByteArrayOutputStream
import java.io.InputStream

/** A complete JPEG marker header, ending at SOS before entropy-coded pixels. */
object JpegMetadata {
    fun readHeader(input: InputStream, limit: Int): ByteArray? {
        val output = ByteArrayOutputStream(minOf(limit, 65536))
        fun byte(): Int {
            if (output.size() >= limit) return -1
            val value = input.read()
            if (value >= 0) output.write(value)
            return value
        }
        if (byte() != 0xff || byte() != 0xd8) return null
        while (output.size() < limit) {
            if (byte() != 0xff) return null
            var marker = byte()
            while (marker == 0xff) marker = byte()
            if (marker < 0 || marker == 0) return null
            if (marker == 0xd9) return output.toByteArray()
            if (marker == 0x01 || marker in 0xd0..0xd7) continue
            if (marker == 0xd8) return null
            val high = byte(); val low = byte()
            if (high < 0 || low < 0) return null
            val length = (high shl 8) or low
            if (length < 2 || output.size().toLong() + length - 2 > limit) return null
            val buffer = ByteArray(length - 2)
            var read = 0
            while (read < buffer.size) {
                val count = input.read(buffer, read, buffer.size - read)
                if (count <= 0) return null
                read += count
            }
            output.write(buffer)
            if (marker == 0xda) return output.toByteArray()
        }
        return null
    }
}

data class JpegMetadataHeader(val bytes: ByteArray, val responseBytes: Long, val requests: Int)
