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
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.RectF
import android.os.Looper
import android.util.AttributeSet
import android.view.View
import com.nxwa.nxconnect.net.NxOsd
import com.nxwa.nxconnect.net.NxOsdValidator
import com.nxwa.nxconnect.net.OsdGeometry
import java.nio.ByteBuffer
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.floor

/**
 * NX500 的透明 UI 叠加层。作为 LiveViewView 上方的同尺寸兄弟 View 使用。
 * 原始 RGBA / 服务端哈希与显示用 Alpha 完全分离，不改协议数据。
 * 变化块在后台做纯黑键控，在主线程通过 copyPixelsFromBuffer 更新持久 Bitmap。
 *
 * invalidate(rect) 只提交脏区提示；Android 21 起系统可能忽略矩形。
 * 下层 liveview 更新也需要重新合成叠加层，因此不承诺 GPU 只重绘变化块。
 */
class OsdView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : View(context, attrs, defStyleAttr) {

    /** prepareFrame 返回后，调用方必须把 frame 的 Map / ByteArray 视为只读。 */
    class PreparedFrame internal constructor(
        internal val frame: NxOsd.Frame,
        internal val keyedBlocks: Map<Int, ByteArray>,
        internal val opaqueCounts: Map<Int, Int>,
    ) {
        val changedBlockCount: Int get() = frame.blocks.size
    }

    companion object {
        private const val BLOCK_W = NxOsd.EXPECTED_BLOCK_W
        private const val BLOCK_H = NxOsd.EXPECTED_BLOCK_H
        private const val BLOCK_BYTES = BLOCK_W * BLOCK_H * 4

        /** 0 字节 HTTP 体由轮询器处理；这里仍严格复用已有解析器和校验器。 */
        @Throws(NxOsd.OsdParseException::class)
        fun parseResponse(raw: ByteArray, geometry: OsdGeometry = OsdGeometry.NX500): NxOsd.Frame =
            NxOsdValidator.parseStrict(raw, geometry)

        /** 在 Dispatchers.Default 调用；只处理本次变化块，绝不计算或改写协议哈希。 */
        @Throws(NxOsd.OsdParseException::class)
        fun prepareFrame(frame: NxOsd.Frame, geometry: OsdGeometry = OsdGeometry.NX500,
            blackKey: Boolean = true): PreparedFrame {
            NxOsdValidator.validateFrame(frame, geometry)
            val keyed = LinkedHashMap<Int, ByteArray>(frame.blocks.size)
            val counts = LinkedHashMap<Int, Int>(frame.blocks.size)
            for ((index, block) in frame.blocks) {
                val pixels = block.rgba.copyOf()
                var opaque = 0
                var p = 0
                while (p < pixels.size) {
                    // 阈值严格为 0。RGB=(1,0,0) 等近黑 UI 仍完全保留。
                    val black = blackKey && pixels[p] == 0.toByte() &&
                        pixels[p + 1] == 0.toByte() && pixels[p + 2] == 0.toByte()
                    // 按 RGB 黑键控生成显示 Alpha；原 Alpha 的语义未约定，原值另行保留。
                    // Alpha=0 时 RGB 也为 0，Alpha=255 时保留原 RGB，符合预乘格式。
                    pixels[p + 3] = if (black) 0.toByte() else 0xFF.toByte()
                    if (!black) opaque++
                    p += 4
                }
                keyed[index] = pixels
                counts[index] = opaque
            }
            return PreparedFrame(frame, keyed, counts)
        }
    }

    private var geometry = OsdGeometry.NX500
    private val FRAME_W get() = geometry.width
    private val FRAME_H get() = geometry.height
    private val BLOCK_COUNT get() = geometry.blockCount

    fun configure(value: OsdGeometry) {
        checkMainThread()
        geometry = value
        rgba = NxOsd.newCanvas(FRAME_W, FRAME_H)
        hashes = IntArray(BLOCK_COUNT)
        received = BooleanArray(BLOCK_COUNT)
        opaquePixels = IntArray(BLOCK_COUNT)
        bitmap = null
        bitmapCanvas = null
        reset()
    }

    /** 保存原样 RGBA（包括原 Alpha）。 */
    private var rgba = NxOsd.newCanvas(FRAME_W, FRAME_H)
    private var hashes = IntArray(BLOCK_COUNT)
    private var received = BooleanArray(BLOCK_COUNT)
    private var opaquePixels = IntArray(BLOCK_COUNT)
    private var bitmap: Bitmap? = null
    private var bitmapCanvas: Canvas? = null
    private var blockBitmap: Bitmap? = null
    private val destination = RectF()
    private var imageScale = 0f
    private var viewportCompatible = false
    private val displayPaint = Paint(Paint.FILTER_BITMAP_FLAG) // 默认 SRC_OVER，叠在取景之上。
    fun setSmoothScaling(smooth: Boolean) {
        displayPaint.isFilterBitmap = smooth
        invalidate()
    }

    private val copyPaint = Paint().apply {
        isFilterBitmap = false
        // 更新缓存时必须 SRC：新块中的透明像素要擦掉旧图标，SRC_OVER 会留下残影。
        xfermode = PorterDuffXfermode(PorterDuff.Mode.SRC)
    }

    init {
        contentDescription = "相机屏幕叠加层"
        isClickable = false
        isFocusable = false
    }

    val hasCompleteFrame: Boolean
        get() { checkMainThread(); return received.all { it } }

    val hasVisibleContent: Boolean
        get() { checkMainThread(); return opaquePixels.any { it > 0 } }

    /** 使用 LiveViewView 给出的同一显示矩形，避免两个 View 独立取整造成错位。 */
    fun setViewport(rect: RectF, compatible: Boolean) {
        checkMainThread()
        require(rect.left.isFinite() && rect.top.isFinite() &&
            rect.right.isFinite() && rect.bottom.isFinite()) { "显示矩形包含非有限值" }
        if (compatible && !rect.isEmpty) {
            require(abs(rect.width() / FRAME_W - rect.height() / FRAME_H) < 0.001f) {
                "OSD 显示矩形必须保持相机屏幕比例"
            }
        }
        if (destination == rect && viewportCompatible == compatible) return
        destination.set(rect)
        viewportCompatible = compatible
        imageScale = if (compatible && !rect.isEmpty) rect.width() / FRAME_W else 0f
        invalidate() // 几何或可见性改变必须清除旧位置。
    }

    /** 只在新会话清零；空体、零块和可重试网络错误绝不能重置增量基线。 */
    fun reset() {
        checkMainThread()
        rgba.fill(0)
        hashes.fill(0)
        received.fill(false)
        opaquePixels.fill(0)
        bitmap?.eraseColor(Color.TRANSPARENT)
        invalidate()
    }

    fun hashsParam(): String {
        checkMainThread()
        return NxOsd.buildHashsParam(hashes, BLOCK_COUNT)
    }

    /** 保留旧调用入口；轮询器应优先使用后台 prepareFrame + 主线程 submitPrepared。 */
    @Throws(NxOsd.OsdParseException::class)
    fun submitFrame(frame: NxOsd.Frame) {
        checkMainThread()
        submitPrepared(prepareFrame(frame, geometry))
    }

    @Throws(NxOsd.OsdParseException::class)
    fun submitPrepared(prepared: PreparedFrame) {
        checkMainThread()
        val frame = prepared.frame
        NxOsdValidator.validateFrame(frame, geometry) // 任何写入前检查所有原始块。
        check(prepared.keyedBlocks.keys == frame.blocks.keys &&
            prepared.opaqueCounts.keys == frame.blocks.keys) { "键控块与协议帧不一致" }
        for (index in frame.blocks.keys) {
            check(prepared.keyedBlocks.getValue(index).size == BLOCK_BYTES) { "键控块长度错误" }
            check(prepared.opaqueCounts.getValue(index) in 0..BLOCK_W * BLOCK_H)
        }
        if (frame.blocks.isEmpty()) return
        ensureBitmaps()
        val tile = checkNotNull(blockBitmap)
        val target = checkNotNull(bitmapCanvas)
        for ((index, block) in frame.blocks) {
            // ARGB_8888 是 Bitmap 的配置名；ByteBuffer 仍携带 R,G,B,A 字节。
            tile.copyPixelsFromBuffer(ByteBuffer.wrap(prepared.keyedBlocks.getValue(index)))
            target.drawBitmap(tile, block.x.toFloat(), block.y.toFloat(), copyPaint)
        }
        frame.applyTo(rgba) // 复用协议层合成，绝不把显示 Alpha 写回原始画布。
        frame.updateHashes(hashes) // 全部合成成功后，才声明持有这些服务端哈希。
        for (index in frame.blocks.keys) {
            received[index] = true
            opaquePixels[index] = prepared.opaqueCounts.getValue(index)
        }
        for (block in frame.blocks.values) invalidateBlock(block.x, block.y)
    }

    private fun ensureBitmaps() {
        if (bitmap == null) {
            bitmap = alphaBitmap(FRAME_W, FRAME_H).also { it.eraseColor(Color.TRANSPARENT) }
            bitmapCanvas = Canvas(checkNotNull(bitmap))
        }
        if (blockBitmap == null) {
            blockBitmap = alphaBitmap(BLOCK_W, BLOCK_H).also {
                check(it.rowBytes == BLOCK_W * 4 && it.byteCount == BLOCK_BYTES) {
                    "Bitmap 行跨度不符合 RGBA 块布局：${it.rowBytes}"
                }
            }
        }
    }

    private fun alphaBitmap(w: Int, h: Int): Bitmap =
        Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).apply {
            density = Bitmap.DENSITY_NONE
            setHasAlpha(true)
        }

    @Suppress("DEPRECATION")
    private fun invalidateBlock(x: Int, y: Int) {
        if (!viewportCompatible || destination.isEmpty) return
        // 外扩一个源像素，覆盖双线性过滤的影响；向外取整，避免缩放漏线。
        val left = floor((destination.left + maxOf(0, x - 1) * imageScale).toDouble()).toInt()
        val top = floor((destination.top + maxOf(0, y - 1) * imageScale).toDouble()).toInt()
        val right = ceil((destination.left + minOf(FRAME_W, x + BLOCK_W + 1) * imageScale).toDouble()).toInt()
        val bottom = ceil((destination.top + minOf(FRAME_H, y + BLOCK_H + 1) * imageScale).toDouble()).toInt()
        val l = maxOf(0, left)
        val t = maxOf(0, top)
        val r = minOf(width, right)
        val b = minOf(height, bottom)
        if (l < r && t < b) invalidate(l, t, r, b)
    }

    override fun isOpaque(): Boolean = false

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        // 不能 drawColor(BLACK) 或清空父 Canvas，否则会遮住下层取景。
        val image = bitmap ?: return
        if (viewportCompatible && !destination.isEmpty) {
            canvas.drawBitmap(image, null, destination, displayPaint)
        }
    }

    override fun onDetachedFromWindow() {
        bitmapCanvas = null
        bitmap = null // 可能仍被硬件 display list 引用，交给 GC。
        blockBitmap?.recycle() // 临时块只用于同步的软件 Canvas。
        blockBitmap = null
        rgba.fill(0)
        hashes.fill(0)
        received.fill(false)
        opaquePixels.fill(0)
        super.onDetachedFromWindow()
    }

    private fun checkMainThread() {
        check(Looper.myLooper() == Looper.getMainLooper()) { "OsdView 状态只能在主线程访问" }
    }
}
