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

import android.app.Activity
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding

/**
 * 系统栏（状态栏/导航栏/刘海）适配。
 *
 * 之前的问题：主题里设了 `statusBarColor=transparent` 让内容延伸到状态栏下方，
 * 但**没有任何页面处理 insets**，于是顶部内容直接被状态栏与刘海挡掉（用户实测）。
 *
 * 这里的做法：内容延伸到系统栏下方（现代 App 的观感），
 * 但给顶部工具栏与底部内容分别补上对应的内边距。
 */
object EdgeToEdge {

    /** 在 Activity onCreate 里尽早调用：声明由我们处理 insets */
    fun setup(activity: Activity) {
        WindowCompat.setDecorFitsSystemWindows(activity.window, false)
        val light =
            activity.resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK != android.content.res.Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(activity.window, activity.window.decorView)
            .isAppearanceLightStatusBars = light
        WindowCompat.getInsetsController(activity.window, activity.window.decorView)
            .isAppearanceLightNavigationBars = light
    }

    /**
     * 给顶部视图加状态栏高度的**外边距**（不用内边距）。
     *
     * ★ 为什么用 margin 而不是 padding：
     *   本项目 compileSdk 35（Android 15），系统**强制边到边**，且 `statusBarColor`
     *   已被忽略 —— 所有 App 的内容都画到状态栏后面。
     *   实测在用户的设备上对 `MaterialToolbar` 加 `paddingTop` **没有生效**
     *   （用户反馈"顶栏被挡住了一点"）。原因通常是视图自身或其 style 里有
     *   `fitsSystemWindows`、或 ActionBar/Toolbar 的 padding 被内部布局覆盖。
     *
     *   给**根容器加 margin** 则一定生效：它在父布局的测量阶段就参与，
     *   不依赖任何子视图对 padding 的处理方式，整块内容一起下移，
     *   也不会截断工具栏文字。
     *
     * @param includeDisplayCutout 是否把刘海高度也算进去
     */
    fun marginTop(view: View, includeDisplayCutout: Boolean = true) {
        val lp = view.layoutParams as? android.view.ViewGroup.MarginLayoutParams
            ?: throw IllegalArgumentException("marginTop 需要视图有 MarginLayoutParams")
        val base = lp.topMargin
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val bars = insets.getInsets(
                if (includeDisplayCutout) {
                    WindowInsetsCompat.Type.statusBars() or WindowInsetsCompat.Type.displayCutout()
                } else {
                    WindowInsetsCompat.Type.statusBars()
                },
            )
            val p = v.layoutParams as android.view.ViewGroup.MarginLayoutParams
            if (p.topMargin != base + bars.top) {
                p.topMargin = base + bars.top
                v.layoutParams = p
            }
            insets
        }
        ViewCompat.requestApplyInsets(view)
    }

    /**
     * 给顶部视图加状态栏高度的内边距。
     * @param includeDisplayCutout 是否把刘海高度也算进去（工具栏需要，普通容器一般不用）
     */
    fun padTop(view: View, includeDisplayCutout: Boolean = true) {
        val base = view.paddingTop
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val bars = insets.getInsets(
                if (includeDisplayCutout) {
                    WindowInsetsCompat.Type.statusBars() or WindowInsetsCompat.Type.displayCutout()
                } else {
                    WindowInsetsCompat.Type.statusBars()
                },
            )
            v.updatePadding(top = base + bars.top)
            insets
        }
        ViewCompat.requestApplyInsets(view)
    }

    /**
     * 给底部视图加导航栏高度的内边距。
     *
     * ★ 注意 `base` 取的是视图**当前已生效的像素值**（XML 里写的是 dp，但 getPaddingBottom
     *   返回的已经是 px），所以它**不能再乘一次 density**；只有 [extraDp] 这种"额外指定
     *   的 dp 值"才需要换算。早期版本把两者混在一起，导致底部内边距被放大一倍多。
     */
    fun padBottom(view: View, extraDp: Int = 0) {
        val base = view.paddingBottom
        val extra = (view.resources.displayMetrics.density * extraDp).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.navigationBars())
            v.updatePadding(bottom = base + bars.bottom + extra)
            insets
        }
        ViewCompat.requestApplyInsets(view)
    }

    /** 左右同时避开横屏时的刘海 */
    fun padHorizontal(view: View, extraDp: Int = 0) {
        val baseLeft = view.paddingLeft
        val baseRight = view.paddingRight
        val extra = (view.resources.displayMetrics.density * extraDp).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            v.updatePadding(
                left = baseLeft + bars.left + extra,
                right = baseRight + bars.right + extra,
            )
            insets
        }
        ViewCompat.requestApplyInsets(view)
    }
}
