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

import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.util.Collections
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * 测试用迷你 HTTP 服务器。
 *
 * 为什么不用 `com.sun.net.httpserver`：AGP 的 Kotlin 编译里拿不到该 JDK 模块
 * （Unresolved reference 'sun'），而引第三方 mock 库又要多一个依赖。
 * 相机端只用 GET + Content-Length，所以 30 行 socket 代码足够了，而且行为完全可控。
 */
class FakeHttpServer(private val handler: (Request) -> Response) {

    class Request(
        val method: String,
        val path: String,
        val query: Map<String, String>,
        val headers: Map<String, String>,
        val port: Int,
    )

    class Response(
        val code: Int = 200,
        val body: ByteArray = ByteArray(0),
        val contentType: String = "text/plain; charset=utf-8",
        val headers: Map<String, String> = emptyMap(),
    )

    private var socket: ServerSocket? = null
    private val pool = Executors.newFixedThreadPool(4)

    val port: Int get() = socket?.localPort ?: 0

    fun start(): FakeHttpServer {
        val s = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        socket = s
        thread(isDaemon = true, name = "fake-http-$port") {
            while (!s.isClosed) {
                val client = try {
                    s.accept()
                } catch (e: Exception) {
                    break
                }
                pool.execute { handleClient(client) }
            }
        }
        return this
    }

    fun stop() {
        try {
            socket?.close()
        } catch (e: Exception) {
            // 忽略
        }
        pool.shutdownNow()
    }

    private fun handleClient(client: Socket) {
        var stage = "accept"
        try {
            client.use { c ->
                c.soTimeout = 15_000
                val input = c.getInputStream()
                stage = "readLine"
                val requestLine = readLine(input) ?: run {
                    diag("requestLine=NULL (对端直接关闭)")
                    return
                }
                val parts = requestLine.split(' ')
                if (parts.size < 2) {
                    diag("requestLine 畸形: [$requestLine]")
                    return
                }
                val method = parts[0]
                val rawTarget = parts[1]

                stage = "readHeaders"
                val headers = mutableMapOf<String, String>()
                while (true) {
                    val line = readLine(input) ?: break
                    if (line.isEmpty()) break
                    val i = line.indexOf(':')
                    if (i > 0) headers[line.substring(0, i).trim().lowercase()] = line.substring(i + 1).trim()
                }

                val qIndex = rawTarget.indexOf('?')
                val path = if (qIndex >= 0) rawTarget.substring(0, qIndex) else rawTarget
                val queryString = if (qIndex >= 0) rawTarget.substring(qIndex + 1) else ""
                val query = queryString.split('&').filter { it.isNotEmpty() }.associate {
                    val eq = it.indexOf('=')
                    if (eq < 0) decode(it) to "" else decode(it.substring(0, eq)) to decode(it.substring(eq + 1))
                }

                stage = "handler"
                val resp = handler(Request(method, path, query, headers, c.localPort))
                stage = "writeResponse(size=${resp.body.size})"
                writeResponse(c, resp)
                stage = "done"
            }
        } catch (e: Exception) {
            diag("异常 @$stage: ${e::class.java.simpleName}: ${e.message}")
        }
    }

    private fun diag(msg: String) {
        try {
            val f = java.io.File("C:\\DsNX\\_host\\fakeserver-diag.log")
            f.parentFile?.mkdirs()
            f.appendText("[$port] $msg\n")
        } catch (e: Exception) {
            // 诊断写入失败不影响测试
        }
    }

    private fun decode(s: String): String = try {
        URLDecoder.decode(s, "UTF-8")
    } catch (e: Exception) {
        s
    }

    private fun readLine(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        var b = input.read()
        if (b < 0) return null
        while (b >= 0 && b != '\n'.code) {
            if (b != '\r'.code) buf.write(b)
            b = input.read()
        }
        return buf.toString("UTF-8")
    }

    private fun writeResponse(c: Socket, resp: Response) {
        val out = BufferedOutputStream(c.getOutputStream())
        val head = StringBuilder()
        head.append("HTTP/1.1 ").append(resp.code).append(' ').append(reason(resp.code)).append("\r\n")
        head.append("Content-Type: ").append(resp.contentType).append("\r\n")
        head.append("Content-Length: ").append(resp.body.size).append("\r\n")
        head.append("Access-Control-Allow-Origin: *\r\n")
        head.append("Connection: close\r\n")
        resp.headers.forEach { (k, v) -> head.append(k).append(": ").append(v).append("\r\n") }
        head.append("\r\n")
        out.write(head.toString().toByteArray(Charsets.ISO_8859_1))
        out.write(resp.body)
        out.flush()
    }

    private fun reason(code: Int) = when (code) {
        200 -> "OK"
        404 -> "Not Found"
        500 -> "Internal Server Error"
        else -> "Status"
    }
}

/**
 * 模拟 NX500 相机端：控制器（daemon）+ 8080 CGI 两种角色。
 *
 * 三种形态对应真实世界的三种相机状态：
 *  - FULL_MOD   : 装了 nx-remote-controller-mod / NX-KS / NX-WA
 *  - STATIC_ONLY: 只有 SD 卡上 busybox httpd 提供的 HTML 目录页
 *  - STOCK      : 原厂固件，两个端口都没有可用服务
 *
 * 这是**测试替身**；被测对象是 app 里的真实 [NxCamera] 实现。
 */
class TestCamera(val mode: Mode = Mode.FULL_MOD) {

    enum class Mode { FULL_MOD, STATIC_ONLY, STOCK }

    companion object {
        /** 与相机端 thumb-cgi 一致的失败行为：HTTP 200 + text/plain */
        const val THUMB_ERROR_TEXT = "thumb error: source not found"

        /** 合法 JPEG 头部（SOI + APP0），用于验证魔数判据 */
        val FAKE_JPEG = byteArrayOf(
            0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte(), 0x00, 0x10,
            0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01,
            0x00, 0x00, 0xFF.toByte(), 0xD9.toByte(),
        )
    }

    private var daemon: FakeHttpServer? = null
    private var cgi: FakeHttpServer? = null

    val daemonPort: Int get() = daemon?.port ?: 0

    /**
     * 8080 端口。
     *
     * STATIC_ONLY 模式下不能简单用 daemonPort+1 —— 那可能正好撞上另一个测试实例
     * 的 server 端口（实测踩到过，表现为"取景居然返回了 518400 字节的高画质帧"）。
     * 这里改为先绑定再关闭拿一个确实空闲的端口。
     */
    val cgiPort: Int get() = cgi?.port ?: deadPort

    private val deadPort: Int by lazy {
        java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1")).use { it.localPort }
    }

    val folders = listOf("103PHOTO", "102PHOTO", "100PHOTO")
    val files = mapOf(
        "103PHOTO" to listOf("SAM_3101.JPG", "SAM_3102.JPG", "MOV_3100.MP4"),
        "102PHOTO" to listOf("SAM_3001.JPG"),
        "100PHOTO" to listOf("SAM_3000.JPG", "notes.txt"),
    )

    /** 记录收到的请求路径（不含 query），供断言 */
    val requestedPaths: MutableList<String> = Collections.synchronizedList(mutableListOf<String>())

    /** 记录收到的 query 原文，供断言 */
    val requestedQueries: MutableList<String> = Collections.synchronizedList(mutableListOf<String>())

    /** 原图字节（下载测试用） */
    var photoBytes: ByteArray = ByteArray(300_000) { (it % 251).toByte() }

    /** liveview 帧字节数（默认 720×480 高画质 = 518400） */
    var liveViewSize: Int = 720 * 480 * 3 / 2

    fun start(): TestCamera {
        val server = FakeHttpServer { req -> route("daemon", req) }.start()
        daemon = server
        if (mode == Mode.FULL_MOD) {
            cgi = FakeHttpServer { req -> route("cgi", req) }.start()
        }
        return this
    }

    fun stop() {
        daemon?.stop()
        cgi?.stop()
        daemon = null
        cgi = null
    }

    /** 被测客户端，端口指向本次测试的随机端口 */
    fun client() = NxCamera("127.0.0.1", daemonPort, cgiPort, HttpBinder.None)

    // ------------------------------------------------------------------ 路由

    private fun route(role: String, req: FakeHttpServer.Request): FakeHttpServer.Response {
        // 路径与 query 分开记录：把 query 拼进路径会让 endsWith 之类的断言失效（踩过）
        requestedPaths.add("$role${req.path}")
        requestedQueries.add("$role${req.path}?${req.query.entries.joinToString("&") { "${it.key}=${it.value}" }}")

        if (role == "cgi") return routeCgi(req)
        return routeDaemon(req)
    }

    private fun routeDaemon(req: FakeHttpServer.Request): FakeHttpServer.Response {
        if (mode == Mode.STOCK) return FakeHttpServer.Response(404, "not found".toByteArray())

        return when {
            req.path == "/api/v1/camera/info" && mode == Mode.FULL_MOD ->
                json("""{"model":"NX500","firmware":"1.12","mac":"02:30:0d:00:00:02"}""")

            req.path == "/api/v1/camera/status" && mode == Mode.FULL_MOD ->
                json("""{"batteryCharging":false,"batteryPercent":73,"mode":"P","drive":"single"}""")

            req.path == "/api/v1/liveview/get" && mode == Mode.FULL_MOD ->
                octet(frame(liveViewSize))

            req.path == "/api/v1/liveview/get_low" && mode == Mode.FULL_MOD ->
                octet(frame(720 * 480 / 4 * 3))

            // 只有装了 mod 才有这些控制端点；STATIC_ONLY 下必须 404
            req.path.startsWith("/api/v1/") && mode == Mode.FULL_MOD -> json("{}")

            req.path == "/DCIM" || req.path == "/DCIM/" ->
                html(htmlListing(""))

            req.path.startsWith("/DCIM/") -> {
                val rel = req.path.removePrefix("/DCIM/").trim('/')
                when {
                    files.containsKey(rel) -> html(htmlListing(rel))
                    rel.endsWith(".JPG") -> FakeHttpServer.Response(200, photoBytes, "image/jpeg")
                    else -> FakeHttpServer.Response(404, "not found".toByteArray())
                }
            }

            req.path == "/" ->
                html("<html><head><title>nx-remote-controller-mod</title></head></html>")

            else -> FakeHttpServer.Response(404, "not found".toByteArray())
        }
    }

    private fun routeCgi(req: FakeHttpServer.Request): FakeHttpServer.Response {
        return when (req.path) {
            "/cgi-bin/dirlist" -> {
                val p = req.query["p"].orEmpty().trim('/')
                when {
                    p.contains("..") -> json("""{"ok":false,"err":"invalid path"}""")
                    p.isEmpty() -> json(
                        """{"ok":true,"path":"","dirs":[${folders.joinToString(",") { "\"$it\"" }}],"files":[]}""",
                    )
                    else -> {
                        val f = files[p]
                        if (f == null) json("""{"ok":false,"err":"not found"}""")
                        else json(
                            """{"ok":true,"path":"$p","dirs":[],"files":[${f.joinToString(",") { "\"$it\"" }}]}""",
                        )
                    }
                }
            }

            "/cgi-bin/thumb" -> {
                val f = req.query["f"].orEmpty()
                // 相机端真实行为：生成失败也返回 200 + text/plain
                if (!f.endsWith(".JPG")) FakeHttpServer.Response(200, THUMB_ERROR_TEXT.toByteArray())
                else FakeHttpServer.Response(200, FAKE_JPEG, "image/jpeg")
            }

            "/cgi-bin/capdtm-api" -> json("""{"ok":true,"params":{"USERDATA_ISO":"ISO_100"}}""")
            else -> FakeHttpServer.Response(404, "not found".toByteArray())
        }
    }

    // ------------------------------------------------------------------ 工具

    private fun json(s: String) = FakeHttpServer.Response(200, s.toByteArray(), "application/json")
    private fun html(s: String) = FakeHttpServer.Response(200, s.toByteArray(), "text/html; charset=utf-8")
    private fun octet(b: ByteArray) = FakeHttpServer.Response(200, b, "application/octet-stream")

    /** mongoose / busybox 风格目录页 */
    private fun htmlListing(path: String): String {
        val sb = StringBuilder()
        sb.append("<html><head><title>Index of /DCIM/$path</title></head><body><pre>")
        if (path.isEmpty()) {
            folders.forEach { sb.append("<a href=\"/DCIM/$it/\">$it/</a>  dir\n") }
        } else {
            (files[path] ?: emptyList()).forEach {
                sb.append("<a href=\"/DCIM/$path/$it\">$it</a>  file\n")
            }
        }
        sb.append("</pre></body></html>")
        return sb.toString()
    }

    /** 造一帧可解码的 NV12（有梯度，保证解出彩色而非常量） */
    private fun frame(size: Int): ByteArray {
        val data = ByteArray(size)
        // Y 平面大小：高低画质的 Y 平面都是 w*h，UV 才是半采样
        val ySize = when (size) {
            720 * 480 * 3 / 2 -> 720 * 480        // 高画质
            720 * 480 / 4 * 3 -> 720 * 480        // 低画质
            else -> minOf(size, size * 2 / 3)
        }.coerceAtMost(size)

        for (i in 0 until ySize) data[i] = ((i / 7) % 200 + 20).toByte()

        var i = ySize
        while (i < size) {
            data[i] = (60 + (i / 2) % 150).toByte()
            // 末尾可能是奇数长度，必须逐字节判断，否则会写出界
            if (i + 1 < size) data[i + 1] = (200 - (i / 2) % 150).toByte()
            i += 2
        }
        return data
    }
}
