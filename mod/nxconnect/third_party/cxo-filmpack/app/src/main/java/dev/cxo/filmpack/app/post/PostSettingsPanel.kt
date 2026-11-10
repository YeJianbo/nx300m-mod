package dev.cxo.filmpack.app.post

import android.view.Gravity
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.ComponentActivity
import dev.cxo.filmpack.app.ui.CatalogGlassButton
import dev.cxo.filmpack.app.ui.CatalogGlassEnvironment
import dev.cxo.filmpack.app.ui.CatalogGlassSlider
import dev.cxo.filmpack.app.ui.EditorPalette
import dev.cxo.filmpack.core.PostProcessSettings
import dev.cxo.filmpack.core.ToneCurvePreset
import java.util.Locale
import kotlin.math.roundToInt

/** Inline editor. The owner publishes each immutable change synchronously.
 * Only one section is open; reopening reads the current state rather than an old copy. */
fun createPostSettingsPanel(
    activity: ComponentActivity,
    current: () -> PostEditingSettings,
    environment: CatalogGlassEnvironment,
    onChange: (PostEditingSettings, Boolean) -> Unit,
): PostSettingsPanel = PostSettingsPanel(activity, current, environment, onChange)

class PostSettingsPanel internal constructor(
    private val activity: ComponentActivity,
    private val current: () -> PostEditingSettings,
    private val environment: CatalogGlassEnvironment,
    private val onChange: (PostEditingSettings, Boolean) -> Unit,
) : LinearLayout(activity) {
    private data class Section(val key: String, val title: String, val header: CatalogGlassButton, val body: LinearLayout)
    private val sections = mutableListOf<Section>()
    private var expanded: String? = null
    private var revision = 0

    init {
        orientation = VERTICAL
        tag = "post_settings_panel"
        clipChildren = false; clipToPadding = false
        listOf(
            "light" to "亮度", "color" to "颜色", "luma" to "亮度范围",
            "contrast" to "对比度", "wb" to "白平衡",
            "hsl" to "色相 · 饱和度 · 明度", "curve" to "色调曲线",
            "edge" to "边缘缺陷 · 漏光与光斑",
        ).forEach { (key, title) ->
            val header = CatalogGlassButton(activity, environment).apply {
                tag = "post_$key"
                setOnClickListener { if (this@PostSettingsPanel.isEnabled) toggle(key) }
            }
            val reset = CatalogGlassButton(activity, environment).apply {
                tag = "post_${key}_reset"; iconKind = "reset"
                contentDescription = "重置$title"
                setOnClickListener {
                    if (this@PostSettingsPanel.isEnabled) {
                        onChange(resetGroup(key), false)
                        refresh()
                    }
                }
            }
            addView(LinearLayout(activity).apply {
                gravity = Gravity.CENTER_VERTICAL; clipChildren = false; clipToPadding = false
                addView(header, LayoutParams(0, dp(64), 1f))
                addView(reset, LayoutParams(dp(64), dp(64)))
            }, LayoutParams(-1, dp(64)))
            val body = LinearLayout(activity).apply {
                orientation = VERTICAL; tag = "post_${key}_content"; visibility = GONE
                clipChildren = false; clipToPadding = false
                setPadding(dp(4), 0, dp(4), dp(8))
            }
            addView(body, LayoutParams(-1, -2))
            addView(View(activity).apply { setBackgroundColor(0x20D9B780) }, LayoutParams(-1, dp(1)))
            sections += Section(key, title, header, body)
        }
        refresh()
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density + .5f).toInt()
    private fun label(value: String, size: Float = 12f) = TextView(activity).apply {
        text = value; textSize = size; setTextColor(EditorPalette.muted)
        setPadding(dp(10), dp(6), dp(10), 0)
    }

    private fun toggle(key: String) {
        expanded = if (expanded == key) null else key
        refresh()
    }

    /** Call after an external reset or state replacement, never on each drag frame. */
    fun refresh() {
        revision++
        sections.forEach { section ->
            val open = section.key == expanded
            section.header.text = (if (open) "⌄  " else "›  ") + section.title
            section.header.contentDescription = section.title + if (open) "，已展开，点击收起" else "，已收起，点击展开"
            section.header.isSelected = open
            section.body.removeAllViews()
            section.body.visibility = if (open) VISIBLE else GONE
            if (open) build(section.key, section.body, revision)
        }
        requestLayout()
    }

    private fun edit(tracking: Boolean = false, transform: (PostEditingSettings) -> PostEditingSettings) {
        if (!isEnabled) return
        val previous = current()
        val next = transform(previous)
        if (next != previous) onChange(next, tracking)
    }

    private fun build(key: String, body: LinearLayout, version: Int) {
        val d = current().development
        fun slider(title: String, field: String, minimum: Int, maximum: Int, initial: Int,
                   format: (Int) -> String = { it.toString() }, change: (PostEditingSettings, Int) -> PostEditingSettings) {
            var tracking = false
            val value = label("$title  ${format(initial)}").apply { tag = "post_${field}_value" }
            val bar = CatalogGlassSlider(activity, environment).apply {
                tag = "post_$field"; max = maximum - minimum; progress = initial - minimum
                contentDescription = "$title，$minimum 到 $maximum"
                setOnSeekBarChangeListener(object : CatalogGlassSlider.OnSeekBarChangeListener {
                    override fun onProgressChanged(bar: CatalogGlassSlider?, progress: Int, fromUser: Boolean) {
                        if (version != revision) return
                        val actual = progress + minimum
                        value.text = "$title  ${format(actual)}"
                        edit(tracking) { change(it, actual) }
                    }
                    override fun onStartTrackingTouch(bar: CatalogGlassSlider?) { tracking = true }
                    override fun onStopTrackingTouch(bar: CatalogGlassSlider?) {
                        tracking = false
                        if (version == revision && this@PostSettingsPanel.isEnabled) onChange(current(), false)
                    }
                })
            }
            body.addView(value)
            body.addView(bar, LayoutParams(-1, dp(64)))
        }
        fun development(title: String, field: String, initial: Int, minimum: Int = -100, maximum: Int = 100,
                        format: (Int) -> String = { it.toString() }, change: (PostProcessSettings, Int) -> PostProcessSettings) {
            slider(title, field, minimum, maximum, initial, format) { s, v -> s.copy(development = change(s.development, v)) }
        }
        fun spinner(title: String, field: String, names: List<String>, selected: Int, change: (PostEditingSettings, Int) -> PostEditingSettings) {
            body.addView(label(title))
            body.addView(Spinner(activity).apply {
                tag = "post_$field"; contentDescription = title
                adapter = ArrayAdapter(activity, android.R.layout.simple_spinner_dropdown_item, names)
                setSelection(selected.coerceIn(names.indices))
                onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                        if (version == revision) edit { change(it, position) }
                    }
                    override fun onNothingSelected(parent: AdapterView<*>?) = Unit
                }
            }, LayoutParams(-1, dp(56)))
        }
        when (key) {
            "light" -> {
                development("曝光", "exposure", (d.exposureEv * 100).roundToInt(), -300, 300,
                    { String.format(Locale.ROOT, "%+.2f EV", it / 100f) }) { s, v -> s.copy(exposureEv = v / 100f) }
                development("亮度", "brightness", d.brightness) { s, v -> s.copy(brightness = v) }
            }
            "color" -> {
                development("自然饱和度", "vibrance", d.vibrance) { s, v -> s.copy(vibrance = v) }
                development("饱和度", "saturation", d.saturation) { s, v -> s.copy(saturation = v) }
            }
            "luma" -> {
                development("高光", "highlights", d.highlights) { s, v -> s.copy(highlights = v) }
                development("阴影", "shadows", d.shadows) { s, v -> s.copy(shadows = v) }
                development("白色色阶", "whites", d.whites) { s, v -> s.copy(whites = v) }
                development("黑色色阶", "blacks", d.blacks) { s, v -> s.copy(blacks = v) }
            }
            "contrast" -> development("对比度", "contrast_amount", d.contrast) { s, v -> s.copy(contrast = v) }
            "wb" -> {
                development("色温 · 冷／暖", "temperature", d.temperature) { s, v -> s.copy(temperature = v) }
                development("色调 · 绿／洋红", "tint", d.tint) { s, v -> s.copy(tint = v) }
            }
            "hsl" -> {
                development("色相", "hue", d.hue, -180, 180, { "$it°" }) { s, v -> s.copy(hue = v) }
                development("饱和度", "saturation", d.saturation) { s, v -> s.copy(saturation = v) }
                development("明度", "brightness", d.brightness) { s, v -> s.copy(brightness = v) }
            }
            "curve" -> spinner("曲线预设", "curve_preset", listOf("线性", "柔和对比", "强对比", "哑光"),
                ToneCurvePreset.entries.indexOf(d.curve)) { s, i -> s.copy(development = s.development.copy(curve = ToneCurvePreset.entries[i])) }
            "edge" -> {
                val e = current().edge
                val choices = EdgeDefects.catalog(activity)
                val ids = listOf("") + choices.map { it.id }
                spinner("效果", "edge_source", listOf("关闭边缘缺陷") + choices.map { it.name }, ids.indexOf(e.id).coerceAtLeast(0)) { s, i -> s.copy(edge = s.edge.copy(id = ids[i])) }
                slider("强度", "edge_intensity", 0, 100, e.intensity) { s, v -> s.copy(edge = s.edge.copy(intensity = v)) }
                val positions = EdgePosition.entries
                spinner("位置", "edge_position", listOf("随机位置", "左侧", "顶部", "右侧", "底部"), positions.indexOf(e.position)) { s, i -> s.copy(edge = s.edge.copy(position = positions[i])) }
                body.addView(CatalogGlassButton(activity, environment).apply {
                    text = "重新随机"; tag = "post_edge_random"
                    setOnClickListener {
                        edit { it.copy(edge = it.edge.copy(seed = java.security.SecureRandom().nextLong())) }
                        text = "已重新随机"
                    }
                }, LayoutParams(-1, dp(64)))
            }
        }
    }

    private fun resetGroup(key: String): PostEditingSettings {
        val s = current(); val d = s.development
        return when (key) {
            "light" -> s.copy(development = d.copy(exposureEv = 0f, brightness = 0))
            "color" -> s.copy(development = d.copy(vibrance = 0, saturation = 0))
            "luma" -> s.copy(development = d.copy(highlights = 0, shadows = 0, whites = 0, blacks = 0))
            "contrast" -> s.copy(development = d.copy(contrast = 0))
            "wb" -> s.copy(development = d.copy(temperature = 0, tint = 0))
            "hsl" -> s.copy(development = d.copy(hue = 0, saturation = 0, brightness = 0))
            "curve" -> s.copy(development = d.copy(curve = ToneCurvePreset.LINEAR))
            "edge" -> s.copy(edge = EdgeDefectSettings())
            else -> s
        }
    }
}
