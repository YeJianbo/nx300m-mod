// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.nxwa.nxconnect.BuildConfig
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.net.NxShell
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch

class AboutActivity : OneUiInfoActivity() {
    private lateinit var page: OneUiInfoPage
    private lateinit var heading: TextView
    private lateinit var cameraRows: LinearLayout
    private lateinit var notes: TextView
    private lateinit var syncTime: MaterialButton
    private lateinit var copyDiagnostics: MaterialButton
    private lateinit var statusCard: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        page = OneUiInfoPage(this, "关于相机")
        val savedModel = getSharedPreferences("camera_pair", 0).getString("model", null)
        heading = page.heading(savedModel ?: "相机")
        val deviceCard = page.card("设备信息")
        cameraRows = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        deviceCard.addView(cameraRows)
        copyDiagnostics = MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
            text = "复制服务诊断"
            isAllCaps = false
            visibility = View.GONE
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                gravity = Gravity.END
                topMargin = page.dp(4)
            }
            setOnClickListener { copyServiceDiagnostics() }
        }
        deviceCard.addView(copyDiagnostics)
        notes = page.text("正在读取相机信息…", 13f, secondary = true)
        statusCard = page.card()
        statusCard.addView(notes)
        syncTime = MaterialButton(this).apply {
            text = getString(R.string.sync_time)
            visibility = View.GONE
            setOnClickListener { syncCameraTime() }
        }
        statusCard.addView(syncTime)
        val app = page.card("应用信息")
        page.row(app, "应用名称", getString(R.string.app_name))
        page.row(app, "版本", BuildConfig.VERSION_NAME)
        page.row(app, "版本代码", BuildConfig.VERSION_CODE.toString())
        page.row(app, "构建时间", BuildConfig.BUILD_STAMP)
        loadCameraInfo()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                NxApp.connection.collect { updateDiagnosticsVisibility() }
            }
        }
    }

    private fun updateDiagnosticsVisibility() {
        copyDiagnostics.visibility = if (NxApp.camera?.profile?.galleryPreview == true) View.VISIBLE else View.GONE
    }

    private fun loadCameraInfo() {
        cameraRows.removeAllViews()
        updateDiagnosticsVisibility()
        val camera = NxApp.camera
        val ip = NxApp.cameraIp
        if (camera == null || ip == null) {
            val saved = getSharedPreferences("camera_pair", 0)
            page.row(cameraRows, "连接状态", "未连接")
            saved.getString("model", null)?.let { page.row(cameraRows, "已保存型号", it) }
            notes.text = "连接相机后可查看型号、固件与设备信息。"
            syncTime.visibility = View.GONE
            return
        }
        page.row(cameraRows, "连接状态", "已连接")
        lifecycleScope.launch {
            val overview = camera.readOverview(ip)
            if (NxApp.camera !== camera || NxApp.cameraIp != ip) {
                notes.text = "相机连接已改变，请返回后重新打开。"
                return@launch
            }
            cameraRows.removeAllViews()
            heading.text = overview.model ?: "相机"
            page.row(cameraRows, "型号", overview.model ?: "未读取")
            page.row(cameraRows, "固件版本", overview.firmware ?: "未读取")
            overview.mac?.let { page.row(cameraRows, "MAC 地址", it) }
            page.row(cameraRows, "相机地址", ip)
            overview.cameraTime?.let { page.row(cameraRows, "相机时间", it) }
            overview.uptime?.let { page.row(cameraRows, "运行时间", it) }
            notes.text = overview.notes.joinToString("\n")
            notes.visibility = if (overview.notes.isEmpty()) View.GONE else View.VISIBLE
            syncTime.visibility = if (camera.profile.timeSync) View.VISIBLE else View.GONE
            (statusCard.parent as View).visibility = if (overview.notes.isNotEmpty() || camera.profile.timeSync) View.VISIBLE else View.GONE
            syncTime.text = overview.timeDriftText?.let { "同步相机时间 · $it" } ?: getString(R.string.sync_time)
            updateDiagnosticsVisibility()
        }
    }

    private fun copyServiceDiagnostics() {
        val camera = NxApp.camera ?: return
        if (!camera.profile.galleryPreview || !copyDiagnostics.isEnabled) return
        copyDiagnostics.isEnabled = false
        copyDiagnostics.text = "正在读取诊断…"
        lifecycleScope.launch {
            try {
                val diagnostics = camera.readServiceDiagnostics()
                coroutineContext.ensureActive()
                if (NxApp.camera !== camera) {
                    Toast.makeText(this@AboutActivity, "相机连接已改变，未复制诊断", Toast.LENGTH_LONG).show()
                    return@launch
                }
                val clipboard = getSystemService(ClipboardManager::class.java) ?: error("手机剪贴板不可用")
                clipboard.setPrimaryClip(ClipData.newPlainText("NXConnect 服务诊断", diagnostics))
                Toast.makeText(this@AboutActivity, "服务诊断已复制到剪贴板", Toast.LENGTH_SHORT).show()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                Toast.makeText(this@AboutActivity, "读取服务诊断失败：${error.message?.take(100) ?: "请重试"}", Toast.LENGTH_LONG).show()
            } finally {
                copyDiagnostics.text = "复制服务诊断"
                copyDiagnostics.isEnabled = true
                updateDiagnosticsVisibility()
            }
        }
    }

    private fun syncCameraTime() {
        val camera = NxApp.camera ?: return
        val ip = NxApp.cameraIp ?: return
        if (!camera.profile.timeSync) return
        syncTime.isEnabled = false
        lifecycleScope.launch {
            val (ok, message) = NxShell.setTime(ip)
            if (NxApp.camera !== camera || NxApp.cameraIp != ip) return@launch
            syncTime.isEnabled = true
            Toast.makeText(this@AboutActivity, if (ok) "相机时间已同步为 $message" else "同步失败：$message", Toast.LENGTH_LONG).show()
            if (ok) loadCameraInfo()
        }
    }
}
