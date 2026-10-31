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
package com.nxwa.nxconnect.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.view.MenuItem
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.CameraOverview
import com.nxwa.nxconnect.databinding.ActivityMainBinding
import com.nxwa.nxconnect.databinding.HomeContentBinding
import com.nxwa.nxconnect.databinding.DialogManualConnectionBinding
import com.nxwa.nxconnect.net.CameraDiscovery
import com.nxwa.nxconnect.net.NfcWifiCredential
import com.nxwa.nxconnect.net.NfcTagInspection
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.net.StockRvfConnection
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

class MainActivity : AppCompatActivity() {

    private val photoPicker = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        LensMetadata.editCopies(this, uris)
    }

    private lateinit var b: ActivityMainBinding
    private lateinit var home: HomeContentBinding
    private lateinit var pairing: CameraPairing
    private var pendingNfcLaunch = false
    private var pendingNfcCredential: NfcWifiCredential? = null
    private var nfcReadJob: Job? = null
    private var nfcDialog: androidx.appcompat.app.AlertDialog? = null
    private var nfcScanDialog: androidx.appcompat.app.AlertDialog? = null
    private var connectionBusy = false
    private var connectionJob: Job? = null
    private var connectionAttempt = 0
    private var overviewJob: Job? = null
    private var renderedConnectionIp: String? = null
    private var renderedCamera: NxCamera? = null
    private var connectionUiInitialized = false
    private var headerBatteryPercent: Int? = null
    private var headerBatteryLevel: Int? = null
    private var headerBatteryCharging: Boolean? = null
    private lateinit var manual: DialogManualConnectionBinding
    private var manualDialog: androidx.appcompat.app.AlertDialog? = null
    private lateinit var homeHeader: OneUiHeader

    private val wifiPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
        if (grants.values.all { it }) startPairing() else {
            pendingNfcCredential = null
            pendingNfcLaunch = false
            status("需要 WLAN / 位置权限才能在应用内查找相机", R.color.nx_warn)
        }
    }

    private fun startPairing() {
        val credential = pendingNfcCredential
        pendingNfcCredential = null
        if (credential == null) pairing.start() else pairing.joinCredential(credential)
    }

    private fun pairCamera() {
        if (pairing.isWorking) {
            status("正在连接相机，请完成当前 WLAN 请求后再试")
            return
        }
        val permissions = mutableListOf<String>()
        // NFC credentials do not need a WLAN scan on Android 13+.
        if (pendingNfcCredential == null || Build.VERSION.SDK_INT < 33)
            permissions.add(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) permissions.add(Manifest.permission.NEARBY_WIFI_DEVICES)
        if (permissions.all { ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED }) startPairing()
        else wifiPermissions.launch(permissions.toTypedArray())
    }

    override fun onDestroy() {
        connectionAttempt++
        connectionJob?.cancel()
        connectionJob = null
        nfcDialog?.dismiss()
        nfcScanDialog?.dismiss()
        manualDialog?.dismiss()
        // Granted WLAN requests are app-owned and survive dispose(). Pending
        // requests still target this Activity and must not outlive its callbacks.
        if (::pairing.isInitialized) pairing.dispose()
        super.onDestroy()
    }

    private val notifPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* 拒绝也不影响核心功能 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        manual = DialogManualConnectionBinding.inflate(layoutInflater)
        b.scroll.layoutManager = LinearLayoutManager(this)
        home = HomeContentBinding.inflate(layoutInflater, b.scroll, false)
        b.scroll.itemAnimator = null
        // The compact dashboard remains one static item; RecyclerView owns nested scrolling
        // and fling cancellation just as it does in the gallery.
        b.scroll.adapter = object : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
            override fun getItemCount() = 1
            override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
                object : RecyclerView.ViewHolder(home.root) {}
            override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) = Unit
        }
        setContentView(b.root)
        homeHeader = OneUiHeader(b.root, b.homeAppBar, b.header, b.homeToolbar, expandedHeightDp = 336) { b.deviceName.text }
        b.homeActions.menu.add(0, 1, 0, "查找相机").apply {
            setIcon(R.drawable.ic_search)
            icon?.setTint(ContextCompat.getColor(this@MainActivity, R.color.nx_on_surface))
            setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        }
        b.homeActions.menu.add(0, 2, 1, "更多").apply {
            setIcon(R.drawable.ic_gallery_more)
            setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
        }
        b.homeActions.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                1 -> { if (!connectionBusy) detect(); true }
                2 -> { showConnectionMenu(); true }
                else -> false
            }
        }
        pairing = CameraPairing(this, { status(it) }, { found, network ->
            connectDiscovered(found, network)
        }, failed = { pendingNfcLaunch = false })
        home.btnPair.setOnClickListener {
            pendingNfcLaunch = false
            pendingNfcCredential = null
            pairCamera()
        }
        home.btnNfc.setOnClickListener { showNfcPairing() }
        NxMotion.touchBounce(home.btnNfc)
        home.btnPair.setOnLongClickListener {
            val saved = getSharedPreferences("camera_pair", 0).getString("ssid", null)
            if (saved != null) NxDialogBuilder(this)
                .setTitle("解除相机绑定").setMessage(saved).setPositiveButton("解除绑定") { _, _ ->
                    getSharedPreferences("camera_pair", 0).edit().clear().apply()
                    home.btnPair.text = "连接"
                    updateSavedDevice()
                }.setNegativeButton("取消", null).show()
            true
        }
        home.btnManual.setOnClickListener {
            showManualConnection()
        }

        NxMotion.touchBounce(home.btnPair)
        NxMotion.touchBounce(home.btnGallery)
        NxMotion.touchBounce(home.btnRemote)
        NxMotion.touchBounce(manual.btnConnect)
        NxMotion.touchBounce(manual.btnDetect)
        NxMotion.touchBounce(manual.btnSimulator)
        getSharedPreferences("camera_pair", 0).getString("ssid", null)?.let {
            home.btnPair.text = "连接"
            if (savedInstanceState == null && !NxApp.isConnected) b.root.post { pairCamera() }
        }
        updateSavedDevice()
        home.btnConnectionHelp.setOnClickListener { OneUiTransitions.open(this, Intent(this, ConnectionHelpActivity::class.java)) }
        home.btnAbout.setOnClickListener { OneUiTransitions.open(this, Intent(this, AboutActivity::class.java)) }
        home.btnModManager.setOnClickListener { OneUiTransitions.open(this, Intent(this, ModManagerActivity::class.java)) }
        home.btnLensMetadata.setOnClickListener { photoPicker.launch(arrayOf("image/jpeg", "image/x-samsung-srw", "application/octet-stream")) }
        val modes = intArrayOf(-1, 1, 2)
        val labels = arrayOf("跟随系统", "浅色", "深色")
        val mode = getSharedPreferences("appearance", 0).getInt("mode", -1)
        home.btnAppearance.text = "外观 · " + labels[modes.indexOf(mode).coerceAtLeast(0)]
        home.btnAppearance.setOnClickListener {
            NxDialogBuilder(this)
                .setTitle("外观")
                .setSingleChoiceItems(labels, modes.indexOf(mode).coerceAtLeast(0)) { dialog, which ->
                    getSharedPreferences("appearance", 0).edit().putInt("mode", modes[which]).apply()
                    dialog.dismiss()
                    androidx.appcompat.app.AppCompatDelegate.setDefaultNightMode(modes[which])
                }.setNegativeButton("取消", null).show()
        }
        manual.btnSimulator.visibility = if (Build.FINGERPRINT.contains("generic") || Build.MODEL.contains("Emulator") || Build.MODEL.contains("sdk_gphone")) View.VISIBLE else View.GONE
        manual.ipInput.setOnEditorActionListener { _, action, _ ->
            if (action == android.view.inputmethod.EditorInfo.IME_ACTION_DONE && manual.btnConnect.isEnabled) {
                connect(manual.ipInput.text?.toString().orEmpty()); true
            } else false
        }
        manual.deviceLine.text = "本机地址：" + (
            com.nxwa.nxconnect.net.NetworkBinder.localIpv4(this) ?: "未获取到局域网地址"
            )

        EdgeToEdge.setup(this)
        androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(b.root) { v, insets ->
            val top = insets.getInsets(
                androidx.core.view.WindowInsetsCompat.Type.statusBars() or
                    androidx.core.view.WindowInsetsCompat.Type.displayCutout(),
            ).top
            val bottom = insets.getInsets(
                androidx.core.view.WindowInsetsCompat.Type.navigationBars(),
            ).bottom
            v.setPadding(v.paddingLeft, top, v.paddingRight, bottom)
            androidx.core.view.WindowInsetsCompat.CONSUMED
        }
        androidx.core.view.ViewCompat.requestApplyInsets(b.root)

        // 恢复上次连接的 IP 与端口
        val prefs = getSharedPreferences("nx", MODE_PRIVATE)
        prefs.getString("last_ip", null)?.let { manual.ipInput.setText(it) }
        manual.portInput.setText(prefs.getInt("daemon_port", NxCamera.PORT).toString())
        manual.cgiPortInput.setText(prefs.getInt("cgi_port", NxCamera.CGI_PORT).toString())
        savedInstanceState?.getString("manual_ip")?.let { manual.ipInput.setText(it) }
        savedInstanceState?.getString("manual_port")?.let { manual.portInput.setText(it) }
        savedInstanceState?.getString("manual_cgi_port")?.let { manual.cgiPortInput.setText(it) }
        if (savedInstanceState?.getBoolean("manual_open") == true) b.root.post { showManualConnection() }
        NxApp.cameraIp?.let {
            manual.ipInput.setText(it)
            refreshConnectedUi()
        }

        manual.btnSimulator.setOnClickListener {
            manual.ipInput.setText("10.0.2.2")
            manual.portInput.setText("8765")
            manual.cgiPortInput.setText("8765")
            connect("10.0.2.2")
        }

        manual.btnDetect.setOnClickListener { detect() }
        manual.btnConnect.setOnClickListener { connect(manual.ipInput.text?.toString().orEmpty()) }
        home.btnGallery.setOnClickListener {
            OneUiTransitions.open(this, Intent(this, GalleryActivity::class.java))
        }
        home.btnRemote.setOnClickListener {
            OneUiTransitions.open(this, Intent(this, RemoteActivity::class.java).putExtra("start_stock_video", true))
        }
        home.btnCameraSettings.setOnClickListener {
            val camera = NxApp.camera ?: return@setOnClickListener
            if (!NxApp.isConnected || !camera.profile.hasCameraSettings) return@setOnClickListener
            OneUiTransitions.open(this, Intent(this, CameraSettingsActivity::class.java))
        }

        askNotificationPermission()
        showCrashReportIfAny()
        if (savedInstanceState == null) handleNfcIntent(intent)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                launch { NxApp.connection.collect { refreshConnectedUi() } }
                launch { NxApp.stockRvf.state.collect { refreshHeaderStatus() } }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleNfcIntent(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("manual_ip", manual.ipInput.text?.toString())
        outState.putString("manual_port", manual.portInput.text?.toString())
        outState.putString("manual_cgi_port", manual.cgiPortInput.text?.toString())
        outState.putBoolean("manual_open", manualDialog?.isShowing == true)
        super.onSaveInstanceState(outState)
    }

    private fun updateSavedDevice() {
        val saved = getSharedPreferences("camera_pair", 0)
        if (!NxApp.isConnected) b.deviceName.text = saved.getString("model", null) ?: "NX300M"
        homeHeader.refresh()
    }

    private fun showConnectionMenu() {
        val items = mutableListOf<Pair<String, () -> Unit>>(
            "NFC 碰一碰" to { showNfcPairing() },
            "连接帮助" to { OneUiTransitions.open(this, Intent(this, ConnectionHelpActivity::class.java)) },
        )
        if (!connectionBusy) {
            items.add(0, "手动连接" to { showManualConnection() })
            val cam = NxApp.camera
            val stock = NxApp.stockRvf.state.value
            if (cam != null && stock.camera === cam && stock.phase != StockRvfConnection.Phase.UNAVAILABLE) {
                when (stock.phase) {
                    StockRvfConnection.Phase.PAIRING -> items.add("取消原厂连接" to { NxApp.stockRvf.cancel(cam) })
                    StockRvfConnection.Phase.CONNECTED -> items.add("断开原厂遥控" to {
                        NxApp.stockRvf.cancel(cam, "原厂遥控已断开，可手动重试")
                    })
                    else -> items.add("重试原厂连接" to { NxApp.stockRvf.request(cam, explicitRetry = true) })
                }
            }
            if (NxApp.isConnected || com.nxwa.nxconnect.net.CameraWifi.currentNetwork != null) items.add("断开连接" to {
                // The menu can remain open while an NFC request starts connecting.
                if (!connectionBusy) {
                    pairing.dispose()
                    pendingNfcCredential = null
                    pendingNfcLaunch = false
                    NxApp.disconnect()
                    refreshConnectedUi()
                    toast("已断开连接")
                }
            })
        }
        val anchor = b.homeActions.findViewById<View>(2) ?: b.homeActions
        GalleryChrome.menu(this, anchor, items)
    }

    private fun refreshHeaderStatus() {
        val connected = NxApp.isConnected
        val stock = NxApp.stockRvf.state.value.takeIf {
            connected && it.camera === NxApp.camera && it.phase != StockRvfConnection.Phase.UNAVAILABLE
        }
        b.statusLine.setCompoundDrawablesRelativeWithIntrinsicBounds(if (connected) R.drawable.ic_connected else 0, 0, 0, 0)
        b.statusLine.text = when {
            stock != null -> "Mod 已连接"
            connected -> "已连接"
            connectionBusy -> "正在连接…"
            else -> "未连接"
        }
        b.statusLine.setTextColor(ContextCompat.getColor(this, if (connected) R.color.nx_ok else R.color.nx_on_surface_variant))
        b.headerBattery.visibility = if (connected) View.VISIBLE else View.GONE
        b.statusBatteryDivider.visibility = if (connected) View.VISIBLE else View.GONE
        b.headerBattery.setImageDrawable(CameraBatteryIcon(this, headerBatteryPercent, headerBatteryLevel, headerBatteryCharging))
        b.headerBattery.contentDescription = CameraBatteryIcon.description(headerBatteryPercent, headerBatteryLevel, headerBatteryCharging)
        // All status stays inside the pull-down area; the compact bar only names the camera.
        b.cameraStateText.visibility = if (connected) View.VISIBLE else View.INVISIBLE
        b.autoStatus.visibility = if (!connected || stock != null) View.VISIBLE else View.INVISIBLE
        if (stock != null) b.autoStatus.text = stock.message
        homeHeader.subtitle = null
        homeHeader.refresh()
    }

    private fun showManualConnection() {
        if (manualDialog?.isShowing == true) return
        (manual.root.parent as? android.view.ViewGroup)?.removeView(manual.root)
        manual.btnClose.setOnClickListener { manualDialog?.dismiss() }
        manualDialog = NxDialogBuilder(this)
            .setTitle("手动连接")
            .setIcon(R.drawable.ic_wifi)
            .setView(manual.root)
            .show()
    }

    private fun handleNfcIntent(intent: Intent?) {
        val action = intent?.action ?: return
        if (action !in listOf(NfcAdapter.ACTION_NDEF_DISCOVERED, NfcAdapter.ACTION_TAG_DISCOVERED,
                NfcAdapter.ACTION_TECH_DISCOVERED)) return
        // A configuration change must not replay a previous tap and open a second Wi-Fi request.
        setIntent(Intent(this, MainActivity::class.java))
        readNfc { com.nxwa.nxconnect.net.NfcPayloadParser.inspect(intent) }
    }

    private fun showNfcPairing() {
        if (connectionBusy || pairing.isWorking) {
            status("正在连接相机，请完成当前连接后再碰一碰")
            return
        }
        val adapter = NfcAdapter.getDefaultAdapter(this)
        if (adapter == null) {
            NxDialogBuilder(this).setTitle("此手机不支持 NFC").setIcon(R.drawable.ic_oneui_nfc)
                .setMessage("可以用 WLAN 连接或自动查找相机。")
                .setPositiveButton("连接 WLAN") { _, _ -> pairCamera() }.setNegativeButton("关闭", null).show()
            return
        }
        if (!adapter.isEnabled) {
            NxDialogBuilder(this).setTitle("开启 NFC").setIcon(R.drawable.ic_oneui_nfc)
                .setMessage("开启手机 NFC 后，回到此页面靠近相机的 NFC 标识。")
                .setPositiveButton("去设置") { _, _ -> startActivity(Intent(android.provider.Settings.ACTION_NFC_SETTINGS)) }
                .setNegativeButton("取消", null).show()
            return
        }
        if (nfcScanDialog?.isShowing == true) return
        nfcScanDialog = NxDialogBuilder(this).setTitle("NFC 碰一碰").setIcon(R.drawable.ic_oneui_nfc)
            .setMessage("先在相机上开启无线连接，再将手机背部靠近相机的 NFC 标识。\n\n读到 WLAN 凭据后会询问是否连接，识别相机服务后打开相册。")
            .setNegativeButton("取消", null).show()
    }

    private fun readNfc(parse: () -> NfcTagInspection) {
        if (nfcReadJob?.isActive == true || nfcDialog?.isShowing == true) return
        if (connectionBusy || pairing.isWorking) {
            status("正在连接相机，请完成当前连接后再碰一碰")
            return
        }
        nfcReadJob = lifecycleScope.launch {
            val inspection = try { withContext(Dispatchers.IO) { parse() } }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) {
                status("未能读取 NFC 标签，请保持手机贴近标签后重试", R.color.nx_warn)
                return@launch
            }
            if (isFinishing || isDestroyed) return@launch
            nfcScanDialog?.dismiss()
            val credential = inspection.credential
            if (credential == null) {
                val hints = inspection.hints.joinToString("\n") { hint ->
                    listOfNotNull(hint.model?.let { "型号：$it" }, hint.macSuffix?.let { "MAC 末六位：$it" }).joinToString(" · ")
                }.take(400)
                val records = inspection.records.joinToString("、") { "${it.type.take(60)}（${it.payloadBytes} 字节）" }.take(300)
                NxApp.log("NFC 读取：${inspection.records.size} 项记录；WLAN 凭据不可用")
                status("已读取 NFC，尚不能识别其中的相机连接信息", R.color.nx_warn)
                nfcDialog = NxDialogBuilder(this@MainActivity).setTitle("已读取 NFC 标签").setIcon(R.drawable.ic_oneui_nfc)
                    .setMessage(buildString {
                        append("标签中没有可用的 WLAN 凭据。可开启相机无线后选择对应的 WLAN。")
                        if (hints.isNotBlank()) append("\n\n标签标注（未验证）：\n$hints")
                        append(if (records.isNotBlank()) "\n\n标签记录：$records" else "\n\n未读取到 NDEF 记录。")
                    })
                    .setPositiveButton("查找相机") { _, _ ->
                        pendingNfcCredential = null
                        pendingNfcLaunch = false
                        pairCamera()
                    }.setNegativeButton("关闭", null).show()
                return@launch
            }
            nfcDialog = NxDialogBuilder(this@MainActivity).setTitle("通过 NFC 连接").setIcon(R.drawable.ic_oneui_nfc)
                .setMessage("连接 WLAN：${credential.ssid}\n\n继续后请在 Android 系统弹窗允许连接。连接成功后会识别相机服务并进入相册。" +
                    if (NxApp.isConnected) "\n\n当前相机连接将断开。" else "")
                .setPositiveButton("连接") { _, _ ->
                    pairing.dispose()
                    NxApp.disconnect()
                    refreshConnectedUi()
                    pendingNfcCredential = credential
                    pendingNfcLaunch = true
                    pairCamera()
                }.setNegativeButton("取消", null).show()
        }
    }

    private fun showCrashReportIfAny() {
        val report = com.nxwa.nxconnect.CrashLog.readLast(this) ?: return
        NxDialogBuilder(this)
            .setTitle("上次运行时崩溃了")
            .setMessage(
                "把下面这段截图发给开发者即可定位。\n\n" +
                    report.take(3000) +
                    if (report.length > 3000) "\n\n…（已截断）" else "",
            )
            .setPositiveButton("知道了") { _, _ -> com.nxwa.nxconnect.CrashLog.clear(this) }
            .setNeutralButton("保留") { _, _ -> }
            .show()
    }

    override fun onResume() {
        super.onResume()
        runCatching {
            NfcAdapter.getDefaultAdapter(this)?.enableReaderMode(this, { tag: Tag ->
                runOnUiThread { readNfc { com.nxwa.nxconnect.net.NfcPayloadParser.inspect(tag) } }
            }, NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or
                NfcAdapter.FLAG_READER_NFC_F or NfcAdapter.FLAG_READER_NFC_V, null)
        }
        refreshConnectedUi()
    }

    override fun onPause() {
        nfcReadJob?.cancel()
        runCatching { NfcAdapter.getDefaultAdapter(this)?.disableReaderMode(this) }
        super.onPause()
    }

    override fun onRestart() {
        super.onRestart()
        if (NxApp.isConnected) loadOverview()
    }

    override fun onStop() {
        overviewJob?.cancel()
        overviewJob = null
        super.onStop()
    }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    private fun setBusy(busy: Boolean, text: String? = null) {
        connectionBusy = busy
        b.progress.visibility = if (busy) View.VISIBLE else View.INVISIBLE
        home.btnPair.isEnabled = !busy
        home.btnNfc.isEnabled = !busy
        manual.btnConnect.isEnabled = !busy
        manual.btnDetect.isEnabled = !busy
        manual.btnSimulator.isEnabled = !busy
        manual.ipInput.isEnabled = !busy
        manual.portInput.isEnabled = !busy
        manual.cgiPortInput.isEnabled = !busy
        manual.btnConnect.text = if (busy) "连接中…" else "连接"
        home.hubCard.visibility = if (!NxApp.isConnected || busy) View.VISIBLE else View.GONE
        if (text != null) status(text)
        else refreshHeaderStatus()
    }

    private fun status(text: String, colorRes: Int = R.color.nx_text_dim) {
        refreshHeaderStatus()
        b.statusLine.text = when {
            NxApp.isConnected -> if (NxApp.stockRvf.state.value.phase == StockRvfConnection.Phase.UNAVAILABLE) "已连接" else "Mod 已连接"
            colorRes == R.color.nx_err || colorRes == R.color.nx_error -> "连接失败"
            colorRes == R.color.nx_warn -> "连接待处理"
            connectionBusy -> "正在连接…"
            else -> "未连接"
        }
        val stock = NxApp.stockRvf.state.value
        b.autoStatus.text = if (NxApp.isConnected && stock.camera === NxApp.camera && stock.phase != StockRvfConnection.Phase.UNAVAILABLE)
            stock.message else if (text == getString(R.string.status_not_connected)) "连接相机后显示拍摄状态" else text
        b.statusLine.contentDescription = "${b.statusLine.text}，$text"
        if (NxApp.isConnected && !connectionBusy && colorRes in listOf(R.color.nx_warn, R.color.nx_err, R.color.nx_error)) {
            toast(text)
        }
    }

    private fun refreshConnectedUi() {
        val connected = NxApp.isConnected
        val ip = NxApp.cameraIp.takeIf { connected }
        val changed = !connectionUiInitialized || renderedConnectionIp != ip || renderedCamera !== NxApp.camera
        // 页面内容由布局测量，连接切换不持有旧高度、绝对位置或透明度。
        listOf(home.hubCard).forEach { view ->
            view.animate().setListener(null)
            view.animate().cancel()
            view.alpha = 1f
            view.translationY = 0f
            view.layoutParams.height = android.view.ViewGroup.LayoutParams.WRAP_CONTENT
        }
        home.actionRow.visibility = View.VISIBLE
        home.btnGallery.isEnabled = connected
        home.btnRemote.isEnabled = connected
        home.btnGallery.alpha = if (connected) 1f else .38f
        home.btnRemote.alpha = if (connected) 1f else .38f
        refreshCameraSettingsEntry()
        home.hubCard.visibility = if (!connected || connectionBusy) View.VISIBLE else View.GONE
        home.manualGroup.visibility = if (connected) View.GONE else View.VISIBLE
        home.btnPair.visibility = if (connected) View.GONE else View.VISIBLE
        home.btnNfc.visibility = if (connected) View.GONE else View.VISIBLE
        if (changed) {
            headerBatteryPercent = null
            headerBatteryLevel = null
            headerBatteryCharging = null
            b.cameraStateText.text = ""
        }
        refreshHeaderStatus()
        if (connected) {
            if (changed) {
                status("已连接相机。", R.color.nx_ok)
                manualDialog?.dismiss()
                loadOverview()
            }
        } else if (changed) {
            status(NxApp.lastDisconnectReason ?: getString(R.string.status_not_connected))
            b.cameraStateText.text = ""
        }
        updateSavedDevice()
        renderedConnectionIp = ip
        renderedCamera = NxApp.camera
        connectionUiInitialized = true
        if (changed) b.scroll.post {
            b.scroll.scrollToPosition(0)
            b.homeAppBar.setExpanded(true, false)
        }
    }

    private fun refreshCameraSettingsEntry() {
        val connected = NxApp.isConnected
        val profile = NxApp.camera?.profile
        val supported = connected && profile?.hasCameraSettings == true
        home.btnCameraSettings.isEnabled = supported
        home.btnCameraSettings.alpha = if (supported) 1f else .5f
        home.cameraSettingsSummary.text = when {
            !connected -> "连接相机后可设置"
            !supported -> "当前相机未提供设置能力"
            profile?.hasBodySettings == true -> "原厂菜单与机身扩展"
            else -> "通过相机屏幕调整原厂菜单"
        }
    }

    private fun loadOverview() {
        val cam = NxApp.camera ?: return
        val ip = NxApp.cameraIp ?: return
        overviewJob?.cancel()
        b.cameraStateText.text = "正在读取相机状态…"
        overviewJob = lifecycleScope.launch {
            try {
                // Home only needs the HTTP model/battery/mode fields. System
                // queries and clock writes remain explicit actions in About.
                val overview = withTimeout(10_000) { cam.readOverview(ip, includeShell = false) }
                if (NxApp.camera !== cam || NxApp.cameraIp != ip) return@launch
                renderOverview(overview)
            } catch (_: TimeoutCancellationException) {
                if (NxApp.camera === cam) b.cameraStateText.text = "暂未获取相机状态"
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (NxApp.camera === cam) b.cameraStateText.text = "暂未获取相机状态"
                NxApp.log("读取首页状态失败：${e.message}")
            }
        }
    }

    private fun renderOverview(o: CameraOverview) {
        o.model?.takeIf { it.isNotBlank() }?.let { b.deviceName.text = it }
        headerBatteryPercent = o.batteryPercent
        headerBatteryLevel = o.batteryLevel
        headerBatteryCharging = o.charging
        refreshHeaderStatus()
        fun known(value: String?) = value?.trim()?.takeIf { it.isNotEmpty() && !it.equals("unknown", ignoreCase = true) }
        val rows = buildList {
            known(o.mode)?.let { add("拍摄模式 $it") }
            known(o.drive)?.let { add("拍摄方式 $it") }
            known(o.sdFreeText)?.let { add("存储空间 $it 可用") }
        }
        b.cameraStateText.text = rows.joinToString("\n").ifBlank { "暂未获取拍摄与存储状态" }
    }
    private fun dp(v: Int): Int = (resources.displayMetrics.density * v).toInt()

    private fun detect() {
        if (connectionBusy || pairing.isWorking) {
            status("正在连接相机，请完成当前 WLAN 请求后再试")
            return
        }
        val attempt = ++connectionAttempt
        setBusy(true, "开始探测…")
        connectionJob = lifecycleScope.launch {
            var discovered: CameraDiscovery.Result.Found? = null
            try {
                val result = withTimeout(45_000) {
                    CameraDiscovery.discover(this@MainActivity) { msg ->
                        runOnUiThread {
                            if (connectionAttempt == attempt && !isDestroyed) status(msg)
                        }
                    }
                }
                when (result) {
                is CameraDiscovery.Result.Found -> {
                    status("发现相机 ${result.ip}:${result.daemonPort}", R.color.nx_ok)
                    discovered = result
                }

                is CameraDiscovery.Result.CameraWithoutDaemon -> {
                    status("${result.ip}：${result.hint}", R.color.nx_warn)
                    toast("连上了设备，但相机端遥控服务没有响应。请确认相机无线连接与 SD 卡服务已启动。")
                }

                is CameraDiscovery.Result.NotFound -> {
                    status("未找到相机（${result.reason}）", R.color.nx_err)
                }
                }
            } catch (_: TimeoutCancellationException) {
                status("查找相机超时。WLAN 连接已保留，可重新查找或手动填写相机地址", R.color.nx_warn)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                status("查找相机失败：${e.message}", R.color.nx_err)
            } finally {
                if (connectionAttempt == attempt) {
                    connectionJob = null
                    setBusy(false)
                }
            }
            currentCoroutineContext().ensureActive()
            if (connectionAttempt == attempt) discovered?.let { connectDiscovered(it) }
        }
    }

    private fun connectDiscovered(found: CameraDiscovery.Result.Found, network: android.net.Network? = null) {
        manual.ipInput.setText(found.ip)
        manual.portInput.setText(found.daemonPort.toString())
        manual.cgiPortInput.setText(found.cgiPort.toString())
        connect(found.ip, network ?: found.network, found)
    }

    private fun connect(ipRaw: String, selectedNetwork: android.net.Network? = null,
                        discovered: CameraDiscovery.Result.Found? = null) {
        val ip = ipRaw.trim().removePrefix("http://").removePrefix("https://").substringBefore('/')
        if (ip.isBlank()) {
            toast("请先填写相机 IP，或点“自动查找”")
            return
        }
        val daemonPort = discovered?.daemonPort ?: manual.portInput.text?.toString()?.toIntOrNull()?.takeIf { it in 1..65535 }
            ?: NxCamera.PORT
        val cgiPort = discovered?.cgiPort ?: manual.cgiPortInput.text?.toString()?.toIntOrNull()?.takeIf { it in 1..65535 }
            ?: NxCamera.CGI_PORT

        (getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager)
            .hideSoftInputFromWindow(manual.ipInput.windowToken, 0)
        manual.ipInput.clearFocus()
        connectionJob?.cancel()
        val attempt = ++connectionAttempt
        setBusy(true, "正在连接 $ip …")
        connectionJob = lifecycleScope.launch {
            var established = false
            try {
                val route = withContext(Dispatchers.IO) {
                    val isLocalOrLoopback = ip == "10.0.2.2" || ip == "127.0.0.1" || ip == "localhost"
                    if (isLocalOrLoopback) {
                        com.nxwa.nxconnect.net.NetworkBinder.unbind()
                        null
                    } else {
                        val selected = selectedNetwork
                            ?: com.nxwa.nxconnect.net.NetworkBinder.networkForAddress(this@MainActivity, ip)
                        com.nxwa.nxconnect.net.NetworkBinder.bindTo(selected)
                        selected
                    }
                }
                val verify = discovered ?: withTimeout(12_000) {
                    // Stored 80/8781 entries predate the native service. Probe
                    // both standard ports; keep explicitly entered custom ports.
                    val probePort = daemonPort.takeUnless { it == NxCamera.PORT || it == 8781 }
                    CameraDiscovery.verify(ip, probePort, network = route)
                }
                currentCoroutineContext().ensureActive()
                if (connectionAttempt != attempt) return@launch
                when (verify) {
                    is CameraDiscovery.Result.Found -> {
                        val connectedPort = verify.daemonPort
                        val connectedCgiPort = if (discovered != null || cgiPort == NxCamera.CGI_PORT)
                            verify.cgiPort else cgiPort
                        NxApp.connect(ip, connectedPort, connectedCgiPort,
                            com.nxwa.nxconnect.net.CameraProfile.fromInfo(verify.info.raw), network = verify.network ?: route)
                        established = true
                        manual.portInput.setText(connectedPort.toString())
                        manual.cgiPortInput.setText(connectedCgiPort.toString())
                        getSharedPreferences("nx", MODE_PRIVATE).edit()
                            .putString("last_ip", ip)
                            .putInt("daemon_port", connectedPort)
                            .putInt("cgi_port", connectedCgiPort)
                            .apply()
                        com.nxwa.nxconnect.net.CameraWifi.ssid?.let { ssid ->
                            getSharedPreferences("camera_pair", 0).edit().putString("ssid", ssid)
                                .putString("model", verify.info.model).putString("ip", ip).putInt("port", connectedPort).apply()
                            home.btnPair.text = "连接"
                        }
                        val info = verify.info
                        val detail = listOfNotNull(
                            info.model?.let { "机型 $it" },
                            info.firmware?.let { "固件 $it" },
                            info.mac?.let { "MAC $it" },
                        ).joinToString(" · ")
                        status("已连接 $ip" + if (detail.isNotEmpty()) "（$detail）" else "", R.color.nx_ok)
                        refreshConnectedUi()

                        // The verified info already advertises the profile.
                        // Do not scan folders and decode thumbnails as part of
                        // connecting; those reads belong to opening the gallery.

                        // 若是 NFC 碰一碰触发，自动直达三星相册
                        if (pendingNfcLaunch) {
                            pendingNfcLaunch = false
                            OneUiTransitions.open(this@MainActivity, Intent(this@MainActivity, GalleryActivity::class.java))
                        }
                    }

                    is CameraDiscovery.Result.CameraWithoutDaemon -> {
                        status("$ip 可访问，但相机端遥控服务未响应", R.color.nx_warn)
                        toast(verify.hint)
                    }

                    is CameraDiscovery.Result.NotFound -> {
                        status("连接失败：${verify.reason}", R.color.nx_err)
                    }
                }
            } catch (_: TimeoutCancellationException) {
                status("识别相机服务超时。WLAN 连接已保留，可稍后重试", R.color.nx_warn)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                status("连接出错：${e.message}", R.color.nx_err)
            } finally {
                if (connectionAttempt == attempt) {
                    connectionJob = null
                    if (!established) pendingNfcLaunch = false
                    setBusy(false)
                }
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
