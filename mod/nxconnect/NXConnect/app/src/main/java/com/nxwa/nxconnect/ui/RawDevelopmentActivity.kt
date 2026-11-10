// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.text.InputFilter
import android.view.Gravity
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.progressindicator.CircularProgressIndicator
import com.google.android.material.slider.Slider
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.CardLutLibrary
import com.nxwa.nxconnect.data.CubeLut
import com.nxwa.nxconnect.data.CxoFilmChoice
import com.nxwa.nxconnect.data.CxoFilmProcessor
import com.nxwa.nxconnect.data.DecodedNxRaw
import com.nxwa.nxconnect.data.LutLibrary
import com.nxwa.nxconnect.data.NxRawDecoder
import com.nxwa.nxconnect.data.RawDecodeException
import com.nxwa.nxconnect.data.RawRecipe
import com.nxwa.nxconnect.data.RawRecipeStore
import com.nxwa.nxconnect.data.RawRenderParams
import com.nxwa.nxconnect.data.RawRenderPipeline
import com.nxwa.nxconnect.transfer.MediaStoreWriter
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Offline RAW development and LUT preview; no camera connection is required. */
class RawDevelopmentActivity : OneUiInfoActivity() {
    private data class LutChoice(val scope: String, val id: String, val label: String, val grid: Int) {
        val key get() = "$scope:$id"
    }

    private data class SliderBinding(val slider: Slider, val label: TextView, val format: (Float) -> String)

    private lateinit var page: OneUiInfoPage
    private lateinit var recipes: RawRecipeStore
    private lateinit var library: LutLibrary
    private lateinit var filmProcessor: CxoFilmProcessor
    private lateinit var image: ZoomImageView
    private lateinit var emptyText: TextView
    private lateinit var progress: CircularProgressIndicator
    private lateinit var imageStatus: TextView
    private lateinit var detailsText: TextView
    private lateinit var lutSpinner: Spinner
    private lateinit var lutStatus: TextView
    private lateinit var filmCategorySpinner: Spinner
    private lateinit var filmSpinner: Spinner
    private lateinit var filmStatus: TextView
    private lateinit var exportButton: MaterialButton
    private lateinit var saveRecipeButton: MaterialButton
    private val sliderBindings = LinkedHashMap<String, SliderBinding>()
    private val renderExecutor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "nx-raw-render").apply { priority = Thread.NORM_PRIORITY - 1 }
    }
    private val renderDispatcher = renderExecutor.asCoroutineDispatcher()
    private val preferences by lazy { getSharedPreferences("lut_card", MODE_PRIVATE) }

    private var sourceUri: Uri? = null
    private var sourceName = ""
    private var basePixels: IntArray? = null
    private var workPixels: IntArray? = null
    private var previewBitmap: Bitmap? = null
    private var params = RawRenderParams()
    private var lutChoices: List<LutChoice> = emptyList()
    private var lutData: CubeLut.Data? = null
    private var filmChoices: List<CxoFilmChoice> = emptyList()
    private var filteredFilmChoices: List<CxoFilmChoice> = emptyList()
    private var cardLibrary: CardLutLibrary? = null
    private var currentRecipeId: String? = null
    private var currentRecipeName: String? = null
    private var pendingLutScope: String? = null
    private var pendingLutId: String? = null
    private var pendingFilmId: String? = null
    private var pendingFilmLabel: String? = null
    private var filmStrength = FILM_STRENGTH_DEFAULT
    private var updatingControls = false
    private var decodeJob: Job? = null
    private var renderJob: Job? = null
    private var lutJob: Job? = null
    private var exportJob: Job? = null
    private var decodeGeneration = 0
    private var renderGeneration = 0
    private var lutGeneration = 0
    private var lastRenderMillis = 0L
    private var firstResume = true

    private val rawPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            persistReadAccess(uri)
            currentRecipeId = null
            currentRecipeName = null
            openRaw(uri)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        recipes = RawRecipeStore(applicationContext)
        library = LutLibrary(applicationContext)
        filmProcessor = CxoFilmProcessor(applicationContext)
        filmChoices = runCatching { filmProcessor.loadCatalog() }.getOrElse { emptyList<CxoFilmChoice>() }
        restoreState(savedInstanceState)

        page = OneUiInfoPage(this, "RAW 显影")
        page.binding.pageToolbar.menu.add("选择 RAW").apply {
            setIcon(R.drawable.ic_oneui_gallery_tab_photo)
            setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
            setOnMenuItemClickListener { chooseRaw(); true }
        }
        page.binding.pageToolbar.menu.add("配方").apply {
            setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
            setOnMenuItemClickListener { showRecipeMenu(); true }
        }
        buildUi()
        applyParamsToSliders()

        val recipeId = intent.getStringExtra("recipe_id")
            ?: savedInstanceState?.getString("currentRecipeId")
        if (savedInstanceState == null && recipeId != null) {
            recipes.get(recipeId)?.let { loadRecipe(it) }
        } else {
            lifecycleScope.launch { refreshLutChoices() }
            sourceUri?.let { openRaw(it, sourceName.ifBlank { displayName(it, "RAW") }) }
        }
    }

    override fun onResume() {
        super.onResume()
        if (firstResume) {
            firstResume = false
        } else if (::lutSpinner.isInitialized) {
            lifecycleScope.launch { refreshLutChoices() }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        sourceUri?.let { outState.putString("sourceUri", it.toString()) }
        outState.putString("sourceName", sourceName)
        outState.putString("currentRecipeId", currentRecipeId)
        outState.putString("currentRecipeName", currentRecipeName)
        outState.putString("pendingLutScope", pendingLutScope)
        outState.putString("pendingLutId", pendingLutId)
        outState.putString("pendingFilmId", pendingFilmId)
        outState.putString("pendingFilmLabel", pendingFilmLabel)
        outState.putInt("filmStrength", filmStrength)
        outState.putFloat("exposureEv", params.exposureEv)
        outState.putInt("contrast", params.contrast)
        outState.putInt("highlights", params.highlights)
        outState.putInt("shadows", params.shadows)
        outState.putInt("temperature", params.temperature)
        outState.putInt("tint", params.tint)
        outState.putInt("saturation", params.saturation)
        outState.putInt("lutStrength", params.lutStrength)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        decodeJob?.cancel()
        renderJob?.cancel()
        lutJob?.cancel()
        exportJob?.cancel()
        filmProcessor.close()
        renderExecutor.shutdownNow()
        renderDispatcher.close()
        previewBitmap?.recycle()
        previewBitmap = null
        basePixels = null
        workPixels = null
        super.onDestroy()
    }

    private fun restoreState(state: Bundle?) {
        if (state == null) return
        state.getString("sourceUri")?.let { sourceUri = Uri.parse(it) }
        sourceName = state.getString("sourceName").orEmpty()
        currentRecipeId = state.getString("currentRecipeId")
        currentRecipeName = state.getString("currentRecipeName")
        pendingLutScope = state.getString("pendingLutScope")
        pendingLutId = state.getString("pendingLutId")
        pendingFilmId = state.getString("pendingFilmId")
        pendingFilmLabel = state.getString("pendingFilmLabel")
        filmStrength = state.getInt("filmStrength", FILM_STRENGTH_DEFAULT)
        params = RawRenderParams(
            exposureEv = state.getFloat("exposureEv", 0f),
            contrast = state.getInt("contrast", 0),
            highlights = state.getInt("highlights", 0),
            shadows = state.getInt("shadows", 0),
            temperature = state.getInt("temperature", 0),
            tint = state.getInt("tint", 0),
            saturation = state.getInt("saturation", 0),
            lutStrength = state.getInt("lutStrength", 100),
        )
    }

    private fun buildUi() {
        page.binding.pageContent.removeAllViews()
        sliderBindings.clear()

        val previewCard = page.card("显影预览")
        val frame = FrameLayout(this).apply {
            setBackgroundColor(android.graphics.Color.BLACK)
            clipToOutline = true
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, page.dp(340))
        }
        image = ZoomImageView(this).apply {
            scaleType = android.widget.ImageView.ScaleType.FIT_CENTER
            contentDescription = "RAW 显影预览"
            layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        emptyText = page.text("选择一张 NX300M SRW 开始显影", 15f, true).apply {
            gravity = Gravity.CENTER
            setTextColor(android.graphics.Color.WHITE)
            setPadding(page.dp(24), 0, page.dp(24), 0)
            layoutParams = FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        }
        progress = CircularProgressIndicator(this).apply {
            isIndeterminate = true
            isVisible = false
            contentDescription = "正在处理 RAW"
            setIndicatorColor(color(R.color.nx_primary))
            layoutParams = FrameLayout.LayoutParams(page.dp(44), page.dp(44), Gravity.CENTER)
        }
        frame.addView(image)
        frame.addView(emptyText)
        frame.addView(progress)
        previewCard.addView(frame)
        imageStatus = page.text("等待选择 RAW", 13f, true).apply {
            setPadding(0, page.dp(12), 0, 0)
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        previewCard.addView(imageStatus)
        detailsText = page.text("", 12f, true).apply { setPadding(0, page.dp(6), 0, 0) }
        previewCard.addView(detailsText)

        val sourceActions = page.card()
        val firstRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        sourceActions.addView(firstRow)
        val choose = materialButton("选择 SRW / DNG") { chooseRaw() }
        val manage = materialButton("LUT 库") {
            OneUiTransitions.open(this, Intent(this, LutLibraryActivity::class.java).putExtra("phone_tab", true))
        }
        firstRow.addView(choose, weightedButtonParams(endMargin = page.dp(4)))
        firstRow.addView(manage, weightedButtonParams(startMargin = page.dp(4)))

        val lutCard = page.card("套用校色 LUT")
        lutSpinner = Spinner(this).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            contentDescription = "选择 LUT"
            adapter = ArrayAdapter(this@RawDevelopmentActivity, android.R.layout.simple_spinner_dropdown_item,
                mutableListOf("正在读取 LUT 仓库…"))
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (!updatingControls && lutChoices.isNotEmpty()) selectLut(position)
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
            }
        }
        lutCard.addView(lutSpinner)
        addSlider(
            lutCard, "lutStrength", "LUT 强度", 0f..100f, 1f, params.lutStrength.toFloat(),
            { value -> "${value.toInt()}%" },
        ) { params = params.copy(lutStrength = it.toInt()); schedulePreview() }
        lutStatus = page.text("未套 LUT", 12f, true).apply { setPadding(0, page.dp(6), 0, 0) }
        lutCard.addView(lutStatus)

        val filmCard = page.card("胶片风格")
        page.text("CXO FilmPack 位于手机 RAW 显影末端；不会写入或替换相机卡中的 9×9×9 LUT。", 12f, true)
            .also(filmCard::addView)
        filmCategorySpinner = Spinner(this).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            contentDescription = "胶片分类"
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (!updatingControls) populateFilmSpinner()
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
            }
        }
        filmCard.addView(filmCategorySpinner)
        filmSpinner = Spinner(this).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            contentDescription = "选择胶片"
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (!updatingControls) selectFilm(position)
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
            }
        }
        filmCard.addView(filmSpinner)
        addSlider(
            filmCard, "filmStrength", "胶片强度", 0f..200f, 1f, filmStrength.toFloat(),
            { value -> "${value.toInt()}%" },
        ) {
            filmStrength = it.toInt()
            updateFilmStatus()
            schedulePreview()
        }
        filmStatus = page.text("未套胶片", 12f, true).apply { setPadding(0, page.dp(6), 0, 0) }
        filmCard.addView(filmStatus)
        initializeFilmControls()

        val adjustmentCard = page.card("基础微调")
        page.text("RAW 解包与相机白平衡固定后调整；这里不会改写相机卡里的 9×9×9 表。", 13f, true)
            .also(adjustmentCard::addView)
        addSlider(adjustmentCard, "exposure", "曝光", -3f..3f, 0.1f, params.exposureEv,
            { String.format(Locale.ROOT, "%+.1f EV", it) }) {
            params = params.copy(exposureEv = it); schedulePreview()
        }
        addSlider(adjustmentCard, "contrast", "对比度", -100f..100f, 1f, params.contrast.toFloat(), ::signedPercent) {
            params = params.copy(contrast = it.toInt()); schedulePreview()
        }
        addSlider(adjustmentCard, "highlights", "高光", -100f..100f, 1f, params.highlights.toFloat(), ::signedPercent) {
            params = params.copy(highlights = it.toInt()); schedulePreview()
        }
        addSlider(adjustmentCard, "shadows", "阴影", -100f..100f, 1f, params.shadows.toFloat(), ::signedPercent) {
            params = params.copy(shadows = it.toInt()); schedulePreview()
        }
        addSlider(adjustmentCard, "temperature", "冷暖", -100f..100f, 1f, params.temperature.toFloat(), ::signedPercent) {
            params = params.copy(temperature = it.toInt()); schedulePreview()
        }
        addSlider(adjustmentCard, "tint", "色调", -100f..100f, 1f, params.tint.toFloat(), ::signedPercent) {
            params = params.copy(tint = it.toInt()); schedulePreview()
        }
        addSlider(adjustmentCard, "saturation", "饱和度", -100f..100f, 1f, params.saturation.toFloat(), ::signedPercent) {
            params = params.copy(saturation = it.toInt()); schedulePreview()
        }

        val outputCard = page.card()
        saveRecipeButton = materialButton("保存配方", enabled = false) { promptSaveRecipe() }
        exportButton = materialButton("导出 JPEG", enabled = false) { chooseExportQuality() }
        outputCard.addView(saveRecipeButton)
        outputCard.addView(exportButton)
    }

    private fun addSlider(
        parent: LinearLayout,
        key: String,
        title: String,
        range: ClosedFloatingPointRange<Float>,
        step: Float,
        value: Float,
        formatter: (Float) -> String,
        changed: (Float) -> Unit,
    ) {
        val heading = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, page.dp(12), 0, 0)
        }
        val label = page.text(title, 14f).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        }
        val valueLabel = page.text(formatter(value), 14f, true).apply {
            gravity = Gravity.END
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        heading.addView(label)
        heading.addView(valueLabel)
        parent.addView(heading)
        val slider = Slider(this).apply {
            valueFrom = range.start
            valueTo = range.endInclusive
            stepSize = step
            this.value = value.coerceIn(valueFrom, valueTo)
            contentDescription = title
            setLabelFormatter { formatter(it) }
        }
        slider.addOnChangeListener { _, next, _ ->
            valueLabel.text = formatter(next)
            if (!updatingControls) changed(next)
        }
        parent.addView(slider)
        sliderBindings[key] = SliderBinding(slider, valueLabel, formatter)
    }

    private fun chooseRaw() {
        rawPicker.launch(arrayOf("image/x-samsung-srw", "image/x-adobe-dng", "image/*", "application/octet-stream"))
    }

    private fun openRaw(uri: Uri, knownName: String? = null) {
        decodeJob?.cancel()
        renderJob?.cancel()
        val generation = ++decodeGeneration
        sourceUri = uri
        sourceName = (knownName ?: displayName(uri, "RAW")).trim().ifEmpty { "RAW" }
        basePixels = null
        workPixels = null
        image.setImageBitmap(null)
        emptyText.text = "正在解包 RAW…"
        emptyText.isVisible = true
        imageStatus.text = "正在读取相机 RAW、应用 NX300M 色彩矩阵和相机白平衡"
        detailsText.text = sourceName
        progress.isVisible = true
        setOutputEnabled(false)

        decodeJob = lifecycleScope.launch {
            var temporary: File? = null
            try {
                val decoded = withContext(Dispatchers.IO) {
                    temporary = File.createTempFile("nx-raw-", ".srw", cacheDir)
                    contentResolver.openInputStream(uri)?.use { input ->
                        temporary!!.outputStream().use { output -> input.copyTo(output) }
                    } ?: error("无法读取所选 RAW 文件")
                    NxRawDecoder.decode(temporary!!, halfSize = true, previewMaxDimension = PREVIEW_MAX_DIMENSION)
                }
                if (generation != decodeGeneration) {
                    decoded.bitmap.recycle()
                    return@launch
                }
                val width = decoded.bitmap.width
                val height = decoded.bitmap.height
                val pixels = IntArray(width * height)
                decoded.bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
                decoded.bitmap.recycle()
                basePixels = pixels
                workPixels = IntArray(pixels.size)
                ensurePreviewBitmap(width, height)
                emptyText.isVisible = false
                image.setImageBitmap(previewBitmap)
                imageStatus.text = "RAW 已解包 · ${width} × ${height} 预览"
                detailsText.text = describeRaw(decoded, width, height)
                setOutputEnabled(true)
                renderPreview(immediate = true)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (generation == decodeGeneration) {
                    emptyText.text = "无法显影这张 RAW"
                    emptyText.isVisible = true
                    imageStatus.text = error.message ?: "LibRaw 解码失败"
                    setOutputEnabled(false)
                }
            } finally {
                temporary?.delete()
                if (generation == decodeGeneration) progress.isVisible = false
            }
        }
    }

    private fun describeRaw(decoded: DecodedNxRaw, width: Int, height: Int): String {
        val info = decoded.info
        val camera = listOf(info.make, info.model).filter(String::isNotBlank).joinToString(" ")
            .ifBlank { "Samsung NX300M" }
        val exposure = buildList {
            if (info.iso > 0f) add("ISO ${info.iso.toInt()}")
            if (info.shutterSeconds > 0f) add(shutterText(info.shutterSeconds))
            if (info.aperture > 0f) add("f/${trimDecimal(info.aperture)}")
            if (info.focalLength > 0f) add("${trimDecimal(info.focalLength)} mm")
        }.joinToString(" · ")
        return listOf(camera, "$width × $height 预览", exposure, "LibRaw ${info.libRawVersion}")
            .filter(String::isNotBlank).joinToString("\n")
    }

    private fun ensurePreviewBitmap(width: Int, height: Int) {
        if (previewBitmap?.width == width && previewBitmap?.height == height) return
        previewBitmap?.recycle()
        previewBitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
    }

    private fun schedulePreview() {
        renderPreview(immediate = false)
    }

    private fun renderPreview(immediate: Boolean) {
        val base = basePixels ?: return
        val target = workPixels ?: IntArray(base.size).also { workPixels = it }
        val generation = ++renderGeneration
        renderJob?.cancel()
        progress.isVisible = true
        imageStatus.text = "正在更新预览…"
        val snapshot = params
        val lut = lutData
        val film = selectedFilm()
        val strength = filmStrength
        renderJob = lifecycleScope.launch {
            try {
                if (!immediate) {
                    val wait = (RENDER_DEBOUNCE_MS - (System.currentTimeMillis() - lastRenderMillis)).coerceIn(60L, RENDER_DEBOUNCE_MS)
                    delay(wait)
                }
                val rawCompleted = withContext(renderDispatcher) {
                    RawRenderPipeline.renderInto(base, target, snapshot, lut, shouldContinue = { isActive })
                }
                if (!rawCompleted) return@launch
                val filmCompleted = withContext(renderDispatcher) {
                    filmProcessor.renderInPlace(
                        target,
                        width = previewBitmap?.width ?: error("预览位图已释放"),
                        film = film,
                        intensity = strength,
                        shouldContinue = { isActive && generation == renderGeneration },
                    )
                }
                if (!filmCompleted) return@launch
                if (generation != renderGeneration) return@launch
                val bitmap = previewBitmap ?: return@launch
                bitmap.setPixels(target, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                image.invalidate()
                lastRenderMillis = System.currentTimeMillis()
                imageStatus.text = buildString {
                    append("${bitmap.width} × ${bitmap.height} 预览")
                    if (lut != null) append(" · ${lut.title ?: selectedLutLabel()}")
                    if (lut != null) append(" ${snapshot.lutStrength}%")
                    if (film != null && strength > 0) append(" · ${film.name} $strength%")
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (generation == renderGeneration) imageStatus.text = "预览更新失败：${error.message ?: "请重试"}"
            } finally {
                if (generation == renderGeneration) progress.isVisible = false
            }
        }
    }

    private suspend fun refreshLutChoices() {
        val generation = ++lutGeneration
        val choices = withContext(Dispatchers.IO) {
            val rows = ArrayList<LutChoice>()
            library.list().forEach { entry ->
                rows += LutChoice("phone", entry.id, entry.label, entry.grid)
            }
            val savedTree = preferences.getString("tree_uri", null)?.let(Uri::parse)
            if (savedTree != null && contentResolver.persistedUriPermissions.any {
                    it.uri == savedTree && it.isReadPermission
                }) {
                runCatching {
                    CardLutLibrary.open(applicationContext, savedTree).also { cardLibrary = it }.refresh().looks
                        .filter { it.directoryPresent }
                        .forEach { look -> rows += LutChoice("card", look.id, look.label, CubeLut.BLEND_GRID) }
                }
            }
            rows
        }
        if (generation != lutGeneration) return
        lutChoices = choices
        val labels = mutableListOf<String>()
        labels += "不套 LUT"
        labels += choices.map { choice ->
            if (choice.scope == "card") "SD · ${choice.label}" else "手机 · ${choice.label}"
        }
        val wanted = pendingLutId?.let { id -> choices.indexOfFirst { it.id == id && it.scope == (pendingLutScope ?: "phone") } }
        val selected = wanted?.takeIf { it >= 0 }?.plus(1) ?: 0
        updatingControls = true
        lutSpinner.adapter = ArrayAdapter(this@RawDevelopmentActivity,
            android.R.layout.simple_spinner_dropdown_item, labels)
        lutSpinner.setSelection(selected.coerceIn(0, labels.lastIndex), false)
        updatingControls = false
        if (selected == 0) {
            lutData = null
            lutStatus.text = "未套 LUT；显影参数仍会保留"
        } else {
            loadLut(choices[selected - 1])
        }
    }

    private fun selectLut(position: Int) {
        if (position == 0) {
            lutGeneration++
            lutData = null
            pendingLutScope = null
            pendingLutId = null
            lutStatus.text = "未套 LUT"
            renderPreview(immediate = true)
            return
        }
        loadLut(lutChoices[position - 1])
    }

    private fun loadLut(choice: LutChoice) {
        lutJob?.cancel()
        val generation = ++lutGeneration
        pendingLutScope = choice.scope
        pendingLutId = choice.id
        lutStatus.text = "正在读取 ${choice.label}…"
        lutJob = lifecycleScope.launch {
            try {
                val data = withContext(Dispatchers.IO) {
                    when (choice.scope) {
                        "card" -> (cardLibrary ?: error("SD 卡 LUT 目录已断开")).preparedBlendInput(choice.id).data
                        else -> library.preparedBlendInput(choice.id).data
                    }
                }
                if (generation != lutGeneration) return@launch
                lutData = data
                lutStatus.text = "${choice.label} · ${data.grid}×${data.grid}×${data.grid} · ${params.lutStrength}%"
                renderPreview(immediate = true)
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                if (generation == lutGeneration) {
                    lutData = null
                    lutStatus.text = "LUT 读取失败：${error.message ?: choice.label}"
                    renderPreview(immediate = true)
                }
            }
        }
    }

    private fun selectedLutLabel(): String {
        val position = lutSpinner.selectedItemPosition
        return if (position > 0) lutChoices.getOrNull(position - 1)?.label.orEmpty() else ""
    }

    private fun initializeFilmControls() {
        if (filmChoices.isEmpty()) {
            val labels = mutableListOf("胶片目录不可用")
            updatingControls = true
            filmCategorySpinner.adapter = spinnerAdapter(labels)
            filmSpinner.adapter = spinnerAdapter(mutableListOf("请检查安装包"))
            filmCategorySpinner.isEnabled = false
            filmSpinner.isEnabled = false
            updatingControls = false
            filmStatus.text = "胶片引擎未载入"
            return
        }

        val selected = selectedFilm()
        val categories = linkedSetOf(FILM_CATEGORY_ALL).apply {
            filmChoices.map(CxoFilmChoice::category).forEach(::add)
        }.toList()
        updatingControls = true
        filmCategorySpinner.adapter = spinnerAdapter(categories)
        val category = selected?.category ?: FILM_CATEGORY_ALL
        filmCategorySpinner.setSelection(categories.indexOf(category).coerceAtLeast(0), false)
        updatingControls = false
        populateFilmSpinner()
    }

    private fun populateFilmSpinner() {
        if (filmChoices.isEmpty()) return
        val category = filmCategorySpinner.selectedItem?.toString() ?: FILM_CATEGORY_ALL
        filteredFilmChoices = if (category == FILM_CATEGORY_ALL) {
            filmChoices
        } else {
            filmChoices.filter { it.category == category }
        }
        val labels = mutableListOf("不套胶片")
        labels += filteredFilmChoices.map(CxoFilmChoice::displayName)
        val previous = selectedFilm()
        val wanted = previous?.let { current ->
            filteredFilmChoices.indexOfFirst { it.id == current.id }.takeIf { it >= 0 }?.plus(1)
        } ?: 0
        updatingControls = true
        filmSpinner.adapter = spinnerAdapter(labels)
        filmSpinner.setSelection(wanted.coerceIn(0, labels.lastIndex), false)
        updatingControls = false
        val selected = filteredFilmChoices.getOrNull(wanted - 1)
        applyFilmChoice(selected, render = selected?.id != previous?.id && basePixels != null)
    }

    private fun selectFilm(position: Int) {
        applyFilmChoice(filteredFilmChoices.getOrNull(position - 1), render = true)
    }

    private fun applyFilmChoice(choice: CxoFilmChoice?, render: Boolean) {
        pendingFilmId = choice?.id
        pendingFilmLabel = choice?.name
        updateFilmStatus()
        if (render) renderPreview(immediate = true)
    }

    private fun selectedFilm(): CxoFilmChoice? = pendingFilmId?.let { id ->
        filmChoices.firstOrNull { it.id == id }
    }

    private fun updateFilmStatus() {
        if (!::filmStatus.isInitialized) return
        val film = selectedFilm()
        filmStatus.text = if (film == null) {
            "未套胶片；只保留相机 RAW、LUT 和基础微调"
        } else {
            "${film.name} · ${film.category} · $filmStrength%"
        }
    }

    private fun spinnerAdapter(labels: List<String>) = ArrayAdapter(
        this,
        android.R.layout.simple_spinner_dropdown_item,
        labels.toMutableList(),
    )

    private fun chooseExportQuality() {
        if (basePixels == null) return
        NxDialogBuilder(this)
            .setTitle("导出 JPEG")
            .setItems(arrayOf("快速 · 半尺寸 RAW", "精细 · 完整分辨率")) { _, which ->
                exportJpeg(fullResolution = which == 1)
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun exportJpeg(fullResolution: Boolean) {
        val uri = sourceUri ?: return
        val name = sourceName
        exportJob?.cancel()
        exportButton.isEnabled = false
        progress.isVisible = true
        imageStatus.text = if (fullResolution) "正在解码完整 RAW…" else "正在解码半尺寸 RAW…"
        exportJob = lifecycleScope.launch {
            var bitmap: Bitmap? = null
            try {
                val temporary = withContext(Dispatchers.IO) {
                    File.createTempFile("nx-export-", ".srw", cacheDir).also { target ->
                        contentResolver.openInputStream(uri)?.use { input ->
                            target.outputStream().use { output -> input.copyTo(output) }
                        } ?: throw IllegalStateException("无法重新读取原始 RAW")
                    }
                }
                try {
                    var fallback = false
                    var decoded: DecodedNxRaw
                    try {
                        decoded = withContext(Dispatchers.IO) {
                            NxRawDecoder.decode(temporary, halfSize = !fullResolution)
                        }
                    } catch (error: Throwable) {
                        if (!fullResolution || !isMemoryFailure(error)) throw error
                        fallback = true
                        imageStatus.text = "完整尺寸内存不足，改用半尺寸导出…"
                        decoded = withContext(Dispatchers.IO) { NxRawDecoder.decode(temporary, halfSize = true) }
                    }
                    bitmap = decoded.bitmap
                    imageStatus.text = "正在应用显影参数 ${decoded.bitmap.width}×${decoded.bitmap.height}…"
                    val film = selectedFilm()
                    val strength = filmStrength
                    val completed = withContext(renderDispatcher) {
                        renderBitmapInBands(
                            decoded.bitmap,
                            params,
                            lutData,
                            film,
                            strength,
                            shouldContinue = { isActive },
                        )
                    }
                    if (!completed) throw CancellationException("显影已取消")
                    val outputName = safeOutputName(name)
                    val exported = withContext(Dispatchers.IO) {
                        val target = MediaStoreWriter.createImageTarget(this@RawDevelopmentActivity, outputName)
                        try {
                            target.output ?: error("系统没有返回 JPEG 输出流")
                            if (!decoded.bitmap.compress(Bitmap.CompressFormat.JPEG, 94, target.output)) {
                                error("JPEG 编码失败")
                            }
                            MediaStoreWriter.finish(this@RawDevelopmentActivity, target)
                            target.displayPath
                        } catch (error: Throwable) {
                            MediaStoreWriter.abort(this@RawDevelopmentActivity, target)
                            throw error
                        }
                    }
                    imageStatus.text = "已导出 JPEG · $exported" + if (fallback) " · 已自动回退半尺寸" else ""
                    Toast.makeText(this@RawDevelopmentActivity, "JPEG 已保存到 $exported", Toast.LENGTH_LONG).show()
                } finally {
                    bitmap?.recycle()
                    bitmap = null
                    temporary.delete()
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Throwable) {
                imageStatus.text = "导出失败：${error.message ?: "请重试"}"
                Toast.makeText(this@RawDevelopmentActivity, imageStatus.text, Toast.LENGTH_LONG).show()
            } finally {
                exportButton.isEnabled = basePixels != null
                progress.isVisible = false
            }
        }
    }

    private fun renderBitmapInBands(
        bitmap: Bitmap,
        params: RawRenderParams,
        lut: CubeLut.Data?,
        film: CxoFilmChoice?,
        strength: Int,
        shouldContinue: () -> Boolean,
    ): Boolean {
        val rowsPerBand = (262_144 / bitmap.width).coerceIn(16, 256)
        val input = IntArray(bitmap.width * rowsPerBand)
        val output = IntArray(input.size)
        var y = 0
        while (y < bitmap.height) {
            if (!shouldContinue()) return false
            val rows = minOf(rowsPerBand, bitmap.height - y)
            val count = bitmap.width * rows
            bitmap.getPixels(input, 0, bitmap.width, 0, y, bitmap.width, rows)
            if (!RawRenderPipeline.renderInto(input, output, params, lut, shouldContinue)) return false
            if (!filmProcessor.renderInPlace(output, bitmap.width, film, strength, shouldContinue)) return false
            bitmap.setPixels(output, 0, bitmap.width, 0, y, bitmap.width, rows)
            y += rows
        }
        return true
    }

    private fun promptSaveRecipe() {
        if (basePixels == null || sourceUri == null) return
        val input = EditText(this).apply {
            setSingleLine()
            filters = arrayOf(InputFilter.LengthFilter(80))
            setText(currentRecipeName ?: sourceName.substringBeforeLast('.'))
            selectAll()
        }
        val dialog = NxDialogBuilder(this).setTitle("保存 RAW 配方").setView(input)
            .setNegativeButton("取消", null).setPositiveButton("保存", null).create()
        dialog.setOnShowListener {
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).setOnClickListener {
                val name = input.text.toString().trim()
                if (name.isEmpty()) {
                    input.error = "请输入配方名称"
                    return@setOnClickListener
                }
                dialog.dismiss()
                saveRecipe(name)
            }
        }
        dialog.show()
    }

    private fun saveRecipe(name: String) {
        val uri = sourceUri ?: return
        val now = System.currentTimeMillis()
        val existing = currentRecipeId?.let { runCatching { recipes.get(it) }.getOrNull() }
        val id = currentRecipeId ?: recipes.newId()
        val choice = lutChoices.getOrNull(lutSpinner.selectedItemPosition - 1)
        val recipe = RawRecipe(
            id = id,
            name = name,
            sourceUri = uri.toString(),
            sourceName = sourceName,
            createdAt = existing?.createdAt ?: now,
            updatedAt = now,
            params = params,
            lutScope = choice?.scope.takeIf { lutData != null },
            lutId = choice?.id.takeIf { lutData != null },
            lutLabel = choice?.label.takeIf { lutData != null },
            filmId = pendingFilmId.takeIf { selectedFilm() != null },
            filmLabel = pendingFilmLabel.takeIf { selectedFilm() != null },
            filmStrength = filmStrength,
        )
        lifecycleScope.launch {
            try {
                val saved = withContext(Dispatchers.IO) { recipes.save(recipe) }
                currentRecipeId = saved.id
                currentRecipeName = saved.name
                imageStatus.text = "配方已保存 · ${saved.name}"
            } catch (error: Throwable) {
                imageStatus.text = "配方保存失败：${error.message ?: "请重试"}"
            }
        }
    }

    private fun showRecipeMenu() {
        lifecycleScope.launch {
            val rows = withContext(Dispatchers.IO) { recipes.list() }
            if (rows.isEmpty()) {
                NxDialogBuilder(this@RawDevelopmentActivity).setTitle("RAW 配方")
                    .setMessage("还没有保存的配方。先选择 RAW、调好参数后再保存。")
                    .setPositiveButton("确定", null).show()
                return@launch
            }
            val labels = rows.map { row ->
                "${row.name}\n${row.sourceName} · ${stamp(row.updatedAt)}"
            }.toTypedArray()
            NxDialogBuilder(this@RawDevelopmentActivity).setTitle("RAW 配方")
                .setItems(labels) { _, which ->
                    val recipe = rows[which]
                    NxDialogBuilder(this@RawDevelopmentActivity).setTitle(recipe.name)
                        .setItems(arrayOf("载入", "删除")) { _, action ->
                            if (action == 0) loadRecipe(recipe) else deleteRecipe(recipe)
                        }.setNegativeButton("取消", null).show()
                }.setNegativeButton("取消", null).show()
        }
    }

    private fun loadRecipe(recipe: RawRecipe) {
        currentRecipeId = recipe.id
        currentRecipeName = recipe.name
        params = recipe.params
        pendingLutScope = recipe.lutScope
        pendingLutId = recipe.lutId
        pendingFilmId = recipe.filmId
        pendingFilmLabel = recipe.filmLabel
        filmStrength = recipe.filmStrength
        applyParamsToSliders()
        initializeFilmControls()
        lifecycleScope.launch {
            refreshLutChoices()
            val uri = Uri.parse(recipe.sourceUri)
            persistReadAccess(uri)
            openRaw(uri, recipe.sourceName)
        }
    }

    private fun deleteRecipe(recipe: RawRecipe) {
        NxDialogBuilder(this).setTitle("删除 ${recipe.name}？")
            .setMessage("只删除显影配方，不删除 RAW 或导出的 JPEG。")
            .setNegativeButton("取消", null)
            .setPositiveButton("删除") { _, _ ->
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { recipes.delete(recipe.id) }
                        if (currentRecipeId == recipe.id) {
                            currentRecipeId = null
                            currentRecipeName = null
                        }
                        imageStatus.text = "已删除配方 ${recipe.name}"
                    } catch (error: Throwable) {
                        imageStatus.text = "删除失败：${error.message ?: "请重试"}"
                    }
                }
            }.show()
    }

    private fun applyParamsToSliders() {
        updatingControls = true
        fun set(key: String, value: Float) {
            sliderBindings[key]?.let { binding ->
                binding.slider.value = value.coerceIn(binding.slider.valueFrom, binding.slider.valueTo)
                binding.label.text = binding.format(value)
            }
        }
        set("exposure", params.exposureEv)
        set("contrast", params.contrast.toFloat())
        set("highlights", params.highlights.toFloat())
        set("shadows", params.shadows.toFloat())
        set("temperature", params.temperature.toFloat())
        set("tint", params.tint.toFloat())
        set("saturation", params.saturation.toFloat())
        set("lutStrength", params.lutStrength.toFloat())
        set("filmStrength", filmStrength.toFloat())
        updatingControls = false
    }

    private fun permissionsRead(uri: Uri): Boolean = contentResolver.persistedUriPermissions.any {
        it.uri == uri && it.isReadPermission
    }

    private fun persistReadAccess(uri: Uri) {
        if (permissionsRead(uri)) return
        runCatching { contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
    }

    private fun displayName(uri: Uri, fallback: String): String {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: fallback
        fun query(): String? = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        }
        runCatching { query() }.getOrNull()?.takeIf { it.isNotBlank() }?.let { name = it }
        return name
    }

    private fun setOutputEnabled(enabled: Boolean) {
        saveRecipeButton.isEnabled = enabled
        exportButton.isEnabled = enabled
    }

    private fun materialButton(label: String, enabled: Boolean = true, action: () -> Unit) =
        MaterialButton(this).apply {
            text = label
            isAllCaps = false
            isEnabled = enabled
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { action() }
        }

    private fun weightedButtonParams(startMargin: Int = 0, endMargin: Int = 0) =
        LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginStart = startMargin
            marginEnd = endMargin
        }

    private fun color(id: Int) = ContextCompat.getColor(this, id)

    private fun signedPercent(value: Float): String {
        val rounded = value.toInt()
        return if (rounded > 0) "+$rounded" else rounded.toString()
    }

    private fun shutterText(seconds: Float): String =
        if (seconds in 0.0001f..0.5f) "1/${(1f / seconds).toInt()}" else
            String.format(Locale.ROOT, "%.1f s", seconds)

    private fun trimDecimal(value: Float): String = String.format(Locale.ROOT, "%.1f", value)
        .removeSuffix(".0")

    private fun safeOutputName(source: String): String {
        val base = source.substringBeforeLast('.').replace(Regex("[\\\\/:*?\"<>|]"), "_").take(96)
        return "${base.ifBlank { "NX300M" }}_NXDevelop.jpg"
    }

    private fun stamp(value: Long): String =
        SimpleDateFormat("MM-dd HH:mm", Locale.ROOT).format(Date(value))

    private fun isMemoryFailure(error: Throwable): Boolean {
        var current: Throwable? = error
        while (current != null) {
            if (current is OutOfMemoryError || (current is RawDecodeException && current.resultCode == -100007)) return true
            current = current.cause
        }
        return false
    }

    companion object {
        private const val PREVIEW_MAX_DIMENSION = 1920
        private const val RENDER_DEBOUNCE_MS = 180L
        private const val FILM_CATEGORY_ALL = "全部"
        private const val FILM_STRENGTH_DEFAULT = 100
    }
}
