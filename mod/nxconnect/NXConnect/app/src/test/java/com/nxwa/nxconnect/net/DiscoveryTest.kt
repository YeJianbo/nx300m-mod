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

import com.nxwa.nxconnect.data.MiniJson
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 相机发现相关测试。
 *
 * 用到的报文与 JSON 都是**真机抓回来的原文**（NX500 / 1.12 / 192.168.5.39），
 * 不是照着文档编的。
 */
class NxBeaconParserTest {

    /** 真机 `/api/v1/camera/status` 的 cameras 字段原文 */
    private val realStatusJson = """{"battery_charging":false,"battery_percent":-1,""" +
        """"battery_level":3,"drive":"UNKNOWN","mode":"UNKNOWN","hevc":"off",""" +
        """"cameras":[{"ip":"192.168.5.39","packet":"NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|"}]}"""

    private val realPacket = "NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|"

    @Test
    fun `解析真机广播报文`() {
        val b = NxBeaconParser.parse("192.168.5.39", realPacket)
        assertEquals("192.168.5.39", b.ip)
        assertEquals("0.9", b.protocolVersion)
        assertEquals("NX500", b.model)
        assertEquals("1.12", b.firmware)
        assertEquals("02:30:0d:00:00:01", b.mac)
    }

    @Test
    fun `报文尾部的 NUL 补齐被清掉`() {
        val raw = ByteArray(64)
        realPacket.toByteArray().copyInto(raw)
        val text = NxBeaconParser.cleanPacket(raw, 0, 64)
        assertFalse("不应残留 NUL", text.contains('\u0000'))
        assertEquals(realPacket, text)
        assertTrue(NxBeaconParser.looksLikeBeacon(text))
    }

    @Test
    fun `从 status 的 cameras 字段取出相机`() {
        val beacons = NxBeaconParser.parseCamerasField(realStatusJson)
        assertEquals(1, beacons.size)
        assertEquals("192.168.5.39", beacons[0].ip)
        assertEquals("NX500", beacons[0].model)
        assertEquals("02:30:0d:00:00:01", beacons[0].mac)
    }

    @Test
    fun `cameras 为空或缺字段时不崩`() {
        assertTrue(NxBeaconParser.parseCamerasField("""{"cameras":[]}""").isEmpty())
        assertTrue(NxBeaconParser.parseCamerasField("""{}""").isEmpty())
        assertTrue(NxBeaconParser.parseCamerasField("not json").isEmpty())
    }

    @Test
    fun `packet 缺失时仍能拿到 ip`() {
        val beacons = NxBeaconParser.parseCamerasField("""{"cameras":[{"ip":"1.2.3.4"}]}""")
        assertEquals(1, beacons.size)
        assertEquals("1.2.3.4", beacons[0].ip)
        assertEquals(null, beacons[0].model)
    }

    @Test
    fun `非 NX 报文不会被误判`() {
        assertFalse(NxBeaconParser.looksLikeBeacon("SOME_OTHER|1|2|3|"))
        assertFalse(NxBeaconParser.looksLikeBeacon(""))
    }

    @Test
    fun `字段缺失的报文解析为 null 而不是空串`() {
        val b = NxBeaconParser.parse("10.0.0.5", "NX_REMOTE|0.9|||")
        assertEquals(null, b.model)
        assertEquals(null, b.firmware)
        assertEquals(null, b.mac)
    }
}

/** 候选地址与探测流程测试。 */
class CameraDiscoveryTest {

    @Test
    fun `相机接路由器时 同网段候选 必须覆盖 DHCP 池里的随机地址`() {
        // 真机：PC 192.168.5.36，网关 192.168.5.1，相机 192.168.5.39
        val cands = CameraDiscovery.candidatesForTest(
            localIpv4 = "192.168.5.36",
            gateway = "192.168.5.1",
        )
        assertTrue("必须包含网关，实际：$cands", cands.contains("192.168.5.1"))
        assertTrue("必须包含真机实测地址 .39，实际：$cands", cands.contains("192.168.5.39"))
        assertTrue("应包含 .105，实际：$cands", cands.contains("192.168.5.105"))
        assertFalse("第一轮不应混入别的网段（会白等超时），实际：$cands", cands.any { it.startsWith("192.168.43.") })
        assertFalse("第一轮不应混入别的网段，实际：$cands", cands.any { it.startsWith("192.168.0.") })
    }

    @Test
    fun `历史地址只作为第二轮兜底且不与第一轮重复`() {
        val first = CameraDiscovery.candidatesForTest("192.168.5.36", "192.168.5.1")
        val second = CameraDiscovery.fallbackCandidatesForTest("192.168.5.36", "192.168.5.1")
        assertTrue("兜底应含文档记录的 192.168.0.105，实际：$second", second.contains("192.168.0.105"))
        assertTrue("兜底应含 192.168.43.90，实际：$second", second.contains("192.168.43.90"))
        assertTrue("两轮不应重叠", second.none { it in first })
    }

    @Test
    fun `相机做热点时覆盖 43 网段常见主机位`() {
        val cands = CameraDiscovery.candidatesForTest(localIpv4 = "192.168.43.85", gateway = "192.168.43.1")
        assertTrue(cands.contains("192.168.43.1"))
        assertTrue(cands.contains("192.168.43.90"))
        assertTrue(cands.contains("192.168.43.100"))
    }

    @Test
    fun `没有本机地址时第一轮退化到已知网段列表`() {
        val cands = CameraDiscovery.candidatesForTest(localIpv4 = null, gateway = null)
        assertTrue("应覆盖相机做热点的 43 网段，实际：$cands", cands.any { it.startsWith("192.168.43.") })
        assertTrue("应覆盖 192.168.1.x，实际：$cands", cands.any { it.startsWith("192.168.1.") })
        assertTrue(cands.isNotEmpty())

        // 历史地址仍由第二轮兜底，保证一个都不漏
        val fallback = CameraDiscovery.fallbackCandidatesForTest(localIpv4 = null, gateway = null)
        assertTrue("兜底应含 192.168.0.105，实际：$fallback", fallback.contains("192.168.0.105"))
    }

    @Test
    fun `候选列表不含重复项且都是合法 IPv4`() {
        val cands = CameraDiscovery.candidatesForTest("10.0.0.7", "10.0.0.1")
        assertEquals("不应有重复", cands.size, cands.distinct().size)
        assertTrue(cands.all { it.count { c -> c == '.' } == 3 })
    }
}

/** 用假相机验证"探测"这条链路本身（含三种形态的判定）。 */
class DiscoveryAgainstFakeCameraTest {

    private lateinit var cam: TestCamera

    @Before
    fun setUp() {
        cam = TestCamera(TestCamera.Mode.FULL_MOD).start()
    }

    @After
    fun tearDown() {
        cam.stop()
    }

    @Test
    fun `探测到完整相机时返回 Found 并带上机型固件`() = runBlocking {
        val r = CameraDiscovery.verify("127.0.0.1", cam.daemonPort)
        assertTrue("应识别为已装 mod 的相机，实际：$r", r is CameraDiscovery.Result.Found)
        val found = r as CameraDiscovery.Result.Found
        assertEquals("127.0.0.1", found.ip)
        assertEquals("NX500", found.info.model)
        assertEquals("1.12", found.info.firmware)
        assertEquals(cam.daemonPort, found.daemonPort)
        assertEquals(NxCamera.CGI_PORT, found.cgiPort)
    }

    @Test
    fun `原生服务发现结果保留实际端口和fw_ver供连接相册使用`() = runBlocking {
        val native = FakeHttpServer { request ->
            if (request.path == "/api/v1/camera/info") {
                FakeHttpServer.Response(body = """{"model":"NX300M","fw_ver":"1.15","capabilities":{"gallery_preview":true}}""".toByteArray(),
                    contentType = "application/json")
            } else FakeHttpServer.Response(404)
        }.start()
        try {
            val found = CameraDiscovery.verify("127.0.0.1", native.port) as CameraDiscovery.Result.Found
            assertEquals("NX300M", found.info.model)
            assertEquals("1.15", found.info.firmware)
            assertEquals(native.port, found.daemonPort)
            assertEquals(native.port, found.cgiPort)
        } finally {
            native.stop()
        }
    }

    @Test
    fun `端口上只有普通 HTTP 服务时给出可读提示`() = runBlocking {
        val staticCam = TestCamera(TestCamera.Mode.STATIC_ONLY).start()
        try {
            val r = CameraDiscovery.verify("127.0.0.1", staticCam.daemonPort)
            assertTrue("应报告『有 HTTP 但不是 daemon』，实际：$r", r is CameraDiscovery.Result.CameraWithoutDaemon)
            assertTrue("提示应可读", (r as CameraDiscovery.Result.CameraWithoutDaemon).hint.isNotBlank())
        } finally {
            staticCam.stop()
        }
    }

    @Test
    fun `不存在的端口返回 NotFound 且原因可读`() = runBlocking {
        val dead = java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1")).use { it.localPort }
        val r = CameraDiscovery.verify("127.0.0.1", dead)
        assertTrue("应是 NotFound，实际：$r", r is CameraDiscovery.Result.NotFound)
        assertTrue("原因应非空", (r as CameraDiscovery.Result.NotFound).reason.isNotBlank())
    }

    @Test
    fun `camera info 真实响应能被解析成机型固件 MAC`() {
        // 固化真机响应，避免以后手滑改错字段名
        val o = MiniJson.parseObject("""{"model":"NX500","fw_ver":"1.12","mac_address":"02:30:0d:00:00:01"}""")
        assertEquals("NX500", MiniJson.strAny(o, "model"))
        assertEquals("1.12", MiniJson.strAny(o, "fw_ver", "firmware"))
        assertEquals("02:30:0d:00:00:01", MiniJson.strAny(o, "mac_address", "mac"))
    }
}
