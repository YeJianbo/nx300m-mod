// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.res.Configuration
import android.view.View
import android.widget.TextView
import com.google.android.material.appbar.AppBarLayout
import com.google.android.material.appbar.MaterialToolbar

/** Keep the large title solid while it scrolls; the toolbar takes over once it leaves. */
class OneUiHeader(
    root: View,
    private val appBar: AppBarLayout,
    private val expandedHeader: View,
    private val toolbar: MaterialToolbar,
    private val expandedHeightDp: Int? = null,
    private val title: () -> CharSequence,
) {
    private var offset = 0
    private var toolbarTitle: TextView? = null
    private var toolbarSubtitle: TextView? = null
    var subtitle: CharSequence? = null
        set(value) {
            field = value
            // Battery spans can change while their plain text stays the same.
            toolbar.subtitle = value
            toolbarSubtitle = null
        }

    init {
        appBar.addOnOffsetChangedListener(AppBarLayout.OnOffsetChangedListener { _, value ->
            offset = value
            refresh()
        })
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            val landscape = root.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
            val density = root.resources.displayMetrics.density
            val height = when {
                landscape -> 0
                expandedHeightDp != null -> (expandedHeightDp * density).toInt()
                else -> (root.height * .34f).toInt()
                    .coerceIn((220 * density).toInt(), (300 * density).toInt())
            }
            if (expandedHeader.layoutParams.height != height) {
                expandedHeader.layoutParams = expandedHeader.layoutParams.apply { this.height = height }
            }
            refresh()
        }
    }

    fun refresh() {
        val collapsed = expandedHeader.height == 0 || -offset >= expandedHeader.height * .82f
        expandedHeader.alpha = 1f
        expandedHeader.importantForAccessibility = if (collapsed) View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
            else View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
        val nextTitle = title()
        // Clearing Toolbar.title removes its TextView and requests a CoordinatorLayout pass.
        // Keep that child measured while crossing the collapse threshold during a drag or snap.
        if (toolbar.title?.toString().orEmpty() != nextTitle.toString()) toolbar.title = nextTitle
        if (toolbar.subtitle?.toString().orEmpty() != subtitle?.toString().orEmpty()) toolbar.subtitle = subtitle
        if (toolbarTitle?.parent !== toolbar) {
            toolbarTitle = (0 until toolbar.childCount)
                .map { toolbar.getChildAt(it) }
                .filterIsInstance<TextView>()
                .firstOrNull { it.text == toolbar.title }
        }
        toolbarTitle?.let {
            it.alpha = if (collapsed) 1f else 0f
            it.importantForAccessibility = if (collapsed) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }
        if (toolbarSubtitle?.parent !== toolbar) {
            toolbarSubtitle = (0 until toolbar.childCount)
                .map { toolbar.getChildAt(it) }
                .filterIsInstance<TextView>()
                .firstOrNull { it !== toolbarTitle && it.text == toolbar.subtitle }
        }
        toolbarSubtitle?.let {
            it.alpha = if (collapsed) 1f else 0f
            it.importantForAccessibility = if (collapsed) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }
    }
}
