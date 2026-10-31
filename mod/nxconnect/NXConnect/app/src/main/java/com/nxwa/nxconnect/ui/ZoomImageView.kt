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
import android.graphics.Matrix
import android.graphics.PointF
import android.graphics.drawable.Drawable
import android.util.AttributeSet
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ScaleGestureDetector
import android.view.VelocityTracker
import androidx.appcompat.widget.AppCompatImageView
import kotlin.math.abs

/** Matrix-based photo zoom; vertical gestures at fit scale open photo details. */
class ZoomImageView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyle: Int = 0,
) : AppCompatImageView(context, attrs, defStyle) {

    private val matrix = Matrix()
    private var scale = 1f
    private var baseScale = 1f
    private val maxScale: Float get() = baseScale * 6f
    private var zoomMotion: android.animation.ValueAnimator? = null
    private var imageInitialized = false
    private val fitImage = Runnable { fitToView() }

    private val lastTouch = PointF()
    private var activePointerId = MotionEvent.INVALID_POINTER_ID
    private var dragging = false
    private var ignoreTouchUntilDown = false
    var onSwipeUp: (() -> Unit)? = null
    var onSingleTap: (() -> Unit)? = null
    var onVerticalDrag: ((Float, Boolean, Float, Boolean) -> Unit)? = null
    private var dragVelocity: VelocityTracker? = null
    private var verticalDrag = false
    private val gestureStart = PointF()
    private var allowDetails = false
    val fittedPhotoHeight: Float?
        get() = drawable?.takeIf { it.intrinsicHeight > 0 }?.let { it.intrinsicHeight * baseScale }

    private val scaleDetector = ScaleGestureDetector(
        context,
        object : ScaleGestureDetector.SimpleOnScaleGestureListener() {
            override fun onScale(detector: ScaleGestureDetector): Boolean {
                zoomMotion?.cancel()
                parent?.requestDisallowInterceptTouchEvent(true)
                val factor = detector.scaleFactor
                val target = (scale * factor).coerceIn(baseScale, maxScale)
                val realFactor = target / scale
                scale = target
                matrix.postScale(realFactor, realFactor, detector.focusX, detector.focusY)
                clamp()
                imageMatrix = matrix
                return true
            }
        },
    )

    private val gestureDetector = GestureDetector(
        context,
        object : GestureDetector.SimpleOnGestureListener() {
            override fun onDoubleTap(e: MotionEvent): Boolean {
                val d = drawable
                val oneToOne = if (d != null && baseScale > 0f) 1.0f else baseScale * 2.8f
                val target = if (scale > baseScale * 1.05f) baseScale else maxOf(baseScale * 2.8f, oneToOne).coerceIn(baseScale, maxScale)
                animateZoom(target, e.x, e.y)
                return true
            }

            override fun onSingleTapConfirmed(e: MotionEvent): Boolean { onSingleTap?.invoke(); performClick(); return true }
            override fun onDown(e: MotionEvent): Boolean = true
        },
    )

    init {
        scaleType = ScaleType.MATRIX
        imageInitialized = true
        if (drawable != null) scheduleImageFit()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (ignoreTouchUntilDown) {
            if (event.actionMasked != MotionEvent.ACTION_DOWN) return true
            ignoreTouchUntilDown = false
        }
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            dragVelocity?.recycle()
            dragVelocity = VelocityTracker.obtain()
        }
        dragVelocity?.addMovement(event)
        if (event.actionMasked == MotionEvent.ACTION_DOWN) zoomMotion?.cancel()
        scaleDetector.onTouchEvent(event)
        gestureDetector.onTouchEvent(event)

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                activePointerId = event.getPointerId(0)
                lastTouch.set(event.x, event.y)
                gestureStart.set(event.rawX, event.rawY)
                verticalDrag = false
                allowDetails = scale <= baseScale * 1.05f
                dragging = true
                parent?.requestDisallowInterceptTouchEvent(scale > baseScale * 1.05f)
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                zoomMotion?.cancel()
                parent?.requestDisallowInterceptTouchEvent(true)
                if (verticalDrag) onVerticalDrag?.invoke(0f, true, 0f, true)
                verticalDrag = false; allowDetails = false
            }
            MotionEvent.ACTION_POINTER_UP -> {
                // Pointer indices change when a finger lifts. Rebase to the surviving
                // finger so its first move cannot inherit the other finger's position.
                val lifted = event.actionIndex
                if (event.getPointerId(lifted) == activePointerId) {
                    val remaining = if (lifted == 0) 1 else 0
                    activePointerId = event.getPointerId(remaining)
                }
                val index = event.findPointerIndex(activePointerId)
                if (index >= 0) lastTouch.set(event.getX(index), event.getY(index))
            }
            MotionEvent.ACTION_MOVE -> {
                val index = event.findPointerIndex(activePointerId)
                if (index < 0) return true
                val touchX = event.getX(index)
                val touchY = event.getY(index)
                val totalY = event.rawY - gestureStart.y
                val totalX = event.rawX - gestureStart.x
                if (allowDetails && !verticalDrag && kotlin.math.abs(totalY) > android.view.ViewConfiguration.get(context).scaledTouchSlop && kotlin.math.abs(totalY) > kotlin.math.abs(totalX) * 1.25f) {
                    verticalDrag = true
                    parent?.requestDisallowInterceptTouchEvent(true)
                    onVerticalDrag?.invoke(0f, false, 0f, false)
                }
                if (verticalDrag) onVerticalDrag?.invoke(totalY, false, 0f, false)
                if (dragging && event.pointerCount == 1 && !scaleDetector.isInProgress && scale > baseScale * 1.05f) {
                    val dx = touchX - lastTouch.x
                    val dy = touchY - lastTouch.y
                    matrix.postTranslate(dx, dy)
                    clamp()
                    imageMatrix = matrix
                }
                lastTouch.set(touchX, touchY)
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                val dy = event.rawY - gestureStart.y
                val dx = event.rawX - gestureStart.x
                dragVelocity?.computeCurrentVelocity(1000)
                if (verticalDrag) onVerticalDrag?.invoke(if (event.actionMasked == MotionEvent.ACTION_CANCEL) 0f else dy, true,
                    if (event.actionMasked == MotionEvent.ACTION_CANCEL) 0f else dragVelocity?.yVelocity ?: 0f,
                    event.actionMasked == MotionEvent.ACTION_CANCEL)
                dragVelocity?.recycle(); dragVelocity = null
                if (onVerticalDrag == null && event.actionMasked == MotionEvent.ACTION_UP && allowDetails &&
                    dy < -64 * resources.displayMetrics.density && kotlin.math.abs(dy) > kotlin.math.abs(dx) * 1.5f) onSwipeUp?.invoke()
                allowDetails = false
                verticalDrag = false
                dragging = false
                activePointerId = MotionEvent.INVALID_POINTER_ID
                parent?.requestDisallowInterceptTouchEvent(false)
            }
        }
        return true
    }

    private fun animateZoom(target: Float, focusX: Float, focusY: Float) {
        zoomMotion?.cancel()
        val startScale = scale
        val startMatrix = Matrix(matrix)
        if (!viewerAnimationsEnabled(context)) {
            matrix.postScale(target / startScale, target / startScale, focusX, focusY)
            scale = target
            clamp()
            imageMatrix = matrix
            return
        }
        zoomMotion = android.animation.ValueAnimator.ofFloat(scale, target).apply {
            interpolator = viewerMotionInterpolator()
            duration = 260
            addUpdateListener {
                val next = it.animatedValue as Float
                // Recompute from the initial transform: centering an underfilled axis
                // during early frames must not accumulate into a shifted final focus.
                matrix.set(startMatrix)
                matrix.postScale(next / startScale, next / startScale, focusX, focusY)
                scale = next
                clamp()
                imageMatrix = matrix
            }
            start()
        }
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(fitImage)
        zoomMotion?.cancel()
        dragVelocity?.recycle()
        dragVelocity = null
        super.onDetachedFromWindow()
    }

    fun resetZoom(animate: Boolean = true): Boolean {
        if (scale <= baseScale * 1.01f) return false
        if (animate) animateZoom(baseScale, width / 2f, height / 2f)
        else { zoomMotion?.cancel(); reset() }
        return true
    }

    fun cancelInteraction() {
        zoomMotion?.cancel()
        verticalDrag = false
        allowDetails = false
        dragging = false
        activePointerId = MotionEvent.INVALID_POINTER_ID
        dragVelocity?.recycle()
        dragVelocity = null
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    override fun performClick(): Boolean = super.performClick()

    override fun setImageDrawable(drawable: Drawable?) {
        super.setImageDrawable(drawable)
        scheduleImageFit()
    }

    override fun setImageBitmap(bitmap: Bitmap?) {
        super.setImageBitmap(bitmap)
        // ImageView can reuse its BitmapDrawable without calling setImageDrawable.
        scheduleImageFit()
    }

    private fun scheduleImageFit() {
        // Superclass construction may dispatch image setters before our detectors exist.
        if (!imageInitialized) return
        val wasVerticalDrag = verticalDrag
        if (dragging || scaleDetector.isInProgress) ignoreTouchUntilDown = true
        cancelInteraction()
        val now = android.os.SystemClock.uptimeMillis()
        MotionEvent.obtain(now, now, MotionEvent.ACTION_CANCEL, 0f, 0f, 0).also { cancel ->
            scaleDetector.onTouchEvent(cancel)
            gestureDetector.onTouchEvent(cancel)
            cancel.recycle()
        }
        if (wasVerticalDrag) onVerticalDrag?.invoke(0f, true, 0f, true)
        removeCallbacks(fitImage)
        // Queued before the adapter's onReady, so both pixels and baseScale agree.
        post(fitImage)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (imageInitialized) fitToView()
    }

    /** 图片按 FIT_CENTER 铺进控件，作为 baseScale */
    private fun fitToView() {
        zoomMotion?.cancel()
        val d = drawable ?: return
        if (width == 0 || height == 0 || d.intrinsicWidth <= 0 || d.intrinsicHeight <= 0) return
        val sx = width.toFloat() / d.intrinsicWidth
        val sy = height.toFloat() / d.intrinsicHeight
        baseScale = minOf(sx, sy)
        scale = baseScale
        reset()
    }

    private fun reset() {
        val d = drawable ?: return
        matrix.reset()
        matrix.postScale(baseScale, baseScale)
        val dx = (width - d.intrinsicWidth * baseScale) / 2f
        val dy = (height - d.intrinsicHeight * baseScale) / 2f
        matrix.postTranslate(dx, dy)
        scale = baseScale
        imageMatrix = matrix
    }

    /** 限制平移范围，避免把图片拖出屏幕 */
    private fun clamp() {
        val d = drawable ?: return
        val values = FloatArray(9)
        matrix.getValues(values)
        val transX = values[Matrix.MTRANS_X]
        val transY = values[Matrix.MTRANS_Y]
        val currentScale = values[Matrix.MSCALE_X]

        val contentW = d.intrinsicWidth * currentScale
        val contentH = d.intrinsicHeight * currentScale

        val maxDx = if (contentW > width) (contentW - width) / 2f else 0f
        val maxDy = if (contentH > height) (contentH - height) / 2f else 0f

        val centerX = (width - contentW) / 2f
        val centerY = (height - contentH) / 2f

        var fixX = 0f
        var fixY = 0f
        if (contentW > width) {
            if (transX > centerX + maxDx) fixX = centerX + maxDx - transX
            if (transX < centerX - maxDx) fixX = centerX - maxDx - transX
            if (abs(transX - centerX) < 1f) fixX = 0f
        } else {
            fixX = centerX - transX
        }
        if (contentH > height) {
            if (transY > centerY + maxDy) fixY = centerY + maxDy - transY
            if (transY < centerY - maxDy) fixY = centerY - maxDy - transY
        } else {
            fixY = centerY - transY
        }
        if (fixX != 0f || fixY != 0f) matrix.postTranslate(fixX, fixY)
    }
}
