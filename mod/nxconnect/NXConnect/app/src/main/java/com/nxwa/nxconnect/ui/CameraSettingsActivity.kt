// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.LinearLayout
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.net.NxCamera
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/** Stock menu access and loaded extensions share one entry; capabilities remain device supplied. */
class CameraSettingsActivity : OneUiInfoActivity() {
    private lateinit var page: OneUiInfoPage
    private var refreshJob: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        page = OneUiInfoPage(this, "相机设置")
        page.binding.pageToolbar.menu.add("刷新").setOnMenuItemClickListener {
            refreshCapabilities()
            true
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                NxApp.connection.collect {
                    refreshJob?.cancel()
                    render()
                    refreshCapabilities()
                }
            }
        }
    }

    private fun refreshCapabilities() {
        refreshJob?.cancel()
        val camera = NxApp.camera ?: return
        refreshJob = lifecycleScope.launch {
            try {
                camera.info()
                if (NxApp.camera === camera) render()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (NxApp.camera === camera) render("未能刷新设置能力，请稍后重试。")
            }
        }
    }

    private fun render(message: String? = null) {
        page.binding.pageContent.removeAllViews()
        val camera = NxApp.camera
        action(page.card(), "LUT 库", "管理手机 LUT 与切换相机色彩") {
            OneUiTransitions.open(this, Intent(this, LutLibraryActivity::class.java)
                .putExtra("camera_tab", NxApp.camera?.profile?.looks == true))
        }
        if (camera == null) {
            page.card().addView(page.text("连接相机后可查看和调整设置。", secondary = true))
            return
        }
        val profile = camera.profile
        if (profile.hasStockMenu) {
            action(page.card(), "原厂菜单", "查看相机屏幕，用 MENU、方向键和 OK 调整。") {
                if (NxApp.camera === camera) OneUiTransitions.open(this,
                    Intent(this, RemoteActivity::class.java).putExtra("show_camera_screen", true))
            }
            page.card().addView(page.text(
                "菜单可用项目由相机当前模式决定。Wi-Fi 模式可能限制拍摄设置；返回拍摄模式可能断开无线连接。",
                size = 13f, secondary = true))
        }
        if (profile.hasBodySettings) {
            val extensions = page.card("机身扩展")
            if (profile.isoControls || profile.shutterLimit || profile.dialLock) {
                action(extensions, "曝光与拨盘", "ISO、快门限制及拨盘控制") {
                    openExtension(camera, ExposureSettingsDialog.Section.CONTROLS)
                }
            }
            if (profile.recipes || profile.recipeStyle) {
                action(extensions, "配方", "选择与调整机内配方") {
                    openExtension(camera, ExposureSettingsDialog.Section.RECIPES)
                }
            }
            if (profile.colorStyle || profile.pictureWizard) {
                action(extensions, "色彩微调", "画面向导与 RGB 参数") {
                    openExtension(camera, ExposureSettingsDialog.Section.TUNING)
                }
            }
        } else {
            page.card("机身扩展").also { card ->
                card.addView(page.text("相机尚未提供配方、LUT 或曝光扩展设置。", secondary = true))
                action(card, "查看 Mod 状态", "查看机内模块与启动配置") {
                    OneUiTransitions.open(this, Intent(this, ModManagerActivity::class.java))
                }
            }
        }
        message?.let { page.card().addView(page.text(it, size = 13f, secondary = true)) }
    }

    private fun openExtension(camera: NxCamera, section: ExposureSettingsDialog.Section) {
        if (NxApp.camera !== camera || !camera.profile.hasBodySettings) return
        ExposureSettingsDialog(this, camera) {
            if (NxApp.camera === camera) OneUiTransitions.open(this,
                Intent(this, RemoteActivity::class.java).putExtra("open_stock_iso", true))
        }.show(section)
    }

    private fun action(parent: LinearLayout, title: String, summary: String, click: () -> Unit) {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            minimumHeight = page.dp(76)
            setPadding(0, page.dp(10), 0, page.dp(10))
            val label = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                addView(page.text(title, 17f).apply {
                    typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                })
                addView(page.text(summary, 13f, secondary = true).apply {
                    setPadding(0, page.dp(4), page.dp(8), 0)
                })
            }
            addView(label, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            addView(androidx.appcompat.widget.AppCompatImageView(context).apply {
                setImageResource(R.drawable.ic_chevron_right)
                imageTintList = android.content.res.ColorStateList.valueOf(
                    androidx.core.content.ContextCompat.getColor(context, R.color.nx_on_surface_variant))
                importantForAccessibility = android.view.View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }, LinearLayout.LayoutParams(page.dp(20), page.dp(20)))
            val attrs = obtainStyledAttributes(intArrayOf(android.R.attr.selectableItemBackground))
            background = attrs.getDrawable(0)
            attrs.recycle()
            isFocusable = true
            setOnClickListener { click() }
        }
        parent.addView(row)
    }
}
