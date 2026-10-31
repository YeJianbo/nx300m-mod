// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.net.ExposureControls
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.net.NxException
import com.nxwa.nxconnect.net.RecipeControls
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** All selectors read the current camera first; a failed write invalidates the dialog snapshot. */
class ExposureSettingsDialog(
    private val activity: AppCompatActivity,
    private val camera: NxCamera,
    private val openStockIso: () -> Unit,
) {
    private enum class Group { ISO, SHUTTER, DIAL, RGB, PW, LOOKS, RECIPE }
    private enum class Field(val title: String, val wire: String, val group: Group) {
        STEP("ISO 步长", "iso_step", Group.ISO), EXPANSION("扩展 ISO", "iso_expansion", Group.ISO),
        MAXIMUM("Auto ISO 上限", "auto_iso_max", Group.ISO), FASTEST("自动曝光最快快门", "fastest", Group.SHUTTER),
        DIAL("指令拨盘锁", "enabled", Group.DIAL),
        RED("RGB / R 红增益", "red", Group.RGB), GREEN("RGB / G 绿增益", "green", Group.RGB),
        BLUE("RGB / B 蓝增益", "blue", Group.RGB),
        RECIPE("七维配方", "id", Group.RECIPE),
        SATURATION("原厂饱和度", "saturation", Group.PW), SHARPNESS("原厂锐度", "sharpness", Group.PW),
        CONTRAST("原厂对比度", "contrast", Group.PW), TONE("原厂色彩角度", "color_tone", Group.PW),
        LOOK("LUT 风格", "id", Group.LOOKS)
    }
    private data class LookChoice(val id: String?, val label: String)

    private val values = mutableMapOf<Field, Any>()
    private val buttons = linkedMapOf<Field, Button>()
    private var looksState: ExposureControls.LooksState? = null
    private var recipeCatalogue: RecipeControls.Catalogue? = null
    private var recipeState: RecipeControls.State? = null
    private var recipeSnapshotConfirmed = false
    private var recipeIssue = ""
    private lateinit var status: TextView
    private lateinit var rgbDetail: TextView
    private lateinit var pwDetail: TextView
    private lateinit var looksDetail: TextView
    private lateinit var recipeDetail: TextView
    private lateinit var resetRgb: Button
    private lateinit var resetRecipe: Button
    private lateinit var retry: Button
    private lateinit var dialog: AlertDialog
    private var busy = false
    private var job: Job? = null
    private var recipePoll: Job? = null

    fun show() {
        val panel = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            val padding = (20 * resources.displayMetrics.density).toInt()
            setPadding(padding, padding / 2, padding, padding / 2)
        }
        status = TextView(activity).apply { text = "正在读取相机设置…" }
        rgbDetail = TextView(activity)
        pwDetail = TextView(activity)
        looksDetail = TextView(activity)
        recipeDetail = TextView(activity)
        resetRecipe = Button(activity).apply {
            text = "恢复原厂七维与额外 RGB 增益"
            isAllCaps = false
            setOnClickListener { writeRecipe(null) }
        }
        resetRgb = Button(activity).apply {
            text = "RGB 复位为 100 / 100 / 100"
            isAllCaps = false
            setOnClickListener { write("RGB 复位") { putRgb(camera.resetRgbSettings()) } }
        }
        panel.addView(status)
        Field.entries.forEach { field ->
            val button = Button(activity).apply {
                isAllCaps = false
                setOnClickListener { choose(field) }
            }
            buttons[field] = button
            panel.addView(button)
            when (field) {
                Field.BLUE -> { panel.addView(resetRgb); panel.addView(rgbDetail) }
                Field.TONE -> panel.addView(pwDetail)
                Field.LOOK -> panel.addView(looksDetail)
                Field.RECIPE -> { panel.addView(resetRecipe); panel.addView(recipeDetail) }
                else -> Unit
            }
        }
        retry = Button(activity).apply {
            text = "重新读取"
            isAllCaps = false
            setOnClickListener { readAll() }
        }
        panel.addView(retry)
        dialog = AlertDialog.Builder(activity)
            .setTitle("机身设置")
            .setView(ScrollView(activity).apply { addView(panel) })
            .setNegativeButton("关闭", null)
            .setNeutralButton("机身 ISO 菜单") { _, _ -> openStockIso() }
            .create()
        dialog.setOnDismissListener { job?.cancel(); recipePoll?.cancel() }
        dialog.show()
        readAll()
    }

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
        for ((field, button) in buttons) {
            button.visibility = if (supported(field.group)) View.VISIBLE else View.GONE
            button.text = "${field.title}：${values[field]?.let { label(field, it) } ?: "未读取"}"
            button.isEnabled = if (field == Field.RECIPE) !busy && recipeCatalogue?.recipes?.isNotEmpty() == true
                else !busy && values.containsKey(field) && (field != Field.LOOK || looksState?.writable == true)
        }
        resetRgb.visibility = if (supported(Group.RGB)) View.VISIBLE else View.GONE
        resetRgb.isEnabled = !busy && listOf(Field.RED, Field.GREEN, Field.BLUE).all { values.containsKey(it) }
        rgbDetail.visibility = if (supported(Group.RGB)) View.VISIBLE else View.GONE
        pwDetail.visibility = if (supported(Group.PW)) View.VISIBLE else View.GONE
        looksDetail.visibility = if (supported(Group.LOOKS)) View.VISIBLE else View.GONE
        resetRecipe.visibility = if (supported(Group.RECIPE)) View.VISIBLE else View.GONE
        resetRecipe.isEnabled = !busy && camera.profile.recipeStyle && recipeSnapshotConfirmed
        recipeDetail.visibility = if (supported(Group.RECIPE)) View.VISIBLE else View.GONE
        renderRecipeDetail()
        retry.isEnabled = !busy
        dialog.getButton(AlertDialog.BUTTON_NEUTRAL)?.isEnabled = !busy
    }

    private fun invalidate() {
        values.clear()
        looksState = null
        recipeCatalogue = null
        recipeState = null
        recipeSnapshotConfirmed = false
        recipeIssue = ""
        rgbDetail.text = ""
        pwDetail.text = ""
        looksDetail.text = ""
        recipeDetail.text = ""
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
        rgbDetail.text = state.detail + "\n百分比，100 为中性；仅当前会话生效。"
    }

    private fun putPw(state: ExposureControls.PictureWizardState) {
        Field.entries.filter { it.group == Group.PW }.forEach { field ->
            values.remove(field)
            state.values[field.wire]?.let { values[field] = it }
        }
        pwDetail.text = "IQ 模式 ${state.iqMode}；" +
            (if (state.iqMatches) "IQ 读回与设置一致" else "IQ 读回与设置不同") + "\n" +
            (if (state.scalarModeEnabled) "当前模式启用饱和度、锐度和对比度" else "当前模式未启用饱和度、锐度和对比度")
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
            "hook-unavailable" -> "原厂 LUT 桥未就绪或根目录不匹配"
            "invalid-selection" -> "当前选择无效，请重新选择或恢复原厂"
            "staged-awaiting-stock-call" -> "已暂存，等待原厂 LUT 调用"
            "selected" -> "原厂已读取所选表，等待转发"
            "forwarded-to-stock-api" -> if (state.forwarded) "已转发三路至原厂 LUT 接口" else "原厂转发中，通道掩码 ${state.forwardedMask}"
            "stock-restore-awaiting-stock-call" -> "已选择原厂输入，等待原厂 LUT 调用"
            "stock" -> "原厂已使用自身 LUT 输入"
            "missing-table" -> "原厂未找到所选表"
            "invalid-table" -> "原厂拒绝无效表"
            else -> "原厂状态：${state.stage}"
        }
        looksDetail.text = "已部署 ${state.looks.size} 项；$stage\n转发不代表已验证 ISP 成像。"
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
        if (!camera.profile.recipeStyle) lines += "配方桥未就绪，目录可只读浏览"
        recipeState?.let { state ->
            lines += "机身回读：${RecipeControls.describe(state.values)}"
            lines += "代次 ${state.generation}；" + when {
                state.superseded -> "七维批次已被独立控件修改"
                state.stock -> "原厂设置"
                state.selectionMatches -> "当前七值匹配 ${state.selectedLabel ?: state.selectedId}"
                else -> "当前七值不匹配已部署目录"
            }
            listOf("预览" to state.preview, "拍摄" to state.capture).forEach { (route, stages) ->
                lines += route + "："
                stages.forEachIndexed { index, stage ->
                    val text = when {
                        stage.error != 0 -> RecipeControls.stageError(stage.error)
                        state.initialStock -> "原厂"
                        state.superseded -> "已被独立设置替换"
                        !stage.available -> if (route == "拍摄") "待拍摄更新" else "等待预览调用"
                        stage.applied -> if (index == 3) "曲线参数已转交" else "已处理"
                        else -> "等待更新"
                    }
                    lines += "${RecipeControls.STAGE_LABELS[index]}：$text"
                }
            }
            if (state.bridgeResult != 0) lines += "机身批次返回 ${state.bridgeResult}"
        }
        if (recipeIssue.isNotBlank()) lines += recipeIssue
        lines += "应用会替换当前七维设置与额外 RGB 增益；仅当前相机会话生效。"
        recipeDetail.text = lines.joinToString("\n")
    }

    private fun chooseRecipe() {
        val entries = recipeCatalogue?.recipes?.takeIf { it.isNotEmpty() } ?: return
        var selected = recipeState?.takeIf { it.selectionMatches && !it.superseded }?.selectedId?.let { id -> entries.indexOfFirst { it.id == id } } ?: -1
        lateinit var picker: AlertDialog
        fun canApply() = !busy && recipeSnapshotConfirmed && camera.profile.recipeStyle && selected in entries.indices && dialog.isShowing
        picker = AlertDialog.Builder(activity)
            .setTitle("七维配方（${entries.size} 项）")
            .setSingleChoiceItems(entries.map { "${it.label}\n${RecipeControls.describe(it.values)}" }.toTypedArray(), selected) { _, index ->
                selected = index
                picker.getButton(AlertDialog.BUTTON_POSITIVE).isEnabled = canApply()
            }
            .setNegativeButton("取消", null)
            .setPositiveButton("应用配方", null)
            .create()
        picker.setOnShowListener {
            picker.getButton(AlertDialog.BUTTON_POSITIVE).apply {
                isEnabled = canApply()
                setOnClickListener {
                    if (canApply()) { val recipe = entries[selected];picker.dismiss();writeRecipe(recipe) }
                }
            }
        }
        picker.show()
    }

    private fun writeRecipe(recipe: RecipeControls.Recipe?) {
        if (!recipeSnapshotConfirmed || !camera.profile.recipeStyle) return
        write(Field.RECIPE.title) {
            recipeIssue = ""
            putRecipe(if (recipe == null) camera.resetRecipe() else camera.applyRecipe(recipe))
            recipeSnapshotConfirmed = true
            // The batch replaces extra row gains as well as the seven style
            // values. Refresh sibling readbacks before those controls are used.
            if (supported(Group.RGB)) try { putRgb(camera.readRgbSettings()) }
                catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { values.keys.removeAll { it.group == Group.RGB };recipeIssue += "RGB 回读失败：${error.message}\n" }
            if (supported(Group.PW)) try { putPw(camera.readPictureWizard()) }
                catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { values.keys.removeAll { it.group == Group.PW };recipeIssue += "原厂画面参数回读失败：${error.message}" }
        }
    }

    private fun scheduleRecipePoll() {
        recipePoll?.cancel()
        if (!dialog.isShowing || !recipeSnapshotConfirmed || recipeState?.awaitingPreview != true) return
        recipePoll = activity.lifecycleScope.launch {
            repeat(8) {
                delay(1500)
                if (busy || !dialog.isShowing) return@launch
                try {
                    checkConnection()
                    val latest = camera.readRecipe()
                    checkConnection()
                    putRecipe(latest)
                    render()
                    if (!latest.awaitingPreview) return@launch
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) {
                    recipeSnapshotConfirmed = false
                    recipeState = (error as? RecipeControls.Failure)?.readback
                    if (recipeState == null) values.remove(Field.RECIPE) else recipeState?.let(::putRecipe)
                    recipeIssue = "配方状态读取失败：${error.message}；请重新读取"
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
        status.text = "正在读取相机设置…"
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
                        else { recipeState = (e as? RecipeControls.Failure)?.readback;recipeState?.let(::putRecipe) }
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
                read(Group.PW, "原厂画面参数") { putPw(camera.readPictureWizard()) }
                read(Group.LOOKS, "LUT") { putLooks(camera.readLooks()) }
                if (camera.profile.recipes) read(Group.RECIPE, "配方目录") {
                    recipeCatalogue = camera.readRecipes()
                    values[Field.RECIPE] = "请选择配方"
                }
                if (camera.profile.recipeStyle) read(Group.RECIPE, "配方状态") {
                    putRecipe(camera.readRecipe());recipeSnapshotConfirmed = true
                }
                checkConnection()
                status.text = when {
                    errors.isNotEmpty() -> errors.joinToString("\n") + "\n失败项需重新读取后再设置"
                    !camera.profile.hasBodySettings -> "当前连接未提供机身设置"
                    values.size != buttons.keys.count { supported(it.group) } -> "部分设置无法识别，请重新读取"
                    else -> "已读取相机当前设置；仅当前会话生效"
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { invalidate(); status.text = "${e.message}\n请重新读取" }
            finally { busy = false; render();scheduleRecipePoll() }
        }
    }

    private fun choose(field: Field) {
        if (busy || !dialog.isShowing) return
        if (field == Field.RECIPE) { chooseRecipe();return }
        val current = values[field] ?: return
        if (field.group == Group.RGB || field == Field.TONE) {
            chooseNumber(field, current as Int, if (field == Field.TONE) -1 else 0, if (field == Field.TONE) 359 else 200)
            return
        }
        val choices: List<Any> = when (field) {
            Field.STEP -> ExposureControls.ISO_STEPS
            Field.EXPANSION, Field.DIAL -> listOf(false, true)
            Field.MAXIMUM -> ExposureControls.AUTO_ISO_MAX
            Field.FASTEST -> ExposureControls.FASTEST
            Field.SATURATION, Field.SHARPNESS, Field.CONTRAST -> (-4..4).toList()
            Field.LOOK -> {
                val state = looksState?.takeIf { it.writable } ?: return
                listOf(LookChoice(null, "原厂（清除当前选择）")) + state.looks.filter { it.tableValid && it.selectable }.map { LookChoice(it.id, it.label) }
            }
            else -> return
        }
        var selected = if (field == Field.LOOK) choices.indexOfFirst { (it as LookChoice).id == (current as LookChoice).id }.coerceAtLeast(0) else choices.indexOf(current)
        if (selected < 0) return
        AlertDialog.Builder(activity)
            .setTitle(field.title)
            .setSingleChoiceItems(choices.map { label(field, it) }.toTypedArray(), selected) { _, index -> selected = index }
            .setNegativeButton("取消", null)
            .setPositiveButton(if (field == Field.LOOK) "暂存选择" else "设置") { _, _ ->
                val requested = choices[selected]
                val changed = if (field == Field.LOOK) !looksState!!.selectionValid || (requested as LookChoice).id != (current as LookChoice).id else requested != current
                if (changed && dialog.isShowing) set(field, requested)
            }.show()
    }

    private fun chooseNumber(field: Field, current: Int, minimum: Int, maximum: Int) {
        val input = EditText(activity).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or if (minimum < 0) InputType.TYPE_NUMBER_FLAG_SIGNED else 0
            hint = "$minimum 至 $maximum"
            setText(current.toString())
            selectAll()
        }
        val picker = AlertDialog.Builder(activity).setTitle(field.title).setView(input)
            .setNegativeButton("取消", null).setPositiveButton("设置", null).create()
        picker.setOnShowListener {
            picker.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val value = input.text.toString().toIntOrNull()
                if (value == null || value !in minimum..maximum) input.error = "请输入 $minimum 至 $maximum 的整数"
                else {
                    picker.dismiss()
                    if (value != current && dialog.isShowing) set(field, value)
                }
            }
        }
        picker.show()
    }

    private fun set(field: Field, requested: Any) {
        if (busy || !values.containsKey(field)) return
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
        if (busy || !dialog.isShowing) return
        recipePoll?.cancel()
        busy = true
        status.text = "正在设置$title…"
        render()
        job = activity.lifecycleScope.launch {
            try {
                checkConnection()
                block()
                checkConnection()
                if (title != Field.RECIPE.title && camera.profile.recipeStyle) {
                    try {
                        putRecipe(camera.readRecipe());recipeSnapshotConfirmed = true;recipeIssue = ""
                        checkConnection()
                    } catch (cancelled: CancellationException) { throw cancelled }
                    catch (error: Exception) {
                        recipeSnapshotConfirmed = false;recipeState = null
                        values.remove(Field.RECIPE)
                        recipeIssue = "配方状态读取失败：${error.message}；请重新读取"
                    }
                }
                status.text = when (title) {
                    Field.LOOK.title -> "LUT 选择已暂存；原厂调用状态见下方"
                    Field.RECIPE.title -> "七值已读回；预览与拍摄阶段见下方"
                    else -> "已读回：$title"
                }
            } catch (e: CancellationException) { throw e }
            catch (error: RecipeControls.Failure) {
                val catalogue = recipeCatalogue
                invalidate()
                recipeCatalogue = catalogue
                error.readback?.let(::putRecipe)
                recipeIssue = error.message.orEmpty() + if (error.mutationMayHaveApplied) "；请求可能已执行，请重新读取确认" else "；请重新读取后再设置"
                status.text = "$title 设置未确认：$recipeIssue"
            }
            catch (e: Exception) {
                invalidate()
                status.text = "$title 设置未确认：${e.message}\n请重新读取后再设置"
            } finally { busy = false; render();scheduleRecipePoll() }
        }
    }

    private fun checkConnection() {
        if (NxApp.camera !== camera) throw NxException("相机连接已改变，请关闭后重新打开设置")
    }

    private fun label(field: Field, value: Any): String = when (field) {
        Field.STEP -> if (value == "one-third") "1/3 EV" else "1 EV"
        Field.EXPANSION -> if (value == true) "开启" else "关闭"
        Field.DIAL -> if (value == true) "锁定" else "未锁定"
        Field.MAXIMUM -> "ISO $value"
        Field.FASTEST -> if (value == "off") "关闭限制" else value.toString()
        Field.RED, Field.GREEN, Field.BLUE -> "$value%"
        Field.TONE -> if (value == -1) "关闭" else "$value°"
        Field.LOOK -> (value as LookChoice).label
        else -> value.toString()
    }
}
