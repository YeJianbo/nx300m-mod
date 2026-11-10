package dev.cxo.filmpack.app.ui

import android.content.Context
import android.graphics.Matrix
import android.graphics.drawable.Drawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.AttributeSet
import android.view.GestureDetector
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.ImageView
import kotlin.math.hypot
import kotlin.math.min

/**
 * A FIT_CENTER photograph with a 500 ms, stationary hold-to-compare gesture.
 *
 * The matrix is in drawable coordinates, so ordinary ImageView rendering and
 * glass backdrop sampling use the same transform. A same-size bitmap swap
 * (original/effect) preserves zoom and pan; a different drawable size resets it.
 * Call [resetZoom] when importing another photo with the same dimensions.
 * All state and callbacks belong to the UI thread.
 */
class ZoomablePreviewImageView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyleAttr: Int = 0
) : ImageView(context, attrs, defStyleAttr) {
    var onCompareChanged: ((Boolean) -> Unit)? = null
    var onTransformChanged: (() -> Unit)? = null

    var comparisonEnabled: Boolean = true
        set(value) {
            field = value
            if (!value) cancelComparison()
        }

    val zoomScale: Float get() = zoom
    val isComparing: Boolean get() = comparing

    private var ready = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop.toFloat()
    private val transform = Matrix()
    private var sourceWidth = 0
    private var sourceHeight = 0
    private var viewportWidth = 0f
    private var viewportHeight = 0f
    private var fitScale = 1f
    private var zoom = 1f
    private var imageLeft = 0f
    private var imageTop = 0f
    private var comparing = false
    private var activePointerId = MotionEvent.INVALID_POINTER_ID
    private var downX = 0f
    private var downY = 0f
    private var lastX = 0f
    private var lastY = 0f
    private var moved = false
    private var multiTouchGesture = false
    private var holding = false

    private val compareAfterHold = Runnable {
        if (holding && !moved && !multiTouchGesture && comparisonEnabled && drawable != null) {
            setComparing(true)
        }
    }
    private val endAccessibleComparison = Runnable { setComparing(false) }
    private var pinchSpan = 0f
    private var pinchFocusX = 0f
    private var pinchFocusY = 0f
    private var pinchStarted = false
    private val tapDetector = GestureDetector(context,
        object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(event: MotionEvent): Boolean = true
            override fun onDoubleTap(event: MotionEvent): Boolean {
                // Do not finish the pointer stream here: the second tap still
                // has an UP to consume, but must not schedule a comparison.
                cancelComparison()
                moved = true
                restoreFit()
                return true
            }
        }).apply { setIsLongpressEnabled(false) }

    /**
     * Measure the actual pointer span instead of ScaleGestureDetector's
     * density-dependent minimum scaling span. That detector ends an inward
     * pinch once fingers get too close, which can leave the photo above fit.
     * Our touchSlop still rejects jitter, but a begun pinch can shrink to fit.
     */
    private fun updatePinch(event: MotionEvent, rebase: Boolean = false) {
        val skipped = if (event.actionMasked == MotionEvent.ACTION_POINTER_UP) event.actionIndex else -1
        val indices = (0 until event.pointerCount).filter { it != skipped }
        if (indices.size < 2) { pinchSpan = 0f; pinchStarted = false; return }
        val focusX = indices.sumOf { event.getX(it).toDouble() }.toFloat() / indices.size - paddingLeft
        val focusY = indices.sumOf { event.getY(it).toDouble() }.toFloat() / indices.size - paddingTop
        val span = indices.sumOf {
            hypot(event.getX(it) - paddingLeft - focusX, event.getY(it) - paddingTop - focusY).toDouble()
        }.toFloat() * 2f / indices.size
        if (!span.isFinite() || span <= 0f) return
        if (rebase || pinchSpan <= 0f) {
            pinchSpan = span
            pinchFocusX = focusX; pinchFocusY = focusY
            pinchStarted = false
            return
        }
        if (!pinchStarted) {
            if (kotlin.math.abs(span - pinchSpan) <= touchSlop &&
                hypot(focusX - pinchFocusX, focusY - pinchFocusY) <= touchSlop) return
            pinchStarted = true
        }
        val next = (zoom * span / pinchSpan).coerceIn(MIN_ZOOM, MAX_ZOOM)
        val ratio = next / zoom
        imageLeft = focusX - (pinchFocusX - imageLeft) * ratio
        imageTop = focusY - (pinchFocusY - imageTop) * ratio
        zoom = next
        pinchSpan = span
        pinchFocusX = focusX; pinchFocusY = focusY
        constrainAndApply()
    }

    init {
        super.setScaleType(ScaleType.MATRIX)
        cropToPadding = true
        defaultFocusHighlightEnabled = false
        isClickable = true
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
        ready = true
    }

    /** Always keep our actual FIT_CENTER-plus-zoom matrix authoritative. */
    override fun setScaleType(scaleType: ScaleType?) {
        if (!ready) super.setScaleType(scaleType)
        else {
            super.setScaleType(ScaleType.MATRIX)
            rebuildTransform()
        }
    }

    override fun setImageDrawable(drawable: Drawable?) {
        super.setImageDrawable(drawable)
        if (ready) rebuildTransform()
    }

    override fun setPadding(left: Int, top: Int, right: Int, bottom: Int) {
        super.setPadding(left, top, right, bottom)
        if (ready) rebuildTransform()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        if (ready) rebuildTransform()
    }

    /** Clear a hold and return to a centred fit; also use this on a new import. */
    fun resetZoom() {
        finishGesture()
        restoreFit()
    }

    private fun restoreFit() {
        zoom = MIN_ZOOM
        sourceWidth = 0
        sourceHeight = 0
        rebuildTransform()
    }

    /** Cancel pending/active comparison when the editor becomes busy or stops. */
    fun cancelComparison() {
        mainHandler.removeCallbacks(compareAfterHold)
        mainHandler.removeCallbacks(endAccessibleComparison)
        holding = false
        setComparing(false)
    }

    private fun setComparing(value: Boolean) {
        if (comparing == value) return
        comparing = value
        onCompareChanged?.invoke(value)
    }

    private fun rebuildTransform() {
        val image = drawable
        val nextWidth = image?.intrinsicWidth?.coerceAtLeast(0) ?: 0
        val nextHeight = image?.intrinsicHeight?.coerceAtLeast(0) ?: 0
        val nextViewportWidth = (width - paddingLeft - paddingRight).coerceAtLeast(0).toFloat()
        val nextViewportHeight = (height - paddingTop - paddingBottom).coerceAtLeast(0).toFloat()
        // Import explicitly resets zoom; resolution swaps preserve normalized center.
        val sameSource = sourceWidth > 0 && sourceHeight > 0 && nextWidth > 0 && nextHeight > 0 &&
            kotlin.math.abs(nextWidth.toDouble()/nextHeight-sourceWidth.toDouble()/sourceHeight) < .005
        val previousScale = fitScale * zoom
        val centerX = if (sameSource && viewportWidth > 0f && previousScale > 0f)
            (viewportWidth * 0.5f - imageLeft) / previousScale * nextWidth / sourceWidth else nextWidth * 0.5f
        val centerY = if (sameSource && viewportHeight > 0f && previousScale > 0f)
            (viewportHeight * 0.5f - imageTop) / previousScale * nextHeight / sourceHeight else nextHeight * 0.5f
        if (!sameSource) zoom = MIN_ZOOM
        sourceWidth = nextWidth
        sourceHeight = nextHeight
        viewportWidth = nextViewportWidth
        viewportHeight = nextViewportHeight
        if (sourceWidth <= 0 || sourceHeight <= 0 || viewportWidth <= 0f || viewportHeight <= 0f) {
            transform.reset()
            imageMatrix = transform
            onTransformChanged?.invoke()
            return
        }
        fitScale = min(viewportWidth / sourceWidth, viewportHeight / sourceHeight)
        val scale = fitScale * zoom
        imageLeft = viewportWidth * 0.5f - centerX * scale
        imageTop = viewportHeight * 0.5f - centerY * scale
        constrainAndApply()
    }

    private fun constrainAndApply() {
        val scale = fitScale * zoom
        val scaledWidth = sourceWidth * scale
        val scaledHeight = sourceHeight * scale
        imageLeft = if (scaledWidth <= viewportWidth) (viewportWidth - scaledWidth) * 0.5f
            else imageLeft.coerceIn(viewportWidth - scaledWidth, 0f)
        imageTop = if (scaledHeight <= viewportHeight) (viewportHeight - scaledHeight) * 0.5f
            else imageTop.coerceIn(viewportHeight - scaledHeight, 0f)
        transform.setScale(scale, scale)
        transform.postTranslate(imageLeft, imageTop)
        imageMatrix = transform
        invalidate()
        onTransformChanged?.invoke()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (!isEnabled || drawable == null) {
            finishGesture()
            return super.onTouchEvent(event)
        }
        // A reset/import can terminate a gesture while fingers are still down.
        // Ignore its remaining events rather than applying them to the new photo.
        if (activePointerId == MotionEvent.INVALID_POINTER_ID && event.actionMasked != MotionEvent.ACTION_DOWN) return true
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                cancelComparison()
                activePointerId = event.getPointerId(0)
                downX = event.x; downY = event.y
                lastX = downX; lastY = downY
                moved = false
                multiTouchGesture = false
                pinchSpan = 0f
                pinchStarted = false
                holding = true
                if (comparisonEnabled) mainHandler.postDelayed(compareAfterHold, COMPARE_HOLD_MS)
                if (zoom > MIN_ZOOM) parent?.requestDisallowInterceptTouchEvent(true)
            }
            MotionEvent.ACTION_POINTER_DOWN -> {
                multiTouchGesture = true
                cancelComparison()
                parent?.requestDisallowInterceptTouchEvent(true)
                updatePinch(event, rebase = true)
            }
        }
        tapDetector.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> {
                val index = event.findPointerIndex(activePointerId)
                if (index < 0) { finishGesture(); return true }
                val x = event.getX(index); val y = event.getY(index)
                if (!moved && hypot(x - downX, y - downY) > touchSlop) {
                    moved = true
                    cancelComparison()
                }
                if (event.pointerCount >= 2) updatePinch(event)
                if (zoom > MIN_ZOOM && event.pointerCount == 1) {
                    imageLeft += x - lastX
                    imageTop += y - lastY
                    constrainAndApply()
                }
                lastX = x; lastY = y
            }
            MotionEvent.ACTION_POINTER_UP -> {
                updatePinch(event, rebase = true)
                // Keep single-finger pan continuous when one pinch finger lifts.
                if (event.getPointerId(event.actionIndex) == activePointerId) {
                    val remaining = if (event.actionIndex == 0) 1 else 0
                    activePointerId = event.getPointerId(remaining)
                }
                val index = event.findPointerIndex(activePointerId)
                if (index >= 0) { lastX = event.getX(index); lastY = event.getY(index) }
            }
            MotionEvent.ACTION_UP -> {
                val wasTap = !moved && !multiTouchGesture && !comparing
                finishGesture()
                if (wasTap) performClick()
            }
            MotionEvent.ACTION_CANCEL -> finishGesture()
        }
        return true
    }

    override fun performClick(): Boolean {
        cancelComparison()
        return super.performClick()
    }

    private fun finishGesture() {
        cancelComparison()
        activePointerId = MotionEvent.INVALID_POINTER_ID
        pinchSpan = 0f
        pinchStarted = false
        if (zoom > MIN_ZOOM && zoom < FIT_SNAP_ZOOM) restoreFit()
        parent?.requestDisallowInterceptTouchEvent(false)
    }

    override fun onDetachedFromWindow() {
        finishGesture()
        super.onDetachedFromWindow()
    }

    override fun onInitializeAccessibilityNodeInfo(info: AccessibilityNodeInfo) {
        super.onInitializeAccessibilityNodeInfo(info)
        if (comparisonEnabled && drawable != null) {
            info.addAction(AccessibilityNodeInfo.AccessibilityAction(
                AccessibilityNodeInfo.ACTION_LONG_CLICK, "短暂查看原图"))
        }
    }

    override fun performAccessibilityAction(action: Int, arguments: Bundle?): Boolean {
        if (action == AccessibilityNodeInfo.ACTION_LONG_CLICK && comparisonEnabled && drawable != null) {
            cancelComparison()
            setComparing(true)
            mainHandler.postDelayed(endAccessibleComparison, ACCESSIBLE_COMPARE_MS)
            return true
        }
        return super.performAccessibilityAction(action, arguments)
    }

    companion object {
        const val COMPARE_HOLD_MS = 500L
        const val MIN_ZOOM = 1f
        const val MAX_ZOOM = 8f
        private const val FIT_SNAP_ZOOM = 1.04f
        private const val ACCESSIBLE_COMPARE_MS = 1500L
    }
}
