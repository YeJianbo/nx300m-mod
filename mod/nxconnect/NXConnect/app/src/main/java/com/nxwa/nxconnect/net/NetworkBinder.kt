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
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Log
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.util.Collections

/**
 * 探测阶段用：决定进程的默认路由走哪个网络，并给出候选网关。
 *
 * 只服务于发现流程；协议层（NxHttp / NxCamera）不依赖它，
 * 因此可以被 JVM 单元测试绕开。
 */
object NetworkBinder {

    private const val TAG = "NetworkBinder"

    @Volatile
    var boundNetwork: Network? = null
        private set

    /** App 级 Context，由 NxApp 注入，避免到处传 Context */
    @Volatile
    var appContext: Context? = null

    fun connectivity(ctx: Context): ConnectivityManager? =
        ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager

    /** 列出当前所有可用网络，Wi-Fi 优先（相机几乎总是通过 Wi-Fi 连的） */
    fun candidateNetworks(ctx: Context): List<Network> {
        val cm = connectivity(ctx) ?: return emptyList()
        val all = try {
            cm.allNetworks.toList()
        } catch (e: Exception) {
            Log.w(TAG, "allNetworks failed: ${e.message}")
            emptyList()
        }
        // Networks can disappear while the snapshot is being ranked.
        fun caps(n: Network) = runCatching { cm.getNetworkCapabilities(n) }.getOrNull()
        val wifi = all.filter { caps(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
            .sortedWith(compareBy<Network>(
                { if (it == CameraWifi.currentNetwork) 0 else 1 },
                { if (hasInternet(ctx, it)) 1 else 0 },
            ))
        val eth = all.filter {
            caps(it)?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true
        }
        val rest = all.filter { it !in wifi && it !in eth }
        return wifi + eth + rest
    }

    /** 手动输入地址时，优先选择实际覆盖目标网段的 Wi-Fi/以太网。 */
    fun networkForAddress(ctx: Context, address: String): Network? {
        val octets = address.trim().split('.').map { it.toIntOrNull() }
        val target = if (octets.size == 4 && octets.all { it != null && it in 0..255 }) {
            InetAddress.getByAddress(octets.map { it!!.toByte() }.toByteArray())
        } else null
        val cm = connectivity(ctx) ?: return null
        val candidates = candidateNetworks(ctx).filter { network ->
            val caps = runCatching { cm.getNetworkCapabilities(network) }.getOrNull()
            caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true ||
                caps?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true
        }
        // A default route covers every IP, so it cannot identify the camera's LAN.
        // Keep the existing camera/no-internet ordering when no specific route matches.
        return candidates.maxByOrNull { network ->
            if (target == null) -1 else linkProperties(ctx, network)?.routes.orEmpty()
                .map { it.destination }
                .filter { it.address is Inet4Address && it.prefixLength > 0 && it.contains(target) }
                .maxOfOrNull { it.prefixLength } ?: -1
        }
    }

    /** 路由列表通常先给出直连网段；其 gateway=0.0.0.0 并非相机地址。 */
    fun gatewayIpv4(ctx: Context, network: Network?): String? {
        val routes = linkProperties(ctx, network)?.routes.orEmpty()
            .filter { route ->
                val gateway = route.gateway
                gateway is Inet4Address && !gateway.isAnyLocalAddress && !gateway.isLoopbackAddress
            }
        return (routes.firstOrNull { it.isDefaultRoute } ?: routes.firstOrNull())?.gateway?.hostAddress
    }

    /** 有外网且已被系统验证过的网络 */
    fun hasInternet(ctx: Context, n: Network): Boolean {
        val cm = connectivity(ctx) ?: return false
        val c = runCatching { cm.getNetworkCapabilities(n) }.getOrNull() ?: return false
        return c.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            c.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    /** 把某个网络设为进程默认路由；传 null 表示恢复系统默认 */
    fun bindTo(network: Network?) {
        val ctx = appContext ?: return
        val cm = connectivity(ctx) ?: return
        try {
            val ok = cm.bindProcessToNetwork(network)
            if (ok) boundNetwork = network
            Log.i(TAG, "bindProcessToNetwork($network) -> $ok")
        } catch (e: Exception) {
            Log.w(TAG, "bind failed: ${e.message}")
        }
    }

    fun unbind() = bindTo(null)

    /** 当前绑定网络的 LinkProperties（拿网关与本机地址） */
    fun linkProperties(ctx: Context, network: Network? = boundNetwork): android.net.LinkProperties? {
        val n = network ?: return null
        val cm = connectivity(ctx) ?: return null
        return try {
            cm.getLinkProperties(n)
        } catch (e: Exception) {
            null
        }
    }

    /** 手机在相机网络里的本机 IPv4（用于推导同网段候选地址） */
    fun localIpv4(ctx: Context): String? {
        val lp = linkProperties(ctx)
        lp?.linkAddresses?.firstOrNull { it.address is Inet4Address }?.let {
            return it.address.hostAddress
        }
        return allLocalIpv4().firstOrNull()
    }

    fun allLocalIpv4(): List<String> = try {
        Collections.list(NetworkInterface.getNetworkInterfaces())
            .flatMap { Collections.list(it.inetAddresses) }
            .filterIsInstance<Inet4Address>()
            .filter { !it.isLoopbackAddress && it.isSiteLocalAddress }
            .mapNotNull { it.hostAddress }
    } catch (e: Exception) {
        emptyList()
    }
}
