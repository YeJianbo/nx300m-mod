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
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Log
import com.nxwa.nxconnect.data.CameraInfo
import com.nxwa.nxconnect.data.MiniJson
import com.nxwa.nxconnect.data.NxNetworkFacts
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * 相机发现。
 *
 * 分两步：
 *  1. 逐个网络探测：UDP 和 HTTP 都显式使用候选网络，保留已有相机会话的路由。
 *  2. 地址并发探测：优先探测独立服务 8781，同时兼容原有 80 端口。
 *
 * 注意：这些端点**不是原厂固件自带的**，由相机端 nx-remote-controller-daemon 提供，
 * 所以要能区分「能连上但不是相机」与「是相机但 mod 没启动」。
 */
object CameraDiscovery {

    private const val TAG = "CameraDiscovery"
    internal val DAEMON_PORTS = listOf(8781, NxCamera.PORT)

    sealed class Result {
        data class Found(
            val ip: String,
            val info: CameraInfo,
            val hasDaemon: Boolean = true,
            val daemonPort: Int = NxCamera.PORT,
            val cgiPort: Int = NxCamera.CGI_PORT,
            val network: Network? = null,
        ) : Result()

        /** 端口 80 通了但没有 daemon —— 相机可能在线但 mod 未启动 */
        data class CameraWithoutDaemon(val ip: String, val hint: String) : Result()

        data class NotFound(val tried: Int, val reason: String) : Result()
    }

    private sealed class Probe {
        data class Daemon(val info: CameraInfo, val daemonPort: Int, val cgiPort: Int) : Probe() {
            fun found(ip: String, network: Network? = null) =
                Result.Found(ip, info, daemonPort = daemonPort, cgiPort = cgiPort, network = network)
        }
        data class HttpOnly(val hint: String) : Probe()
        object Dead : Probe()
    }

    private class Scan(
        val found: MutableList<Pair<String, Probe.Daemon>> = mutableListOf(),
        val httpOnly: MutableList<Pair<String, Probe.HttpOnly>> = mutableListOf(),
        var tried: Int = 0,
    )

    /**
     * 探测相机。
     * @param explicitIp 用户手填的地址；给定时只验证它，不做全网段扫描
     */
    suspend fun discover(
        ctx: Context,
        explicitIp: String? = null,
        onProgress: (String) -> Unit = {},
    ): Result = withContext(Dispatchers.IO) {
        val scan = Scan()
        val networks = orderedNetworks(ctx) + listOf<Network?>(null)
        val seenByNetwork = mutableMapOf<Network?, MutableSet<String>>()

        if (!explicitIp.isNullOrBlank()) {
            val ip = explicitIp.trim()
            onProgress("正在验证 $ip …")
            for (net in networks) {
                currentCoroutineContext().ensureActive()
                scan.tried++
                when (val p = probe(ip, binder = binderFor(net))) {
                    is Probe.Daemon -> return@withContext p.found(ip, net)
                    is Probe.HttpOnly -> scan.httpOnly.add(Pair(ip, p))
                    Probe.Dead -> Unit
                }
            }
            return@withContext scan.httpOnly.firstOrNull()?.let { (_, p) -> Result.CameraWithoutDaemon(ip, p.hint) }
                ?: Result.NotFound(scan.tried, "地址 $ip 无响应：请确认手机已连上相机的网络")
        }

        // 同一个 IP 可以出现在不同网络上；只在当前网络内去重。
        for (net in networks) {
            currentCoroutineContext().ensureActive()
            val seen = seenByNetwork.getOrPut(net) { mutableSetOf() }
            val binder = binderFor(net)

            // 0.1) 在该网络上做 UDP 广播发现
            if (net != null) {
                onProgress("正在监听相机广播（UDP ${NxBeaconParser.BROADCAST_PORT}）…")
                val prefix = NetworkBinder.linkProperties(ctx, net)?.linkAddresses
                    ?.firstOrNull { it.address is java.net.Inet4Address }
                    ?.address?.hostAddress?.substringBeforeLast('.')?.plus(".")
                val beacons = NxUdpDiscovery.listen(2_000, ctx, net, prefix)
                if (beacons.isNotEmpty()) {
                    Log.i(TAG, "网络 $net 上广播发现 ${beacons.size} 台相机：${beacons.map { it.ip }}")
                }
                for (b in beacons) {
                    if (!seen.add(b.ip)) continue
                    onProgress("广播发现 ${b.ip}，正在验证…")
                    when (val p = probe(b.ip, binder = binder)) {
                        is Probe.Daemon -> {
                            val info =
                                if (p.info.model == null && b.model != null) b.toCameraInfo() else p.info
                            return@withContext p.copy(info = info).found(b.ip, net)
                        }

                        is Probe.HttpOnly -> {
                            scan.httpOnly.add(Pair(b.ip, p))
                            scan.tried++
                        }

                        Probe.Dead -> {
                            scan.tried++
                            Log.w(TAG, "广播里的 ${b.ip} 无法通过 HTTP 访问")
                        }
                    }
                }
            }

            // 0.2) 该网络的 HTTP 地址扫描
            val cands = roundOne(ctx, net).filter { seen.add(it) }
            if (cands.isEmpty()) continue

            onProgress("网络 ${net?.toString()?.takeLast(8) ?: "默认"}：探测 ${cands.size} 个地址…")
            scanFound(scan, cands, binder)

            scan.found.firstOrNull()?.let { (ip, d) ->
                Log.i(TAG, "found daemon at $ip")
                return@withContext d.found(ip, net)
            }
        }

        // ---- 第二轮：本网段都没找到，才试文档里的历史地址（相机换过网段就是这种情况）----
        for (net in networks) {
            val seen = seenByNetwork.getOrPut(net) { mutableSetOf() }
            val fallback = roundTwo(ctx, net).filter { seen.add(it) }
            if (fallback.isNotEmpty()) {
                onProgress("本网段未找到，回退探测 ${fallback.size} 个历史地址…")
                scanFound(scan, fallback, binderFor(net))
                scan.found.firstOrNull()?.let { (ip, d) ->
                    Log.i(TAG, "found daemon at $ip (fallback)")
                    return@withContext d.found(ip, net)
                }
            }
        }

        // ---- 第三轮：整段扫一遍 ----
        //
        // ★ 为什么需要这一步：相机做热点时的自身地址、以及相机接入手机热点时拿到的地址，
        //   都由对端的 DHCP 池决定，**猜不得**。AP_HOSTS 那种"常见主机位"列表
        //   在真机上就漏过（实测相机落在 .39，而列表里没有）。
        //   既然广播可能被 AP 隔离挡住，就补一次全 /24 扫描：64 并发下约 4 秒。
        for (net in networks) {
            val seen = seenByNetwork.getOrPut(net) { mutableSetOf() }
            val fullScan = fullSubnetCandidates(ctx, net).filter { seen.add(it) }
            if (fullScan.isNotEmpty()) {
                onProgress("正在扫描本网段 ${fullScan.size} 个地址…")
                scanFound(scan, fullScan, binderFor(net))
                scan.found.firstOrNull()?.let { (ip, d) ->
                    Log.i(TAG, "found daemon at $ip (full subnet scan)")
                    return@withContext d.found(ip, net)
                }
            }
        }

        scan.httpOnly.firstOrNull()?.let { (ip, h) -> return@withContext Result.CameraWithoutDaemon(ip, h.hint) }
        Result.NotFound(scan.tried, "已探测 ${scan.tried} 个地址，均无响应")
    }

    /** 并发探测一批地址，把结果并进 [scan] */
    private suspend fun scanFound(scan: Scan, ips: List<String>, binder: HttpBinder) = coroutineScope {
        // 限流：一次最多 64 个并发，避免把手机网络栈打满
        ips.chunked(64).forEach { chunk ->
            chunk.map { ip ->
                async {
                    val p = probe(ip, binder = binder)
                    synchronized(scan) {
                        scan.tried++
                        when (p) {
                            is Probe.Daemon -> scan.found.add(Pair(ip, p))
                            is Probe.HttpOnly -> scan.httpOnly.add(Pair(ip, p))
                            Probe.Dead -> Unit
                        }
                    }
                }
            }.forEach { it.await() }
            // A successful batch is sufficient. Continuing through the rest
            // of /24 kept the user waiting despite already finding the camera.
            if (scan.found.isNotEmpty()) return@coroutineScope
        }
    }

    /**
     * 本机所在网段的全部可用主机位（.1 ~ .254）。
     * 只在前面几轮都没命中时才用，代价是几秒钟。
     */
    private suspend fun fullSubnetCandidates(ctx: Context, network: Network?): List<String> = withContext(Dispatchers.IO) {
        val prefixes = if (network == null) NxUdpDiscovery.localPrefixes() else
            NetworkBinder.linkProperties(ctx, network)?.linkAddresses.orEmpty()
                .filter { it.address is java.net.Inet4Address }
                .mapNotNull { it.address.hostAddress?.substringBeforeLast('.')?.plus(".") }
        prefixes.distinct()
            .flatMap { prefix -> (1..254).map { "$prefix$it" } }
            .distinct()
    }

    /** 第一轮：本机所在网段的候选（含网关） */
    private suspend fun roundOne(ctx: Context, network: Network?): List<String> = withContext(Dispatchers.IO) {
        candidatesForNetwork(ctx, network)
    }

    /** 第二轮：文档记录过的历史地址 */
    private suspend fun roundTwo(ctx: Context, network: Network?): List<String> = withContext(Dispatchers.IO) {
        fallbackCandidates(
            localIpv4 = localIpv4(ctx, network),
            gateway = NetworkBinder.gatewayIpv4(ctx, network),
        )
    }

    private fun fallbackCandidates(localIpv4: String?, gateway: String?): List<String> =
        fallbackCandidatesForTest(localIpv4, gateway)

    /** 显式端口只验证该端口；未指定时优先 8781，并兼容旧服务 80。 */
    suspend fun verify(ip: String, port: Int? = null, network: Network? = NetworkBinder.boundNetwork): Result {
        val target = ip.trim()
        return when (val p = probe(target, port?.let { listOf(it) } ?: DAEMON_PORTS, binderFor(network))) {
            is Probe.Daemon -> p.found(target, network)
            is Probe.HttpOnly -> Result.CameraWithoutDaemon(target, p.hint)
            Probe.Dead -> Result.NotFound(1, "地址 $target${port?.let { ":$it" }.orEmpty()} 无响应")
        }
    }

    /**
     * 网络尝试顺序。
     *
     * 排序依据（经验性，但都有理由）：
     *  1. 用户刚加入的相机网络优先，其余 Wi-Fi 随后
     *  2. 同类型里**没有外网**的优先——相机热点/相机所在网段通常 validated=false，
     *     而手机若同时连着家用路由，那个网络是 validated=true，先试它会白跑一轮
     *  3. 以太网次之
     *  4. 最后用 null（系统默认路由）兜底——用户可能把相机接在家里路由器上，
     *     手机也在同一网段，此时默认路由本来就是对的
     */
    private fun orderedNetworks(ctx: Context): List<Network> {
        val cm = NetworkBinder.connectivity(ctx)
        fun transportRank(n: Network): Int {
            val c = runCatching { cm?.getNetworkCapabilities(n) }.getOrNull()
            return when {
                c?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true -> 0
                c?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true -> 2
                else -> 3
            }
        }

        val candidates = NetworkBinder.candidateNetworks(ctx).filter { transportRank(it) < 3 }.sortedWith(
            compareBy(
                { transportRank(it) + if (NetworkBinder.hasInternet(ctx, it)) 1 else 0 },
            ),
        )
        return (listOfNotNull(CameraWifi.currentNetwork) + candidates).distinct()
    }

    /**
     * 候选地址：网关 → 已知历史地址 → 本机同网段常见主机位。
     *
     * 注意实际组网的两种情况（真机都出现过）：
     *  - 相机做热点：相机常常就是网关（.1），也可能自报 .90/.100
     *  - 相机接家用路由器：**相机是 DHCP 客户端，IP 随机**（实测 192.168.5.39），
     *    此时靠这里的主机位列表只能碰运气，真正的解法是 UDP 广播（见 discover）
     */
    fun candidates(ctx: Context): List<String> {
        return candidatesForNetwork(ctx, NetworkBinder.boundNetwork)
    }

    private fun localIpv4(ctx: Context, network: Network?): String? =
        if (network == null) NetworkBinder.allLocalIpv4().firstOrNull() else
            NetworkBinder.linkProperties(ctx, network)?.linkAddresses
                ?.firstOrNull { it.address is java.net.Inet4Address }?.address?.hostAddress

    private fun binderFor(network: Network?): HttpBinder =
        if (network == null) HttpBinder.None else AndroidHttpBinder(network)

    private fun candidatesForNetwork(ctx: Context, network: Network?): List<String> {
        return candidatesForTest(
            localIpv4 = localIpv4(ctx, network),
            gateway = NetworkBinder.gatewayIpv4(ctx, network),
        )
    }

    /**
     * 候选地址推导（不触网）。
     *
     * 真机实测：相机接家用路由器时是 DHCP 客户端，IP 是 `192.168.5.39`，
     * 而网关是 `192.168.5.1` —— 所以这里必须包含"本机同网段的常见主机位"。
     * 历史网段（192.168.0.x / 192.168.43.x）**不混进来**，否则每次探测都要为
     * 不在本网段的地址白等超时（实测会拖慢十几秒）；它们由 [fallbackCandidatesForTest]
     * 作为第二轮兜底。
     */
    fun candidatesForTest(localIpv4: String?, gateway: String? = null): List<String> {
        val out = LinkedHashSet<String>()
        gateway?.let { out += it }

        if (localIpv4 != null && localIpv4.count { it == '.' } == 3) {
            val prefix = localIpv4.substringBeforeLast('.') + "."
            // 相机自建热点时占 .1/.90/.100；接路由器时是 DHCP 池里的随机地址，
            // 补一批常见主机位提高命中率（真机实测落在 .39）
            (NxNetworkFacts.AP_HOSTS + listOf(2, 20, 30, 39, 50, 100, 103, 104, 105))
                .forEach { out += "$prefix$it" }
        } else {
            NxNetworkFacts.AP_SUBNETS.forEach { sub ->
                NxNetworkFacts.AP_HOSTS.forEach { out += "$sub$it" }
            }
        }
        return out.filter { it.count { c -> c == '.' } == 3 && it != localIpv4 && it != "0.0.0.0" }.distinct()
    }

    /**
     * 第二轮兜底：文档里记录过的历史地址。
     * 只有在第一轮（本网段）完全没找到相机时才值得一试——相机接的网段变了就是这种情况。
     */
    fun fallbackCandidatesForTest(localIpv4: String?, gateway: String? = null): List<String> {
        val tried = candidatesForTest(localIpv4, gateway).toSet()
        return NxNetworkFacts.KNOWN_IPS.filter { it !in tried }
    }

    private suspend fun probe(ip: String, ports: List<Int> = DAEMON_PORTS, binder: HttpBinder = HttpBinder.None): Probe {
        // 先查所有候选端口的 API，避免原厂 80 的普通页面盖过独立服务 8781。
        for (port in ports) {
            currentCoroutineContext().ensureActive()
            val base = if (port == 80) "http://$ip" else "http://$ip:$port"
            val info = try {
                withTimeoutOrNull(2_500) {
                    val raw = NxHttp.getText("$base/api/v1/camera/info", 2_200, binder)
                    parseInfo(raw).takeIf { !it.model.isNullOrBlank() }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            if (info != null) {
                return Probe.Daemon(info, port, cgiPortForInfo(info.raw, port))
            }
        }

        for (port in ports) {
            currentCoroutineContext().ensureActive()
            val base = if (port == 80) "http://$ip" else "http://$ip:$port"
            val plain = try {
                withTimeoutOrNull(2_000) { NxHttp.getText("$base/", 1_600, binder) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            if (plain != null) {
                val hint = if (plain.contains("nx-remote-controller", true)) {
                    "检测到端口 $port 的遥控服务页面，但 API 未响应（daemon 可能启动中）"
                } else {
                    "端口 $port 有 HTTP 服务，但不是 NX 遥控 daemon"
                }
                return Probe.HttpOnly(hint)
            }
        }
        return Probe.Dead
    }

    /** 新原生服务集成相册；旧 NX-WA 服务的 CGI 仍使用独立 8080。 */
    internal fun cgiPortForInfo(raw: String, daemonPort: Int): Int {
        val caps = MiniJson.obj(MiniJson.parseObject(raw)["capabilities"])
        return if (caps != null && MiniJson.bool(caps, "gallery_preview") == true) daemonPort else NxCamera.CGI_PORT
    }

    internal fun parseInfo(text: String): CameraInfo = try {
        val o = MiniJson.parseObject(text)
        CameraInfo(
            model = MiniJson.strAny(o, "model", "camera", "modelName"),
            firmware = MiniJson.strAny(o, "fw_ver", "firmware", "version", "firmwareVersion"),
            mac = MiniJson.strAny(o, "mac", "macAddress", "mac_address"),
            raw = text,
        )
    } catch (e: Exception) {
        CameraInfo(raw = text)
    }
}
