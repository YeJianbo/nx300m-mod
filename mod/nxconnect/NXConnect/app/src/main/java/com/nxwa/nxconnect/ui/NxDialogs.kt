// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Context
import android.content.DialogInterface
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.widget.ButtonBarLayout
import androidx.core.content.ContextCompat
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputLayout
import com.nxwa.nxconnect.R
import kotlin.math.min

/** Keeps the existing AlertDialog callbacks while sharing the app's bottom dialog presentation. */
class NxDialogBuilder(context: Context) : MaterialAlertDialogBuilder(context, R.style.NxDialog) {
    init {
        setBackgroundInsetStart(0)
        setBackgroundInsetEnd(0)
        setBackgroundInsetTop(0)
        setBackgroundInsetBottom(0)
    }

    override fun setView(view: View?): MaterialAlertDialogBuilder = super.setView(
        if (view is EditText) FrameLayout(context).apply {
            setPadding(dimension(R.dimen.oneui_content_inset), dp(8), dimension(R.dimen.oneui_content_inset), dp(8))
            addView(view, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        } else view,
    )

    override fun create(): AlertDialog {
        val dialog = super.create()
        val window = dialog.window ?: return dialog
        // MaterialAlertDialogBuilder creates its own tinted shape after resolving windowBackground.
        // Replace that final drawable so surface elevation and dynamic palettes cannot tint the panel.
        window.setBackgroundDrawable(GradientDrawable().apply {
            setColor(ContextCompat.getColor(context, R.color.nx_surface))
            cornerRadius = context.resources.getDimension(R.dimen.oneui_panel_corner)
        })
        window.setGravity(Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        window.attributes = window.attributes.apply {
            dimAmount = .25f
            y = dimension(R.dimen.oneui_panel_inset)
            width = panelWidth()
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
        // Do not consume setOnShowListener: forms use it to install validation and save callbacks.
        window.decorView.addOnAttachStateChangeListener(object : View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(view: View) {
                view.post {
                    if (!dialog.isShowing) return@post
                    window.setLayout(
                        panelWidth(),
                        WindowManager.LayoutParams.WRAP_CONTENT,
                    )
                    styleInputs(view)
                    styleButtons(dialog)
                }
            }
            override fun onViewDetachedFromWindow(view: View) = Unit
        })
        return dialog
    }

    private fun styleButtons(dialog: AlertDialog) {
        val buttons = listOf(
            DialogInterface.BUTTON_NEUTRAL,
            DialogInterface.BUTTON_NEGATIVE,
            DialogInterface.BUTTON_POSITIVE,
        ).mapNotNull { dialog.getButton(it)?.takeIf { button -> button.visibility == View.VISIBLE } }
        val row = buttons.firstOrNull()?.parent as? LinearLayout ?: return
        (row as? ButtonBarLayout)?.setAllowStacking(false)
        row.orientation = LinearLayout.HORIZONTAL
        row.gravity = Gravity.CENTER
        row.setPadding(dp(12), dp(4), dp(12), dp(12))
        for (index in 0 until row.childCount) {
            val child = row.getChildAt(index)
            if (child !is Button) child.visibility = View.GONE
        }
        buttons.forEach { button ->
            button.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            button.minimumWidth = 0
            button.minWidth = 0
            button.minimumHeight = dp(52)
            button.setPadding(dp(8), dp(8), dp(8), dp(8))
            button.textSize = 17f
            button.isAllCaps = false
            button.letterSpacing = 0f
            button.typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
            button.setTextColor(ColorStateList(
                arrayOf(intArrayOf(-android.R.attr.state_enabled), intArrayOf()),
                intArrayOf(ContextCompat.getColor(context, R.color.nx_outline), ContextCompat.getColor(context, R.color.nx_primary)),
            ))
        }
    }

    private fun styleInputs(view: View) {
        if (view is TextInputLayout) {
            view.boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_NONE
            view.setHintTextAppearance(R.style.NxInputLabel)
            view.defaultHintTextColor = ColorStateList.valueOf(ContextCompat.getColor(context, R.color.nx_on_surface_variant))
        }
        if (view is EditText) {
            view.setBackgroundResource(R.drawable.bg_oneui_input_underline)
            view.backgroundTintList = null
            view.setTextColor(ContextCompat.getColor(context, R.color.nx_on_surface))
            view.setHintTextColor(ContextCompat.getColor(context, R.color.nx_on_surface_variant))
            view.textSize = 18f
            view.minimumHeight = dp(52)
            view.setPadding(0, dp(14), 0, dp(10))
        }
        if (view is ViewGroup) {
            for (index in 0 until view.childCount) styleInputs(view.getChildAt(index))
        }
    }

    private fun dp(value: Int) = (context.resources.displayMetrics.density * value + .5f).toInt()
    private fun dimension(id: Int) = context.resources.getDimensionPixelSize(id)
    private fun panelWidth() = min(context.resources.displayMetrics.widthPixels - 2 * dimension(R.dimen.oneui_panel_inset), dp(560))
}
