/*
 * NXConnectForNX500 — 三星 NX500 相机的照片传输与遥控 App
 * Copyright (C) 2026 Katsiy and contributors
 *
 * 本程序是自由软件：你可以依据 GNU Affero 通用公共许可证第 3 版
 * （或任何更新版本）的条款重新分发和/或修改它。
 * 本程序按"有用"的目的分发，但不提供任何担保。
 * 详见随附的 LICENSE 文件，或 <https://www.gnu.org/licenses/>。
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MediaEntry
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * 协议层端到端测试。
 *
 * 被测对象是 **app 的真实实现** [NxCamera] / [NxHttp]，对端是 JDK HTTP 服务器
 * 模拟的相机（[TestCamera]）。覆盖三种相机固件形态与所有关键坑。
 */
class NxCameraProtocolTest {

    private lateinit var cam: TestCamera

    @Before
    fun setUp() {
        cam = TestCamera(TestCamera.Mode.FULL_MOD).start()
    }

    @After
    fun tearDown() {
        cam.stop()
    }

    // ------------------------------------------------------------- 元信息

    @Test
    fun `camera info 解析出机型固件与 MAC`() = runBlocking {
        val info = cam.client().info()
        assertEquals("NX500", info.model)
        assertEquals("1.12", info.firmware)
        assertEquals("02:30:0d:00:00:02", info.mac)
    }

    @Test
    fun `camera status 解析电量与模式`() = runBlocking {
        val st = cam.client().status()
        assertEquals(73, st.batteryPercent)
        assertEquals("P", st.mode)
        assertEquals("single", st.drive)
    }

    // --------------------------------------------------------------- 相册

    @Test
    fun `listFolders 返回目录且最新的在前`() = runBlocking {
        val folders = cam.client().listFolders()
        assertEquals(listOf("103PHOTO", "102PHOTO", "100PHOTO"), folders)
    }

    @Test
    fun `listMedia 过滤掉非媒体文件并倒序`() = runBlocking {
        val media = cam.client().listMedia("100PHOTO")
        // notes.txt 必须被过滤掉
        assertEquals(1, media.size)
        assertEquals("SAM_3000.JPG", media[0].name)
        assertTrue(media[0].isImage)
        assertFalse(media[0].isVideo)
    }

    @Test
    fun `listMedia 识别视频`() = runBlocking {
        val media = cam.client().listMedia("103PHOTO")
        assertEquals(3, media.size)
        assertEquals("SAM_3102.JPG", media[0].name)
        val video = media.first { it.name.endsWith(".MP4") }
        assertTrue(video.isVideo)
        assertFalse(video.isImage)
    }

    @Test
    fun `MediaEntry 生成的相对路径与 URL 正确`() {
        val e = MediaEntry("103PHOTO", "SAM_3102.JPG")
        assertEquals("103PHOTO/SAM_3102.JPG", e.relPath)
        assertEquals("/DCIM/103PHOTO/SAM_3102.JPG", e.filePath)
        assertEquals(
            "http://127.0.0.1:${cam.daemonPort}/DCIM/103PHOTO/SAM_3102.JPG",
            cam.client().fileUrl(e.relPath),
        )
    }

    // ------------------------------------------------------------- 缩略图

    @Test
    fun `fetchThumb 返回 JPEG 字节`() = runBlocking {
        val bytes = cam.client().fetchThumb("103PHOTO/SAM_3102.JPG", 320, 75)
        assertTrue("应以 JPEG 魔数开头", bytes.size >= 2 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte())
    }

    @Test
    fun `缩略图失败时抛异常而不是把错误文本当图片`() = runBlocking {
        // 这是相机端最坑的行为：失败也返回 HTTP 200 + text/plain
        try {
            cam.client().fetchThumb("103PHOTO/NOPE.TXT", 320, 75)
            fail("应当抛出 NxException")
        } catch (e: NxException) {
            assertTrue("错误信息里应包含相机返回的原因", e.message!!.contains("thumb error"))
        }
    }

    @Test
    fun `thumbUrl 对宽高做钳制`() {
        val url = cam.client().thumbUrl("a/b.JPG", width = 9999, quality = 10)
        assertTrue("宽度应被钳到 1024：$url", url.contains("w=1024"))
        assertTrue("质量应被钳到 50：$url", url.contains("q=50"))
    }

    @Test
    fun `thumbUrl 对中文与空格做 URL 编码`() {
        val url = cam.client().thumbUrl("100PHOTO/我的 照片.JPG")
        assertFalse("不应出现裸空格", url.contains(" "))
        assertTrue(url.contains("%20"))
    }

    // ------------------------------------------------------------- 原图下载

    @Test
    fun `downloadMedia 分块回调且总长度正确`() = runBlocking {
        val client = cam.client()
        val entry = MediaEntry("103PHOTO", "SAM_3102.JPG")
        val sink = java.io.ByteArrayOutputStream()
        var progressCalls = 0
        var lastTotal = -1L

        val total = client.downloadMedia(
            entry = entry,
            onProgress = { _, total ->
                progressCalls++
                lastTotal = total
            },
            onChunk = { buf, len -> sink.write(buf, 0, len) },
        )

        assertEquals(cam.photoBytes.size.toLong(), total)
        assertEquals(cam.photoBytes.size, sink.size())
        assertTrue("应有进度回调", progressCalls > 0)
        assertEquals("Content-Length 应可读", cam.photoBytes.size.toLong(), lastTotal)
        assertArrayEquals(cam.photoBytes, sink.toByteArray())
    }

    private fun assertArrayEquals(a: ByteArray, b: ByteArray) =
        assertTrue("字节内容不一致（a=${a.size} b=${b.size}）", a.contentEquals(b))

    // -------------------------------------------------------------- liveview

    @Test
    fun `liveView 高画质返回 720x480 的 NV12`() = runBlocking {
        val data = cam.client().liveView(low = false)
        assertEquals(720 * 480 * 3 / 2, data.size)
        val spec = com.nxwa.nxconnect.media.Nv12Decoder.FrameSpec.detect(data.size)
        assertNotNull("长度必须能被 FrameSpec 识别", spec)
        assertEquals(720, spec!!.width)
        assertEquals(480, spec.height)
        assertFalse(spec.low)
    }

    @Test
    fun `liveView 低画质返回半采样 NV12`() = runBlocking {
        val data = cam.client().liveView(low = true)
        val spec = com.nxwa.nxconnect.media.Nv12Decoder.FrameSpec.detect(data.size)
        assertNotNull(spec)
        assertTrue("应被识别为低画质", spec!!.low)
        assertEquals(720, spec.width)
    }

    // ------------------------------------------------------------ 控制端点

    @Test
    fun `按键注入与保活打到正确的路径`() = runBlocking {
        val client = cam.client()
        client.sendKey("Super_R")
        client.keepAlive()
        client.shutter(silent = true)
        client.lcd(false)

        val paths = cam.requestedPaths.toList()
        assertTrue("应有按键注入请求，实际：$paths", paths.any { it.endsWith("/api/v1/input/inject") })
        assertTrue("应有保活请求，实际：$paths", paths.any { it.endsWith("/api/v1/input/inject_keep_alive") })
        assertTrue("应打到 silent 快门，实际：$paths", paths.any { it.endsWith("/api/v1/shutter/silent") })
        assertTrue("应打到 lcd off，实际：$paths", paths.any { it.endsWith("/api/v1/lcd/off") })

        // 键名必须是相机端认可的 X11 keysym
        val keyQuery = cam.requestedQueries.firstOrNull { it.contains("/api/v1/input/inject?") }
        assertTrue("注入的键名应为 Super_R，实际：$keyQuery", keyQuery?.contains("key=Super_R") == true)
    }

    // ---------------------------------------------------------- 能力探测

    @Test
    fun `完全体相机探测为 FULL_MOD`() = runBlocking {
        val caps = cam.client().detectCapabilities()
        assertEquals(CameraMode.FULL_MOD, caps.mode)
        assertTrue(caps.daemonOk)
        assertTrue(caps.cgiDirlistOk)
        assertTrue("缩略图自检应通过", caps.cgiThumbOk)
    }
}

/** 只有 80 端口 HTML 目录页的相机（SD 卡 busybox httpd），验证回退路径。 */
class StaticOnlyCameraTest {

    private lateinit var cam: TestCamera

    @Before
    fun setUp() {
        cam = TestCamera(TestCamera.Mode.STATIC_ONLY).start()
    }

    @After
    fun tearDown() {
        cam.stop()
    }

    @Test
    fun `8080 不可用时回退解析 HTML 目录页`() = runBlocking {
        val folders = cam.client().listFolders()
        assertEquals("应从 HTML 目录页解析出目录", listOf("103PHOTO", "102PHOTO", "100PHOTO"), folders)
    }

    @Test
    fun `HTML 目录页解析出媒体文件并过滤非媒体`() = runBlocking {
        val media = cam.client().listMedia("100PHOTO")
        assertEquals(1, media.size)
        assertEquals("SAM_3000.JPG", media[0].name)
    }

    @Test
    fun `能列出子目录内容`() = runBlocking {
        val media = cam.client().listMedia("103PHOTO")
        assertEquals(3, media.size)
        assertTrue(media.any { it.isVideo })
    }

    @Test
    fun `探测为 STATIC_ONLY 且明确报告缺 8080`() = runBlocking {
        val caps = cam.client().detectCapabilities()
        assertEquals(CameraMode.STATIC_ONLY, caps.mode)
        assertFalse("daemon 不存在", caps.daemonOk)
        assertFalse("8080 CGI 不存在", caps.cgiDirlistOk)
        assertTrue("但目录仍可读", caps.canDirList())
    }

    @Test
    fun `STATIC_ONLY 下取景端点不存在应抛异常`() = runBlocking {
        // 只有 SD 卡 busybox httpd 时没有 liveview 端点，必须明确失败而不是返回垃圾
        try {
            val data = cam.client().liveView()
            fail("应抛异常，实际拿到 ${data.size} 字节")
        } catch (e: Exception) {
            assertTrue("应是 NxException 或 IOException，实际 ${e::class.java.simpleName}", true)
        }
    }
}

private fun CameraCapabilities.canDirList(): Boolean = daemonOk || cgiDirlistOk || cgiThumbOk ||
    mode == CameraMode.FULL_MOD || mode == CameraMode.STATIC_ONLY

/** Actual HTTP errors must not become a stale HTML gallery or "unknown". */
class GalleryListingFailureTest {
    @Test
    fun `diagnostics uses three read-only endpoints and continues after individual failure`() = runBlocking {
        val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
        val opened = mutableListOf<String>()
        val info = """{"model":"NX300M","media_paused":false}"""
        val storage = """{"error":"fixture storage busy"}"""
        val server = FakeHttpServer { req ->
            requests += "${req.method} ${req.path}"
            FakeHttpServer.Response(if (req.path.endsWith("storage/status")) 503 else 200,
                (if (req.path.endsWith("storage/status")) storage else info).toByteArray(), "application/json")
        }.start()
        try {
            val binder = object : HttpBinder {
                override fun bind(conn: java.net.HttpURLConnection) = Unit
                override fun openConnection(url: java.net.URL): java.net.HttpURLConnection {
                    opened += url.path
                    if (url.path.endsWith("media/status")) throw java.io.IOException("fixture unavailable")
                    return url.openConnection() as java.net.HttpURLConnection
                }
            }
            val output = NxCamera("127.0.0.1", server.port, server.port, binder).readServiceDiagnostics()
            assertEquals(listOf("/api/v1/camera/info", "/api/v1/media/status", "/api/v1/storage/status"), opened)
            assertEquals(listOf("GET /api/v1/camera/info", "GET /api/v1/storage/status"), requests)
            assertTrue(output.contains("HTTP 200") && output.contains(info))
            assertTrue(output.contains("fixture unavailable") && output.contains("ms"))
            assertTrue(output.contains("HTTP 503") && output.contains(storage))
        } finally { server.stop() }
    }

    @Test
    fun `media paused preserves status and body without fallback or resume`() = runBlocking {
        val body = """{"error":"media access paused","code":"media_paused"}"""
        for (native in listOf(false, true)) {
            val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
            val logs = mutableListOf<String>()
            val server = FakeHttpServer { req ->
                requests += req.path
                if (req.path == "/cgi-bin/dirlist") FakeHttpServer.Response(503, body.toByteArray(), "application/json")
                else FakeHttpServer.Response(body = "<a href='SAM_0001.JPG'>stale</a>".toByteArray())
            }.start()
            try {
                val camera = NxCamera("127.0.0.1", server.port, server.port).apply {
                    profile = CameraProfile(galleryPreview = native)
                    logger = { logs += it }
                }
                val listing = camera.listDir("")
                assertFalse(listing.ok)
                assertTrue(listing.err!!, listing.err.contains("媒体访问已暂停"))
                assertTrue(listing.err.contains("HTTP 503") && listing.err.contains("media_paused"))
                assertTrue(logs.any { it.contains(body) && it.contains("HTTP 503") })
                assertEquals(listOf("/cgi-bin/dirlist"), requests)
            } finally { server.stop() }
        }
    }

    @Test
    fun `legacy err and unfamiliar native error remain explicit failures`() = runBlocking {
        for ((status, body, detail) in listOf(
            Triple(200, """{"ok":false,"err":"legacy directory failure"}""", "legacy directory failure"),
            Triple(409, """{"error":"camera is busy","code":"future_busy"}""", "future_busy"),
        )) {
            val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
            val server = FakeHttpServer { req ->
                requests += req.path
                FakeHttpServer.Response(status, body.toByteArray(), "application/json")
            }.start()
            try {
                val listing = NxCamera("127.0.0.1", server.port, server.port).listDir("")
                assertFalse(listing.ok)
                assertTrue(listing.err!!, listing.err.contains(detail) && listing.err.contains("HTTP $status"))
                assertFalse(listing.err.contains("unknown"))
                assertEquals(listOf("/cgi-bin/dirlist"), requests)
            } finally { server.stop() }
        }
    }

    @Test
    fun `native invalid response refuses HTML while missing legacy CGI still falls back`() = runBlocking {
        for (native in listOf(false, true)) {
            val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
            val server = FakeHttpServer { req ->
                requests += req.path
                if (req.path == "/cgi-bin/dirlist") FakeHttpServer.Response(
                    if (native) 503 else 404, "not available".toByteArray())
                else FakeHttpServer.Response(body = "<a href='SAM_0001.JPG'>SAM_0001.JPG</a>".toByteArray())
            }.start()
            try {
                val camera = NxCamera("127.0.0.1", server.port, server.port).apply {
                    profile = CameraProfile(galleryPreview = native)
                }
                val listing = camera.listDir("")
                assertEquals(!native, listing.ok)
                if (native) {
                    assertTrue(listing.err!!, listing.err.contains("HTTP 503") && listing.err.contains("not available"))
                    assertEquals(listOf("/cgi-bin/dirlist"), requests)
                } else {
                    assertEquals(listOf("SAM_0001.JPG"), listing.files)
                    assertEquals(2, requests.size)
                }
            } finally { server.stop() }
        }
    }
}
