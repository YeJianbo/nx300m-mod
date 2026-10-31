package com.nxwa.nxconnect.ui

import android.content.Context
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.LinearLayout
import android.widget.ScrollView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import com.nxwa.nxconnect.R
import org.json.JSONArray
import org.json.JSONObject

data class LensPreset(
    val id: String,
    val make: String,
    val model: String,
    val min: Double,
    val max: Double,
    val defaultAperture: Double? = null,
    val category: String = "手动镜头"
) {
    val fixed get() = min == max
}

object LensPresets {
    private val builtins = listOf(
        LensPreset("pentax-m-50-17", "PENTAX", "smc PENTAX-M 50mm F1.7", 50.0, 50.0, 1.7, "宾得 / 太苦玛"),
        LensPreset("pentax-f-70-200", "PENTAX", "PENTAX-F 70-200mm F4-5.6", 70.0, 200.0, 4.0, "宾得 / 太苦玛"),
        LensPreset("pentax-fa-100-300", "PENTAX", "smc PENTAX-FA 100-300mm F4.7-5.8", 100.0, 300.0, 4.7, "宾得 / 太苦玛"),
        LensPreset("pentax-takumar-55-18", "Asahi Optical Co.", "Super-Takumar 55mm F1.8", 55.0, 55.0, 1.8, "宾得 / 太苦玛"),
        // 蔡司 / 康泰时 C/Y
        LensPreset("contax-planar-50-14", "Carl Zeiss", "Contax Carl Zeiss Planar 50mm F1.4 T*", 50.0, 50.0, 1.4, "蔡司 / 康泰时"),
        LensPreset("contax-distagon-28-28", "Carl Zeiss", "Contax Carl Zeiss Distagon 28mm F2.8 T*", 28.0, 28.0, 2.8, "蔡司 / 康泰时"),
        LensPreset("contax-sonnar-85-28", "Carl Zeiss", "Contax Carl Zeiss Sonnar 85mm F2.8 T*", 85.0, 85.0, 2.8, "蔡司 / 康泰时"),
        LensPreset("czj-tessar-50-28", "Carl Zeiss Jena", "Carl Zeiss Jena Tessar 50mm F2.8", 50.0, 50.0, 2.8, "蔡司 / 康泰时"),
        LensPreset("czj-flektogon-35-24", "Carl Zeiss Jena", "Carl Zeiss Jena Flektogon 35mm F2.4", 35.0, 35.0, 2.4, "蔡司 / 康泰时"),

        // 徕卡 M / R
        LensPreset("leica-summicron-m-35", "Leica", "Leica Summicron-M 35mm F2 ASPH", 35.0, 35.0, 2.0, "徕卡 M / R"),
        LensPreset("leica-summicron-r-50", "Leica", "Leica Summicron-R 50mm F2", 50.0, 50.0, 2.0, "徕卡 M / R"),
        LensPreset("leica-elmarit-r-90", "Leica", "Leica Elmarit-R 90mm F2.8", 90.0, 90.0, 2.8, "徕卡 M / R"),

        // 日系经典老镜头 (FD / MD / AI-S / OM / PK / M42)
        LensPreset("canon-fd-50-14", "Canon", "Canon FD 50mm F1.4 S.S.C.", 50.0, 50.0, 1.4, "经典日系单反老镜"),
        LensPreset("canon-fd-35-20", "Canon", "Canon FD 35mm F2 Concave", 35.0, 35.0, 2.0, "经典日系单反老镜"),
        LensPreset("minolta-md-50-14", "Minolta", "Minolta MD Rokkor 50mm F1.4", 50.0, 50.0, 1.4, "经典日系单反老镜"),
        LensPreset("nikon-ais-50-14", "Nikon", "Nikon AI-S Nikkor 50mm F1.4", 50.0, 50.0, 1.4, "经典日系单反老镜"),
        LensPreset("olympus-om-50-18", "Olympus", "Olympus OM-System F.Zuiko 50mm F1.8", 50.0, 50.0, 1.8, "经典日系单反老镜"),
        LensPreset("helios-44-2", "KMZ", "Helios-44-2 58mm F2 八羽怪", 58.0, 58.0, 2.0, "经典日系单反老镜"),

        // 国产 / 现代手动大光圈镜头
        LensPreset("ttartisan-35-14", "TTArtisan", "TTArtisan 35mm F1.4", 35.0, 35.0, 1.4, "现代手动大光圈"),
        LensPreset("ttartisan-50-12", "TTArtisan", "TTArtisan 50mm F1.2", 50.0, 50.0, 1.2, "现代手动大光圈"),
        LensPreset("7artisans-35-12", "7Artisans", "7Artisans 35mm F1.2 II", 35.0, 35.0, 1.2, "现代手动大光圈"),
        LensPreset("7artisans-50-095", "7Artisans", "7Artisans 50mm F0.95", 50.0, 50.0, 0.95, "现代手动大光圈"),
        LensPreset("brightinstar-50-095", "Brightin Star", "Brightin Star 50mm F0.95 星耀", 50.0, 50.0, 0.95, "现代手动大光圈"),

        // 三星 NX 原厂电子镜头
        LensPreset("samsung-nx-30-20", "SAMSUNG", "SAMSUNG NX 30mm F2.0 Pancake", 30.0, 30.0, 2.0, "三星 NX 原厂镜头"),
        LensPreset("samsung-nx-45-18", "SAMSUNG", "SAMSUNG NX 45mm F1.8 2D/3D", 45.0, 45.0, 1.8, "三星 NX 原厂镜头"),
        LensPreset("samsung-nx-16-24", "SAMSUNG", "SAMSUNG NX 16mm F2.4 Pancake", 16.0, 16.0, 2.4, "三星 NX 原厂镜头"),
        LensPreset("samsung-nx-20-28", "SAMSUNG", "SAMSUNG NX 20mm F2.8 Pancake", 20.0, 20.0, 2.8, "三星 NX 原厂镜头"),
        LensPreset("samsung-nx-85-14", "SAMSUNG", "SAMSUNG NX 85mm F1.4 ED SSA", 85.0, 85.0, 1.4, "三星 NX 原厂镜头"),
        LensPreset("samsung-nx-18-55", "SAMSUNG", "SAMSUNG NX 18-55mm F3.5-5.6 OIS", 18.0, 55.0, 3.5, "三星 NX 原厂镜头"),
        LensPreset("samsung-nx-50-200", "SAMSUNG", "SAMSUNG NX 50-200mm F4-5.6 ED OIS", 50.0, 200.0, 4.0, "三星 NX 原厂镜头")
    )

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("lens_presets", 0)

    private fun custom(ctx: Context): List<LensPreset> = runCatching {
        val array = JSONArray(prefs(ctx).getString("custom", "[]"))
        (0 until array.length()).map { i ->
            val o = array.getJSONObject(i)
            LensPreset(
                o.getString("id"),
                o.getString("make"),
                o.getString("model"),
                o.getDouble("min"),
                o.getDouble("max"),
                o.optDouble("aperture", 0.0).takeIf { it > 0 },
                "自定义镜头"
            )
        }
    }.getOrDefault(emptyList())

    private fun save(ctx: Context, list: List<LensPreset>) {
        val array = JSONArray()
        list.forEach {
            val o = JSONObject().put("id", it.id).put("make", it.make).put("model", it.model)
                .put("min", it.min).put("max", it.max)
            it.defaultAperture?.let { ap -> o.put("aperture", ap) }
            array.put(o)
        }
        prefs(ctx).edit().putString("custom", array.toString()).apply()
    }

    fun choose(activity: AppCompatActivity, selected: (LensPreset) -> Unit) {
        val lenses = builtins + custom(activity)
        val categories = lenses.groupBy { it.category }
        val categoryNames = categories.keys.toList()
        val labels = categoryNames + listOf("添加自定义镜头", "管理自定义镜头")
        val adapter = object : ArrayAdapter<String>(activity, android.R.layout.simple_list_item_1, labels) {
            override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
                val row = super.getView(position, convertView, parent) as android.widget.TextView
                val icon = when (position) {
                    categoryNames.size -> R.drawable.ic_oneui_add
                    categoryNames.size + 1 -> R.drawable.ic_oneui_settings
                    else -> R.drawable.ic_lens
                }
                val drawable = ContextCompat.getDrawable(activity, icon)?.mutate()
                val size = (24 * activity.resources.displayMetrics.density).toInt()
                drawable?.setBounds(0, 0, size, size)
                drawable?.setTint(ContextCompat.getColor(activity, R.color.nx_on_surface))
                row.setCompoundDrawablesRelative(drawable, null, null, null)
                row.compoundDrawablePadding = (14 * activity.resources.displayMetrics.density).toInt()
                return row
            }
        }

        NxDialogBuilder(activity)
            .setTitle("选择镜头预设")
            .setAdapter(adapter) { _, index ->
                when {
                    index < categoryNames.size -> {
                        val cat = categoryNames[index]
                        val inCat = categories[cat].orEmpty()
                        NxDialogBuilder(activity)
                            .setTitle(cat)
                            .setItems(inCat.map { it.model }.toTypedArray()) { _, subIndex ->
                                selected(inCat[subIndex])
                            }
                            .setNegativeButton("返回") { _, _ -> choose(activity, selected) }
                            .show()
                    }
                    index == categoryNames.size -> edit(activity, null, selected)
                    else -> manage(activity, selected)
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun manage(activity: AppCompatActivity, selected: (LensPreset) -> Unit) {
        val lenses = custom(activity)
        if (lenses.isEmpty()) {
            edit(activity, null, selected)
            return
        }
        NxDialogBuilder(activity)
            .setTitle("自定义镜头管理")
            .setItems(lenses.map { it.model }.toTypedArray()) { _, index ->
                edit(activity, lenses[index], selected)
            }
            .setPositiveButton("添加新镜头") { _, _ -> edit(activity, null, selected) }
            .setNegativeButton("返回") { _, _ -> choose(activity, selected) }
            .show()
    }

    private fun edit(activity: AppCompatActivity, old: LensPreset?, selected: (LensPreset) -> Unit) {
        val box = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            val p = (24 * resources.displayMetrics.density).toInt()
            setPadding(p, (12 * resources.displayMetrics.density).toInt(), p, 0)
        }
        fun field(label: String, initial: String, number: Boolean = false): TextInputEditText {
            val input = TextInputEditText(activity).apply {
                setSingleLine()
                setText(initial)
                if (number) inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            }
            box.addView(TextInputLayout(activity).apply {
                hint = label
                boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_NONE
                layoutParams = LinearLayout.LayoutParams(-1, -2).apply {
                    topMargin = (10 * resources.displayMetrics.density).toInt()
                }
                addView(input)
            })
            return input
        }
        val make = field("镜头品牌 (如 Carl Zeiss / Canon)", old?.make.orEmpty())
        val model = field("镜头完整型号 (如 Planar 50mm F1.4 T*)", old?.model.orEmpty())
        val min = field("实际焦距 (mm)", old?.min?.toString().orEmpty(), true)
        val max = field("最长焦距 (定焦可留空)", old?.max?.toString().orEmpty(), true)
        val aperture = field("最大光圈 (如 1.4，可选)", old?.defaultAperture?.toString().orEmpty(), true)

        val builder = NxDialogBuilder(activity)
            .setTitle(if (old == null) "添加自定义镜头" else "编辑镜头预设")
            .setView(ScrollView(activity).apply { addView(box) })
            .setNegativeButton("取消", null)
            .setPositiveButton("保存并选用", null)

        if (old != null) {
            builder.setNeutralButton("删除此预设") { _, _ ->
                save(activity, custom(activity).filter { it.id != old.id })
            }
        }
        val dialog = builder.create()
        dialog.setOnShowListener {
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).setOnClickListener {
                val lower = min.text.toString().toDoubleOrNull()
                val upper = max.text.toString().toDoubleOrNull() ?: lower
                if (model.text.isNullOrBlank()) {
                    model.error = "请填写镜头型号"
                    return@setOnClickListener
                }
                if (lower == null || upper == null || !lower.isFinite() || !upper.isFinite() || lower <= 0 || upper < lower || upper > 10000) {
                    min.error = "请填写有效的焦距"
                    return@setOnClickListener
                }
                val ap = aperture.text.toString().toDoubleOrNull()?.takeIf { it > 0 }
                val lens = LensPreset(
                    old?.id ?: java.util.UUID.randomUUID().toString(),
                    make.text.toString().trim(),
                    model.text.toString().trim(),
                    lower,
                    upper,
                    ap,
                    "自定义镜头"
                )
                save(activity, custom(activity).filter { it.id != lens.id } + lens)
                dialog.dismiss()
                selected(lens)
            }
        }
        dialog.show()
    }
}
