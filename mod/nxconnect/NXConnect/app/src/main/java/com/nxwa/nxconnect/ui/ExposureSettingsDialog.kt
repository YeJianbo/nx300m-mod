// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.widget.NestedScrollView
import androidx.lifecycle.lifecycleScope
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

/** One shared camera settings sheet; values and write access come from the connected camera. */
class ExposureSettingsDialog(
    private val activity: AppCompatActivity,
    private val camera: NxCamera,
    private val openStockIso: () -> Unit,
) {
    enum class Section { RECIPES, LOOKS, TUNING, CONTROLS }
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

    private data class LookChoice(val id: String?, val label: String, val strength: Int = 100)

    private val values = mutableMapOf<Field, Any>()
    private var isoSnapshot: ExposureControls.IsoState? = null
    private var looksState: ExposureControls.LooksState? = null
    private var pendingLook: LookChoice? = null
    private var recipeCatalogue: RecipeControls.Catalogue? = null
    private var recipeState: RecipeControls.State? = null
    private var recipeSnapshotConfirmed = false
    private var recipeIssue = ""

    private var busy = false
    private var rendering = false
    private val draggingSliders = mutableSetOf<Slider>()
    private val sliderJobs = mutableMapOf<Slider, Job>()
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
    private lateinit var btnApplyLook: Button
    private lateinit var lookStrengthCard: View
    private lateinit var sliderLookStrength: Slider
    private lateinit var labelLookStrength: TextView

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
    private lateinit var isoStepDetailText: TextView
    private lateinit var autoIsoMaxScroll: HorizontalScrollView
    private lateinit var autoIsoMaxGroup: LinearLayout
    private lateinit var autoIsoMaxHeaderVal: TextView
    private lateinit var autoIsoPolicyText: TextView
    private lateinit var autoIsoSupportText: TextView
    private lateinit var fastestShutterScroll: HorizontalScrollView
    private lateinit var fastestShutterGroup: LinearLayout
    private lateinit var fastestShutterHeaderVal: TextView
    private lateinit var btnOpenStockIso: Button

    private val FULL_STOP_ISOS = setOf(200, 400, 800, 1600, 3200, 6400, 12800, 25600)

    private fun dp(v: Int): Int = (v * activity.resources.displayMetrics.density).toInt()

    private fun roundedBg(bgColor: Int, radiusDp: Int): GradientDrawable {
        return GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(radiusDp).toFloat()
            setColor(bgColor)
        }
    }

    private fun cardBackground(selected: Boolean = false): GradientDrawable = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        cornerRadius = activity.resources.getDimension(R.dimen.oneui_card_corner)
        setColor(ContextCompat.getColor(activity,
            if (selected) R.color.nx_surface_variant else R.color.nx_surface_container))
    }

    private fun Button.styleSecondaryAction() {
        isAllCaps = false
        backgroundTintList = null
        setTextColor(ColorStateList(
            arrayOf(intArrayOf(android.R.attr.state_enabled), intArrayOf()),
            intArrayOf(ContextCompat.getColor(activity, R.color.nx_primary),
                ContextCompat.getColor(activity, R.color.nx_text_dim)),
        ))
        setBackgroundResource(R.drawable.bg_gallery_touch)
        minimumHeight = activity.resources.getDimensionPixelSize(R.dimen.oneui_row_height)
        setPadding(dp(12), dp(8), dp(12), dp(8))
    }

    fun show(section: Section = Section.RECIPES) {
        sheet = NxBottomSheetDialog(activity)
        tabBtns.clear()
        tabContainers.clear()

        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.bg_oneui_dialog)
            clipToOutline = true
            val contentInset = resources.getDimensionPixelSize(R.dimen.oneui_content_inset)
            setPadding(contentInset, dp(10), contentInset, contentInset)
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

        // 1. 顶部拖动药丸把手
        val handleBar = View(activity).apply {
            layoutParams = LinearLayout.LayoutParams(dp(44), dp(4)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = dp(4)
                bottomMargin = dp(12)
            }
            setBackgroundResource(R.drawable.bg_oneui_drag_handle)
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
            text = "相机设置"
            textSize = 20f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            typeface = android.graphics.Typeface.DEFAULT_BOLD
        }
        statusText = TextView(activity).apply {
            text = "正在读取相机设置…"
            textSize = 14f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(2), 0, 0)
        }
        titleCol.addView(mainTitle)
        titleCol.addView(statusText)
        header.addView(titleCol)

        // 右侧操作行
        val rightActions = LinearLayout(activity).apply {
            id = View.generateViewId()
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = RelativeLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                addRule(RelativeLayout.ALIGN_PARENT_END)
                addRule(RelativeLayout.CENTER_VERTICAL)
            }
        }

        statusBadge = TextView(activity).apply {
            text = "读取中"
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_variant), 12)
            setPadding(dp(10), dp(4), dp(10), dp(4))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginEnd = dp(8)
            }
        }
        titleCol.addView(statusBadge)

        refreshBtn = TextView(activity).apply {
            text = "刷新"
            textSize = 15f
            minHeight = dp(48)
            minWidth = dp(48)
            gravity = Gravity.CENTER
            setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
            setBackgroundResource(R.drawable.bg_gallery_touch)
            setPadding(dp(10), dp(5), dp(10), dp(5))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginEnd = dp(6)
            }
            GalleryChrome.press(this)
            setOnClickListener { readAll() }
        }
        rightActions.addView(refreshBtn)

        val closeBtn = ImageButton(activity).apply {
            setImageResource(R.drawable.ic_gallery_close)
            imageTintList = ColorStateList.valueOf(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            setPadding(dp(12), dp(12), dp(12), dp(12))
            contentDescription = "关闭相机设置"
            setBackgroundResource(R.drawable.bg_gallery_touch)
            layoutParams = LinearLayout.LayoutParams(dp(48), dp(48))
            GalleryChrome.press(this)
            setOnClickListener { sheet.dismiss() }
        }
        rightActions.addView(closeBtn)
        header.addView(rightActions)
        titleCol.layoutParams = RelativeLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            addRule(RelativeLayout.ALIGN_PARENT_START)
            addRule(RelativeLayout.START_OF, rightActions.id)
            marginEnd = dp(8)
        }
        root.addView(header)

        // 分段导航与内容共享同一抽屉，不创建第二层面板。
        val tabLayout = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            weightSum = 4f
            background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_variant), 27)
            setPadding(dp(3), dp(3), dp(3), dp(3))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(54)).apply {
                bottomMargin = dp(14)
            }
        }
        val tabTitles = listOf("配方", "LUT", "画面", "机身")
        tabTitles.forEachIndexed { index, title ->
            val tabBtn = TextView(activity).apply {
                text = title
                textSize = 17f
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
            val screenHeight = activity.resources.displayMetrics.heightPixels
            val maxH = minOf((screenHeight * .62f).toInt(), screenHeight - dp(220)).coerceAtLeast(dp(120))
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
        root.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            val headerHeight = (0 until root.childCount)
                .map { root.getChildAt(it) }
                .filter { it !== contentStage }
                .sumOf { child ->
                    val margins = child.layoutParams as? ViewGroup.MarginLayoutParams
                    child.height + (margins?.topMargin ?: 0) + (margins?.bottomMargin ?: 0)
                }
            val available = root.height - root.paddingTop - root.paddingBottom - headerHeight
            if (available > 0 && contentStage.height > available) {
                contentStage.layoutParams = contentStage.layoutParams.apply { height = available }
            }
        }

        sheet.setContentView(root)
        sheet.setOnDismissListener {
            job?.cancel()
            recipePoll?.cancel()
            sliderJobs.values.forEach { it.cancel() }
            sliderJobs.clear()
        }

        switchTab(section.ordinal)
        sheet.show()
        readAll()
    }

    private fun switchTab(target: Int) {
        currentTab = target
        tabBtns.forEachIndexed { i, btn ->
            if (i == target) {
                btn.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface), 24)
                btn.setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
            } else {
                btn.background = null
                btn.setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            }
        }
        tabContainers.forEachIndexed { i, container ->
            // Material caches the nested scrolling child on layout. Only the visible page
            // participates, so an earlier hidden page cannot capture the next page's drag.
            fun updateNestedScrolling(view: View) {
                if (view is NestedScrollView) view.isNestedScrollingEnabled = i == target
                if (view is ViewGroup) {
                    for (child in 0 until view.childCount) updateNestedScrolling(view.getChildAt(child))
                }
            }
            updateNestedScrolling(container)
            if (i == target) {
                container.visibility = View.VISIBLE
                container.animate().cancel()
                container.alpha = 1f
                container.translationY = 0f
                if (NxMotion.enabled(container)) {
                    container.alpha = 0f
                    container.animate().alpha(1f).setDuration(150).setInterpolator(NxMotion.easing).start()
                }
            } else {
                container.animate().cancel()
                container.visibility = View.GONE
            }
        }
        tabContainers.getOrNull(target)?.requestLayout()
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
                textSize = 14f
                minHeight = dp(48)
                gravity = Gravity.CENTER
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
        recipeContainer.addView(HorizontalScrollView(activity).apply {
            isHorizontalScrollBarEnabled = false
            addView(recipeCategoryGroup)
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        renderRecipeChips()

        // 垂直可滚动的配方卡片流
        val scroll = NestedScrollView(activity).apply {
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
            textSize = 17f
            styleSecondaryAction()
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                resources.getDimensionPixelSize(R.dimen.oneui_row_height))
            GalleryChrome.press(this)
            setOnClickListener { writeRecipe(null) }
        }
        bottomBar.addView(btnResetRecipe)

        recipeDetailText = TextView(activity).apply {
            textSize = 12f
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
                chip.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_variant), 24)
                chip.setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
            } else {
                chip.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 24)
                chip.setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            }
        }
    }

    private fun renderRecipeCards() {
        recipeCardList.removeAllViews()
        val allRecipes = recipeCatalogue?.recipes.orEmpty()
        if (allRecipes.isEmpty()) {
            val empty = TextView(activity).apply {
                text = if (busy) "正在读取配方…" else "当前相机未提供可用配方"
                textSize = 17f
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
                background = cardBackground(isCurrent)
                setPadding(dp(16), dp(16), dp(16), dp(16))
                GalleryChrome.press(this)
                setOnClickListener {
                    if (!busy && camera.profile.recipeStyle) writeRecipe(recipe)
                }
            }

            val leftCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(8)
                }
            }

            val titleRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val titleView = TextView(activity).apply {
                text = recipe.label
                textSize = 17f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_on_surface))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            titleRow.addView(titleView)

            if (isCurrent) {
                val activeTag = TextView(activity).apply {
                    text = "● 已读回"
                    textSize = 12f
                    setTextColor(ContextCompat.getColor(activity, R.color.nx_ok))
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
                textSize = 12f
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
                setPadding(0, dp(4), 0, 0)
            }
            leftCol.addView(descView)
            card.addView(leftCol)

            val applyBtn = TextView(activity).apply {
                text = if (isCurrent) "已选中" else "应用"
                textSize = 14f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
                minWidth = dp(48)
                minHeight = dp(48)
                gravity = Gravity.CENTER
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
        val scroll = NestedScrollView(activity).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            isVerticalScrollBarEnabled = false
        }
        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, dp(16))
        }

        btnClearLook = Button(activity).apply {
            text = "清除 LUT · 恢复原厂色彩"
            textSize = 17f
            styleSecondaryAction()
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                resources.getDimensionPixelSize(R.dimen.oneui_row_height)).apply {
                bottomMargin = dp(12)
            }
            GalleryChrome.press(this)
            setOnClickListener { set(Field.LOOK, LookChoice(null, "原厂")) }
        }
        container.addView(btnClearLook)

        container.addView(Button(activity).apply {
            text = "管理 LUT 库"
            styleSecondaryAction()
            setOnClickListener {
                sheet.dismiss()
                OneUiTransitions.open(activity, android.content.Intent(activity, LutLibraryActivity::class.java)
                    .putExtra("camera_tab", true))
            }
        })

        looksCardList = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
        }
        container.addView(looksCardList)

        val strengthCard = buildSliderCard("LUT 强度", "#EF8B63", 0f, 100f, 100f) { strength ->
            if (::labelLookStrength.isInitialized) labelLookStrength.text = "$strength%"
            if (!rendering) pendingLook = pendingLook?.copy(strength = strength)
        }
        sliderLookStrength = strengthCard.first
        sliderLookStrength.contentDescription = "LUT 强度，确认后暂存"
        labelLookStrength = strengthCard.second
        lookStrengthCard = strengthCard.third
        container.addView(lookStrengthCard)

        btnApplyLook = Button(activity).apply {
            text = "确认并暂存"
            textSize = 17f
            styleSecondaryAction()
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                resources.getDimensionPixelSize(R.dimen.oneui_row_height))
            GalleryChrome.press(this)
            setOnClickListener { pendingLook?.let { set(Field.LOOK, it) } }
        }
        container.addView(btnApplyLook)

        looksDetailText = TextView(activity).apply {
            textSize = 12f
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
        val editable = !busy && state?.writable == true && NxApp.camera === camera
        val draft = pendingLook
        val selectable = state?.looks?.any { it.id == draft?.id && it.tableValid && it.selectable } == true
        lookStrengthCard.visibility = if (state?.strengthSupported == true) View.VISIBLE else View.GONE
        sliderLookStrength.isEnabled = editable && selectable && state?.strengthSupported == true
        val strength = draft?.strength ?: state?.strength ?: 100
        if (sliderLookStrength.value != strength.toFloat()) sliderLookStrength.value = strength.toFloat()
        labelLookStrength.text = "$strength%"
        btnApplyLook.isEnabled = editable && selectable
        if (state == null) {
            looksDetailText.text = ""
            val empty = TextView(activity).apply {
                text = if (busy) "正在读取 3D LUT 库…" else "未读取到 LUT 状态或不支持机内 LUT"
                textSize = 17f
                gravity = Gravity.CENTER
                setPadding(0, dp(40), 0, 0)
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            }
            looksCardList.addView(empty)
            return
        }

        val currentId = state.selectedId
        state.looks.filter { it.tableValid && it.selectable }.forEach { look ->
            val isCurrent = state.selectionValid && !state.stock && look.id == currentId
            val isPending = look.id == pendingLook?.id
            val card = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    bottomMargin = dp(8)
                }
                background = cardBackground(isPending)
                setPadding(dp(16), dp(16), dp(16), dp(16))
                GalleryChrome.press(this)
                setOnClickListener {
                    if (editable) {
                        pendingLook = LookChoice(look.id, look.label, pendingLook?.strength ?: state.strength)
                        render()
                    }
                }
            }

            val leftCol = LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(8)
                }
            }
            val titleRow = LinearLayout(activity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
            }
            val title = TextView(activity).apply {
                text = look.label
                textSize = 17f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(if (isCurrent) ContextCompat.getColor(activity, R.color.nx_primary) else ContextCompat.getColor(activity, R.color.nx_on_surface))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            }
            titleRow.addView(title)
            if (isCurrent) {
                val tag = TextView(activity).apply {
                    text = if (state.forwarded) "● 已转发" else "● 已暂存"
                    textSize = 12f
                    setTextColor(ContextCompat.getColor(activity, R.color.nx_ok))
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
                textSize = 12f
                setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
                setPadding(0, dp(4), 0, 0)
            }
            leftCol.addView(sub)
            card.addView(leftCol)

            val selectBtn = TextView(activity).apply {
                text = if (isPending) "已选择" else "选择"
                textSize = 14f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
                setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
                minWidth = dp(48)
                minHeight = dp(48)
                gravity = Gravity.CENTER
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
        val scroll = NestedScrollView(activity).apply {
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
            textSize = 17f
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
        bindSlider(sliderRed, Field.RED)
        container.addView(cardR.third)

        // G 滑块卡片
        val cardG = buildSliderCard("绿色增益 (Green Gain)", "#2E7D32", 0f, 200f, 100f) { v ->
            labelGreen.text = "$v% (${v - 100}%)"
        }
        sliderGreen = cardG.first; labelGreen = cardG.second
        bindSlider(sliderGreen, Field.GREEN)
        container.addView(cardG.third)

        // B 滑块卡片
        val cardB = buildSliderCard("蓝色增益 (Blue Gain)", "#1565C0", 0f, 200f, 100f) { v ->
            labelBlue.text = "$v% (${v - 100}%)"
        }
        sliderBlue = cardB.first; labelBlue = cardB.second
        bindSlider(sliderBlue, Field.BLUE)
        container.addView(cardB.third)

        btnResetRgb = Button(activity).apply {
            text = "一键复位 RGB (100 / 100 / 100)"
            textSize = 17f
            styleSecondaryAction()
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                resources.getDimensionPixelSize(R.dimen.oneui_row_height)).apply {
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
            textSize = 17f
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
        bindSlider(sliderSat, Field.SATURATION)
        container.addView(cardSat.third)

        // 锐度
        val cardSharp = buildSliderCard("原厂锐度 (-4 至 +4)", "#00897B", -4f, 4f, 0f, step = 1f) { v ->
            labelSharp.text = if (v > 0) "+$v" else "$v"
        }
        sliderSharp = cardSharp.first; labelSharp = cardSharp.second
        bindSlider(sliderSharp, Field.SHARPNESS)
        container.addView(cardSharp.third)

        // 对比度
        val cardContrast = buildSliderCard("原厂对比度 (-4 至 +4)", "#FB8C00", -4f, 4f, 0f, step = 1f) { v ->
            labelContrast.text = if (v > 0) "+$v" else "$v"
        }
        sliderContrast = cardContrast.first; labelContrast = cardContrast.second
        bindSlider(sliderContrast, Field.CONTRAST)
        container.addView(cardContrast.third)

        // 色彩角度
        val cardTone = buildSliderCard("原厂色彩角度 (-1 为关闭，0°~359°)", "#8E24AA", -1f, 359f, -1f, step = 1f) { v ->
            labelTone.text = if (v == -1) "关闭" else "$v°"
        }
        sliderTone = cardTone.first; labelTone = cardTone.second
        bindSlider(sliderTone, Field.TONE)
        container.addView(cardTone.third)

        pwDetailText = TextView(activity).apply {
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(4), 0, 0)
        }
        container.addView(pwDetailText)

        scroll.addView(container)
        return scroll
    }

    // Touch commits on release; accessibility and keyboard steps settle before one write.
    private fun bindSlider(slider: Slider, field: Field) {
        slider.addOnSliderTouchListener(object : Slider.OnSliderTouchListener {
            override fun onStartTrackingTouch(slider: Slider) {
                sliderJobs.remove(slider)?.cancel()
                draggingSliders.add(slider)
            }
            override fun onStopTrackingTouch(slider: Slider) {
                draggingSliders.remove(slider)
                set(field, slider.value.toInt())
            }
        })
        slider.addOnChangeListener { changed, value, fromUser ->
            if (fromUser && !busy && !rendering && changed !in draggingSliders) {
                sliderJobs.remove(changed)?.cancel()
                sliderJobs[changed] = activity.lifecycleScope.launch {
                    delay(300)
                    sliderJobs.remove(changed)
                    set(field, value.toInt())
                }
            }
        }
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
            background = cardBackground()
            setPadding(dp(16), dp(16), dp(16), dp(8))
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
            textSize = 17f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val valueView = TextView(activity).apply {
            text = "$defaultVal"
            textSize = 17f
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
            trackInactiveTintList = ColorStateList.valueOf(ContextCompat.getColor(activity, R.color.nx_outline_variant))
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
        val scroll = NestedScrollView(activity).apply {
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            isVerticalScrollBarEnabled = false
        }
        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(0, 0, 0, dp(20))
        }

        // 1. 指令拨盘锁卡片 (Switch)
        val dialCard = buildSwitchCard("指令拨盘锁", "锁定机身指令拨轮，防止误触") { checked ->
            set(Field.DIAL, checked)
        }
        switchDialLock = dialCard.first
        container.addView(dialCard.second)

        // 2. 扩展 ISO 卡片 (Switch)
        val expCard = buildSwitchCard("扩展 ISO", "允许使用扩展感光度档位") { checked ->
            set(Field.EXPANSION, checked)
        }
        switchIsoExpansion = expCard.first
        container.addView(expCard.second)

        // 3. ISO 步长选择卡片
        val stepCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = cardBackground()
            setPadding(dp(16), dp(16), dp(16), dp(16))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        val stepHeader = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val stepTitle = TextView(activity).apply {
            text = "ISO 调节步长"
            textSize = 17f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val stepIndicator = TextView(activity).apply {
            text = "机身 EV 刻度"
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
        }
        stepHeader.addView(stepTitle)
        stepHeader.addView(stepIndicator)
        stepCard.addView(stepHeader)

        isoStepGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(8), 0, 0)
        }
        listOf("one-third" to "1/3 EV", "one-stop" to "1 EV").forEach { (wire, label) ->
            val pill = TextView(activity).apply {
                tag = wire
                text = label
                textSize = 14f
                minHeight = dp(48)
                gravity = Gravity.CENTER
                setPadding(dp(14), dp(9), dp(14), dp(9))
                layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(6)
                }
                GalleryChrome.press(this)
                setOnClickListener {
                    performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP)
                    set(Field.STEP, wire)
                }
            }
            isoStepGroup.addView(pill)
        }
        stepCard.addView(isoStepGroup)

        isoStepDetailText = TextView(activity).apply {
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(6), 0, 0)
            text = "1/3 EV 步长：提供 16 档精细调节，精准适配微弱光比变动"
        }
        stepCard.addView(isoStepDetailText)
        container.addView(stepCard)

        // 4. Auto ISO 上限选择（拟物平滑滚动滑轨 + 整档突出）
        val maxIsoCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = cardBackground()
            setPadding(dp(16), dp(16), dp(16), dp(16))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        val maxIsoHeader = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val maxIsoTitle = TextView(activity).apply {
            text = "自动 ISO 上限"
            textSize = 17f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        autoIsoMaxHeaderVal = TextView(activity).apply {
            text = "读取中…"
            textSize = 14f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
        }
        maxIsoHeader.addView(maxIsoTitle)
        maxIsoHeader.addView(autoIsoMaxHeaderVal)
        maxIsoCard.addView(maxIsoHeader)
        maxIsoCard.addView(TextView(activity).apply {
            text = "自动 ISO 上限，不固定当前 ISO；实际 ISO 由相机测光决定。"
            textSize = 14f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            setPadding(0, dp(8), 0, 0)
        })

        autoIsoMaxScroll = HorizontalScrollView(activity).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8)
            }
            isHorizontalScrollBarEnabled = false
            clipToPadding = false
            setPadding(0, dp(2), 0, dp(2))
        }
        autoIsoMaxGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        ExposureControls.AUTO_ISO_MAX.forEach { iso ->
            val isFull = iso in FULL_STOP_ISOS
            val pill = TextView(activity).apply {
                tag = iso
                text = if (iso > 3200) "$iso\n扩展" else "$iso"
                textSize = if (isFull) 15f else 14f
                typeface = if (isFull) android.graphics.Typeface.DEFAULT_BOLD else android.graphics.Typeface.DEFAULT
                gravity = Gravity.CENTER
                minWidth = if (isFull) dp(64) else dp(48)
                setPadding(dp(8), dp(2), dp(8), dp(2))
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)).apply {
                    marginEnd = dp(6)
                }
                GalleryChrome.press(this)
                setOnClickListener {
                    performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP)
                    set(Field.MAXIMUM, iso)
                }
            }
            autoIsoMaxGroup.addView(pill)
        }
        autoIsoMaxScroll.addView(autoIsoMaxGroup)
        maxIsoCard.addView(autoIsoMaxScroll)
        autoIsoSupportText = TextView(activity).apply {
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            setPadding(0, dp(8), 0, 0)
        }
        maxIsoCard.addView(autoIsoSupportText)
        autoIsoPolicyText = TextView(activity).apply {
            textSize = 14f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            setPadding(0, dp(10), 0, 0)
            setLineSpacing(dp(3).toFloat(), 1f)
        }
        maxIsoCard.addView(autoIsoPolicyText)
        container.addView(maxIsoCard)

        // 5. 自动曝光最快快门限制（拟物平滑滚动滑轨 + 安全/高速分级）
        val shutterCard = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = cardBackground()
            setPadding(dp(16), dp(16), dp(16), dp(16))
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(8)
            }
        }
        val shutterHeader = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val shutterTitle = TextView(activity).apply {
            text = "最快快门限制 (AE Cap)"
            textSize = 17f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        fastestShutterHeaderVal = TextView(activity).apply {
            text = "读取中…"
            textSize = 14f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
        }
        shutterHeader.addView(shutterTitle)
        shutterHeader.addView(fastestShutterHeaderVal)
        shutterCard.addView(shutterHeader)

        fastestShutterScroll = HorizontalScrollView(activity).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8)
            }
            isHorizontalScrollBarEnabled = false
            clipToPadding = false
            setPadding(0, dp(2), 0, dp(2))
        }
        fastestShutterGroup = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        ExposureControls.FASTEST.forEach { shutter ->
            val isFast = shutter in listOf("1/500", "1/1000", "1/2000", "1/4000", "1/6000", "1/8000")
            val pill = TextView(activity).apply {
                tag = shutter
                text = if (shutter == "off") "关闭" else shutter
                textSize = 14f
                typeface = if (isFast) android.graphics.Typeface.DEFAULT_BOLD else android.graphics.Typeface.DEFAULT
                gravity = Gravity.CENTER
                minWidth = if (shutter == "off") dp(56) else if (isFast) dp(66) else dp(56)
                setPadding(dp(10), dp(6), dp(10), dp(6))
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)).apply {
                    marginEnd = dp(6)
                }
                GalleryChrome.press(this)
                setOnClickListener {
                    performHapticFeedback(android.view.HapticFeedbackConstants.KEYBOARD_TAP)
                    set(Field.FASTEST, shutter)
                }
            }
            fastestShutterGroup.addView(pill)
        }
        fastestShutterScroll.addView(fastestShutterGroup)
        shutterCard.addView(fastestShutterScroll)
        container.addView(shutterCard)

        // 6. 机身 ISO 原厂菜单直达
        btnOpenStockIso = Button(activity).apply {
            text = "打开机身原生 ISO 菜单"
            textSize = 17f
            styleSecondaryAction()
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                resources.getDimensionPixelSize(R.dimen.oneui_row_height)).apply {
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
            background = cardBackground()
            setPadding(dp(16), dp(16), dp(16), dp(16))
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
            textSize = 17f
            typeface = android.graphics.Typeface.DEFAULT_BOLD
            setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
        }
        val descView = TextView(activity).apply {
            text = desc
            textSize = 12f
            setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
            setPadding(0, dp(3), 0, 0)
        }
        col.addView(titleView)
        col.addView(descView)
        card.addView(col)

        val sw = MaterialSwitch(activity)
        sw.setOnCheckedChangeListener { button, isChecked ->
            if (!busy && !rendering && button.isEnabled) onToggle(isChecked)
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
        rendering = true
        try {

        // 渲染状态胶囊
        statusBadge.text = when {
            busy -> "读取中…"
            isoSnapshot?.aePolicy?.policyMatchesRequested == false -> "上限不一致"
            values.isNotEmpty() -> "已读取"
            else -> "未读取"
        }
        statusBadge.setTextColor(ContextCompat.getColor(activity, when {
                busy || values.isEmpty() -> R.color.nx_text_dim
                isoSnapshot?.aePolicy?.policyMatchesRequested == false -> R.color.nx_error
                else -> R.color.nx_primary
            }))

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
        labelRed.text = if (Field.RED in values) "$r% (${r - 100}%)" else "未读取"
        labelGreen.text = if (Field.GREEN in values) "$g% (${g - 100}%)" else "未读取"
        labelBlue.text = if (Field.BLUE in values) "$b% (${b - 100}%)" else "未读取"

        val sat = values[Field.SATURATION] as? Int ?: 0
        val sharp = values[Field.SHARPNESS] as? Int ?: 0
        val cont = values[Field.CONTRAST] as? Int ?: 0
        val tone = values[Field.TONE] as? Int ?: -1
        if (sliderSat.value != sat.toFloat()) sliderSat.value = sat.toFloat().coerceIn(-4f, 4f)
        if (sliderSharp.value != sharp.toFloat()) sliderSharp.value = sharp.toFloat().coerceIn(-4f, 4f)
        if (sliderContrast.value != cont.toFloat()) sliderContrast.value = cont.toFloat().coerceIn(-4f, 4f)
        if (sliderTone.value != tone.toFloat()) sliderTone.value = tone.toFloat().coerceIn(-1f, 359f)
        labelSat.text = if (Field.SATURATION !in values) "未读取" else if (sat > 0) "+$sat" else "$sat"
        labelSharp.text = if (Field.SHARPNESS !in values) "未读取" else if (sharp > 0) "+$sharp" else "$sharp"
        labelContrast.text = if (Field.CONTRAST !in values) "未读取" else if (cont > 0) "+$cont" else "$cont"
        labelTone.text = if (Field.TONE !in values) "未读取" else if (tone == -1) "关闭" else "$tone°"

        // 渲染机身控制
        val dial = values[Field.DIAL] as? Boolean ?: false
        if (switchDialLock.isChecked != dial) switchDialLock.isChecked = dial

        val exp = values[Field.EXPANSION] as? Boolean ?: false
        if (switchIsoExpansion.isChecked != exp) switchIsoExpansion.isChecked = exp

        val step = values[Field.STEP] as? String
        for (i in 0 until isoStepGroup.childCount) {
            val pill = isoStepGroup.getChildAt(i) as TextView
            val isSel = pill.tag == step
            pill.background = roundedBg(ContextCompat.getColor(activity,
                if (isSel) R.color.nx_surface_container_highest else R.color.nx_surface_variant), 24)
            pill.setTextColor(ContextCompat.getColor(activity,
                if (isSel) R.color.nx_primary else R.color.nx_on_surface))
        }
        isoStepDetailText.text = when (step) {
            "one-third" -> "每次调整 1/3 EV"
            "one-stop" -> "每次调整 1 EV"
            else -> if (supported(Group.ISO)) "尚未读取相机值" else "当前连接未提供 ISO 设置"
        }

        val maxIso = values[Field.MAXIMUM] as? Int
        for (i in 0 until autoIsoMaxGroup.childCount) {
            val pill = autoIsoMaxGroup.getChildAt(i) as TextView
            val iso = pill.tag as? Int ?: pill.text.toString().filter { it.isDigit() }.toIntOrNull() ?: continue
            val isFullStop = iso in FULL_STOP_ISOS
            val isSel = iso == maxIso
            val isStepOne = step == "one-stop"

            if (isSel) {
                pill.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container_highest), 24)
                pill.setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
                pill.alpha = 1.0f
                centerSelectedOnce(autoIsoMaxScroll, pill, iso)
            } else {
                if (isFullStop) {
                    pill.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container_high), 24)
                    pill.setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface))
                    pill.alpha = 1.0f
                } else {
                    pill.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container), 24)
                    pill.setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
                    pill.alpha = if (isStepOne) 0.35f else 1.0f
                }
            }
        }
        autoIsoSupportText.text = when {
            isoSnapshot == null -> "原厂菜单最高 ISO 3200；正在读取可用扩展档位。"
            isoSnapshot?.extendedReady == true -> "原厂菜单最高 ISO 3200；扩展档位由机身 Mod 提供。"
            else -> "原厂菜单最高 ISO 3200；12800／25600 需要启用新版机身控制模块。"
        }
        maxIso?.let { iso ->
            autoIsoMaxHeaderVal.text = "ISO $iso"
            autoIsoMaxHeaderVal.setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
        } ?: run {
            autoIsoMaxHeaderVal.text = "未读取"
            autoIsoMaxHeaderVal.setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
        }
        renderIsoPolicy()

        val fastest = values[Field.FASTEST] as? String
        for (i in 0 until fastestShutterGroup.childCount) {
            val pill = fastestShutterGroup.getChildAt(i) as TextView
            val shutter = pill.tag as? String ?: (if (pill.text == "关闭") "off" else pill.text.toString())
            val isSel = shutter == fastest
            if (isSel) {
                pill.background = roundedBg(ContextCompat.getColor(activity, R.color.nx_surface_container_highest), 24)
                pill.setTextColor(ContextCompat.getColor(activity, R.color.nx_primary))
                centerSelectedOnce(fastestShutterScroll, pill, shutter)
            } else {
                val isFast = shutter in listOf("1/500", "1/1000", "1/2000", "1/4000", "1/6000", "1/8000")
                pill.background = roundedBg(
                    if (isFast) ContextCompat.getColor(activity, R.color.nx_surface_container_high)
                    else ContextCompat.getColor(activity, R.color.nx_surface_container),
                    24
                )
                pill.setTextColor(if (isFast) ContextCompat.getColor(activity, R.color.nx_on_surface) else ContextCompat.getColor(activity, R.color.nx_text_dim))
            }
        }
        fastest?.let { shutter ->
            val desc = when (shutter) {
                "off" -> "关闭 (无快门上限)"
                else -> shutter
            }
            fastestShutterHeaderVal.text = desc
            fastestShutterHeaderVal.setTextColor(if (shutter == "off") ContextCompat.getColor(activity, R.color.nx_text_dim) else ContextCompat.getColor(activity, R.color.nx_primary))
        } ?: run {
            fastestShutterHeaderVal.text = "未读取"
            fastestShutterHeaderVal.setTextColor(ContextCompat.getColor(activity, R.color.nx_text_dim))
        }

        btnResetRecipe.isEnabled = !busy && camera.profile.recipeStyle && recipeSnapshotConfirmed
        btnResetRgb.isEnabled = !busy && listOf(Field.RED, Field.GREEN, Field.BLUE).all { values.containsKey(it) }
        btnClearLook.isEnabled = !busy && looksState?.writable == true
        refreshBtn.isEnabled = !busy
        fun control(view: View, field: Field) {
            view.isEnabled = !busy && NxApp.camera === camera && supported(field.group) && field in values
            view.alpha = if (view.isEnabled) 1f else .45f
            if (Build.VERSION.SDK_INT >= 30) view.stateDescription = when {
                !supported(field.group) -> "当前连接不支持"
                field !in values -> "尚未读取相机值"
                busy -> "正在同步"
                else -> null
            }
        }
        listOf(sliderRed to Field.RED, sliderGreen to Field.GREEN, sliderBlue to Field.BLUE,
            sliderSat to Field.SATURATION, sliderSharp to Field.SHARPNESS,
            sliderContrast to Field.CONTRAST, sliderTone to Field.TONE,
            switchDialLock to Field.DIAL, switchIsoExpansion to Field.EXPANSION).forEach { (view, field) -> control(view, field) }
        listOf(isoStepGroup to Field.STEP, autoIsoMaxGroup to Field.MAXIMUM,
            fastestShutterGroup to Field.FASTEST).forEach { (group, field) ->
            for (i in 0 until group.childCount) control(group.getChildAt(i), field)
        }
        for (i in 0 until autoIsoMaxGroup.childCount) {
            val pill = autoIsoMaxGroup.getChildAt(i)
            val iso = pill.tag as? Int
            val available = iso in (isoSnapshot?.supportedMaximums ?: emptyList())
            pill.visibility = if ((step == "one-stop" && iso !in FULL_STOP_ISOS && iso != maxIso) ||
                (isoSnapshot != null && iso != null && iso > 6400 && !available)) View.GONE else View.VISIBLE
            if (!available) {
                pill.isEnabled = false
                pill.alpha = .35f
                if (Build.VERSION.SDK_INT >= 30) pill.stateDescription = "当前机身服务不支持此上限"
            }
        }
        btnOpenStockIso.isEnabled = !busy && NxApp.camera === camera && camera.profile.keyInput
        } finally {
            rendering = false
        }
    }

    private fun centerSelectedOnce(scroll: HorizontalScrollView, selected: View, value: Any) {
        if (scroll.tag == value) return
        val animate = scroll.tag != null
        scroll.tag = value
        scroll.post {
            if (scroll.tag != value || !sheet.isShowing) return@post
            val target = (selected.left - (scroll.width - selected.width) / 2).coerceAtLeast(0)
            if (animate) scroll.smoothScrollTo(target, 0) else scroll.scrollTo(target, 0)
        }
    }

    private fun isoLimitSummary(saved: Boolean = false): String = when (isoSnapshot?.maxStatus) {
        ExposureControls.MaxStatus.POLICY_LIMITED ->
            if (saved) "自动 ISO 上限已保存；当前曝光策略采用较低上限" else "自动 ISO 上限受当前曝光策略限制"
        ExposureControls.MaxStatus.MISMATCH -> "自动 ISO 上限读回不一致，请刷新确认"
        ExposureControls.MaxStatus.CONFIRMED ->
            if (saved) "自动 ISO 上限已保存并读回" else "已读取相机设置 · 点击刷新更新"
        else -> if (saved) "自动 ISO 上限已保存；有效上限尚未确认" else "已读取相机设置 · 点击刷新更新"
    }

    private fun renderIsoPolicy() {
        val snapshot = isoSnapshot
        val policy = snapshot?.aePolicy
        if (snapshot == null || policy == null) {
            autoIsoPolicyText.text = when {
                snapshot != null -> "本次读取：当前接口未提供 ISO 模式、当前 ISO 与 AE 上限。"
                busy -> "正在读取曝光快照…"
                !supported(Group.ISO) -> "当前连接未提供 ISO 设置。"
                else -> "尚未读取曝光快照，请点击刷新。"
            }
            autoIsoPolicyText.setTextColor(ContextCompat.getColor(activity, R.color.nx_on_surface_variant))
            return
        }
        fun iso(value: Int?) = value?.let { "ISO $it" } ?: "未提供"
        val mode = when (policy.isoMode) {
            ExposureControls.IsoMode.AUTO -> "自动 ISO"
            ExposureControls.IsoMode.MANUAL -> "手动 ISO" +
                (policy.isoSetting?.let { "（设定 $it）" } ?: "")
            ExposureControls.IsoMode.UNKNOWN -> "ISO 模式未知"
        }
        val lines = mutableListOf(
            "本次读取：$mode",
            "设定上限：${iso(snapshot.maximum)} · 有效上限：${iso(policy.maxIso)}",
            "当前 ISO：${policy.currentIso?.toString() ?: "未读到"}",
        )
        when (snapshot.maxStatus) {
            ExposureControls.MaxStatus.POLICY_LIMITED ->
                lines += "设定已送达；当前曝光策略将自动 ISO 限制为 ${iso(policy.maxIso)}。"
            ExposureControls.MaxStatus.MISMATCH -> lines += "机身读回与设定不同，请刷新确认。"
            ExposureControls.MaxStatus.CONFIRMED -> lines += "自动曝光上限已读回。"
            ExposureControls.MaxStatus.UNKNOWN -> lines += "有效上限尚未确认。"
        }
        if (policy.isoMode == ExposureControls.IsoMode.MANUAL) {
            lines += "当前使用手动 ISO；自动 ISO 上限不限制手动设定的 ISO。"
        }
        lines += "点击刷新更新此快照。"
        autoIsoPolicyText.text = lines.joinToString("\n")
        autoIsoPolicyText.setTextColor(ContextCompat.getColor(activity,
            if (snapshot.maxStatus == ExposureControls.MaxStatus.MISMATCH) R.color.nx_error else R.color.nx_on_surface_variant))
    }

    private fun invalidate() {
        values.clear()
        isoSnapshot = null
        looksState = null
        pendingLook = null
        recipeCatalogue = null
        recipeState = null
        recipeSnapshotConfirmed = false
        recipeIssue = ""
    }

    private fun putIso(state: ExposureControls.IsoState) {
        isoSnapshot = state
        values.keys.removeAll { it.group == Group.ISO }
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
            current != null -> LookChoice(current.id, current.label, state.strength)
            else -> throw NxException("已选 LUT 不在当前部署列表中")
        }
        pendingLook = (values[Field.LOOK] as LookChoice).takeIf { state.selectionValid && !state.stock }
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
        val selected = if (state.stock) "原厂" else current?.label ?: "无效选择"
        val strength = if (state.strengthSupported && !state.stock) " · 强度 ${state.strength}%" else ""
        looksDetailText.text = "当前：$selected$strength\n$stage · 实拍效果未验证"
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
            lines += recipeRouteStatus("预览", state.preview, state.superseded)
            lines += recipeRouteStatus("拍摄", state.capture, state.superseded)
        }
        if (recipeIssue.isNotBlank()) lines += recipeIssue
        recipeDetailText.text = lines.joinToString(" · ")
    }

    private fun recipeRouteStatus(label: String, stages: List<RecipeControls.Stage>, superseded: Boolean): String {
        if (superseded) return "$label：等待重新应用配方"
        val errors = stages.mapIndexedNotNull { index, stage ->
            if (stage.error == 0) null else "${RecipeControls.STAGE_LABELS[index]} ${RecipeControls.stageError(stage.error)}"
        }
        val applied = stages.count { it.applied }
        val progress = if (applied == stages.size) "原厂接口已接收 $applied/${stages.size}"
            else "原厂接口已接收 $applied/${stages.size}，等待其余阶段"
        return "$label：$progress" + if (errors.isEmpty()) "" else "（${errors.joinToString("；")}）"
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
        sliderJobs.values.forEach { it.cancel() }
        sliderJobs.clear()
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
                    if (group == Group.ISO) isoSnapshot = null
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
                    isoSnapshot?.maxStatus in listOf(ExposureControls.MaxStatus.MISMATCH, ExposureControls.MaxStatus.POLICY_LIMITED) -> isoLimitSummary()
                    else -> "已读取相机设置 · 点击刷新更新"
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { invalidate(); statusText.text = "读取异常：${e.message}" }
            finally { busy = false; render(); scheduleRecipePoll() }
        }
    }

    private fun set(field: Field, requested: Any) {
        if (busy || rendering || !supported(field.group) || field !in values || NxApp.camera !== camera) return
        if (field == Field.MAXIMUM && requested !in (isoSnapshot?.supportedMaximums ?: emptyList())) return
        if (values[field] == requested && field != Field.LOOK) { render(); return }
        write(field.title) {
            when (field.group) {
                Group.ISO -> putIso(camera.setIsoSetting(field.wire, requested))
                Group.SHUTTER -> { values[field] = camera.setFastestShutter(requested as String) }
                Group.DIAL -> { values[field] = camera.setDialLock(requested as Boolean) }
                Group.RGB -> putRgb(camera.setRgbSetting(field.wire, requested as Int))
                Group.PW -> putPw(camera.setPictureWizardSetting(field.wire, requested as Int))
                Group.LOOKS -> {
                    val look = requested as LookChoice
                    val strength = if (look.id != null && looksState?.strengthSupported == true) look.strength else null
                    putLooks(camera.selectLook(look.id, strength))
                }
                Group.RECIPE -> throw NxException("请选择目录中的完整七维配方")
            }
            val actual = values[field] ?: throw NxException("原厂读回无法识别")
            val confirmed = if (field == Field.LOOK) {
                val look = requested as LookChoice
                (actual as LookChoice).id == look.id &&
                    (look.id == null || looksState?.strengthSupported != true || actual.strength == look.strength)
            } else actual == requested
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
                    Field.MAXIMUM.title -> isoLimitSummary(saved = true)
                    Field.LOOK.title -> "LUT 选择已暂存 · 原厂转发状态见下方"
                    Field.RECIPE.title -> "配方七值已读回，预览与拍摄状态见下方"
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
