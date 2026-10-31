// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.databinding.ActivityGalleryBinding
import com.nxwa.nxconnect.transfer.DownloadService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Samsung OneUI 9 风格相机相册页面。
 * 支持大标题平滑折叠呼吸感顶栏、悬浮液态玻璃药丸 Dock、时间轴网格、多选批量镜头 EXIF 补录与下载。
 */
class GalleryActivity : AppCompatActivity() {
    private lateinit var b: ActivityGalleryBinding
    private lateinit var mediaAdapter: MediaAdapter
    private lateinit var folderAdapter: FolderAdapter
    private var all = emptyList<MediaGroup>()
    private var folders = emptyList<String>()
    private var tab = 0
    private var folder: String? = null
    private var query = ""
    private var loading = false
    private var loaded = false
    private var pendingRestore: Bundle? = null
    private var maxHeadingHeight = 0

    private val photoPicker = registerForActivityResult(androidx.activity.result.contract.ActivityResultContracts.OpenMultipleDocuments()) {
        LensMetadata.editCopies(this, it)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityGalleryBinding.inflate(layoutInflater)
        setContentView(b.root)
        EdgeToEdge.setup(this)

        b.navigationDock.source = b.galleryContent
        b.root.addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
            val width = minOf(dp(440), view.width - view.paddingLeft - view.paddingRight - dp(32)).coerceAtLeast(0)
            if (width > 0 && b.navigationDock.layoutParams.width != width) {
                b.navigationDock.layoutParams = b.navigationDock.layoutParams.apply { this.width = width }
            }
        }
        listOf(b.tabPhotos, b.tabAlbums, b.tabFavorites, b.tabMenu).forEach { GalleryChrome.press(it) }

        ViewCompat.setOnApplyWindowInsetsListener(b.root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        if (!NxApp.isConnected) { finish(); return }

        pendingRestore = savedInstanceState
        tab = savedInstanceState?.getInt("galleryTab", 0)?.coerceIn(0, 2) ?: 0
        folder = savedInstanceState?.getString("galleryFolder")
        query = savedInstanceState?.getString("galleryQuery").orEmpty()

        folderAdapter = FolderAdapter { row -> folder = row.name; render() }
        mediaAdapter = MediaAdapter(
            { openViewer(it) },
            { mediaAdapter.enterSelection(it); renderSelection() },
            { renderSelection() }
        )

        b.toolbar.setNavigationIconTint(androidx.core.content.ContextCompat.getColor(this, R.color.nx_on_surface))
        b.toolbar.setNavigationOnClickListener { back() }
        b.toolbar.menu.add("搜索").apply {
            setIcon(R.drawable.ic_search)
            setShowAsAction(android.view.MenuItem.SHOW_AS_ACTION_ALWAYS)
            setOnMenuItemClickListener { search(); true }
        }
        b.toolbar.menu.add("刷新").setOnMenuItemClickListener { load(); true }

        b.swipe.setOnRefreshListener { load() }
        b.tabPhotos.setOnClickListener { chooseTab(0) }
        b.tabAlbums.setOnClickListener { chooseTab(1) }
        b.tabFavorites.setOnClickListener { chooseTab(2) }
        b.tabMenu.setOnClickListener { menu() }

        b.selectAllText.setOnClickListener {
            if (!mediaAdapter.selectionMode) mediaAdapter.beginSelection()
            else if (mediaAdapter.itemCount > 0 && mediaAdapter.selectedEntries().size == mediaAdapter.itemCount) mediaAdapter.clearSelectedItems()
            else mediaAdapter.selectAll()
            renderSelection()
        }

        b.btnClear.setOnClickListener { mediaAdapter.clearSelection(); renderSelection() }

        // 多选模式下批量补录镜头源信息
        b.btnBatchLens.setOnClickListener {
            val selected = mediaAdapter.selectedEntries()
            val jpegs = selected.mapNotNull { it.jpeg }
            val ip = NxApp.cameraIp ?: return@setOnClickListener
            if (jpegs.isEmpty()) {
                Toast.makeText(this, "所选项目中没有可写入 EXIF 的 JPEG 原片", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            LensMetadata.prompt(this, jpegs.size) { metadata ->
                DownloadService.start(this, ip, jpegs, metadata)
                mediaAdapter.clearSelection()
                renderSelection()
                Toast.makeText(this, "正在批量保存带镜头源信息的原片到手机相册", Toast.LENGTH_SHORT).show()
            }
        }

        b.btnDownload.setOnClickListener {
            MediaDownloads.choose(this, mediaAdapter.selectedEntries()) { files, metadata ->
                val ip = NxApp.cameraIp ?: return@choose
                DownloadService.start(this, ip, files, metadata)
                mediaAdapter.clearSelection()
                renderSelection()
                Toast.makeText(this, "已加入下载队列", Toast.LENGTH_SHORT).show()
            }
        }

        b.headingContainer.post {
            maxHeadingHeight = b.headingContainer.height
        }

        // 联动滚动：OneUI 经典两段式折叠大标题
        b.list.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recycler: RecyclerView, dx: Int, dy: Int) {
                b.navigationDock.invalidate()
                if (dy == 0) return
                if (maxHeadingHeight <= 0) maxHeadingHeight = b.headingContainer.height.coerceAtLeast(dp(64))

                val current = b.headingContainer.layoutParams.height.let { if (it > 0) it else b.headingContainer.height }
                val next = (current - dy).coerceIn(0, maxHeadingHeight)
                if (next != current) {
                    b.headingContainer.layoutParams = b.headingContainer.layoutParams.apply { height = next }
                    val fraction = if (maxHeadingHeight > 0) next.toFloat() / maxHeadingHeight else 1f
                    b.headingContainer.alpha = fraction
                }
                b.toolbar.title = if (next < dp(20)) b.galleryHeading.text else ""
            }
        })

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = back()
        })

        load()
    }

    override fun onResume() {
        super.onResume()
        if (loaded && tab == 2) render()
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

        // 恢复大标题展开状态
        if (maxHeadingHeight > 0) {
            b.headingContainer.layoutParams = b.headingContainer.layoutParams.apply { height = maxHeadingHeight }
            b.headingContainer.alpha = 1f
        }
        b.toolbar.title = ""
        render()
    }

    private fun back() {
        when {
            mediaAdapter.selectionMode -> { mediaAdapter.clearSelection(); renderSelection() }
            query.isNotEmpty() -> { pendingRestore = null; query = ""; render() }
            folder != null -> { folder = null; render() }
            else -> finish()
        }
    }

    private fun load() {
        if (loading) return
        val camera = NxApp.camera ?: return
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
                folders = result.first
                all = result.second
                loaded = true
                render()
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                b.empty.visibility = View.VISIBLE
                b.emptyLabel.text = "无法读取照片\n${e.message}\n下拉刷新重试"
            } finally {
                loading = false
                b.loading.visibility = View.GONE
                b.swipe.isRefreshing = false
            }
        }
    }

    private fun render() {
        val albums = tab == 1 && folder == null
        val title = folder ?: when (tab) {
            1 -> "相册"
            2 -> "收藏"
            else -> "照片"
        }
        b.galleryHeading.text = title
        if (b.headingContainer.height < dp(20) && b.headingContainer.layoutParams.height < dp(20)) {
            b.toolbar.title = title
        }

        b.crumbText.text = if (query.isNotEmpty()) "搜索结果：$query"
        else if (albums) "全部相册"
        else folder ?: if (tab == 2) "收藏标记的照片" else "按拍摄时间"

        b.selectAllText.visibility = if (albums) View.GONE else View.VISIBLE

        val columns = if (albums) 1 else (resources.configuration.screenWidthDp / 96).coerceIn(3, 8)
        val grid = b.list.layoutManager as? GridLayoutManager
        if (grid == null) b.list.layoutManager = GridLayoutManager(this, columns)
        else if (grid.spanCount != columns) grid.spanCount = columns

        if (albums) {
            b.gallerySubtitle.text = "共 ${folders.size} 个相册目录"
            if (b.list.adapter !== folderAdapter) b.list.adapter = folderAdapter
            folderAdapter.submitList(folders.map { name ->
                val items = all.filter { it.folder == name }
                FolderRow(name, items.size, items.firstOrNull())
            }) { restoreBrowsingState() }
        } else {
            if (b.list.adapter !== mediaAdapter) b.list.adapter = mediaAdapter
            val shown = all.filter {
                (folder == null || it.folder == folder) &&
                (tab != 2 || GalleryFavorites.contains(this, it)) &&
                (query.isEmpty() || it.displayName.contains(query, true))
            }
            b.gallerySubtitle.text = when {
                query.isNotEmpty() -> "找到 ${shown.size} 项匹配"
                tab == 2 -> "共 ${shown.size} 项收藏照片"
                folder != null -> "$folder · 共 ${shown.size} 项"
                else -> "相机机身 · 共 ${shown.size} 张照片"
            }
            mediaAdapter.submitList(shown) { restoreBrowsingState() }
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
            view.setBackgroundResource(if (index == tab) R.drawable.bg_gallery_selected else R.drawable.bg_gallery_touch)
            view.isSelected = index == tab
        }
        renderSelection()
    }

    private fun renderSelection() {
        val count = mediaAdapter.selectedEntries().size
        val selecting = mediaAdapter.selectionMode
        b.bottomBar.visibility = if (selecting) View.VISIBLE else View.GONE
        b.navigationDock.visibility = if (selecting) View.GONE else View.VISIBLE
        b.btnDownload.isEnabled = count > 0
        b.btnBatchLens.isEnabled = count > 0
        b.selectedCount.text = "已选择 $count 项"
        b.selectAllText.isEnabled = mediaAdapter.itemCount > 0
        b.selectAllText.text = when {
            !selecting -> "选择"
            count > 0 && count == mediaAdapter.itemCount -> "取消全选"
            else -> "全选"
        }
    }

    private fun search() {
        val input = EditText(this).apply {
            hint = "照片文件名 (如 SAM_4262)"
            setSingleLine()
            setText(query)
            setPadding(dp(24), dp(16), dp(24), dp(16))
        }
        MaterialAlertDialogBuilder(this)
            .setTitle("搜索照片")
            .setView(input)
            .setPositiveButton("搜索") { _, _ ->
                pendingRestore = null
                mediaAdapter.clearSelection()
                query = input.text.toString().trim()
                tab = 0
                folder = null
                render()
            }
            .setNeutralButton("清除") { _, _ ->
                pendingRestore = null
                query = ""
                render()
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun menu() {
        GalleryChrome.menu(this, b.tabMenu, listOf(
            "刷新照片" to { load() },
            "批量补充镜头源信息" to { photoPicker.launch(arrayOf("image/jpeg")) },
            "远程拍摄遥控" to { startActivity(Intent(this, RemoteActivity::class.java)) },
            "连接与外观设置" to { finish() }
        ))
    }

    private fun openViewer(group: MediaGroup) {
        if (!group.isImage) {
            MediaDownloads.choose(this, listOf(group)) { files, metadata ->
                NxApp.cameraIp?.let { DownloadService.start(this, it, files, metadata) }
            }
            return
        }
        val images = mediaAdapter.currentList.filter { it.isImage }
        val session = com.nxwa.nxconnect.data.ViewerSessions.create(images)
        try {
            startActivity(
                Intent(this, ViewerActivity::class.java)
                    .putExtra(ViewerActivity.EXTRA_SESSION, session)
                    .putExtra(ViewerActivity.EXTRA_INDEX, images.indexOf(group).coerceAtLeast(0))
            )
        } catch (error: Exception) {
            com.nxwa.nxconnect.data.ViewerSessions.release(session)
            throw error
        }
    }
}
