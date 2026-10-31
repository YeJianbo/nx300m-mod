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

import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.databinding.ActivityRemoteBinding
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.remote.KeyMap
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * 遥控面板：实时取景 + 对焦/快门 + 按键注入。
 *
 * 所有控制都走相机端 daemon 的 `/api/v1/` 接口（装了 mod 才有）。
 * 相机会在长时间无操作后休眠，所以遥控期间后台定时发 `inject_keep_alive`。
 */
class RemoteActivity : AppCompatActivity() {

    companion object {
        /** S1 按下后到 S2 之间的等待：让相机进入"半按对焦"状态 */
        private const val AF_PRESS_WAIT_MS = 450L

        /** S2 触发后的等待：等相机完成一次曝光与写卡 */
        private const val SHUTTER_WAIT_MS = 2500L

        /** 一次拍摄后的冷却：防止连点被相机理解成连拍 */
        private const val SHOT_COOLDOWN_MS = 1200L

        // 以下是 App 的调度策略，不是改写相机实测耗时。
        //
        // ★ 关键实测（用户 v0.13.4 截图）：间隔 400ms 时实测正好 2.5fps，
        //   而 OSD 只花 12~20ms、prep 1ms、sub 0ms —— 说明**帧率就是被这个限速器压住的**，
        //   相机当时还有余量（用户不开 OSD 时实测 6fps）。
        //
        //   限速器的由来是我和接手方为"省请求预算"设的，但后来已实测证明：
        //   ① OSD 对 liveview 的真实代价只有约 17%（A/B/A 同会话对照）；
        //   ② 请求预算不守恒（合计请求/秒随 OSD 加密反而上升）。
        //   所以这个限速器没有依据，改成"只防请求堆积"的 150ms 下限，
        //   让相机自己的出图速度决定帧率 —— 相机慢下来时它自然就慢，不会堆请求。

        /** 叠加模式下 liveview 的请求间隔：只防堆积，不人为压帧率 */
        private const val LIVE_OVERLAY_INTERVAL_MS = 150L

        /** 有按键待观察时 liveview 的请求间隔（与空闲相同，两路对称） */
        private const val LIVE_INPUT_INTERVAL_MS = 150L

        /** OSD 空闲轮询间隔：画面静止时它没什么可报的 */
        private const val OSD_IDLE_INTERVAL_MS = 2000L

        /** OSD 突发轮询间隔：按键后短时间内密集观察画面变化 */
        private const val OSD_ACTIVE_INTERVAL_MS = 300L

        private const val INPUT_OBSERVE_WINDOW_MS = 2500L
        private const val BACK_KEY = "Escape"
    }

    private lateinit var b: ActivityRemoteBinding
    private var lowQuality = false
    private var monitorExpanded = false
    private var smoothMonitor = true

    private enum class PreviewMode { OFF, LIVE, OVERLAY }
    private var previewMode = PreviewMode.OFF
    private var previewGeneration = 0
    private var previewSwitchJob: Job? = null
    private var osdJob: Job? = null
    private var changingPreview = false
    private var updatingOsdSwitch = false
    private lateinit var osdView: OsdView
    private lateinit var osdSwitch: SwitchCompat
    private lateinit var osdNavigation: LinearLayout
    private lateinit var osdFeedback: TextView
    private lateinit var osdProgress: ProgressBar
    private lateinit var markShootingButton: android.widget.Button
    private val osdWake = Channel<Unit>(Channel.CONFLATED)
    private var feedbackTicker: Job? = null
    private var liveStatusText = "取景未启动"
    private var osdStatusText = "等待首帧"

    private var previewNotice: String? = null
    private var overlayGeometryOk = true
    private var osdRequestStartedAt: Long? = null
    private var burstUntil = 0L

    // 哈希和变化块数不包含页面语义；这些是推测，不能伪装成相机状态读回。
    private enum class CameraUi { UNKNOWN, SHOOTING, MENU, PANEL }
    private var cameraUi = CameraUi.UNKNOWN
    private var menuDepth: Int? = null
    private var stateBasis = "未识别"
    private var shootingReference: String? = null
    private var inputFeedback = "等待相机屏幕"
    private var inputSequence = 0L
    private val activeInputs = mutableSetOf<Long>()
    private var pendingInput: PendingInput? = null

    private data class PendingInput(
        val id: Long,
        val generation: Int,
        val label: String,
        val startedAt: Long,
        var acknowledgedAt: Long? = null,
        var ambiguous: Boolean = false,
    )

    /** 拍摄进行中标志，用于把连点挡掉（连点会被相机当成连拍） */
    @Volatile
    private var shooting = false

    /** 静音快门开关：影响下一次拍摄时设的 ADJUSTSHUTTERTYPE */
    private val profile get() = NxApp.camera?.profile ?: com.nxwa.nxconnect.net.CameraProfile()
    private var silentMode = false

    // ---- B 门状态 ----
    /** 是否正在长曝光（S2 已按下未松开） */
    @Volatile
    private var bulbRunning = false

    /** 本次 B 门开始时刻，用于显示已曝光时长 */
    private var bulbStartedAt = 0L

    /** 计时刷新任务，停曝光时取消 */
    private var bulbTicker: Job? = null

    /**
     * B 门期间是否自动关掉**相机**屏幕。
     *
     * 注意这里和「手机屏幕常亮」是**两件事、两个设备**，不会冲突：
     *  - 本开关管的是相机那块 LCD（走 `/api/v1/lcd/off`），目的是省电、避免屏幕漏光影响长曝光
     *  - 手机屏幕常亮是 Window flag，管的是手机自己，让用户能看着计时
     * 两者同时生效正是想要的效果：手机亮着看计时，相机黑着省电。
     */
    private var bulbLcdOff = true

    /** 相机 LCD 当前是否被我们关掉了（用于判断需不需要恢复） */
    @Volatile
    private var cameraLcdOffed = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityRemoteBinding.inflate(layoutInflater)
        setContentView(b.root)

        if (!NxApp.isConnected) {
            toast("尚未连接相机")
            finish()
            return
        }

        b.toolbar.setNavigationOnClickListener { if (monitorExpanded) toggleMonitor() else finish() }
        b.toolbar.menu.add("全屏监看").apply {
            setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_IF_ROOM)
            setOnMenuItemClickListener { toggleMonitor(); title = if (monitorExpanded) "退出全屏" else "全屏监看"; true }
        }
        b.toolbar.menu.add("拍摄").setOnMenuItemClickListener { fireShutter(silentMode); true }
        b.toolbar.menu.add("锐利显示").apply {
            setOnMenuItemClickListener {
                smoothMonitor = !smoothMonitor
                osdView.setSmoothScaling(smoothMonitor)
                title = if (smoothMonitor) "锐利显示" else "平滑显示"
                true
            }
        }
        // 标题带上版本号：遥控页是最常用来验证行为的地方，一眼确认装的是哪个包
        b.toolbar.title = getString(R.string.remote_title)

        // ---- 状态栏适配：改用系统自动 inset（最稳、跨版本一致）----
        //
        // ★ 这里改过三次，记录清楚免得再犯：
        //   v1 `padTop(toolbar)`      -> 用户实测"顶栏被挡住了一点"
        //   v2 `marginTop(root)`      -> 更糟，顶栏被推到取景区下面（叠加了下移量）
        //   两次都是在没有实测依据的情况下改 inset 处理，所以改不对。
        //
        // 现在改用系统 inset 直接给**根布局**加内边距：
        //   - 内容是**正常流程布局**（标题栏在上、取景区在下），不需要边到边
        //   - 系统自动把内容放到状态栏下方，不依赖任何视图对 padding 的解释
        //   - 跨 Android 版本行为一致（本项目 compileSdk 35 / Android 15 强制边到边，
        //     手动处理 inset 在各设备上差异大）
        // 代价：放弃"内容延伸到状态栏下方"的边到边观感 —— 但工具栏本来就该在状态栏下面，
        // 所以这个代价对当前界面结构没有实际损失。
        // 深色状态栏外观（setup 只负责边到边标志与图标亮暗，不再用于加内边距）
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

        // 遥控期间让**手机**屏幕保持常亮：长曝光时用户要盯着计时，
        // 息屏会让人以为 App 卡住了。这与「B 门关相机屏幕」互不影响（见 bulbLcdOff 注释）。
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // 恢复用户对「B 门时是否关相机屏」的选择
        bulbLcdOff = getSharedPreferences("nx", MODE_PRIVATE).getBoolean("bulb_lcd_off", true)
        updateBulbLcdButton()

        buildOsdControls()
        osdView.configure(profile.geometry)
        b.liveView.osdGeometry = profile.geometry
        b.btnSilentToggle.isEnabled = profile.silentShutter
        b.btnBulbLcd.isEnabled = profile.lcdControl
        if (!profile.lcdControl) bulbLcdOff = false
        updateBulbLcdButton()
        b.liveView.onViewportChanged = { rect, compatible ->
            overlayGeometryOk = compatible
            osdView.setViewport(rect, compatible)
            renderPreviewStatus()
        }
        b.liveView.onStatus = { text ->
            liveStatusText = text
            renderPreviewStatus()
        }
        b.liveView.onStopped = { reason ->
            liveStatusText = "$reason（最后画面已保留）"
            if (previewMode == PreviewMode.LIVE) previewMode = PreviewMode.OFF
            // OSD 可以继续显示菜单；底图故障不连带清空 OSD 或增量哈希。
            updatePreviewUi()
            renderPreviewStatus()
        }

        b.btnLive.setOnClickListener { toggleLive() }
        b.btnQuality.setOnClickListener {
            lowQuality = !lowQuality
            configureLiveView()
            updatePreviewUi()
            NxApp.log("取景画质切换为 " + if (lowQuality) "低画质" else "高画质")
        }

        b.btnShutter.setOnClickListener { fireShutter(silent = silentMode) }

        // 静音快门改成开关：原来的「对焦」按钮没有意义了（快门自己就带半按对焦，
        // 用户实测单独按对焦也没反应），把这个位置让给更有用的静音开关。
        b.btnSilentToggle.setOnClickListener {
            silentMode = !silentMode
            b.btnSilentToggle.setText(if (silentMode) R.string.silent_on else R.string.silent_off)
            NxApp.log("静音快门：" + if (silentMode) "开" else "关")
        }

        b.btnPlayback.setOnClickListener { sendKey("播放/拍摄切换", KeyMap.PB) }

        // 原来这里是一个 16 键的折叠面板（"更多按键"），实际几乎没人用，
        // 留着只占地方。改成三个真正常用的功能键直接铺在界面上。
        b.btnKeysToggle.setOnClickListener { toggleKeyPanel() }
        b.btnBulbLcd.setOnClickListener { toggleBulbLcdOff() }

        b.btnBulb.setOnClickListener {
            if (bulbRunning) stopBulb() else startBulb()
        }

        buildKeyPanel()
        unifyToggleRow()

        // B 门：点一次开始长曝光，再点一次结束
        b.btnBulb.setOnClickListener {
            if (bulbRunning) stopBulb() else startBulb()
        }

        loadStatus()
        if (profile.keepAlive) startKeepAlive()
    }

    /** 两个同尺寸兄弟 View；不修改布局资源，不遮挡现有预览按钮。 */
    private fun buildOsdControls() {
        val previewContainer = b.liveView.parent as FrameLayout
        osdView = OsdView(this).apply {
            visibility = View.INVISIBLE
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
        }
        previewContainer.addView(osdView, previewContainer.indexOfChild(b.liveView) + 1)
        osdSwitch = SwitchCompat(this).apply {
            text = "相机屏幕"
            contentDescription = "把相机界面叠加在实时取景上"
            setTextColor(ContextCompat.getColor(this@RemoteActivity, R.color.nx_on_surface))
            setPadding(dp(8), dp(4), dp(8), dp(4))
        }
        osdNavigation = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val keys = listOf(KeyMap.UP, KeyMap.DOWN, KeyMap.LEFT, KeyMap.RIGHT,
            KeyMap.OK, KeyMap.MENU, KeyMap.FN)
        val definitions = keys.map { key -> KeyMap.GROUP_NAV.first { it.keysym == key } } +
            KeyMap.KeyDef("返回", BACK_KEY)
        fillRows(osdNavigation, definitions, 4)

        val feedbackRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = android.view.Gravity.CENTER_VERTICAL
            setPadding(dp(4), dp(6), dp(4), dp(6))
        }
        osdProgress = ProgressBar(this).apply {
            isIndeterminate = true
            visibility = View.INVISIBLE
        }
        osdFeedback = TextView(this).apply {
            setTextColor(ContextCompat.getColor(this@RemoteActivity, R.color.nx_on_surface))
            textSize = 12f
            setPadding(dp(8), 0, 0, 0)
        }
        feedbackRow.addView(osdProgress, LinearLayout.LayoutParams(dp(20), dp(20)))
        feedbackRow.addView(osdFeedback, LinearLayout.LayoutParams(0,
            ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        osdNavigation.addView(feedbackRow)
        markShootingButton = android.widget.Button(this).apply {
            text = "标记当前为拍摄界面"
            isAllCaps = false
            textSize = 12f
            setOnClickListener { markShootingReference() }
        }
        applyKeyButtonLook(markShootingButton,
            ContextCompat.getColor(this, R.color.nx_outline_variant), R.color.nx_on_surface)
        osdNavigation.addView(markShootingButton, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        b.controlPanel.addView(osdSwitch, 0, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        b.controlPanel.addView(osdNavigation, 1, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        osdSwitch.setOnCheckedChangeListener { _, checked ->
            if (!updatingOsdSwitch) {
                // 关 OSD 后保留取景；「停止取景」停止两路。
                selectPreview(if (checked) PreviewMode.OVERLAY else if (profile.sensorLiveview) PreviewMode.LIVE else PreviewMode.OFF)
            }
        }
        updatePreviewUi()
    }

    private fun toggleMonitor() {
        monitorExpanded = !monitorExpanded
        b.controlScroll.visibility = if (monitorExpanded) View.GONE else View.VISIBLE
        b.toolbar.subtitle = if (monitorExpanded) "菜单中可触发快门" else null
    }

    private fun toggleLive() {
        if (profile.screenOnly) {
            selectPreview(if (previewMode == PreviewMode.OFF) PreviewMode.OVERLAY else PreviewMode.OFF)
            return
        }
        if (previewMode == PreviewMode.OVERLAY && !b.liveView.isRunning) {
            // 只重启失败的底图，不能因此重置正常 OSD 的画布和哈希。
            liveStatusText = "取景重试中…"
            configureLiveView()
            b.liveView.start()
            updatePreviewUi()
            renderPreviewStatus()
            return
        }
        selectPreview(if (previewMode == PreviewMode.OFF) PreviewMode.LIVE else PreviewMode.OFF)
    }

    private fun configureLiveView() {
        val overlay = previewMode == PreviewMode.OVERLAY
        b.liveView.lowQuality = lowQuality
        b.liveView.minFrameIntervalMs = when {
            !overlay -> 40L
            pendingInput != null -> LIVE_INPUT_INTERVAL_MS
            else -> LIVE_OVERLAY_INTERVAL_MS
        }
    }

    private fun updatePreviewUi() {
        if (!::osdView.isInitialized) return
        val overlay = previewMode == PreviewMode.OVERLAY
        updatingOsdSwitch = true
        try {
            osdSwitch.isChecked = overlay
        } finally {
            updatingOsdSwitch = false
        }
        osdView.visibility = if (overlay) View.VISIBLE else View.INVISIBLE
        b.liveView.visibility = View.VISIBLE
        osdNavigation.visibility = if (overlay) View.VISIBLE else View.GONE
        osdSwitch.isEnabled = profile.screenMirroring && !changingPreview
        b.btnLive.isEnabled = !changingPreview
        b.btnQuality.isEnabled = profile.sensorLiveview && !changingPreview
        b.btnQuality.visibility = if (profile.sensorLiveview) View.VISIBLE else View.GONE
        b.btnLive.visibility = if (profile.screenOnly) View.GONE else View.VISIBLE
        osdSwitch.contentDescription = if (profile.screenOnly) "显示相机屏幕镜像" else "把相机界面叠加在实时取景上"
        b.btnQuality.setText(if (lowQuality) R.string.quality_lq else R.string.quality_hq)
        b.btnLive.setText(if (previewMode != PreviewMode.OFF && b.liveView.isRunning)
            R.string.liveview_stop else R.string.liveview_start)
        if (profile.screenOnly) {
            b.btnLive.text = if (overlay) "停止屏幕镜像" else "开启屏幕镜像"
        } else if (overlay && !b.liveView.isRunning && !changingPreview) b.btnLive.text = "重试取景"
    }

    private fun renderPreviewStatus() {
        if (!::osdView.isInitialized) return
        b.liveStatus.visibility = View.VISIBLE
        b.liveStatus.text = when {
            profile.screenOnly && previewMode == PreviewMode.OFF && previewNotice == null -> "屏幕镜像未开启"
            profile.screenOnly && previewMode == PreviewMode.OVERLAY -> "${profile.geometry.width}×${profile.geometry.height} · 屏幕镜像"
            previewMode == PreviewMode.OVERLAY && !overlayGeometryOk ->
                "$liveStatusText\n取景比例非 3:2，叠加暂不可用"
            previewMode == PreviewMode.OVERLAY -> {
                // 把两路各自的真实帧率和 OSD 三段耗时都摆出来，用于判断
                // 帧率是"被限速器卡住"还是"真的慢"。
                val lv = String.format(java.util.Locale.US, "%.1f", b.liveView.liveFps)
                "$liveStatusText\nOSD · $osdStatusText\n两路 · 取景 ${lv}fps" +
                    " / 间隔 ${b.liveView.minFrameIntervalMs}ms"
            }
            previewNotice != null -> "$liveStatusText\n$previewNotice"
            else -> liveStatusText
        }
    }

    /** 只等待本路旧请求；LIVE ⇄ OVERLAY 不停止正常的取景流。 */
    private fun selectPreview(target: PreviewMode) {
        val generation = ++previewGeneration
        previewSwitchJob?.cancel()
        val previousOsd = osdJob
        previousOsd?.cancel()
        feedbackTicker?.cancel()
        previewMode = target
        previewNotice = null
        changingPreview = true
        resetUiTracking()
        configureLiveView()
        if (target == PreviewMode.OFF) b.liveView.stop()
        updatePreviewUi()
        previewSwitchJob = lifecycleScope.launch {
            try {
                if (target == PreviewMode.OFF) b.liveView.stopAndJoin()
                previousOsd?.join()
                coroutineContext.ensureActive()
                if (generation != previewGeneration) return@launch
                if (osdJob === previousOsd) osdJob = null
                if (target != PreviewMode.OFF && profile.sensorLiveview) {
                    if (!b.liveView.isRunning) liveStatusText = "取景启动中…"
                    b.liveView.start() // 正在运行时直接返回，内部保证旧取景请求先退出。
                }
                when (target) {
                    PreviewMode.OVERLAY -> {
                        val cam = NxApp.camera ?: error("相机未连接")
                        osdView.reset()
                        osdStatusText = "等待首帧"
                        startOsdLoop(cam, generation)
                        feedbackTicker = lifecycleScope.launch {
                            while (isActive && generation == previewGeneration) {
                                refreshOsdFeedback()
                                delay(100L)
                            }
                        }
                    }
                    PreviewMode.LIVE -> Unit
                    PreviewMode.OFF -> liveStatusText = getString(R.string.liveview_stopped_hint)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                if (generation == previewGeneration) failOsd(t)
            } finally {
                if (generation == previewGeneration) {
                    changingPreview = false
                    updatePreviewUi()
                    renderPreviewStatus()
                }
            }
        }
    }

    private fun startOsdLoop(cam: NxCamera, generation: Int) {
        osdJob = lifecycleScope.launch {
            var failures = 0
            try {
                // 底层 liveview 循环本身负责激活出流，不再额外发第三路预热请求。
                while (isActive && generation == previewGeneration) {
                    check(NxApp.camera === cam) { "相机连接已改变，请重新开启相机屏幕" }
                    val started = SystemClock.elapsedRealtime()
                    val requestInputSequence = inputSequence
                    val requestedAfterKeys = activeInputs.isEmpty()
                    val hashs = osdView.hashsParam()
                    osdRequestStartedAt = started
                    val raw = try {
                        withContext(Dispatchers.IO) { cam.osd(hashs) }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: IOException) {
                        failures++
                        if (failures >= 6) throw e
                        osdStatusText = "请求重试 $failures/6，保留上一帧"
                        renderPreviewStatus()
                        NxApp.log("OSD 请求重试 $failures/6：${e.message}")
                        delay(500L)
                        continue
                    } finally {
                        osdRequestStartedAt = null
                    }
                    coroutineContext.ensureActive()
                    if (generation != previewGeneration) return@launch
                    check(NxApp.camera === cam) { "相机连接已改变，请重新开启相机屏幕" }
                    val requestMs = SystemClock.elapsedRealtime() - started
                    failures = 0
                    if (raw.isEmpty()) {
                        osdStatusText = "暂不可用，等待相机出流"
                        renderPreviewStatus()
                        // 0 字节 != 合法的 12 字节零块帧；两者都不清空画布/哈希。
                        waitForOsdPoll(started, unavailable = true)
                        continue
                    }
                    val prepared = withContext(Dispatchers.Default) {
                        OsdView.prepareFrame(OsdView.parseResponse(raw, cam.profile.geometry),
                            cam.profile.geometry, blackKey = !cam.profile.screenOnly)
                    }
                    val prepMs = SystemClock.elapsedRealtime() - started - requestMs
                    val submitStart = SystemClock.elapsedRealtime()
                    osdView.submitPrepared(prepared)
                    val submitMs = SystemClock.elapsedRealtime() - submitStart
                    val count = prepared.changedBlockCount
                    // ★ 诊断用：把三段耗时都显示出来，定位 App 侧瓶颈。
                    //   net = HTTP 往返（相机端实测中位约 200ms）
                    //   prep = 键控 + 块拷贝（在 Dispatchers.Default）
                    //   submit = 主线程合成 + 失效
                    //   若 net+prep+submit 远小于实际帧间隔，说明瓶颈在渲染合成而非这些阶段。
                    osdStatusText = if (count == 0) "无变化 · net ${requestMs}ms prep ${prepMs}ms sub ${submitMs}ms"
                        else "更新 $count 块 · net ${requestMs}ms prep ${prepMs}ms sub ${submitMs}ms"
                    // 诊断用，放在状态串里不是给用户看的重点；详见 renderPreviewStatus 的拼装。
                    observeOsdFrame(count, requestInputSequence, requestedAfterKeys)
                    renderPreviewStatus()
                    refreshOsdFeedback()
                    waitForOsdPoll(started)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                if (generation == previewGeneration) failOsd(t)
            }
        }
    }

    /** 输入只唤醒延时，不取消 HTTP；按请求起点节流，慢帧不会再额外加 200ms。 */
    private suspend fun waitForOsdPoll(started: Long, unavailable: Boolean = false) {
        while (true) {
            val now = SystemClock.elapsedRealtime()
            val interval = if (!unavailable && (pendingInput != null || now < burstUntil))
                OSD_ACTIVE_INTERVAL_MS else OSD_IDLE_INTERVAL_MS
            val wait = interval - (now - started)
            if (wait <= 0L) return
            withTimeoutOrNull(wait) { osdWake.receive() }
        }
    }

    private fun failOsd(t: Throwable) {
        NxApp.log("OSD 已停止：${t.javaClass.simpleName}: ${t.message}")
        try {
            com.nxwa.nxconnect.CrashLog.record(NxApp.ctx(), "OSD 叠加层", t)
        } catch (ignored: Throwable) {
            // 原异常已上报，日志失败不能再次导致崩溃。
        }
        feedbackTicker?.cancel()
        previewMode = if (b.liveView.isRunning) PreviewMode.LIVE else PreviewMode.OFF
        previewNotice = "相机屏幕已停止：${t.message ?: t.javaClass.simpleName}"
        resetUiTracking()
        configureLiveView()
        updatePreviewUi()
        renderPreviewStatus()
        toast(previewNotice ?: "相机屏幕已停止")
    }

    private fun stopPreviewForLifecycle() {
        ++previewGeneration
        previewSwitchJob?.cancel()
        osdJob?.cancel()
        feedbackTicker?.cancel()
        b.liveView.stop()
        previewMode = PreviewMode.OFF
        changingPreview = false
        previewNotice = null
        resetUiTracking()
        updatePreviewUi()
        liveStatusText = getString(R.string.liveview_stopped_hint)
        renderPreviewStatus()
    }

    private fun resetUiTracking() {
        pendingInput = null
        activeInputs.clear()
        cameraUi = CameraUi.UNKNOWN
        menuDepth = null
        stateBasis = "未识别"
        shootingReference = null
        inputFeedback = "首帧可能需要约 0.6–0.8 秒"
        burstUntil = 0L
        osdRequestStartedAt = null
        while (osdWake.tryReceive().isSuccess) { /* 丢弃上一会话的唤醒事件。 */ }
    }

    /** 只观察现有发送过程，不加锁、不等待、不改变 sendKey/holdKey 的调用顺序。 */
    private fun beginUiInput(label: String, keysym: String): PendingInput? {
        if (previewMode != PreviewMode.OVERLAY) return null
        val now = SystemClock.elapsedRealtime()
        val overlapping = activeInputs.isNotEmpty()
        val input = PendingInput(++inputSequence, previewGeneration, label, now,
            ambiguous = overlapping)
        activeInputs.add(input.id)
        pendingInput = input
        when (keysym) {
            // 已验证 Menu 会打开菜单，但没有验证它总会回到某个固定层级。
            KeyMap.MENU -> { cameraUi = CameraUi.MENU; menuDepth = null }
            KeyMap.FN -> { cameraUi = CameraUi.PANEL; menuDepth = null }
            KeyMap.OK -> {
                // OK 可能进入子菜单，也可能只应用数值；不能盲目 depth++。
                if (cameraUi != CameraUi.MENU) cameraUi = CameraUi.UNKNOWN
                menuDepth = null
            }
            BACK_KEY, KeyMap.PB -> { cameraUi = CameraUi.UNKNOWN; menuDepth = null }
            KeyMap.UP, KeyMap.DOWN, KeyMap.LEFT, KeyMap.RIGHT -> {
                // 拍摄界面上的方向键也可能打开功能面板，不能沿用“拍摄”判断。
                if (cameraUi == CameraUi.SHOOTING) {
                    cameraUi = CameraUi.UNKNOWN
                    menuDepth = null
                }
            }
            else -> { cameraUi = CameraUi.UNKNOWN; menuDepth = null }
        }
        if (overlapping) { cameraUi = CameraUi.UNKNOWN; menuDepth = null }
        stateBasis = "按键推测，待画面"
        burstUntil = now + INPUT_OBSERVE_WINDOW_MS
        osdWake.trySend(Unit)
        configureLiveView()
        refreshOsdFeedback()
        return input
    }

    private fun finishUiInput(input: PendingInput?, succeeded: Boolean) {
        if (input == null || input.generation != previewGeneration ||
            previewMode != PreviewMode.OVERLAY) return
        activeInputs.remove(input.id)
        val pending = pendingInput
        if (pending == null || pending.id != input.id) {
            // 并行按键返回顺序不可靠，不能据此拼出菜单层级。
            pending?.ambiguous = true
            cameraUi = CameraUi.UNKNOWN
            menuDepth = null
            stateBasis = "有重叠按键，层级未知"
        } else if (!succeeded) {
            pendingInput = null
            cameraUi = CameraUi.UNKNOWN
            menuDepth = null
            stateBasis = "按键请求失败，结果未知"
            inputFeedback = "“${input.label}”请求失败，未自动重发"
        } else {
            // 只表示现有 HTTP 调用已返回，绝不是相机已执行的确认。
            pending.acknowledgedAt = SystemClock.elapsedRealtime()
            pending.ambiguous = pending.ambiguous || activeInputs.isNotEmpty()
        }
        burstUntil = SystemClock.elapsedRealtime() + INPUT_OBSERVE_WINDOW_MS
        osdWake.trySend(Unit)
        configureLiveView()
        refreshOsdFeedback()
    }

    private fun observeOsdFrame(changed: Int, requestSequence: Long, requestedAfterKeys: Boolean) {
        val current = requestedAfterKeys && activeInputs.isEmpty() && requestSequence == inputSequence
        val pending = pendingInput
        if (pending != null && current && pending.acknowledgedAt != null && changed > 0) {
            pendingInput = null
            inputFeedback = "已收到“${pending.label}”后的变化画面（$changed 块），请核对结果"
            stateBasis = "按键推测；后续画面已更新"
            if (pending.ambiguous) {
                cameraUi = CameraUi.UNKNOWN
                menuDepth = null
                stateBasis = "有重叠按键，层级未知"
            }
        } else if (pending == null && changed >= 54) {
            // 半屏只是“可能换页”的启发式；外部操作/参数变化也会大量重绘。
            cameraUi = CameraUi.UNKNOWN
            menuDepth = null
            stateBasis = "画面大幅变化，需重新识别"
        }
        // 唯一自动恢复：与用户刚才标记的完整拍摄画面服务端哈希逐项一致。
        // 不生成图像哈希，不把全黑帧、块数或 OK 次数当作页面分类器。
        if (current && pendingInput == null && changed > 0 &&
            osdView.hasCompleteFrame && osdView.hasVisibleContent &&
            shootingReference != null && osdView.hashsParam() == shootingReference) {
            cameraUi = CameraUi.SHOOTING
            menuDepth = 0
            stateBasis = "与手动参考一致"
        }
        configureLiveView()
    }

    private fun markShootingReference() {
        if (previewMode != PreviewMode.OVERLAY || pendingInput != null ||
            activeInputs.isNotEmpty() || !overlayGeometryOk ||
            !osdView.hasCompleteFrame || !osdView.hasVisibleContent) return
        shootingReference = osdView.hashsParam()
        cameraUi = CameraUi.SHOOTING
        menuDepth = 0
        stateBasis = "手动标记"
        inputFeedback = "已记住当前拍摄画面；参数变化后可能需要重新标记"
        refreshOsdFeedback()
    }

    private fun refreshOsdFeedback() {
        if (previewMode != PreviewMode.OVERLAY || !::osdFeedback.isInitialized) return
        val now = SystemClock.elapsedRealtime()
        val pending = pendingInput
        if (pending != null) {
            val elapsed = String.format(java.util.Locale.US, "%.1f", (now - pending.startedAt) / 1000.0)
            val acknowledged = pending.acknowledgedAt
            if (acknowledged != null && now - acknowledged >= INPUT_OBSERVE_WINDOW_MS) {
                pendingInput = null
                cameraUi = CameraUi.UNKNOWN
                menuDepth = null
                stateBasis = "操作结果未确认"
                inputFeedback = "“${pending.label}”后尚未观察到变化；没有自动重发"
                configureLiveView()
            } else {
                inputFeedback = if (acknowledged == null) "正在发送“${pending.label}” · ${elapsed}s"
                    else "等待“${pending.label}”后的画面 · ${elapsed}s"
            }
        }
        val state = when (cameraUi) {
            CameraUi.SHOOTING -> "拍摄界面"
            CameraUi.MENU -> menuDepth?.let { "菜单第 $it 层（估计）" } ?: "菜单（估计），层级未知"
            CameraUi.PANEL -> "参数面板（估计）"
            CameraUi.UNKNOWN -> "当前界面 / 层级未知"
        }
        val inFlight = osdRequestStartedAt
        val loading = pendingInput != null || !osdView.hasCompleteFrame ||
            (inFlight != null && now - inFlight >= 200L)
        osdProgress.visibility = if (loading) View.VISIBLE else View.INVISIBLE
        val visibleFeedback = if (profile.screenOnly && inputFeedback.startsWith("首帧可能")) "" else inputFeedback
        val feedback = listOf("$state · $stateBasis", visibleFeedback).filter { it.isNotBlank() }.joinToString("\n")
        if (osdFeedback.text.toString() != feedback) osdFeedback.text = feedback
        markShootingButton.isEnabled = pendingInput == null && activeInputs.isEmpty() &&
            overlayGeometryOk && osdView.hasCompleteFrame && osdView.hasVisibleContent
    }

    /**
     * 触发一次拍摄。
     *
     * 真机实测（2026-10-14，NX500/1.12，脚本化逐种时序验证）：
     *
     * | 时序 | 结果 |
     * |---|---|
     * | 仅 S2 单击 | 没反应 |
     * | S1 短按并松手 → S2 | 没反应 |
     * | **S1 按住不放 + S2 单击** | **✅ 拍到** |
     * | 仅 S2 按下 200ms | 没反应 |
     * | S1 长按 1 秒松手 → S2 | 没反应 |
     *
     * ★ 关键：**S1 必须与 S2 重叠**。这是机械快门两段行程的物理语义——
     *   手指同时压着半按和全按两段；一旦松开 S1，相机就退回待机，随后的 S2 无效。
     *   第一版"松 S1 再发 S2"因此只会对焦，这正是用户实测到的现象。
     *
     * 另注：相机一次拍摄会写 **2 个文件**（`SAM_xxxx.JPG` + `SAM_xxxx.SRW`），
     * 所以"文件数 +2"就是单张，不是连拍。
     *
     * @param silent 先设静音（电子）快门类型
     * @param skipAf true 时跳过 S1（实测相机会因缺少半按而对不上焦，仅作备用）
     */
    private fun fireShutter(silent: Boolean, skipAf: Boolean = false) {
        if (shooting) {
            NxApp.log("上一次拍摄还在进行，忽略本次点击（避免变成连拍）")
            return
        }
        shooting = true
        b.btnShutter.isEnabled = false
        b.btnShutter.text = "正在触发快门…"
        b.btnShutter.performHapticFeedback(android.view.HapticFeedbackConstants.VIRTUAL_KEY)
        lifecycleScope.launch {
            val cam = NxApp.camera ?: run { shooting = false; b.btnShutter.isEnabled = true; b.btnShutter.setText(R.string.shutter); return@launch }
            try {
                // 1) 设快门类型（仅 NX500 有意义）
                try {
                    if (cam.profile.silentShutter) withContext(Dispatchers.IO) { cam.shutter(silent = silent) }
                } catch (e: Exception) {
                    NxApp.log("设置快门类型失败（继续拍摄）：${e.message}")
                }

                withContext(Dispatchers.IO) {
                    // 2) 半按：按下后**保持不放**
                    if (!skipAf) {
                        cam.keyDown(KeyMap.S1)
                        delay(AF_PRESS_WAIT_MS)
                    }

                    // 3) 在半按保持状态下触发全按（单次点击语义，不要按太久，
                    //    否则会被相机当成连续全按 → 连拍）
                    cam.sendKey(KeyMap.S2)
                    delay(SHUTTER_WAIT_MS)

                    // 半按在 finally 中释放，网络失败或页面关闭也执行。
                }

                NxApp.log(
                    "拍摄按键已发送：S1按住→S2单击→松S1" + if (silent) "（静音快门类型）" else "",
                )
                toast("已发送拍摄按键")
            } catch (e: Exception) {
                toast("拍摄失败：${e.message}")
                NxApp.log("拍摄失败：${e.message}")
            } finally {
                withContext(kotlinx.coroutines.NonCancellable + Dispatchers.IO) {
                    if (!skipAf) try { cam.keyUp(KeyMap.S1) } catch (e: Exception) { NxApp.log("释放半按失败：${e.message}") }
                }
                shooting = false
                b.btnShutter.isEnabled = true
                b.btnShutter.setText(R.string.shutter)
            }
        }
    }

    private fun sendKey(label: String, keysym: String) {
        lifecycleScope.launch {
            val cam = NxApp.camera ?: return@launch
            val input = beginUiInput(label, keysym)
            try {
                withContext(Dispatchers.IO) { cam.sendKey(keysym) }
                finishUiInput(input, succeeded = true)
                NxApp.log("按键 $label → $keysym")
            } catch (e: CancellationException) {
                finishUiInput(input, succeeded = false)
                throw e
            } catch (e: Exception) {
                finishUiInput(input, succeeded = false)
                toast("按键失败：${e.message}")
            }
        }
    }

    // ==================== A/B/C 组按键面板 ====================

    /**
     * 按键面板的三个网格。
     *
     * 为什么不走 `b.xxx`：面板是用 `<include layout="@layout/view_remote_keys">` 嵌进来的，
     * 而 ViewBinding 对 include **不会**把子布局里的 id 展开进父绑定
     * （实测父绑定里根本没有 keyPanel/grid* 字段）。所以这里自己 findViewById 一次，
     * 用 lazy 缓存住。
     */
    private val keyPanel: View by lazy { findViewById(R.id.keyPanel) }
    private val gridExposure: LinearLayout by lazy { findViewById(R.id.gridExposure) }
    private val gridMode: LinearLayout by lazy { findViewById(R.id.gridMode) }
    private val gridNav: LinearLayout by lazy { findViewById(R.id.gridNav) }

    private fun toggleKeyPanel() {
        val show = keyPanel.visibility != View.VISIBLE
        keyPanel.visibility = if (show) View.VISIBLE else View.GONE
        b.btnKeysToggle.setText(if (show) R.string.keys_collapse else R.string.keys_expand)
    }

    /** 生成三组按键。布局在 view_remote_keys.xml，按钮在这里动态建（要支持按住语义）。 */
    private fun buildKeyPanel() {
        fillRows(gridExposure, KeyMap.GROUP_EXPOSURE, columns = 2)
        fillRows(gridMode, KeyMap.GROUP_MODE, columns = 3)
        fillRows(gridNav, KeyMap.GROUP_NAV, columns = 4)
    }

    /**
     * 按固定列数把按键铺成多行。
     *
     * ★ 这里**故意不用 GridLayout**：它的 `weight` 语义与 LinearLayout 不同
     *   （LinearLayout 的 weight 会先给 0 宽的子视图分配宽度，GridLayout 是按剩余空间比例分），
     *   配合 `width = 0` 的子视图会让按钮宽度塌成 0 —— 表现出来就是"分组标题在、按钮全不见"。
     *   改成手写「每行一个水平 LinearLayout + 子按钮 weight=1」，行为完全可预测。
     *
     * @param columns 每行放几个
     */
    private fun fillRows(container: LinearLayout, defs: List<KeyMap.KeyDef>, columns: Int) {
        container.removeAllViews()
        if (columns <= 0) return
        val gap = dp(4)

        defs.chunked(columns).forEach { rowDefs ->
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            }
            rowDefs.forEach { def ->
                val btn = buildKeyButton(def)
                val lp = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                lp.setMargins(gap, gap, gap, gap)
                btn.layoutParams = lp
                row.addView(btn)
            }
            // 补齐空位，保证最后一行不足时按钮宽度与其他行一致
            repeat(columns - rowDefs.size) {
                row.addView(View(this).apply {
                    layoutParams = LinearLayout.LayoutParams(0, 1, 1f)
                })
            }
            container.addView(row)
        }
    }

    private fun buildKeyButton(def: KeyMap.KeyDef): android.widget.Button {
        // 「按住生效」的键：主色描边 + 主色文字（主色本身够亮，对比度没问题）
        // 其余键：弱描边 + 亮色文字。两者一眼能区分操作方式。
        val isHold = def.hold
        val stroke = ContextCompat.getColor(
            this,
            if (isHold) R.color.nx_primary else R.color.nx_outline_variant,
        )
        val btn = android.widget.Button(this)
        btn.text = def.label
        btn.isAllCaps = false
        btn.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 12f)
        btn.setPadding(dp(2), dp(8), dp(2), dp(8))
        btn.minimumWidth = 0
        btn.minimumHeight = dp(44)
        btn.maxLines = 2
        btn.ellipsize = android.text.TextUtils.TruncateAt.END
        applyKeyButtonLook(btn, stroke, if (isHold) R.color.nx_primary else R.color.nx_on_surface)

        if (def.hold) {
            // 「按住生效」的键：按下/松开映射到 keydown/keyup，
            // 这样才和机身上"按住 EV 再转拨盘"的手感一致；单击会立刻松开，配合转盘无效。
            btn.setOnTouchListener { v, ev ->
                when (ev.actionMasked) {
                    MotionEvent.ACTION_DOWN -> {
                        holdKey(def, down = true)
                        v.isPressed = true
                        true
                    }

                    MotionEvent.ACTION_UP,
                    MotionEvent.ACTION_CANCEL,
                    -> {
                        holdKey(def, down = false)
                        v.isPressed = false
                        true
                    }

                    else -> false
                }
            }
        } else {
            if (def.keysym == KeyMap.ISO && profile.hasBodySettings) {
                btn.text = "机身设置"
                btn.setOnClickListener {
                    val camera = NxApp.camera ?: return@setOnClickListener
                    ExposureSettingsDialog(this, camera) { sendKey(def.label, def.keysym) }.show()
                }
            } else btn.setOnClickListener { sendKey(def.label, def.keysym) }
        }
        return btn
    }

    /**
     * 给代码里动态创建的按钮统一外观。
     *
     * ★ 必须用**状态列表**（ColorStateList）而不是单个颜色。
     *   踩过的坑：主题的 `buttonStyle` 指向 Material3 填充按钮，它的 `?attr/colorOnSurface`
     *   是**半透明**的灰（设计上配合填充底色使用）。我把它直接当文字色画在
     *   "深色底 + 仅描边"的按钮上，结果所有按键看起来都是灰的、像被禁用了。
     *   现在改成：正常态用高对比亮色，按下态变暗，禁用态才用灰。
     */
    private fun applyKeyButtonLook(btn: android.widget.Button, stroke: Int, textColorRes: Int) {
        val normal = ContextCompat.getColor(this, textColorRes)
        val pressed = ContextCompat.getColor(this, R.color.nx_text_dim)
        val disabled = ContextCompat.getColor(this, R.color.nx_text_note)
        val fill = ContextCompat.getColor(this, R.color.nx_surface_container_high)
        val fillPressed = ContextCompat.getColor(this, R.color.nx_surface_container_highest)

        btn.setTextColor(
            android.content.res.ColorStateList(
                arrayOf(
                    intArrayOf(-android.R.attr.state_enabled),   // 禁用
                    intArrayOf(android.R.attr.state_pressed),      // 按下
                    intArrayOf(),                                  // 正常
                ),
                intArrayOf(disabled, pressed, normal),
            ),
        )
        btn.background = android.graphics.drawable.StateListDrawable().apply {
            addState(
                intArrayOf(android.R.attr.state_pressed),
                roundedFill(fillPressed, stroke),
            )
            addState(intArrayOf(), roundedFill(fill, stroke))
        }
    }

    private fun roundedFill(fill: Int, stroke: Int): android.graphics.drawable.GradientDrawable =
        android.graphics.drawable.GradientDrawable().apply {
            cornerRadius = dp(12).toFloat()
            setColor(fill)
            setStroke(dp(1), stroke)
        }

    /**
     * 把「静音 / 播放 / 收起面板」这一行的三个按钮统一成等高单行。
     *
     * 它们原来用 XML 的 `NxButton.Outlined`（minHeight 52dp），文字一长就折成两行，
     * 把整行撑高、三个按钮高度参差 —— 用户反馈的"原本三个按钮歪了"就是这个。
     * 这里统一改小字号 + 单行省略号，保证一行三个永远对齐。
     */
    private fun unifyToggleRow() {
        val row = listOf(b.btnSilentToggle, b.btnPlayback, b.btnKeysToggle)
        val targetH = dp(48)
        row.forEach { btn ->
            btn.layoutParams = (btn.layoutParams as LinearLayout.LayoutParams).apply {
                height = targetH
            }
            btn.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 12f)
            btn.maxLines = 1
            btn.setSingleLine(true)
            btn.ellipsize = android.text.TextUtils.TruncateAt.END
            btn.setPadding(dp(4), 0, dp(4), 0)
            btn.isAllCaps = false
            // 用与按键面板一致的配色，整页观感统一
            val accent = ContextCompat.getColor(this, R.color.nx_outline_variant)
            val normal = ContextCompat.getColor(this, R.color.nx_on_surface)
            val pressed = ContextCompat.getColor(this, R.color.nx_text_dim)
            btn.setTextColor(
                android.content.res.ColorStateList(
                    arrayOf(
                        intArrayOf(-android.R.attr.state_enabled),
                        intArrayOf(android.R.attr.state_pressed),
                        intArrayOf(),
                    ),
                    intArrayOf(
                        ContextCompat.getColor(this, R.color.nx_text_note),
                        pressed,
                        normal,
                    ),
                ),
            )
            btn.background = android.graphics.drawable.StateListDrawable().apply {
                addState(
                    intArrayOf(android.R.attr.state_pressed),
                    roundedFill(
                        ContextCompat.getColor(this@RemoteActivity, R.color.nx_surface_container_highest),
                        accent,
                    ),
                )
                addState(
                    intArrayOf(),
                    roundedFill(
                        ContextCompat.getColor(this@RemoteActivity, R.color.nx_surface_container_high),
                        accent,
                    ),
                )
            }
        }
    }
    /**
     * 按住/松开一个「hold」键。
     *
     * 松开要保证一定发出：如果 Midway 断线，键会卡在按下状态，
     * 相机就会一直认为半按/锁定生效，所以失败也记日志。
     */
    private fun holdKey(def: KeyMap.KeyDef, down: Boolean) {
        lifecycleScope.launch {
            val cam = NxApp.camera ?: return@launch
            val input = beginUiInput("${def.label}${if (down) "按下" else "松开"}", def.keysym)
            try {
                withContext(Dispatchers.IO) {
                    if (down) cam.keyDown(def.keysym) else cam.keyUp(def.keysym)
                }
                finishUiInput(input, succeeded = true)
                NxApp.log("按键 ${def.label} ${if (down) "按下" else "松开"} → ${def.keysym}")
            } catch (e: CancellationException) {
                finishUiInput(input, succeeded = false)
                throw e
            } catch (e: Exception) {
                finishUiInput(input, succeeded = false)
                // 松开发不出去时提示一次；按下失败则忽略（多半是没连上）
                if (!down) toast("${def.label} 松开失败，可能仍处于按住状态")
                NxApp.log("按键 ${def.label} ${if (down) "按下" else "松开"}失败：${e.message}")
            }
        }
    }

    private fun dp(v: Int): Int = (resources.displayMetrics.density * v).toInt()

    // ==================== B 门（长时间曝光）====================

    /**
     * 启动 B 门曝光。
     *
     * 与普通快门的关键差别：**S2 要一直按住，不能点一下就松**。
     * 普通拍摄时"按住 S2 太久会被相机当成连续全按 → 连拍"，所以那时是单击；
     * 而 B 门的语义正是"全按持续期间快门保持开启"，所以这里必须保持按下。
     *
     * 时序沿用已实测可行的重叠关系（S1 与 S2 必须重叠）：
     * ```
     * keyDown(S1) → 等 450ms → keyDown(S2) → [保持，等用户点结束] → keyUp(S2) → keyUp(S1)
     * ```
     * 注意松开的顺序**先 S2 后 S1**：反过来会先退出半按、
     * 让相机在半按释放时就结束曝光，长曝光时间会不受控。
     *
     * ⚠️ 相机需要先把**模式拨盘转到 B**，否则长按 S2 只会拍出一张普通照片。
     * 这一点 App 无法代劳（拨盘是物理控件），已在界面上提示。
     */
    private fun startBulb() {
        if (bulbRunning || shooting) return
        bulbRunning = true
        bulbStartedAt = System.currentTimeMillis()
        b.btnShutter.isEnabled = false
        b.bulbInfo.visibility = View.VISIBLE
        lifecycleScope.launch {
            val cam = NxApp.camera ?: run { endBulbUi(); return@launch }
            try {
                withContext(Dispatchers.IO) {
                    cam.keyDown(KeyMap.S1)
                    delay(AF_PRESS_WAIT_MS)
                    cam.keyDown(KeyMap.S2)
                }
                NxApp.log("B 门开始：S1 按住 → S2 按住（等待结束指令）")
                startBulbTicker()
                // 相机屏幕在长曝光期间没用了，关掉省电、也避免屏幕漏光。
                // 只动相机那块 LCD，手机屏幕靠 FLAG_KEEP_SCREEN_ON 保持常亮，两者不冲突。
                if (bulbLcdOff) {
                    try {
                        withContext(Dispatchers.IO) { cam.lcd(on = false) }
                        cameraLcdOffed = true
                        NxApp.log("B 门：已关闭相机屏幕")
                    } catch (e: Exception) {
                        NxApp.log("关闭相机屏幕失败（不影响曝光）：${e.message}")
                    }
                }
            } catch (e: Exception) {
                NxApp.log("B 门启动失败：${e.message}")
                toast(getString(R.string.bulb_failed, e.message ?: "未知错误"))
                // 失败也要把可能已按下的键松开，避免相机停在半按状态
                try {
                    withContext(Dispatchers.IO) {
                        cam.keyUp(KeyMap.S2)
                        cam.keyUp(KeyMap.S1)
                    }
                } catch (ignored: Exception) {
                    // 忽略
                }
                endBulbUi()
            }
        }
    }

    /** 结束 B 门曝光：先松 S2（结束曝光），再松 S1 */
    private fun stopBulb() {
        if (!bulbRunning) return
        bulbRunning = false
        val elapsed = System.currentTimeMillis() - bulbStartedAt
        lifecycleScope.launch {
            val cam = NxApp.camera
            try {
                if (cam != null) {
                    withContext(Dispatchers.IO) {
                        cam.keyUp(KeyMap.S2)
                        delay(120)
                        cam.keyUp(KeyMap.S1)
                    }
                }
                NxApp.log("B 门结束：S2 松开 → S1 松开，曝光 ${formatDuration(elapsed)}")
                toast(getString(R.string.bulb_done, formatDuration(elapsed)))
            } catch (e: Exception) {
                NxApp.log("B 门结束时松键失败：${e.message}")
                toast("B 门结束异常：${e.message}")
            } finally {
                // 无论成败都要把相机屏幕恢复，否则用户会以为相机死机了
                restoreCameraLcd()
                endBulbUi()
            }
        }
    }

    /**
     * 恢复相机屏幕（仅在确实是我们关掉的时候才发恢复指令，避免误动用户手动关掉的屏）。
     * 用 NonCancellable 保证即使页面正在销毁也能发出去。
     */
    private fun restoreCameraLcd() {
        if (!cameraLcdOffed) return
        val cam = NxApp.camera ?: run { cameraLcdOffed = false; return }
        cameraLcdOffed = false
        try {
            kotlinx.coroutines.runBlocking(Dispatchers.IO) {
                runCatching { cam.lcd(on = true) }
                    .onFailure { NxApp.log("恢复相机屏幕失败：${it.message}") }
                    .onSuccess { NxApp.log("B 门：已恢复相机屏幕") }
            }
        } catch (e: Exception) {
            NxApp.log("恢复相机屏幕异常：${e.message}")
        }
    }

    /** 切换「B 门时是否关相机屏幕」，选择记进 SharedPreferences */
    private fun toggleBulbLcdOff() {
        if (!profile.lcdControl) return
        bulbLcdOff = !bulbLcdOff
        getSharedPreferences("nx", MODE_PRIVATE).edit()
            .putBoolean("bulb_lcd_off", bulbLcdOff).apply()
        updateBulbLcdButton()
    }

    private fun updateBulbLcdButton() {
        b.btnBulbLcd.setText(
            if (bulbLcdOff) R.string.bulb_lcd_off_on else R.string.bulb_lcd_off_off,
        )
    }

    /** 计时刷新：每 100ms 更新一次已曝光时长 */
    private fun startBulbTicker() {
        bulbTicker?.cancel()
        bulbTicker = lifecycleScope.launch {
            while (isActive && bulbRunning) {
                val elapsed = System.currentTimeMillis() - bulbStartedAt
                b.btnBulb.text = getString(R.string.bulb_stop, formatDuration(elapsed))
                b.bulbInfo.text = getString(R.string.bulb_running, formatDuration(elapsed))
                delay(100)
            }
        }
    }

    private fun endBulbUi() {
        bulbRunning = false
        bulbTicker?.cancel()
        bulbTicker = null
        b.btnShutter.isEnabled = true
        b.btnBulb.setText(R.string.bulb_start)
        b.bulbInfo.setText(R.string.bulb_prerequisite)
    }

    /** 毫秒 → `12.3s` / `1m05s` */
    private fun formatDuration(ms: Long): String {
        val sec = ms / 1000.0
        return if (sec < 60) {
            "%.1fs".format(sec)
        } else {
            val m = (sec / 60).toInt()
            val s = (sec % 60).toInt()
            "%dm%02ds".format(m, s)
        }
    }

    override fun onDestroy() {
        stopPreviewForLifecycle()
        // 页面被销毁时若还在长曝光，必须把快门松开，否则相机会一直开着快门
        if (bulbRunning) {
            bulbRunning = false
            bulbTicker?.cancel()
            val cam = NxApp.camera
            if (cam != null) {
                try {
                    kotlinx.coroutines.runBlocking(Dispatchers.IO) {
                        runCatching { cam.keyUp(KeyMap.S2) }
                        runCatching { cam.keyUp(KeyMap.S1) }
                    }
                    NxApp.log("页面销毁时仍在 B 门曝光，已强制松开快门")
                } catch (e: Exception) {
                    NxApp.log("强制松开 B 门快门失败：${e.message}")
                }
            }
        }
        // 页面销毁时若相机屏幕还关着，要恢复，否则用户会以为相机坏了
        restoreCameraLcd()
        super.onDestroy()
    }

    private fun loadStatus() {
        lifecycleScope.launch {
            val cam = NxApp.camera ?: return@launch
            try {
                val s = withContext(Dispatchers.IO) { cam.status() }
                val parts: List<String> = buildList {
                    s.batteryPercent?.let { this.add("电量 $it%") }
                    s.charging?.let { if (it) this.add("充电中") }
                    s.mode?.takeIf { it != "UNKNOWN" }?.let { this.add("模式 $it") }
                    s.drive?.takeIf { it != "UNKNOWN" }?.let { this.add("驱动 $it") }
                }
                if (parts.isNotEmpty()) {
                    b.toolbar.subtitle = parts.joinToString(" · ")
                }
            } catch (e: Exception) {
                NxApp.log("读取状态失败：${e.message}")
            }
        }
    }

    /** 相机长时间无操作会休眠；遥控期间每 30s 保活一次 */
    private fun startKeepAlive() {
        lifecycleScope.launch {
            while (isActive) {
                delay(30_000)
                val cam = NxApp.camera ?: continue
                try {
                    withContext(Dispatchers.IO) { cam.keepAlive() }
                } catch (e: Exception) {
                    // 保活失败不打扰用户
                }
            }
        }
    }

    override fun onPause() {
        super.onPause()
        // 两路预览都停止；返回前台后由用户手动重新开启。
        stopPreviewForLifecycle()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
