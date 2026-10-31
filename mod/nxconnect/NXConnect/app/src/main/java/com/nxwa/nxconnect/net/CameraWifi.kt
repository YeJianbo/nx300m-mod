package com.nxwa.nxconnect.net

import android.content.Context
import android.net.*
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import android.os.Handler
import android.os.Looper
import com.nxwa.nxconnect.NxApp

/** Keep the system-approved, local-only network alive while the camera is in use. */
object CameraWifi {
    private var callback: ConnectivityManager.NetworkCallback? = null
    @Volatile private var joinedNetwork: Network? = null
    val currentNetwork: Network? get() = joinedNetwork
    private var requestOwner: Any? = null
    private var accepted = false
    private var stopWaiting: (() -> Unit)? = null
    var ssid: String? = null
        private set

    fun release(ctx: Context) {
        val manager = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val previous = callback
        callback = null // Ignore callbacks already queued for the released request.
        stopWaiting?.invoke()
        stopWaiting = null
        requestOwner = null
        accepted = false
        if (NetworkBinder.boundNetwork == joinedNetwork && joinedNetwork != null) NetworkBinder.unbind()
        joinedNetwork = null
        ssid = null
        previous?.let { runCatching { manager.unregisterNetworkCallback(it) } }
    }

    fun cancelPending(ctx: Context, owner: Any) {
        // Once Android has granted the WLAN, an Activity no longer owns its
        // lifetime. Losing an address during a handover does not revoke it.
        if (requestOwner === owner && !accepted) release(ctx)
    }

    fun isCurrent(network: Network): Boolean = callback != null && joinedNetwork == network
    fun isPending(owner: Any): Boolean = callback != null && requestOwner === owner && !accepted

    fun join(ctx: Context, name: String, password: String, wpa3: Boolean, owner: Any, result: (Network?, String?) -> Unit) {
        if (Build.VERSION.SDK_INT < 29) {
            result(null, "应用内加入 WLAN 需要 Android 10 或更高版本，请在系统 WLAN 设置中连接相机")
            return
        }
        val existing = joinedNetwork
        if (existing != null && ssid == name &&
            NetworkBinder.linkProperties(ctx, existing)?.linkAddresses?.any { it.address is java.net.Inet4Address &&
                !it.address.isAnyLocalAddress && !it.address.isLinkLocalAddress && !it.address.isLoopbackAddress } == true) {
            result(existing, null)
            return
        }
        release(ctx)
        val manager = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val handler = Handler(Looper.getMainLooper())
        var completion: ((Network?, String?) -> Unit)? = result
        var availableNetwork: Network? = null
        var deadline: Runnable? = null
        var ipv4Poll: Runnable? = null
        fun cancelTimers() {
            deadline?.let { handler.removeCallbacks(it) }
            ipv4Poll?.let { handler.removeCallbacks(it) }
            deadline = null
            ipv4Poll = null
        }
        val cb = object : ConnectivityManager.NetworkCallback() {
            private fun fail(message: String) {
                val notify = completion
                completion = null
                release(ctx)
                notify?.invoke(null, message)
            }

            private fun acceptIpv4(network: Network, props: LinkProperties): Boolean {
                if (callback !== this || availableNetwork != network) return false
                if (joinedNetwork == network) {
                    cancelTimers()
                    return true
                }
                if (props.linkAddresses.none { it.address is java.net.Inet4Address &&
                        !it.address.isAnyLocalAddress && !it.address.isLinkLocalAddress && !it.address.isLoopbackAddress }) return false
                cancelTimers()
                joinedNetwork = network
                accepted = true
                ssid = name
                NetworkBinder.bindTo(network)
                val notify = completion
                completion = null
                NxApp.log("相机 WLAN IPv4 就绪，保留系统请求：$network")
                notify?.invoke(network, null)
                return true
            }

            private fun inspectIpv4(network: Network) {
                if (callback !== this || availableNetwork != network) return
                val props = NetworkBinder.linkProperties(ctx, network)
                if (props != null && acceptIpv4(network, props)) return
                // Some vendors deliver onAvailable after DHCP and omit a later
                // LinkProperties callback. Read the existing address and retry.
                ipv4Poll = Runnable { inspectIpv4(network) }.also { handler.postDelayed(it, 300) }
            }

            override fun onAvailable(network: Network) {
                if (callback !== this) return
                cancelTimers()
                availableNetwork = network
                NxApp.log("相机 WLAN 已获系统批准，等待 IPv4：$network")
                deadline = Runnable {
                    if (callback !== this || availableNetwork != network) return@Runnable
                    cancelTimers()
                    if (!accepted) fail("已加入相机 WLAN，但未获取 IPv4 地址。请检查相机无线连接后重试")
                }.also { handler.postDelayed(it, 20_000) }
                inspectIpv4(network)
            }

            override fun onLinkPropertiesChanged(network: Network, props: LinkProperties) {
                // HTTP sockets use the captured Network directly. A process
                // binding failure must not unregister a granted WLAN request.
                acceptIpv4(network, props)
            }

            override fun onUnavailable() {
                if (callback !== this) return
                fail("未能加入相机 WLAN，可能已取消系统请求或相机热点未开启，请重试")
            }

            override fun onLost(network: Network) {
                if (callback !== this) return
                // A callback can receive a new network before the old one's
                // onLost. Never tear down the replacement or its request.
                if (joinedNetwork != network && availableNetwork != network) return
                // Before the initial IPv4 result, retaining this callback would
                // leave isPending(owner) true forever and block every retry.
                if (!accepted) {
                    fail("相机 WLAN 在获取地址前已断开，请重新连接")
                    return
                }
                if (availableNetwork == network) {
                    availableNetwork = null
                    cancelTimers()
                }
                if (joinedNetwork == network) {
                    joinedNetwork = null
                    ssid = null
                    if (NetworkBinder.boundNetwork == network) NetworkBinder.unbind()
                }
                val notify = completion
                completion = null
                NxApp.log("相机 WLAN 链路丢失，保留系统请求等待恢复：$network")
                notify?.invoke(null, "相机 WLAN 已断开，请重新连接")
            }
        }
        try {
            val spec = WifiNetworkSpecifier.Builder().setSsid(name).apply {
                if (password.isNotEmpty()) {
                    if (wpa3) setWpa3Passphrase(password) else setWpa2Passphrase(password)
                }
            }.build()
            val request = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).setNetworkSpecifier(spec).build()
            callback = cb
            requestOwner = owner
            stopWaiting = ::cancelTimers
            // Android's request timeout stops once onAvailable is delivered;
            // our own timers cover both approval and the subsequent DHCP wait.
            deadline = Runnable {
                if (callback !== cb) return@Runnable
                val notify = completion
                completion = null
                release(ctx)
                notify?.invoke(null, "连接相机 WLAN 超时，请确认系统连接弹窗和相机无线状态后重试")
            }.also { handler.postDelayed(it, 65_000) }
            manager.requestNetwork(request, cb, handler, 60_000)
        } catch (e: Exception) {
            completion = null
            release(ctx)
            result(null, e.message ?: "无法请求 WLAN")
        }
    }
}
