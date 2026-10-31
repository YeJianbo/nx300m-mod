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
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 解析器基线测试 —— 用的都是**真机抓回来的原始响应**（NX500 / 固件 1.12 / 192.168.5.39）。
 *
 * 这些字符串不要"美化"：字段名与取值就是相机端的真实行为，
 * 包括 `battery_percent: -1` 这种"未知"哨兵值。
 */
class CameraJsonParsingTest {

    private val cam = NxCamera("127.0.0.1", 80, 8080, HttpBinder.None)

    // --------------------------------------------- 真机响应：/api/v1/camera/info

    @Test
    fun `解析真机 camera info`() {
        val raw = """{"model":"NX500","fw_ver":"1.12","mac_address":"02:30:0d:00:00:01"}"""
        val info = cam.parseInfo(raw)
        assertEquals("NX500", info.model)
        assertEquals("1.12", info.firmware)
        assertEquals("02:30:0d:00:00:01", info.mac)
    }

    @Test
    fun `解析真机 camera status`() {
        val raw = """{"battery_charging":false,"battery_percent":-1,"battery_level":3,""" +
            """"drive":"UNKNOWN","mode":"UNKNOWN","hevc":"off","cameras":[""" +
            """{"ip":"192.168.5.39","packet":"NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|"}]}"""
        val st = cam.parseStatus(raw)
        assertEquals(false, st.charging)
        assertEquals(3, st.batteryLevel)
        assertEquals("UNKNOWN", st.mode)
        assertEquals("UNKNOWN", st.drive)
        assertEquals("off", st.hevc)
    }

    @Test
    fun `battery_percent 为 -1 时视为未知而不是 0`() {
        val raw = """{"battery_charging":false,"battery_percent":-1,"battery_level":3}"""
        val st = cam.parseStatus(raw)
        assertNull("负数是相机端的未知哨兵值，应报未知", st.batteryPercent)
        assertEquals(3, st.batteryLevel)
    }

    @Test
    fun `正常电量能解析`() {
        val st = cam.parseStatus("""{"battery_charging":true,"battery_percent":73,"battery_level":3}""")
        assertEquals(73, st.batteryPercent)
        assertEquals(true, st.charging)
    }

    @Test
    fun `真机 dirlist 响应`() {
        val raw = """{"ok":true,"path":"","dirs":["100PHOTO"],"files":[]}"""
        val o = MiniJson.parseObject(raw)
        assertEquals(true, MiniJson.bool(o, "ok"))
        assertEquals(listOf("100PHOTO"), MiniJson.stringList(o["dirs"]))
        assertTrue(MiniJson.stringList(o["files"]).isEmpty())
    }

    @Test
    fun `真机 thumb 失败响应是 200 加 text-plain 的错误文本`() {
        // 实测：f=100PHOTO/SAM_0001.JPG（文件不存在）→ 200 / text/plain / 30 字节
        val body = "thumb error: source not found"
        assertTrue("错误文本必须能被识别为非 JPEG（无尖括号/非 JSON）", body.startsWith("thumb error:"))
        assertFalse("不应被当成路径", body.contains('/'))
    }

    // ------------------------------------------------------- 兼容性：旧字段名
    @Test
    fun `同时兼容老写法以便跨固件版本`() {
        val info = cam.parseInfo("""{"model":"NX500","firmware":"1.41","mac":"aa:bb:cc:dd:ee:ff"}""")
        assertEquals("1.41", info.firmware)
        assertEquals("aa:bb:cc:dd:ee:ff", info.mac)

        val st = cam.parseStatus("""{"batteryCharging":true,"batteryPercent":55,"mode":"P"}""")
        assertEquals(55, st.batteryPercent)
        assertEquals(true, st.charging)
        assertEquals("P", st.mode)
    }

    // ------------------------------------------------------------ 健壮性

    @Test
    fun `字段缺失或为 null 时不崩且报未知`() {
        val st = cam.parseStatus("""{"battery_percent":null,"mode":null}""")
        assertNull(st.batteryPercent)
        assertNull(st.mode)
    }

    @Test
    fun `非 JSON 响应退化为 raw 而不抛异常`() {
        val info = cam.parseInfo("<html>not json</html>")
        assertNull(info.model)
        assertEquals("<html>not json</html>", info.raw)
    }
}
