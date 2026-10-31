// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.app.Activity
import android.content.Intent
import androidx.appcompat.app.AppCompatActivity
import com.nxwa.nxconnect.R

object OneUiTransitions {
    @Suppress("DEPRECATION")
    fun open(activity: Activity, intent: Intent) {
        activity.startActivity(intent)
        val enabled = NxMotion.enabled(activity.window.decorView)
        activity.overridePendingTransition(if (enabled) R.anim.oneui_page_enter else 0, if (enabled) R.anim.oneui_page_exit else 0)
    }

    @Suppress("DEPRECATION")
    fun close(activity: Activity) {
        val enabled = NxMotion.enabled(activity.window.decorView)
        activity.overridePendingTransition(if (enabled) R.anim.oneui_page_return else 0, if (enabled) R.anim.oneui_page_leave else 0)
    }
}

/** Information pages share the same short transition on toolbar and system back. */
open class OneUiInfoActivity : AppCompatActivity() {
    override fun finish() {
        super.finish()
        OneUiTransitions.close(this)
    }
}
