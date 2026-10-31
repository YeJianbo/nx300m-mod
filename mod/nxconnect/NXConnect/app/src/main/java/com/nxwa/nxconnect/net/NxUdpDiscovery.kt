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

import android.content.Context
import android.net.wifi.WifiManager
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.util.Collections

/**
 * 相机发现（UDP 广播监听）。
 *
 * 为什么这是最可靠的发现方式：相机接入路由器/手机热点时是 **DHCP 客户端**，
 * IP 由对端分配，靠"探测网关"或"扫常见主机位"都可能找不到；
 * 而相机每秒向 5681 端口广播的报文里**直接带着它自己的 IP**（IP 由报文来源地址给出）。
 *
 * ★ 热点场景下必须注意两件事（都是踩过的坑）：
 *  1. **必须先把 socket 绑到相机所在网络**。手机连着"无外网的相机热点"时，
 *     系统默认路由通常仍是蜂窝；直接 new DatagramSocket() 会从蜂窝网卡发包，
 *     既发不到相机网段、也收不到相机的广播 —— 表现就是"怎么都发现不了"。
 *  2. **要持有 WifiManager.MulticastLock**。Android 的 Wi-Fi 固件会过滤广播/组播帧，
 *     不持有锁时接收广播的成功率极低（尤其息屏后）。
 *
 * 解析逻辑在纯 JVM 的 [NxBeaconParser] 里，本文件只负责 socket 部分。
 */
object NxUdpDiscovery {

    private const val TAG = "NxUdpDiscovery"

    /**
     * 监听 [waitMs] 毫秒，返回期间广播过的相机。
     *
     * @param network 要绑定到的网络；null 表示用系统默认路由。
     *   **传相机所在网络可显著提高成功率**（见类注释）。
     * @param localPrefix 本机在相机网络里的网段前缀（如 `192.168.43.`）。
     *   给出时会额外向该网段的定向广播地址发一个探测包，
     *   比 255.255.255.255 更容易穿过某些 AP 的转发限制。
     */
    suspend fun listen(
        waitMs: Long = 3_000,
        ctx: Context? = null,
        network: android.net.Network? = null,
        localPrefix: String? = null,
    ): List<NxBeaconParser.Beacon> = withContext(Dispatchers.IO) {
        val found = LinkedHashMap<String, NxBeaconParser.Beacon>()
        var socket: DatagramSocket? = null
        var lock: WifiManager.MulticastLock? = null
        try {
            // 1) 持有组播锁：否则 Wi-Fi 固件会过滤广播帧，收包率极低
            lock = acquireMulticastLock(ctx)

            // 2) 关键：先把 socket 绑到相机所在网络
            socket = if (network != null) bindSocketToNetwork(network) else DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 400
                bind(InetSocketAddress(NxBeaconParser.BROADCAST_PORT))
            }
            Log.i(TAG, "已在 ${NxBeaconParser.BROADCAST_PORT} 监听相机广播（network=$network）")

            // 3) 主动发探测包：定向广播优先，再补一个全局广播
            pokeOn(socket, localPrefix)

            val buf = ByteArray(NxBeaconParser.PACKET_SIZE + 64)
            val deadline = System.currentTimeMillis() + waitMs
            while (System.currentTimeMillis() < deadline) {
                currentCoroutineContext().ensureActive()
                val packet = DatagramPacket(buf, buf.size)
                try {
                    socket.receive(packet)
                } catch (e: SocketTimeoutException) {
                    continue
                }
                val fromIp = packet.address?.hostAddress ?: continue
                val text = NxBeaconParser.cleanPacket(packet.data, packet.offset, packet.length)
                if (!NxBeaconParser.looksLikeBeacon(text)) continue
                val beacon = NxBeaconParser.parse(fromIp, text)
                if (found.put(beacon.ip, beacon) == null) {
                    Log.i(TAG, "发现相机广播：${beacon.ip} ${beacon.model} ${beacon.firmware}")
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Log.w(TAG, "UDP 监听不可用（${e.javaClass.simpleName}: ${e.message}），回退到 HTTP 扫描")
        } finally {
            try {
                socket?.close()
            } catch (e: Exception) {
                // 忽略
            }
            try {
                lock?.release()
            } catch (e: Exception) {
                // 忽略
            }
        }
        found.values.toList()
    }

    /**
     * 建一个绑到指定网络的 UDP socket，并完成 bind。
     * 显式网络失效时终止本轮 UDP，交由同网络 HTTP 验证或下一个网络处理。
     * 不退回系统默认路由，否则无外网相机热点可能被切换成蜂窝。
     */
    private fun bindSocketToNetwork(net: android.net.Network): DatagramSocket {
        var socket: DatagramSocket? = null
        return try {
            socket = DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 400
                bind(InetSocketAddress(NxBeaconParser.BROADCAST_PORT))
            }
            // ★ 这一步是热点场景的关键：之后这个 socket 的收发都走该网络
            net.bindSocket(socket)
            socket
        } catch (e: Exception) {
            Log.w(TAG, "bindSocket 到 $net 失败，跳过该网络的 UDP：${e.message}")
            try {
                socket?.close()
            } catch (ignored: Exception) {
                // 忽略
            }
            throw e
        }
    }

    private fun acquireMulticastLock(ctx: Context?): WifiManager.MulticastLock? {
        val c = ctx ?: return null
        return try {
            val wm = c.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
                ?: return null
            wm.createMulticastLock("nxconnect-discovery").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            Log.w(TAG, "获取 MulticastLock 失败：${e.message}")
            null
        }
    }

    /** 在已建立的 socket 上发探测包（定向 + 全局） */
    private fun pokeOn(socket: DatagramSocket, localPrefix: String?) {
        val payload = "${NxBeaconParser.MAGIC}|0.9|||".toByteArray(Charsets.UTF_8)
        val targets = mutableListOf<String>()
        localPrefix?.takeIf { it.count { ch -> ch == '.' } == 3 }?.let { targets += "${it}255" }
        targets += "255.255.255.255"
        for (addr in targets) {
            try {
                socket.send(
                    DatagramPacket(payload, payload.size,
                        InetAddress.getByName(addr), NxBeaconParser.BROADCAST_PORT),
                )
            } catch (e: Exception) {
                Log.v(TAG, "向 $addr 发探测包失败：${e.message}")
            }
        }
    }

    /** 本机所有局域网网段前缀（用于定向广播与候选地址推导） */
    fun localPrefixes(): List<String> = try {
        Collections.list(NetworkInterface.getNetworkInterfaces())
            .flatMap { Collections.list(it.inetAddresses) }
            .filterIsInstance<java.net.Inet4Address>()
            .filter { !it.isLoopbackAddress && it.isSiteLocalAddress }
            .mapNotNull { it.hostAddress?.substringBeforeLast('.')?.plus(".") }
            .distinct()
    } catch (e: Exception) {
        emptyList()
    }

    /**
     * 主动发一个探测包（兼容旧调用；不指定网络时走系统默认路由）。
     * 新代码请用 [listen] 的 network 参数，让收发同路。
     */
    suspend fun poke(): Unit = withContext(Dispatchers.IO) {
        try {
            DatagramSocket().use { s ->
                s.broadcast = true
                pokeOn(s, localPrefixes().firstOrNull())
            }
        } catch (e: Exception) {
            Log.v(TAG, "发探测包失败：${e.message}")
        }
    }
}
