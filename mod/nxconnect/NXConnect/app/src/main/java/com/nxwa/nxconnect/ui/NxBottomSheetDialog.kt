/* SPDX-License-Identifier: AGPL-3.0-or-later */
package com.nxwa.nxconnect.ui

import android.app.Activity
import android.content.Context
import android.view.MotionEvent
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import androidx.core.view.ViewCompat
import androidx.coordinatorlayout.widget.CoordinatorLayout
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.nxwa.nxconnect.R

/** One expanded position; nested content scrolls before a downward drag hides the sheet. */
class NxBottomSheetDialog(context: Context) : BottomSheetDialog(context, R.style.NxBottomSheetDialog) {
    private val hostActivity = context as? Activity
    private var surface: View? = null
    private var closing = false

    override fun onStart() {
        super.onStart()
        closing = false
        window?.apply {
            setWindowAnimations(0)
            setDimAmount(.25f)
        }
        dismissWithAnimation = false
        behavior.apply {
            isFitToContents = true
            skipCollapsed = true
            isHideable = true
            state = BottomSheetBehavior.STATE_EXPANDED
        }
        surface = findViewById<View>(com.google.android.material.R.id.design_bottom_sheet)?.apply {
            (layoutParams as? ViewGroup.MarginLayoutParams)?.let { params ->
                val inset = resources.getDimensionPixelSize(R.dimen.oneui_panel_inset)
                val width = (resources.displayMetrics.widthPixels - 2 * inset).coerceAtLeast(1)
                if (params.width != width || params.marginStart != inset || params.marginEnd != inset) {
                    // Resolve start/end as well as physical margins: CoordinatorLayout
                    // resolves relative margins again during the first inset/layout pass.
                    params.width = width
                    params.marginStart = inset
                    params.marginEnd = inset
                    (params as? CoordinatorLayout.LayoutParams)?.gravity = Gravity.CENTER_HORIZONTAL
                    layoutParams = params
                }
            }
            setBackgroundColor(android.graphics.Color.TRANSPARENT)
            ViewCompat.setElevation(this, 0f)
            animate().cancel()
            alpha = 1f
            translationY = 0f
            if (NxMotion.enabled(this)) {
                alpha = 0f
                post {
                    if (isShowing && !closing) {
                        // Position belongs to BottomSheetBehavior, including when the user
                        // grabs the panel during entry. Only opacity animates independently.
                        animate().alpha(1f).setDuration(150)
                            .setInterpolator(NxMotion.easing).start()
                    }
                }
            }
        }
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (closing) return true
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            surface?.apply { animate().cancel(); alpha = 1f }
        }
        return super.dispatchTouchEvent(event)
    }

    override fun dismiss() {
        if (closing) return
        val panel = surface
        // Lifecycle teardown must not leave a window waiting for a frame callback.
        if (!isShowing || panel == null || !panel.isAttachedToWindow || !NxMotion.enabled(panel) ||
            hostActivity?.isFinishing == true || hostActivity?.isDestroyed == true ||
            behavior.state == BottomSheetBehavior.STATE_HIDDEN) {
            super.dismiss()
            return
        }
        closing = true
        panel.animate().cancel()
        panel.animate().alpha(0f)
            .setDuration(80).setInterpolator(NxMotion.easing)
            .withEndAction { super.dismiss() }.start()
    }

    override fun onStop() {
        surface?.apply {
            animate().withEndAction(null).cancel()
            alpha = 1f
            translationY = 0f
        }
        surface = null
        super.onStop()
    }
}
