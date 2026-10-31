package com.nxwa.nxconnect.ui

import android.content.Context
import android.util.AttributeSet
import android.widget.FrameLayout

/** Sizes the viewport without changing either liveview or OSD coordinates. */
class RemotePreviewFrame @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs) {
    var screenRatio: Float = 800f / 480f
        set(value) {
            require(value.isFinite() && value > 0f)
            field = value
            requestLayout()
        }

    var expanded: Boolean = false
        set(value) { field = value; requestLayout() }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        val available = MeasureSpec.getSize(heightMeasureSpec)
        val bounded = MeasureSpec.getMode(heightMeasureSpec) != MeasureSpec.UNSPECIFIED
        val naturalHeight = (width / screenRatio).toInt().coerceAtLeast(1)
        val height = when {
            expanded && bounded -> available
            // Short landscape windows retain space for the scrollable keys.
            bounded -> naturalHeight.coerceAtMost((available * .48f).toInt().coerceAtLeast(1))
            else -> naturalHeight
        }
        super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(height, MeasureSpec.EXACTLY))
    }
}
