# -*- coding: utf-8 -*-
"""Generate high-quality OneUI 9 ExposureSettingsDialog (Color Studio & Body Settings BottomSheet)."""
import os
from pathlib import Path

target_file = Path("mod/nxconnect/NXConnect/app/src/main/java/com/nxwa/nxconnect/ui/ExposureSettingsDialog.kt")

code = '''// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.slider.Slider
import com.google.android.material.materialswitch.MaterialSwitch
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.net.ExposureControls
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.net.NxException
import com.nxwa.nxconnect.net.RecipeControls
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Samsung OneUI 9 风格「液态色彩工作台与机身设置」抽屉。
 * 融合 68 款胶片配方画廊、19 款 3D LUT 风格、RGB 三通道微调滑块、Picture Wizard 原厂画面以及硬件级曝光与拨盘锁控制。
 */
class ExposureSettingsDialog(
    private val activity: AppCompatActivity,
    private val camera: NxCamera,
    private val openStockIso: () -> Unit,
) {
    private enum class Group { ISO, SHUTTER, DIAL, RGB, PW, LOOKS, RECIPE }
    private enum class Field(val title: String, val wire: String, val group: Group) {
        STEP("ISO 步长", "iso_step", Group.ISO),
        EXPANSION("扩展 ISO", "iso_expansion", Group.ISO),
        MAXIMUM("Auto ISO 上限", "auto_iso_max", Group.ISO),
        FASTEST("自动曝光最快快门", "fastest", Group.SHUTTER),
        DIAL("指令拨盘锁", "enabled", Group.DIAL),
        RED("RGB / R 红增益", "red", Group.RGB),
        GREEN("RGB / G 绿增益", "green", Group.RGB),
        BLUE("RGB / B 蓝增益", "blue", Group.RGB),
        RECIPE("七维配方", "id", Group.RECIPE),
        SATURATION("原厂饱和度", "saturation", Group.PW),
        SHARPNESS("原厂锐度", "sharpness", Group.PW),
        CONTRAST("原厂对比度", "contrast", Group.PW),
        TONE("原厂色彩角度", "color_tone", Group.PW),
        LOOK("LUT 风格", "id", Group.LOOKS)
    }

    private data class LookChoice(val id: String?, val label: String)

    private val values = mutableMapOf<Field, Any>()
    private var looksState: ExposureControls.LooksState? = null
    private var recipeCatalogue: RecipeControls.Catalogue? = null
    private var recipeState: RecipeControls.State? = null
    private var recipeSnapshotConfirmed = false
    private var recipeIssue = ""

    private var busy = false
    private var job: Job? = null
    private var recipePoll: Job? = null

    // UI 组件
    private lateinit var sheet: BottomSheetDialog
    private lateinit var statusText: TextView
    private lateinit var statusBadge: TextView
    private lateinit var refreshBtn: TextView

    // Tab 切换
    private var currentTab = 0
    private val tabBtns = mutableListOf<TextView>()
    private val tabContainers = mutableListOf<View>()

    // Tab 0: 配方视图
    private lateinit var recipeContainer: LinearLayout
    private lateinit var recipeCategoryGroup: LinearLayout
    private lateinit var recipeCardList: LinearLayout
    private lateinit var btnResetRecipe: Button
    private lateinit var recipeDetailText: TextView
    private var selectedRecipeCategory = 0

    // Tab 1: 3D LUT 视图
    private lateinit var looksCardList: LinearLayout
    private lateinit var looksDetailText: TextView
    private lateinit var btnClearLook: Button

    // Tab 2: 色彩微调视图
    private lateinit var sliderRed: Slider
    private lateinit var sliderGreen: Slider
    private lateinit var sliderBlue: Slider
    private lateinit var labelRed: TextView
    private lateinit var labelGreen: TextView
    private lateinit var labelBlue: TextView
    private lateinit var btnResetRgb: Button
    private lateinit var sliderSat: Slider
    private lateinit var sliderSharp: Slider
    private lateinit var sliderContrast: Slider
    private lateinit var sliderTone: Slider
    private lateinit var labelSat: TextView
    private lateinit var labelSharp: TextView
    private lateinit var labelContrast: TextView
    private lateinit var labelTone: TextView
    private lateinit var pwDetailText: TextView

    // Tab 3: 机身控制视图
    private lateinit var switchDialLock: MaterialSwitch
    private lateinit var switchIsoExpansion: MaterialSwitch
    private lateinit var isoStepGroup: LinearLayout
    private lateinit var autoIsoMaxGroup: LinearLayout
    private lateinit var fastestShutterGroup: LinearLayout
    private lateinit var btnOpenStockIso: Button

    private fun dp(v: Int): Int = (v * activity.resources.displayMetrics.density).toInt()

    private fun fluidCurve() = PathInterpolator(0.22f, 1f, 0.36f, 1f)

    private fun roundedBg(bgColor: Int, radiusDp: Int, strokeColor: Int = 0, strokeWidthDp: Int = 0): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(radiusDp).toFloat()
            setColor(bgColor)
            if (strokeWidthDp > 0 && strokeColor != 0) {
                setStroke(dp(strokeWidthDp), strokeColor)
            }
        }
    }

    fun show() {
        sheet = BottomSheetDialog(activity).apply {
            window?.setWindowAnimations(0) // 交由系统底抽屉自带贝塞尔动效
        }

        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface), 24)
            setPadding(dp(16), dp(10), dp(16), dp(20))
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

        // 1. 顶部拖动药丸把手
        val handleBar = View(activity).apply {
            layoutParams = LinearLayout.LayoutParams(dp(44), dp(4)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = dp(4)
                bottomMargin = dp(12)
            }
            background = roundedBg(Color.parseColor("#33888888"), 2)
        }
        root.addView(handleBar)

        // 2. 头部主信息行
        val header = RelativeLayout(activity).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(12)
            }
        }

        val titleCol = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            id = View.generateViewId()
        }
        val mainTitle = TextView(activity).apply {
            text = "色彩工作台 & 机身控制"
            textSize = 18f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        }
        statusText = TextView(activity).apply {
            text = "NX300M Mod · 正在同步机身状态…"
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(2), 0, 0)
        }
        titleCol.addView(mainTitle)
        titleCol.addView(statusText)
        header.addView(titleCol)

        // 右侧操作行
        val rightActions = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = RelativeLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                addRule(RelativeLayout.ALIGN_PARENT_END)
                addRule(RelativeLayout.CENTER_VERTICAL)
            }
        }

        statusBadge = TextView(activity).apply {
            text = "读取中"
            textSize = 11f
            setTextColor(Color.WHITE)
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_primary), 12)
            setPadding(dp(10), dp(4), dp(10), dp(4))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginEnd = dp(8)
            }
        }
        rightActions.addView(statusBadge)

        refreshBtn = TextView(activity).apply {
            text = "刷新"
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 14)
            setPadding(dp(10), dp(5), dp(10), dp(5))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginEnd = dp(6)
            }
            GalleryChrome.press(this)
            setOnClickListener { readAll() }
        }
        rightActions.addView(refreshBtn)

        val closeBtn = TextView(activity).apply {
            text = "✕"
            textSize = 14f
            gravity = Gravity.CENTER
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 14)
            layoutParams = LinearLayout.LayoutParams(dp(28), dp(28))
            GalleryChrome.press(this)
            setOnClickListener { sheet.dismiss() }
        }
        rightActions.addView(closeBtn)
        header.addView(rightActions)
        root.addView(header)

        // 3. OneUI 9 流体 Tab 导航栏
        val tabLayout = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            weightSum = 4f
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container_high), 16)
            setPadding(dp(3), dp(3), dp(3), dp(3))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(38)).apply {
                bottomMargin = dp(14)
            }
        }
        val tabTitles = listOf("🎨 配方", "🎞️ LUT", "🎛️ 微调", "⚙️ 控制")
        tabTitles.forEachIndexed { index, title ->
            val tabBtn = TextView(activity).apply {
                text = title
                textSize = 13f
                gravity = Gravity.CENTER
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f)
                setOnClickListener { switchTab(index) }
            }
            tabBtns.add(tabBtn)
            tabLayout.addView(tabBtn)
        }
        root.addView(tabLayout)

        // 4. Tab 内容容器 (高度固定或自适应最大高度)
        val contentStage = FrameLayout(activity).apply {
            val maxH = (activity.resources.displayMetrics.heightPixels * 0.62f).toInt()
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, maxH)
        }

        // --- Tab 0: 胶片配方视图 ---
        buildRecipeTab()
        contentStage.addView(recipeContainer)
        tabContainers.add(recipeContainer)

        // --- Tab 1: 3D LUT 视图 ---
        val looksScroll = buildLooksTab()
        contentStage.addView(looksScroll)
        tabContainers.add(looksScroll)

        // --- Tab 2: 色彩微调视图 ---
        val tuningScroll = buildTuningTab()
        contentStage.addView(tuningScroll)
        tabContainers.add(tuningScroll)

        // --- Tab 3: 机身控制视图 ---
        val controlsScroll = buildControlsTab()
        contentStage.addView(controlsScroll)
        tabContainers.add(controlsScroll)

        root.addView(contentStage)

        sheet.setContentView(root)
        val behavior = BottomSheetBehavior.from(root.parent as View)
        behavior.skipCollapsed = true
        behavior.state = BottomSheetBehavior.STATE_EXPANDED

        sheet.setOnDismissListener {
            job?.cancel()
            recipePoll?.cancel()
        }

        switchTab(0)
        sheet.show()
        readAll()
    }

    private fun switchTab(target: Int) {
        currentTab = target
        tabBtns.forEachIndexed { i, btn ->
            if (i == target) {
                btn.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_primary), 14)
                btn.setTextColor(Color.WHITE)
            } else {
                btn.background = null
                btn.setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            }
        }
        tabContainers.forEachIndexed { i, container ->
            if (i == target) {
                container.visibility = View.VISIBLE
                container.alpha = 0f
                container.translationY = dp(12).toFloat()
                container.animate()
                    .alpha(1f)
                    .translationY(0f)
                    .setDuration(200)
                    .setInterpolator(fluidCurve())
                    .start()
            } else {
                container.visibility = View.GONE
            }
        }
    }

    // ==========================================
    // 视图构建：Tab 0 胶片配方
    // ==========================================
    private fun buildRecipeTab() {
        recipeContainer = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }

        // 分类标签胶囊
        recipeCategoryGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 0, 0, dp(10))
        }
        val categories = listOf("全部 (68)", "经典负片", "反转正片", "黑白单色", "电影色调")
        categories.forEachIndexed { idx, cat ->
            val chip = TextView(activity).apply {
                text = cat
                textSize = 12f
                setPadding(dp(12), dp(6), dp(12), dp(6))
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    marginEnd = dp(8)
                }
                GalleryChrome.press(this)
                setOnClickListener {
                    selectedRecipeCategory = idx
                    renderRecipeChips()
                    renderRecipeCards()
                }
            }
            recipeCategoryGroup.addView(chip)
        }
        recipeContainer.addView(recipeCategoryGroup)
        renderRecipeChips()

        // 垂直可滚动的配方卡片流
        val scroll = ScrollView(activity).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
            isVerticalScrollBarEnabled = false
        }
        recipeCardList = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, dp(12))
        }
        scroll.addView(recipeCardList)
        recipeContainer.addView(scroll)

        // 底部固定操作行：复位按钮与详情折叠
        val bottomBar = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, dp(8), 0, 0)
        }
        btnResetRecipe = Button(activity).apply {
            text = "恢复原厂七维配方与 RGB 增益"
            textSize = 13f
            isAllCaps = false
            setTextColor(Color.WHITE)
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_secondary), 16)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44))
            GalleryChrome.press(this)
            setOnClickListener { writeRecipe(null) }
        }
        bottomBar.addView(btnResetRecipe)

        recipeDetailText = TextView(activity).apply {
            textSize = 11f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(6), 0, 0)
        }
        bottomBar.addView(recipeDetailText)
        recipeContainer.addView(bottomBar)
    }

    private fun renderRecipeChips() {
        for (i in 0 until recipeCategoryGroup.childCount) {
            val chip = recipeCategoryGroup.getChildAt(i) as TextView
            if (i == selectedRecipeCategory) {
                chip.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_primary), 14)
                chip.setTextColor(Color.WHITE)
            } else {
                chip.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 14)
                chip.setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            }
        }
    }

    private fun renderRecipeCards() {
        recipeCardList.removeAllViews()
        val allRecipes = recipeCatalogue?.recipes.orEmpty()
        if (allRecipes.isEmpty()) {
            val empty = TextView(activity).apply {
                text = if (busy) "正在读取 68 套胶片配方…" else "未读取到配方目录或配方桥未就绪"
                textSize = 13f
                gravity = Gravity.CENTER
                setPadding(0, dp(40), 0, 0)
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            }
            recipeCardList.addView(empty)
            return
        }

        // 分类过滤
        val filtered = allRecipes.filter { r ->
            val id = r.id.lowercase()
            when (selectedRecipeCategory) {
                1 -> id.contains("portra") || id.contains("superia") || id.contains("color") || id.contains("gold") || id.contains("c200") || id.contains("provia")
                2 -> id.contains("velvia") || id.contains("ektachrome") || id.contains("astia") || id.contains("positive")
                3 -> id.contains("tri") || id.contains("hp5") || id.contains("acros") || id.contains("mono") || id.contains("b&w") || id.contains("pan")
                4 -> id.contains("cine") || id.contains("eterna") || id.contains("fl") || id.contains("film") || id.contains("standard") || id.contains("custom")
                else -> true
            }
        }

        val activeId = recipeState?.takeIf { it.selectionMatches && !it.superseded }?.selectedId

        filtered.forEach { recipe ->
            val isCurrent = recipe.id == activeId
            val card = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                background = if (isCurrent) {
                    roundedBg(ContextCompat.getColor(activity, R.color.nx_primary_container), 16, ContextCompat.getColor(activity, R.color.nx_primary), 2)
                } else {
                    roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
                }
                setPadding(dp(14), dp(12), dp(14), dp(12))
                GalleryChrome.press(this)
                setOnClickListener {
                    if (!busy && camera.profile.recipeStyle) writeRecipe(recipe)
                }
            }

            val leftCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }

            val titleRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val titleView = TextView(activity).apply {
                text = recipe.label
                textSize = 14f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_on_surface))
            }
            titleRow.addView(titleView)

            if (isCurrent) {
                val activeTag = TextView(activity).apply {
                    text = "● 生效中"
                    textSize = 11f
                    setTextColor(Color.WHITE)
                    background = roundedBg(ContextCompat.getColor(activity, R.color.nx_ok), 10)
                    setPadding(dp(6), dp(2), dp(6), dp(2))
                    layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                        marginStart = dp(8)
                    }
                }
                titleRow.addView(activeTag)
            }
            leftCol.addView(titleRow)

            val descView = TextView(activity).apply {
                text = RecipeControls.describe(recipe.values)
                textSize = 11f
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
                setPadding(0, dp(4), 0, 0)
            }
            leftCol.addView(descView)
            card.addView(leftCol)

            val applyBtn = TextView(activity).apply {
                text = if (isCurrent) "已应用" else "应用"
                textSize = 12f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else Color.WHITE)
                background = roundedBg(
                    if (isCurrent) Color.TRANSPARENT else ContextCompat.getColor(activity, R.color.nx_primary),
                    14,
                    if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else 0,
                    if (isCurrent) 1 else 0
                )
                setPadding(dp(12), dp(6), dp(12), dp(6))
            }
            card.addView(applyBtn)

            recipeCardList.addView(card)
        }
    }

    // ==========================================
    // 视图构建：Tab 1 3D LUT
    // ==========================================
    private fun buildLooksTab(): View {
        val scroll = ScrollView(activity).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            isVerticalScrollBarEnabled = false
        }
        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, dp(16))
        }

        btnClearLook = Button(activity).apply {
            text = "清除 LUT · 恢复原厂色彩"
            textSize = 13f
            isAllCaps = false
            setTextColor(Color.WHITE)
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_secondary), 16)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(44)).apply {
                bottomMargin = dp(12)
            }
            GalleryChrome.press(this)
            setOnClickListener { set(Field.LOOK, LookChoice(null, "原厂")) }
        }
        container.addView(btnClearLook)

        looksCardList = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
        }
        container.addView(looksCardList)

        looksDetailText = TextView(activity).apply {
            textSize = 11f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(8), 0, 0)
        }
        container.addView(looksDetailText)

        scroll.addView(container)
        return scroll
    }

    private fun renderLooksCards() {
        looksCardList.removeAllViews()
        val state = looksState
        if (state == null) {
            val empty = TextView(activity).apply {
                text = if (busy) "正在读取 3D LUT 库…" else "未读取到 LUT 状态或不支持机内 LUT"
                textSize = 13f
                gravity = Gravity.CENTER
                setPadding(0, dp(40), 0, 0)
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            }
            looksCardList.addView(empty)
            return
        }

        val currentId = state.selectedId
        state.looks.filter { it.tableValid && it.selectable }.forEach { look ->
            val isCurrent = !state.stock && look.id == currentId
            val card = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                background = if (isCurrent) {
                    roundedBg(ContextCompat.getColor(activity, R.color.nx_primary_container), 16, ContextCompat.getColor(activity, R.color.nx_primary), 2)
                } else {
                    roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
                }
                setPadding(dp(14), dp(12), dp(14), dp(12))
                GalleryChrome.press(this)
                setOnClickListener {
                    if (!busy && state.writable) set(Field.LOOK, LookChoice(look.id, look.label))
                }
            }

            val leftCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            val titleRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val title = TextView(activity).apply {
                text = look.label
                textSize = 14f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_on_surface))
            }
            titleRow.addView(title)
            if (isCurrent) {
                val tag = TextView(activity).apply {
                    text = "● 已生效"
                    textSize = 11f
                    setTextColor(Color.WHITE)
                    background = roundedBg(ContextCompat.getColor(activity, R.color.nx_ok), 10)
                    setPadding(dp(6), dp(2), dp(6), dp(2))
                    layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                        marginStart = dp(8)
                    }
                }
                titleRow.addView(tag)
            }
            leftCol.addView(titleRow)

            val sub = TextView(activity).apply {
                text = "ID: ${look.id} · D4 硬件 9x9x9 表"
                textSize = 11f
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
                setPadding(0, dp(4), 0, 0)
            }
            leftCol.addView(sub)
            card.addView(leftCol)

            val selectBtn = TextView(activity).apply {
                text = if (isCurrent) "已暂存" else "选择"
                textSize = 12f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else Color.WHITE)
                background = roundedBg(
                    if (isCurrent) Color.TRANSPARENT else ContextCompat.getColor(activity, R.color.nx_primary),
                    14,
                    if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else 0,
                    if (isCurrent) 1 else 0
                )
                setPadding(dp(12), dp(6), dp(12), dp(6))
            }
            card.addView(selectBtn)

            looksCardList.addView(card)
        }
    }

    // ==========================================
    // 视图构建：Tab 2 色彩微调 (RGB & PW 滑块)
    // ==========================================
    private fun buildTuningTab(): View {
        val scroll = ScrollView(activity).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            isVerticalScrollBarEnabled = false
        }
        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, dp(20))
        }

        // RGB 组标题
        val rgbHeader = TextView(activity).apply {
            text = "RGB 传感器通道增益 (100% 为中性)"
            textSize = 14f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            setPadding(0, dp(4), 0, dp(8))
        }
        container.addView(rgbHeader)

        // R 滑块卡片
        val cardR = buildSliderCard("红色增益 (Red Gain)", "#C62828", 0f, 200f, 100f) { v ->
            labelRed.text = "$v% (${v - 100}%)"
        }
        sliderRed = cardR.first; labelRed = cardR.second
        sliderRed.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.RED, slider.value.toInt()) }
        })
        container.addView(cardR.third)

        // G 滑块卡片
        val cardG = buildSliderCard("绿色增益 (Green Gain)", "#2E7D32", 0f, 200f, 100f) { v ->
            labelGreen.text = "$v% (${v - 100}%)"
        }
        sliderGreen = cardG.first; labelGreen = cardG.second
        sliderGreen.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.GREEN, slider.value.toInt()) }
        })
        container.addView(cardG.third)

        // B 滑块卡片
        val cardB = buildSliderCard("蓝色增益 (Blue Gain)", "#1565C0", 0f, 200f, 100f) { v ->
            labelBlue.text = "$v% (${v - 100}%)"
        }
        sliderBlue = cardB.first; labelBlue = cardB.second
        sliderBlue.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.BLUE, slider.value.toInt()) }
        })
        container.addView(cardB.third)

        btnResetRgb = Button(activity).apply {
            text = "一键复位 RGB (100 / 100 / 100)"
            textSize = 13f
            isAllCaps = false
            setTextColor(Color.WHITE)
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_secondary), 16)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(40)).apply {
                topMargin = dp(4)
                bottomMargin = dp(16)
            }
            GalleryChrome.press(this)
            setOnClickListener { write("RGB 复位") { putRgb(camera.resetRgbSettings()) } }
        }
        container.addView(btnResetRgb)

        // Picture Wizard 标题
        val pwHeader = TextView(activity).apply {
            text = "Picture Wizard 原厂画面参数"
            textSize = 14f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            setPadding(0, dp(4), 0, dp(8))
        }
        container.addView(pwHeader)

        // 饱和度
        val cardSat = buildSliderCard("原厂饱和度 (-4 至 +4)", "#D81B60", -4f, 4f, 0f, step = 1f) { v ->
            labelSat.text = if (v > 0) "+$v" else "$v"
        }
        sliderSat = cardSat.first; labelSat = cardSat.second
        sliderSat.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.SATURATION, slider.value.toInt()) }
        })
        container.addView(cardSat.third)

        // 锐度
        val cardSharp = buildSliderCard("原厂锐度 (-4 至 +4)", "#00897B", -4f, 4f, 0f, step = 1f) { v ->
            labelSharp.text = if (v > 0) "+$v" else "$v"
        }
        sliderSharp = cardSharp.first; labelSharp = cardSharp.second
        sliderSharp.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.SHARPNESS, slider.value.toInt()) }
        })
        container.addView(cardSharp.third)

        // 对比度
        val cardContrast = buildSliderCard("原厂对比度 (-4 至 +4)", "#FB8C00", -4f, 4f, 0f, step = 1f) { v ->
            labelContrast.text = if (v > 0) "+$v" else "$v"
        }
        sliderContrast = cardContrast.first; labelContrast = cardContrast.second
        sliderContrast.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.CONTRAST, slider.value.toInt()) }
        })
        container.addView(cardContrast.third)

        // 色彩角度
        val cardTone = buildSliderCard("原厂色彩角度 (-1 为关闭，0°~359°)", "#8E24AA", -1f, 359f, -1f, step = 1f) { v ->
            labelTone.text = if (v == -1) "关闭" else "$v°"
        }
        sliderTone = cardTone.first; labelTone = cardTone.second
        sliderTone.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {}
            override fun onStopTrackingTouch(slider: Slider) { set(Field.TONE, slider.value.toInt()) }
        })
        container.addView(cardTone.third)

        pwDetailText = TextView(activity).apply {
            textSize = 11f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(4), 0, 0)
        }
        container.addView(pwDetailText)

        scroll.addView(container)
        return scroll
    }

    private fun buildSliderCard(
        title: String,
        thumbColorHex: String,
        min: Float,
        max: Float,
        defaultVal: Float,
        step: Float = 1f,
        onUpdate: (Int) -> Unit
    ): Triple<Slider, TextView, View> {
        val card = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
            setPadding(dp(14), dp(10), dp(14), dp(8))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        val topRow = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val titleView = TextView(activity).apply {
            text = title
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val valueView = TextView(activity).apply {
            text = "$defaultVal"
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(thumbColorHex))
        }
        topRow.addView(titleView)
        topRow.addView(valueView)
        card.addView(topRow)

        val slider = Slider(activity).apply {
            valueFrom = min
            valueTo = max
            stepSize = step
            value = defaultVal
            thumbTintList = ColorStateList.valueOf(Color.parseColor(thumbColorHex))
            trackActiveTintList = ColorStateList.valueOf(Color.parseColor(thumbColorHex))
            trackInactiveTintList = ColorStateList.valueOf(Color.parseColor("#33888888"))
            addOnChangeListener { _, value, _ ->
                onUpdate(value.toInt())
            }
        }
        card.addView(slider)
        return Triple(slider, valueView, card)
    }

    // ==========================================
    // 视图构建：Tab 3 机身控制
    // ==========================================
    private fun buildControlsTab(): View {
        val scroll = ScrollView(activity).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            isVerticalScrollBarEnabled = false
        }
        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, dp(20))
        }

        // 1. 指令拨盘锁卡片 (Switch)
        val dialCard = buildSwitchCard("指令拨盘锁 (Dial Lock)", "锁定机身指令波轮，防止在握持或街拍时误触修改曝光") { checked ->
            set(Field.DIAL, checked)
        }
        switchDialLock = dialCard.first
        container.addView(dialCard.second)

        // 2. 扩展 ISO 卡片 (Switch)
        val expCard = buildSwitchCard("扩展 ISO (ISO Expansion)", "开启后可在标准 ISO 范围之外使用扩展档位") { checked ->
            set(Field.EXPANSION, checked)
        }
        switchIsoExpansion = expCard.first
        container.addView(expCard.second)

        // 3. ISO 步长选择卡片
        val stepCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        val stepTitle = TextView(activity).apply {
            text = "ISO 调节步长"
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
        }
        stepCard.addView(stepTitle)

        isoStepGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, 0)
        }
        listOf("one-third" to "1/3 EV 档位", "one" to "1 EV 整数档位").forEach { (wire, label) ->
            val pill = TextView(activity).apply {
                text = label
                textSize = 12f
                gravity = Gravity.CENTER
                setPadding(dp(14), dp(8), dp(14), dp(8))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(6)
                }
                GalleryChrome.press(this)
                setOnClickListener { set(Field.STEP, wire) }
            }
            isoStepGroup.addView(pill)
        }
        stepCard.addView(isoStepGroup)
        container.addView(stepCard)

        // 4. Auto ISO 上限选择
        val maxIsoCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        maxIsoCard.addView(TextView(activity).apply {
            text = "Auto ISO 上限设置"
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
        })
        autoIsoMaxGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, 0)
        }
        ExposureControls.AUTO_ISO_MAX.forEach { iso ->
            val pill = TextView(activity).apply {
                text = "$iso"
                textSize = 11f
                gravity = Gravity.CENTER
                setPadding(dp(8), dp(6), dp(8), dp(6))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(4)
                }
                GalleryChrome.press(this)
                setOnClickListener { set(Field.MAXIMUM, iso) }
            }
            autoIsoMaxGroup.addView(pill)
        }
        maxIsoCard.addView(autoIsoMaxGroup)
        container.addView(maxIsoCard)

        // 5. 自动曝光最快快门限制
        val shutterCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        shutterCard.addView(TextView(activity).apply {
            text = "自动曝光最快快门限制"
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
        })
        fastestShutterGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, 0)
        }
        ExposureControls.FASTEST.forEach { shutter ->
            val pill = TextView(activity).apply {
                text = if (shutter == "off") "关闭" else shutter
                textSize = 11f
                gravity = Gravity.CENTER
                setPadding(dp(8), dp(6), dp(8), dp(6))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(4)
                }
                GalleryChrome.press(this)
                setOnClickListener { set(Field.FASTEST, shutter) }
            }
            fastestShutterGroup.addView(pill)
        }
        shutterCard.addView(fastestShutterGroup)
        container.addView(shutterCard)

        // 6. 机身 ISO 原厂菜单直达
        btnOpenStockIso = Button(activity).apply {
            text = "打开机身原生 ISO 菜单"
            textSize = 13f
            isAllCaps = false
            setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
            background = roundedBg(Color.TRANSPARENT, 16, ContextCompat.getColor(activity, R.color.nx_primary), 1)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(42)).apply {
                topMargin = dp(4)
            }
            GalleryChrome.press(this)
            setOnClickListener {
                sheet.dismiss()
                openStockIso()
            }
        }
        container.addView(btnOpenStockIso)

        scroll.addView(container)
        return scroll
    }

    private fun buildSwitchCard(title: String, desc: String, onToggle: (Boolean) -> Unit): Pair<MaterialSwitch, View> {
        val card = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 16)
            setPadding(dp(14), dp(12), dp(14), dp(12))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        val col = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginEnd = dp(8)
            }
        }
        val titleView = TextView(activity).apply {
            text = title
            textSize = 13f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
        }
        val descView = TextView(activity).apply {
            text = desc
            textSize = 11f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(3), 0, 0)
        }
        col.addView(titleView)
        col.addView(descView)
        card.addView(col)

        val sw = MaterialSwitch(activity)
        sw.setOnCheckedChangeListener { _, isChecked ->
            if (!busy) onToggle(isChecked)
        }
        card.addView(sw)
        return Pair(sw, card)
    }

    // ==========================================
    // 数据同步与状态渲染
    // ==========================================
    private fun supported(group: Group): Boolean = camera.profile.let {
        when (group) {
            Group.ISO -> it.isoControls
            Group.SHUTTER -> it.shutterLimit
            Group.DIAL -> it.dialLock
            Group.RGB -> it.colorStyle
            Group.PW -> it.pictureWizard
            Group.LOOKS -> it.looks
            Group.RECIPE -> it.recipes || it.recipeStyle
        }
    }

    private fun render() {
        if (!::sheet.isInitialized || !sheet.isShowing) return

        // 渲染状态胶囊
        statusBadge.text = when {
            busy -> "读取中…"
            values.isNotEmpty() -> "已同步"
            else -> "未同步"
        }
        statusBadge.background = roundedBg(
            if (busy) ContextCompat.getColor(activity, R.color.nx_warn) else ContextCompat.getColor(activity, R.color.nx_ok),
            12
        )

        // 渲染配方
        renderRecipeCards()
        renderRecipeDetail()

        // 渲染 LUT
        renderLooksCards()

        // 渲染色彩微调
        val r = values[Field.RED] as? Int ?: 100
        val g = values[Field.GREEN] as? Int ?: 100
        val b = values[Field.BLUE] as? Int ?: 100
        if (sliderRed.value != r.toFloat()) sliderRed.value = r.toFloat().coerceIn(0f, 200f)
        if (sliderGreen.value != g.toFloat()) sliderGreen.value = g.toFloat().coerceIn(0f, 200f)
        if (sliderBlue.value != b.toFloat()) sliderBlue.value = b.toFloat().coerceIn(0f, 200f)
        labelRed.text = "$r% (${r - 100}%)"
        labelGreen.text = "$g% (${g - 100}%)"
        labelBlue.text = "$b% (${b - 100}%)"

        val sat = values[Field.SATURATION] as? Int ?: 0
        val sharp = values[Field.SHARPNESS] as? Int ?: 0
        val cont = values[Field.CONTRAST] as? Int ?: 0
        val tone = values[Field.TONE] as? Int ?: -1
        if (sliderSat.value != sat.toFloat()) sliderSat.value = sat.toFloat().coerceIn(-4f, 4f)
        if (sliderSharp.value != sharp.toFloat()) sliderSharp.value = sharp.toFloat().coerceIn(-4f, 4f)
        if (sliderContrast.value != cont.toFloat()) sliderContrast.value = cont.toFloat().coerceIn(-4f, 4f)
        if (sliderTone.value != tone.toFloat()) sliderTone.value = tone.toFloat().coerceIn(-1f, 359f)
        labelSat.text = if (sat > 0) "+$sat" else "$sat"
        labelSharp.text = if (sharp > 0) "+$sharp" else "$sharp"
        labelContrast.text = if (cont > 0) "+$cont" else "$cont"
        labelTone.text = if (tone == -1) "关闭" else "$tone°"

        // 渲染机身控制
        val dial = values[Field.DIAL] as? Boolean ?: false
        if (switchDialLock.isChecked != dial) switchDialLock.isChecked = dial

        val exp = values[Field.EXPANSION] as? Boolean ?: false
        if (switchIsoExpansion.isChecked != exp) switchIsoExpansion.isChecked = exp

        val step = values[Field.STEP] as? String ?: "one-third"
        for (i in 0 until isoStepGroup.childCount) {
            val pill = isoStepGroup.getChildAt(i) as TextView
            val isSel = (i == 0 && step == "one-third") || (i == 1 && step != "one-third")
            pill.background = roundedBg(if (isSel) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_surface_variant), 12)
            pill.setTextColor(if (isSel) Color.WHITE else ContextCompat.getColor(activity, R.color.nx_on_surface))
        }

        val maxIso = values[Field.MAXIMUM] as? Int
        for (i in 0 until autoIsoMaxGroup.childCount) {
            val pill = autoIsoMaxGroup.getChildAt(i) as TextView
            val isSel = pill.text.toString().toIntOrNull() == maxIso
            pill.background = roundedBg(if (isSel) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_surface_variant), 12)
            pill.setTextColor(if (isSel) Color.WHITE else ContextCompat.getColor(activity, R.color.nx_on_surface))
        }

        val fastest = values[Field.FASTEST] as? String
        for (i in 0 until fastestShutterGroup.childCount) {
            val pill = fastestShutterGroup.getChildAt(i) as TextView
            val expected = if (pill.text == "关闭") "off" else pill.text.toString()
            val isSel = expected == fastest
            pill.background = roundedBg(if (isSel) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_surface_variant), 12)
            pill.setTextColor(if (isSel) Color.WHITE else ContextCompat.getColor(activity, R.color.nx_on_surface))
        }

        btnResetRecipe.isEnabled = !busy && camera.profile.recipeStyle && recipeSnapshotConfirmed
        btnResetRgb.isEnabled = !busy && listOf(Field.RED, Field.GREEN, Field.BLUE).all { values.containsKey(it) }
        btnClearLook.isEnabled = !busy && looksState?.writable == true
        refreshBtn.isEnabled = !busy
    }

    private fun invalidate() {
        values.clear()
        looksState = null
        recipeCatalogue = null
        recipeState = null
        recipeSnapshotConfirmed = false
        recipeIssue = ""
    }

    private fun putIso(state: ExposureControls.IsoState) {
        state.step?.let { values[Field.STEP] = it }
        state.expansion?.let { values[Field.EXPANSION] = it }
        state.maximum?.let { values[Field.MAXIMUM] = it }
    }

    private fun putRgb(state: ExposureControls.RgbState) {
        values[Field.RED] = state.red
        values[Field.GREEN] = state.green
        values[Field.BLUE] = state.blue
    }

    private fun putPw(state: ExposureControls.PictureWizardState) {
        Field.entries.filter { it.group == Group.PW }.forEach { field ->
            values.remove(field)
            state.values[field.wire]?.let { values[field] = it }
        }
        pwDetailText.text = "IQ 模式 ${state.iqMode} · " +
            (if (state.iqMatches) "IQ 读回与设置一致" else "IQ 读回与设置不同") + " · " +
            (if (state.scalarModeEnabled) "饱和度/锐度/对比度已启用" else "画面参数未在当前模式启用")
    }

    private fun putLooks(state: ExposureControls.LooksState) {
        looksState = state
        val current = state.looks.firstOrNull { it.id == state.selectedId }
        values[Field.LOOK] = when {
            !state.selectionValid -> LookChoice(state.selectedId, "当前选择无效")
            state.stock -> LookChoice(null, "原厂")
            current != null -> LookChoice(current.id, current.label)
            else -> throw NxException("已选 LUT 不在当前部署列表中")
        }
        val stage = when (state.stage) {
            "hook-unavailable" -> "原厂 LUT 桥未就绪"
            "invalid-selection" -> "当前选择无效"
            "staged-awaiting-stock-call" -> "已暂存，等待原厂调用"
            "selected" -> "原厂已读取，等待转发"
            "forwarded-to-stock-api" -> if (state.forwarded) "已转发三路至原厂 LUT 接口" else "原厂转发中"
            "stock-restore-awaiting-stock-call" -> "已选择原厂输入，等待调用"
            "stock" -> "原厂自身 LUT 输入"
            else -> "状态：${state.stage}"
        }
        looksDetailText.text = "已部署 ${state.looks.size} 项 D4 表 · $stage"
    }

    private fun putRecipe(state: RecipeControls.State) {
        recipeState = state
        values[Field.RECIPE] = when {
            state.superseded -> "已被独立控件修改"
            state.stock -> "原厂"
            state.selectionMatches -> state.selectedLabel ?: state.selectedId ?: "已匹配目录"
            else -> "当前七值不匹配目录"
        }
    }

    private fun renderRecipeDetail() {
        val lines = mutableListOf<String>()
        recipeCatalogue?.let { lines += "已部署 ${it.recipes.size} 项配方" }
        recipeState?.let { state ->
            lines += "机身回读：${RecipeControls.describe(state.values)}"
            lines += "状态：代次 ${state.generation} · " + when {
                state.superseded -> "批次已被独立控件微调"
                state.stock -> "原厂设置"
                state.selectionMatches -> "匹配 ${state.selectedLabel ?: state.selectedId}"
                else -> "自定义七值"
            }
        }
        if (recipeIssue.isNotBlank()) lines += recipeIssue
        recipeDetailText.text = lines.joinToString(" · ")
    }

    private fun writeRecipe(recipe: RecipeControls.Recipe?) {
        if (!recipeSnapshotConfirmed || !camera.profile.recipeStyle) return
        write(Field.RECIPE.title) {
            recipeIssue = ""
            putRecipe(if (recipe == null) camera.resetRecipe() else camera.applyRecipe(recipe))
            recipeSnapshotConfirmed = true
            if (supported(Group.RGB)) try { putRgb(camera.readRgbSettings()) } catch (c: CancellationException) { throw c } catch (e: Exception) { values.keys.removeAll { it.group == Group.RGB } }
            if (supported(Group.PW)) try { putPw(camera.readPictureWizard()) } catch (c: CancellationException) { throw c } catch (e: Exception) { values.keys.removeAll { it.group == Group.PW } }
        }
    }

    private fun scheduleRecipePoll() {
        recipePoll?.cancel()
        if (!sheet.isShowing || !recipeSnapshotConfirmed || recipeState?.awaitingPreview != true) return
        recipePoll = activity.lifecycleScope.launch {
            repeat(8) {
                delay(1500)
                if (busy || !sheet.isShowing) return@launch
                try {
                    checkConnection()
                    val latest = camera.readRecipe()
                    checkConnection()
                    putRecipe(latest)
                    render()
                    if (!latest.awaitingPreview) return@launch
                } catch (c: CancellationException) { throw c }
                catch (e: Exception) {
                    recipeSnapshotConfirmed = false
                    recipeState = (e as? RecipeControls.Failure)?.readback
                    if (recipeState == null) values.remove(Field.RECIPE) else recipeState?.let(::putRecipe)
                    render()
                    return@launch
                }
            }
        }
    }

    private fun readAll() {
        if (busy) return
        recipePoll?.cancel()
        busy = true
        invalidate()
        statusText.text = "正在读取相机机身设置…"
        render()
        job = activity.lifecycleScope.launch {
            val errors = mutableListOf<String>()
            suspend fun read(group: Group, title: String, block: suspend () -> Unit) {
                if (!supported(group)) return
                try {
                    checkConnection()
                    block()
                    checkConnection()
                } catch (e: CancellationException) { throw e }
                catch (e: Exception) {
                    values.keys.removeAll { it.group == group }
                    if (group == Group.LOOKS) looksState = null
                    if (group == Group.RECIPE) {
                        recipeSnapshotConfirmed = false
                        if (title == "配方目录") recipeCatalogue = null
                        else { recipeState = (e as? RecipeControls.Failure)?.readback; recipeState?.let(::putRecipe) }
                        recipeIssue = "$title：${e.message}"
                    }
                    errors += "$title：${e.message}"
                }
            }
            try {
                checkConnection()
                camera.info()
                checkConnection()
                render()
                read(Group.ISO, "ISO") { putIso(camera.readIsoSettings()) }
                read(Group.SHUTTER, "最快快门") { values[Field.FASTEST] = camera.readFastestShutter() }
                read(Group.DIAL, "拨盘锁") { values[Field.DIAL] = camera.readDialLock() }
                read(Group.RGB, "RGB") { putRgb(camera.readRgbSettings()) }
                read(Group.PW, "原厂画面") { putPw(camera.readPictureWizard()) }
                read(Group.LOOKS, "LUT") { putLooks(camera.readLooks()) }
                if (camera.profile.recipes) read(Group.RECIPE, "配方目录") {
                    recipeCatalogue = camera.readRecipes()
                    values[Field.RECIPE] = "请选择配方"
                }
                if (camera.profile.recipeStyle) read(Group.RECIPE, "配方状态") {
                    putRecipe(camera.readRecipe()); recipeSnapshotConfirmed = true
                }
                checkConnection()
                statusText.text = when {
                    errors.isNotEmpty() -> "部分设置读取失败，请点击刷新"
                    !camera.profile.hasBodySettings -> "当前连接未提供机身扩展能力"
                    else -> "已同步相机实时设置 · 仅当前相机会话生效"
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { invalidate(); statusText.text = "读取异常：${e.message}" }
            finally { busy = false; render(); scheduleRecipePoll() }
        }
    }

    private fun set(field: Field, requested: Any) {
        if (busy) return
        write(field.title) {
            when (field.group) {
                Group.ISO -> putIso(camera.setIsoSetting(field.wire, requested))
                Group.SHUTTER -> { values[field] = camera.setFastestShutter(requested as String) }
                Group.DIAL -> { values[field] = camera.setDialLock(requested as Boolean) }
                Group.RGB -> putRgb(camera.setRgbSetting(field.wire, requested as Int))
                Group.PW -> putPw(camera.setPictureWizardSetting(field.wire, requested as Int))
                Group.LOOKS -> putLooks(camera.selectLook((requested as LookChoice).id))
                Group.RECIPE -> throw NxException("请选择目录中的完整七维配方")
            }
            val actual = values[field] ?: throw NxException("原厂读回无法识别")
            val confirmed = if (field == Field.LOOK) (actual as LookChoice).id == (requested as LookChoice).id else actual == requested
            if (!confirmed) throw NxException("原厂读回与请求不一致")
        }
    }

    private fun write(title: String, block: suspend () -> Unit) {
        if (busy || !sheet.isShowing) return
        recipePoll?.cancel()
        busy = true
        statusText.text = "正在同步 $title…"
        render()
        job = activity.lifecycleScope.launch {
            try {
                checkConnection()
                block()
                checkConnection()
                if (title != Field.RECIPE.title && camera.profile.recipeStyle) {
                    try {
                        putRecipe(camera.readRecipe())
                        recipeSnapshotConfirmed = true
                        recipeIssue = ""
                        checkConnection()
                    } catch (c: CancellationException) { throw c }
                    catch (e: Exception) {
                        recipeSnapshotConfirmed = false
                        recipeState = null
                        values.remove(Field.RECIPE)
                        recipeIssue = "配方状态读取失败：${e.message}"
                    }
                }
                statusText.text = when (title) {
                    Field.LOOK.title -> "LUT 选择已暂存并同步"
                    Field.RECIPE.title -> "七维色彩已读回；预览与拍摄阶段已转交"
                    else -> "已同步 $title"
                }
            } catch (e: CancellationException) { throw e }
            catch (error: RecipeControls.Failure) {
                val catalogue = recipeCatalogue
                invalidate()
                recipeCatalogue = catalogue
                error.readback?.let(::putRecipe)
                recipeIssue = error.message.orEmpty()
                statusText.text = "$title 未确认：$recipeIssue"
            }
            catch (e: Exception) {
                invalidate()
                statusText.text = "$title 设置失败：${e.message}"
            } finally {
                busy = false
                render()
                scheduleRecipePoll()
            }
        }
    }

    private fun checkConnection() {
        if (NxApp.camera !== camera) throw NxException("相机连接已改变，请关闭后重新打开设置")
    }
}
'''

target_file.write_text(code, encoding="utf-8")
print(f"ExposureSettingsDialog.kt successfully generated at {target_file}")
