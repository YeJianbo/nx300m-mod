package com.nxwa.nxconnect.net

import java.io.ByteArrayInputStream
import java.io.EOFException
import org.junit.Assert.*
import org.junit.Test

/** Synthetic protocol fixtures, not captured camera output or decoder validation. */
class RiffVideoReaderTest {
    private fun le(n: Long) = ByteArray(4) { (n shr (it * 8)).toByte() }
    private fun text(s: String) = s.toByteArray(Charsets.ISO_8859_1)
    private fun chunk(id: String, data: ByteArray): ByteArray =
        text(id) + le(data.size.toLong()) + data + if (data.size % 2 == 1) byteArrayOf(0) else byteArrayOf()
    private fun list(kind: String, data: ByteArray) = chunk("LIST", text(kind) + data)
    private fun header(codec: String, extra: ByteArray = byteArrayOf(), unknownLists: Boolean = false): ByteArray {
        val strh = text("vids$codec") + ByteArray(48)
        val strf = le(40) + le(640) + le(480) + byteArrayOf(1, 0, 24, 0) + text(codec) + ByteArray(20) + extra
        val stream = chunk("strh", strh) + chunk("strf", strf)
        val hdrl = if (unknownLists) text("LIST") + le(0) + text("hdrl") +
            text("LIST") + le(0) + text("strl") + stream else list("hdrl", list("strl", stream))
        return text("RIFF") + le(0) + text("AVI ") + hdrl + text("LIST") + le(0) + text("movi")
    }

    @Test fun `zero RIFF lengths fragmented input nested rec and skipped audio`() {
        val annex = byteArrayOf(0, 0, 1, 0x65, 0x11, 0x22)
        val bytes = header("H264", unknownLists = true) + chunk("01wb", byteArrayOf(7)) +
            list("rec ", chunk("00dc", annex)) + chunk("idx1", ByteArray(16)) + chunk("00db", annex)
        val fragmented = object : ByteArrayInputStream(bytes) {
            override fun read(b: ByteArray, off: Int, len: Int): Int = super.read(b, off, minOf(len, 3))
        }
        val reader = RiffVideoReader(fragmented)
        assertEquals(RiffVideoReader.Codec.H264, reader.readHeader().codec)
        assertEquals(640, reader.readHeader().width)
        val expected = byteArrayOf(0, 0, 0, 1, 0x65, 0x11, 0x22)
        assertArrayEquals(expected, reader.nextFrame())
        assertArrayEquals(expected, reader.nextFrame())
        assertNull(reader.nextFrame())
    }

    @Test fun `avcC describes two byte lengths and SPS PPS`() {
        val avcc = byteArrayOf(1, 66, 0, 30, 0xfd.toByte(), 0xe1.toByte(),
            0, 2, 0x67, 66, 1, 0, 2, 0x68, 1)
        val reader = RiffVideoReader(ByteArrayInputStream(header("avc1", avcc) +
            chunk("00dc", byteArrayOf(0, 2, 0x65, 9, 0, 2, 0x61, 8))))
        val format = reader.readHeader()
        assertEquals(2, format.codecSpecificData.size)
        assertArrayEquals(byteArrayOf(0, 0, 0, 1, 0x67, 66), format.codecSpecificData[0])
        assertArrayEquals(byteArrayOf(0, 0, 0, 1, 0x65, 9, 0, 0, 0, 1, 0x61, 8), reader.nextFrame())
    }

    @Test fun `MJPG is accepted only with actual MJPG header and complete markers`() {
        val jpg = byteArrayOf(0xff.toByte(), 0xd8.toByte(), 7, 0xff.toByte(), 0xd9.toByte())
        val reader = RiffVideoReader(ByteArrayInputStream(header("MJPG") + chunk("00dc", jpg)))
        assertEquals(RiffVideoReader.Codec.MJPG, reader.readHeader().codec)
        assertArrayEquals(jpg, reader.nextFrame())
        assertThrows(RiffVideoReader.FormatException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("HEVC"))).readHeader()
        }
    }

    @Test fun `oversized frames malformed lengths and truncated packet fail bounded`() {
        assertThrows(RiffVideoReader.FormatException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("H264") + text("00dc") +
                le(RiffVideoReader.MAX_FRAME_BYTES.toLong() + 1))).nextFrame()
        }
        assertThrows(RiffVideoReader.FormatException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("H264") + chunk("00dc", byteArrayOf(0, 0, 0, 9, 0x65)))).nextFrame()
        }
        assertThrows(EOFException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("H264") + text("00dc") + le(8) + byteArrayOf(1))).nextFrame()
        }
    }

    @Test fun `video chunks cannot overrun finite rec LIST`() {
        val data = text("LIST") + le(12) + text("rec ") + text("00dc") + le(16) + ByteArray(16)
        assertThrows(RiffVideoReader.FormatException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("H264") + data)).nextFrame()
        }
    }

    @Test fun `avcC length that resembles Annex B is decoded as a length`() {
        val avcc = byteArrayOf(1, 66, 0, 30, 0xff.toByte(), 0xe1.toByte(),
            0, 2, 0x67, 66, 1, 0, 2, 0x68, 1)
        val nal = byteArrayOf(0x65) + ByteArray(356) { 0x22 }
        // 357-byte NAL: 00 00 01 65 is a length, not a three-byte start code + NAL header.
        val packet = byteArrayOf(0, 0, 1, 0x65) + nal
        val reader = RiffVideoReader(ByteArrayInputStream(header("avc1", avcc) + chunk("00dc", packet)))
        assertArrayEquals(byteArrayOf(0, 0, 0, 1) + nal, reader.nextFrame())
    }

    @Test fun `unknown media LIST is rejected before skipping past finite parent`() {
        val data = text("LIST") + le(16) + text("rec ") + text("LIST") + le(20) + text("JUNK")
        assertThrows(RiffVideoReader.FormatException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("H264") + data)).nextFrame()
        }
    }

    @Test fun `EOF inside finite LIST is a truncated stream not clean end`() {
        val data = text("LIST") + le(12) + text("rec ")
        assertThrows(EOFException::class.java) {
            RiffVideoReader(ByteArrayInputStream(header("H264") + data)).nextFrame()
        }
    }
}
