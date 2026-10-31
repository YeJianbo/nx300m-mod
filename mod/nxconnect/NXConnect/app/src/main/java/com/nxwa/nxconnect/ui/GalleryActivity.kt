// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Intent
import android.os.Bundle
import android.animation.ValueAnimator
import android.os.Build
import android.provider.Settings
import android.view.View
import android.view.MenuItem
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.FrameLayout
import android.widget.EditText
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.databinding.ActivityGalleryBinding
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.transfer.DownloadService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Camera browsing with platform app-bar scrolling and grouped photo rows. */
class GalleryActivity : AppCompatActivity() {
    private val browsingCamera = NxApp.camera
    private lateinit var b: ActivityGalleryBinding
    private lateinit var mediaAdapter: MediaAdapter
    private lateinit var folderAdapter: FolderAdapter
    private lateinit var dragSelection: GalleryDragSelection
    private var browsingTitle = "照片"
    private var all = emptyList<MediaGroup>()
    private var folders = emptyList<String>()
    private var tab = 0
    private var folder: String? = null
    private var query = ""
    private var loading = false
    private var loaded = false
    private var pendingRestore: Bundle? = null
    private var appBarFraction = 0f
    private var appBarOffset = 0
    private var compactHeader = false
    private var expandedTitleWidth = 0f
    private var selectingPreviously = false
    private lateinit var searchAction: MenuItem
    private lateinit var menuAction: MenuItem

    private val photoPicker = registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.OpenMultipleDocuments()) {
        LensMetadata.editCopies(this, it)
    }
    private val viewerLauncher = registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.StartActivityForResult()) {
        if (it.resultCode == RESULT_OK && requireBrowsingCamera() != null) load()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityGalleryBinding.inflate(layoutInflater)
        setContentView(b.root)
        EdgeToEdge.setup(this)
        expandedTitleWidth = b.galleryHeading.paint.measureText(b.galleryHeading.text.toString())
        b.galleryCollapsing.layoutParams = b.galleryCollapsing.layoutParams.apply {
            height = if (resources.configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE) 0
                else dp((resources.configuration.screenHeightDp * .38f).toInt().coerceIn(220, 340))
        }

        b.navigationDock.source = b.galleryContent
        listOf(b.tabPhotos, b.tabAlbums, b.tabFavorites, b.tabMenu).forEach { GalleryChrome.press(it) }
        b.root.addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
            val available = (view.width - view.paddingLeft - view.paddingRight).coerceAtLeast(0)
            val width = minOf(dp(400), (available * .80f).toInt())
            if (width > 0 && b.navigationDock.layoutParams.width != width) {
                b.navigationDock.layoutParams = b.navigationDock.layoutParams.apply { this.width = width }
            }
        }

        ViewCompat.setOnApplyWindowInsetsListener(b.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        if (requireBrowsingCamera() == null) return
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                NxApp.connection.collect { if (it !== browsingCamera) finish() }
            }
        }

        pendingRestore = savedInstanceState
        tab = savedInstanceState?.getInt("galleryTab", 0)?.coerceIn(0, 2) ?: 0
        folder = savedInstanceState?.getString("galleryFolder")
        query = savedInstanceState?.getString("galleryQuery").orEmpty()

        folderAdapter = FolderAdapter { row ->
            pendingRestore = null
            folder = row.name
            render(animateContent = true)
            b.galleryAppBar.setExpanded(true, motionEnabled())
        }
        mediaAdapter = MediaAdapter(
            { openViewer(it) },
            { entry -> if (!dragSelection.start(mediaAdapter.mediaPosition(entry.key))) mediaAdapter.enterSelection(entry) },
            { renderSelection() }
        )

        dragSelection = GalleryDragSelection(b.list, mediaAdapter)

        b.toolbar.setNavigationIconTint(androidx.core.content.ContextCompat.getColor(this, R.color.nx_on_surface))
        b.toolbar.setNavigationOnClickListener { back() }
        searchAction = b.toolbar.menu.add("搜索").apply {
            setIcon(R.drawable.ic_search)
            setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_ALWAYS)
            setOnMenuItemClickListener { search(); true }
        }
        menuAction = b.toolbar.menu.add("更多").apply {
            setIcon(R.drawable.ic_gallery_more)
            setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS)
            setOnMenuItemClickListener { menu(); true }
        }

        b.swipe.setOnRefreshListener { load() }
        b.tabPhotos.setOnClickListener { chooseTab(0) }
        b.tabAlbums.setOnClickListener { chooseTab(1) }
        b.tabFavorites.setOnClickListener { chooseTab(2) }
        b.tabMenu.setOnClickListener { menu(b.tabMenu) }
        listOf(b.tabPhotos, b.tabAlbums, b.tabFavorites).forEachIndexed { index, view -> view.isSelected = index == tab }

        b.selectAllControl.setOnClickListener {
            if (!mediaAdapter.selectionMode) mediaAdapter.beginSelection()
            else if (mediaAdapter.mediaCount > 0 && mediaAdapter.selectedEntries().size == mediaAdapter.mediaCount) mediaAdapter.clearSelectedItems()
            else mediaAdapter.selectAll()
            renderSelection()
        }

        b.cancelSelection.setOnClickListener { dragSelection.stop(); mediaAdapter.clearSelection() }

        // 多选模式下批量补录镜头源信息
        b.btnBatchLens.setOnClickListener {
            val selected = mediaAdapter.selectedEntries().mapNotNull { group ->
                group.files.filter(PhotoEdits::supports).takeIf { it.isNotEmpty() }?.let { MediaGroup(it) }
            }
            val camera = requireBrowsingCamera() ?: return@setOnClickListener
            val ip = camera.ip
            if (selected.isEmpty()) {
                Toast.makeText(this, "所选项目中没有可编辑的 JPEG 或 SRW 照片", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            LensMetadata.prompt(this, selected.size) { metadata ->
                if (NxApp.camera !== camera) {
                    Toast.makeText(this, "相机连接已改变，请重新编辑", Toast.LENGTH_SHORT).show()
                    return@prompt
                }
                selected.forEach { PhotoEdits.update(this, it, metadata) }
                MediaDownloads.choose(this, selected) { files, _ ->
                    if (NxApp.camera !== camera) {
                        Toast.makeText(this, "修改已保存在本地，请重连后下载", Toast.LENGTH_SHORT).show()
                        return@choose
                    }
                    // Read each file's merged edits; a shared override would discard earlier adjustments.
                    DownloadService.start(this, ip, files, null)
                    mediaAdapter.clearSelection()
                    renderSelection()
                    Toast.makeText(this, "已加入下载队列，镜头信息将写入副本", Toast.LENGTH_SHORT).show()
                }
            }
        }

        b.btnDownload.setOnClickListener {
            val camera = requireBrowsingCamera() ?: return@setOnClickListener
            MediaDownloads.choose(this, mediaAdapter.selectedEntries()) { files, metadata ->
                if (requireBrowsingCamera() !== camera) return@choose
                DownloadService.start(this, camera.ip, files, metadata)
                mediaAdapter.clearSelection()
                renderSelection()
                Toast.makeText(this, "已加入下载队列", Toast.LENGTH_SHORT).show()
            }
        }

        b.galleryAppBar.addOnOffsetChangedListener(com.google.android.material.appbar.AppBarLayout.OnOffsetChangedListener { appBar, offset ->
            appBarOffset = offset
            appBarFraction = if (appBar.totalScrollRange > 0) -offset.toFloat() / appBar.totalScrollRange else 0f
            updateToolbarTitle()
        })
        b.swipe.setOnChildScrollUpCallback { _, _ -> appBarFraction > 0f || b.list.canScrollVertically(-1) }
        b.list.itemAnimator = null
        b.toolbar.post { updateToolbarTitle() }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = back()
        })

        load()
    }

    override fun onResume() {
        super.onResume()
        if (requireBrowsingCamera() == null) return
        if (loaded && tab == 2) render()
    }

    private fun requireBrowsingCamera(): NxCamera? {
        val camera = browsingCamera
        if (camera == null || NxApp.camera !== camera) {
            finish()
            return null
        }
        return camera
    }

    override fun onStop() {
        if (::dragSelection.isInitialized) dragSelection.stop()
        super.onStop()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        if (!::mediaAdapter.isInitialized) { super.onSaveInstanceState(outState); return }
        if (!loaded && pendingRestore != null) outState.putAll(pendingRestore!!)
        else {
            outState.putInt("galleryTab", tab)
            outState.putString("galleryFolder", folder)
            outState.putString("galleryQuery", query)
            outState.putParcelable("galleryScroll", b.list.layoutManager?.onSaveInstanceState())
            outState.putBoolean("gallerySelecting", mediaAdapter.selectionMode)
            outState.putStringArrayList("gallerySelection", ArrayList(mediaAdapter.selected))
        }
        super.onSaveInstanceState(outState)
    }

    @Suppress("DEPRECATION")
    private fun restoreBrowsingState() {
        val saved = pendingRestore ?: return
        pendingRestore = null
        b.list.layoutManager?.onRestoreInstanceState(saved.getParcelable("galleryScroll"))
        mediaAdapter.restoreSelection(saved.getStringArrayList("gallerySelection").orEmpty(), saved.getBoolean("gallerySelecting"))
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun chooseTab(value: Int) {
        if (tab == value && folder == null && query.isEmpty()) return
        pendingRestore = null
        tab = value
        folder = null
        query = ""
        mediaAdapter.clearSelection()

        render(animateContent = true)
        b.galleryAppBar.setExpanded(true, motionEnabled())
    }

    private fun back() {
        when {
            mediaAdapter.selectionMode -> { mediaAdapter.clearSelection(); renderSelection() }
            query.isNotEmpty() -> { pendingRestore = null; query = ""; render(animateContent = true) }
            folder != null -> { folder = null; render(animateContent = true) }
            else -> finish()
        }
    }

    private fun load() {
        if (loading) return
        val camera = requireBrowsingCamera() ?: return
        loading = true
        b.loading.visibility = View.VISIBLE
        b.empty.visibility = View.GONE

        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    val names = camera.listFolders()
                    val files = names.flatMap { camera.listMedia(it) }
                    names to MediaGroup.group(files).sortedByDescending { it.key }
                }
                if (requireBrowsingCamera() !== camera) return@launch
                folders = result.first
                all = result.second
                loaded = true
                render()
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                if (requireBrowsingCamera() !== camera) return@launch
                b.empty.visibility = View.VISIBLE
                b.emptyLabel.text = "无法读取照片\n${e.message}\n下拉刷新重试"
            } finally {
                loading = false
                b.loading.visibility = View.GONE
                b.swipe.isRefreshing = false
            }
        }
    }

    private fun render(animateContent: Boolean = false) {
        if (requireBrowsingCamera() == null) return
        val albums = tab == 1 && folder == null
        val title = folder ?: when (tab) {
            1 -> "相册"
            2 -> "收藏"
            else -> "照片"
        }
        browsingTitle = title
        b.galleryHeading.text = title
        expandedTitleWidth = b.galleryHeading.paint.measureText(title)
        updateToolbarTitle()

        val columns = if (albums) (resources.configuration.screenWidthDp / 140).coerceIn(3, 8)
            else (resources.configuration.screenWidthDp / 105).coerceIn(4, 8)
        val grid = b.list.layoutManager as? GridLayoutManager
        if (grid == null) b.list.layoutManager = GridLayoutManager(this, columns)
        else if (grid.spanCount != columns) grid.spanCount = columns
        (b.list.layoutManager as GridLayoutManager).spanSizeLookup = object : GridLayoutManager.SpanSizeLookup() {
            override fun getSpanSize(position: Int) = if (!albums && mediaAdapter.isHeader(position)) columns else 1
        }
        b.list.setPadding(if (albums) dp(8) else 0, headerContentPadding(), if (albums) dp(8) else 0, dp(88))
        val targetAdapter: RecyclerView.Adapter<*> = if (albums) folderAdapter else mediaAdapter
        val onCommitted: () -> Unit = committed@{
            if (b.list.adapter !== targetAdapter) return@committed
            if (animateContent) {
                b.list.scrollToPosition(0)
                b.list.animate().cancel()
                if (motionEnabled()) {
                    b.list.alpha = 0f
                    b.list.translationY = dp(12).toFloat()
                    b.list.animate().alpha(1f).translationY(0f).setDuration(220).setInterpolator(PathInterpolator(.22f, .25f, 0f, 1f)).start()
                } else { b.list.alpha = 1f; b.list.translationY = 0f }
            }
            restoreBrowsingState()
        }

        if (albums) {
            if (b.list.adapter !== folderAdapter) b.list.adapter = folderAdapter
            folderAdapter.submitList(folders.map { name ->
                val items = all.filter { it.folder == name }
                FolderRow(name, items.size, items.firstOrNull())
            }) { onCommitted() }
        } else {
            if (b.list.adapter !== mediaAdapter) b.list.adapter = mediaAdapter
            val shown = all.filter {
                (folder == null || it.folder == folder) &&
                (tab != 2 || GalleryFavorites.contains(this, it)) &&
                (query.isEmpty() || it.displayName.contains(query, true))
            }
            mediaAdapter.submitList(shown, onCommitted)
            b.empty.visibility = if (shown.isEmpty()) View.VISIBLE else View.GONE
            b.emptyLabel.text = when {
                query.isNotEmpty() -> "未找到包含“$query”的照片"
                tab == 2 -> "尚未收藏照片\n浏览照片时点击心形图标即可收藏"
                else -> "相机中没有照片"
            }
        }

        if (albums) {
            b.empty.visibility = if (folders.isEmpty()) View.VISIBLE else View.GONE
            b.emptyLabel.text = "相机中没有相册目录"
        }

        listOf(b.tabPhotos, b.tabAlbums, b.tabFavorites).forEachIndexed { index, view ->
            view.isSelected = index == tab
            val color = androidx.core.content.ContextCompat.getColor(this,
                R.color.nx_on_surface)
            (view.getChildAt(0) as ImageView).setColorFilter(color)
            (view.getChildAt(1) as TextView).setTextColor(color)
            (view.getChildAt(1) as TextView).typeface = android.graphics.Typeface.create(
                if (index == tab) "sans-serif-medium" else "sans-serif", android.graphics.Typeface.NORMAL)
        }
        renderSelection()
    }

    private fun renderSelection() {
        val count = mediaAdapter.selectedCount
        val selecting = mediaAdapter.selectionMode
        if (selecting != selectingPreviously) {
            selectingPreviously = selecting
            if (!selecting) dragSelection.stop()
            animateBar(b.bottomBar, selecting)
            animateBar(b.navigationDock, !selecting)
            b.navigationDock.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
        }
        configureToolbar(selecting)
        b.crumbRow.visibility = if (selecting) View.VISIBLE else View.GONE
        b.toolbar.visibility = if (selecting) View.GONE else View.VISIBLE
        val heading = if (selecting) "已选中 $count 个" else browsingTitle
        if (b.galleryHeading.text != heading) {
            b.galleryHeading.text = heading
            expandedTitleWidth = b.galleryHeading.paint.measureText(heading)
        }
        val topPadding = headerContentPadding()
        if (b.list.paddingTop != topPadding) b.list.setPadding(b.list.paddingLeft, topPadding, b.list.paddingRight, b.list.paddingBottom)
        b.swipe.isEnabled = !selecting
        searchAction.isVisible = !selecting
        menuAction.isVisible = !selecting
        b.btnDownload.isEnabled = count > 0
        b.btnBatchLens.isEnabled = count > 0
        val allSelected = count > 0 && count == mediaAdapter.mediaCount
        b.selectAllControl.isEnabled = mediaAdapter.mediaCount > 0
        b.selectAllControl.contentDescription = if (allSelected) "取消选择全部照片" else "选择全部照片"
        b.selectAllCheck.isSelected = allSelected
        b.selectAllCheck.imageAlpha = if (allSelected) 255 else 0
        b.selectAllText.text = "全部"
        b.selectionCompactTitle.text = "已选中 $count 个"
        updateToolbarTitle()
    }

    private fun updateToolbarTitle() {
        val selecting = mediaAdapter.selectionMode
        val range = b.galleryAppBar.totalScrollRange
        val disabled = b.galleryCollapsing.layoutParams.height == 0
        val compact = disabled || (range > 0 && -appBarOffset >= range)
        if (compactHeader != compact) {
            compactHeader = compact
            configureToolbar(selecting)
        }
        val title = if (!selecting && compact) b.galleryHeading.text else ""
        if (b.toolbar.title != title) b.toolbar.title = title
        b.toolbar.translationY = if (selecting || compact) 0f
            else (b.galleryAppBar.height + appBarOffset - dp(56)).coerceAtLeast(0) + dp(4) * appBarFraction
        b.toolbar.translationX = if (selecting || compact) 0f else dp(12) * appBarFraction
        b.headingContainer.alpha = 1f
        b.crumbRow.translationY = if (compact) 0f else (b.galleryAppBar.height + appBarOffset - dp(56)).coerceAtLeast(0).toFloat()
        b.selectionCompactTitle.visibility = if (compact) View.VISIBLE else View.INVISIBLE
        b.headingContainer.visibility = if (compact) View.INVISIBLE else View.VISIBLE
        if (!compact && b.galleryHeading.width > 0) {
            val progress = appBarFraction.coerceIn(0f, 1f)
            val scale = 1f + (20f / 30f - 1f) * progress
            val expandedCenterY = b.headingContainer.top + b.galleryHeading.top + b.galleryHeading.height / 2f
            val expandedCenterX = b.headingContainer.left + b.galleryHeading.left + b.galleryHeading.width / 2f
            val titleWidth = expandedTitleWidth.coerceAtMost(b.galleryHeading.width.toFloat()) * (20f / 30f)
            val compactLeft = dp(if (selecting || folder != null || query.isNotEmpty()) 72 else 24)
            val available = (b.galleryAppBar.width - compactLeft - dp(116)).coerceAtLeast(0)
            val compactCenterX = compactLeft + minOf(titleWidth, available.toFloat()) / 2f
            b.headingContainer.translationY = -appBarOffset + (dp(28) - expandedCenterY) * progress
            b.galleryHeading.translationX = (compactCenterX - expandedCenterX) * progress
            b.galleryHeading.scaleX = scale
            b.galleryHeading.scaleY = scale
        }
        if (!selecting && !compact) b.toolbar.background?.alpha = (appBarFraction * 255).toInt().coerceIn(0, 255)
    }

    private fun headerContentPadding() = if (b.galleryCollapsing.layoutParams.height == 0) dp(56) else 0

    private fun configureToolbar(selecting: Boolean) {
        val nested = folder != null || query.isNotEmpty()
        val fullToolbar = selecting || compactHeader
        val targetWidth = if (fullToolbar) ViewGroup.LayoutParams.MATCH_PARENT else dp(if (nested) 152 else 104)
        val targetHeight = dp(if (fullToolbar) 56 else 48)
        val params = b.toolbar.layoutParams as FrameLayout.LayoutParams
        val margin = if (fullToolbar) 0 else dp(12)
        if (params.width != targetWidth || params.height != targetHeight || params.marginEnd != margin) {
            params.width = targetWidth; params.height = targetHeight; params.marginEnd = margin
            b.toolbar.layoutParams = params
        }
        when {
            selecting -> b.toolbar.setNavigationIcon(R.drawable.ic_gallery_close)
            nested -> b.toolbar.setNavigationIcon(R.drawable.ic_arrow_back)
            else -> b.toolbar.navigationIcon = null
        }
        b.toolbar.navigationContentDescription = if (selecting) "取消选择" else "返回"
        b.toolbar.setContentInsetsRelative(if (compactHeader && !nested && !selecting) dp(24) else 0, if (selecting) dp(88) else 0)
        b.toolbar.setBackgroundResource(if (fullToolbar) R.color.gallery_page else R.drawable.bg_oneui_floating_toolbar)
    }

    private fun motionEnabled() = if (Build.VERSION.SDK_INT >= 26) ValueAnimator.areAnimatorsEnabled()
        else Settings.Global.getFloat(contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) > 0f

    private fun animateBar(view: View, show: Boolean) {
        view.animate().cancel()
        if (!motionEnabled()) {
            view.visibility = if (show) View.VISIBLE else View.GONE
            view.alpha = 1f; view.translationY = 0f
            return
        }
        if (show) {
            view.visibility = View.VISIBLE
            view.alpha = 0f; view.translationY = dp(24).toFloat()
            view.animate().alpha(1f).translationY(0f).setDuration(220).setInterpolator(PathInterpolator(.22f, .25f, 0f, 1f)).withEndAction(null).start()
        } else {
            view.animate().alpha(0f).translationY(dp(24).toFloat()).setDuration(160).setInterpolator(PathInterpolator(.22f, .25f, 0f, 1f)).withEndAction {
                view.visibility = View.GONE
                view.alpha = 1f; view.translationY = 0f
            }.start()
        }
    }

    private fun search() {
        val input = EditText(this).apply {
            hint = "照片文件名 (如 SAM_4262)"
            setSingleLine()
            setText(query)
            setPadding(dp(24), dp(16), dp(24), dp(16))
        }
        NxDialogBuilder(this)
            .setTitle("搜索照片")
            .setView(input)
            .setPositiveButton("搜索") { _, _ ->
                pendingRestore = null
                mediaAdapter.clearSelection()
                query = input.text.toString().trim()
                tab = 0
                folder = null
                render(animateContent = true)
            }
            .setNeutralButton("清除") { _, _ ->
                pendingRestore = null
                query = ""
                render()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun menu(anchor: View = b.toolbar) {
        if (requireBrowsingCamera() == null) return
        val items = mutableListOf<Pair<String, () -> Unit>>()
        if (b.list.adapter === mediaAdapter && mediaAdapter.mediaCount > 0) {
            items.add("选择照片" to { mediaAdapter.beginSelection(); renderSelection() })
        }
        items.addAll(listOf(
            "相机设置" to {
                requireBrowsingCamera()?.let { camera ->
                    ExposureSettingsDialog(this, camera) {
                        startActivity(Intent(this, RemoteActivity::class.java).putExtra("open_stock_iso", true))
                    }.show(section = ExposureSettingsDialog.Section.CONTROLS)
                }
            },
            "刷新照片" to { load() },
            "批量补充镜头源信息" to { photoPicker.launch(arrayOf("image/jpeg")) },
            "远程拍摄遥控" to { startActivity(Intent(this, RemoteActivity::class.java)) },
            "连接与外观设置" to { finish() }
        ))
        GalleryChrome.menu(this, anchor, items)
    }

    private fun openViewer(group: MediaGroup) {
        val camera = requireBrowsingCamera() ?: return
        if (!group.isImage) {
            MediaDownloads.choose(this, listOf(group)) { files, metadata ->
                if (requireBrowsingCamera() === camera) DownloadService.start(this, camera.ip, files, metadata)
            }
            return
        }
        val images = mediaAdapter.currentList.filter { it.isImage }
        val index = images.indexOfFirst { it.key == group.key }
        if (index < 0) return
        val session = com.nxwa.nxconnect.data.ViewerSessions.create(images)
        try {
            viewerLauncher.launch(
                Intent(this, ViewerActivity::class.java)
                    .putExtra(ViewerActivity.EXTRA_SESSION, session)
                    .putExtra(ViewerActivity.EXTRA_INDEX, index)
                    .putExtra("source_key", group.key)
                    .apply { mediaAdapter.sourceBoundsFor(group.key)?.let { putExtra("source_bounds", it) } }
            )
        } catch (error: Exception) {
            com.nxwa.nxconnect.data.ViewerSessions.release(session)
            throw error
        }
    }
}
