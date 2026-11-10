package dev.cxo.filmpack.app.post

import android.app.AlertDialog
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.activity.ComponentActivity
import dev.cxo.filmpack.app.ui.*
import dev.cxo.filmpack.core.ToneCurvePreset
import kotlin.math.roundToInt

/** Transactional editor: only Apply publishes changes; all other exits discard them. */
fun createPostSettingsDialog(
    activity: ComponentActivity,
    current: PostEditingSettings,
    section: String,
    environment: CatalogGlassEnvironment,
    onApply: (PostEditingSettings) -> Unit,
): AlertDialog {
    fun dp(value: Int) = (value * activity.resources.displayMetrics.density + .5f).toInt()
    fun text(value: String, size: Float = 13f, muted: Boolean = false) = TextView(activity).apply {
        text = value; textSize = size; setTextColor(if (muted) EditorPalette.muted else EditorPalette.text)
        setLineSpacing(dp(3).toFloat(), 1f)
    }
    var development = current.development
    var edge = current.edge
    val resetters = mutableListOf<() -> Unit>()
    val titles = mapOf(
        "light" to "亮度", "color" to "颜色", "luma" to "亮度范围",
        "contrast" to "对比度", "wb" to "白平衡",
        "hsl" to "色相 · 饱和度 · 明度", "curve" to "色调曲线", "edge" to "边缘缺陷")
    val panel = LinearLayout(activity).apply {
        orientation = LinearLayout.VERTICAL; tag = "post_settings_content"
        clipChildren = false; clipToPadding = false
        isFocusable = true; isFocusableInTouchMode = true; defaultFocusHighlightEnabled = false
        setPadding(dp(16), dp(8), dp(16), dp(12))
    }
    panel.addView(text(titles[section] ?: "后处理", 21f).apply {
        tag = "post_title"; setPadding(dp(6), dp(4), dp(6), dp(12))
    })
    panel.addView(text(if (section == "edge") "漏光与光斑 · 强度、位置与随机变化"
        else "CXO 冲洗首版 · 参数与原版数值不等值", 11f, true).apply {
        setPadding(dp(6), 0, dp(6), dp(12))
    })
    fun slider(title: String, key: String, minimum: Int, maximum: Int, initial: Int, default: Int = 0,
               format: (Int) -> String = { it.toString() }, change: (Int) -> Unit) {
        val label = text("$title  ${format(initial)}", muted = true).apply {
            tag = "post_${key}_value"; setPadding(dp(10), dp(8), dp(10), 0)
        }
        val bar = CatalogGlassSlider(activity, environment).apply {
            tag = "post_$key"; max = maximum - minimum; progress = initial - minimum
            contentDescription = "$title，$minimum 到 $maximum"
            setOnSeekBarChangeListener(object : CatalogGlassSlider.OnSeekBarChangeListener {
                override fun onProgressChanged(bar: CatalogGlassSlider?, value: Int, fromUser: Boolean) {
                    val actual = value + minimum
                    label.text = "$title  ${format(actual)}"; change(actual)
                }
                override fun onStartTrackingTouch(bar: CatalogGlassSlider?) = Unit
                override fun onStopTrackingTouch(bar: CatalogGlassSlider?) = Unit
            })
        }
        panel.addView(label)
        panel.addView(bar, LinearLayout.LayoutParams(-1, dp(68)))
        resetters += { bar.progress = default - minimum; change(default); label.text = "$title  ${format(default)}" }
    }
    when (section) {
        "light" -> {
            slider("曝光", "exposure", -300, 300, (development.exposureEv * 100).roundToInt(),
                format = { String.format(java.util.Locale.ROOT, "%+.2f EV", it / 100f) }) { development = development.copy(exposureEv = it / 100f) }
            slider("亮度", "brightness", -100, 100, development.brightness) { development = development.copy(brightness = it) }
        }
        "color" -> {
            slider("自然饱和度", "vibrance", -100, 100, development.vibrance) { development = development.copy(vibrance = it) }
            slider("饱和度", "saturation", -100, 100, development.saturation) { development = development.copy(saturation = it) }
        }
        "luma" -> {
            panel.addView(text("按亮度范围调整；本版不含原版局部蒙版工具。", 11f, true).apply { setPadding(dp(10), 0, dp(10), dp(8)) })
            slider("高光", "highlights", -100, 100, development.highlights) { development = development.copy(highlights = it) }
            slider("阴影", "shadows", -100, 100, development.shadows) { development = development.copy(shadows = it) }
            slider("白色色阶", "whites", -100, 100, development.whites) { development = development.copy(whites = it) }
            slider("黑色色阶", "blacks", -100, 100, development.blacks) { development = development.copy(blacks = it) }
        }
        "contrast" -> slider("对比度", "contrast", -100, 100, development.contrast) { development = development.copy(contrast = it) }
        "wb" -> {
            panel.addView(text("相对冷暖调整，适用于 RAW 与已显影图片。", 11f, true).apply { setPadding(dp(10), 0, dp(10), dp(8)) })
            slider("色温 · 冷／暖", "temperature", -100, 100, development.temperature) { development = development.copy(temperature = it) }
            slider("色调 · 绿／洋红", "tint", -100, 100, development.tint) { development = development.copy(tint = it) }
        }
        "hsl" -> {
            slider("色相", "hue", -180, 180, development.hue, format = { "$it°" }) { development = development.copy(hue = it) }
            slider("饱和度", "saturation", -100, 100, development.saturation) { development = development.copy(saturation = it) }
            slider("明度", "brightness", -100, 100, development.brightness) { development = development.copy(brightness = it) }
        }
        "curve" -> {
            panel.addView(text("CXO 曲线预设", muted = true).apply { setPadding(dp(10), dp(8), dp(10), dp(8)) })
            val presets = ToneCurvePreset.entries
            val spinner = Spinner(activity).apply {
                tag = "post_curve"; contentDescription = "色调曲线预设"
                adapter = ArrayAdapter(activity, android.R.layout.simple_spinner_dropdown_item, listOf("线性", "柔和对比", "强对比", "哑光"))
                setSelection(presets.indexOf(development.curve))
                onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) { development = development.copy(curve = presets[position]) }
                    override fun onNothingSelected(parent: AdapterView<*>?) = Unit
                }
            }
            panel.addView(spinner, LinearLayout.LayoutParams(-1, dp(56)))
            resetters += { spinner.setSelection(0); development = development.copy(curve = ToneCurvePreset.LINEAR) }
        }
        "edge" -> {
            val choices = EdgeDefects.catalog(activity)
            val ids = listOf("") + choices.map { it.id }
            val names = listOf("关闭边缘缺陷") + choices.map { it.name }
            panel.addView(text("效果", muted = true).apply { setPadding(dp(10), dp(8), dp(10), dp(8)) })
            val source = Spinner(activity).apply {
                tag = "post_edge_source"; contentDescription = "边缘缺陷效果"
                adapter = ArrayAdapter(activity, android.R.layout.simple_spinner_dropdown_item, names)
                setSelection(ids.indexOf(edge.id).coerceAtLeast(0))
                onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) { edge = edge.copy(id = ids[position]) }
                    override fun onNothingSelected(parent: AdapterView<*>?) = Unit
                }
            }
            panel.addView(source, LinearLayout.LayoutParams(-1, dp(56)))
            slider("强度", "edge_intensity", 0, 100, edge.intensity, default = 50) { edge = edge.copy(intensity = it) }
            panel.addView(text("位置", muted = true).apply { setPadding(dp(10), dp(8), dp(10), dp(8)) })
            val positions = EdgePosition.entries
            val position = Spinner(activity).apply {
                tag = "post_edge_position"; contentDescription = "边缘缺陷位置"
                adapter = ArrayAdapter(activity, android.R.layout.simple_spinner_dropdown_item, listOf("随机位置", "左侧", "顶部", "右侧", "底部"))
                setSelection(positions.indexOf(edge.position))
                onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                    override fun onItemSelected(parent: AdapterView<*>?, view: View?, index: Int, id: Long) { edge = edge.copy(position = positions[index]) }
                    override fun onNothingSelected(parent: AdapterView<*>?) = Unit
                }
            }
            panel.addView(position, LinearLayout.LayoutParams(-1, dp(56)))
            val random = CatalogGlassButton(activity, environment).apply {
                text = "重新随机"; tag = "post_edge_random"
                setOnClickListener {
                    edge = edge.copy(seed = java.security.SecureRandom().nextLong())
                    text = "已重新随机"
                }
            }
            panel.addView(random, LinearLayout.LayoutParams(-1, dp(64)))
            resetters += { source.setSelection(0); position.setSelection(0); edge = EdgeDefectSettings(); random.text = "重新随机" }
        }
    }
    panel.addView(CatalogGlassButton(activity, environment).apply {
        text = "重置本组"; tag = "post_group_reset"
        setOnClickListener { resetters.forEach { it() } }
    }, LinearLayout.LayoutParams(-1, dp(64)))
    val scroll = SliderAxisScrollView(activity).apply {
        tag = "post_settings_scroll"; isVerticalScrollBarEnabled = false
        clipChildren = true; clipToPadding = true; defaultFocusHighlightEnabled = false
        addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ -> view.clipBounds = android.graphics.Rect(0, 0, view.width, view.height) }
        addView(panel, FrameLayout.LayoutParams(-1, -2))
    }
    lateinit var dialog: AlertDialog
    val footer = LinearLayout(activity).apply {
        gravity = Gravity.END or Gravity.CENTER_VERTICAL; clipChildren = false; clipToPadding = false
        setPadding(dp(8), dp(4), dp(8), dp(4))
        addView(CatalogGlassButton(activity, environment).apply {
            text = "取消"; tag = "post_cancel"; setOnClickListener { dialog.dismiss() }
        }, LinearLayout.LayoutParams(0, dp(64), 1f))
        addView(CatalogGlassButton(activity, environment, true).apply {
            text = "应用"; tag = "post_apply"; setOnClickListener {
                onApply(PostEditingSettings(development, edge)); dialog.dismiss()
            }
        }, LinearLayout.LayoutParams(0, dp(64), 1f))
    }
    val content = LinearLayout(activity).apply {
        orientation = LinearLayout.VERTICAL; clipChildren = false; clipToPadding = false
        addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        addView(footer, LinearLayout.LayoutParams(-1, dp(72)))
    }
    panel.requestFocus()
    dialog = AlertDialog.Builder(activity).setView(catalogSheet(activity, environment, content)).create()
    dialog.setOnShowListener { panel.requestFocus(); scroll.post { scroll.scrollTo(0, 0) } }
    return dialog
}
