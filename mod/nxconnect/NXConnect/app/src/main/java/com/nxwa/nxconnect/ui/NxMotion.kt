/* SPDX-License-Identifier: AGPL-3.0-or-later */
package com.nxwa.nxconnect.ui

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ObjectAnimator
import android.animation.PropertyValuesHolder
import android.animation.ValueAnimator
import android.os.Build
import android.provider.Settings
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import java.util.WeakHashMap

/** Shared motion follows Samsung's public One UI motion guideline; see docs/ONEUI.md. */
object NxMotion {
    val easing = PathInterpolator(.22f, .25f, 0f, 1f)
    private val presses = WeakHashMap<View, Animator>()
    private val fades = WeakHashMap<View, Animator>()
    private val expansions = WeakHashMap<View, Animator>()

    fun enabled(view: View): Boolean = if (Build.VERSION.SDK_INT >= 26) {
        ValueAnimator.areAnimatorsEnabled()
    } else {
        Settings.Global.getFloat(view.context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f
    }

    fun touchBounce(view: View, scale: Float = .97f) {
        view.setOnTouchListener { v, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    presses.remove(v)?.cancel()
                    val pressed = event.actionMasked == MotionEvent.ACTION_DOWN && v.isEnabled
                    val target = if (pressed && enabled(v)) scale else 1f
                    if (!enabled(v)) {
                        v.scaleX = target
                        v.scaleY = target
                    } else {
                        val animation = ObjectAnimator.ofPropertyValuesHolder(v,
                            PropertyValuesHolder.ofFloat(View.SCALE_X, target),
                            PropertyValuesHolder.ofFloat(View.SCALE_Y, target))
                        animation.duration = if (pressed) 100 else 220
                        animation.interpolator = easing
                        animation.addListener(object : AnimatorListenerAdapter() {
                            override fun onAnimationEnd(a: Animator) {
                                if (presses[v] === a) presses.remove(v)
                            }
                        })
                        presses[v] = animation
                        animation.start()
                    }
                }
            }
            false
        }
    }

    fun fadeIn(view: View, duration: Long = 150, translationYDelta: Float = 12f) {
        val wasHidden = view.visibility != View.VISIBLE
        fades.remove(view)?.cancel()
        if (wasHidden) {
            view.alpha = 0f
            view.translationY = translationYDelta * view.resources.displayMetrics.density
        }
        view.visibility = View.VISIBLE
        fade(view, 1f, 0f, duration, null)
    }

    fun fadeOut(view: View, duration: Long = 80, gone: Boolean = true) {
        fades.remove(view)?.cancel()
        if (view.visibility != View.VISIBLE) return
        fade(view, 0f, 0f, duration, if (gone) View.GONE else View.INVISIBLE)
    }

    private fun fade(view: View, alpha: Float, y: Float, duration: Long, visibility: Int?) {
        if (!enabled(view)) {
            view.alpha = alpha
            view.translationY = y
            if (visibility != null) view.visibility = visibility
            return
        }
        val animation = ObjectAnimator.ofPropertyValuesHolder(view,
            PropertyValuesHolder.ofFloat(View.ALPHA, alpha),
            PropertyValuesHolder.ofFloat(View.TRANSLATION_Y, y))
        animation.duration = duration
        animation.interpolator = easing
        animation.addListener(object : AnimatorListenerAdapter() {
            private var cancelled = false
            override fun onAnimationCancel(a: Animator) { cancelled = true }
            override fun onAnimationEnd(a: Animator) {
                if (fades[view] === a) {
                    fades.remove(view)
                    if (!cancelled && visibility != null) view.visibility = visibility
                }
            }
        })
        fades[view] = animation
        animation.start()
    }

    fun toggleExpand(view: View, expand: Boolean, duration: Long = 280) {
        expansions.remove(view)?.cancel()
        val parent = view.parent as? ViewGroup
        fun settle() {
            view.layoutParams.height = ViewGroup.LayoutParams.WRAP_CONTENT
            view.visibility = if (expand) View.VISIBLE else View.GONE
            view.alpha = 1f
            view.requestLayout()
        }
        if (!enabled(view) || parent == null || parent.width == 0) {
            settle()
            return
        }
        val start = if (view.visibility == View.VISIBLE) view.height else 0
        val lp = view.layoutParams
        val margins = lp as? ViewGroup.MarginLayoutParams
        val available = (parent.width - parent.paddingLeft - parent.paddingRight -
            (margins?.leftMargin ?: 0) - (margins?.rightMargin ?: 0)).coerceAtLeast(1)
        val widthSpec = View.MeasureSpec.makeMeasureSpec(
            if (lp.width >= 0) lp.width else available,
            if (lp.width == ViewGroup.LayoutParams.WRAP_CONTENT) View.MeasureSpec.AT_MOST else View.MeasureSpec.EXACTLY)
        view.measure(widthSpec, View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED))
        val target = if (expand) view.measuredHeight else 0
        view.visibility = View.VISIBLE
        val animation = ValueAnimator.ofInt(start, target)
        animation.duration = duration
        animation.interpolator = easing
        animation.addUpdateListener {
            view.layoutParams.height = it.animatedValue as Int
            view.requestLayout()
        }
        animation.addListener(object : AnimatorListenerAdapter() {
            private var cancelled = false
            override fun onAnimationCancel(a: Animator) { cancelled = true }
            override fun onAnimationEnd(a: Animator) {
                if (expansions[view] === a) {
                    expansions.remove(view)
                    if (!cancelled) settle()
                }
            }
        })
        expansions[view] = animation
        animation.start()
    }
}
