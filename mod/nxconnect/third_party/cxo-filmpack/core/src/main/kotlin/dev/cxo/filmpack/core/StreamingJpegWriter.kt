/*
Copyright (c) 2008, Adobe Systems Incorporated
  All rights reserved.

  Redistribution and use in source and binary forms, with or without 
  modification, are permitted provided that the following conditions are
  met:

  * Redistributions of source code must retain the above copyright notice, 
    this list of conditions and the following disclaimer.
  
  * Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in the 
    documentation and/or other materials provided with the distribution.
  
  * Neither the name of Adobe Systems Incorporated nor the names of its 
    contributors may be used to endorse or promote products derived from 
    this software without specific prior written permission.

  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
  IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
  THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
  PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR 
  CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
  EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
  PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
  PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
  LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
  NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/
package dev.cxo.filmpack.core

import java.io.BufferedOutputStream
import java.io.OutputStream

/**
 * Streaming baseline JPEG, 8-bit YCbCr 4:4:4, adapted from jpeg-js's Adobe/AAN
 * encoder at commit 1031ccd7a63c641c16afb2430279d1598530a85c.
 * See THIRD_PARTY_NOTICES/jpeg-js-encoder.txt for source and attribution.
 *
 * [readRows] fills the first width*count packed ARGB values for up to eight
 * source rows. Alpha must already be composited by the caller. Only this row
 * buffer and small DCT/Huffman tables are retained, never a whole photo.
 */
object StreamingJpegWriter {
    fun write(
        output: OutputStream,
        width: Int,
        height: Int,
        quality: Int = 98,
        exifOrientation: Int = 1,
        readRows: (top: Int, count: Int, argb: IntArray) -> Unit,
    ) {
        require(width in 1..65535 && height in 1..65535) { "JPEG dimensions must be 1..65535" }
        require(quality in 1..100) { "JPEG quality must be 1..100" }
        require(exifOrientation in 1..8) { "EXIF orientation must be 1..8" }
        val buffered = if (output is BufferedOutputStream) output else BufferedOutputStream(output, 65536)
        Encoder(buffered, quality).write(width, height, exifOrientation, readRows)
        // The caller owns the stream. In particular, callback/I/O failures are
        // propagated without pretending that a partial image completed.
        buffered.flush()
    }

    private class Huffman(val codes: IntArray, val sizes: IntArray)
    private fun huffman(counts: IntArray, values: IntArray): Huffman {
        val result = Huffman(IntArray(256), IntArray(256))
        var code = 0
        var index = 0
        for (length in 1..16) {
            repeat(counts[length]) {
                val symbol = values[index++]
                result.codes[symbol] = code++
                result.sizes[symbol] = length
            }
            code = code shl 1
        }
        require(index == values.size)
        return result
    }

    private class Encoder(private val output: OutputStream, quality: Int) {
        private val yTable = IntArray(64)
        private val uvTable = IntArray(64)
        private val yScale = DoubleArray(64)
        private val uvScale = DoubleArray(64)
        private val yBlock = DoubleArray(64)
        private val uBlock = DoubleArray(64)
        private val vBlock = DoubleArray(64)
        private val du = IntArray(64)
        private var bitBuffer = 0
        private var bitCount = 0

        init {
            val scale = if (quality < 50) 5000 / quality else 200 - quality * 2
            for (i in 0 until 64) {
                yTable[ZIGZAG[i]] = ((Y_QUANT[i] * scale + 50) / 100).coerceIn(1, 255)
                uvTable[ZIGZAG[i]] = ((UV_QUANT[i] * scale + 50) / 100).coerceIn(1, 255)
            }
            for (row in 0 until 8) for (column in 0 until 8) {
                val i = row * 8 + column
                yScale[i] = 1.0 / (yTable[ZIGZAG[i]] * AAN_SCALE[row] * AAN_SCALE[column] * 8.0)
                uvScale[i] = 1.0 / (uvTable[ZIGZAG[i]] * AAN_SCALE[row] * AAN_SCALE[column] * 8.0)
            }
        }

        fun write(width: Int, height: Int, orientation: Int, rows: (Int, Int, IntArray) -> Unit) {
            word(0xffd8)
            writeApp0()
            writeOrientation(orientation)
            word(0xffdb); word(132)
            byte(0); for (value in yTable) byte(value)
            byte(1); for (value in uvTable) byte(value)
            word(0xffc0); word(17); byte(8); word(height); word(width); byte(3)
            byte(1); byte(0x11); byte(0)
            byte(2); byte(0x11); byte(1)
            byte(3); byte(0x11); byte(1)
            word(0xffc4); word(0x01a2)
            writeHuffman(0x00, DC_Y_COUNTS, DC_Y_VALUES)
            writeHuffman(0x10, AC_Y_COUNTS, AC_Y_VALUES)
            writeHuffman(0x01, DC_UV_COUNTS, DC_UV_VALUES)
            writeHuffman(0x11, AC_UV_COUNTS, AC_UV_VALUES)
            word(0xffda); word(12); byte(3)
            byte(1); byte(0); byte(2); byte(0x11); byte(3); byte(0x11)
            byte(0); byte(0x3f); byte(0)

            val pixels = IntArray(width * 8)
            var dcY = 0
            var dcU = 0
            var dcV = 0
            var top = 0
            while (top < height) {
                val count = minOf(8, height - top)
                rows(top, count, pixels)
                var left = 0
                while (left < width) {
                    for (position in 0 until 64) {
                        val row = minOf(position ushr 3, count - 1)
                        val column = minOf(left + (position and 7), width - 1)
                        val argb = pixels[row * width + column]
                        val r = (argb ushr 16) and 255
                        val g = (argb ushr 8) and 255
                        val b = argb and 255
                        yBlock[position] = (((RGB_YUV[r] + RGB_YUV[g + 256] + RGB_YUV[b + 512]) shr 16) - 128).toDouble()
                        uBlock[position] = (((RGB_YUV[r + 768] + RGB_YUV[g + 1024] + RGB_YUV[b + 1280]) shr 16) - 128).toDouble()
                        vBlock[position] = (((RGB_YUV[r + 1280] + RGB_YUV[g + 1536] + RGB_YUV[b + 1792]) shr 16) - 128).toDouble()
                    }
                    dcY = block(yBlock, yScale, dcY, DC_Y, AC_Y)
                    dcU = block(uBlock, uvScale, dcU, DC_UV, AC_UV)
                    dcV = block(vBlock, uvScale, dcV, DC_UV, AC_UV)
                    left += 8
                }
                top += count
            }
            // Preserve the upstream all-one bit padding, including its complete
            // fill byte when the entropy stream already ends on a byte boundary.
            val padding = 8 - bitCount
            bits((1 shl padding) - 1, padding)
            word(0xffd9)
        }

        private fun byte(value: Int) = output.write(value and 255)
        private fun word(value: Int) { byte(value ushr 8); byte(value) }

        private fun bits(value: Int, count: Int) {
            bitBuffer = (bitBuffer shl count) or value
            bitCount += count
            while (bitCount >= 8) {
                val next = (bitBuffer ushr (bitCount - 8)) and 255
                byte(next)
                if (next == 255) byte(0)
                bitCount -= 8
            }
        }

        private fun symbol(table: Huffman, value: Int) {
            val count = table.sizes[value]
            require(count != 0) { "Coefficient has no baseline JPEG Huffman symbol: $value" }
            bits(table.codes[value], count)
        }

        private fun amplitude(value: Int, count: Int) =
            if (value >= 0) value else (1 shl count) - 1 + value

        private fun block(data: DoubleArray, factors: DoubleArray, previous: Int, dc: Huffman, ac: Huffman): Int {
            dct(data)
            for (i in 0 until 64) {
                val value = data[i] * factors[i]
                du[ZIGZAG[i]] = if (value > 0.0) (value + 0.5).toInt() else (value - 0.5).toInt()
            }
            val current = du[0]
            val difference = current - previous
            if (difference == 0) symbol(dc, 0) else {
                val count = 32 - Integer.numberOfLeadingZeros(kotlin.math.abs(difference))
                symbol(dc, count)
                bits(amplitude(difference, count), count)
            }
            var end = 63
            while (end > 0 && du[end] == 0) end--
            if (end == 0) {
                symbol(ac, 0)
                return current
            }
            var i = 1
            while (i <= end) {
                val start = i
                while (i <= end && du[i] == 0) i++
                var zeroes = i - start
                while (zeroes >= 16) { symbol(ac, 0xf0); zeroes -= 16 }
                val value = du[i]
                val count = 32 - Integer.numberOfLeadingZeros(kotlin.math.abs(value))
                symbol(ac, (zeroes shl 4) + count)
                bits(amplitude(value, count), count)
                i++
            }
            if (end != 63) symbol(ac, 0)
            return current
        }

        /** Same AAN pass equations and Double constants as the upstream encoder. */
        private fun dct(data: DoubleArray) {
            for (row in 0 until 8) pass(data, row * 8, 1)
            for (column in 0 until 8) pass(data, column, 8)
        }

        private fun pass(data: DoubleArray, offset: Int, step: Int) {
            val d0 = data[offset]
            val d1 = data[offset + step]
            val d2 = data[offset + 2 * step]
            val d3 = data[offset + 3 * step]
            val d4 = data[offset + 4 * step]
            val d5 = data[offset + 5 * step]
            val d6 = data[offset + 6 * step]
            val d7 = data[offset + 7 * step]
            val tmp0 = d0 + d7
            val tmp7 = d0 - d7
            val tmp1 = d1 + d6
            val tmp6 = d1 - d6
            val tmp2 = d2 + d5
            val tmp5 = d2 - d5
            val tmp3 = d3 + d4
            val tmp4 = d3 - d4
            var tmp10 = tmp0 + tmp3
            val tmp13 = tmp0 - tmp3
            var tmp11 = tmp1 + tmp2
            var tmp12 = tmp1 - tmp2
            data[offset] = tmp10 + tmp11
            data[offset + 4 * step] = tmp10 - tmp11
            val z1 = (tmp12 + tmp13) * 0.707106781
            data[offset + 2 * step] = tmp13 + z1
            data[offset + 6 * step] = tmp13 - z1
            tmp10 = tmp4 + tmp5
            tmp11 = tmp5 + tmp6
            tmp12 = tmp6 + tmp7
            val z5 = (tmp10 - tmp12) * 0.382683433
            val z2 = 0.541196100 * tmp10 + z5
            val z4 = 1.306562965 * tmp12 + z5
            val z3 = tmp11 * 0.707106781
            val z11 = tmp7 + z3
            val z13 = tmp7 - z3
            data[offset + 5 * step] = z13 + z2
            data[offset + 3 * step] = z13 - z2
            data[offset + step] = z11 + z4
            data[offset + 7 * step] = z11 - z4
        }

        private fun writeApp0() {
            word(0xffe0); word(16)
            byte(0x4a); byte(0x46); byte(0x49); byte(0x46); byte(0)
            byte(1); byte(1); byte(0); word(1); word(1); byte(0); byte(0)
        }

        private fun writeOrientation(orientation: Int) {
            word(0xffe1); word(34)
            val payload = intArrayOf(
                0x45,0x78,0x69,0x66,0,0, 0x49,0x49,0x2a,0,8,0,0,0,
                1,0, 0x12,1,3,0,1,0,0,0,orientation,0,0,0, 0,0,0,0,
            )
            for (value in payload) byte(value)
        }

        private fun writeHuffman(id: Int, counts: IntArray, values: IntArray) {
            byte(id)
            for (length in 1..16) byte(counts[length])
            for (value in values) byte(value)
        }
    }

    private val ZIGZAG = intArrayOf(
        0, 1, 5, 6, 14, 15, 27, 28, 2, 4, 7, 13, 16, 26, 29, 42,
        3, 8, 12, 17, 25, 30, 41, 43, 9, 11, 18, 24, 31, 40, 44, 53,
        10, 19, 23, 32, 39, 45, 52, 54, 20, 22, 33, 38, 46, 51, 55, 60,
        21, 34, 37, 47, 50, 56, 59, 61, 35, 36, 48, 49, 57, 58, 62, 63
    )

    private val DC_Y_COUNTS = intArrayOf(
        0, 0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,
        0
    )

    private val DC_Y_VALUES = intArrayOf(
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
    )

    private val AC_Y_COUNTS = intArrayOf(
        0, 0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1,
        0x7d
    )

    private val AC_Y_VALUES = intArrayOf(
        0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
        0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
        0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
        0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
        0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
        0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
        0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
        0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
        0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
        0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
        0xf9, 0xfa
    )

    private val DC_UV_COUNTS = intArrayOf(
        0, 0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0,
        0
    )

    private val DC_UV_VALUES = intArrayOf(
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
    )

    private val AC_UV_COUNTS = intArrayOf(
        0, 0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2,
        0x77
    )

    private val AC_UV_VALUES = intArrayOf(
        0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
        0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
        0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
        0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
        0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
        0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
        0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
        0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
        0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
        0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
        0xf9, 0xfa
    )

    private val Y_QUANT = intArrayOf(
        16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
        14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
        18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
        49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99
    )

    private val UV_QUANT = intArrayOf(
        17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
        24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99,
        99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99,
        99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99
    )

    private val AAN_SCALE = doubleArrayOf(1.0, 1.387039845, 1.306562965, 1.175875602,
        1.0, 0.785694958, 0.541196100, 0.275899379)
    private val RGB_YUV = IntArray(2048).apply {
        for (i in 0 until 256) {
            this[i] = 19595 * i
            this[i + 256] = 38470 * i
            this[i + 512] = 7471 * i + 0x8000
            this[i + 768] = -11059 * i
            this[i + 1024] = -21709 * i
            this[i + 1280] = 32768 * i + 0x807fff
            this[i + 1536] = -27439 * i
            this[i + 1792] = -5329 * i
        }
    }
    private val DC_Y = huffman(DC_Y_COUNTS, DC_Y_VALUES)
    private val AC_Y = huffman(AC_Y_COUNTS, AC_Y_VALUES)
    private val DC_UV = huffman(DC_UV_COUNTS, DC_UV_VALUES)
    private val AC_UV = huffman(AC_UV_COUNTS, AC_UV_VALUES)
}
