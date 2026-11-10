// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class PreviewImageTest {
    @Test
    fun `only declared embedded sources enable padding recognition while bytes stay unchanged`() = runBlocking {
        val jpeg = byteArrayOf(0xff.toByte(), 0xd8.toByte(), 0xff.toByte(), 0xd9.toByte())
        for (source in listOf("jpeg-exif-thumbnail", "tiff-embedded-jpeg", "original-jpeg", "unknown", "")) {
            val server = FakeHttpServer {
                FakeHttpServer.Response(200, jpeg, "image/jpeg",
                    if (source.isEmpty()) emptyMap() else mapOf("X-Preview-Source" to source))
            }.start()
            try {
                val camera = NxCamera("127.0.0.1", server.port).apply {
                    profile = CameraProfile(galleryPreview = true)
                }
                val image = camera.fetchPreviewImage("100PHOTO/SAM_5398.JPG")
                assertEquals(source, source in setOf("jpeg-exif-thumbnail", "tiff-embedded-jpeg"), image.embedded)
                assertArrayEquals(jpeg, image.bytes)
                assertArrayEquals(jpeg, camera.fetchPreview("100PHOTO/SAM_5398.JPG"))
            } finally { server.stop() }
        }
    }
}
