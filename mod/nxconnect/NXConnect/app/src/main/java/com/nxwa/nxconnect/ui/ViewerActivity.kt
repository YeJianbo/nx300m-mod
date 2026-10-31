// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.animation.ValueAnimator
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.Outline
import android.graphics.RectF
import android.graphics.drawable.RippleDrawable
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.ViewOutlineProvider
import android.view.ViewTreeObserver
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.graphics.ColorUtils
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import androidx.viewpager2.widget.ViewPager2
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.data.MediaEntry
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.databinding.ActivityViewerBinding
import com.nxwa.nxconnect.net.CameraSync
import com.nxwa.nxconnect.net.NxCamera
import com.nxwa.nxconnect.transfer.DownloadService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** Full-screen photo viewer with a draggable information sheet. */
class ViewerActivity : AppCompatActivity() {
    private val browsingCamera = NxApp.camera
    companion object {
        const val EXTRA_FOLDER = "folder"
        const val EXTRA_NAMES = "names"
        const val EXTRA_PATHS = "paths"
        const val EXTRA_INDEX = "index"
        const val EXTRA_SIZES = "sizes"
        const val EXTRA_SESSION = "viewer_session"
        const val EXTRA_SOURCE_BOUNDS = "source_bounds"
        const val EXTRA_SOURCE_KEY = "source_key"
    }

    private lateinit var b: ActivityViewerBinding
    private var images = mutableListOf<MediaGroup>()
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
    private var syncing = false
    private var cameraFilesChanged = false
    private var leaving = false
    private var entrance = 1f
    private var entranceMotion: ValueAnimator? = null
    private var exitMotion: ValueAnimator? = null
    private var sourceBounds: RectF? = null
    private var sourceKey: String? = null
    private var pendingEntrance = false
    private var tileTransition: ViewerTileTransition? = null
    private var useTileTransition = false
    private val entranceReadyTimeout = Runnable {
        if (pendingEntrance && !leaving) animateEntrance(allowFallback = true)
    }
    private val entrancePreDraw = ViewTreeObserver.OnPreDrawListener {
        if (pendingEntrance && !leaving) {
            val holder = currentImageHolder()
            if (holder?.error?.visibility == View.VISIBLE) animateEntrance(allowFallback = true)
        }
        true
    }
    private var detailsTarget = 0f
    private var lightSystemBars: Boolean? = null
    private val finishExit = Runnable { if (leaving && !isDestroyed) finish() }
    private val canvasColor: Int get() = ContextCompat.getColor(this, R.color.oneui_viewer_background)
    private val filmstrip = Filmstrip()
    private val current: MediaGroup? get() = images.getOrNull(b.pager.currentItem)
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityViewerBinding.inflate(layoutInflater)
        setContentView(b.root)
        if (requireBrowsingCamera() == null) return
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                NxApp.connection.collect { if (it !== browsingCamera) finish() }
            }
        }
        overridePendingTransition(0, 0)
        sourceKey = intent.getStringExtra(EXTRA_SOURCE_KEY)
        sourceBounds = if (savedInstanceState == null) intent.getIntArrayExtra(EXTRA_SOURCE_BOUNDS)
            ?.takeIf { it.size == 4 && it[2] > it[0] && it[3] > it[1] }
            ?.let { RectF(it[0].toFloat(), it[1].toFloat(), it[2].toFloat(), it[3].toFloat()) } else null
        pendingEntrance = savedInstanceState == null && animationsEnabled()
        entrance = if (pendingEntrance) 0f else 1f
        useTileTransition = pendingEntrance && sourceBounds != null && sourceKey != null
        if (pendingEntrance) {
            b.root.viewTreeObserver.addOnPreDrawListener(entrancePreDraw)
            b.root.postDelayed(entranceReadyTimeout, 700)
        }
        cameraFilesChanged = savedInstanceState?.getBoolean("cameraFilesChanged") == true
        if (cameraFilesChanged) setResult(RESULT_OK)

        WindowCompat.setDecorFitsSystemWindows(window, false)
        val light = resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK != Configuration.UI_MODE_NIGHT_YES
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = light
            isAppearanceLightNavigationBars = light
        }
        configurePanels()

        ViewCompat.setOnApplyWindowInsetsListener(b.root) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            (b.backGlass.layoutParams as? ViewGroup.MarginLayoutParams)?.let { lp ->
                val topTarget = dp(8) + bars.top
                if (lp.topMargin != topTarget) {
                    lp.topMargin = topTarget
                    b.backGlass.layoutParams = lp
                }
            }
            (b.viewerFormatBadge.layoutParams as? ViewGroup.MarginLayoutParams)?.let { lp ->
                val topTarget = dp(72) + bars.top
                if (lp.topMargin != topTarget) {
                    lp.topMargin = topTarget
                    b.viewerFormatBadge.layoutParams = lp
                }
            }
            val bottomTarget = dp(4) + bars.bottom
            if (b.bottomChrome.paddingBottom != bottomTarget) {
                b.bottomChrome.setPadding(0, 0, 0, bottomTarget)
            }
            val sheetBottomTarget = dp(24) + bars.bottom
            val innerLinear = b.detailsScroll.getChildAt(0) as? ViewGroup
            if (innerLinear != null && innerLinear.paddingBottom != sheetBottomTarget) {
                innerLinear.setPadding(innerLinear.paddingStart, innerLinear.paddingTop, innerLinear.paddingEnd, sheetBottomTarget)
            }
            setProgress(progress)
            insets
        }

        val session = intent.getStringExtra(EXTRA_SESSION)
        images = (if (session != null) com.nxwa.nxconnect.data.ViewerSessions.get(session).orEmpty() else {
            val paths = intent.getStringArrayExtra(EXTRA_PATHS)
                ?: intent.getStringArrayExtra(EXTRA_NAMES)?.map { "${intent.getStringExtra(EXTRA_FOLDER).orEmpty()}/$it" }?.toTypedArray()
                ?: emptyArray()
            val sizes = intent.getLongArrayExtra(EXTRA_SIZES) ?: longArrayOf()
            MediaGroup.group(paths.mapIndexed { index, path ->
                MediaEntry(path.substringBeforeLast('/', ""), path.substringAfterLast('/'), sizes.getOrElse(index) { -1 })
            })
        }).toMutableList()
        if (images.isEmpty() || !NxApp.isConnected) { finish(); return }

        adapter = ImagePagerAdapter(
            images,
            onInfo = { showDetails(true) },
            onTap = { toggleChrome() },
            onDrag = drag@{ delta, end, velocity, cancelled ->
                if (leaving) return@drag
                if (delta == 0f && !end) {
                    completeEntrance()
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
                if (pendingEntrance) animateEntrance()
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
        b.filmstrip.setPadding(dp(8), 0, dp(8), 0)
        b.filmstrip.post { positionFilmstrip(b.pager.currentItem) }

        listOf(b.btnBack, b.btnFavorite, b.btnEdit, b.btnInfo, b.btnShare, b.btnDownload, b.btnMore, b.btnCameraSettings).forEach {
            GalleryChrome.press(it)
        }

        var pullStart = 1f
        b.detailsScroll.onPull = pull@{ delta, end, velocity, cancelled ->
            if (leaving) return@pull
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
        b.btnCameraSettings.contentDescription = "相机设置"
        b.btnCameraSettings.setImageResource(R.drawable.ic_oneui_settings)
        b.btnCameraSettings.setOnClickListener { openCameraSettings() }
        b.btnFavorite.setOnClickListener {
            if (requireBrowsingCamera() != null) current?.let { GalleryFavorites.toggle(this, it); updateFavorite() }
        }
        b.btnInfo.setOnClickListener { showDetails(progress < .5f) }
        b.btnEdit.setOnClickListener { edit() }
        b.infoEdit.setOnClickListener { edit() }
        b.btnDownload.setOnClickListener { download() }
        b.btnShare.setOnClickListener { share() }
        b.btnMore.setOnClickListener {
            val actions = mutableListOf<Pair<String, () -> Unit>>(
                "详细信息" to { showDetails(true) },
                "相机设置" to { openCameraSettings() },
                "编辑镜头信息" to { edit() },
                "还原信息修改" to { restoreEdits() },
                "下载原文件" to { download() }
            )
            val profile = requireBrowsingCamera()?.profile ?: return@setOnClickListener
            if (current?.jpeg?.ext in setOf("jpg", "jpeg") &&
                profile.galleryWriteback && profile.galleryOriginalBackup) {
                actions.add("同步信息到相机" to { syncEdits() })
                actions.add("还原相机原始 JPEG" to { restoreCameraOriginal() })
            }
            GalleryChrome.menu(this, it, actions)
        }

        b.pager.registerOnPageChangeCallback(object : ViewPager2.OnPageChangeCallback() {
            override fun onPageSelected(position: Int) {
                if (current?.key != sourceKey && (pendingEntrance || entrance < 1f)) completeEntrance()
                adapter?.resetZoomExcept(position, b.pager)
                updateFavorite()
                filmstrip.select(position)
                positionFilmstrip(position)
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
            b.detailsScroll.layoutParams = (b.detailsScroll.layoutParams as FrameLayout.LayoutParams).apply {
                height = detailsSheetHeight
                gravity = Gravity.BOTTOM
            }
            setProgress(if (savedInstanceState?.getBoolean("detailsOpen") == true) 1f else 0f)
            if (progress > 0f) loadInfo()
            if (pendingEntrance && !leaving) animateEntrance()
        }
    }

    override fun onResume() {
        super.onResume()
        requireBrowsingCamera()
    }

    private fun requireBrowsingCamera(): NxCamera? {
        val camera = browsingCamera
        if (camera == null || NxApp.camera !== camera) {
            finish()
            return null
        }
        return camera
    }

    private fun configurePanels() {
        b.root.setBackgroundColor(canvasColor)
        b.detailsScroll.setBackgroundColor(canvasColor)
        listOf(b.root, b.backGlass, b.bottomChrome).forEach { parent ->
            parent.clipChildren = false
            parent.clipToPadding = false
        }
        listOf(b.btnBack, b.upperActions, b.actionDock).forEach { surface ->
            surface.backgroundTintList = null
            surface.background = ContextCompat.getDrawable(this, R.drawable.bg_oneui_floating_toolbar)?.mutate()?.apply { alpha = 255 }
            surface.stateListAnimator = null
            surface.elevation = dp(3).toFloat()
            surface.translationZ = 0f
            surface.outlineProvider = object : ViewOutlineProvider() {
                override fun getOutline(view: View, outline: Outline) {
                    outline.setRoundRect(0, 0, view.width, view.height, dp(30).toFloat())
                    outline.alpha = 1f
                }
            }
            surface.clipToOutline = true
        }
        val mask = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.WHITE) }
        b.btnBack.background = RippleDrawable(android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(this, R.color.gallery_ripple)), b.btnBack.background, mask).mutate()
    }

    private fun positionFilmstrip(position: Int) {
        val manager = b.filmstrip.layoutManager as? LinearLayoutManager ?: return
        val viewport = b.filmstrip.width - b.filmstrip.paddingLeft - b.filmstrip.paddingRight
        val itemWidth = dp(36)
        if (viewport <= 0 || itemWidth == 0 || images.isEmpty()) return
        val maxScroll = (images.size * itemWidth - viewport).coerceAtLeast(0)
        val desiredScroll = (position * itemWidth - (viewport - itemWidth) / 2).coerceIn(0, maxScroll)
        val first = desiredScroll / itemWidth
        val offset = -(desiredScroll % itemWidth)
        manager.scrollToPositionWithOffset(first, offset)
    }

    private fun currentImageHolder(): ImagePagerAdapter.VH? =
        ((b.pager.getChildAt(0) as? RecyclerView)?.findViewHolderForAdapterPosition(b.pager.currentItem) as? ImagePagerAdapter.VH)

    private fun captureTileTransition(): ViewerTileTransition? {
        if (current?.key != sourceKey) return null
        val bounds = sourceBounds?.let(::RectF) ?: return null
        val image = currentImageHolder()?.image ?: return null
        val drawable = image.drawable ?: return null
        val fittedHeight = image.fittedPhotoHeight?.takeIf { it > 0f } ?: return null
        if (drawable.intrinsicWidth <= 0 || drawable.intrinsicHeight <= 0 || b.imageStage.width <= 0 || b.imageStage.height <= 0) return null
        val fittedWidth = fittedHeight * drawable.intrinsicWidth / drawable.intrinsicHeight
        val rootLocation = IntArray(2).also { b.root.getLocationOnScreen(it) }
        bounds.offset(-rootLocation[0].toFloat() - b.imageStage.left, -rootLocation[1].toFloat() - b.imageStage.top)
        return ViewerTileTransition(bounds, fittedWidth, fittedHeight, b.imageStage.width, b.imageStage.height)
    }

    private fun completeEntrance() {
        if (leaving) return
        pendingEntrance = false
        entranceMotion?.cancel()
        entrance = 1f
        b.root.viewTreeObserver.removeOnPreDrawListener(entrancePreDraw)
        b.root.removeCallbacks(entranceReadyTimeout)
        b.imageStage.clipBounds = null
        setProgress(progress)
    }

    private fun animateEntrance(allowFallback: Boolean = false) {
        if (!pendingEntrance || leaving || isFinishing) return
        if (!animationsEnabled()) { completeEntrance(); return }
        val geometry = captureTileTransition()
        if (useTileTransition && geometry == null && !allowFallback && current?.key == sourceKey) return
        pendingEntrance = false
        b.root.viewTreeObserver.removeOnPreDrawListener(entrancePreDraw)
        b.root.removeCallbacks(entranceReadyTimeout)
        tileTransition = geometry
        useTileTransition = geometry != null
        currentImageHolder()?.image?.let { it.animate().cancel(); it.alpha = 1f }
        entranceMotion?.cancel()
        entrance = 0f
        entranceMotion = ValueAnimator.ofFloat(0f, 1f).apply {
            duration = if (useTileTransition) 300 else 150
            interpolator = viewerMotionInterpolator()
            addUpdateListener { entrance = it.animatedValue as Float; setProgress(progress) }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                private var cancelled = false
                override fun onAnimationCancel(animation: android.animation.Animator) { cancelled = true }
                override fun onAnimationEnd(animation: android.animation.Animator) {
                    if (!cancelled && !leaving) {
                        entrance = 1f
                        b.imageStage.clipBounds = null
                        setProgress(progress)
                    }
                }
            })
            start()
        }
    }

    override fun onSaveInstanceState(out: Bundle) {
        out.putInt("index", b.pager.currentItem)
        out.putBoolean("detailsOpen", progress > .5f)
        out.putBoolean("chromeVisible", chromeVisible)
        out.putBoolean("cameraFilesChanged", cameraFilesChanged)
        super.onSaveInstanceState(out)
    }

    private fun back() {
        if (leaving) { finish(); return }
        adapter?.cancelInteraction(b.pager)
        b.detailsScroll.cancelPull()
        when {
            progress > .01f || detailsDragging -> {
                if (motion?.isRunning == true && detailsTarget == 0f) {
                    motion?.cancel(); detailsDragging = false; setProgress(0f); leaveViewer()
                } else showDetails(false)
            }
            adapter?.resetCurrentZoom(b.pager, animate = false) == true -> Unit
            else -> leaveViewer()
        }
    }

    private fun leaveViewer() {
        if (!animationsEnabled()) { finish(); return }
        leaving = true
        pendingEntrance = false
        b.root.viewTreeObserver.removeOnPreDrawListener(entrancePreDraw)
        b.root.removeCallbacks(entranceReadyTimeout)
        entranceMotion?.cancel()
        motion?.cancel()
        chromeMotion?.cancel()
        adapter?.cancelInteraction(b.pager)
        tileTransition = captureTileTransition()
        useTileTransition = tileTransition != null
        if (!useTileTransition) entrance = 1f
        b.pager.isUserInputEnabled = false
        exitMotion = ValueAnimator.ofFloat(entrance, 0f).apply {
            duration = if (useTileTransition) 260 else 150
            interpolator = viewerMotionInterpolator()
            addUpdateListener { entrance = it.animatedValue as Float; setProgress(progress) }
            addListener(object : android.animation.AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: android.animation.Animator) {
                    if (leaving && !isDestroyed) finish()
                }
            })
            start()
        }
        // Finishing must not depend on an animation-end callback surviving interruption.
        b.root.postDelayed(finishExit, viewerMotionCompletionDelay(this, if (useTileTransition) 260 else 150))
    }

    private val detailsSheetHeight: Int
        get() {
            val rootH = b.root.height.takeIf { it > 0 } ?: resources.displayMetrics.heightPixels
            return (rootH - detailsTop()).coerceAtLeast(1)
        }

    private fun detailsPhotoScale(): Float {
        val height = b.root.height.takeIf { it > 0 } ?: resources.displayMetrics.heightPixels
        val fitted = adapter?.fittedPhotoHeight(b.pager) ?: height * .45f
        return (height * .44f / fitted.coerceAtLeast(1f)).coerceIn(.15f, 1.35f)
    }

    private fun detailsTop(): Int {
        val height = b.root.height.takeIf { it > 0 } ?: resources.displayMetrics.heightPixels
        val top = ViewCompat.getRootWindowInsets(b.root)?.getInsets(WindowInsetsCompat.Type.systemBars())?.top ?: 0
        val fitted = adapter?.fittedPhotoHeight(b.pager) ?: height * .45f
        return (top + fitted * detailsPhotoScale()).toInt().coerceAtMost((height * .6f).toInt())
    }

    private fun setProgress(value: Float) {
        val opening = progress <= .001f && value > .001f
        progress = value.coerceIn(0f, 1f)
        val sheetH = detailsSheetHeight
        if (sheetH <= 1) return

        if (progress > 0.001f || detailsDragging) {
            if (opening) loadInfo()
            b.detailsScroll.visibility = View.VISIBLE
        } else {
            b.detailsScroll.visibility = View.GONE
        }

        val sheetParams = b.detailsScroll.layoutParams as FrameLayout.LayoutParams
        if (sheetParams.height != sheetH && !detailsDragging) {
            sheetParams.height = sheetH
            b.detailsScroll.layoutParams = sheetParams
        }

        b.detailsScroll.translationY = (1f - progress) * sheetH
        b.detailsScroll.alpha = 1f
        b.detailsScroll.importantForAccessibility = if (progress > .5f) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
            else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        b.pager.isUserInputEnabled = progress < .01f && !syncing && !leaving
        b.pager.importantForAccessibility = if (progress < .01f) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
            else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS

        val rootH = b.root.height.takeIf { it > 0 } ?: resources.displayMetrics.heightPixels
        val top = ViewCompat.getRootWindowInsets(b.root)?.getInsets(WindowInsetsCompat.Type.systemBars())?.top ?: 0
        val targetCenterY = (detailsTop() + top) / 2f
        val initialCenterY = rootH / 2f

        val targetScale = detailsPhotoScale()
        val geometry = tileTransition.takeIf { useTileTransition }
        val entranceScale = geometry?.let { it.startScale + (1f - it.startScale) * entrance } ?: 1f
        val currentScale = (1f - (1f - targetScale) * progress) * entranceScale
        b.imageStage.pivotX = b.imageStage.width / 2f
        b.imageStage.pivotY = b.imageStage.height / 2f
        b.imageStage.scaleX = currentScale
        b.imageStage.scaleY = currentScale
        b.imageStage.translationX = (geometry?.translationX ?: 0f) * (1f - entrance)
        b.imageStage.translationY = (targetCenterY - initialCenterY) * progress + (geometry?.translationY ?: 0f) * (1f - entrance)
        b.imageStage.clipBounds = if (geometry != null && entrance < .9999f) geometry.clip(entrance) else null
        b.imageStage.alpha = if (geometry != null) 1f else entrance

        renderChrome()
    }

    private fun renderChrome() {
        val alpha = chromeReveal * (1f - progress * 2.5f).coerceIn(0f, 1f) * entrance
        b.bottomChrome.translationY = dp(12) * (1f - chromeReveal) + dp(24) * progress
        b.backGlass.translationY = -dp(8) * (1f - chromeReveal) - dp(12) * progress
        val canvasReveal = maxOf(chromeReveal, progress)
        val background = ColorUtils.blendARGB(Color.BLACK, canvasColor, canvasReveal)
        b.root.setBackgroundColor(if (useTileTransition) ColorUtils.setAlphaComponent(background, (255 * entrance).toInt()) else background)
        val light = canvasReveal > .55f && resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK != Configuration.UI_MODE_NIGHT_YES
        if (lightSystemBars != light) {
            lightSystemBars = light
            WindowCompat.getInsetsController(window, window.decorView).apply {
                isAppearanceLightStatusBars = light
                isAppearanceLightNavigationBars = light
            }
        }
        b.filmstrip.alpha = (1f - progress * 3.0f).coerceIn(0f, 1f)
        b.filmstrip.translationY = dp(24) * progress
        listOf(b.bottomChrome, b.backGlass, b.viewerFormatBadge).forEach { view ->
            view.alpha = alpha
            view.visibility = if (alpha > .001f) View.VISIBLE else View.INVISIBLE
            view.importantForAccessibility = if (alpha > .5f) View.IMPORTANT_FOR_ACCESSIBILITY_AUTO
                else View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        }
        val interactive = alpha > .1f && progress < .01f && !leaving
        listOf(b.btnBack, b.btnFavorite, b.btnInfo, b.btnDownload, b.btnMore, b.btnCameraSettings).forEach { it.isEnabled = interactive }
        b.btnEdit.isEnabled = interactive && current?.files?.any(PhotoEdits::supports) == true
        b.btnShare.isEnabled = interactive && !sharing
        b.filmstrip.isEnabled = interactive
        b.actionDock.invalidate()
        b.backGlass.invalidate()
    }

    private fun animationsEnabled() = viewerAnimationsEnabled(this)

    private fun settleDetails(next: Float, velocity: Float, start: Float) {
        val open = if (kotlin.math.abs(velocity) > dp(650)) velocity < 0f
            else if (start > .5f) next > .72f else next > .28f
        showDetails(open)
    }

    private fun showDetails(open: Boolean) {
        if (leaving) return
        completeEntrance()
        motion?.cancel()
        detailsDragging = false
        if (open) {
            adapter?.resetCurrentZoom(b.pager, animate = false)
            loadInfo()
        }
        val target = if (open) 1f else 0f
        detailsTarget = target
        if (!animationsEnabled() || kotlin.math.abs(target - progress) < .001f) {
            setProgress(target)
            return
        }
        motion = ValueAnimator.ofFloat(progress, target).apply {
            duration = (140 + kotlin.math.abs(target - progress) * 200).toLong()
            interpolator = viewerMotionInterpolator()
            addUpdateListener { setProgress(it.animatedValue as Float) }
            start()
        }
    }

    private fun toggleChrome() {
        if (progress > .01f || detailsDragging || leaving) return
        completeEntrance()
        chromeVisible = !chromeVisible
        chromeMotion?.cancel()
        val target = if (chromeVisible) 1f else 0f
        if (!animationsEnabled()) {
            chromeReveal = target
            renderChrome()
            return
        }
        chromeMotion = ValueAnimator.ofFloat(chromeReveal, target).apply {
            duration = if (chromeVisible) 150L else 80L
            interpolator = viewerMotionInterpolator()
            addUpdateListener { chromeReveal = it.animatedValue as Float; renderChrome() }
            start()
        }
    }

    private fun updateFavorite() {
        if (requireBrowsingCamera() == null) return
        val item = current ?: return
        b.btnFavorite.imageTintList = android.content.res.ColorStateList.valueOf(
            ContextCompat.getColor(this, if (GalleryFavorites.contains(this, item)) R.color.nx_error else R.color.nx_on_surface)
        )
        b.btnFavorite.contentDescription = if (GalleryFavorites.contains(this, item)) "取消收藏" else "收藏"
        b.btnFavorite.isSelected = GalleryFavorites.contains(this, item)
        b.pager.contentDescription = "第 ${b.pager.currentItem + 1} 张，共 ${images.size} 张"
        b.infoEdit.visibility = if (item.files.any(PhotoEdits::supports)) View.VISIBLE else View.GONE
        b.viewerFormatBadge.text = formatLabel(item)
        renderChrome()
    }

    private fun loadInfo() {
        val item = current ?: return
        val camera = requireBrowsingCamera() ?: return
        if (detailKey == item.key && (detailJob?.isActive == true || infoCache.containsKey(item.key))) {
            infoCache[item.key]?.let { render(it, item) }
            return
        }
        detailJob?.cancel()
        if (detailKey != item.key) {
            b.detailsScroll.scrollTo(0, 0)
        }
        detailKey = item.key

        b.badgeCameraSync.visibility = View.GONE
        b.infoDate.text = "照片信息"
        b.infoFile.text = if (item.paired) item.displayName else item.name
        b.infoPath.text = "/相机/DCIM/${item.folder}"
        b.infoCamera.text = "正在读取相机信息…"
        b.infoLens.text = "正在读取镜头元数据…"
        b.infoSize.text = fileSizes(item)
        b.allExif.text = ""
        b.allExif.visibility = View.GONE
        b.exifToggle.text = "展开完整 EXIF 属性"
        b.exifToggle.visibility = View.GONE
        b.infoExposure.text = ""
        b.badgeFormat.text = formatLabel(item)
        b.infoStatus.text = "正在读取原文件 EXIF 信息…"
        b.infoStatus.visibility = View.VISIBLE

        infoCache[item.key]?.let {
            render(it, item)
            return
        }

        detailJob = lifecycleScope.launch {
            try {
                val info = PhotoDetails.read(camera, item, cacheDir, PhotoEdits.get(this@ViewerActivity, item.primary))
                if (NxApp.camera !== camera || current?.key != item.key) return@launch
                infoCache[item.key] = info
                if (current?.key == item.key) render(info, item)
            } catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                if (NxApp.camera !== camera || current?.key != item.key) return@launch
                b.infoStatus.text = "无法读取原文件信息：${e.message}"
                b.infoStatus.visibility = View.VISIBLE
            }
        }
    }

    private fun formatLabel(item: MediaGroup) = when {
        item.paired -> "RAW + JPG"
        item.raw != null -> "RAW · SRW"
        else -> "JPG"
    }

    // Keep each value and unit together while allowing breaks between metadata items.
    private fun metadataLine(values: List<String>) = values.filter { it.isNotBlank() }
        .joinToString("  |  ") { it.replace(' ', '\u00a0') }

    private fun fileSizes(item: MediaGroup) = metadataLine(item.files.map { file ->
        "${if (file.isRaw) "RAW" else "JPG"} ${MediaEntry.formatSize(file.size) ?: "大小未知"}"
    })

    private fun render(info: SourcePhotoInfo, item: MediaGroup) {
        if (requireBrowsingCamera() == null) return
        b.infoDate.text = info.date
        b.infoCamera.text = info.camera
        val totalSize = item.size.takeIf { it >= 0 }
        b.infoSize.text = metadataLine(listOf(
            totalSize?.let { MediaEntry.formatSize(it) } ?: "大小未知",
            info.resolution.replace(" ", ""),
            info.megaPixels.replace(".0 MP", "MP").replace(" MP", "MP"),
        ))
        b.infoSize.contentDescription = metadataLine(listOf(fileSizes(item), info.resolution, info.megaPixels))
        b.infoExposure.text = metadataLine(listOf(
            info.isoVal.takeUnless { it == "ISO --" }.orEmpty(),
            info.focalEquivVal,
            info.evVal,
            info.apertureVal.takeUnless { it == "F--" }.orEmpty(),
            info.shutterVal.takeUnless { it == "--s" }.orEmpty(),
        ))
        b.badgeFormat.text = formatLabel(item)
        val sourceAdjusted = PhotoEdits.get(this, item.primary) != null
        val adjusted = item.files.any { PhotoEdits.get(this, it) != null }
        b.badgeCameraSync.visibility = if (adjusted) View.VISIBLE else View.GONE
        b.badgeCameraSync.text = when {
            sourceAdjusted -> "本地信息修改"
            adjusted -> "RAW 副本有本地修改"
            else -> "源文件信息"
        }
        b.badgeCameraSync.setTextColor(ContextCompat.getColor(this, R.color.nx_on_surface_variant))
        b.infoLens.text = info.lens

        b.exifToggle.visibility = if (info.tags.isEmpty()) View.GONE else View.VISIBLE
        b.allExif.text = info.tags.joinToString("\n\n") { (tag, value) -> "${ExifLabels.name(tag)}\n$value" }
        b.infoStatus.text = ""
        b.infoStatus.visibility = View.GONE
    }

    private fun openCameraSettings() {
        val camera = requireBrowsingCamera() ?: return
        ExposureSettingsDialog(this, camera) {
            startActivity(Intent(this, RemoteActivity::class.java).putExtra("open_stock_iso", true))
        }.show(section = ExposureSettingsDialog.Section.CONTROLS)
    }

    private fun edit() {
        if (editing || syncing) return
        val item = current ?: return
        if (item.files.none(PhotoEdits::supports)) return
        val source = item.primary
        val camera = requireBrowsingCamera() ?: return
        editing = true
        lifecycleScope.launch {
            try {
                val info = infoCache[item.key] ?: PhotoDetails.read(camera, item, cacheDir, PhotoEdits.get(this@ViewerActivity, source))
                if (current?.key != item.key) { editing = false; return@launch }
                if (NxApp.camera !== camera) {
                    editing = false
                    toast("相机连接已改变，请重新编辑")
                    return@launch
                }
                infoCache[item.key] = info
                val initial = org.json.JSONObject().apply { info.tags.forEach { (tag, value) -> put(tag, value) } }
                LensMetadata.prompt(this@ViewerActivity, 1, initialMetadata = initial.toString(), displayName = item.displayName,
                    onDismiss = { editing = false }) { changes ->
                    if (NxApp.camera !== camera) { toast("相机连接已改变，请重新编辑"); return@prompt }
                    PhotoEdits.update(this@ViewerActivity, item, changes)
                    toast("已保存本地修改，下载或分享 JPEG／SRW 时写入副本")
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
        if (syncing) return
        val item = current ?: return
        val camera = requireBrowsingCamera() ?: return
        if (item.files.none { PhotoEdits.get(this, it) != null }) { toast("没有信息修改"); return }
        NxDialogBuilder(this)
            .setTitle("还原照片信息？")
            .setMessage("移除此相册中的信息修改，重新显示源文件 EXIF。")
            .setNegativeButton("取消", null)
            .setPositiveButton("还原") { _, _ ->
                if (NxApp.camera !== camera) {
                    toast("相机连接已改变，请重新选择照片")
                    return@setPositiveButton
                }
                PhotoEdits.clear(this, item, camera.baseUrl)
                detailJob?.cancel(); infoCache.remove(item.key); detailKey = null
                if (current?.key == item.key) loadInfo()
            }.show()
    }

    private fun syncEdits() {
        if (syncing) return
        val item = current ?: return
        val jpeg = item.jpeg?.takeIf { it.ext in setOf("jpg", "jpeg") } ?: return
        val camera = requireBrowsingCamera() ?: return
        val metadata = PhotoEdits.get(this, jpeg)
        if (metadata == null) { toast("请先编辑镜头信息"); return }
        NxDialogBuilder(this)
            .setTitle("同步信息到相机？")
            .setMessage("将本地信息写入 ${jpeg.name} 的 JPEG，保留相机上的原始备份，可在此菜单还原。RAW 文件不会修改。")
            .setNegativeButton("取消", null)
            .setPositiveButton("同步") { _, _ ->
                runCameraEdit("同步照片信息") { status ->
                    check(NxApp.camera === camera) { "相机连接已改变，请重新同步" }
                    camera.requireGalleryWriteback()
                    status("正在下载相机 JPEG…")
                    val local = withContext(Dispatchers.IO) {
                        File.createTempFile("camera-edit-", ".jpg", cacheDir)
                    }
                    try {
                        withContext(Dispatchers.IO) {
                            local.outputStream().use { stream ->
                                camera.downloadMedia(jpeg, { read, total ->
                                    if (total > 0) status("正在下载 JPEG：${read * 100 / total}%")
                                }) { bytes, count -> stream.write(bytes, 0, count) }
                            }
                            LensMetadata.write(androidx.exifinterface.media.ExifInterface(local), metadata)
                            LensMetadata.verify(androidx.exifinterface.media.ExifInterface(local), metadata)
                        }
                        // An interrupted acknowledgement can still follow a committed camera write.
                        // Refresh the parent gallery and stop trusting its old byte count once sent.
                        markCameraFilesChanged()
                        updatePhotoSize(item, jpeg, -1L, camera.baseUrl)
                        when (val result = CameraSync.syncToCamera(jpeg, local, camera, status)) {
                            is CameraSync.SyncResult.Failed -> error(result.reason)
                            CameraSync.SyncResult.Success -> {
                                check(NxApp.camera === camera) { "相机连接已改变，已保留本地修改，请重连后确认" }
                                // Clear only the submitted version; edits entered elsewhere remain pending.
                                val updated = updatePhotoSize(item, jpeg, local.length(), camera.baseUrl)
                                updated.jpeg?.let { if (PhotoEdits.get(this, it, camera.baseUrl) == metadata) PhotoEdits.clear(this, it, camera.baseUrl) }
                                invalidatePhotoInfo(updated)
                                toast("已同步并从相机完整读回，原始备份已保留")
                            }
                        }
                    } finally { local.delete() }
                }
            }.show()
    }

    private fun restoreCameraOriginal() {
        if (syncing) return
        val item = current ?: return
        val jpeg = item.jpeg?.takeIf { it.ext in setOf("jpg", "jpeg") } ?: return
        val camera = requireBrowsingCamera() ?: return
        NxDialogBuilder(this)
            .setTitle("还原相机原始 JPEG？")
            .setMessage("用首次同步前保留的原始文件还原 ${jpeg.name}。JPEG 的本地修改也将清除，备份仍保留。" +
                if (item.raw != null) "RAW 的本地修改保留供下载副本使用。" else "")
            .setNegativeButton("取消", null)
            .setPositiveButton("还原") { _, _ ->
                runCameraEdit("还原相机原始 JPEG") { status ->
                    check(NxApp.camera === camera) { "相机连接已改变，请重新还原" }
                    camera.requireGalleryWriteback()
                    check(camera.galleryEditStatus(jpeg).backupAvailable) { "这张照片尚无原始备份" }
                    status("正在还原相机原始 JPEG…")
                    markCameraFilesChanged()
                    updatePhotoSize(item, jpeg, -1L, camera.baseUrl)
                    camera.restoreMedia(jpeg)
                    status("正在读回还原后的照片信息…")
                    val sourceInfo = PhotoDetails.read(camera, item, cacheDir)
                    val restored = camera.galleryEditStatus(jpeg)
                    check(restored.backupAvailable) { "还原已请求，但无法确认保留的备份" }
                    check(NxApp.camera === camera) { "相机连接已改变，已保留本地修改，请重连后确认" }
                    val updated = updatePhotoSize(item, jpeg, restored.size, camera.baseUrl)
                    updated.jpeg?.let { PhotoEdits.clear(this, it, camera.baseUrl) }
                    invalidatePhotoInfo(updated)
                    infoCache[item.key] = sourceInfo
                    if (current?.key == item.key) render(sourceInfo, updated)
                    toast("已还原并读回原始 JPEG 信息")
                }
            }.show()
    }

    private fun markCameraFilesChanged() {
        cameraFilesChanged = true
        setResult(RESULT_OK)
    }

    private fun updatePhotoSize(item: MediaGroup, entry: MediaEntry, size: Long, cameraUrl: String): MediaGroup {
        val index = images.indexOfFirst { it.key == item.key }
        val latest = images.getOrNull(index) ?: item
        val previous = latest.files.firstOrNull { it.relPath == entry.relPath } ?: entry
        val replacement = previous.copy(size = size)
        val updated = latest.copy(files = latest.files.map { if (it.relPath == entry.relPath) replacement else it })
        if (previous.size != size || size < 0) {
            PhotoEdits.move(this, previous, replacement, uncertain = size < 0, cameraUrl = cameraUrl)
        }
        if (index >= 0) images[index] = updated
        intent.getStringExtra(EXTRA_SESSION)?.let { com.nxwa.nxconnect.data.ViewerSessions.update(it, images) }
        return updated
    }

    private fun invalidatePhotoInfo(item: MediaGroup) {
        detailJob?.cancel()
        infoCache.remove(item.key)
        detailKey = null
        if (current?.key == item.key && progress > 0f) loadInfo()
    }

    private fun runCameraEdit(title: String, action: suspend ((String) -> Unit) -> Unit) {
        if (syncing) return
        syncing = true
        b.pager.isUserInputEnabled = false
        val dialog = NxDialogBuilder(this)
            .setTitle(title).setMessage("正在连接相机…").setCancelable(false).show()
        val status: (String) -> Unit = { text ->
            runOnUiThread { if (!isDestroyed && dialog.isShowing) dialog.setMessage(text) }
        }
        lifecycleScope.launch {
            try { action(status) }
            catch (e: Exception) {
                if (e is kotlinx.coroutines.CancellationException) throw e
                if (requireBrowsingCamera() == null || isFinishing || isDestroyed) return@launch
                NxDialogBuilder(this@ViewerActivity)
                    .setTitle("操作未完成")
                    .setMessage("${e.message}\n\n本地信息修改已保留。可重新连接后检查照片，或还原相机原始 JPEG。")
                    .setPositiveButton("知道了", null).show()
            } finally {
                dialog.dismiss()
                syncing = false
                b.pager.isUserInputEnabled = progress < .01f && !leaving
            }
        }
    }

    private fun download() {
        val camera = requireBrowsingCamera() ?: return
        val image = current ?: return
        MediaDownloads.choose(this, listOf(image)) { files, metadata ->
            if (requireBrowsingCamera() !== camera) return@choose
            DownloadService.start(this, camera.ip, files, metadata)
            toast("已加入下载队列")
        }
    }

    private fun share() {
        if (sharing) return
        val file = current?.primary ?: return
        val camera = requireBrowsingCamera() ?: return
        val metadata = if (PhotoEdits.supports(file)) PhotoEdits.get(this, file) else null
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
                            if (file.ext == "srw") {
                                SrwMetadata.write(out, metadata)
                                SrwMetadata.verify(out, metadata)
                            } else {
                                LensMetadata.write(androidx.exifinterface.media.ExifInterface(out), metadata)
                                LensMetadata.verify(androidx.exifinterface.media.ExifInterface(out), metadata)
                            }
                        }
                        out
                    } catch (e: Exception) {
                        out.delete()
                        throw e
                    }
                }
                if (requireBrowsingCamera() !== camera) {
                    target.delete()
                    return@launch
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
                renderChrome()
            }
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()

    override fun onDestroy() {
        leaving = false
        pendingEntrance = false
        b.root.viewTreeObserver.removeOnPreDrawListener(entrancePreDraw)
        b.root.removeCallbacks(entranceReadyTimeout)
        b.imageStage.clipBounds = null
        b.root.removeCallbacks(finishExit)
        exitMotion?.cancel()
        entranceMotion?.cancel()
        motion?.cancel()
        chromeMotion?.cancel()
        adapter?.dispose()
        if (isFinishing) intent.getStringExtra(EXTRA_SESSION)?.let { com.nxwa.nxconnect.data.ViewerSessions.release(it) }
        super.onDestroy()
    }

    override fun finish() {
        if (::b.isInitialized) {
            b.imageStage.visibility = View.INVISIBLE
            b.imageStage.clipBounds = null
        }
        super.finish()
        overridePendingTransition(0, 0)
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
            holder.image.alpha = if (position == selected) 1f else .9f
            holder.image.scaleY = if (position == selected) 1.1f else 1f
            holder.image.isSelected = position == selected
        }

        inner class Holder(frame: FrameLayout, val image: ImageView) : RecyclerView.ViewHolder(frame) {
            var job: Job? = null
        }

        override fun getItemCount() = images.size

        override fun onCreateViewHolder(parent: ViewGroup, type: Int): Holder {
            val frame = FrameLayout(this@ViewerActivity).apply {
                layoutParams = RecyclerView.LayoutParams(dp(36), dp(48))
            }
            val image = ImageView(this@ViewerActivity).apply {
                layoutParams = FrameLayout.LayoutParams(dp(34), dp(32), Gravity.CENTER)
                scaleType = ImageView.ScaleType.CENTER_CROP
                clipToOutline = true
                setBackgroundResource(R.drawable.bg_thumb)
            }
            frame.addView(image)
            return Holder(frame, image)
        }

        override fun onBindViewHolder(holder: Holder, position: Int) {
            holder.job?.cancel()
            holder.image.setImageDrawable(null)
            bindSelection(holder, position)
            holder.image.contentDescription = images[position].displayName
            holder.itemView.contentDescription = images[position].displayName
            holder.itemView.setOnClickListener {
                if (chromeReveal < .1f || progress > .01f || leaving) return@setOnClickListener
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
