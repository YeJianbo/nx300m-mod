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

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.media.Nv12Decoder
import java.util.Locale
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * NV12 实时取景底图，继续复用已有 Nv12Decoder。
 * 每路最多一个请求；画质和请求起点间隔可在运行时改变，不 stop/start。
 * 正常模式保留原来的 40ms 下限；叠加模式由 Activity 设置低画质和 500ms 下限。
 */
class LiveViewView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : View(context, attrs, defStyle) {

    companion object {
        private const val MAX_CONSECUTIVE_FAILURES = 6
    }

    var osdGeometry = com.nxwa.nxconnect.net.OsdGeometry.NX500
        set(value) { field = value; updateViewport() }

    @Volatile
    var lowQuality: Boolean = false

    /** 请求起点的最小间隔，是限速上限而非保证帧率；已有请求从不因调速而取消。 */
    @Volatile
    var minFrameIntervalMs: Long = 40L
        set(value) {
            require(value >= 0L)
            field = value
        }

    var onStatus: ((String) -> Unit)? = null
    var onStopped: ((String) -> Unit)? = null

    /** 坐标属于本 View；兄弟 OsdView 使用相同布局尺寸和位置。回调始终在 Main。 */
    var onViewportChanged: ((RectF, Boolean) -> Unit)? = null
        set(value) {
            field = value
            updateViewport()
        }

    private var scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var job: Job? = null
    private var bitmap: Bitmap? = null
    private var pixels: IntArray? = null
    private val paint = Paint(Paint.FILTER_BITMAP_FLAG)
    private val destination = RectF()

    val isRunning: Boolean get() = job?.isActive == true

    /**
     * 最近一次统计出的取景帧率。
     *
     * 用途：叠加模式下需要**同时**看到取景和 OSD 两路各自的真实帧率，
     * 才能判断帧率是被限速器卡住还是真的慢。
     * 只在帧循环所在的 Main 线程写入/读取。
     */
    var liveFps: Double = 0.0
        private set

    fun start() {
        if (isRunning) return
        val previous = job
        job = scope.launch {
            // cancel 不代表阻塞 HTTP 已返回；重启本路前等旧请求退出。
            previous?.join()
            var frames = 0
            var lastFpsAt = SystemClock.elapsedRealtime()
            var failures = 0
            var waitingForData = false
            val camera = NxApp.camera
            if (camera == null) {
                stop("相机未连接")
                return@launch
            }
            try {
                while (isActive) {
                    check(NxApp.camera === camera) { "相机连接已改变，请重新开启取景" }
                    val started = SystemClock.elapsedRealtime()
                    val wantLow = lowQuality
                    val data = try {
                        withContext(Dispatchers.IO) { camera.liveView(low = wantLow) }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        failures++
                        val reason = "取景请求失败：${e.message ?: e.javaClass.simpleName}"
                        if (failures >= MAX_CONSECUTIVE_FAILURES) {
                            stop(reason)
                            return@launch
                        }
                        onStatus?.invoke("取景重试 $failures/$MAX_CONSECUTIVE_FAILURES，保留上一帧")
                        NxApp.log(reason)
                        pace(started, 300L)
                        continue
                    }
                    coroutineContext.ensureActive()
                    check(NxApp.camera === camera) { "相机连接已改变，请重新开启取景" }

                    if (data.isEmpty()) {
                        // 出流时偶发空体、空转时稳定空体，均不是网络/解码失败。
                        failures = 0
                        waitingForData = true
                        onStatus?.invoke("取景暂不可用，保留上一帧并等待出流")
                        pace(started, 300L)
                        continue
                    }
                    val decoded = withContext(Dispatchers.Default) {
                        Nv12Decoder.decode(data, pixels)
                    }
                    if (decoded == null) {
                        failures++
                        val reason = "无法识别的取景帧（${data.size} 字节）"
                        if (failures >= MAX_CONSECUTIVE_FAILURES) {
                            stop(reason)
                            return@launch
                        }
                        onStatus?.invoke("$reason，正在重试")
                        pace(started, 200L)
                        continue
                    }
                    failures = 0
                    pixels = decoded.pixels
                    val old = bitmap
                    val needNew = old == null || old.isRecycled ||
                        old.width != decoded.width || old.height != decoded.height
                    val bmp = if (needNew) {
                        // 旧 Bitmap 可能仍被硬件 display list 使用，不立即 recycle。
                        Bitmap.createBitmap(decoded.width, decoded.height, Bitmap.Config.ARGB_8888)
                            .apply { density = Bitmap.DENSITY_NONE }
                            .also { bitmap = it }
                    } else {
                        checkNotNull(old)
                    }
                    bmp.setPixels(decoded.pixels, 0, decoded.width, 0, 0, decoded.width, decoded.height)
                    if (needNew) updateViewport()
                    invalidate()

                    frames++
                    val now = SystemClock.elapsedRealtime()
                    val window = now - lastFpsAt
                    if (needNew || waitingForData || window >= 1500L) {
                        val fps = frames * 1000.0 / window.coerceAtLeast(1L)
                        liveFps = fps
                        val q = if (wantLow) "低画质" else "高画质"
                        onStatus?.invoke("${decoded.width}×${decoded.height} · $q · " +
                            String.format(Locale.US, "%.1ffps", fps))
                        frames = 0
                        lastFpsAt = now
                        waitingForData = false
                    }
                    pace(started)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (t: Throwable) {
                NxApp.log("取景线程异常：${t.javaClass.simpleName}: ${t.message}")
                try {
                    com.nxwa.nxconnect.CrashLog.record(NxApp.ctx(), "LiveViewView 取景循环", t)
                } catch (ignored: Throwable) {
                    // 记录失败也不能再次抛出。
                }
                stop("取景异常已停止：${t.javaClass.simpleName}（详情见崩溃日志）")
            }
        }
    }

    private suspend fun pace(started: Long, retryDelay: Long = 0L) {
        val wait = maxOf(retryDelay, minFrameIntervalMs - (SystemClock.elapsedRealtime() - started))
        if (wait > 0L) delay(wait)
    }

    fun stop(reason: String? = null) {
        job?.cancel()
        // 保留 Job，供下一次 start / stopAndJoin 等待阻塞请求退出。
        if (reason != null) onStopped?.invoke(reason)
    }

    suspend fun stopAndJoin() {
        val previous = job
        stop()
        previous?.join()
        if (job === previous) job = null
    }

    private fun updateViewport() {
        val sourceW = bitmap?.width ?: osdGeometry.width
        val sourceH = bitmap?.height ?: osdGeometry.height
        val availableW = width - paddingLeft - paddingRight
        val availableH = height - paddingTop - paddingBottom
        if (availableW <= 0 || availableH <= 0) {
            destination.setEmpty()
        } else {
            val scale = minOf(availableW.toFloat() / sourceW, availableH.toFloat() / sourceH)
            val w = sourceW * scale
            val h = sourceH * scale
            val left = paddingLeft + (availableW - w) / 2f
            val top = paddingTop + (availableH - h) / 2f
            destination.set(left, top, left + w, top + h)
        }
        // 附件还提到 720×404 等取景尺寸，其与 OSD 的坐标关系未验证，不能擅自拉伸。
        val compatible = sourceW.toLong() * osdGeometry.height == sourceH.toLong() * osdGeometry.width
        onViewportChanged?.invoke(RectF(destination), compatible)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        updateViewport()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!scope.isActive) scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
        updateViewport()
    }

    override fun onDraw(canvas: Canvas) {
        canvas.drawColor(Color.BLACK)
        val bmp = bitmap ?: return
        if (!bmp.isRecycled && !destination.isEmpty) canvas.drawBitmap(bmp, null, destination, paint)
    }

    override fun onDetachedFromWindow() {
        stop()
        scope.cancel()
        bitmap = null // 硬件 Canvas 可能仍持有旧图，交给 GC。
        pixels = null
        super.onDetachedFromWindow()
    }
}
