// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Context
import android.content.res.Configuration
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.Window
import android.view.WindowManager
import android.widget.DatePicker
import android.widget.HorizontalScrollView
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.TimePicker
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDialog
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.exifinterface.media.ExifInterface
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.nxwa.nxconnect.R
import org.json.JSONObject
import java.math.RoundingMode
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/** Full-page editor; saving only returns metadata to the caller's existing copy/write flow. */
internal object PhotoMetadataEditor {
    private data class Field(val tag: String, val input: TextInputEditText, val wrapper: TextInputLayout)

    fun show(
        activity: AppCompatActivity,
        count: Int,
        initialMake: String,
        initialModel: String,
        initialFocal: String,
        initialAperture: String,
        initialMetadata: String?,
        displayName: String?,
        onDismiss: (() -> Unit)?,
        apply: (String) -> Unit,
    ) {
        val initial = initialMetadata?.let(::JSONObject)
        val prefs = activity.getSharedPreferences("lens_metadata", Context.MODE_PRIVATE)
        fun dp(v: Int) = (v * activity.resources.displayMetrics.density + .5f).toInt()
        fun color(id: Int) = activity.getColor(id)
        fun label(value: String, size: Float = 16f, bold: Boolean = false) = TextView(activity).apply {
            text = value
            textSize = size
            setTextColor(color(R.color.nx_on_surface))
            if (bold) typeface = Typeface.create("sans-serif", Typeface.BOLD)
        }
        fun row(icon: Int, content: View) = LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(10), 0, dp(10))
            addView(ImageView(activity).apply {
                setImageResource(icon)
                imageTintList = android.content.res.ColorStateList.valueOf(color(R.color.nx_on_surface_variant))
                importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
            }, LinearLayout.LayoutParams(dp(24), dp(24)).apply { marginEnd = dp(22) })
            addView(content, if (content is TextView) LinearLayout.LayoutParams(-2, -2) else LinearLayout.LayoutParams(0, -2, 1f))
        }

        val dialog = AppCompatDialog(activity, R.style.Theme_NXConnect)
        dialog.supportRequestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.setCanceledOnTouchOutside(false)
        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(color(R.color.oneui_viewer_background))
            isFocusableInTouchMode = true
        }
        val toolbar = LinearLayout(activity).apply {
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(12), 0, dp(24), 0)
        }
        val back = ImageButton(activity).apply {
            setImageResource(R.drawable.ic_gallery_back)
            imageTintList = android.content.res.ColorStateList.valueOf(color(R.color.nx_on_surface))
            setBackgroundResource(R.drawable.bg_gallery_touch)
            contentDescription = "返回"
            setPadding(dp(12), dp(12), dp(12), dp(12))
        }
        toolbar.addView(back, LinearLayout.LayoutParams(dp(48), dp(48)))
        toolbar.addView(label("详细信息", 24f, true), LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = dp(8) })
        root.addView(toolbar, LinearLayout.LayoutParams(-1, dp(64)))
        val body = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(28), dp(8), dp(28), dp(48))
        }
        val scroll = ScrollView(activity).apply {
            isFillViewport = true
            clipToPadding = false
            setPadding(0, 0, 0, dp(12))
            isVerticalScrollBarEnabled = false
            addView(body)
        }
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))

        val rawDate = initial?.optString(ExifInterface.TAG_DATETIME_ORIGINAL).orEmpty()
            .ifEmpty { initial?.optString(ExifInterface.TAG_DATETIME).orEmpty() }
        var dateValue = rawDate
        val dateFormat = SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).apply { isLenient = false }
        fun dateCalendar() = Calendar.getInstance().apply {
            if (dateValue.isNotEmpty()) runCatching { time = requireNotNull(dateFormat.parse(dateValue)) }
        }
        val dateButton = label("", 17f, true).apply {
            minHeight = dp(40)
            gravity = Gravity.CENTER_VERTICAL
            paintFlags = paintFlags or android.graphics.Paint.UNDERLINE_TEXT_FLAG
            isClickable = true
            isFocusable = true
            contentDescription = "修改拍摄日期和时间"
        }
        fun renderDate() {
            dateButton.text = if (dateValue.isEmpty()) "添加拍摄日期" else runCatching {
                SimpleDateFormat("yyyy年M月d日 HH:mm", Locale.SIMPLIFIED_CHINESE).format(requireNotNull(dateFormat.parse(dateValue)))
            }.getOrDefault(dateValue)
        }
        renderDate()
        if (initial != null) body.addView(row(R.drawable.ic_metadata_date, dateButton))
        if (!displayName.isNullOrBlank()) {
            body.addView(row(R.drawable.ic_oneui_gallery_tab_photo, LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL
                addView(label(displayName, 15f))
                addView(label("文件名", 12f).apply {
                    setTextColor(color(R.color.nx_on_surface_variant))
                    setPadding(0, dp(4), 0, 0)
                })
            }))
        }
        if (count > 1) body.addView(label("已选择 $count 张照片", 14f).apply { setPadding(0, dp(10), 0, dp(8)) })
        body.addView(label("镜头信息", 14f, true).apply {
            setTextColor(color(R.color.nx_on_surface_variant))
            setPadding(dp(46), dp(14), 0, dp(2))
        })
        val presetText = label("选择镜头", 15f).apply {
            minHeight = dp(40)
            gravity = Gravity.CENTER_VERTICAL
            paintFlags = paintFlags or android.graphics.Paint.UNDERLINE_TEXT_FLAG
        }
        val presetRow = row(R.drawable.ic_lens, presetText).apply {
            isClickable = true
            isFocusable = true
            setBackgroundResource(R.drawable.bg_gallery_touch)
            contentDescription = "选择镜头预设"
        }
        body.addView(presetRow)

        val fields = listOf(
            Triple(ExifInterface.TAG_LENS_MAKE, "镜头品牌", false),
            Triple(ExifInterface.TAG_LENS_MODEL, "镜头型号", false),
            Triple(ExifInterface.TAG_FOCAL_LENGTH, "实际焦距（mm）", true),
            Triple(ExifInterface.TAG_F_NUMBER, "拍摄光圈（f/）", true),
            Triple(ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM, "35 mm 等效焦距（mm）", true),
        ).map { (tag, title, numeric) ->
            val fallback = when (tag) {
                ExifInterface.TAG_LENS_MAKE -> initialMake
                ExifInterface.TAG_LENS_MODEL -> initialModel
                ExifInterface.TAG_FOCAL_LENGTH -> initialFocal
                ExifInterface.TAG_F_NUMBER -> initialAperture
                else -> ""
            }
            val raw = if (initial != null) initial.optString(tag, "") else fallback.ifEmpty { prefs.getString(tag, "").orEmpty() }
            val value = if (numeric && raw.contains('/')) runCatching {
                val parts = raw.split('/')
                parts[0].toBigDecimal().divide(parts[1].toBigDecimal(), 6, RoundingMode.HALF_UP).stripTrailingZeros().toPlainString()
            }.getOrDefault(raw) else raw
            val input = TextInputEditText(activity).apply {
                inputType = if (numeric) InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL else InputType.TYPE_CLASS_TEXT
                setSingleLine()
                textSize = 15f
                setTextColor(color(R.color.nx_on_surface))
                typeface = Typeface.create("sans-serif", Typeface.NORMAL)
                setBackgroundResource(R.drawable.bg_oneui_input_underline)
                backgroundTintList = null
                setPadding(0, dp(6), 0, dp(6))
                minHeight = dp(40)
                setText(value)
            }
            val wrapper = TextInputLayout(activity).apply {
                hint = title
                boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_NONE
                layoutParams = LinearLayout.LayoutParams(if (numeric) dp(160) else -1, -2).apply { marginStart = dp(46); topMargin = dp(6) }
                addView(input)
            }
            Field(tag, input, wrapper)
        }
        val byTag = fields.associateBy { it.tag }
        val focal = byTag.getValue(ExifInterface.TAG_FOCAL_LENGTH)
        val aperture = byTag.getValue(ExifInterface.TAG_F_NUMBER)
        val equivalent = byTag.getValue(ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM)
        fields.forEach { field ->
            body.addView(field.wrapper)
            if (field === aperture) {
                val pills = LinearLayout(activity).apply { setPadding(0, dp(4), 0, dp(2)) }
                listOf("0.95", "1.2", "1.4", "1.8", "2", "2.8", "4", "5.6", "8", "11", "16").forEach { value ->
                    pills.addView(label("f/$value", 13f).apply {
                        gravity = Gravity.CENTER
                        minHeight = dp(40)
                        setPadding(dp(12), 0, dp(12), 0)
                        setBackgroundResource(R.drawable.bg_capsule_pill)
                        isClickable = true
                        isFocusable = true
                        setOnClickListener { aperture.input.setText(value) }
                    }, LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(6) })
                }
                body.addView(HorizontalScrollView(activity).apply {
                    isHorizontalScrollBarEnabled = false
                    layoutParams = LinearLayout.LayoutParams(-1, -2).apply { marginStart = dp(46) }
                    addView(pills)
                })
            }
        }
        val crop = TextInputEditText(activity).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            setSingleLine()
            textSize = 15f
            setTextColor(color(R.color.nx_on_surface))
            setBackgroundResource(R.drawable.bg_oneui_input_underline)
            backgroundTintList = null
            setText("1.538")
        }
        val cropWrapper = TextInputLayout(activity).apply {
            hint = "画幅系数（APS-C）"
            boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_NONE
            layoutParams = LinearLayout.LayoutParams(dp(160), -2).apply { marginStart = dp(46); topMargin = dp(6) }
            addView(crop)
        }
        body.addView(cropWrapper)
        body.addView(label(if (initial != null) "下载或分享 JPEG／SRW 时将修改写入副本。" else "保存为新的 JPEG／SRW 副本。", 12f).apply {
            setTextColor(color(R.color.nx_on_surface_variant))
            setPadding(dp(46), dp(16), 0, dp(8))
        })

        val footer = LinearLayout(activity).apply {
            gravity = Gravity.CENTER
            // Elevated children draw into the padding; clipping it cuts the shadow into a rectangle.
            clipChildren = false
            clipToPadding = false
            setPadding(dp(24), dp(16), dp(24), dp(24))
        }
        val dock = LinearLayout(activity).apply {
            gravity = Gravity.CENTER_VERTICAL
            background = GradientDrawable().apply { setColor(color(R.color.nx_surface)); cornerRadius = dp(36).toFloat() }
            outlineProvider = android.view.ViewOutlineProvider.BACKGROUND
            clipToOutline = true
            elevation = dp(3).toFloat()
            setPadding(dp(12), dp(4), dp(12), dp(4))
        }
        fun action(text: String) = label(text, 18f, true).apply {
            gravity = Gravity.CENTER
            minHeight = dp(52)
            isClickable = true
            isFocusable = true
            setBackgroundResource(R.drawable.bg_gallery_touch)
        }
        val cancel = action("取消")
        val save = action("保存")
        dock.addView(cancel, LinearLayout.LayoutParams(0, -2, 1f))
        dock.addView(View(activity).apply { setBackgroundColor(color(R.color.nx_outline_variant)) }, LinearLayout.LayoutParams(dp(1), dp(20)))
        dock.addView(save, LinearLayout.LayoutParams(0, -2, 1f))
        footer.addView(dock, LinearLayout.LayoutParams(dp(244), -2))
        root.addView(footer, LinearLayout.LayoutParams(-1, -2))

        val opening = fields.associate { it.tag to it.input.text.toString().trim() }
        fun dirty() = dateValue != rawDate || fields.any { it.input.text.toString().trim() != opening[it.tag] }
        fun updateSave() {
            save.isEnabled = if (initial != null) dirty() else fields.any { it.input.text?.isNotBlank() == true }
            save.alpha = if (save.isEnabled) 1f else .35f
        }
        fun watcher(changed: () -> Unit) = object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = changed()
            override fun afterTextChanged(s: Editable?) {}
        }
        fun calculateEquivalent() {
            val mm = focal.input.text.toString().toDoubleOrNull()
            val factor = crop.text.toString().toDoubleOrNull()
            equivalent.input.setText(if (mm != null && mm.isFinite() && mm > 0 && factor != null && factor.isFinite() && factor > 0 && factor <= 10) {
                kotlin.math.round(mm * factor).toLong().toString()
            } else "")
        }
        fields.forEach { it.input.addTextChangedListener(watcher { updateSave() }) }
        focal.input.addTextChangedListener(watcher { calculateEquivalent() })
        crop.addTextChangedListener(watcher { calculateEquivalent() })
        var selectedPreset: LensPreset? = null
        presetRow.setOnClickListener {
            LensPresets.choose(activity) { preset ->
                selectedPreset = preset
                presetText.text = preset.model
                byTag.getValue(ExifInterface.TAG_LENS_MAKE).input.setText(preset.make)
                byTag.getValue(ExifInterface.TAG_LENS_MODEL).input.setText(preset.model)
                focal.input.setText(if (preset.fixed) preset.min.toBigDecimal().stripTrailingZeros().toPlainString() else "")
                focal.wrapper.helperText = if (preset.fixed) null else "变焦范围 ${preset.min.toInt()}–${preset.max.toInt()} mm"
            }
        }
        dateButton.setOnClickListener {
            val calendar = dateCalendar()
            val picker = DatePicker(activity).apply { init(calendar.get(Calendar.YEAR), calendar.get(Calendar.MONTH), calendar.get(Calendar.DAY_OF_MONTH), null) }
            NxDialogBuilder(activity).setTitle("拍摄日期").setView(picker)
                .setNegativeButton("取消", null).setPositiveButton("下一步") { _, _ ->
                    val time = TimePicker(activity).apply { setIs24HourView(true); hour = calendar.get(Calendar.HOUR_OF_DAY); minute = calendar.get(Calendar.MINUTE) }
                    NxDialogBuilder(activity).setTitle("拍摄时间").setView(time)
                        .setNegativeButton("取消", null).setPositiveButton("完成") { _, _ ->
                            calendar.set(picker.year, picker.month, picker.dayOfMonth, time.hour, time.minute)
                            dateValue = dateFormat.format(calendar.time)
                            renderDate()
                            updateSave()
                        }.show()
                }.show()
        }
        fun close() {
            if (!dirty()) dialog.dismiss() else NxDialogBuilder(activity).setTitle("放弃修改？")
                .setMessage("尚未保存的修改将丢失。")
                .setNegativeButton("继续编辑", null).setPositiveButton("放弃") { _, _ -> dialog.dismiss() }.show()
        }
        cancel.setOnClickListener { close() }
        back.setOnClickListener { close() }
        dialog.onBackPressedDispatcher.addCallback(object : androidx.activity.OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = close()
        })
        dialog.setOnKeyListener { _, code, event ->
            if (code == KeyEvent.KEYCODE_BACK) {
                if (event.action == KeyEvent.ACTION_UP && !event.isCanceled) close()
                true
            } else false
        }
        var submitted = false
        save.setOnClickListener {
            if (submitted) return@setOnClickListener
            val data = JSONObject()
            var valid = true
            fields.forEach { field ->
                field.wrapper.error = null
                val value = field.input.text.toString().trim()
                if (initial != null && value == opening[field.tag]) return@forEach
                if (value.isEmpty()) {
                    if (initial != null) data.put(field.tag, JSONObject.NULL)
                    return@forEach
                }
                val numeric = field.tag != ExifInterface.TAG_LENS_MAKE && field.tag != ExifInterface.TAG_LENS_MODEL
                val number = value.toDoubleOrNull()
                if (numeric && (number == null || !number.isFinite() || number <= 0 || number > 65535 ||
                        (field.tag == ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM && number % 1.0 != 0.0))) {
                    field.wrapper.error = "请输入有效正数"
                    valid = false
                }
                data.put(field.tag, value)
            }
            val factor = crop.text.toString().toDoubleOrNull()
            cropWrapper.error = if (factor == null || !factor.isFinite() || factor <= 0 || factor > 10) "请输入有效画幅系数" else null
            if (cropWrapper.error != null) valid = false
            selectedPreset?.let { preset ->
                focal.input.text.toString().toDoubleOrNull()?.let { mm ->
                    if (mm < preset.min || mm > preset.max) {
                        focal.wrapper.error = "焦距应在 ${preset.min}–${preset.max} mm 内"
                        valid = false
                    }
                }
            }
            if (dateValue != rawDate) data.put(ExifInterface.TAG_DATETIME_ORIGINAL, dateValue)
            if (!valid || data.length() == 0) return@setOnClickListener
            submitted = true
            prefs.edit().apply { fields.forEach { putString(it.tag, it.input.text.toString().trim()) } }.apply()
            dialog.dismiss()
            apply(data.toString())
        }
        val closeWithActivity = object : androidx.lifecycle.DefaultLifecycleObserver {
            override fun onDestroy(owner: androidx.lifecycle.LifecycleOwner) { dialog.dismiss() }
        }
        activity.lifecycle.addObserver(closeWithActivity)
        dialog.setOnDismissListener {
            activity.lifecycle.removeObserver(closeWithActivity)
            onDismiss?.invoke()
        }
        dialog.setContentView(root)
        dialog.window?.apply {
            setBackgroundDrawableResource(android.R.color.transparent)
            setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
            WindowCompat.setDecorFitsSystemWindows(this, false)
            val light = activity.resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK != Configuration.UI_MODE_NIGHT_YES
            WindowCompat.getInsetsController(this, decorView).apply {
                isAppearanceLightStatusBars = light
                isAppearanceLightNavigationBars = light
            }
        }
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            insets
        }
        updateSave()
        dialog.show()
        dialog.window?.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        ViewCompat.requestApplyInsets(root)
    }
}
