// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.card.MaterialCardView
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.databinding.ActivityInfoPageBinding

/** Shared reading layout for camera information and connection instructions. */
class OneUiInfoPage(val activity: AppCompatActivity, title: String) {
    val binding = ActivityInfoPageBinding.inflate(activity.layoutInflater)

    init {
        activity.setContentView(binding.root)
        binding.pageTitle.text = title
        binding.pageToolbar.setNavigationOnClickListener { activity.onBackPressedDispatcher.onBackPressed() }
        OneUiHeader(binding.root, binding.pageAppBar, binding.pageHeader, binding.pageToolbar) { title }
        EdgeToEdge.setup(activity)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        ViewCompat.requestApplyInsets(binding.root)
    }

    fun dp(value: Int) = (value * activity.resources.displayMetrics.density).toInt()

    fun text(value: String, size: Float = 15f, secondary: Boolean = false): TextView = TextView(activity).apply {
        text = value
        textSize = size
        setTextColor(ContextCompat.getColor(activity, if (secondary) R.color.nx_on_surface_variant else R.color.nx_on_surface))
        setLineSpacing(dp(4).toFloat(), 1f)
        layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    }

    fun heading(value: String): TextView = text(value, 24f).apply {
        gravity = Gravity.CENTER
        setPadding(dp(20), dp(28), dp(20), dp(34))
        binding.pageContent.addView(this)
    }

    fun card(title: String? = null): LinearLayout {
        val card = MaterialCardView(activity).apply {
            radius = resources.getDimension(R.dimen.oneui_card_corner)
            cardElevation = 0f
            strokeWidth = 0
            setCardBackgroundColor(ContextCompat.getColor(activity, R.color.nx_surface_container))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8)
            }
        }
        val content = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(18))
        }
        if (title != null) content.addView(text(title, 17f).apply { setPadding(0, 0, 0, dp(10)) })
        card.addView(content)
        binding.pageContent.addView(card)
        return content
    }

    fun row(parent: LinearLayout, label: String, value: String) {
        parent.addView(LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.TOP
            setPadding(0, dp(10), 0, dp(10))
            addView(text(label, 14f, secondary = true).apply {
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, .38f)
                setPadding(0, 0, dp(12), 0)
            })
            addView(text(value, 15f).apply {
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, .62f)
                gravity = Gravity.END
                setTextIsSelectable(true)
            })
        })
    }
}
