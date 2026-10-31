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

import android.graphics.drawable.Drawable
import androidx.core.content.ContextCompat
import androidx.core.graphics.drawable.DrawableCompat

/** 给状态点这类小圆形 Drawable 上色的小工具（避免到处写 wrap/mutate 的样板）。 */
object TintUtil {

    /**
     * 用颜色资源给视图背景上色。
     * 会 mutate 一份副本再设置回去，避免多个视图共享同一 Drawable 时互相影响。
     */
    fun tintBackground(drawable: Drawable?, colorRes: Int): Drawable? {
        val d = drawable ?: return null
        return try {
            val wrapped = DrawableCompat.wrap(d.mutate())
            DrawableCompat.setTint(wrapped, androidx.core.content.ContextCompat.getColor(
                com.nxwa.nxconnect.NxApp.ctx(), colorRes,
            ))
            wrapped
        } catch (e: Exception) {
            d
        }
    }
}
