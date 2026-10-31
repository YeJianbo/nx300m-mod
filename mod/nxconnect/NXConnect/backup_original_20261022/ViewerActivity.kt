// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.animation.ValueAnimator
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.animation.PathInterpolator
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.MediaEntry
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.databinding.ActivityViewerBinding
import com.nxwa.nxconnect.transfer.DownloadService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Samsung OneUI 9 风格大图沉浸式查看器。
 * 支持纯黑无界看图、平滑收放顶底工具栏、一比一上滑拉出五联曝光胶囊与镜头卡片详情抽屉、原片 EXIF 补录与写入。
 */
class ViewerActivity : AppCompatActivity() {
    companion object {
        const val EXTRA_FOLDER = "folder"
        const val EXTRA_NAMES = "names"
        const val EXTRA_PATHS = "paths"
        const val EXTRA_INDEX = "index"
        const val EXTRA_SIZES = "sizes"
        const val EXTRA_SESSION = "viewer_session"
    }

    private lateinit var b: ActivityViewerBinding
    private var images = emptyList<MediaGroup>()
    private var adapter: ImagePagerAdapter? = null
    private var detailJob: Job? = null
    private val infoCache = mutableMapOf<String, SourcePhotoInfo>()
    private var detailKey: String? = null
    private var progress = 0f
    private var gestureProgress = 0f
    private var motion: ValueAnimator? = null
    private var chromeVisible = true
    private var chromeReveal = 1f
    private var chromeMotion: ValueAnimator? = null
    private var detailsDragging = false
    private var sharing = false
    private var editing = false
    private val filmstrip = Filmstrip()
    private val current: MediaGroup? get() = images.getOrNull(b.pager.currentItem)
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityViewerBinding.inflate(layoutInflater)
        setContentView(b.root)

        // 纯黑沉浸式 Edge-to-Edge：状态栏和导航栏文字图标统一为浅色（白色）
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = false
            isAppearanceLightNavigationBars = false
        }

        // 舞台无黑白断层，通过 Insets 动态避让顶部返回与底部 Dock，根容器不加 paddingTop/Bottom
        ViewCompat.setOnApplyWindowInsetsListener(b.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            (b.backGlass.layoutParams as? ViewGroup.MarginLayoutParams)?.let { lp ->
                val topTarget = dp(16) + bars.top
                if (lp.topMargin != topTarget) {
                    lp.topMargin = topTarget
                    b.backGlass.layoutParams = lp
                }
            }
            val bottomTarget = dp(10) + bars.bottom
            if (b.bottomChrome.paddingBottom != bottomTarget) {
                b.bottomChrome.setPadding(0, 0, 0, bottomTarget)
            }
            val sheetBottomTarget = dp(42) + bars.bottom
            val innerLinear = b.detailsScroll.getChildAt(0) as? ViewGroup
            if (innerLinear != null && innerLinear.paddingBottom != sheetBottomTarget) {
                innerLinear.setPadding(innerLinear.paddingStart, innerLinear.paddingTop, innerLinear.paddingEnd, sheetBottomTarget)
            }
            insets
        }

        val session = intent.getStringExtra(EXTRA_SESSION)
        images = if (session != null) com.nxwa.nxconnect.data.ViewerSessions.get(session).orEmpty() else {
            val paths = intent.getStringArrayExtra(EXTRA_PATHS)
                ?: intent.getStringArrayExtra(EXTRA_NAMES)?.map { "${intent.getStringExtra(EXTRA_FOLDER).orEmpty()}/$it" }?.toTypedArray()
                ?: emptyArray()
            val sizes = intent.getLongArrayExtra(EXTRA_SIZES) ?: longArrayOf()
            MediaGroup.group(paths.mapIndexed { index, path ->
                MediaEntry(path.substringBeforeLast('/', ""), path.substringAfterLast('/'), sizes.getOrElse(index) { -1 })
            })
        }
        if (images.isEmpty() || !NxApp.isConnected) { finish(); return }

        adapter = ImagePagerAdapter(
            images,
            onInfo = { showDetails(true) },
            onTap = { toggleChrome() },
            onDrag = { delta, end, velocity, cancelled ->
                if (delta == 0f && !end) {
                    motion?.cancel()
                    gestureProgress = progress
                    detailsDragging = true
                }
                val distance = detailsSheetHeight.toFloat().coerceAtLeast(1f)
                val next = (gestureProgress - delta / distance).coerceIn(0f, 1f)
                if (end) {
                    detailsDragging = false
                    if (cancelled) showDetails(progress > .5f) else settleDetails(next, velocity, gestureProgress)
                } else setProgress(next)
            },
            onReady = {
                setProgress(progress)
                b.actionDock.invalidate()
                b.backGlass.invalidate()
            }
        )
        b.pager.adapter = adapter
        b.pager.setCurrentItem(
            (savedInstanceState?.getInt("index") ?: intent.getIntExtra(EXTRA_INDEX, 0)).coerceIn(0, images.lastIndex),
            false
        )

        b.filmstrip.layoutManager = LinearLayoutManager(this, RecyclerView.HORIZONTAL, false)
        b.filmstrip.adapter = filmstrip
        b.filmstrip.post {
            val side = ((b.filmstrip.width - dp(42)) / 2).coerceAtLeast(0)
            b.filmstrip.setPadding(side, 0, side, 0)
            (b.filmstrip.layoutManager as LinearLayoutManager).scrollToPositionWithOffset(b.pager.currentItem, 0)
        }

        b.actionDock.source = b.viewerContent
        b.backGlass.source = b.viewerContent
        listOf(b.btnBack, b.btnFavorite, b.btnEdit, b.btnInfo, b.btnShare, b.btnDownload, b.btnMore).forEach {
            GalleryChrome.press(it)
        }

        var pullStart = 1f
        b.detailsScroll.onPull = { delta, end, velocity, cancelled ->
            if (delta == 0f && !end) { motion?.cancel(); pullStart = progress; detailsDragging = true }
            val next = (pullStart - delta / detailsSheetHeight).coerceIn(0f, 1f)
            if (end) {
                detailsDragging = false
                if (cancelled) showDetails(progress > .5f) else settleDetails(next, velocity, pullStart)
            } else setProgress(next)
        }

        b.exifToggle.setOnClickListener {
            val expanded = b.allExif.visibility != View.VISIBLE
            b.allExif.visibility = if (expanded) View.VISIBLE else View.GONE
            b.exifToggle.text = if (expanded) "收起完整 EXIF 属性" else "展开完整 EXIF 属性"
        }

        b.btnBack.setOnClickListener { back() }
        b.btnFavorite.setOnClickListener { current?.let { GalleryFavorites.toggle(this, it); updateFavorite() } }
        b.btnInfo.setOnClickListener { showDetails(progress < .5f) }
        b.btnEdit.setOnClickListener { edit() }
        b.infoEdit.setOnClickListener { edit() }
        b.btnDownload.setOnClickListener { download() }
        b.btnShare.setOnClickListener { share() }
        b.btnMore.setOnClickListener {
            GalleryChrome.menu(this, it, listOf(
                "详细信息" to { showDetails(true) },
                "编辑镜头信息" to { edit() },
                "还原信息修改" to { restoreEdits() },
                "下载原文件" to { download() }
            ))
        }

        // 五联曝光胶囊与镜头卡片点击：一键直达老镜头与曝光补录
        listOf(
            b.capsuleApertureCol,
            b.capsuleFocalCol,
            b.rowLens,
            b.infoLensAction
        ).forEach { view ->
            view.setOnClickListener { edit() }
        }

        b.pager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                updateFavorite()
                filmstrip.select(position)
                (b.filmstrip.layoutManager as LinearLayoutManager).scrollToPositionWithOffset(position, 0)
                if (progress > 0f) loadInfo()
                b.actionDock.invalidate()
                b.backGlass.invalidate()
            }
        })

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = back()
        })

        updateFavorite()
        filmstrip.select(b.pager.currentItem)
        chromeVisible = savedInstanceState?.getBoolean("chromeVisible", true) ?: true
        chromeReveal = if (chromeVisible) 1f else 0f
        b.viewerContent.post {
            // 初始化抽屉卡片固定高度（屏幕高度的 58%），避免在动画中触发布局测量
            b.detailsScroll.layoutParams = (b.detailsScroll.layoutParams as FrameLayout.LayoutParams).apply {
                height = detailsSheetHeight
                gravity = Gravity.BOTTOM
            }
            setProgress(if (savedInstanceState?.getBoolean("detailsOpen") == true) 1f else 0f)
            if (progress > 0f) loadInfo()
        }
    }

    override fun onSaveInstanceState(out: Bundle) {
        out.putInt("index", b.pager.currentItem)
        out.putBoolean("detailsOpen", progress > .5f)
        out.putBoolean("chromeVisible", chromeVisible)
        super.onSaveInstanceState(out)
    }

    private fun back() {
        if (progress > .01f) showDetails(false) else finish()
    }

    private fun photoGeometry(): Pair<Float, Float> {
        val height = b.imageStage.height.toFloat().coerceAtLeast(1f)
        val width = b.imageStage.width.toFloat().coerceAtLeast(1f)
        val holder = (b.pager.getChildAt(0) as? RecyclerView)
            ?.findViewHolderForAdapterPosition(b.pager.currentItem) as? ImagePagerAdapter.VH
        val drawable = holder?.image?.drawable
        val naturalHeight = if (drawable != null && drawable.intrinsicWidth > 0 && drawable.intrinsicHeight > 0) {
            drawable.intrinsicHeight * minOf(width / drawable.intrinsicWidth, height / drawable.intrinsicHeight)
        } else height * .35f
        val top = (ViewCompat.getRootWindowInsets(b.root)?.getInsets(
            WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())?.top ?: 0) + dp(8)
        val photoHeight = minOf(naturalHeight, (height * .43f - top).coerceAtLeast(dp(80).toFloat()))
        return photoHeight / naturalHeight to (top + photoHeight)
    }

    private val detailsSheetHeight: Int
        get() = (b.root.height - photoGeometry().second - dp(12)).toInt().coerceAtLeast(1)

    private fun setProgress(value: Float) {
        val opening = progress <= .001f && value > .001f
        progress = value
        val sheetH = detailsSheetHeight
        if (sheetH <= 1) return

        if (value > 0.001f || detailsDragging) {
            if (opening) loadInfo()
            b.detailsScroll.visibility = View.VISIBLE
        } else {
            b.detailsScroll.visibility = View.GONE
        }

        val sheetParams = b.detailsScroll.layoutParams as FrameLayout.LayoutParams
        if (sheetParams.height != sheetH) {
            sheetParams.height = sheetH
            b.detailsScroll.layoutParams = sheetParams
        }
        b.detailsScroll.translationY = (1f - value) * sheetH
        b.detailsScroll.alpha = (value * 2.2f).coerceIn(0f, 1f)

        // Move the complete photo above its details, using the same drag progress.
        val (targetScale, photoBottom) = photoGeometry()
        val scale = 1f + (targetScale - 1f) * value
        b.imageStage.pivotX = b.imageStage.width / 2f
        b.imageStage.pivotY = b.imageStage.height / 2f
        b.imageStage.scaleX = scale
        b.imageStage.scaleY = scale
        val top = (ViewCompat.getRootWindowInsets(b.root)?.getInsets(
            WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())?.top ?: 0) + dp(8)
        b.imageStage.translationY = ((top + photoBottom) / 2f - b.imageStage.height / 2f) * value

        renderChrome()
    }

    private fun renderChrome() {
        val alpha = chromeReveal * (1f - progress * 2.5f).coerceIn(0f, 1f)
        b.bottomChrome.translationY = dp(80) * (1f - chromeReveal) + dp(90) * progress
        b.backGlass.translationY = -dp(30) * (1f - chromeReveal) - dp(30) * progress
        listOf(b.bottomChrome, b.backGlass).forEach { view ->
            view.alpha = alpha
            view.visibility = if (alpha > .001f) View.VISIBLE else View.INVISIBLE
            view.importantForAccessibility = if (alpha > .5f) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }
        b.actionDock.invalidate()
        b.backGlass.invalidate()
    }

    private fun animationsEnabled() = android.os.Build.VERSION.SDK_INT < 26 || ValueAnimator.areAnimatorsEnabled()

    private fun settleDetails(next: Float, velocity: Float, start: Float) {
        val open = if (kotlin.math.abs(velocity) > dp(650)) velocity < 0f
            else if (start > .5f) next > .72f else next > .28f
        showDetails(open)
    }

    private fun showDetails(open: Boolean) {
        motion?.cancel()
        detailsDragging = false
        if (open) loadInfo()
        val target = if (open) 1f else 0f
        if (!animationsEnabled() || kotlin.math.abs(target - progress) < .001f) {
            setProgress(target)
            return
        }
        motion = ValueAnimator.ofFloat(progress, target).apply {
            duration = (100 + kotlin.math.abs(target - progress) * 180).toLong()
            interpolator = PathInterpolator(0.22f, 0.25f, 0f, 1f)
            addUpdateListener { setProgress(it.animatedValue as Float) }
            start()
        }
    }

    private fun toggleChrome() {
        if (progress > .01f || detailsDragging) return
        chromeVisible = !chromeVisible
        chromeMotion?.cancel()
        val target = if (chromeVisible) 1f else 0f
        if (!animationsEnabled()) {
            chromeReveal = target
            renderChrome()
            return
        }
        chromeMotion = ValueAnimator.ofFloat(chromeReveal, target).apply {
            duration = (80 + kotlin.math.abs(target - chromeReveal) * 100).toLong()
            interpolator = PathInterpolator(.2f, 0f, 0f, 1f)
            addUpdateListener { chromeReveal = it.animatedValue as Float; renderChrome() }
            start()
        }
    }

    private fun updateFavorite() {
        val item = current ?: return
        b.btnFavorite.imageTintList = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(this, if (GalleryFavorites.contains(this, item)) R.color.nx_error else R.color.nx_on_surface)
        )
        b.btnFavorite.contentDescription = if (GalleryFavorites.contains(this, item)) "取消收藏" else "收藏"
        b.btnEdit.isEnabled = item.jpeg != null
        b.infoEdit.visibility = if (item.jpeg != null) View.VISIBLE else View.GONE
    }

    private fun loadInfo() {
        val item = current ?: return
        val camera = NxApp.camera ?: return
        if (detailKey == item.key && (detailJob?.isActive == true || infoCache.containsKey(item.key))) {
            infoCache[item.key]?.let { render(it, item) }
            return
        }
        detailJob?.cancel()
        if (detailKey != item.key) b.detailsScroll.scrollTo(0, 0)
        detailKey = item.key

        b.badgeCameraSync.visibility = View.GONE
        b.infoDate.text = "照片信息"
        b.infoFile.text = if (item.paired) item.displayName else item.name
        b.infoPath.text = "/相机/DCIM/${item.folder}"
        val fileSizes = item.files.joinToString("  |  ") { file ->
            "${if (file.isRaw) "RAW" else "JPEG"} ${MediaEntry.formatSize(file.size) ?: "大小未知"}"
        }
        b.infoCamera.text = "SAMSUNG NX300M"
        b.infoCameraBadge.text = "APS-C 20.3MP"
        b.infoLens.text = "正在读取镜头元数据…"
        b.infoLensSub.text = "请稍候"
        b.infoLensAction.text = "补录"
        b.infoSize.text = fileSizes
        b.allExif.text = ""
        b.allExif.visibility = View.GONE
        b.exifToggle.text = "展开完整 EXIF 属性"
        b.exifToggle.visibility = View.GONE
        b.capsuleAperture.text = "F--"
        b.capsuleShutter.text = "--s"
        b.capsuleFocal.text = "--mm"
        b.capsuleIso.text = "ISO --"
        b.capsuleEv.text = "0.0 EV"
        b.infoStatus.text = "正在读取原文件 EXIF 信息…"

        infoCache[item.key]?.let {
            render(it, item)
            return
        }

        detailJob = lifecycleScope.launch {
            try {
                val info = PhotoDetails.read(camera, item, cacheDir, item.jpeg?.let { PhotoEdits.get(this@ViewerActivity, it) })
                infoCache[item.key] = info
                if (current?.key == item.key) render(info, item)
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                b.infoStatus.text = "无法读取原文件信息：${e.message}"
            }
        }
    }

    private fun render(info: SourcePhotoInfo, item: MediaGroup) {
        b.infoDate.text = info.date
        b.infoCamera.text = info.camera
        b.infoCameraBadge.text = "APS-C 20.3MP"
        val fileSizes = item.files.joinToString("  ·  ") { file ->
            "${if (file.isRaw) "RAW" else "JPEG"} ${MediaEntry.formatSize(file.size) ?: ""}"
        }
        b.infoSize.text = if (info.size.isNotBlank()) "$fileSizes\n${info.size}" else fileSizes

        b.badgeFormat.text = when {
            item.paired -> "RAW + JPEG"
            item.raw != null -> "RAW (SRW)"
            else -> "JPEG"
        }
        val adjusted = item.jpeg?.let { PhotoEdits.get(this, it) } != null
        b.badgeCameraSync.visibility = if (adjusted) View.VISIBLE else View.GONE
        b.badgeCameraSync.text = if (adjusted) "相册信息已修改" else "源文件信息"
        b.badgeCameraSync.setTextColor(ContextCompat.getColor(this, R.color.nx_on_surface_variant))
        
        if (info.isManualLens) {
            b.infoLens.text = info.lens
            b.infoLensSub.text = if (adjusted) "下载或分享 JPEG 时写入修改" else "镜头信息未完整记录"
            b.infoLensAction.text = "补录"
        } else {
            b.infoLens.text = info.lens
            b.infoLensSub.text = if (adjusted) "下载或分享 JPEG 时写入修改" else "读取自照片 EXIF"
            b.infoLensAction.text = "修改"
        }
        
        b.capsuleAperture.text = info.apertureVal
        b.capsuleShutter.text = info.shutterVal
        b.capsuleFocal.text = info.focalVal
        b.capsuleIso.text = info.isoVal
        b.capsuleEv.text = info.evVal

        b.exifToggle.visibility = if (info.tags.isEmpty()) View.GONE else View.VISIBLE
        b.allExif.text = info.tags.joinToString("\n\n") { (tag, value) -> "${ExifLabels.name(tag)}\n$value" }
        b.infoStatus.text = ""
    }

    private fun edit() {
        if (editing) return
        val item = current ?: return
        val jpeg = item.jpeg ?: return
        val camera = NxApp.camera ?: return
        editing = true
        lifecycleScope.launch {
            try {
                val info = infoCache[item.key] ?: PhotoDetails.read(camera, item, cacheDir, PhotoEdits.get(this@ViewerActivity, jpeg))
                    .also { infoCache[item.key] = it }
                if (current?.key != item.key) { editing = false; return@launch }
                val initial = org.json.JSONObject().apply { info.tags.forEach { (tag, value) -> put(tag, value) } }
                LensMetadata.prompt(this@ViewerActivity, 1, initialMetadata = initial.toString(),
                    onDismiss = { editing = false }) { changes ->
                    if (NxApp.camera !== camera) { toast("相机连接已改变，请重新编辑"); return@prompt }
                    PhotoEdits.update(this@ViewerActivity, jpeg, changes)
                    detailJob?.cancel()
                    infoCache.remove(item.key)
                    detailKey = null
                    if (current?.key == item.key) showDetails(true)
                }
            } catch (e: Exception) {
                editing = false
                if (e is kotlinx.coroutines.CancellationException) throw e
                toast("无法打开照片信息：${e.message}")
            }
        }
    }

    private fun restoreEdits() {
        val item = current ?: return
        val jpeg = item.jpeg ?: return
        if (PhotoEdits.get(this, jpeg) == null) { toast("没有信息修改"); return }
        com.google.android.material.dialog.MaterialAlertDialogBuilder(this)
            .setTitle("还原照片信息？")
            .setMessage("移除此相册中的信息修改，重新显示源文件 EXIF。")
            .setNegativeButton("取消", null)
            .setPositiveButton("还原") { _, _ ->
                PhotoEdits.clear(this, jpeg)
                detailJob?.cancel(); infoCache.remove(item.key); detailKey = null
                if (current?.key == item.key) loadInfo()
            }.show()
    }

    private fun download() {
        val image = current ?: return
        MediaDownloads.choose(this, listOf(image)) { files, metadata ->
            NxApp.cameraIp?.let {
                DownloadService.start(this, it, files, metadata)
                toast("已加入下载队列")
            }
        }
    }

    private fun share() {
        if (sharing) return
        val file = current?.primary ?: return
        val camera = NxApp.camera ?: return
        val metadata = if (file.ext in setOf("jpg", "jpeg")) PhotoEdits.get(this, file) else null
        sharing = true
        b.btnShare.isEnabled = false
        toast("正在准备原文件…")
        lifecycleScope.launch {
            try {
                val target = withContext(Dispatchers.IO) {
                    val dir = File(cacheDir, "shared").apply { mkdirs() }
                    val out = File.createTempFile("share-", ".${file.name.substringAfterLast('.')}", dir)
                    try {
                        out.outputStream().use { stream ->
                            camera.downloadMedia(file, { _, _ -> }, { bytes, n -> stream.write(bytes, 0, n) })
                        }
                        if (metadata != null) {
                            LensMetadata.write(androidx.exifinterface.media.ExifInterface(out), metadata)
                            LensMetadata.verify(androidx.exifinterface.media.ExifInterface(out), metadata)
                        }
                        out
                    } catch (e: Exception) {
                        out.delete()
                        throw e
                    }
                }
                val uri = FileProvider.getUriForFile(this@ViewerActivity, "$packageName.fileprovider", target)
                startActivity(
                    Intent.createChooser(
                        Intent(Intent.ACTION_SEND)
                            .setType(com.nxwa.nxconnect.transfer.MediaStoreWriter.mimeOf(file.name))
                            .putExtra(Intent.EXTRA_STREAM, uri)
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                        "分享照片"
                    )
                )
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                toast("无法分享：${e.message}")
            } finally {
                sharing = false
                b.btnShare.isEnabled = true
            }
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()

    override fun onDestroy() {
        motion?.cancel()
        chromeMotion?.cancel()
        adapter?.dispose()
        if (isFinishing) intent.getStringExtra(EXTRA_SESSION)?.let { com.nxwa.nxconnect.data.ViewerSessions.release(it) }
        super.onDestroy()
    }

    inner class Filmstrip : RecyclerView.Adapter<Filmstrip.Holder>() {
        private var selected = RecyclerView.NO_POSITION
        private val selectionPayload = Any()

        fun select(position: Int) {
            if (position == selected) return
            val previous = selected
            selected = position
            if (previous in images.indices) notifyItemChanged(previous, selectionPayload)
            if (position in images.indices) notifyItemChanged(position, selectionPayload)
        }

        private fun bindSelection(holder: Holder, position: Int) {
            holder.image.alpha = if (position == selected) 1f else .55f
            holder.image.isSelected = position == selected
        }

        inner class Holder(val image: ImageView) : RecyclerView.ViewHolder(image) {
            var job: Job? = null
        }

        override fun getItemCount() = images.size

        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder = Holder(
            ImageView(this@ViewerActivity).apply {
                layoutParams = RecyclerView.LayoutParams(dp(42), dp(52)).apply { setMargins(dp(2), 0, dp(2), 0) }
                scaleType = ImageView.ScaleType.CENTER_CROP
                clipToOutline = true
                setBackgroundResource(R.drawable.bg_thumb)
            }
        )

        override fun onBindViewHolder(holder: Holder, position: Int) {
            holder.job?.cancel()
            holder.image.setImageDrawable(null)
            bindSelection(holder, position)
            holder.image.contentDescription = images[position].displayName
            holder.image.setOnClickListener {
                val target = holder.bindingAdapterPosition
                if (target != RecyclerView.NO_POSITION) b.pager.setCurrentItem(target, true)
            }
            holder.job = lifecycleScope.launch { holder.image.setImageBitmap(ThumbLoader.load(images[position], 160)) }
        }

        override fun onBindViewHolder(holder: Holder, position: Int, payloads: MutableList<Any>) {
            if (payloads.isNotEmpty() && payloads.all { it === selectionPayload }) bindSelection(holder, position)
            else onBindViewHolder(holder, position)
        }

        override fun onViewRecycled(holder: Holder) {
            holder.job?.cancel()
        }
    }
}
