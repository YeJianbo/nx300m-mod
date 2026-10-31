/* SPDX-License-Identifier: AGPL-3.0-or-later */
package com.nxwa.nxconnect.ui

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Rect
import android.graphics.drawable.ColorDrawable
import android.content.res.ColorStateList
import android.util.AttributeSet
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ImageView
import android.widget.PopupWindow
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.core.widget.NestedScrollView
import com.nxwa.nxconnect.R

/** Floating navigation surface; XML may provide a component-specific outline. */
class GalleryGlass @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : LinearLayout(context, attrs) {
    var source: View? = null
    init {
        if (background == null) setBackgroundResource(R.drawable.bg_oneui_floating_toolbar)
        clipToOutline = true
    }
}

object GalleryChrome {
    fun press(view: View) = NxMotion.touchBounce(view)

    fun menu(activity: Activity, anchor: View, items: List<Pair<String, () -> Unit>>) {
        val dp = activity.resources.displayMetrics.density
        val frame = Rect().also { anchor.getWindowVisibleDisplayFrame(it) }
        val position = IntArray(2).also { anchor.getLocationOnScreen(it) }
        if (position[1] > frame.centerY()) {
            bottomMenu(activity, items)
            return
        }
        val width = (240 * dp).toInt().coerceAtMost((frame.width() - 24 * dp).toInt().coerceAtLeast(1))
        val body = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, (6 * dp).toInt(), 0, (6 * dp).toInt())
        }
        val scroll = NestedScrollView(activity).apply {
            isFillViewport = false
            isVerticalScrollBarEnabled = false
            setBackgroundResource(R.drawable.bg_oneui_dialog)
            clipToOutline = true
            addView(body)
        }
        val popup = PopupWindow(scroll, width, ViewGroup.LayoutParams.WRAP_CONTENT, true).apply {
            setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))
            elevation = 8 * dp
            isOutsideTouchable = true
            animationStyle = R.style.NxPopupAnimation
        }
        items.forEach { (title, action) ->
            body.addView(TextView(activity).apply {
                text = title
                textSize = 17f
                setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
                val drawable = ContextCompat.getDrawable(activity, menuPresentation(title).second)?.mutate()
                drawable?.setTint(ContextCompat.getColor(activity, R.color.nx_on_surface))
                drawable?.setBounds(0, 0, (24 * dp).toInt(), (24 * dp).toInt())
                setCompoundDrawablesRelative(drawable, null, null, null)
                compoundDrawablePadding = (14 * dp).toInt()
                gravity = Gravity.CENTER_VERTICAL
                setPadding((24 * dp).toInt(), (12 * dp).toInt(), (24 * dp).toInt(), (12 * dp).toInt())
                minHeight = (48 * dp).toInt()
                setBackgroundResource(R.drawable.bg_gallery_touch)
                setOnClickListener { popup.dismiss(); action() }
            })
        }
        scroll.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec((frame.height() - 96 * dp).toInt().coerceAtLeast(1), View.MeasureSpec.AT_MOST))
        popup.height = scroll.measuredHeight
        // Framework anchoring chooses above/below and keeps the popup inside the window.
        popup.showAsDropDown(anchor, 0, (4 * dp).toInt(), Gravity.END)
    }

    private fun bottomMenu(activity: Activity, items: List<Pair<String, () -> Unit>>) {
        val density = activity.resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()
        val dialog = NxBottomSheetDialog(activity)
        val panel = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(8), dp(16), dp(8), dp(12))
            setBackgroundResource(R.drawable.bg_oneui_menu_panel)
            clipToOutline = true
        }
        panel.addView(View(activity).apply {
            setBackgroundResource(R.drawable.bg_oneui_drag_handle)
        }, LinearLayout.LayoutParams(dp(36), dp(4)).apply {
            gravity = Gravity.CENTER_HORIZONTAL
            bottomMargin = dp(12)
        })
        val columns = if (items.size > 6) 4 else 3
        items.chunked(columns).forEach { rowItems ->
            val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
            rowItems.forEach { (title, action) ->
                val (label, icon) = menuPresentation(title)
                val cell = LinearLayout(activity).apply {
                    orientation = LinearLayout.VERTICAL
                    gravity = Gravity.CENTER_HORIZONTAL
                    setPadding(dp(2), dp(8), dp(2), dp(8))
                    contentDescription = title
                    isFocusable = true
                    setBackgroundResource(R.drawable.bg_gallery_touch)
                    setOnClickListener { dialog.dismiss(); action() }
                }
                cell.addView(ImageView(activity).apply {
                    setPadding(dp(10), dp(10), dp(10), dp(10))
                    setImageResource(icon)
                    imageTintList = ColorStateList.valueOf(ContextCompat.getColor(activity, R.color.nx_on_surface))
                    setBackgroundResource(R.drawable.bg_oneui_action_icon)
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                }, LinearLayout.LayoutParams(dp(48), dp(48)))
                cell.addView(TextView(activity).apply {
                    text = label
                    textSize = 13f
                    setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
                    gravity = Gravity.CENTER
                    minHeight = dp(36)
                    maxLines = 2
                    importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
                }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
                press(cell)
                row.addView(cell, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            }
            repeat(columns - rowItems.size) {
                row.addView(View(activity), LinearLayout.LayoutParams(0, 1, 1f))
            }
            panel.addView(row)
        }
        val scroll = NestedScrollView(activity).apply {
            isVerticalScrollBarEnabled = false
            setPadding(dp(8), 0, dp(8), dp(8))
            clipToPadding = false
            addView(panel)
        }
        dialog.setContentView(scroll)
        dialog.show()
    }

    private fun menuPresentation(title: String): Pair<String, Int> = when {
        title.contains("NFC", ignoreCase = true) || title.contains("碰一碰") -> title to R.drawable.ic_oneui_nfc
        title.contains("断开") || title.contains("解除绑定") -> title to R.drawable.ic_oneui_disconnect
        title.contains("帮助") -> title to R.drawable.ic_oneui_help
        title.contains("手动连接") || title.contains("WLAN") -> title to R.drawable.ic_wifi
        title.contains("查找相机") -> title to R.drawable.ic_search
        title.contains("连接与外观") -> title to R.drawable.ic_oneui_settings
        title.contains("连接") || title.contains("配对") -> title to R.drawable.ic_oneui_connect
        title.contains("Mod", ignoreCase = true) || title.contains("固件") -> title to R.drawable.ic_oneui_mod
        title.contains("外观") -> title to R.drawable.ic_oneui_appearance
        title.contains("关于") -> title to R.drawable.ic_gallery_info
        title.contains("选择") -> "选择照片" to R.drawable.ic_check
        title.contains("相机设置") || title.contains("色彩工作台") -> "相机设置" to R.drawable.ic_oneui_settings
        title.contains("刷新") -> "刷新照片" to R.drawable.ic_oneui_refresh
        title.contains("批量补充") -> "补充镜头" to R.drawable.ic_lens
        title.contains("远程") -> "遥控拍摄" to R.drawable.ic_remote
        title.contains("详细") -> "详细信息" to R.drawable.ic_gallery_info
        title.contains("编辑") -> "编辑信息" to R.drawable.ic_gallery_edit
        title.contains("同步") -> "同步到相机" to R.drawable.ic_oneui_refresh
        title.contains("还原相机") -> "还原原始照片" to R.drawable.ic_oneui_restore
        title.contains("还原") -> "还原信息" to R.drawable.ic_oneui_restore
        title.contains("下载") -> "下载原文件" to R.drawable.ic_download
        title.contains("分享") -> title to R.drawable.ic_gallery_share
        title.contains("收藏") -> title to R.drawable.ic_gallery_heart
        title.contains("镜头") -> title to R.drawable.ic_lens
        else -> title to R.drawable.ic_gallery_more
    }
}
