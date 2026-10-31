package com.nxwa.nxconnect.net

import android.app.Activity
import android.app.Application
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkRequest
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Observe the selected network, rather than the default (often cellular) network. */
class CameraConnectionMonitor(
    private val app: Application,
    private val captureInProgress: (NxCamera) -> Boolean = { false },
    private val lost: (NxCamera, String) -> Unit,
) : Application.ActivityLifecycleCallbacks {
    private val main = Handler(Looper.getMainLooper())
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var foregroundActivities = 0
    private var camera: NxCamera? = null
    private var network: Network? = null
    private var callback: ConnectivityManager.NetworkCallback? = null
    private var heartbeat: Job? = null

    init { app.registerActivityLifecycleCallbacks(this) }

    fun watch(cam: NxCamera, selected: Network?) {
        stop()
        camera = cam
        network = selected
        if (selected != null) {
            val observer = object : ConnectivityManager.NetworkCallback() {
                override fun onLost(network: Network) {
                    main.post {
                        if (camera === cam && this@CameraConnectionMonitor.network == selected && network == selected)
                            lost(cam, "相机 WLAN 已断开")
                    }
                }
            }
            try {
                NetworkBinder.connectivity(app)?.registerNetworkCallback(
                    NetworkRequest.Builder().clearCapabilities().build(), observer)
                callback = observer
            } catch (_: Exception) {
                // Service checks still detect loss when a device rejects network observation.
            }
        }
        startHeartbeat()
    }

    fun stop() {
        camera = null
        network = null
        heartbeat?.cancel()
        heartbeat = null
        callback?.let { runCatching { NetworkBinder.connectivity(app)?.unregisterNetworkCallback(it) } }
        callback = null
    }

    private fun startHeartbeat() {
        val cam = camera ?: return
        val selected = network
        val binder = AndroidHttpBinder(selected)
        if (foregroundActivities == 0 || heartbeat?.isActive == true) return
        heartbeat = scope.launch {
            var failures = 0
            while (isActive && camera === cam) {
                if (selected != null && NetworkBinder.linkProperties(app, selected) == null) {
                    lost(cam, "相机 WLAN 已断开")
                    return@launch
                }
                val reachable = try {
                    val body = NxHttp.getText("${cam.baseUrl}/api/v1/camera/info", 2_500, binder)
                    !CameraDiscovery.parseInfo(body).model.isNullOrBlank()
                } catch (e: CancellationException) {
                    throw e
                } catch (_: Exception) { false }
                if (camera !== cam) return@launch
                // A busy Mod HTTP endpoint during a bounded stock Shot is not WLAN loss.
                // onLost and linkProperties above still handle actual network removal.
                failures = if (reachable || captureInProgress(cam)) 0 else failures + 1
                if (failures >= 2) {
                    lost(cam, "相机服务无响应，请确认相机已开机并连接 WLAN")
                    return@launch
                }
                delay(5_000)
            }
        }
    }

    override fun onActivityStarted(activity: Activity) { foregroundActivities++; startHeartbeat() }
    override fun onActivityStopped(activity: Activity) {
        foregroundActivities = (foregroundActivities - 1).coerceAtLeast(0)
        if (foregroundActivities == 0) { heartbeat?.cancel(); heartbeat = null }
    }
    override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
    override fun onActivityResumed(activity: Activity) = Unit
    override fun onActivityPaused(activity: Activity) = Unit
    override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) = Unit
    override fun onActivityDestroyed(activity: Activity) = Unit
}
