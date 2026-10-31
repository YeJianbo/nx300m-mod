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

import kotlinx.coroutines.runBlocking
import org.junit.Test

/** 诊断：对比“原始 socket”与“真实客户端”两条路径看到的东西。 */
class FakeServerDiagnosticTest {

    @Test
    fun dumpRawResponses() {
        val cam = TestCamera(TestCamera.Mode.FULL_MOD).start()
        try {
            for (path in listOf("/api/v1/camera/info", "/api/v1/camera/status", "/api/v1/input/inject_keep_alive")) {
                println("========== 原始 socket: $path ==========")
                println(rawGet(cam.daemonPort, path).take(400))
            }

            println("========== 真实客户端: info() ==========")
            val info = runBlocking { cam.client().info() }
            println("model=${info.model} firmware=${info.firmware} mac=${info.mac}")
            println("raw=[${info.raw}]")

            println("========== 真实客户端: status() ==========")
            val st = runBlocking { cam.client().status() }
            println("battery=${st.batteryPercent} mode=${st.mode} drive=${st.drive} raw=[${st.raw}]")

            println("========== 真实客户端: NxHttp.getText 直取 ==========")
            val text = runBlocking {
                NxHttp.getText("http://127.0.0.1:${cam.daemonPort}/api/v1/camera/info", 3000)
            }
            println("getText=[$text] len=${text.length}")

            println("========== 真实客户端: NxHttp.getBytes 直取 ==========")
            val bin = runBlocking {
                NxHttp.getBytes("http://127.0.0.1:${cam.daemonPort}/api/v1/camera/info", 3000)
            }
            println("getBytes code=${bin.code} ctype=${bin.contentType} len=${bin.bytes.size} body=[${String(bin.bytes)}]")
        } finally {
            cam.stop()
        }
    }

    @Test
    fun dumpStaticOnlyBehaviour() {
        val cam = TestCamera(TestCamera.Mode.STATIC_ONLY).start()
        try {
            println("=== STATIC_ONLY: mode=${cam.mode} daemon=${cam.daemonPort} cgi=${cam.cgiPort} ===")
            println("原始 socket /api/v1/camera/info -> " + rawGet(cam.daemonPort, "/api/v1/camera/info").take(200))
            println("原始 socket /api/v1/liveview/get -> " + rawGet(cam.daemonPort, "/api/v1/liveview/get").take(200))

            val client = cam.client()
            println("client baseUrl=${client.baseUrl} cgiUrl=${client.cgiUrl}")

            val info = runBlocking {
                try {
                    client.info().toString()
                } catch (e: Exception) {
                    "抛异常 ${e::class.java.simpleName}: ${e.message}"
                }
            }
            println("客户端 info() -> $info")

            val lv = runBlocking {
                try {
                    "size=${client.liveView().size}"
                } catch (e: Exception) {
                    "抛异常 ${e::class.java.simpleName}: ${e.message}"
                }
            }
            println("客户端 liveView() -> $lv")

            val caps = runBlocking { client.detectCapabilities() }
            println("客户端 detectCapabilities -> mode=${caps.mode} daemon=${caps.daemonOk} cgi=${caps.cgiDirlistOk} thumb=${caps.cgiThumbOk}")

            println("请求记录：")
            cam.requestedPaths.forEach { println("   $it") }
        } finally {
            cam.stop()
        }
    }

    private fun rawGet(port: Int, path: String): String {
        return try {
            java.net.Socket("127.0.0.1", port).use { s ->
                s.soTimeout = 8000
                s.getOutputStream().write(
                    ("GET $path HTTP/1.1\r\nHost: 127.0.0.1:$port\r\nAccept: */*\r\nConnection: close\r\n\r\n")
                        .toByteArray(),
                )
                s.getOutputStream().flush()
                String(s.getInputStream().readBytes(), Charsets.UTF_8)
            }
        } catch (e: Exception) {
            "!! ${e::class.java.simpleName}: ${e.message}"
        }
    }
}

