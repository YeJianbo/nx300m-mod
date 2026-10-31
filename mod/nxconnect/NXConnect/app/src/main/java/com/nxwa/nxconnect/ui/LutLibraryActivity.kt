// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.os.Bundle
import android.provider.OpenableColumns
import android.text.InputFilter
import android.view.Gravity
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.google.android.material.slider.Slider
import com.google.android.material.tabs.TabLayout
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.LutLibrary
import com.nxwa.nxconnect.net.ExposureControls
import com.nxwa.nxconnect.net.NxCamera
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Local masters stay usable offline; camera selections always use the existing native API. */
class LutLibraryActivity : OneUiInfoActivity() {
    private lateinit var page: OneUiInfoPage
    private lateinit var library: LutLibrary
    private var local = emptyList<LutLibrary.Entry>()
    private var localLoaded = false
    private var state: ExposureControls.LooksState? = null
    private var loadedCamera: NxCamera? = null
    private var cameraTab = false
    private var busy = false
    private var message = ""
    private var exportId: String? = null
    private var importMenu: MenuItem? = null
    private var refreshMenu: MenuItem? = null
    private var sortMenu: MenuItem? = null

    private val importer = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) work {
            val failures = mutableListOf<String>()
            var imported = 0
            withContext(Dispatchers.IO) {
                for (uri in uris) {
                    var name = uri.lastPathSegment?.substringAfterLast('/') ?: "LUT.cube"
                    try {
                        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                            if (it.moveToFirst()) name = it.getString(0) ?: name
                        }
                        contentResolver.openInputStream(uri)?.use { library.import(it, name) }
                            ?: error("无法读取文件")
                        imported++
                    } catch (e: CancellationException) { throw e }
                    catch (e: Exception) { failures += "$name：${e.message ?: "导入失败"}" }
                }
            }
            local = withContext(Dispatchers.IO) { library.list() }
            localLoaded = true
            message = "已导入 $imported 项到手机"
            if (failures.isNotEmpty()) NxDialogBuilder(this@LutLibraryActivity)
                .setTitle("部分文件未导入").setMessage(failures.take(8).joinToString("\n") +
                    if (failures.size > 8) "\n另有 ${failures.size - 8} 项失败" else "")
                .setPositiveButton("确定", null).show()
        }
    }
    private val exporter = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val id = exportId
        exportId = null
        if (uri != null && id != null) work {
            withContext(Dispatchers.IO) {
                library.source(id).inputStream().use { incoming ->
                    contentResolver.openOutputStream(uri, "wt")?.use { incoming.copyTo(it) }
                        ?: error("无法写入所选位置")
                }
            }
            message = "已导出 .cube 文件"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        library = LutLibrary(applicationContext)
        cameraTab = savedInstanceState?.getBoolean("cameraTab") ?: intent.getBooleanExtra("camera_tab", false)
        exportId = savedInstanceState?.getString("exportId")
        page = OneUiInfoPage(this, "LUT 库")
        importMenu = page.binding.pageToolbar.menu.add("导入").apply {
            setIcon(R.drawable.ic_oneui_add)
            setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
            setOnMenuItemClickListener { if (!busy) importer.launch(arrayOf("*/*")); true }
        }
        refreshMenu = page.binding.pageToolbar.menu.add("刷新").apply {
            setOnMenuItemClickListener { if (!busy) refresh(); true }
        }
        sortMenu = page.binding.pageToolbar.menu.add("排序").setOnMenuItemClickListener {
            if (!busy && !cameraTab && localLoaded) NxDialogBuilder(this).setTitle("手机 LUT 排序")
                .setItems(arrayOf("名称", "最近导入")) { _, which ->
                    changeLocal { library.sort(which == 0) }
                }.setNegativeButton("取消", null).show()
            true
        }
        render()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                NxApp.connection.collect {
                    state = null
                    loadedCamera = null
                    render()
                    if (cameraTab && !busy) refresh()
                }
            }
        }
        if (!cameraTab) refresh()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("cameraTab", cameraTab)
        outState.putString("exportId", exportId)
        super.onSaveInstanceState(outState)
    }

    private fun render() {
        page.binding.pageContent.removeAllViews()
        importMenu?.isVisible = !cameraTab
        importMenu?.isEnabled = !busy
        refreshMenu?.isEnabled = !busy
        sortMenu?.isVisible = !cameraTab
        sortMenu?.isEnabled = !busy && localLoaded
        val tabs = TabLayout(this).apply {
            tabMode = TabLayout.MODE_FIXED
            tabGravity = TabLayout.GRAVITY_FILL
            setBackgroundColor(android.graphics.Color.TRANSPARENT)
            setTabTextColors(color(R.color.nx_on_surface_variant), color(R.color.nx_primary))
            addTab(newTab().setText("相机"), cameraTab)
            addTab(newTab().setText("手机"), !cameraTab)
            addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
                override fun onTabSelected(tab: TabLayout.Tab) {
                    cameraTab = tab.position == 0
                    message = ""
                    render()
                    if (!busy && (cameraTab || !localLoaded)) refresh()
                }
                override fun onTabUnselected(tab: TabLayout.Tab) = Unit
                override fun onTabReselected(tab: TabLayout.Tab) = Unit
            })
        }
        page.binding.pageContent.addView(tabs)
        if (message.isNotEmpty()) page.card().addView(page.text(message, 14f, true).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE })
        if (busy) page.card().addView(page.text("正在处理…", 14f, true))
        if (cameraTab) renderCamera() else renderLocal()
    }

    private fun renderLocal() {
        if (!localLoaded) {
            if (!busy) page.card().addView(page.text("未能读取手机 LUT 库，请刷新重试。", secondary = true))
            return
        }
        if (local.isEmpty()) {
            page.card().also { card ->
                card.addView(page.text("还没有手机 LUT", 18f))
                card.addView(page.text("导入 .cube 文件，随时整理或导出。", 14f, true))
                button(card, "导入 .cube") { importer.launch(arrayOf("*/*")) }
            }
            return
        }
        page.card().addView(page.text("${local.size} 项 · 保存在手机", 14f, true))
        local.forEach { entry ->
            val card = page.card()
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            val labels = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                addView(page.text(entry.label, 17f))
                addView(page.text("${entry.grid} × ${entry.grid} × ${entry.grid} · ${entry.filename}", 13f, true))
                isFocusable = true
                setOnClickListener { if (!busy) localActions(entry) }
                GalleryChrome.press(this)
            }
            row.addView(labels, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(androidx.appcompat.widget.AppCompatImageButton(this).apply {
                setImageResource(R.drawable.ic_gallery_more)
                imageTintList = android.content.res.ColorStateList.valueOf(color(R.color.nx_on_surface))
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
                contentDescription = "${entry.label}，更多操作"
                isEnabled = !busy
                setOnClickListener { localActions(entry) }
            }, LinearLayout.LayoutParams(page.dp(48), page.dp(48)))
            card.addView(row)
        }
    }

    private fun renderCamera() {
        val current = NxApp.camera
        if (current == null) {
            page.card().addView(page.text("连接相机后，可切换卡上的 LUT。", secondary = true))
            return
        }
        val snapshot = state.takeIf { loadedCamera === current }
        if (snapshot == null) {
            if (!busy) page.card().addView(page.text("尚未读取相机 LUT。点击刷新重试。", secondary = true))
            return
        }
        val status = page.card("当前色彩")
        status.addView(page.text(if (snapshot.stock) "原厂色彩" else snapshot.looks.firstOrNull { it.id == snapshot.selectedId }?.label ?: "当前选择无效", 19f))
        status.addView(page.text(stageText(snapshot), 13f, true))
        button(status, "恢复原厂色彩", snapshot.writable && !snapshot.stock) { applyLook(current, null, null) }
        if (!snapshot.writable) status.addView(page.text("相机 LUT 模块尚未就绪，当前只能查看列表。", 13f, true))
        snapshot.looks.forEach { look ->
            val card = page.card()
            card.addView(page.text(look.label, 17f))
            val selected = snapshot.selectionValid && !snapshot.stock && snapshot.selectedId == look.id
            val detail = when {
                !look.tableValid -> "文件不可用"
                selected -> "当前选择" + if (snapshot.strengthSupported) " · ${snapshot.strength}%" else ""
                else -> "已在相机"
            }
            card.addView(page.text(detail, 13f, true))
            card.minimumHeight = page.dp(72)
            card.isFocusable = true
            card.isEnabled = !busy && snapshot.writable && look.selectable && look.tableValid
            GalleryChrome.press(card)
            card.setOnClickListener {
                if (!busy && snapshot.writable && look.selectable && look.tableValid) selectCameraLook(current, snapshot, look)
            }
        }
    }

    private fun refresh() = work {
        message = ""
        if (!cameraTab) {
            local = withContext(Dispatchers.IO) { library.list() }
            localLoaded = true
        } else {
            val camera = NxApp.camera ?: return@work
            camera.info()
            check(NxApp.camera === camera) { "相机连接已变化，请刷新" }
            check(camera.profile.looks) { "相机尚未提供 LUT 接口，请先启用机内 LUT 模块" }
            val result = camera.readLooks()
            check(NxApp.camera === camera) { "相机连接已变化，请刷新" }
            loadedCamera = camera
            state = result
        }
    }

    private fun localActions(entry: LutLibrary.Entry) {
        val index = local.indexOfFirst { it.id == entry.id }
        if (index < 0 || busy) return
        val actions = mutableListOf("重命名", "导出 .cube")
        if (index > 0) actions += "上移"
        if (index < local.lastIndex) actions += "下移"
        actions += "详细信息"
        actions += "删除"
        NxDialogBuilder(this).setTitle(entry.label).setItems(actions.toTypedArray()) { _, which ->
            when (actions[which]) {
                "重命名" -> rename(entry)
                "导出 .cube" -> {
                    exportId = entry.id
                    exporter.launch(entry.label.replace(Regex("[\\\\/:*?\"<>|]"), "_") + ".cube")
                }
                "上移" -> changeLocal { library.move(entry.id, -1) }
                "下移" -> changeLocal { library.move(entry.id, 1) }
                "详细信息" -> NxDialogBuilder(this).setTitle(entry.label)
                    .setMessage("${entry.filename}\n${entry.grid} × ${entry.grid} × ${entry.grid}\n输入下限：${entry.domainMin.joinToString()}\n输入上限：${entry.domainMax.joinToString()}\n保存在手机")
                    .setPositiveButton("确定", null).show()
                "删除" -> NxDialogBuilder(this).setTitle("删除 ${entry.label}？")
                    .setMessage("将从手机 LUT 库删除此文件。相机卡上的文件不受影响。")
                    .setNegativeButton("取消", null).setPositiveButton("删除") { _, _ ->
                        changeLocal { library.delete(entry.id) }
                    }.show()
            }
        }.setNegativeButton("取消", null).show()
    }

    private fun rename(entry: LutLibrary.Entry) {
        val input = EditText(this).apply { setSingleLine(); setText(entry.label); selectAll(); filters = arrayOf(InputFilter.LengthFilter(80)) }
        val dialog = NxDialogBuilder(this).setTitle("重命名").setView(input)
            .setNegativeButton("取消", null).setPositiveButton("保存", null).create()
        dialog.setOnShowListener {
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).setOnClickListener {
                val name = input.text.toString().trim()
                if (name.isEmpty() || name.any { it.isISOControl() || it == '|' }) {
                    input.error = "请输入有效名称"
                } else {
                    dialog.dismiss()
                    changeLocal { library.rename(entry.id, name) }
                }
            }
        }
        dialog.show()
    }

    private fun selectCameraLook(camera: NxCamera, snapshot: ExposureControls.LooksState, look: ExposureControls.Look) {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(page.dp(24), page.dp(8), page.dp(24), page.dp(8))
        }
        var strength = snapshot.strength
        if (snapshot.strengthSupported) {
            val label = page.text("强度 $strength%")
            content.addView(label)
            content.addView(Slider(this).apply {
                valueFrom = 0f; valueTo = 100f; stepSize = 1f; value = strength.toFloat()
                contentDescription = "LUT 强度"
                addOnChangeListener { _, value, _ -> strength = value.toInt(); label.text = "强度 $strength%" }
            })
        } else content.addView(page.text("应用到相机", 15f))
        NxDialogBuilder(this).setTitle(look.label).setView(content).setNegativeButton("取消", null)
            .setPositiveButton("应用") { _, _ -> applyLook(camera, look.id, strength.takeIf { snapshot.strengthSupported }) }.show()
    }

    private fun applyLook(camera: NxCamera, id: String?, strength: Int?) = work {
        check(NxApp.camera === camera) { "相机连接已变化，请刷新" }
        val result = camera.selectLook(id, strength)
        check(NxApp.camera === camera) { "相机连接已变化，请刷新" }
        loadedCamera = camera
        state = result
        message = stageText(result)
    }

    private fun stageText(snapshot: ExposureControls.LooksState): String = when {
        !snapshot.selectionValid -> "当前选择无效，请重新选择"
        !snapshot.writable -> "相机 LUT 模块尚未就绪"
        snapshot.stage == "stock" -> "原厂色彩"
        snapshot.stage == "stock-restore-awaiting-stock-call" -> "已请求恢复，等待相机更新"
        snapshot.forwarded -> "已交给相机成像链"
        else -> "选择已暂存，等待相机更新"
    }

    private fun changeLocal(action: () -> Unit) = work {
        local = withContext(Dispatchers.IO) { action(); library.list() }
        localLoaded = true
        message = ""
    }

    private fun work(action: suspend () -> Unit) {
        if (busy) return
        busy = true
        render()
        lifecycleScope.launch {
            try { action() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { message = e.message ?: "操作失败，请重试" }
            finally { busy = false; render() }
        }
    }

    private fun button(parent: LinearLayout, label: String, enabled: Boolean = true, action: () -> Unit) {
        parent.addView(MaterialButton(this).apply {
            text = label; isAllCaps = false; isEnabled = enabled && !busy
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { if (!busy) action() }
        })
    }
    private fun color(id: Int) = ContextCompat.getColor(this, id)
}
