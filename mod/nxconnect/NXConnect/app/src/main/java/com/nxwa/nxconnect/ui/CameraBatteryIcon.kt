package com.nxwa.nxconnect.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ImageSpan
import androidx.core.content.ContextCompat
import com.nxwa.nxconnect.R

/** The old NX bodies report four battery steps, not a measured percentage. */
class CameraBatteryIcon(context: Context, percent: Int?, level: Int?, private val charging: Boolean?) : Drawable() {
    private val steps = level?.takeIf { it in 0..4 }
        ?: percent?.takeIf { it in 0..100 }?.let { if (it == 0) 0 else (it + 24) / 25 }
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = ContextCompat.getColor(context, if (steps == 0 && charging != true) R.color.nx_err else R.color.nx_on_surface_variant)
        strokeWidth = 1.5f
        strokeJoin = Paint.Join.ROUND
        strokeCap = Paint.Cap.ROUND
    }
    private val width = (26 * context.resources.displayMetrics.density).toInt()
    private val height = (18 * context.resources.displayMetrics.density).toInt()

    override fun getIntrinsicWidth() = width
    override fun getIntrinsicHeight() = height

    override fun draw(canvas: Canvas) {
        val saved = canvas.save()
        canvas.translate(bounds.left.toFloat(), bounds.top.toFloat())
        canvas.scale(bounds.width() / 26f, bounds.height() / 18f)
        paint.style = Paint.Style.STROKE
        canvas.drawRoundRect(1f, 3f, 22f, 15f, 1.8f, 1.8f, paint)
        paint.style = Paint.Style.FILL
        canvas.drawRoundRect(23f, 6.5f, 25f, 11.5f, .7f, .7f, paint)
        if (charging == true) {
            val bolt = Path().apply {
                moveTo(12.5f, 4.5f); lineTo(8f, 9.5f); lineTo(11.5f, 9.5f)
                lineTo(10.5f, 13.5f); lineTo(15.5f, 8f); lineTo(12f, 8f); close()
            }
            canvas.drawPath(bolt, paint)
        } else if (steps != null) {
            repeat(steps) { index ->
                val left = 3.5f + index * 4.2f
                canvas.drawRoundRect(left, 5.5f, left + 3f, 12.5f, .4f, .4f, paint)
            }
        } else {
            paint.textSize = 10f
            paint.textAlign = Paint.Align.CENTER
            canvas.drawText("?", 11.5f, 12.5f, paint)
        }
        canvas.restoreToCount(saved)
    }

    override fun setAlpha(alpha: Int) { paint.alpha = alpha; invalidateSelf() }
    override fun setColorFilter(colorFilter: ColorFilter?) { paint.colorFilter = colorFilter; invalidateSelf() }
    @Deprecated("Drawable opacity is no longer used")
    override fun getOpacity() = PixelFormat.TRANSLUCENT

    companion object {
        fun label(context: Context, text: String, percent: Int?, level: Int?, charging: Boolean?): CharSequence {
            val icon = CameraBatteryIcon(context, percent, level, charging).apply {
                setBounds(0, 0, intrinsicWidth, intrinsicHeight)
            }
            return SpannableStringBuilder(text).apply {
                if (isNotEmpty()) append("  ")
                val start = length
                append('\uFFFC')
                setSpan(ImageSpan(icon, ImageSpan.ALIGN_BOTTOM), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
        }

        fun description(percent: Int?, level: Int?, charging: Boolean?): String {
            val value = level?.takeIf { it in 0..4 }?.let { "相机电量 $it 档，共 4 档" }
                ?: percent?.takeIf { it in 0..100 }?.let { "相机电量 $it%" } ?: "相机电量未知"
            return value + if (charging == true) "，充电中" else ""
        }
    }
}
