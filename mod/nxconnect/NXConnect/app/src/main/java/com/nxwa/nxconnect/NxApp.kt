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
package com.nxwa.nxconnect

import android.app.Application
import android.content.Context
import android.util.Log
import com.nxwa.nxconnect.net.AndroidHttpBinder
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.net.NetworkBinder
import com.nxwa.nxconnect.net.CameraConnectionMonitor
import com.nxwa.nxconnect.net.StockRvfClient
import com.nxwa.nxconnect.net.StockRvfConnection
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
/**
 * 全局单例：保存当前连接的相机、网络绑定状态，以及一个便于用户回报问题的日志缓冲。
 */
class NxApp : Application() {
    private lateinit var connectionMonitor: CameraConnectionMonitor

    override fun onCreate() {
        super.onCreate()
        instance = this
        stockRvf = StockRvfConnection(CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate),
            isCurrentCamera = { camera === it },
            createClient = { cam ->
                val prefs = getSharedPreferences("nx", MODE_PRIVATE)
                val identity = prefs.getString("rvf_client_identity", null) ?: run {
                    val value = "02:" + java.util.UUID.randomUUID().toString().replace("-", "")
                        .take(10).chunked(2).joinToString(":")
                    prefs.edit().putString("rvf_client_identity", value).apply()
                    value
                }
                StockRvfClient(cam.ip, identity, binder = cam.httpBinder)
            }, log = { log(it) })
        androidx.appcompat.app.AppCompatDelegate.setDefaultNightMode(
            getSharedPreferences("appearance", 0).getInt("mode", androidx.appcompat.app.AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM))
        // 尽早安装：真机出现过"切画质后进程直接没了"，没有日志就无从定位
        CrashLog.install(this)
        NetworkBinder.appContext = this
        connectionMonitor = CameraConnectionMonitor(this, captureInProgress = stockRvf::captureInProgress) { expected, reason ->
            if (camera === expected) disconnect(reason, releaseWifi = false)
        }
        Log.i(TAG, "NX 连接启动 v${BuildConfig.VERSION_NAME} (${BuildConfig.BUILD_STAMP})")
    }

    companion object {
        private const val TAG = "NxApp"

        @Volatile
        lateinit var instance: NxApp
            private set

        /** 当前已连接的相机；未连接为 null */
        @Volatile
        var camera: NxCamera? = null
            private set

        private val connectedCamera = MutableStateFlow<NxCamera?>(null)
        val connection = connectedCamera.asStateFlow()
        lateinit var stockRvf: StockRvfConnection
            private set
        var lastDisconnectReason: String? = null
            private set

        /** 当前相机 IP（便于 UI 恢复） */
        @Volatile
        var cameraIp: String? = null

        /** 实际使用的端口（真机 80/8080；联调模拟相机时可能是 8081/8082） */
        @Volatile
        var daemonPort: Int = NxCamera.PORT

        @Volatile
        var cgiPort: Int = NxCamera.CGI_PORT

        private val logBuffer = ArrayDeque<String>()
        private const val LOG_LIMIT = 300

        fun connect(ip: String, port: Int = daemonPort, cgi: Int = cgiPort,
            profile: com.nxwa.nxconnect.net.CameraProfile = com.nxwa.nxconnect.net.CameraProfile(),
            network: android.net.Network? = NetworkBinder.boundNetwork) {
            com.nxwa.nxconnect.data.ViewerSessions.clear()
            cameraIp = ip
            daemonPort = port
            cgiPort = cgi
            // Android 侧传入网络绑定实现；协议层本身是纯 JVM 的，测试时用 HttpBinder.None
            camera = NxCamera(ip, port, cgi, AndroidHttpBinder(instance, network)).also { cam ->
                // 协议层不依赖 Android，日志通过钩子接回这里
                cam.logger = { msg -> log(msg) }
                cam.profile = profile
                cam.isCurrentConnection = { camera === cam }
                cam.restoreCacheDirectory = instance.cacheDir
            }
            lastDisconnectReason = null
            stockRvf.attach(camera!!)
            connectedCamera.value = camera
            instance.connectionMonitor.watch(camera!!, network)
            log("已连接相机 $ip（daemon:$port cgi:$cgi）")
        }

        fun disconnect(reason: String? = null, releaseWifi: Boolean = true) {
            instance.connectionMonitor.stop()
            stockRvf.detach()
            com.nxwa.nxconnect.data.ViewerSessions.clear()
            log("断开相机 ${cameraIp ?: "-"}")
            camera = null
            cameraIp = null
            lastDisconnectReason = reason
            connectedCamera.value = null
            NetworkBinder.unbind()
            // A missing service is not permission to revoke the user's approved WLAN.
            if (releaseWifi) com.nxwa.nxconnect.net.CameraWifi.release(instance)
            reason?.let { log(it) }
        }

        val isConnected: Boolean get() = camera != null

        fun log(msg: String) {
            val line = "${System.currentTimeMillis() % 100000}  $msg"
            Log.i(TAG, msg)
            synchronized(logBuffer) {
                logBuffer.addLast(line)
                while (logBuffer.size > LOG_LIMIT) logBuffer.removeFirst()
            }
        }

        fun logs(): List<String> = synchronized(logBuffer) { logBuffer.toList() }

        fun ctx(): Context = instance
    }
}
