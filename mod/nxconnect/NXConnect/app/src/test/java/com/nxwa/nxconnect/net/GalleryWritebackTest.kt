// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MediaEntry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

class GalleryWritebackTest {
    private val entry = MediaEntry("100PHOTO", "SAM_0001.JPG")
    private fun response(text: String) = FakeHttpServer.Response(body = text.toByteArray(), contentType = "application/json")

    @Test
    fun `old daemon is rejected before any upload`() = runBlocking {
        var uploads = 0
        val server = FakeHttpServer { req ->
            if (req.path.endsWith("upload")) uploads++
            response("""{"model":"NX300M","capabilities":{}}""")
        }.start()
        val file = File.createTempFile("writeback-", ".jpg").apply { writeBytes(byteArrayOf(-1, -40, -1, -39)) }
        try {
            val camera = NxCamera("127.0.0.1", server.port)
            try { camera.uploadMedia(entry, file); fail("旧服务不应接收回写") }
            catch (e: IllegalStateException) { assertTrue(e.message!!.contains("备份")) }
            assertEquals(0, uploads)
        } finally { file.delete(); server.stop() }
    }

    @Test
    fun `writeback requires backup acknowledgement and correct byte count`() = runBlocking {
        var acknowledged = false
        var uploads = 0
        val server = FakeHttpServer { req ->
            when (req.path) {
                "/api/v1/camera/info" -> response("""{"model":"NX300M","capabilities":{"gallery_writeback":true,"gallery_original_backup":true}}""")
                "/api/v1/gallery/upload/capabilities" -> response("""{"ok":true,"backup_available":true,"original_download_available":true}""")
                "/api/v1/gallery/original", "/DCIM/${entry.relPath}" -> FakeHttpServer.Response(
                    body = byteArrayOf(-1, -40, -1, -39), contentType = "image/jpeg")
                "/api/v1/gallery/upload" -> {
                    uploads++
                    assertEquals("1", req.query["require_backup"])
                    assertEquals(entry.relPath, req.query["p"])
                    response(if (acknowledged) """{"ok":true,"backup_available":true,"bytes":4}"""
                        else """{"ok":true,"message":"updated"}""")
                }
                "/api/v1/gallery/edit-status" -> response("""{"ok":true,"backup_available":true,"size":4}""")
                "/api/v1/gallery/restore" -> {
                    assertEquals("POST", req.method)
                    response("""{"ok":true,"backup_available":true}""")
                }
                else -> FakeHttpServer.Response(code = 404)
            }
        }.start()
        val file = File.createTempFile("writeback-", ".jpg").apply { writeBytes(byteArrayOf(-1, -40, -1, -39)) }
        try {
            val camera = NxCamera("127.0.0.1", server.port)
            try { camera.uploadMedia(entry, file); fail("只返回 updated 不得当成成功") }
            catch (e: IllegalStateException) { assertTrue(e.message!!.contains("完整写入")) }
            acknowledged = true
            assertTrue(camera.uploadMedia(entry, file))
            assertEquals(2, uploads)
            val status = camera.galleryEditStatus(entry)
            assertTrue(status.backupAvailable)
            assertEquals(4L, status.size)
            camera.restoreMedia(entry)
        } finally { file.delete(); server.stop() }
    }

    @Test
    fun `restore refuses old backup service before any write`() = runBlocking {
        var restores = 0
        val server = FakeHttpServer { req ->
            when (req.path) {
                "/api/v1/camera/info" -> response("""{"model":"NX300M","capabilities":{"gallery_writeback":true,"gallery_original_backup":true}}""")
                "/api/v1/gallery/upload/capabilities" -> response("""{"ok":true,"backup_available":true}""")
                else -> { restores++; FakeHttpServer.Response(code = 404) }
            }
        }.start()
        try {
            try { NxCamera("127.0.0.1", server.port).restoreMedia(entry); fail("无法读取备份不得执行还原") }
            catch (e: IllegalStateException) { assertTrue(e.message!!.contains("更新服务")) }
            assertEquals(0, restores)
        } finally { server.stop() }
    }

    @Test
    fun `restore verifies every byte and both lengths rather than acknowledgement only`() = runBlocking {
        val backup = ByteArray(150_000) { (it % 251).toByte() }
        val variants = listOf(backup.copyOf().apply { this[100_000] = (this[100_000] + 1).toByte() },
            backup.copyOf(backup.size - 1), backup + byteArrayOf(0))
        for (readback in variants) {
            var restores = 0
            val cache = kotlin.io.path.createTempDirectory("restore-cache-").toFile()
            val server = FakeHttpServer { req ->
                when (req.path) {
                    "/api/v1/camera/info" -> response("""{"model":"NX300M","capabilities":{"gallery_writeback":true,"gallery_original_backup":true}}""")
                    "/api/v1/gallery/upload/capabilities" -> response("""{"ok":true,"backup_available":true,"original_download_available":true}""")
                    "/api/v1/gallery/original" -> {
                        assertEquals(entry.relPath, req.query["p"])
                        FakeHttpServer.Response(body = backup, contentType = "image/jpeg")
                    }
                    "/api/v1/gallery/restore" -> {
                        restores++
                        response("""{"ok":true,"backup_available":true}""")
                    }
                    "/DCIM/${entry.relPath}" -> FakeHttpServer.Response(body = readback, contentType = "image/jpeg")
                    else -> FakeHttpServer.Response(code = 404)
                }
            }.start()
            try {
                val camera = NxCamera("127.0.0.1", server.port).apply { restoreCacheDirectory = cache }
                try { camera.restoreMedia(entry); fail("内容或长度不符不得报告还原成功") }
                catch (e: IllegalStateException) { assertTrue(e.message!!.contains("还原后的文件")) }
                assertEquals(1, restores)
                assertEquals(0, cache.listFiles()!!.size)
            } finally { server.stop(); cache.delete() }
        }
    }

    @Test
    fun `connection change while reading original prevents restore and deletes temporary copy`() = runBlocking {
        val current = AtomicBoolean(true)
        var restores = 0
        val cache = kotlin.io.path.createTempDirectory("restore-cache-").toFile()
        val server = FakeHttpServer { req ->
            when (req.path) {
                "/api/v1/camera/info" -> response("""{"model":"NX300M","capabilities":{"gallery_writeback":true,"gallery_original_backup":true}}""")
                "/api/v1/gallery/upload/capabilities" -> response("""{"ok":true,"backup_available":true,"original_download_available":true}""")
                "/api/v1/gallery/original" -> {
                    current.set(false)
                    FakeHttpServer.Response(body = ByteArray(100_000), contentType = "image/jpeg")
                }
                else -> { restores++; FakeHttpServer.Response(code = 404) }
            }
        }.start()
        try {
            val camera = NxCamera("127.0.0.1", server.port).apply {
                restoreCacheDirectory = cache
                isCurrentConnection = { current.get() }
            }
            try { camera.restoreMedia(entry); fail("切换连接后不得继续还原") }
            catch (e: IllegalStateException) { assertTrue(e.message!!.contains("连接已改变")) }
            assertEquals(0, restores)
            assertEquals(0, cache.listFiles()!!.size)
        } finally { server.stop(); cache.delete() }
    }
}
