// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Rect
import android.graphics.RectF
import android.os.Build
import android.provider.Settings
import android.view.animation.PathInterpolator

internal fun viewerMotionInterpolator() = PathInterpolator(.22f, .25f, 0f, 1f)

internal fun viewerAnimationsEnabled(context: Context): Boolean =
    if (Build.VERSION.SDK_INT >= 26) ValueAnimator.areAnimatorsEnabled()
    else Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f

internal fun viewerMotionCompletionDelay(context: Context, duration: Long): Long =
    (duration * maxOf(1f, Settings.Global.getFloat(context.contentResolver,
        Settings.Global.ANIMATOR_DURATION_SCALE, 1f))).toLong() + 40L

/** A fit-centered photo covers the source tile before its local crop opens. */
internal class ViewerTileTransition(
    source: RectF, fittedWidth: Float, fittedHeight: Float,
    val stageWidth: Int, val stageHeight: Int,
) {
    val startScale = maxOf(source.width() / fittedWidth, source.height() / fittedHeight)
    val translationX = source.centerX() - stageWidth / 2f
    val translationY = source.centerY() - stageHeight / 2f
    private val startClip = RectF(
        (stageWidth - source.width() / startScale) / 2f,
        (stageHeight - source.height() / startScale) / 2f,
        (stageWidth + source.width() / startScale) / 2f,
        (stageHeight + source.height() / startScale) / 2f,
    )

    fun clip(progress: Float): Rect = Rect(
        (startClip.left * (1f - progress)).toInt(),
        (startClip.top * (1f - progress)).toInt(),
        (startClip.right + (stageWidth - startClip.right) * progress).toInt(),
        (startClip.bottom + (stageHeight - startClip.bottom) * progress).toInt(),
    )
}
