// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

class SrwExifEditorTest {
    private val source = File("../../../../work/reference-media/SAM_4262.SRW").canonicalFile
    private val changes = mapOf("LensMake" to "Carl Zeiss", "LensModel" to "Contax Carl Zeiss Planar 50mm F1.4",
        "FocalLength" to "50", "FNumber" to "1.4", "FocalLengthIn35mmFilm" to "77")

    @Test fun realNx300mCopyRetainsAllExistingDataExceptExifPointer() {
        assumeTrue("Local original SRW sample required", source.isFile)
        val original = source.readBytes()
        val raw = ByteBuffer.wrap(original).order(if (original[0] == 'M'.code.toByte()) ByteOrder.BIG_ENDIAN else ByteOrder.LITTLE_ENDIAN)
        val root = raw.getInt(4)
        val count = raw.getShort(root).toInt() and 0xffff
        val pointer = (0 until count).first { (raw.getShort(root + 2 + it * 12).toInt() and 0xffff) == 0x8769 }.let { root + 2 + it * 12 + 8 }
        val copy = File("../../../../outputs/nxconnect/raw-metadata/SAM_4262_lens.SRW").canonicalFile
        copy.parentFile.mkdirs()
        source.copyTo(copy, overwrite = true)
        SrwExifEditor.write(copy, changes)
        SrwExifEditor.verify(copy, changes)
        val updated = copy.readBytes()
        assertTrue(updated.size > original.size)
        val changedPositions = original.indices.filter { original[it] != updated[it] }
        assertTrue(changedPositions.isNotEmpty())
        assertTrue("Existing RAW, previews, and MakerNote must stay byte-identical", changedPositions.all { it in pointer until pointer + 4 })
        assertArrayEquals("Source file must remain untouched", original, source.readBytes())

        val cleared = File(copy.parentFile, "SAM_4262_cleared.SRW")
        copy.copyTo(cleared, overwrite = true)
        SrwExifEditor.write(cleared, mapOf("LensMake" to null, "LensModel" to null))
        SrwExifEditor.verify(cleared, changes - setOf("LensMake", "LensModel"))
        SrwExifEditor.verify(cleared, mapOf("LensMake" to null, "LensModel" to null))
    }

    @Test fun invalidFieldIsRejectedBeforeAnyFileMutation() {
        assumeTrue("Local original SRW sample required", source.isFile)
        val copy = File.createTempFile("nx-invalid-", ".SRW")
        try {
            source.copyTo(copy, overwrite = true)
            val bytes = copy.readBytes()
            assertThrows(IllegalArgumentException::class.java) { SrwExifEditor.write(copy, mapOf("FNumber" to "NaN")) }
            assertArrayEquals(bytes, copy.readBytes())
            assertThrows(IllegalArgumentException::class.java) { SrwExifEditor.write(copy, mapOf("ExposureTime" to "1/60")) }
            assertArrayEquals(bytes, copy.readBytes())
        } finally { copy.delete() }
    }
}
