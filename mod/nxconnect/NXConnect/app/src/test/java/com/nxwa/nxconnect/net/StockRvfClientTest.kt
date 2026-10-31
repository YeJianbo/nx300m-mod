// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import org.junit.Assert.*
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class StockRvfClientTest {
    private val serviceType = "urn:schemas-upnp-org:service:ContentDirectory:3"

    private fun description(port: Int, base: String = "http://127.0.0.1:$port/rvf/service/",
                            control: String = "../control-dynamic"): String =
        """<root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>$base</URLBase><device><serviceList><service><serviceType>$serviceType</serviceType><controlURL>$control</controlURL><SCPDURL>./actions.xml?schema=2</SCPDURL></service></serviceList></device></root>"""

    private fun scpd(vararg actions: Pair<String, String?>): String =
        """<scpd xmlns="urn:schemas-upnp-org:service-1-0"><actionList>""" + actions.joinToString("") { (name, input) ->
            "<action><name>$name</name>" + (input?.let {
                "<argumentList><argument><name>$it</name><direction>in</direction></argument></argumentList>"
            } ?: "") + "</action>"
        } + "</actionList></scpd>"

    private fun envelope(action: String, contents: String = "") =
        """<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:${action}Response xmlns:u="$serviceType">$contents</u:${action}Response></s:Body></s:Envelope>"""

    private fun xml(value: String, status: Int = 200) = FakeHttpServer.Response(status, value.toByteArray(), "text/xml")

    @Test
    fun `等待机身确认后按描述的动态服务地址连接 不自动拍摄`() = runBlocking {
        val received = CountDownLatch(1)
        val allow = CountDownLatch(1)
        val requests = Collections.synchronizedList(mutableListOf<FakeHttpServer.Request>())
        val server = FakeHttpServer { request ->
            requests += request
            when (request.path) {
                "/smp_2_" -> {
                    received.countDown()
                    check(allow.await(5, TimeUnit.SECONDS))
                    xml(description(request.port))
                }
                "/rvf/service/actions.xml" -> {
                    check(request.query["schema"] == "2")
                    xml(scpd("GetInfomation" to "GPSINFO", "HeartBeat" to "Duration", "MULTIAF" to null, "Shot" to null))
                }
                "/rvf/control-dynamic" -> {
                    check(request.method == "POST")
                    when (request.headers["soapaction"]) {
                        "\"$serviceType#GetInfomation\"" -> xml(envelope("GetInfomation", "<StreamUrl><QualityHighUrl>http://127.0.0.1:${request.port}/live/high.avi</QualityHighUrl><QualityLowUrl>/live/low.avi</QualityLowUrl></StreamUrl>"))
                        "\"$serviceType#HeartBeat\"" -> xml(envelope("HeartBeat"))
                        "\"$serviceType#MULTIAF\"" -> xml(envelope("MULTIAF", "<AFSTATUS>focused</AFSTATUS>"))
                        "\"$serviceType#Shot\"" -> xml(envelope("Shot", "<AFSHOTRESULT>/photos/last.jpg</AFSHOTRESULT><AVAILSHOTS>124</AVAILSHOTS>"))
                        else -> error("unexpected SOAP action")
                    }
                }
                else -> FakeHttpServer.Response(404)
            }
        }.start()
        val client = StockRvfClient("127.0.0.1", "02:ab:12:34:56:78", server.port, pairingTimeoutMs = 5_000)
        try {
            val connecting = async { client.connect() }
            assertTrue(withContext(Dispatchers.IO) { received.await(3, TimeUnit.SECONDS) })
            assertFalse("描述GET必须等待机身确认", connecting.isCompleted)
            allow.countDown()
            val session = connecting.await()
            assertEquals(serviceType, session.serviceType)
            assertEquals("http://127.0.0.1:${server.port}/rvf/control-dynamic", session.controlUrl)
            assertEquals("http://127.0.0.1:${server.port}/rvf/service/actions.xml?schema=2", session.scpdUrl)
            assertEquals("http://127.0.0.1:${server.port}/live/high.avi", session.highQualityUrl)
            assertEquals("http://127.0.0.1:${server.port}/live/low.avi", session.lowQualityUrl)
            assertTrue(session.supportedActions.containsAll(listOf("GetInfomation", "HeartBeat", "MULTIAF", "Shot")))
            assertEquals(1, requests.count { it.method == "POST" })
            assertTrue(requests.all { it.headers["user-agent"] == "SEC_RVF_02:ab:12:34:56:78" })
            assertTrue(requests.all { it.headers["access-method"] == "manual" })
            client.heartbeat(30)
            assertEquals("focused", client.focus())
            val shot = client.shot()
            assertEquals("http://127.0.0.1:${server.port}/photos/last.jpg", shot.photoUrl)
            assertEquals(124, shot.availableShots)
        } finally {
            allow.countDown()
            client.close()
            server.stop()
        }
    }

    @Test
    fun `跨主机URLBase和控制地址在发送后续请求前拒绝`() = runBlocking {
        for (outsideBase in listOf(true, false)) {
            var requestCount = 0
            val server = FakeHttpServer { request ->
                requestCount++
                xml(if (outsideBase) description(request.port, base = "http://localhost:${request.port}/")
                    else description(request.port, control = "http://localhost:${request.port}/foreign"))
            }.start()
            val client = StockRvfClient("127.0.0.1", "fixed-client", server.port)
            try {
                try {
                    client.connect()
                    fail("不能跟随跨主机原厂服务URL")
                } catch (expected: StockRvfClient.ProtocolException) {
                    assertTrue(expected.message.orEmpty().contains("非本相机"))
                    assertEquals(1, requestCount)
                }
            } finally {
                client.close()
                server.stop()
            }
        }
    }

    @Test
    fun `SOAP拒绝返回原厂错误码与说明 不误报已连接`() = runBlocking {
        val server = FakeHttpServer { request ->
            when (request.path) {
                "/smp_2_" -> xml(description(request.port))
                "/rvf/service/actions.xml" -> xml(scpd("GetInfomation" to null))
                else -> xml("""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail><UPnPError><errorCode>606</errorCode><errorDescription>Action not authorized</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>""", 500)
            }
        }.start()
        val client = StockRvfClient("127.0.0.1", "fixed-client", server.port)
        try {
            try {
                client.connect()
                fail("SOAP fault不能当作会话成功")
            } catch (expected: StockRvfClient.ProtocolException) {
                assertEquals(500, expected.httpStatus)
                assertEquals("606", expected.faultCode)
                assertTrue(expected.message.orEmpty().contains("Action not authorized"))
            }
        } finally {
            client.close()
            server.stop()
        }
    }

    @Test
    fun `取消会结束正在等待人工确认的HTTP而不是等到60秒超时`() = runBlocking {
        val received = CountDownLatch(1)
        val release = CountDownLatch(1)
        val server = FakeHttpServer { request ->
            received.countDown()
            release.await(5, TimeUnit.SECONDS)
            xml(description(request.port))
        }.start()
        val client = StockRvfClient("127.0.0.1", "fixed-client", server.port)
        try {
            val connecting = async { client.connect() }
            assertTrue(withContext(Dispatchers.IO) { received.await(3, TimeUnit.SECONDS) })
            val cancelStarted = System.nanoTime()
            client.cancel()
            try {
                connecting.await()
                fail("取消后不应继续连接")
            } catch (_: CancellationException) {
                assertTrue(connecting.isCancelled)
            }
            assertTrue("取消必须关闭等待中的HTTP", TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - cancelStarted) < 2_000)
        } finally {
            release.countDown()
            client.close()
            server.stop()
        }
    }

    @Test
    fun `设备描述中的DTD和外部实体不会被解析或请求`() = runBlocking {
        var requests = 0
        val server = FakeHttpServer { request ->
            requests++
            xml("""<!DOCTYPE root [<!ENTITY external SYSTEM "http://127.0.0.1:${request.port}/private">]><root><URLBase>&external;</URLBase></root>""")
        }.start()
        val client = StockRvfClient("127.0.0.1", "fixed-client", server.port)
        try {
            try {
                client.connect()
                fail("DTD应被拒绝")
            } catch (expected: StockRvfClient.ProtocolException) {
                assertTrue(expected.message.orEmpty().contains("DTD"))
                assertEquals(1, requests)
            }
        } finally {
            client.close()
            server.stop()
        }
    }

    @Test
    fun `长时间Shot等待期间保活不会被动作锁阻塞`() = runBlocking {
        val shotReceived = CountDownLatch(1)
        val releaseShot = CountDownLatch(1)
        val heartbeatReceived = CountDownLatch(1)
        val server = FakeHttpServer { request ->
            when (request.path) {
                "/smp_2_" -> xml(description(request.port))
                "/rvf/service/actions.xml" -> xml(scpd("GetInfomation" to null, "Shot" to null, "HeartBeat" to "Duration"))
                else -> when (request.headers["soapaction"]) {
                    "\"$serviceType#GetInfomation\"" -> xml(envelope("GetInfomation"))
                    "\"$serviceType#Shot\"" -> {
                        shotReceived.countDown()
                        check(releaseShot.await(5, TimeUnit.SECONDS))
                        xml(envelope("Shot"))
                    }
                    "\"$serviceType#HeartBeat\"" -> {
                        heartbeatReceived.countDown()
                        xml(envelope("HeartBeat"))
                    }
                    else -> error("unexpected SOAP action")
                }
            }
        }.start()
        val client = StockRvfClient("127.0.0.1", "fixed-client", server.port)
        try {
            client.connect()
            val shooting = async { client.shot() }
            assertTrue(withContext(Dispatchers.IO) { shotReceived.await(3, TimeUnit.SECONDS) })
            val heartbeat = async { client.heartbeat() }
            assertTrue("Shot等待时仍应发送HeartBeat", withContext(Dispatchers.IO) {
                heartbeatReceived.await(2, TimeUnit.SECONDS)
            })
            heartbeat.await()
            assertFalse(shooting.isCompleted)
            client.close()
            try { shooting.await(); fail("关闭会话必须取消未完成Shot") }
            catch (_: CancellationException) { assertTrue(shooting.isCancelled) }
        } finally {
            releaseShot.countDown()
            client.close()
            server.stop()
        }
    }
}
