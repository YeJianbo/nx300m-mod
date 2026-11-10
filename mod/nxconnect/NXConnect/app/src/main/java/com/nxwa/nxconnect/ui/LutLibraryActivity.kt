// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.text.InputFilter
import android.view.Gravity
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ArrayAdapter
import android.widget.Spinner
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.button.MaterialButton
import com.google.android.material.checkbox.MaterialCheckBox
import com.google.android.material.progressindicator.CircularProgressIndicator
import com.google.android.material.slider.Slider
import com.google.android.material.tabs.TabLayout
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.CardLutFormat
import com.nxwa.nxconnect.data.CardLutLibrary
import com.nxwa.nxconnect.data.CubeLut
import com.nxwa.nxconnect.data.LutLibrary
import com.nxwa.nxconnect.net.ExposureControls
import com.nxwa.nxconnect.net.NxCamera
import java.io.ByteArrayOutputStream
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/** SD-card management is direct and offline; camera switching remains a separate tab. */
class LutLibraryActivity : OneUiInfoActivity() {
    private enum class Area { CAMERA, CARD, PHONE }

    private data class BlendChoice(val id: String, val label: String, val grid: Int)

    private lateinit var page: OneUiInfoPage
    private lateinit var library: LutLibrary
    private var area = Area.CARD
    private var local = emptyList<LutLibrary.Entry>()
    private var localLoaded = false
    private var cardLibrary: CardLutLibrary? = null
    private var card: CardLutLibrary.Snapshot? = null
    private var cardQuery = ""
    private var cardFavoritesOnly = false
    private var cardGroupFilter: String? = null
    private val selectedCardIds = linkedSetOf<String>()
    private var state: ExposureControls.LooksState? = null
    private var loadedCamera: NxCamera? = null
    private var busy = false
    private var message = ""
    private var exportId: String? = null
    private var exportFromCard = false
    private var importMenu: MenuItem? = null
    private var blendMenu: MenuItem? = null
    private var refreshMenu: MenuItem? = null
    private var sortMenu: MenuItem? = null
    private var searchMenu: MenuItem? = null
    private var filterMenu: MenuItem? = null
    private var groupsMenu: MenuItem? = null
    private var selectAllButton: MaterialButton? = null
    private var favoriteSelectedButton: MaterialButton? = null
    private var statusRefreshJob: Job? = null
    private var statusRevision = 0
    private var blendPhotoAction: ((Uri) -> Unit)? = null
    private val preferences by lazy { getSharedPreferences("lut_card", MODE_PRIVATE) }

    private val treePicker = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) authorizeCard(uri)
    }
    private val phoneImporter = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) work {
            val failures = mutableListOf<String>()
            var imported = 0
            withContext(Dispatchers.IO) {
                for ((index, uri) in uris.withIndex()) {
                    val name = displayName(uri, "LUT.cube")
                    updateBusyMessage("正在导入手机 ${index + 1}/${uris.size}：$name")
                    try {
                        contentResolver.openInputStream(uri)?.use { library.import(it, name) }
                            ?: error("无法读取文件")
                        imported++
                    } catch (error: CancellationException) { throw error }
                    catch (error: Exception) { failures += "$name：${error.message ?: "导入失败"}" }
                }
            }
            updateBusyMessage("正在回读手机 LUT 仓库")
            local = withContext(Dispatchers.IO) { library.list() }
            localLoaded = true
            message = "已导入 $imported 项到手机"
            reportFailures(failures, "部分文件未导入")
        }
    }
    private val cardImporter = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isNotEmpty()) work {
            val target = cardLibrary ?: error("请先选择 SD 卡目录")
            val failures = mutableListOf<String>()
            var imported = 0
            for ((index, uri) in uris.withIndex()) {
                val name = withContext(Dispatchers.IO) { displayName(uri, "LUT.cube") }
                updateBusyMessage("正在导入 ${index + 1}/${uris.size}：$name")
                try {
                    withContext(Dispatchers.IO) {
                        contentResolver.openInputStream(uri)?.use { input ->
                            target.importCube(input, name) { phase ->
                                updateBusyMessage("正在导入 ${index + 1}/${uris.size}：$name · $phase")
                            }
                        }
                            ?: error("无法读取文件")
                    }
                    imported++
                } catch (error: CancellationException) { throw error }
                catch (error: Exception) { failures += "$name：${error.message ?: "导入失败"}" }
            }
            updateBusyMessage("正在回读 SD 卡 LUT 目录")
            useCardSnapshot(withContext(Dispatchers.IO) { target.refresh() })
            message = "已写入 SD 卡 $imported 项；9×9×9 表已回读校验"
            reportFailures(failures, "部分 LUT 未写入")
        }
    }
    private val exporter = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val id = exportId
        val fromCard = exportFromCard
        exportId = null
        exportFromCard = false
        if (uri != null && id != null) work {
            withContext(Dispatchers.IO) {
                if (fromCard) {
                    val target = cardLibrary ?: error("SD 卡目录已断开")
                    target.exportMaster(id, uri)
                } else {
                    library.source(id).inputStream().use { incoming ->
                        contentResolver.openOutputStream(uri, "wt")?.use { incoming.copyTo(it) }
                            ?: error("无法写入所选位置")
                    }
                }
            }
            message = "已导出 .cube 文件"
        }
    }
    private val blendPhotoPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) blendPhotoAction?.invoke(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        library = LutLibrary(applicationContext)
        area = savedInstanceState?.getString("area")?.let { value ->
            Area.entries.firstOrNull { it.name == value }
        } ?: if (savedInstanceState?.getBoolean("cameraTab") == true || intent.getBooleanExtra("camera_tab", false)) {
            Area.CAMERA
        } else if (intent.getBooleanExtra("phone_tab", false)) {
            Area.PHONE
        } else Area.CARD
        exportId = savedInstanceState?.getString("exportId")
        exportFromCard = savedInstanceState?.getBoolean("exportFromCard") ?: false
        cardQuery = savedInstanceState?.getString("cardQuery").orEmpty()
        cardFavoritesOnly = savedInstanceState?.getBoolean("cardFavoritesOnly") ?: false
        cardGroupFilter = savedInstanceState?.getString("cardGroupFilter")
        selectedCardIds += savedInstanceState?.getStringArrayList("selectedCardIds").orEmpty()
        page = OneUiInfoPage(this, "LUT 库")
        importMenu = page.binding.pageToolbar.menu.add("导入").apply {
            setIcon(R.drawable.ic_oneui_add)
            setShowAsAction(MenuItem.SHOW_AS_ACTION_IF_ROOM)
            setOnMenuItemClickListener {
                if (!busy && area == Area.CARD && card != null) cardImporter.launch(arrayOf("text/plain", "application/octet-stream", "*/*"))
                else if (!busy && area == Area.PHONE) phoneImporter.launch(arrayOf("*/*"))
                true
            }
        }
        blendMenu = page.binding.pageToolbar.menu.add("混合").apply {
            setOnMenuItemClickListener {
                if (!busy && area == Area.CARD && card != null) openBlendDialog()
                true
            }
        }
        refreshMenu = page.binding.pageToolbar.menu.add("刷新").apply {
            setOnMenuItemClickListener { if (!busy) refresh(); true }
        }
        sortMenu = page.binding.pageToolbar.menu.add("排序").apply {
            setOnMenuItemClickListener {
                if (!busy && area == Area.PHONE && localLoaded) NxDialogBuilder(this@LutLibraryActivity)
                    .setTitle("手机 LUT 排序").setItems(arrayOf("名称", "最近导入")) { _, which ->
                        changeLocal { library.sort(which == 0) }
                    }.setNegativeButton("取消", null).show()
                true
            }
        }
        searchMenu = page.binding.pageToolbar.menu.add("搜索").apply {
            setOnMenuItemClickListener { if (!busy && area == Area.CARD && card != null) searchCard(); true }
        }
        filterMenu = page.binding.pageToolbar.menu.add("筛选").apply {
            setOnMenuItemClickListener { if (!busy && area == Area.CARD && card != null) filterCard(); true }
        }
        groupsMenu = page.binding.pageToolbar.menu.add("分类管理").apply {
            setOnMenuItemClickListener { if (!busy && area == Area.CARD && card != null) manageGroups(); true }
        }
        render()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                NxApp.connection.collect {
                    stopStatusRefresh()
                    state = null
                    loadedCamera = null
                    if (area == Area.CAMERA) render()
                    if (area == Area.CAMERA && !busy) refresh()
                }
            }
        }
        when (area) {
            Area.CARD -> openSavedCard()
            Area.CAMERA -> refresh()
            Area.PHONE -> refresh()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("area", area.name)
        outState.putString("exportId", exportId)
        outState.putBoolean("exportFromCard", exportFromCard)
        outState.putString("cardQuery", cardQuery)
        outState.putBoolean("cardFavoritesOnly", cardFavoritesOnly)
        outState.putString("cardGroupFilter", cardGroupFilter)
        outState.putStringArrayList("selectedCardIds", ArrayList(selectedCardIds))
        super.onSaveInstanceState(outState)
    }

    override fun onStop() {
        stopStatusRefresh()
        super.onStop()
    }

    private fun stopStatusRefresh() {
        statusRevision++
        statusRefreshJob?.cancel()
        statusRefreshJob = null
    }

    private fun render() {
        page.binding.pageContent.removeAllViews()
        selectAllButton = null
        favoriteSelectedButton = null
        importMenu?.isVisible = area != Area.CAMERA
        importMenu?.isEnabled = !busy && (area == Area.PHONE || card != null)
        blendMenu?.isVisible = area == Area.CARD && card != null
        blendMenu?.isEnabled = !busy && card != null
        refreshMenu?.isEnabled = !busy
        sortMenu?.isVisible = area == Area.PHONE
        sortMenu?.isEnabled = !busy && localLoaded
        searchMenu?.isVisible = area == Area.CARD && card != null
        filterMenu?.isVisible = area == Area.CARD && card != null
        groupsMenu?.isVisible = area == Area.CARD && card != null
        searchMenu?.isEnabled = !busy
        filterMenu?.isEnabled = !busy
        groupsMenu?.isEnabled = !busy
        val tabs = TabLayout(this).apply {
            tabMode = TabLayout.MODE_FIXED
            tabGravity = TabLayout.GRAVITY_FILL
            setBackgroundColor(android.graphics.Color.TRANSPARENT)
            setTabTextColors(color(R.color.nx_on_surface_variant), color(R.color.nx_primary))
            addTab(newTab().setText("相机"), area == Area.CAMERA)
            addTab(newTab().setText("SD 卡"), area == Area.CARD)
            addTab(newTab().setText("手机"), area == Area.PHONE)
            addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
                override fun onTabSelected(tab: TabLayout.Tab) {
                    stopStatusRefresh()
                    area = Area.entries[tab.position]
                    message = ""
                    render()
                    when (area) {
                        Area.CARD -> if (card != null) refresh() else openSavedCard()
                        Area.CAMERA -> if (!busy) refresh()
                        Area.PHONE -> if (!localLoaded && !busy) refresh()
                    }
                }
                override fun onTabUnselected(tab: TabLayout.Tab) = Unit
                override fun onTabReselected(tab: TabLayout.Tab) = Unit
            })
        }
        page.binding.pageContent.addView(tabs)
        if (message.isNotEmpty() && !busy) page.card().addView(
            page.text(message, 14f, true).apply { accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE })
        if (busy) {
            renderBusy()
            return
        }
        when (area) {
            Area.CAMERA -> renderCamera()
            Area.CARD -> renderCard()
            Area.PHONE -> renderPhone()
        }
    }

    private fun renderCard() {
        val snapshot = card
        if (snapshot == null) {
            page.card().also { cardView ->
                cardView.addView(page.text("直接管理相机 SD 卡", 20f))
                cardView.addView(page.text("不需要连接相机。选择卡里的 nx300m-mod 文件夹后，可搜索、筛选、批量收藏、分类、重命名、删除并导入 .cube；App 不碰照片。", 14f, true))
                button(cardView, "选择 nx300m-mod 文件夹") { treePicker.launch(null) }
            }
            return
        }
        val visible = visibleCardLooks()
        page.card("插入式 SD 卡").also { summary ->
            summary.addView(page.text("${snapshot.looks.size} 款 LUT · ${snapshot.favorites.size} 款收藏 · ${snapshot.groups.size} 个自定义分类", 17f))
            summary.addView(page.text("目录：${snapshot.rootLabel} · 离线读写，写入后逐文件回读", 13f, true))
            val filters = buildList {
                if (cardFavoritesOnly) add("仅收藏")
                cardGroupFilter?.let { id -> snapshot.groups.firstOrNull { it.id == id }?.let { add("分类：${it.label}") } }
                if (cardQuery.isNotBlank()) add("搜索：$cardQuery")
            }.ifEmpty { listOf("全部 LUT") }
            summary.addView(page.text("筛选：${filters.joinToString(" · ")} · 显示 ${visible.size} 项", 13f, true))
            val firstActions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
            compactButton(firstActions, "更换卡目录") { treePicker.launch(null) }
            compactButton(firstActions, "清除筛选") { clearCardFilters() }
            summary.addView(firstActions)
            val selectionActions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
            selectAllButton = compactButton(selectionActions, if (visible.isNotEmpty() && visible.all { it.id in selectedCardIds }) "取消全选" else "全选结果") {
                if (visible.isNotEmpty() && visible.all { it.id in selectedCardIds }) selectedCardIds.removeAll(visible.map { it.id }.toSet())
                else selectedCardIds.addAll(visible.map { it.id })
                render()
            }
            favoriteSelectedButton = compactButton(selectionActions, "收藏 ${selectedCardIds.size}") { setCardFavorites(true) }
            summary.addView(selectionActions)
            val moreActions = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
            compactButton(moreActions, "取消收藏") { setCardFavorites(false) }
            compactButton(moreActions, "分类选中") { assignSelectedGroups() }
            summary.addView(moreActions)
            button(summary, "创建混合风格") { openBlendDialog() }
        }
        val listCard = page.card("${visible.size} 个结果")
        if (visible.isEmpty()) {
            listCard.addView(page.text(if (snapshot.looks.isEmpty()) "卡内目录为空" else "没有符合当前筛选的 LUT", secondary = true))
            return
        }
        val list = RecyclerView(this).apply {
            layoutManager = LinearLayoutManager(this@LutLibraryActivity)
            itemAnimator = null
            setHasFixedSize(true)
            adapter = CardLookAdapter(visible)
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                minOf(page.dp(520), maxOf(page.dp(150), page.dp(74) * visible.size)))
        }
        listCard.addView(list)
    }

    private fun renderPhone() {
        if (!localLoaded) {
            if (!busy) page.card().addView(page.text("未能读取手机 LUT 库，请刷新重试。", secondary = true))
            return
        }
        if (local.isEmpty()) {
            page.card().also { cardView ->
                cardView.addView(page.text("手机仓库还没有 LUT", 18f))
                cardView.addView(page.text("这里是 App 私有备份库；相机实际读取 SD 卡上的目录。", 14f, true))
                button(cardView, "导入 .cube") { phoneImporter.launch(arrayOf("*/*")) }
            }
            return
        }
        page.card().addView(page.text("${local.size} 项 · 手机私有仓库", 14f, true))
        local.forEach { entry ->
            val cardView = page.card()
            val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
            val labels = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                addView(page.text(entry.label, 17f))
                addView(page.text("${entry.grid} × ${entry.grid} × ${entry.grid} · ${entry.filename}", 13f, true))
                isFocusable = true
                setOnClickListener { if (!busy) phoneActions(entry) }
                GalleryChrome.press(this)
            }
            row.addView(labels, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            row.addView(androidx.appcompat.widget.AppCompatImageButton(this).apply {
                setImageResource(R.drawable.ic_gallery_more)
                imageTintList = android.content.res.ColorStateList.valueOf(color(R.color.nx_on_surface))
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
                contentDescription = "${entry.label}，更多操作"
                isEnabled = !busy
                setOnClickListener { phoneActions(entry) }
            }, LinearLayout.LayoutParams(page.dp(48), page.dp(48)))
            cardView.addView(row)
        }
    }

    private fun renderCamera() {
        val current = NxApp.camera
        if (current == null) {
            page.card().addView(page.text("连接相机后，可切换卡上的 LUT；插卡管理无需连接。", secondary = true))
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
        if (snapshot.persistenceSupported) status.addView(page.text(
            if (snapshot.persistent) "已保存到相机卡" else "未保存到卡，仅本次有效", 13f, true))
        button(status, "恢复原厂色彩", snapshot.writable && !snapshot.stock) { applyLook(current, null, null) }
        if (!snapshot.writable) status.addView(page.text("相机 LUT 模块尚未就绪，当前只能查看列表。", 13f, true))
        snapshot.looks.forEach { look ->
            val cardView = page.card()
            cardView.addView(page.text(look.label, 17f))
            val selected = snapshot.selectionValid && !snapshot.stock && snapshot.selectedId == look.id
            val detail = when {
                !look.tableValid -> "文件不可用"
                selected -> "当前选择" + if (snapshot.strengthSupported) " · ${snapshot.strength}%" else ""
                else -> "已在相机"
            }
            cardView.addView(page.text(detail, 13f, true))
            cardView.minimumHeight = page.dp(72)
            cardView.isFocusable = true
            cardView.isEnabled = !busy && snapshot.writable && look.selectable && look.tableValid
            GalleryChrome.press(cardView)
            cardView.setOnClickListener {
                if (!busy && snapshot.writable && look.selectable && look.tableValid) selectCameraLook(current, snapshot, look)
            }
        }
    }

    private fun refresh() = work {
        message = ""
        when (area) {
            Area.CARD -> {
                val target = cardLibrary ?: error("请先选择 SD 卡目录")
                useCardSnapshot(withContext(Dispatchers.IO) { target.refresh() })
            }
            Area.PHONE -> {
                local = withContext(Dispatchers.IO) { library.list() }
                localLoaded = true
            }
            Area.CAMERA -> {
                val camera = NxApp.camera ?: return@work
                camera.info()
                check(NxApp.camera === camera) { "相机连接已变化，请刷新" }
                check(camera.profile.looks) { "相机尚未提供 LUT 接口，请先启用机内 LUT 模块" }
                val result = camera.readLooks()
                check(NxApp.camera === camera) { "相机连接已变化，请刷新" }
                loadedCamera = camera
                state = result
                watchStatus(camera, result)
            }
        }
    }

    private fun openSavedCard() {
        val saved = preferences.getString("tree_uri", null) ?: run { render(); return }
        try {
            val uri = Uri.parse(saved)
            if (!hasPersistedCardAccess(uri)) {
                clearSavedCard("上次的 SD 卡授权已失效，请重新选择目录。")
                return
            }
            authorizeCard(uri, savedCard = true)
        }
        catch (error: Exception) {
            clearSavedCard("上次的 SD 卡授权已失效，请重新选择：${error.message ?: "无权访问"}")
        }
    }

    private fun authorizeCard(uri: Uri, savedCard: Boolean = false) {
        message = "正在读取 SD 卡目录…"
        work {
            try {
                val (target, snapshot) = withContext(Dispatchers.IO) {
                    if (!hasPersistedCardAccess(uri)) {
                        contentResolver.takePersistableUriPermission(uri,
                            Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                    }
                    val opened = CardLutLibrary.open(applicationContext, uri)
                    opened to opened.refresh()
                }
                preferences.edit().putString("tree_uri", uri.toString()).apply()
                cardQuery = ""
                cardFavoritesOnly = false
                cardGroupFilter = null
                selectedCardIds.clear()
                cardLibrary = target
                card = snapshot
                message = "已连接 SD 卡目录；所有修改仅限 nx300m-mod"
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                if (savedCard && !hasPersistedCardAccess(uri)) {
                    val reason = "上次的 SD 卡授权已失效，请重新选择：${error.message ?: "无权访问"}"
                    clearSavedCard(reason)
                    throw IllegalStateException(reason, error)
                }
                throw error
            }
        }
    }

    private fun hasPersistedCardAccess(uri: Uri) = contentResolver.persistedUriPermissions.any {
        it.uri == uri && it.isReadPermission && it.isWritePermission
    }

    private fun clearSavedCard(reason: String) {
        preferences.edit().remove("tree_uri").apply()
        cardLibrary = null
        card = null
        selectedCardIds.clear()
        message = reason
        render()
    }

    private fun useCardSnapshot(snapshot: CardLutLibrary.Snapshot) {
        card = snapshot
        val known = snapshot.looks.mapTo(HashSet(), CardLutLibrary.Look::id)
        selectedCardIds.retainAll(known)
        if (cardGroupFilter != null && snapshot.groups.none { it.id == cardGroupFilter }) cardGroupFilter = null
    }

    private fun visibleCardLooks(): List<CardLutLibrary.Look> {
        val query = cardQuery.trim()
        return card?.looks.orEmpty().filter { look ->
            (!cardFavoritesOnly || look.favorite) &&
                (cardGroupFilter == null || look.groups.any { it.id == cardGroupFilter }) &&
                (query.isEmpty() || look.label.contains(query, ignoreCase = true) || look.id.contains(query, ignoreCase = true))
        }
    }

    private fun clearCardFilters() {
        cardQuery = ""
        cardFavoritesOnly = false
        cardGroupFilter = null
        render()
    }

    private fun searchCard() {
        val input = EditText(this).apply {
            setSingleLine()
            setText(cardQuery)
            hint = "名称或 LUT ID"
            filters = arrayOf(InputFilter.LengthFilter(80))
        }
        NxDialogBuilder(this).setTitle("搜索卡内 LUT").setView(input)
            .setNegativeButton("取消", null)
            .setNeutralButton("清除") { _, _ -> cardQuery = ""; render() }
            .setPositiveButton("搜索") { _, _ -> cardQuery = input.text.toString().trim(); render() }
            .show()
    }

    private fun filterCard() {
        val snapshot = card ?: return
        val labels = buildList {
            add("全部 LUT")
            add("仅收藏")
            snapshot.groups.forEach { add("${it.label}（${snapshot.count(it.id)}）") }
        }
        val selected = when {
            cardGroupFilter != null -> labels.indexOfFirst { label ->
                snapshot.groups.any { it.id == cardGroupFilter && label.startsWith(it.label) }
            }
            cardFavoritesOnly -> 1
            else -> 0
        }
        NxDialogBuilder(this).setTitle("筛选卡内 LUT")
            .setSingleChoiceItems(labels.toTypedArray(), selected.coerceAtLeast(0)) { dialog, which ->
                dialog.dismiss()
                cardFavoritesOnly = which == 1
                cardGroupFilter = if (which >= 2) snapshot.groups[which - 2].id else null
                render()
            }.setNegativeButton("取消", null).show()
    }

    private fun setCardFavorites(favorite: Boolean) {
        if (selectedCardIds.isEmpty()) { message = "先勾选 LUT"; render(); return }
        val target = cardLibrary ?: return
        val ids = selectedCardIds.toSet()
        work {
            val updated = withContext(Dispatchers.IO) { target.setFavorites(ids, favorite) }
            useCardSnapshot(updated)
            message = if (favorite) "已收藏 ${ids.size} 款" else "已取消收藏 ${ids.size} 款"
        }
    }

    private fun assignSelectedGroups() {
        if (selectedCardIds.isEmpty()) { message = "先勾选 LUT"; render(); return }
        val snapshot = card ?: return
        if (snapshot.groups.isEmpty()) { message = "还没有自定义分类；请先从右上角“分类管理”新建"; render(); return }
        val selectedLooks = snapshot.looks.filter { it.id in selectedCardIds }
        val common = snapshot.groups.filter { group -> selectedLooks.all { look -> look.groups.any { it.id == group.id } } }
            .mapTo(HashSet(), CardLutFormat.Group::id)
        val checked = BooleanArray(snapshot.groups.size) { snapshot.groups[it].id in common }
        NxDialogBuilder(this).setTitle("分类选中（决定最终归属）")
            .setMultiChoiceItems(snapshot.groups.map(CardLutFormat.Group::label).toTypedArray(), checked) { _, which, value ->
                checked[which] = value
            }
            .setMessage("勾选结果会覆盖这 ${selectedLooks.size} 款 LUT 的自定义分类；全不勾选将从自定义分类移除。")
            .setNegativeButton("取消", null)
            .setPositiveButton("应用") { _, _ ->
                val ids = selectedCardIds.toSet()
                val groupIds = snapshot.groups.filterIndexed { index, _ -> checked[index] }
                    .mapTo(HashSet(), CardLutFormat.Group::id)
                val target = cardLibrary ?: return@setPositiveButton
                work {
                    val updated = withContext(Dispatchers.IO) { target.assignGroups(ids, groupIds) }
                    useCardSnapshot(updated)
                    message = "已更新 ${ids.size} 款 LUT 的分类"
                }
            }.show()
    }

    private fun manageGroups() {
        val items = arrayOf("新建分类", "重命名分类", "删除分类")
        NxDialogBuilder(this).setTitle("LUT 分类管理").setItems(items) { _, which ->
            when (which) {
                0 -> inputDialog("新建 LUT 分类", "分类名称") { label ->
                    val target = cardLibrary ?: return@inputDialog
                    work {
                        useCardSnapshot(withContext(Dispatchers.IO) { target.createGroup(label) })
                        message = "已新建分类 $label"
                    }
                }
                1 -> pickGroup("重命名分类") { group ->
                    inputDialog("重命名“${group.label}”", "分类名称", group.label) { label ->
                        val target = cardLibrary ?: return@inputDialog
                        work {
                            useCardSnapshot(withContext(Dispatchers.IO) { target.renameGroup(group.id, label) })
                            message = "分类已重命名为 $label"
                        }
                    }
                }
                2 -> pickGroup("删除分类") { group ->
                    val count = card?.count(group.id) ?: 0
                    NxDialogBuilder(this).setTitle("删除“${group.label}”？")
                        .setMessage("这会从 $count 款 LUT 移除此分类；LUT 文件和收藏不受影响。\r\n\r\n会更新的文件：lut-groups.json、lut-groups.txt")
                        .setNegativeButton("取消", null)
                        .setPositiveButton("删除") { _, _ ->
                            val target = cardLibrary ?: return@setPositiveButton
                            work {
                                useCardSnapshot(withContext(Dispatchers.IO) { target.deleteGroup(group.id) })
                                message = "已删除分类 ${group.label}"
                            }
                        }.show()
                }
            }
        }.setNegativeButton("取消", null).show()
    }

    private fun pickGroup(title: String, action: (CardLutFormat.Group) -> Unit) {
        val groups = card?.groups.orEmpty()
        if (groups.isEmpty()) { message = "还没有自定义分类"; render(); return }
        NxDialogBuilder(this).setTitle(title).setItems(groups.map { it.label }.toTypedArray()) { _, which ->
            action(groups[which])
        }.setNegativeButton("取消", null).show()
    }

    private fun inputDialog(title: String, hint: String, initial: String = "", action: (String) -> Unit) {
        val input = EditText(this).apply {
            setSingleLine(); setText(initial); selectAll(); this.hint = hint
            filters = arrayOf(InputFilter.LengthFilter(80))
        }
        val dialog = NxDialogBuilder(this).setTitle(title).setView(input)
            .setNegativeButton("取消", null).setPositiveButton("保存", null).create()
        dialog.setOnShowListener {
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).setOnClickListener {
                val value = input.text.toString().trim()
                if (value.isEmpty() || value.any { it.isISOControl() || it == '|' }) input.error = "请输入有效名称"
                else { dialog.dismiss(); action(value) }
            }
        }
        dialog.show()
    }

    private fun openBlendDialog(editId: String? = null) {
        val target = cardLibrary ?: return
        work {
            val sources = withContext(Dispatchers.IO) {
                target.blendSources().map { source ->
                    BlendChoice("card:" + source.id, source.label, source.grid)
                } + library.blendSources().map { source ->
                    BlendChoice("phone:" + source.id, source.label, source.grid)
                }
            }
            if (sources.isEmpty() && editId == null) {
                message = "卡内没有保留 source.cube，手机仓库也没有可混合的 sRGB master。请先导入原始 .cube。"
                return@work
            }
            val recipe = editId?.let { withContext(Dispatchers.IO) { target.blendRecipe(it) } }
            showBlendDialog(target, sources, editId, recipe)
        }
    }

    private fun showBlendDialog(target: CardLutLibrary, sources: List<BlendChoice>,
                                editId: String?, recipe: org.json.JSONObject?) {
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(page.dp(24), page.dp(6), page.dp(24), page.dp(4))
        }
        val aOptions = mutableListOf<Pair<String, String?>>("原图" to null)
        val bOptions = mutableListOf<Pair<String, String?>>("原图" to null)
        sources.forEach { source ->
            aOptions += "${source.label} · ${source.grid}³" to source.id
            bOptions += "${source.label} · ${source.grid}³" to source.id
        }
        if (recipe != null) {
            for ((options, key) in listOf(aOptions to "a", bOptions to "b")) {
                val source = recipe.optJSONObject(key) ?: continue
                val id = source.optString("id")
                if (options.none { it.second == id }) {
                    options += "${source.optString("label", id)} · 快照" to id
                }
            }
        }
        fun adapter(options: List<Pair<String, String?>>) = ArrayAdapter(
            this, android.R.layout.simple_spinner_item, options.map { it.first }
        ).apply { setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item) }
        val aSpinner = Spinner(this).apply { adapter = adapter(aOptions); contentDescription = "混合源 A" }
        val bSpinner = Spinner(this).apply { adapter = adapter(bOptions); contentDescription = "混合源 B" }
        content.addView(page.text("源 A", 13f, true))
        content.addView(aSpinner)
        content.addView(page.text("源 B", 13f, true))
        content.addView(bSpinner)

        val preferredA = recipe?.optJSONObject("a")?.optString("id")
            ?: selectedCardIds.firstOrNull { id -> sources.any { it.id == id } }
            ?: sources.firstOrNull()?.id
        val preferredB = recipe?.optJSONObject("b")?.optString("id")
            ?: sources.firstOrNull { it.id != preferredA }?.id
        aSpinner.setSelection(aOptions.indexOfFirst { it.second == preferredA }.coerceAtLeast(0))
        bSpinner.setSelection(bOptions.indexOfFirst { it.second == preferredB }.coerceAtLeast(0))
        val swap = MaterialButton(this).apply {
            text = "交换 A / B"; isAllCaps = false; isEnabled = editId == null
        }
        content.addView(swap)
        if (editId != null) {
            aSpinner.isEnabled = false
            bSpinner.isEnabled = false
        }

        val reference = ImageView(this).apply {
            scaleType = ImageView.ScaleType.CENTER_CROP
            setImageBitmap(syntheticBlendReference())
            contentDescription = "33 网格混合预览"
        }
        content.addView(reference, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, page.dp(190)))
        val choosePhoto = MaterialButton(this).apply { text = "选择参考照片"; isAllCaps = false }
        content.addView(choosePhoto)

        val ratioLabel = page.text("A 50% · B 50%", 16f)
        content.addView(ratioLabel)
        val initialRatio = ((recipe?.optDouble("amount_b", 0.5) ?: 0.5) * 100.0).toInt().coerceIn(0, 100)
        val ratio = Slider(this).apply {
            valueFrom = 0f; valueTo = 100f; stepSize = 1f; value = initialRatio.toFloat()
            contentDescription = "B 源混合比例"
        }
        content.addView(ratio)
        content.addView(page.text("拖动时更新 33³ master 预览；保存时才编译两张 9³ 相机表。", 12f, true))
        val name = EditText(this).apply {
            setSingleLine()
            hint = "混合风格名称"
            filters = arrayOf(InputFilter.LengthFilter(60))
            setText(if (editId == null) "混合风格" else recipe?.optString("label")
                ?.takeUnless { it.isBlank() } ?: editId?.let { id -> card?.looks?.firstOrNull { it.id == id }?.label }
                ?: "混合风格")
            selectAll()
        }
        content.addView(name)
        val status = page.text("正在读取混合源…", 12f, true)
        content.addView(status)
        val dialog = NxDialogBuilder(this)
            .setTitle(if (editId == null) "创建混合风格" else "编辑混合配方")
            .setView(content)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", null)
            .create()
        var inputs: Pair<CubeLut.Data, CubeLut.Data>? = null
        var rawInputs: Pair<CardLutLibrary.BlendInput, CardLutLibrary.BlendInput>? = null
        var inputGeneration = 0
        var inputJob: Job? = null
        var previewJob: Job? = null
        var requestedSources: Pair<String?, String?>? = null
        var suppressSelectionLoads = false
        var referencePixels: IntArray? = null
        var referenceWidth = 480
        var referenceHeight = 260
        fun refreshLabel() {
            val value = ratio.value.toInt()
            ratioLabel.text = "A ${100 - value}% · B $value%"
        }
        fun currentBitmapPixels(): IntArray {
            val bitmap = (reference.drawable as? android.graphics.drawable.BitmapDrawable)?.bitmap
                ?: syntheticBlendReference()
            val pixels = IntArray(bitmap.width * bitmap.height)
            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
            return pixels
        }
        referencePixels = currentBitmapPixels()
        fun schedulePreview() {
            val pair = inputs ?: return
            val generation = inputGeneration
            val amount = ratio.value / 100.0
            previewJob?.cancel()
            previewJob = lifecycleScope.launch {
                delay(140)
                val pixels = referencePixels ?: return@launch
                status.text = "正在更新 33³ 预览…"
                val output = withContext(Dispatchers.Default) {
                    val blended = CubeLut.blendLattice(pair.first, pair.second, amount)
                    CubeLut.applyArgb(blended, pixels)
                }
                if (generation == inputGeneration && dialog.isShowing) {
                    val bitmap = Bitmap.createBitmap(referenceWidth, referenceHeight, Bitmap.Config.ARGB_8888)
                    @Suppress("DEPRECATION")
                    bitmap.setPixels(output, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                    reference.setImageBitmap(bitmap)
                    status.text = "33³ master 预览；相机使用保存后的 9³ 近似。"
                }
            }
        }
        fun loadInputs() {
            val sourceChoice = Pair(
                aOptions[aSpinner.selectedItemPosition].second,
                bOptions[bSpinner.selectedItemPosition].second,
            )
            if (requestedSources == sourceChoice) return
            requestedSources = sourceChoice
            inputJob?.cancel()
            inputs = null
            rawInputs = null
            val generation = ++inputGeneration
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE)?.isEnabled = false
            status.text = "正在读取 A/B 源 LUT…"
            inputJob = lifecycleScope.launch {
                try {
                    val rawPair = withContext(Dispatchers.IO) {
                        if (editId != null) target.snapshotBlendInputs(editId)
                        else {
                            fun load(choiceId: String?): CardLutLibrary.BlendInput = when {
                                choiceId == null -> target.preparedBlendInput(null)
                                choiceId.startsWith("card:") -> target.preparedBlendInput(choiceId.removePrefix("card:"))
                                    .copy(id = choiceId)
                                choiceId.startsWith("phone:") -> library.preparedBlendInput(choiceId.removePrefix("phone:"))
                                else -> error("未知的混合源：$choiceId")
                            }
                            Pair(load(sourceChoice.first), load(sourceChoice.second))
                        }
                    }
                    if (generation == inputGeneration && dialog.isShowing) {
                        rawInputs = rawPair
                        inputs = Pair(CubeLut.resample(rawPair.first.data), CubeLut.resample(rawPair.second.data))
                        dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE)?.isEnabled = true
                        status.text = "混合源已就绪。"
                        schedulePreview()
                    }
                } catch (error: CancellationException) { throw error }
                catch (error: Exception) {
                    if (generation == inputGeneration && dialog.isShowing) {
                        requestedSources = null
                        status.text = "混合源不可用：${error.message ?: "读取失败"}"
                    }
                }
            }
        }
        ratio.addOnChangeListener { _, _, fromUser ->
            refreshLabel()
            if (fromUser) schedulePreview()
        }
        swap.setOnClickListener {
            if (editId != null) return@setOnClickListener
            suppressSelectionLoads = true
            val a = aSpinner.selectedItemPosition
            val b = bSpinner.selectedItemPosition
            aSpinner.setSelection(b, false)
            bSpinner.setSelection(a, false)
            ratio.value = 100f - ratio.value
            requestedSources = null
            aSpinner.post {
                suppressSelectionLoads = false
                loadInputs()
            }
        }
        aSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!suppressSelectionLoads && editId == null && dialog.isShowing) loadInputs()
            }
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
        }
        bSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (!suppressSelectionLoads && editId == null && dialog.isShowing) loadInputs()
            }
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) = Unit
        }
        choosePhoto.setOnClickListener {
            blendPhotoAction = { uri ->
                lifecycleScope.launch {
                    status.text = "正在读取参考照片…"
                    val bitmap = withContext(Dispatchers.IO) { decodeBlendReference(uri) }
                    if (bitmap != null && dialog.isShowing) {
                        reference.setImageBitmap(bitmap)
                        referenceWidth = bitmap.width
                        referenceHeight = bitmap.height
                        referencePixels = IntArray(bitmap.width * bitmap.height).also { pixels ->
                            bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
                        }
                        schedulePreview()
                    } else if (dialog.isShowing) status.text = "无法读取这张参考照片。"
                }
            }
            blendPhotoPicker.launch(arrayOf("image/*"))
        }
        dialog.setOnShowListener {
            refreshLabel()
            if (editId != null) {
                recipe?.optJSONObject("a")?.let { source ->
                    aSpinner.setSelection(aOptions.indexOfFirst { it.second == source.optString("id") }.coerceAtLeast(0), false)
                }
                recipe?.optJSONObject("b")?.let { source ->
                    bSpinner.setSelection(bOptions.indexOfFirst { it.second == source.optString("id") }.coerceAtLeast(0), false)
                }
            }
            loadInputs()
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).setOnClickListener {
                val sourcePair = inputs ?: return@setOnClickListener
                val sourceInputs = rawInputs ?: return@setOnClickListener
                val label = name.text.toString().trim()
                if (label.isEmpty() || label.any { it.isISOControl() || it == '|' }) {
                    name.error = "请输入有效名称"
                    return@setOnClickListener
                }
                val amount = ratio.value / 100.0
                val previewPixels = referencePixels
                val previewWidth = referenceWidth
                val previewHeight = referenceHeight
                dialog.dismiss()
                work {
                    val result = withContext(Dispatchers.IO) {
                        updateBusyMessage("正在生成参考缩略图")
                        val referenceData = previewPixels?.let { pixels ->
                            val blended = CubeLut.blendLattice(sourcePair.first, sourcePair.second, amount)
                            val mapped = CubeLut.applyArgb(blended, pixels)
                            val bitmap = Bitmap.createBitmap(previewWidth, previewHeight, Bitmap.Config.ARGB_8888)
                            bitmap.setPixels(mapped, 0, previewWidth, 0, 0, previewWidth, previewHeight)
                            ByteArrayOutputStream().use { output ->
                                bitmap.compress(Bitmap.CompressFormat.JPEG, 88, output)
                                output.toByteArray()
                            }
                        }
                        if (editId != null) target.updateBlend(editId, amount, label, referenceData) {
                            updateBusyMessage(it)
                        } else target.createBlendFromInputs(
                            sourceInputs.first, sourceInputs.second, amount, label, referenceData,
                        ) {
                            updateBusyMessage(it)
                        }
                    }
                    updateBusyMessage("正在回读 SD 卡 LUT 目录")
                    useCardSnapshot(withContext(Dispatchers.IO) { target.refresh() })
                    selectedCardIds.clear()
                    selectedCardIds += result.id
                    message = "已保存混合风格 ${result.label}，并归入“自制”"
                }
            }
        }
        dialog.setOnDismissListener {
            inputJob?.cancel()
            previewJob?.cancel()
            blendPhotoAction = null
        }
        dialog.show()
    }

    private fun syntheticBlendReference(): Bitmap {
        val width = 480
        val height = 260
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val pixels = IntArray(width * height)
        for (y in 0 until height) for (x in 0 until width) {
            var red = (45 + 190 * x / (width - 1.0)).toInt()
            var green = (35 + 190 * (1.0 - y / (height - 1.0))).toInt()
            var blue = (55 + 135 * (1.0 - x / (width - 1.0))).toInt()
            if (x in width * 2 / 3..width * 5 / 6 && y in height / 4..height * 3 / 4) {
                red = 202; green = 151; blue = 128
            }
            pixels[y * width + x] = (0xff shl 24) or (red shl 16) or (green shl 8) or blue
        }
        bitmap.setPixels(pixels, 0, width, 0, 0, width, height)
        return bitmap
    }

    private fun decodeBlendReference(uri: Uri): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        while (maxOf(bounds.outWidth / sample, bounds.outHeight / sample) > 720) sample *= 2
        val decoded = contentResolver.openInputStream(uri)?.use { input ->
            BitmapFactory.decodeStream(input, null, BitmapFactory.Options().apply { inSampleSize = sample })
        } ?: return null
        val scale = minOf(1f, 520f / maxOf(decoded.width, decoded.height))
        return if (scale < 1f) Bitmap.createScaledBitmap(decoded,
            (decoded.width * scale).toInt().coerceAtLeast(1),
            (decoded.height * scale).toInt().coerceAtLeast(1), true) else decoded
    }

    private fun cardLookActions(look: CardLutLibrary.Look) {
        val actions = buildList {
            if (look.isBlend) add("编辑混合配方")
            if (look.id != card?.protectedId) add("导出 .cube")
            if (look.id != card?.protectedId) add("重命名")
            add("详细信息")
            if (look.id != card?.protectedId) add("删除并备份")
        }
        val detail = "${look.label}\n${look.id}\n分类：${look.groups.joinToString("、", transform = CardLutFormat.Group::label).ifEmpty { "未分类" }}\n收藏：${if (look.favorite) "是" else "否"}\n卡片目录：${if (look.directoryPresent) "已部署" else "缺失"}"
        NxDialogBuilder(this).setTitle(look.label).setItems(actions.toTypedArray()) { _, which ->
            when (actions[which]) {
                "编辑混合配方" -> openBlendDialog(look.id)
                "导出 .cube" -> {
                    exportId = look.id
                    exportFromCard = true
                    exporter.launch(look.label.replace(Regex("[\\\\/:*?\"<>|]"), "_") + ".cube")
                }
                "重命名" -> inputDialog("重命名 LUT", "LUT 名称", look.label) { label ->
                    val target = cardLibrary ?: return@inputDialog
                    work {
                        useCardSnapshot(withContext(Dispatchers.IO) { target.renameLook(look.id, label) })
                        message = "已重命名为 $label"
                    }
                }
                "详细信息" -> NxDialogBuilder(this).setTitle(look.label).setMessage(detail)
                    .setPositiveButton("确定", null).show()
                "删除并备份" -> NxDialogBuilder(this).setTitle("删除 ${look.label}？")
                    .setMessage("先把 looks/${look.id} 的完整目录备份到手机，再从相机目录移除。不会删除照片。")
                    .setNegativeButton("取消", null)
                    .setPositiveButton("删除") { _, _ ->
                        val target = cardLibrary ?: return@setPositiveButton
                        work {
                            useCardSnapshot(withContext(Dispatchers.IO) { target.deleteLook(look.id) })
                            message = "已移出相机目录，并备份到手机 card-lut-backups"
                        }
                    }.show()
            }
        }.setNegativeButton("取消", null).show()
    }

    private fun phoneActions(entry: LutLibrary.Entry) {
        val index = local.indexOfFirst { it.id == entry.id }
        if (index < 0 || busy) return
        val actions = mutableListOf("重命名", "导出 .cube")
        if (index > 0) actions += "上移"
        if (index < local.lastIndex) actions += "下移"
        actions += "详细信息"
        actions += "删除"
        NxDialogBuilder(this).setTitle(entry.label).setItems(actions.toTypedArray()) { _, which ->
            when (actions[which]) {
                "重命名" -> phoneRename(entry)
                "导出 .cube" -> {
                    exportId = entry.id
                    exportFromCard = false
                    exporter.launch(entry.label.replace(Regex("[\\\\/:*?\"<>|]"), "_") + ".cube")
                }
                "上移" -> changeLocal { library.move(entry.id, -1) }
                "下移" -> changeLocal { library.move(entry.id, 1) }
                "详细信息" -> NxDialogBuilder(this).setTitle(entry.label)
                    .setMessage("${entry.filename}\n${entry.grid} × ${entry.grid} × ${entry.grid}\n输入下限：${entry.domainMin.joinToString()}\n输入上限：${entry.domainMax.joinToString()}\n保存在手机")
                    .setPositiveButton("确定", null).show()
                "删除" -> NxDialogBuilder(this).setTitle("删除 ${entry.label}？")
                    .setMessage("将从手机私有仓库删除。SD 卡上的 LUT 不受影响。")
                    .setNegativeButton("取消", null).setPositiveButton("删除") { _, _ ->
                        changeLocal { library.delete(entry.id) }
                    }.show()
            }
        }.setNegativeButton("取消", null).show()
    }

    private fun phoneRename(entry: LutLibrary.Entry) {
        val input = EditText(this).apply { setSingleLine(); setText(entry.label); selectAll(); filters = arrayOf(InputFilter.LengthFilter(80)) }
        val dialog = NxDialogBuilder(this).setTitle("重命名").setView(input)
            .setNegativeButton("取消", null).setPositiveButton("保存", null).create()
        dialog.setOnShowListener {
            dialog.getButton(android.content.DialogInterface.BUTTON_POSITIVE).setOnClickListener {
                val name = input.text.toString().trim()
                if (name.isEmpty() || name.any { it.isISOControl() || it == '|' }) input.error = "请输入有效名称"
                else { dialog.dismiss(); changeLocal { library.rename(entry.id, name) } }
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
        watchStatus(camera, result)
    }

    private fun pending(snapshot: ExposureControls.LooksState) = snapshot.writable &&
        snapshot.selectionValid && !snapshot.forwarded && snapshot.stage != "stock"

    /** Follow only this selection, while visible, for at most twelve seconds. */
    private fun watchStatus(camera: NxCamera, selected: ExposureControls.LooksState) {
        stopStatusRefresh()
        if (area != Area.CAMERA || !pending(selected) || !lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) return
        val revision = statusRevision
        statusRefreshJob = lifecycleScope.launch {
            try {
                val completed = withTimeoutOrNull(12_000L) {
                    while (true) {
                        delay(750)
                        if (NxApp.camera !== camera || revision != statusRevision || area != Area.CAMERA) return@withTimeoutOrNull true
                        val next = camera.readLooks()
                        currentCoroutineContext().ensureActive()
                        if (NxApp.camera !== camera || revision != statusRevision) return@withTimeoutOrNull true
                        val changedSelection = next.stock != selected.stock || next.selectedId != selected.selectedId ||
                            next.strength != selected.strength || next.generation != selected.generation
                        val nextMessage = if (changedSelection) "相机上的 LUT 选择已变化" else stageText(next)
                        val changed = state != next || message != nextMessage
                        loadedCamera = camera
                        state = next
                        message = nextMessage
                        if (changed) render()
                        if (changedSelection || !pending(next)) return@withTimeoutOrNull true
                    }
                    @Suppress("UNREACHABLE_CODE")
                    false
                }
                if (completed == null && NxApp.camera === camera && revision == statusRevision) {
                    message = "选择已暂存，尚未收到原厂写表确认。可半按快门后刷新。"
                    render()
                }
            } catch (error: CancellationException) { throw error }
            catch (error: Exception) {
                if (NxApp.camera === camera && revision == statusRevision) {
                    message = "自动读取状态失败：${error.message ?: "请刷新重试"}"
                    render()
                }
            }
        }
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

    private fun updateBusyMessage(value: String) {
        if (isFinishing || isDestroyed) return
        runOnUiThread {
            if (busy && !isFinishing && !isDestroyed) {
                message = value
                render()
            }
        }
    }

    private fun renderBusy() {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, page.dp(4), 0, page.dp(4))
        }
        val indicator = CircularProgressIndicator(this).apply {
            isIndeterminate = true
            setIndicatorColor(color(R.color.nx_primary))
            trackThickness = page.dp(3)
            contentDescription = "操作进行中"
        }
        row.addView(indicator, LinearLayout.LayoutParams(page.dp(32), page.dp(32)).apply {
            marginEnd = page.dp(14)
        })
        val label = message.ifBlank {
            when (area) {
                Area.CARD -> "正在读取 SD 卡…"
                Area.CAMERA -> "正在读取相机…"
                Area.PHONE -> "正在读取手机仓库…"
            }
        }
        row.addView(page.text(label, 14f, true).apply {
            layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        })
        page.card().addView(row)
    }

    private fun work(action: suspend () -> Unit) {
        if (busy) return
        stopStatusRefresh()
        busy = true
        render()
        lifecycleScope.launch {
            try { action() }
            catch (error: CancellationException) { throw error }
            catch (error: Exception) { message = error.message ?: "操作失败，请重试" }
            finally { busy = false; render() }
        }
    }

    private fun reportFailures(failures: List<String>, title: String) {
        if (failures.isEmpty()) return
        NxDialogBuilder(this).setTitle(title).setMessage(failures.take(8).joinToString("\n") +
            if (failures.size > 8) "\n另有 ${failures.size - 8} 项失败" else "")
            .setPositiveButton("确定", null).show()
    }

    private fun displayName(uri: Uri, fallback: String): String {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: fallback
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) name = it.getString(0) ?: name
        }
        return name
    }

    private fun button(parent: LinearLayout, label: String, enabled: Boolean = true, action: () -> Unit) {
        parent.addView(MaterialButton(this).apply {
            text = label; isAllCaps = false; isEnabled = enabled && !busy
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            setOnClickListener { if (!busy) action() }
        })
    }

    private fun compactButton(parent: LinearLayout, label: String, action: () -> Unit): MaterialButton {
        val button = MaterialButton(this).apply {
            text = label; isAllCaps = false; isEnabled = !busy
            textSize = 13f; insetTop = 0; insetBottom = 0
            setOnClickListener { if (!busy) action() }
        }
        parent.addView(button, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
            marginEnd = page.dp(4)
        })
        return button
    }

    private fun color(id: Int) = ContextCompat.getColor(this, id)

    private inner class CardLookAdapter(private val items: List<CardLutLibrary.Look>) :
        RecyclerView.Adapter<CardLookAdapter.Holder>() {
        init { setHasStableIds(true) }
        inner class Holder(val root: LinearLayout, val check: MaterialCheckBox, val labels: LinearLayout,
                     val more: androidx.appcompat.widget.AppCompatImageButton) : RecyclerView.ViewHolder(root)

        override fun getItemId(position: Int) = items[position].id.hashCode().toLong()
        override fun getItemCount() = items.size
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
            val root = LinearLayout(this@LutLibraryActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                minimumHeight = page.dp(74)
                setPadding(page.dp(2), page.dp(5), page.dp(2), page.dp(5))
                val attrs = obtainStyledAttributes(intArrayOf(android.R.attr.selectableItemBackground))
                background = attrs.getDrawable(0); attrs.recycle()
            }
            val check = MaterialCheckBox(this@LutLibraryActivity).apply { contentDescription = "选择 LUT" }
            val labels = LinearLayout(this@LutLibraryActivity).apply { orientation = LinearLayout.VERTICAL }
            val more = androidx.appcompat.widget.AppCompatImageButton(this@LutLibraryActivity).apply {
                setImageResource(R.drawable.ic_gallery_more)
                imageTintList = android.content.res.ColorStateList.valueOf(color(R.color.nx_on_surface))
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
                contentDescription = "更多操作"
            }
            root.addView(check, LinearLayout.LayoutParams(page.dp(48), page.dp(48)))
            root.addView(labels, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            root.addView(more, LinearLayout.LayoutParams(page.dp(48), page.dp(48)))
            return Holder(root, check, labels, more)
        }
        override fun onBindViewHolder(holder: Holder, position: Int) {
            val look = items[position]
            holder.check.setOnCheckedChangeListener(null)
            holder.check.isChecked = look.id in selectedCardIds
            holder.check.setOnCheckedChangeListener { _, checked ->
                if (checked) selectedCardIds += look.id else selectedCardIds -= look.id
                favoriteSelectedButton?.text = "收藏 ${selectedCardIds.size}"
                val all = items.isNotEmpty() && items.all { it.id in selectedCardIds }
                selectAllButton?.text = if (all) "取消全选" else "全选结果"
            }
            holder.labels.removeAllViews()
            holder.labels.addView(page.text((if (look.favorite) "★ " else "") + look.label, 16f))
            val categories = look.groups.joinToString("、", transform = CardLutFormat.Group::label).ifEmpty { "未分类" }
            holder.labels.addView(page.text("$categories · ${if (look.directoryPresent) "目录已部署" else "目录缺失"}", 12f, true))
            holder.root.setOnClickListener { holder.check.isChecked = !holder.check.isChecked }
            holder.more.setOnClickListener { cardLookActions(look) }
        }
    }
}
