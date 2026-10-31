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

import com.nxwa.nxconnect.data.CameraInfo
import com.nxwa.nxconnect.data.MiniJson

/**
 * 相机 UDP 广播报文的解析（纯 JVM，可单元测试）。
 *
 * 报文契约来自相机端源码（`network.h`：`PORT_UDP_BROADCAST = 5681`、
 * `DISCOVERY_PACKET_SIZE = 64`）与真机抓包：
 *
 * ```
 * NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|<补 NUL 到 64 字节>
 *   魔数    协议  机型   固件   MAC
 * ```
 *
 * 真机 `/api/v1/camera/status` 里的 `cameras` 字段就是把这条报文原样透出：
 * `"cameras":[{"ip":"192.168.5.39","packet":"NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|"}]`
 */
object NxBeaconParser {

    const val MAGIC = "NX_REMOTE"
    const val BROADCAST_PORT = 5681
    const val PACKET_SIZE = 64

    data class Beacon(
        val ip: String,
        val protocolVersion: String? = null,
        val model: String? = null,
        val firmware: String? = null,
        val mac: String? = null,
    ) {
        fun toCameraInfo() = CameraInfo(model = model, firmware = firmware, mac = mac)
    }

    /** 报文文本是否像 NX 相机的广播 */
    fun looksLikeBeacon(text: String): Boolean = text.trimStart().startsWith(MAGIC)

    /** 去掉补位的 NUL 与空白 */
    fun cleanPacket(raw: ByteArray, offset: Int = 0, length: Int = raw.size): String =
        String(raw, offset, length, Charsets.UTF_8).trimEnd('\u0000', '\n', '\r', ' ')

    /** 解析一条广播报文 */
    fun parse(ip: String, packet: String): Beacon {
        val parts = packet.split('|')
        fun at(i: Int) = parts.getOrNull(i)?.trim()?.takeIf { it.isNotEmpty() }
        return Beacon(
            ip = ip,
            protocolVersion = at(1),
            model = at(2),
            firmware = at(3),
            mac = at(4),
        )
    }

    /**
     * 从 `/api/v1/camera/status` 的 JSON 里取出相机列表。
     * 这是"被动已知"路径：只要 daemon 在线，就能拿到它广播里收集到的相机。
     */
    fun parseCamerasField(statusJson: String): List<Beacon> = try {
        val o = MiniJson.parseObject(statusJson)
        val arr = MiniJson.arr(o["cameras"]) ?: emptyList()
        arr.mapNotNull { item ->
            val m = MiniJson.obj(item) ?: return@mapNotNull null
            val ip = MiniJson.str(m, "ip") ?: return@mapNotNull null
            val packet = MiniJson.str(m, "packet")
            if (packet != null && looksLikeBeacon(packet)) parse(ip, packet)
            else Beacon(ip)
        }
    } catch (e: Exception) {
        emptyList()
    }
}
