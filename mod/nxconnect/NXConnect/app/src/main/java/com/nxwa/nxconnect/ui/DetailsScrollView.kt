package com.nxwa.nxconnect.ui

import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.VelocityTracker
import android.view.ViewConfiguration
import android.widget.ScrollView
import kotlin.math.abs

/** Scroll the details normally; a downward pull at the top returns to the photo. */
class DetailsScrollView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : ScrollView(context, attrs) {
    var onPull: ((Float, Boolean, Float, Boolean) -> Unit)? = null
    private var downX = 0f
    private var downY = 0f
    private var pulling = false
    private var multiTouch = false
    private var velocity: VelocityTracker? = null
    private val slop = ViewConfiguration.get(context).scaledTouchSlop

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            downX = event.rawX; downY = event.rawY; pulling = false
            multiTouch = false
            velocity?.recycle()
            velocity = VelocityTracker.obtain()
        }
        velocity?.addMovement(event)
        if (event.actionMasked == MotionEvent.ACTION_POINTER_DOWN) {
            multiTouch = true
            if (pulling) onPull?.invoke(event.rawY - downY, true, 0f, true)
            pulling = false
        }
        val handled = super.dispatchTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP || event.actionMasked == MotionEvent.ACTION_CANCEL) {
            velocity?.recycle(); velocity = null
        }
        return handled
    }

    private fun beginPull(event: MotionEvent) {
        if (event.actionMasked == MotionEvent.ACTION_MOVE && !multiTouch) {
            if (scrollY > 0 && !pulling) downY = event.rawY
            val dy = event.rawY - downY
            if (!pulling && scrollY == 0 && dy > slop && dy > abs(event.rawX - downX) * 1.25f) {
                pulling = true
                MotionEvent.obtain(event).also {
                    it.action = MotionEvent.ACTION_CANCEL
                    super.onTouchEvent(it)
                    it.recycle()
                }
                parent?.requestDisallowInterceptTouchEvent(true)
                onPull?.invoke(0f, false, 0f, false)
            }
        }
    }

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        beginPull(event)
        return pulling || super.onInterceptTouchEvent(event)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        beginPull(event)
        if (!pulling) return super.onTouchEvent(event)
        when (event.actionMasked) {
            MotionEvent.ACTION_MOVE -> onPull?.invoke(event.rawY - downY, false, 0f, false)
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                velocity?.computeCurrentVelocity(1000)
                onPull?.invoke(event.rawY - downY, true, velocity?.yVelocity ?: 0f,
                    event.actionMasked == MotionEvent.ACTION_CANCEL)
                pulling = false
            }
        }
        return true
    }

    override fun onDetachedFromWindow() {
        velocity?.recycle(); velocity = null
        super.onDetachedFromWindow()
    }

    fun cancelPull() {
        pulling = false
        multiTouch = true
        velocity?.recycle()
        velocity = null
        parent?.requestDisallowInterceptTouchEvent(false)
    }
}
