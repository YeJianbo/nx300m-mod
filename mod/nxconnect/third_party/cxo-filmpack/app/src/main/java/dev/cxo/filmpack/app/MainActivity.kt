package dev.cxo.filmpack.app

import androidx.activity.ComponentActivity
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.content.res.Configuration
import android.os.Build
import android.view.MotionEvent
import android.view.ViewGroup
import android.view.WindowInsets
import dev.cxo.filmpack.app.ui.EditorPalette
import dev.cxo.filmpack.app.ui.CatalogGlassButton
import dev.cxo.filmpack.app.ui.CatalogGlassSlider
import dev.cxo.filmpack.app.ui.CatalogGlassToggle
import dev.cxo.filmpack.app.ui.SliderAxisScrollView
import dev.cxo.filmpack.app.ui.CatalogGlassEnvironment
import dev.cxo.filmpack.app.ui.ZoomablePreviewImageView
import dev.cxo.filmpack.app.ui.catalogWindow
import dev.cxo.filmpack.app.ui.catalogSheet
import dev.cxo.filmpack.app.ui.EditorIcon
import dev.cxo.filmpack.app.ui.FilmCard
import android.net.Uri
import android.os.Bundle
import android.os.SystemClock
import android.animation.ValueAnimator
import android.view.animation.DecelerateInterpolator
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.*
import dev.cxo.filmpack.core.NativeGrain8Geometry
import dev.cxo.filmpack.app.post.*
import dev.cxo.filmpack.core.PostProcessSettings
import dev.cxo.filmpack.core.ToneCurvePreset
import java.util.concurrent.CancellationException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/** Dark photo workbench; all photo processing stays on its existing serial worker. */
class MainActivity : ComponentActivity() {
    private val worker = Executors.newSingleThreadExecutor()
    private val imageGeneration = AtomicInteger()
    private val previewGeneration = AtomicInteger()
    private val previewBarrier = AtomicInteger()
    private val previewBaseCache = PreviewBaseCache() // worker-owned
    private data class PreviewKey(val image: Int, val film: String, val intensity: Int, val grain: GrainSettings)
    private data class PreviewRequest(val generation: Int, val barrier: Int, val key: PreviewKey,
        val source: Bitmap, val film: FilmChoice, val uri: Uri?, val metadata: PhotoGrainPreview?,
        val post: PostEditingSettings, val interactive: Boolean)
    private var pendingPreview: PreviewRequest? = null
    private var previewTaskRunning = false
    private var previewInteraction = false
    private var precisePreviewReady = false
    private val exportCancelled = AtomicBoolean()
    @Volatile private var destroyed = false
    private var processor: PhotoProcessor? = null
    private var films: List<FilmChoice> = emptyList()
    private var selectedFilm: FilmChoice? = null
    private var sourceUri: Uri? = null
    private var original: Bitmap? = null
    private var rendered: Bitmap? = null
    private var loading = false
    private var rendering = false
    private var exporting = false
    private var choosingDestination = false
    private var showOriginal = false
    private var pendingExportFilm: FilmChoice? = null
    private var exportSettingsDialog: AlertDialog? = null
    private var pendingExportSettings = ExportSettings()
    private var intensity = 100
    private var pendingExportIntensity = 100
    private var applyingIntensity = false
    private var restoredFilmToken: String? = null
    private var restoredIntensity = 100
    private var filmDialog: AlertDialog? = null
    private var filmInfoDialog: AlertDialog? = null
    private val filmDescriptions by lazy {
        org.json.JSONObject(assets.open("film-descriptions-zh.json").bufferedReader().use { it.readText() })
    }
    private var grainDialog: AlertDialog? = null
    private var grainStore: GrainPackStore? = null // Used only on the single worker.
    private var grainIdentifiers: List<String> = emptyList()
    private var grainSettings = GrainSettings()
    private var postSettings = PostEditingSettings()
    private var pendingExportPost = PostEditingSettings()
    private var postDialog: AlertDialog? = null
    private var editorModule = 0
    private var postModuleView: View? = null
    private var postPanel: PostSettingsPanel? = null
    private var filmModuleView: View? = null
    private lateinit var filmModuleButton: CatalogGlassButton
    private lateinit var postModuleButton: CatalogGlassButton
    private var grainPreview: PhotoGrainPreview? = null
    private var pendingExportGrain = GrainSettings()
    private var pendingExportPreview: PhotoGrainPreview? = null
    private var importingPack = false
    private val packCancelled = AtomicBoolean()
    private val preferences by lazy { getSharedPreferences("photo-settings",MODE_PRIVATE) }

    private lateinit var photo: ZoomablePreviewImageView
    private lateinit var placeholder: TextView
    private lateinit var importButton: CatalogGlassButton
    private lateinit var exportButton: CatalogGlassButton
    private lateinit var filmButton: CatalogGlassButton
    private lateinit var cancelButton: CatalogGlassButton
    private lateinit var progress: ProgressBar
    private lateinit var status: TextView
    private lateinit var photoInfo: TextView
    private lateinit var intensitySlider: CatalogGlassSlider
    private lateinit var intensityValue: TextView
    private lateinit var resetIntensityButton: CatalogGlassButton
    private lateinit var grainButton: CatalogGlassButton

    private var glassEnvironment = CatalogGlassEnvironment()
    private val filmCards = linkedMapOf<String, FilmCard>()
    private val thumbnailCache = linkedMapOf<String, Bitmap>()
    private lateinit var filmStrip: LinearLayout
    private lateinit var filmScroll: HorizontalScrollView
    private var stripSelectionToken: String? = null
    private var thumbnailTaskRunning = false
    private val thumbnailViewport = AtomicInteger()
    private var thumbnailWantedTokens = emptyList<String>()
    private val thumbnailUnavailable = mutableSetOf<String>()
    private val thumbnailRefresh = Runnable { requestFilmThumbnails() }
    private lateinit var favoriteButton: CatalogGlassButton
    private var stripTokens = emptyList<String>()
    private var editorDrawer: FrameLayout? = null
    private var editorDrawerTitle: TextView? = null
    private var editorDrawerExpanded = false
    private var drawerAnimator: ValueAnimator? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        restoredFilmToken = savedInstanceState?.getString("film")
        restoredIntensity = savedInstanceState?.getInt("intensity", 100)?.coerceIn(0, 200) ?: 100
        intensity = restoredIntensity
        grainSettings = GrainSettings.read(preferences)
        postSettings = PostEditingSettings.read(preferences)
        val restoredUri = savedInstanceState?.getString("uri")?.let(Uri::parse)
        buildUi()
        status.text = "正在准备胶片与内置颗粒…"
        updateControls()
        worker.execute {
            try {
                val engine = PhotoProcessor(applicationContext)
                val available = engine.films
                val store = GrainPackStore(applicationContext)
                grainStore = store
                val grainResult = runCatching { store.openInstalled() }
                val installedGrains = grainResult.getOrDefault(emptyList())
                runOnUiThread {
                    if (!destroyed) {
                        processor = engine
                        films = available
                        grainIdentifiers = installedGrains
                        selectedFilm = films.firstOrNull { it.token == restoredFilmToken }
                            ?: films.firstOrNull {
                                it.name.contains("Portra", true) && it.name.contains("400")
                            } ?: films.firstOrNull()
                        status.text = if (films.isEmpty()) "没有可用的胶片效果。"
                            else if (grainResult.isFailure) "内置颗粒准备失败：${grainResult.exceptionOrNull()?.message}"
                            else ""
                        updateControls()
                        if (restoredUri != null && selectedFilm != null) loadPhoto(restoredUri)
                    }
                }
            } catch (_: Exception) {
                runOnUiThread { if (!destroyed) status.text = "胶片加载失败，请重新打开应用。" }
            }
        }
    }

    private fun buildUi() {
        window.statusBarColor = EditorPalette.background
        window.navigationBarColor = EditorPalette.background
        window.decorView.systemUiVisibility = 0
        if (Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(false)
        glassEnvironment = CatalogGlassEnvironment(); filmCards.clear(); stripTokens = emptyList(); stripSelectionToken = null
        val landscape = resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            clipChildren=false;clipToPadding=false
            setPadding(0,0,0,0)
            setOnApplyWindowInsetsListener { view, insets ->
                if (Build.VERSION.SDK_INT >= 30) {
                    val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                    view.setPadding(bars.left,bars.top,bars.right,bars.bottom)
                } else {
                    view.setPadding(insets.systemWindowInsetLeft,insets.systemWindowInsetTop,
                        insets.systemWindowInsetRight,insets.systemWindowInsetBottom)
                }
                insets
            }
        }
        val header = FrameLayout(this).apply { tag="editor_header";clipChildren=false;clipToPadding=false }
        val headerRow = LinearLayout(this).apply { gravity=Gravity.CENTER_VERTICAL;clipChildren=false;clipToPadding=false;setPadding(dp(14),0,dp(14),0) }
        importButton = button("", "import_photo") { choosePhoto() }.apply {
            contentDescription="导入照片"; icon(this,"open")
        }
        headerRow.addView(importButton,LinearLayout.LayoutParams(dp(80),dp(64)))
        headerRow.addView(LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER
            addView(label("CXO FilmPack",18f,EditorPalette.text).apply {
                typeface=Typeface.create("sans-serif-medium",Typeface.NORMAL);letterSpacing=.025f
            })
            addView(label("THE FILM WORKROOM",8f,EditorPalette.muted).apply {letterSpacing=.19f;setPadding(0,dp(3),0,0)})
        },LinearLayout.LayoutParams(0,-2,1f).apply {marginStart=dp(4)})
        exportButton = CatalogGlassButton(this,glassEnvironment,true).apply {
            text="导出";tag="export_photo";contentDescription="保存原尺寸照片"
            setOnClickListener { chooseExportDestination() };icon(this,"export",EditorPalette.background)
        }
        headerRow.addView(exportButton,LinearLayout.LayoutParams(dp(112),dp(64)))
        header.addView(headerRow,FrameLayout.LayoutParams(-1,-1))
        root.addView(header,LinearLayout.LayoutParams(-1,dp(64)))
        val body: ViewGroup=if(landscape) LinearLayout(this).apply {orientation=LinearLayout.HORIZONTAL;clipChildren=false;clipToPadding=false}
            else FrameLayout(this).apply {clipChildren=false;clipToPadding=false}
        root.addView(body,LinearLayout.LayoutParams(-1,0,1f))
        val preview=FrameLayout(this).apply {minimumHeight=dp(120);setBackgroundColor(EditorPalette.photoBackground);tag="photo_canvas"}
        photo=ZoomablePreviewImageView(this).apply {
            contentDescription="照片预览";tag="photo_preview"
            onCompareChanged = { before ->
                if (showOriginal != before) { showOriginal=before;updatePhoto();updateControls() }
            }
            var previousZoom=1f
            onTransformChanged = {
                val changed=kotlin.math.abs(zoomScale-previousZoom)>.001f
                previousZoom=zoomScale
                if(changed && zoomScale>1.01f)setEditorDrawerExpanded(false)
            }
        }
        preview.addView(photo,FrameLayout.LayoutParams(-1,-1))
        placeholder=label("◎\n\n每一张照片，都有胶片的可能\n\n轻点左上角，打开你的照片\nRAW · JPEG · PNG\nHEIC 视系统支持",15f,EditorPalette.muted).apply {
            gravity=Gravity.CENTER;setLineSpacing(dp(5).toFloat(),1f)
            setOnClickListener { if(importButton.isEnabled) choosePhoto() }
        }
        preview.addView(placeholder,FrameLayout.LayoutParams(-1,-1))
        photoInfo=label("",10f,EditorPalette.muted).apply {gravity=Gravity.CENTER_VERTICAL;maxLines=1}
        photoInfo.setPadding(dp(14),0,dp(14),0)
        if(landscape) body.addView(preview,LinearLayout.LayoutParams(0,-1,1f).apply {marginEnd=dp(12)})
        else body.addView(preview,FrameLayout.LayoutParams(-1,-1))

        // Tools share the ambient background; no enclosing card or bottom navigation.
        val filmControls=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;clipChildren=false;clipToPadding=false
            setPadding(dp(14),dp(8),dp(14),dp(4));tag="editor_tools"
        }
        val caption=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL}
        caption.addView(label("胶片与风格",12f,EditorPalette.text).apply {typeface=Typeface.DEFAULT_BOLD},LinearLayout.LayoutParams(0,dp(24),1f))
        filmControls.addView(caption)
        val filmHeading=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL;clipChildren=false;clipToPadding=false}
        filmButton=button("选择胶片", "choose_film") {showFilmPicker()}.apply {
            textSize=13f;maxLines=2;contentDescription="选择胶片，打开搜索列表"
        }
        favoriteButton=button("☆", "favorite_film") {toggleFavorite()}.apply {textSize=21f;contentDescription="收藏当前胶片"}
        filmHeading.addView(filmButton,LinearLayout.LayoutParams(0,dp(64),1f).apply {marginEnd=dp(6)})
        filmHeading.addView(favoriteButton,LinearLayout.LayoutParams(dp(80),dp(64)))
        filmControls.addView(filmHeading)
        filmStrip=LinearLayout(this).apply {
            orientation=LinearLayout.HORIZONTAL;setPadding(dp(4),dp(4),dp(4),dp(4));tag="film_strip_items"
            addOnLayoutChangeListener { _,left,top,right,bottom,oldLeft,oldTop,oldRight,oldBottom ->
                if(right-left!=oldRight-oldLeft || bottom-top!=oldBottom-oldTop) {
                    centerSelectedFilm();requestFilmThumbnails()
                }
            }
        }
        filmScroll=HorizontalScrollView(this).apply {
            tag="film_strip";contentDescription="全部胶片缩略图";isHorizontalScrollBarEnabled=false
            clipToPadding=false
            addView(filmStrip,FrameLayout.LayoutParams(-2,-1))
            setOnScrollChangeListener { _,_,_,_,_ ->
                // Coalesce fling frames, and only request the viewport plus one neighbour.
                removeCallbacks(thumbnailRefresh);postDelayed(thumbnailRefresh,90)
            }
        }
        filmControls.addView(filmScroll,LinearLayout.LayoutParams(-1,dp(102)).apply {topMargin=dp(6);bottomMargin=dp(4)})
        val intensityRow=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL;clipChildren=false;clipToPadding=false}
        intensityValue=label("强度 100",11f,EditorPalette.muted).apply {tag="intensity_value";minWidth=dp(65)}
        intensitySlider=CatalogGlassSlider(this,glassEnvironment).apply {
            tag="intensity_slider";max=200;keyProgressIncrement=1;progress=intensity
            contentDescription="胶片强度，0 到 200"
            var tracking=false
            setOnSeekBarChangeListener(object:CatalogGlassSlider.OnSeekBarChangeListener {
                override fun onStartTrackingTouch(bar:CatalogGlassSlider?){tracking=true}
                override fun onProgressChanged(bar:CatalogGlassSlider?,value:Int,fromUser:Boolean){
                    intensity=value.coerceIn(0,200);updateIntensityLabel()
                    if(!applyingIntensity && original!=null && !exporting && !choosingDestination)renderPreview(tracking)
                }
                override fun onStopTrackingTouch(bar:CatalogGlassSlider?){tracking=false;if(original!=null && !exporting && !choosingDestination)renderPreview()}
            })
        }
        resetIntensityButton=button("", "reset_intensity") {setIntensity(100,true)}.apply {
            icon(this,"reset");contentDescription="恢复胶片强度100";background=null
        }
        intensityRow.addView(intensityValue,LinearLayout.LayoutParams(dp(65),dp(44)))
        intensityRow.addView(intensitySlider,LinearLayout.LayoutParams(0,dp(64),1f))
        intensityRow.addView(resetIntensityButton,LinearLayout.LayoutParams(dp(80),dp(64)))
        filmControls.addView(intensityRow.apply {minimumHeight=dp(64)})
        filmControls.addView(View(this).apply {setBackgroundColor(0x20D9B780)},LinearLayout.LayoutParams(-1,dp(1)))
        val grainRow=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL;clipChildren=false;clipToPadding=false;setPadding(0,dp(4),0,0)}
        grainButton=button("颗粒 · 关闭", "grain_settings") {showGrainSettings()}.apply {icon(this,"grain");maxLines=1}
        grainRow.addView(grainButton,LinearLayout.LayoutParams(0,dp(64),1f))
        filmControls.addView(grainRow)
        filmModuleView=filmControls
        val postControls=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;setPadding(dp(14),dp(8),dp(14),dp(4))
            tag="post_tools";clipChildren=false;clipToPadding=false
            addView(label("后处理",16f,EditorPalette.text).apply {setPadding(dp(6),0,0,dp(8))})
            postPanel=createPostSettingsPanel(this@MainActivity,{postSettings},glassEnvironment) { next, tracking ->
                if(!exporting && !choosingDestination && !importingPack) {
                    postSettings=next
                    if(!tracking)next.save(preferences)
                    editorDrawerTitle?.text="后处理 · ${if(next.isIdentity) "未调整" else "已调整"}"
                    if(original!=null)renderPreview(tracking)
                }
            }
            addView(postPanel)
            addView(button("重置全部后处理","post_reset") {
                if(exporting || choosingDestination || importingPack)return@button
                postSettings=PostEditingSettings();postSettings.save(preferences);postPanel?.refresh();updateControls()
                if(original!=null)renderPreview()
            },LinearLayout.LayoutParams(-1,dp(64)))
        }
        postModuleView=postControls
        val controls=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;tag="editor_tools";clipChildren=false;clipToPadding=false
            val modules=LinearLayout(this@MainActivity).apply {
                gravity=Gravity.CENTER_VERTICAL;setPadding(dp(14),0,dp(14),0);clipChildren=false;clipToPadding=false
            }
            filmModuleButton=button("胶片","module_film") {switchEditorModule(0)}
            postModuleButton=button("后处理","module_post") {switchEditorModule(1)}
            modules.addView(filmModuleButton,LinearLayout.LayoutParams(0,dp(64),1f))
            modules.addView(postModuleButton,LinearLayout.LayoutParams(0,dp(64),1f))
            addView(modules);addView(filmControls);addView(postControls)
        }
        switchEditorModule(editorModule)
        val dockScroll=SliderAxisScrollView(this).apply {
            isFillViewport=false;isVerticalScrollBarEnabled=false;tag="editor_drawer_scroll"
            addOnLayoutChangeListener { view,_,_,_,_,_,_,_,_ ->
                view.clipBounds=android.graphics.Rect(0,0,view.width,view.height)
            }
            addView(controls,FrameLayout.LayoutParams(-1,-2))
        }
        var panelContent: LinearLayout? = null
        if(landscape) {
            body.addView(dockScroll,LinearLayout.LayoutParams(dp(350),-1))
        } else {
            val drawer=FrameLayout(this).apply {
                tag="editor_drawer";clipChildren=false;clipToPadding=false
                background=EditorPalette.agedSurface()
                addOnLayoutChangeListener { view,_,_,_,_,_,_,_,_ ->
                    view.clipBounds=android.graphics.Rect(0,0,view.width,view.height)
                }
                elevation=dp(12).toFloat()
            }
            val drawerContent=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;clipChildren=false;clipToPadding=false}
            panelContent=drawerContent
            val drawerHandle=LinearLayout(this).apply {
                orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER;tag="editor_drawer_handle"
                setPadding(dp(16),dp(4),dp(16),0);isClickable=true;isFocusable=true
            }
            drawerHandle.addView(View(this).apply {setBackground(EditorPalette.rounded(EditorPalette.muted,dp(3).toFloat()))},LinearLayout.LayoutParams(dp(34),dp(4)))
            val drawerTitle=TextView(this).apply {
                tag="editor_drawer_title";text="胶片与风格 · ${selectedFilm?.name ?: "选择胶片"}"
                setTextColor(EditorPalette.text);textSize=13f;maxLines=1;ellipsize=android.text.TextUtils.TruncateAt.END
                gravity=Gravity.CENTER_VERTICAL
            }
            editorDrawerTitle=drawerTitle
            drawerHandle.addView(drawerTitle,LinearLayout.LayoutParams(-1,dp(44)))
            drawerContent.addView(drawerHandle,LinearLayout.LayoutParams(-1,dp(58)))
            dockScroll.visibility=View.GONE
            drawerContent.addView(dockScroll,LinearLayout.LayoutParams(-1,0,1f))
            drawer.addView(drawerContent,FrameLayout.LayoutParams(-1,-1))
            val minHeight=dp(110)
            var dragStartY=0f;var dragStartHeight=minHeight;var dragged=false
            fun maxDrawerHeight()=expandedDrawerHeight(drawer)
            fun resize(height:Int) {
                val clamped=height.coerceIn(minHeight,maxDrawerHeight())
                drawer.layoutParams=drawer.layoutParams.apply {this.height=clamped}
                dockScroll.visibility=if(clamped>minHeight+dp(12))View.VISIBLE else View.GONE
                editorDrawerExpanded=clamped>minHeight+dp(12)
            }
            fun settle(expanded:Boolean,animate:Boolean=true) {
                drawerAnimator?.cancel()
                val from=drawer.height.coerceAtLeast(minHeight)
                val target=if(expanded)maxDrawerHeight() else minHeight
                editorDrawerExpanded=expanded
                if(!expanded) dockScroll.visibility=View.GONE else dockScroll.visibility=View.VISIBLE
                if(!animate || from==target) {resize(target);return}
                drawerAnimator=ValueAnimator.ofInt(from,target).apply {
                    duration=220;interpolator=DecelerateInterpolator()
                    addUpdateListener { resize(it.animatedValue as Int) }
                    start()
                }
            }
            fun handleTouch(view:View,event:android.view.MotionEvent):Boolean {
                when(event.actionMasked) {
                    android.view.MotionEvent.ACTION_DOWN -> {
                        drawerAnimator?.cancel();dragStartY=event.rawY;dragStartHeight=drawer.height.coerceAtLeast(minHeight);dragged=false;return true
                    }
                    android.view.MotionEvent.ACTION_MOVE -> {
                        val dy=event.rawY-dragStartY
                        if(kotlin.math.abs(dy)>dp(6))dragged=true
                        if(dragged)resize((dragStartHeight-dy).toInt())
                        return true
                    }
                    android.view.MotionEvent.ACTION_UP -> {
                        if(dragged) settle(drawer.height>minHeight+(maxDrawerHeight()-minHeight)/2)
                        else view.performClick()
                        return true
                    }
                    android.view.MotionEvent.ACTION_CANCEL -> {settle(editorDrawerExpanded);return true}
                }
                return true
            }
            drawerHandle.setOnTouchListener(::handleTouch)
            drawerHandle.setOnClickListener { settle(!editorDrawerExpanded) }
            body.addView(drawer,FrameLayout.LayoutParams(-1,minHeight,Gravity.BOTTOM))
            editorDrawer=drawer
            fun syncPreviewViewport() {
                val params=preview.layoutParams as FrameLayout.LayoutParams
                val bottom=drawer.height.coerceAtLeast(minHeight)
                if(params.bottomMargin!=bottom) {
                    params.bottomMargin=bottom
                    preview.layoutParams=params
                }
            }
            drawer.addOnLayoutChangeListener { _,_,_,_,_,_,_,_,_ -> syncPreviewViewport() }
            preview.post { syncPreviewViewport() }
        }
        val progressRow=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL;clipChildren=false;clipToPadding=false}
        progressRow.setPadding(dp(14),0,dp(14),0)
        status=label("",10f,EditorPalette.muted).apply {maxLines=2;accessibilityLiveRegion=View.ACCESSIBILITY_LIVE_REGION_POLITE}
        progressRow.addView(status,LinearLayout.LayoutParams(0,dp(38),1f))
        cancelButton=button("取消", "cancel_processing") {cancelWork()}.apply {textSize=11f;background=null}
        progressRow.addView(cancelButton,LinearLayout.LayoutParams(dp(104),dp(48)))
        val panelFooter=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;tag="editor_panel_status"
            setBackgroundColor(Color.TRANSPARENT)
            progressRow.removeView(status)
            val information=LinearLayout(this@MainActivity).apply {
                orientation=LinearLayout.VERTICAL
                addView(photoInfo,LinearLayout.LayoutParams(-1,dp(18)))
                addView(status,LinearLayout.LayoutParams(-1,dp(30)))
            }
            progressRow.addView(information,0,LinearLayout.LayoutParams(0,dp(48),1f))
            addView(progressRow,LinearLayout.LayoutParams(-1,dp(48)))
        }
        progress=ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal).apply {
            max=100;progressTintList=android.content.res.ColorStateList.valueOf(EditorPalette.accent)
            importantForAccessibility=View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }
        panelFooter.addView(progress,LinearLayout.LayoutParams(-1,dp(3)))
        if(panelContent!=null) panelContent.addView(panelFooter,LinearLayout.LayoutParams(-1,dp(51)))
        else root.addView(panelFooter,LinearLayout.LayoutParams(-1,dp(51)))
        setContentView(catalogWindow(this,glassEnvironment,root))
        root.requestApplyInsets()
        updateIntensityLabel()
    }

    private fun switchEditorModule(module:Int) {
        editorModule=module
        filmModuleView?.visibility=if(module==0)View.VISIBLE else View.GONE
        postModuleView?.visibility=if(module==1)View.VISIBLE else View.GONE
        filmModuleButton.text=if(module==0)"● 胶片" else "胶片"
        postModuleButton.text=if(module==1)"● 后处理" else "后处理"
        editorDrawerTitle?.text=if(module==1) "后处理 · ${if(postSettings.isIdentity) "未调整" else "已调整"}"
            else "胶片与风格 · ${selectedFilm?.name ?: "选择胶片"}"
        editorDrawer?.post {if(editorDrawerExpanded)setEditorDrawerExpanded(true)}
    }

    private fun showPostSettings(section:String) {
        if(exporting || choosingDestination || importingPack)return
        exportSettingsDialog?.dismiss()
        postDialog?.dismiss()
        val environment=CatalogGlassEnvironment().apply {bitmap=glassEnvironment.bitmap}
        val dialog=createPostSettingsDialog(this,postSettings,section,environment) { next ->
            postSettings=next;next.save(preferences);updateControls()
            if(original!=null)renderPreview()
        }
        postDialog=dialog
        dialog.setOnDismissListener {postDialog=null}
        attachDialogOwners(dialog);dialog.show();styleSheet(dialog)
    }

    private fun icon(button:CatalogGlassButton,kind:String,color:Int=EditorPalette.text) {
        button.iconKind=kind;button.setTextColor(color)
    }
    private fun label(text:String,size:Float,color:Int)=TextView(this).apply {this.text=text;textSize=size;setTextColor(color)}
    private fun button(text:String,identifier:String,environment:CatalogGlassEnvironment=glassEnvironment,action:()->Unit)=CatalogGlassButton(this,environment).apply {
        this.text=text;tag=identifier;setOnClickListener {action()}
    }
    private fun dp(value:Int)=(value*resources.displayMetrics.density+.5f).toInt()

    /** Fit tools without a weighted empty region; scroll on short screens. */
    private fun expandedDrawerHeight(drawer: FrameLayout): Int {
        val body=drawer.parent as? View ?: return dp(110)
        val inner=drawer.getChildAt(0) as ViewGroup
        val scroll=inner.getChildAt(1) as ViewGroup
        val tools=scroll.getChildAt(0)
        val width=drawer.width.takeIf { it>0 } ?: body.width
        tools.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0,View.MeasureSpec.UNSPECIFIED))
        val contentHeight=dp(110)+tools.measuredHeight
        return minOf(contentHeight,(body.height*.72f).toInt()).coerceAtLeast(dp(110))
    }

    private fun setEditorDrawerExpanded(expanded:Boolean) {
        val drawer=editorDrawer ?: return
        val inner=drawer.getChildAt(0) as? ViewGroup ?: return
        val scroll=inner.getChildAt(1) ?: return
        val minimum=dp(110)
        val maximum=expandedDrawerHeight(drawer)
        val target=if(expanded)maximum else minimum
        if(editorDrawerExpanded==expanded && (drawer.height==target || drawerAnimator?.isRunning==true))return
        drawerAnimator?.cancel()
        editorDrawerExpanded=expanded
        scroll.visibility=if(expanded)View.VISIBLE else View.GONE
        val start=drawer.height.coerceAtLeast(minimum)
        if(start==target) {
            drawer.layoutParams=drawer.layoutParams.apply {height=target}
            return
        }
        drawerAnimator=ValueAnimator.ofInt(start,target).apply {
            duration=220;interpolator=DecelerateInterpolator()
            addUpdateListener { value ->
                drawer.layoutParams=drawer.layoutParams.apply {height=value.animatedValue as Int}
            }
            start()
        }
    }

    private fun toggleFavorite() {
        val token=selectedFilm?.token ?: return
        val favorites=preferences.getStringSet("film.favorites",emptySet()).orEmpty().toMutableSet()
        if(!favorites.add(token))favorites.remove(token)
        preferences.edit().putStringSet("film.favorites",favorites).apply()
        updateControls()
        requestFilmThumbnails()
    }

    private fun updateFilmStrip() {
        if(!::filmStrip.isInitialized)return
        val favorites=preferences.getStringSet("film.favorites",emptySet()).orEmpty()
        val tokens=films.map {it.token}
        if(tokens!=stripTokens) {
            stripTokens=tokens;filmStrip.removeAllViews();filmCards.clear()
            for((index,film) in films.withIndex()) {
                val token=film.token
                val card=FilmCard(this,token,film.name,listOf(EditorPalette.accent,0xFFB7B08A.toInt(),0xFFC69D85.toInt())[index%3])
                card.tag="film_card_$token"
                card.thumbnail.setImageBitmap(thumbnailCache[token]);card.setOnClickListener {
                    if(filmButton.isEnabled && selectedFilm?.token!=token) {
                        selectedFilm=film
                        if(original!=null)renderPreview() else updateControls()
                    }
                }
                filmCards[token]=card
                filmStrip.addView(card,LinearLayout.LayoutParams(dp(100),-1).apply {marginEnd=dp(8)})
            }
        }
        filmCards.forEach {(token,card)->card.markSelected(token==selectedFilm?.token);card.isEnabled=filmButton.isEnabled;card.alpha=if(filmButton.isEnabled)1f else .4f}
        favoriteButton.text=if(selectedFilm?.token in favorites)"★" else "☆"
        favoriteButton.setTextColor(if(selectedFilm?.token in favorites)EditorPalette.accent else EditorPalette.muted)
        favoriteButton.isEnabled=filmButton.isEnabled
        val selection=selectedFilm?.token
        if(stripSelectionToken!=selection) {
            stripSelectionToken=selection
            filmScroll.post {
                if(!destroyed && stripSelectionToken==selection) {
                    centerSelectedFilm();requestFilmThumbnails()
                }
            }
        }
    }

    private fun centerSelectedFilm() {
        filmCards[selectedFilm?.token]?.let { card ->
            if(card.width>0 && filmScroll.width>0) {
                val target=(card.left-(filmScroll.width-card.width)/2).coerceAtLeast(0)
                filmScroll.smoothScrollTo(target,0)
            }
        }
    }

    /** All films are browsable, but numerical thumbnails are rendered only near the viewport.
     * One batch on the existing serial worker; newer edits/exports preempt it. No grain. */
    private fun requestFilmThumbnails() {
        if(destroyed || !::filmScroll.isInitialized)return
        val left=filmScroll.scrollX-dp(108)
        val right=filmScroll.scrollX+filmScroll.width+dp(108)
        val wanted=filmCards.filterValues {it.right>left && it.left<right}.keys.toList()
        if(wanted!=thumbnailWantedTokens) {
            thumbnailWantedTokens=wanted;thumbnailViewport.incrementAndGet()
        }
        if(thumbnailTaskRunning || loading || rendering || exporting || choosingDestination || importingPack)return
        val source=original ?: return;val engine=processor ?: return;val uri=sourceUri
        val generation=imageGeneration.get();val preview=previewGeneration.get();val viewport=thumbnailViewport.get()
        val needed=wanted.filter {it !in thumbnailCache && it !in thumbnailUnavailable}.mapNotNull {t->films.firstOrNull {it.token==t}}
        if(needed.isEmpty())return
        thumbnailTaskRunning=true
        worker.execute {
            val cancelled={destroyed || generation!=imageGeneration.get() || preview!=previewGeneration.get() || viewport!=thumbnailViewport.get()}
            var small:Bitmap?=null
            try {
                if(!cancelled()) {
                    val scale=144f/maxOf(source.width,source.height)
                    val input=Bitmap.createScaledBitmap(source,maxOf(1,(source.width*scale).toInt()),maxOf(1,(source.height*scale).toInt()),true)
                    small=input
                    for(film in needed) {
                        if(cancelled())break
                        val result=runCatching {engine.render(input,film,{},cancelled,intensity=100,rawUri=uri)}
                        val bitmap=result.getOrNull()
                        if(bitmap!=null)runOnUiThread {
                            if(!destroyed && generation==imageGeneration.get()) {
                                thumbnailCache[film.token]=bitmap;filmCards[film.token]?.thumbnail?.setImageBitmap(bitmap)
                            } else bitmap.recycle()
                        } else if(!cancelled())runOnUiThread {
                            if(generation==imageGeneration.get())thumbnailUnavailable.add(film.token)
                        }
                    }
                }
            } finally {
                small?.let {if(it!==source)it.recycle()}
                runOnUiThread {
                    thumbnailTaskRunning=false
                    if(!destroyed)requestFilmThumbnails()
                }
            }
        }
    }

    override fun onConfigurationChanged(newConfig:Configuration) {
        super.onConfigurationChanged(newConfig)
        val message=status.text
        postDialog?.dismiss();filmInfoDialog?.dismiss();filmDialog?.dismiss();grainDialog?.dismiss()
        buildUi();status.text=message;updatePhoto();updateControls()
    }

    private fun choosePhoto() {
        try {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                // Several document providers label RW2 as application/octet-stream.
                // Decode validates the actual header; do not hide RAW behind image/*.
                type = "*/*"
                addCategory(Intent.CATEGORY_OPENABLE)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
            }, REQUEST_PHOTO)
        } catch (_: Exception) {
            status.text = "无法打开照片选择器，请稍后重试。"
        }
    }

    /** Also accepts an app-owned fixture URI for instrumentation without driving a system picker. */
    fun loadPhoto(uri: Uri) {
        if (destroyed || exporting || choosingDestination || importingPack || processor == null) return
        val engine = processor ?: return
        val generation = imageGeneration.incrementAndGet()
        previewGeneration.incrementAndGet()
        previewBarrier.incrementAndGet();pendingPreview=null;previewInteraction=false
        thumbnailCache.clear();thumbnailUnavailable.clear();thumbnailViewport.incrementAndGet();filmCards.values.forEach {it.thumbnail.setImageDrawable(null)}
        photo.resetZoom()
        sourceUri = uri
        original = null
        rendered = null
        grainPreview = null
        val useGrain = grainSettings.enabled
        showOriginal = false
        loading = true
        rendering = false
        updatePhoto()
        status.text = "正在读取照片…"
        updateControls()
        worker.execute {
            try {
                val cancelled = { destroyed || generation != imageGeneration.get() }
                previewBaseCache.close()
                engine.beginImport()
                val metadata = if (useGrain) engine.decodeGrainPreview(uri,cancelled=cancelled) else null
                val decoded = metadata?.bitmap ?: engine.decodePreview(uri,cancelled=cancelled)
                runOnUiThread {
                    if (!destroyed && generation == imageGeneration.get()) {
                        original = decoded
                        grainPreview = metadata
                        loading = false
                        updatePhoto()
                        renderPreview()
                    } else decoded.recycle()
                }
            } catch (failure: Exception) {
                runOnUiThread {
                    if (!destroyed && generation == imageGeneration.get()) {
                        loading = false
                        if (!useGrain) sourceUri = null
                        status.text = if (useGrain) "无法生成颗粒预览：${failure.message}。可关闭颗粒后重试。"
                            else "无法读取照片：${failure.message ?: "请检查访问权限或照片格式"}"
                        updateControls()
                    }
                }
            }
        }
    }

    private fun renderPreview(interactive: Boolean = false) {
        val source = original ?: return
        val film = selectedFilm ?: return
        if(processor==null || destroyed || exporting || choosingDestination || importingPack)return
        val generation=previewGeneration.incrementAndGet()
        if(!interactive || !previewInteraction)previewBarrier.incrementAndGet()
        previewInteraction=interactive
        pendingPreview=PreviewRequest(generation,previewBarrier.get(),
            PreviewKey(imageGeneration.get(),film.id,intensity,grainSettings),source,film,sourceUri,grainPreview,postSettings,interactive)
        val wasRendering=rendering
        rendering=true;showOriginal=false;precisePreviewReady=false
        photo.cancelComparison();photo.comparisonEnabled=false
        status.text=if(interactive) "实时预览 · 调整中…" else "正在生成精细预览…"
        if(!wasRendering)updateControls()
        startPreviewTask()
    }

    private fun startPreviewTask() {
        if(previewTaskRunning || destroyed)return
        val request=pendingPreview ?: return
        val engine=processor ?: return
        pendingPreview=null;previewTaskRunning=true
        worker.execute {
            val started=SystemClock.elapsedRealtime()
            val cancelled={destroyed || request.barrier!=previewBarrier.get() || request.key.image!=imageGeneration.get()}
            var result:Bitmap?=null
            var failure:Exception?=null
            try {
                val settings=request.key.grain
                result=previewBaseCache.frame(request.key,request.source,request.interactive,settings.enabled,cancelled,
                    renderBase={ input ->
                        val layer=if(settings.enabled) {
                            val identifier=settings.resolve(request.film,grainIdentifiers)
                            requireNotNull(grainStore).prepare(engine.grainRequest(requireNotNull(request.metadata) {
                                "请重新读取照片后使用颗粒"
                            },request.film,identifier,settings.format,settings.manualSize,settings.intensity,settings.seed))
                        } else null
                        engine.render(input,request.film,{},cancelled,intensity=request.key.intensity,grain=layer,rawUri=request.uri)
                    },edit={ owned -> applyPostEditing(applicationContext,owned,request.post,cancelled) })
            } catch(error:Exception) {failure=error}
            val completed=result;val error=failure
            runOnUiThread {
                previewTaskRunning=false
                if(!cancelled()) {
                    if(completed!=null) {
                        rendered=completed
                        precisePreviewReady=!request.interactive && pendingPreview==null
                        rendering=request.interactive || pendingPreview!=null
                        status.text=if(request.interactive) "实时预览 · ${SystemClock.elapsedRealtime()-started} 毫秒"
                            else "预览已就绪 · ${formatSeconds(SystemClock.elapsedRealtime()-started)} 秒"
                        updatePhoto()
                    } else {
                        rendering=pendingPreview!=null
                        status.text=if(error is CancellationException) "已取消" else "预览生成失败：${error?.message ?: "请重试"}"
                    }
                    if(!request.interactive || error!=null)updateControls()
                    if(!rendering)requestFilmThumbnails()
                } else completed?.recycle()
                startPreviewTask()
            }
        }
    }

    private fun showFilmPicker(onlyFavorites:Boolean=false) {
        if (exporting || choosingDestination || importingPack || films.isEmpty()) return
        val dialogEnvironment=CatalogGlassEnvironment().apply {bitmap=glassEnvironment.bitmap}
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            clipChildren=false;clipToPadding=false
            setPadding(dp(16), dp(4), dp(16), 0)
            background=EditorPalette.rounded(EditorPalette.surface,dp(28).toFloat())
        }
        panel.addView(label("胶片与风格 · ${films.size} 项",20f,EditorPalette.text).apply {
            tag="film_picker_title";setPadding(dp(8),dp(8),dp(8),dp(12))
        },LinearLayout.LayoutParams(-1,dp(54)))
        val search = EditText(this).apply {
            hint = "搜索胶片名称"
            isSingleLine = true
            inputType = android.text.InputType.TYPE_CLASS_TEXT
            contentDescription = "搜索胶片";tag="film_search"
            isFocusableInTouchMode=true
        }
        panel.addView(search,LinearLayout.LayoutParams(-1,dp(52)))
        search.setTextColor(EditorPalette.text)
        search.setHintTextColor(EditorPalette.muted)
        search.setPadding(dp(14),dp(12),dp(14),dp(12))
        search.background=EditorPalette.rounded(EditorPalette.surface,dp(16).toFloat())
        val categories = listOf("全部类别", "★ 收藏") + films.map { it.category }.distinct().sorted()
        val category = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, categories)
            contentDescription = "胶片类别";tag="film_category"
        }
        panel.addView(category,LinearLayout.LayoutParams(-1,dp(48)))
        if(onlyFavorites)category.setSelection(1)
        val results = ListView(this).apply {
            contentDescription = "胶片列表";tag="film_results"
            // Glass interop allows overflow globally. A scrolling list must keep
            // its own hard viewport so rows cannot paint over search/category.
            addOnLayoutChangeListener { view,_,_,_,_,_,_,_,_ ->
                view.clipBounds=android.graphics.Rect(0,0,view.width,view.height)
            }
        }
        panel.addView(results, LinearLayout.LayoutParams(-1,0,1f))
        var shown = films
        fun filter() {
            val query = search.text.toString().trim()
            val selectedCategory = categories[category.selectedItemPosition.coerceAtLeast(0)]
            shown = films.filter {
                (selectedCategory == "全部类别" || it.category == selectedCategory ||
                    (selectedCategory == "★ 收藏" && it.token in preferences.getStringSet("film.favorites",emptySet()).orEmpty())) &&
                    (query.isBlank() || it.name.contains(query, true) || it.category.contains(query, true))
            }
            results.adapter = object:ArrayAdapter<FilmChoice>(this,android.R.layout.simple_list_item_1,shown) {
                override fun getView(position:Int,convertView:View?,parent:ViewGroup):View {
                    val film=getItem(position)!!
                    val chosen=film.token==selectedFilm?.token
                    return LinearLayout(this@MainActivity).apply {
                        orientation=LinearLayout.HORIZONTAL;gravity=Gravity.CENTER_VERTICAL
                        descendantFocusability=ViewGroup.FOCUS_BLOCK_DESCENDANTS
                        setPadding(dp(4),dp(4),dp(4),dp(4))
                        background=EditorPalette.rounded(if(chosen)0x18E2C695 else Color.TRANSPARENT,dp(12).toFloat())
                        addView(label("${if(chosen) "●  " else ""}${film.name}\n${film.category}${if(film.experimental) " · 实验效果" else ""}",14f,
                            if(chosen)EditorPalette.accent else EditorPalette.text).apply {
                            tag="film_row_label";setPadding(dp(10),dp(10),dp(8),dp(10));setLineSpacing(dp(6).toFloat(),1f)
                        },LinearLayout.LayoutParams(0,-2,1f))
                        addView(TextView(this@MainActivity).apply {
                            text="ⓘ";textSize=24f;gravity=Gravity.CENTER
                            setTextColor(EditorPalette.accent)
                            tag="film_info_${film.token}";contentDescription="${film.name}的胶片介绍"
                            isFocusable=false;isClickable=true
                            val state=android.util.TypedValue()
                            theme.resolveAttribute(android.R.attr.selectableItemBackgroundBorderless,state,true)
                            setBackgroundResource(state.resourceId)
                            setOnClickListener { showFilmInfo(film) }
                        },LinearLayout.LayoutParams(dp(48),dp(48)))
                    }
                }
            }
        }
        search.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = filter()
            override fun afterTextChanged(s: Editable?) = Unit
        })
        category.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) = filter()
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        panel.addView(button("关闭", "film_close",dialogEnvironment) {filmDialog?.dismiss()},LinearLayout.LayoutParams(-1,dp(64)))
        val dialog = AlertDialog.Builder(this).setView(catalogSheet(this,dialogEnvironment,panel)).create()
        results.setOnItemClickListener { _, _, position, _ ->
            val choice = shown.getOrNull(position) ?: return@setOnItemClickListener
            if (choice.token != selectedFilm?.token || (original != null && rendered == null && !rendering)) {
                selectedFilm = choice
                if (original != null) renderPreview() else updateControls()
            }
            dialog.dismiss()
        }
        filmDialog = dialog
        dialog.setOnDismissListener { filmDialog = null }
        filter()
        // Opening the list should not summon the IME until the search is tapped.
        panel.isFocusableInTouchMode=true
        panel.requestFocus()
        attachDialogOwners(dialog)
        dialog.show()
        styleSheet(dialog)
        dialog.window?.apply {
            // AlertDialog cannot discover an EditText hosted in AndroidView at
            // creation time and otherwise puts this window above the keyboard.
            clearFlags(WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE)
            setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE or WindowManager.LayoutParams.SOFT_INPUT_STATE_HIDDEN)
        }
        fun showSearchKeyboard() {
            search.requestFocus()
            search.post {
                if(dialog.isShowing) {
                    val ime=getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager
                    ime.showSoftInput(search,android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT)
                }
            }
        }
        search.setOnClickListener { showSearchKeyboard() }
        search.setOnFocusChangeListener { _,focused -> if(focused) showSearchKeyboard() }
    }

    /** Read-only details preserve the underlying picker and selection. */
    private fun showFilmInfo(film:FilmChoice) {
        filmInfoDialog?.dismiss()
        val ime=getSystemService(INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager
        filmDialog?.window?.decorView?.let { ime.hideSoftInputFromWindow(it.windowToken,0) }
        val detail=filmDescriptions.optJSONObject(film.token)
        val environment=CatalogGlassEnvironment().apply {bitmap=glassEnvironment.bitmap}
        val panel=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;setPadding(dp(20),dp(12),dp(20),0)
            setBackgroundColor(EditorPalette.surface)
        }
        panel.addView(label(film.name,22f,EditorPalette.text).apply {
            tag="film_info_title";setPadding(dp(4),dp(4),dp(4),dp(12))
        },LinearLayout.LayoutParams(-1,-2))
        val scroll=ScrollView(this).apply {
            isFillViewport=false;clipChildren=true;clipToPadding=true
            defaultFocusHighlightEnabled=false
            addOnLayoutChangeListener { v,_,_,_,_,_,_,_,_ -> v.clipBounds=android.graphics.Rect(0,0,v.width,v.height) }
        }
        val body=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setPadding(dp(4),0,dp(4),dp(16))}
        val metadata=listOf("manufacturer" to "创建者","type" to "类型","introduced" to "首次投产年份","discontinued" to "停产年份")
            .mapNotNull { (key,title) -> detail?.optString(key)?.takeIf {it.isNotBlank()}?.let {"$title：$it"} }
            .joinToString("\n")
        body.addView(label(metadata.ifBlank {"类型：${film.category}"},14f,EditorPalette.muted).apply {
            tag="film_info_metadata";setLineSpacing(dp(5).toFloat(),1f);setPadding(0,0,0,dp(18))
        })
        body.addView(label(detail?.optString("description")?.takeIf {it.isNotBlank()} ?: "此效果暂无可用的原版中文介绍。",16f,EditorPalette.text).apply {
            tag="film_info_description";setLineSpacing(dp(7).toFloat(),1f);setTextIsSelectable(true)
        })
        scroll.addView(body,FrameLayout.LayoutParams(-1,-2))
        panel.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        panel.addView(button("返回胶片列表","film_info_close",environment) {filmInfoDialog?.dismiss()},LinearLayout.LayoutParams(-1,dp(64)))
        val dialog=AlertDialog.Builder(this).setView(catalogSheet(this,environment,panel)).create()
        filmInfoDialog=dialog
        dialog.setOnDismissListener {filmInfoDialog=null}
        attachDialogOwners(dialog);dialog.show();styleSheet(dialog)
    }

    private fun attachDialogOwners(dialog:AlertDialog) {
        dialog.window?.decorView?.apply {
            setViewTreeLifecycleOwner(this@MainActivity)
            setViewTreeSavedStateRegistryOwner(this@MainActivity)
        }
    }

    private fun styleSheet(dialog:AlertDialog) {
        dialog.window?.apply {
            setBackgroundDrawable(EditorPalette.rounded(EditorPalette.surface,dp(28).toFloat()))
            setGravity(Gravity.BOTTOM)
            setLayout(-1,-2)
            statusBarColor=EditorPalette.surface
            navigationBarColor=EditorPalette.surface
            decorView.systemUiVisibility=0
            addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND)
            attributes=attributes.apply {dimAmount=.55f}
        }
        dialog.getButton(AlertDialog.BUTTON_NEGATIVE)?.setTextColor(EditorPalette.muted)
        dialog.getButton(AlertDialog.BUTTON_POSITIVE)?.setTextColor(EditorPalette.accent)
    }

    private fun chooseExportDestination() {
        val film = selectedFilm ?: return
        if (!precisePreviewReady || sourceUri == null || original == null || rendered == null ||
            exporting || choosingDestination || loading || rendering || importingPack) return
        // Freeze the editing state for both the settings sheet and document picker.
        pendingExportPost = postSettings
        pendingExportFilm = film
        pendingExportIntensity = intensity
        pendingExportGrain = grainSettings
        pendingExportPreview = grainPreview
        choosingDestination = true
        updateControls()
        showExportOptions(film)
    }

    private fun showExportOptions(film: FilmChoice) {
        val environment = CatalogGlassEnvironment()
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(EditorPalette.surface)
            setPadding(dp(16),dp(12),dp(16),dp(8))
            clipChildren = false; clipToPadding = false
        }
        panel.addView(label("导出照片",21f,EditorPalette.text))
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; clipChildren = false; clipToPadding = false
        }
        val scroll = SliderAxisScrollView(this).apply {
            isFillViewport = false; clipChildren = false; clipToPadding = false
            addView(body,ViewGroup.LayoutParams(-1,-2))
        }
        panel.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        body.addView(label("保存格式",13f,EditorPalette.muted))
        val format = Spinner(this).apply {
            tag = "export_format"
            adapter = ArrayAdapter(this@MainActivity,android.R.layout.simple_spinner_dropdown_item,
                listOf("JPEG · 体积更小","TIFF · 8位 RGB，无损"))
            setSelection(if(preferences.getString("export.format","JPEG")=="TIFF") 1 else 0)
        }
        body.addView(format,LinearLayout.LayoutParams(-1,dp(52)))
        val qualityLabel = label("JPEG 质量",14f,EditorPalette.text)
        body.addView(qualityLabel)
        val quality = CatalogGlassSlider(this,environment).apply {
            tag = "export_quality"; max = 99
            progress = preferences.getInt("export.quality",98).coerceIn(1,100)-1
            contentDescription = "JPEG保存质量，1到100"
        }
        fun updateQuality() {
            val jpeg = format.selectedItemPosition==0
            quality.isEnabled = jpeg
            qualityLabel.text = if(jpeg) "JPEG 质量 ${quality.progress+1}" else "TIFF 无损保存 · 无需设置质量"
            qualityLabel.alpha = if(jpeg) 1f else .6f
        }
        quality.setOnSeekBarChangeListener(object:CatalogGlassSlider.OnSeekBarChangeListener {
            override fun onProgressChanged(bar:CatalogGlassSlider?,value:Int,fromUser:Boolean) {updateQuality()}
            override fun onStartTrackingTouch(bar:CatalogGlassSlider?) = Unit
            override fun onStopTrackingTouch(bar:CatalogGlassSlider?) = Unit
        })
        body.addView(quality,LinearLayout.LayoutParams(-1,dp(64)))
        body.addView(label("输出分辨率 · 保持比例，只缩小",14f,EditorPalette.text))
        val edges = listOf(0,6000,4000,3000,2000,1600,1080,-1)
        val storedEdge = preferences.getInt("export.longEdge",0).coerceIn(0,65535)
        val resolution = Spinner(this).apply {
            tag = "export_resolution"
            adapter = ArrayAdapter(this@MainActivity,android.R.layout.simple_spinner_dropdown_item,
                listOf("原始尺寸","最长边 6000 像素","最长边 4000 像素","最长边 3000 像素",
                    "最长边 2000 像素","最长边 1600 像素","最长边 1080 像素","自定义最长边…"))
            setSelection(edges.indexOf(storedEdge).takeIf {it>=0} ?: edges.lastIndex)
        }
        body.addView(resolution,LinearLayout.LayoutParams(-1,dp(52)))
        val customEdge = EditText(this).apply {
            tag = "export_custom_edge"; hint = "最长边像素，1–65535"
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            setSingleLine(true); setTextColor(EditorPalette.text); setHintTextColor(EditorPalette.muted)
            if(storedEdge>0) setText(storedEdge.toString())
            visibility = if(resolution.selectedItemPosition==edges.lastIndex) View.VISIBLE else View.GONE
        }
        body.addView(customEdge,LinearLayout.LayoutParams(-1,dp(56)))
        body.addView(label("先完成胶片、颗粒与后处理，再缩小整张照片。低像素输出会让细颗粒自然融合；想保留明显颗粒，可提高颗粒尺寸后再导出。\n\nTIFF 当前为 8 位 RGB，无损但文件较大；不增加原图的色彩位深。",12f,EditorPalette.muted).apply {
            setPadding(0,dp(8),0,dp(12))
        })
        format.onItemSelectedListener = object:AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent:AdapterView<*>?,view:View?,position:Int,id:Long) {updateQuality()}
            override fun onNothingSelected(parent:AdapterView<*>?) = Unit
        }
        resolution.onItemSelectedListener = object:AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent:AdapterView<*>?,view:View?,position:Int,id:Long) {
                customEdge.visibility = if(position==edges.lastIndex) View.VISIBLE else View.GONE
            }
            override fun onNothingSelected(parent:AdapterView<*>?) = Unit
        }
        var launched = false
        val dialog = AlertDialog.Builder(this).setView(panel).create()
        exportSettingsDialog = dialog
        panel.addView(button("选择保存位置","export_confirm",environment) {
            val edge = if(resolution.selectedItemPosition==edges.lastIndex) {
                val value = customEdge.text.toString().toIntOrNull()
                if(value==null || value !in 1..65535) {
                    customEdge.error = "请输入1到65535之间的像素数"
                    return@button
                }
                value
            } else edges[resolution.selectedItemPosition]
            pendingExportSettings = ExportSettings(
                format=if(format.selectedItemPosition==0) ExportFormat.JPEG else ExportFormat.TIFF,
                jpegQuality=quality.progress+1,maxLongEdge=edge)
            preferences.edit().putString("export.format",pendingExportSettings.format.name)
                .putInt("export.quality",pendingExportSettings.jpegQuality)
                .putInt("export.longEdge",edge).apply()
            launched = true
            dialog.dismiss()
            launchExportDestination(film)
        },LinearLayout.LayoutParams(-1,dp(64)))
        panel.addView(button("取消","export_cancel",environment) {dialog.dismiss()},LinearLayout.LayoutParams(-1,dp(56)))
        dialog.setOnDismissListener {
            exportSettingsDialog = null
            if(!launched) {
                choosingDestination = false; pendingExportFilm = null
                if(!destroyed) updateControls()
            }
        }
        updateQuality()
        attachDialogOwners(dialog); dialog.show(); styleSheet(dialog)
        dialog.window?.apply {
            setLayout(-1,(resources.displayMetrics.heightPixels*.78f).toInt())
            clearFlags(WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM)
            setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        }
    }

    private fun launchExportDestination(film:FilmChoice) {
        val safeName = film.name.replace(Regex("""[^\p{L}\p{N}._-]+"""), "-").trim('-').take(60)
        val tiff = pendingExportSettings.format==ExportFormat.TIFF
        try {
            startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                type = if(tiff) "image/tiff" else "image/jpeg"
                addCategory(Intent.CATEGORY_OPENABLE)
                putExtra(Intent.EXTRA_TITLE, "cxo-$safeName.${if(tiff) "tif" else "jpg"}")
            }, REQUEST_EXPORT)
        } catch (_: Exception) {
            choosingDestination = false; pendingExportFilm = null
            status.text = "无法打开保存位置，请稍后重试。"
            updateControls()
        }
    }

    private fun grainDisplayName(identifier: String): String = films.firstOrNull { it.token == identifier }?.name ?: identifier

    private fun chooseGrainPack() {
        if (exporting || importingPack || choosingDestination) return
        choosingDestination=true; updateControls()
        try {
            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                type="*/*"; addCategory(Intent.CATEGORY_OPENABLE)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                putExtra(Intent.EXTRA_TITLE,"选择 native-grain8-v1.cxograinpack")
            },REQUEST_GRAIN_PACK)
        } catch (_: Exception) {
            choosingDestination=false;status.text="无法打开资源包选择器";updateControls()
        }
    }

    /** Same bounded content-URI import used by the system picker. */
    fun importGrainPack(uri: Uri) {
        if (destroyed || importingPack || exporting || choosingDestination || processor == null) return
        imageGeneration.incrementAndGet(); loading=false
        previewGeneration.incrementAndGet(); rendering=false
        importingPack=true; packCancelled.set(false)
        progress.progress=0;status.text="正在导入颗粒资源包…";updateControls()
        worker.execute {
            try {
                val identifiers=requireNotNull(grainStore).importPack(uri,{ percent -> runOnUiThread {
                    if (!destroyed && importingPack) progress.progress=percent
                } },{ destroyed || packCancelled.get() })
                runOnUiThread {
                    if (!destroyed) {
                        grainIdentifiers=identifiers;importingPack=false
                        status.text="已安装 ${identifiers.size}种颗粒 · 点击颗粒设置开启"
                        updateControls()
                        if (grainSettings.enabled || rendered == null) sourceUri?.let(::loadPhoto)
                    }
                }
            } catch (failure: Exception) {
                runOnUiThread {
                    if (!destroyed) {
                        importingPack=false
                        status.text=if (failure is CancellationException) "已取消资源包导入，原资源包保留"
                            else "导入失败，原资源包保留：${failure.message}"
                        updateControls()
                    }
                }
            }
        }
    }

    private fun showGrainSettings() {
        if (exporting || importingPack || choosingDestination) return
        if (grainIdentifiers.isEmpty()) {
            AlertDialog.Builder(this).setTitle("内置颗粒尚未就绪")
                .setMessage("请重新打开应用重试，并检查可用存储空间。若仍失败，可重新安装完整安装包。")
                .setPositiveButton("知道了",null)
                .setNeutralButton("关闭颗粒") { _,_ ->
                    grainSettings=grainSettings.copy(enabled=false);grainSettings.save(preferences)
                    updateControls();sourceUri?.let(::loadPhoto)
                }
                .setNegativeButton("取消",null).show()
            return
        }
        grainDialog?.dismiss()
        val current=grainSettings
        var committed=false
        var ready=false
        var publishing=false
        var publish:(Boolean)->Boolean={false}
        val dialogEnvironment=CatalogGlassEnvironment().apply {bitmap=glassEnvironment.bitmap}
        val panel=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;clipChildren=false;clipToPadding=false
            isFocusable=true;isFocusableInTouchMode=true;defaultFocusHighlightEnabled=false
            setPadding(dp(16),dp(8),dp(16),dp(8))
        }
        val enabled=CatalogGlassToggle(this,dialogEnvironment).apply { text="启用颗粒";isChecked=current.enabled;tag="grain_enabled" }
        panel.addView(enabled,LinearLayout.LayoutParams(-1,dp(76)))
        panel.addView(label("颗粒类型",13f,EditorPalette.muted))
        val identifiers=listOf("")+grainIdentifiers.sortedBy { grainDisplayName(it) }
        val source=Spinner(this).apply {
            tag="grain_source"
            adapter=ArrayAdapter(this@MainActivity,android.R.layout.simple_spinner_dropdown_item,
                identifiers.map { if (it.isEmpty()) "跟随当前胶片" else grainDisplayName(it) })
            setSelection(identifiers.indexOf(current.identifier).coerceAtLeast(0))
        }
        panel.addView(source)
        panel.addView(label("没有跟随映射的风格，请明确选择一种颗粒。",12f,EditorPalette.muted))
        val value=label("颗粒强度 ${current.intensity}",13f,EditorPalette.muted)
        panel.addView(value)
        val strength=CatalogGlassSlider(this,dialogEnvironment).apply {
            tag="grain_intensity";max=200;progress=current.intensity;contentDescription="颗粒强度，0到200"
            var tracking=false
            setOnSeekBarChangeListener(object: CatalogGlassSlider.OnSeekBarChangeListener {
                override fun onProgressChanged(bar: CatalogGlassSlider?,p: Int,user: Boolean) { value.text="颗粒强度 $p";publish(tracking) }
                override fun onStartTrackingTouch(bar: CatalogGlassSlider?) {tracking=true}
                override fun onStopTrackingTouch(bar: CatalogGlassSlider?) {tracking=false;publish(false)}
            })
        }
        panel.addView(strength,LinearLayout.LayoutParams(-1,dp(64)))
        panel.addView(label("颗粒尺寸",13f,EditorPalette.muted))
        val formats=NativeGrain8Geometry.Format.entries
        val format=Spinner(this).apply {
            tag="grain_format"
            adapter=ArrayAdapter(this@MainActivity,android.R.layout.simple_spinner_dropdown_item,
                listOf("自动 · 24×36","自动 · 中画幅","自动 · 大画幅","手动"))
            setSelection(formats.indexOf(current.format))
        }
        panel.addView(format)
        val manualValue=label("手动尺寸 ${current.manualSize}",13f,EditorPalette.muted)
        panel.addView(manualValue)
        val manual=CatalogGlassSlider(this,dialogEnvironment).apply {
            tag="grain_manual_size";max=9;progress=(current.manualSize*2).toInt()-1
            contentDescription="手动颗粒尺寸，0.5到5"
            var tracking=false
            setOnSeekBarChangeListener(object: CatalogGlassSlider.OnSeekBarChangeListener {
                override fun onProgressChanged(bar: CatalogGlassSlider?,p: Int,user: Boolean) { manualValue.text="手动尺寸 ${(p+1)/2f}";publish(tracking) }
                override fun onStartTrackingTouch(bar: CatalogGlassSlider?) {tracking=true}
                override fun onStopTrackingTouch(bar: CatalogGlassSlider?) {tracking=false;publish(false)}
            })
        }
        panel.addView(manual,LinearLayout.LayoutParams(-1,dp(64)))
        manual.isEnabled=current.format==NativeGrain8Geometry.Format.MANUAL
        manualValue.alpha=if(manual.isEnabled)1f else .45f
        format.onItemSelectedListener=object: AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?,view: View?,position: Int,id: Long) {
                manual.isEnabled=formats[position]==NativeGrain8Geometry.Format.MANUAL
                manualValue.alpha=if (manual.isEnabled) 1f else .45f
                if(ready && formats[position]!=grainSettings.format)publish(false)
            }
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
        var seed=current.seed
        val shuffle=button("重新随机", "grain_shuffle",dialogEnvironment) { }
        shuffle.setOnClickListener {
            seed=java.security.SecureRandom().nextInt().toLong() and 0xFFFFFFFFL
            shuffle.text="已重新随机";publish(false)
        }
        panel.addView(shuffle,LinearLayout.LayoutParams(-1,dp(64)).apply {topMargin=dp(8)})
        panel.addView(label("保存时会按原图分辨率呈现颗粒细节。",12f,EditorPalette.muted))
        fun updatePreview(tracking:Boolean) {
            if(loading)return
            if(grainSettings.enabled && grainPreview==null)sourceUri?.let(::loadPhoto)
            else if(original!=null)renderPreview(tracking)
            else sourceUri?.let(::loadPhoto)
        }
        var lastPublished=current
        publish=publish@ {tracking ->
            if(!ready || publishing)return@publish false
            val candidate=GrainSettings(enabled.isChecked,identifiers[source.selectedItemPosition.coerceAtLeast(0)],strength.progress,
                formats[format.selectedItemPosition.coerceAtLeast(0)],(manual.progress+1)/2f,seed)
            val failure=if(candidate.enabled)runCatching {candidate.resolve(requireNotNull(selectedFilm),grainIdentifiers)}.exceptionOrNull() else null
            if(failure!=null) {
                Toast.makeText(this,failure.message,Toast.LENGTH_LONG).show()
                publishing=true;enabled.isChecked=grainSettings.enabled;publishing=false
                return@publish false
            }
            if(candidate!=lastPublished || !tracking) {
                grainSettings=candidate;lastPublished=candidate
                updatePreview(tracking);updateControls()
            }
            true
        }
        val footer=LinearLayout(this).apply {gravity=Gravity.END or Gravity.CENTER_VERTICAL;clipChildren=false;clipToPadding=false}
        val cancel=button("取消", "grain_cancel",dialogEnvironment) {grainDialog?.dismiss()}
        val apply=CatalogGlassButton(this,dialogEnvironment,true).apply {text="应用";tag="grain_apply"}
        footer.addView(cancel,LinearLayout.LayoutParams(dp(112),dp(64)))
        footer.addView(apply,LinearLayout.LayoutParams(dp(112),dp(64)))
        footer.setPadding(dp(8),dp(8),dp(8),dp(8))
        val grainScroll=SliderAxisScrollView(this).apply {
            tag="grain_settings_scroll"
            // This viewport must clip its scrolling content above the pinned
            // actions, while each glass control reserves its own optical gutter.
            clipChildren=true;clipToPadding=true;isVerticalScrollBarEnabled=false
            addOnLayoutChangeListener { view,_,_,_,_,_,_,_,_ ->
                view.clipBounds=android.graphics.Rect(0,0,view.width,view.height)
            }
            addView(panel)
        }
        val grainSheet=LinearLayout(this).apply {
            orientation=LinearLayout.VERTICAL;clipChildren=false;clipToPadding=false
            addView(grainScroll,LinearLayout.LayoutParams(-1,0,1f))
            addView(footer,LinearLayout.LayoutParams(-1,dp(80)))
        }
        panel.requestFocus() // Do not auto-focus a slider and scroll the toggle behind the title.
        val dialog=AlertDialog.Builder(this).setTitle("颗粒设置")
            .setView(catalogSheet(this,dialogEnvironment,grainSheet)).create()
        grainDialog=dialog
        dialog.setOnDismissListener {
            ready=false;grainDialog=null
            if(!committed && grainSettings!=current) {
                grainSettings=current
                if(!destroyed) {updateControls();updatePreview(false)}
            }
        }
        dialog.setOnShowListener {
            panel.requestFocus();grainScroll.post {grainScroll.scrollTo(0,0)}
            apply.setOnClickListener {
                if(publish(false)) {committed=true;grainSettings.save(preferences);dialog.dismiss();updateControls()}
            }
            source.onItemSelectedListener=object:AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent:AdapterView<*>?,view:View?,position:Int,id:Long) {
                    if(ready && identifiers[position]!=grainSettings.identifier)publish(false)
                }
                override fun onNothingSelected(parent:AdapterView<*>?) = Unit
            }
            enabled.onCheckedChange={if(ready)publish(false)}
            ready=true
        }
        attachDialogOwners(dialog)
        dialog.show()
        styleSheet(dialog)
        dialog.window?.apply {
            setLayout(-1,(resources.displayMetrics.heightPixels*.58f).toInt())
            setGravity(Gravity.BOTTOM);attributes=attributes.apply {dimAmount=0f}
        }
    }

    @Deprecated("Platform Activity result callback remains supported without extra dependencies")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_GRAIN_PACK) {
            choosingDestination = false
            val uri = data?.data
            if (resultCode == RESULT_OK && uri != null) importGrainPack(uri)
            else { status.text="已取消资源包选择"; updateControls() }
        } else if (requestCode == REQUEST_PHOTO && resultCode == RESULT_OK) {
            val uri = data?.data ?: return
            try {
                if (data.flags and Intent.FLAG_GRANT_READ_URI_PERMISSION != 0) {
                    contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
            } catch (_: SecurityException) { /* Some image providers grant this session only. */ }
            loadPhoto(uri)
        } else if (requestCode == REQUEST_EXPORT) {
            choosingDestination = false
            val film = pendingExportFilm
            val exportIntensity = pendingExportIntensity
            pendingExportFilm = null
            val destination = data?.data
            if (resultCode == RESULT_OK && destination != null && film != null && processor != null && sourceUri != null) {
                exportPhoto(destination, film, exportIntensity,pendingExportGrain,pendingExportPreview)
            } else {
                if (resultCode == RESULT_OK && destination != null && destination != sourceUri) {
                    // The system may recreate the editor while its external picker is open.
                    // No render snapshot survives that event; remove the newly created empty
                    // document instead of leaving a misleading successful-looking file.
                    worker.execute { PhotoProcessor(applicationContext).use { it.deleteOutput(destination) } }
                    status.text = "编辑状态已重建，本次保存已取消；照片就绪后请重新导出。"
                } else status.text = "已取消保存"
                updateControls()
            }
        }
    }

    private fun exportPhoto(destination: Uri, film: FilmChoice, exportIntensity: Int,
                            settings: GrainSettings, metadata: PhotoGrainPreview?) {
        val uri = sourceUri ?: return
        val engine = processor ?: return
        val postSnapshot = pendingExportPost
        val exportSnapshot = pendingExportSettings
        previewGeneration.incrementAndGet()
        rendering = false
        previewBarrier.incrementAndGet();pendingPreview=null;previewInteraction=false
        exporting = true
        exportCancelled.set(false)
        progress.progress = 0
        status.text = "正在保存照片…请保持应用前台，退出编辑会取消保存。"
        updateControls()
        worker.execute {
            try {
                val lastProgress = AtomicInteger(-1)
                val layer = if (settings.enabled) {
                    val identifier=settings.resolve(film,grainIdentifiers)
                    requireNotNull(grainStore).prepare(engine.grainRequest(requireNotNull(metadata),film,
                        identifier,settings.format,settings.manualSize,settings.intensity,settings.seed,fullResolution=true))
                } else null
                val result = engine.export(uri, film, destination, { value ->
                    val percent = value.coerceIn(0, 100)
                    if (lastProgress.getAndSet(percent) != percent) runOnUiThread {
                        if (!destroyed && exporting) progress.progress = percent
                    }
                }, { destroyed || exportCancelled.get() }, intensity = exportIntensity,grain=layer,post=postSnapshot,settings=exportSnapshot)
                runOnUiThread {
                    if (!destroyed) {
                        exporting = false
                        status.text = "已保存 ${exportSnapshot.format.name} · ${result.width} × ${result.height} · ${formatSeconds(result.elapsedMillis)} 秒" +
                            if (result.resized) "\n已按输出设置或处理尺寸限制缩小。" else ""
                        updateControls()
                    }
                }
            } catch (error: Exception) {
                if (destination != uri) engine.deleteOutput(destination)
                runOnUiThread {
                    if (!destroyed) {
                        exporting = false
                        status.text = if (exportCancelled.get() || error is CancellationException) "已取消保存" else "保存失败：${error.message}"
                        updateControls()
                    }
                }
            }
        }
    }

    private fun cancelWork() {
        if (importingPack) {
            packCancelled.set(true); cancelButton.isEnabled=false; status.text="正在取消资源包导入…"
            return
        }
        if (exporting) {
            exportCancelled.set(true)
            cancelButton.isEnabled = false
            status.text = "正在取消保存…"
            return
        }
        if (loading) {
            imageGeneration.incrementAndGet()
            sourceUri = null
            loading = false
        }
        previewGeneration.incrementAndGet()
        rendering = false
        status.text = "已取消"
        previewBarrier.incrementAndGet();pendingPreview=null;previewInteraction=false
        updatePhoto()
        updateControls()
    }

    private fun updatePhoto() {
        val bitmap = if (showOriginal || rendered == null) original else rendered
        photo.setImageBitmap(bitmap)
        glassEnvironment.bitmap=bitmap
        photo.comparisonEnabled=rendered!=null && !loading && !rendering
        placeholder.visibility = if (bitmap == null) View.VISIBLE else View.GONE
        photo.contentDescription = if (showOriginal || rendered == null) "原图预览" else "胶片效果预览"
        photoInfo.text = original?.let {
            "预览 ${it.width} × ${it.height} · ${if (showOriginal || rendered == null) "原图" else "胶片效果"}"
        } ?: ""
        // Keep normal strong references until no view or worker needs a bitmap. Android's GC
        // reclaims replaced/stale previews; explicit recycle could invalidate an in-flight draw.
    }

    private fun updateControls() {
        val locked = exporting || choosingDestination || importingPack
        importButton.isEnabled = processor != null && !locked
        filmButton.isEnabled = films.isNotEmpty() && !locked
        filmButton.text = selectedFilm?.let {
            android.text.SpannableString("${it.name}\n${it.category}${if(it.experimental) " · 实验效果" else ""}").apply {
                val start=it.name.length+1
                setSpan(android.text.style.AbsoluteSizeSpan(10,true),start,length,0)
                setSpan(android.text.style.ForegroundColorSpan(EditorPalette.muted),start,length,0)
            }
        } ?: "选择胶片"
        editorDrawerTitle?.text=if(editorModule==1) "后处理 · ${if(postSettings.isIdentity) "未调整" else "已调整"}" else "胶片与风格 · ${selectedFilm?.name ?: "选择胶片"}"
        filmModuleButton.isEnabled=!locked;postModuleButton.isEnabled=!locked
        postPanel?.isEnabled=!locked
        exportButton.isEnabled = precisePreviewReady && rendered != null && selectedFilm != null && !loading && !rendering && !locked
        intensitySlider.isEnabled = original != null && !locked
        resetIntensityButton.isEnabled = original != null && !locked && intensity != 100
        grainButton.isEnabled = processor != null && !locked
        grainButton.text = if (!grainSettings.enabled) "颗粒 · 关闭" else
            "颗粒 ${grainSettings.intensity} · " + if (grainSettings.identifier.isEmpty()) "跟随胶片" else grainDisplayName(grainSettings.identifier)
        updateFilmStrip()
        val busy = loading || rendering || exporting || importingPack
        if(busy)window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if(!busy && !locked && ::filmScroll.isInitialized) {
            // Resume viewport work after cancellation, failed render or export too.
            filmScroll.removeCallbacks(thumbnailRefresh);filmScroll.postDelayed(thumbnailRefresh,90)
        }
        progress.visibility = if (busy) View.VISIBLE else View.INVISIBLE
        progress.isIndeterminate = loading
        cancelButton.visibility = if (busy) View.VISIBLE else View.INVISIBLE
        cancelButton.isEnabled = busy && !(exporting && exportCancelled.get()) && !(importingPack && packCancelled.get())
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("uri", sourceUri?.toString())
        outState.putString("film", selectedFilm?.token ?: restoredFilmToken)
        outState.putInt("intensity", intensity)
    }

    override fun onPause() {
        photo.cancelComparison()
        super.onPause()
    }

    override fun onDestroy() {
        destroyed = true
        imageGeneration.incrementAndGet()
        previewGeneration.incrementAndGet()
        exportCancelled.set(true)
        previewBarrier.incrementAndGet();pendingPreview=null
        packCancelled.set(true)
        exportSettingsDialog?.dismiss()
        postDialog?.dismiss()
        filmInfoDialog?.dismiss()
        filmDialog?.dismiss()
        grainDialog?.dismiss()
        if(::filmScroll.isInitialized)filmScroll.removeCallbacks(thumbnailRefresh)
        photo.setImageDrawable(null)
        original = null
        rendered = null
        // Queued work observes destroyed/generation flags, then the same worker
        // closes resources. Do not race a close against an active preparation.
        worker.execute { previewBaseCache.close();grainStore?.close(); grainStore=null; processor?.close(); processor=null }
        worker.shutdown()
        super.onDestroy()
    }

    private fun formatSeconds(millis: Long) = String.format(java.util.Locale.ROOT, "%.1f", millis / 1000.0)

    private fun updateIntensityLabel() {
        if (!::intensityValue.isInitialized) return
        intensityValue.text = "强度 $intensity"
        intensityValue.contentDescription = "胶片强度 $intensity"
        if (::intensitySlider.isInitialized && intensitySlider.progress != intensity) {
            intensitySlider.progress = intensity
        }
        if (::resetIntensityButton.isInitialized) resetIntensityButton.isEnabled = original != null && !exporting && !choosingDestination && intensity != 100
    }

    private fun setIntensity(value: Int, render: Boolean) {
        if (exporting || choosingDestination) return
        intensity = value.coerceIn(0, 200)
        applyingIntensity = true
        updateIntensityLabel()
        if (::intensitySlider.isInitialized && intensitySlider.progress != intensity) intensitySlider.progress = intensity
        applyingIntensity = false
        if (render && original != null) renderPreview() else updateControls()
    }

    companion object {
        private const val REQUEST_PHOTO = 101
        private const val REQUEST_EXPORT = 102
        private const val REQUEST_GRAIN_PACK = 103
    }
}
