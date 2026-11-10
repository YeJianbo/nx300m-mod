package dev.cxo.filmpack.app.ui

import android.content.Context
import android.content.res.Configuration
import android.graphics.Bitmap
import android.os.Bundle
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.FrameLayout
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.semantics.*
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.kyant.backdrop.Backdrop
import com.kyant.backdrop.backdrops.layerBackdrop
import com.kyant.backdrop.backdrops.rememberCanvasBackdrop
import com.kyant.backdrop.backdrops.rememberLayerBackdrop
import com.kyant.backdrop.catalog.components.*
import kotlin.math.roundToInt

/** One recorded background per window; controls use the original Catalog components. */
class CatalogGlassEnvironment {
    var bitmap: Bitmap? by mutableStateOf(null)
    internal var backdrop: Backdrop? by mutableStateOf(null)
}

private val TextColor = Color(EditorPalette.text)
private val Accent = Color(EditorPalette.accent)

/** Keeps horizontal slider drags on their track while preserving vertical scrolling. */
class SliderAxisScrollView(context: Context) : android.widget.ScrollView(context) {
    private var sliderGesture = false
    private var gestureAxis = 0 // 0 undecided, 1 horizontal, 2 vertical
    private var downX = 0f
    private var downY = 0f
    private val slop = android.view.ViewConfiguration.get(context).scaledTouchSlop.toFloat()

    override fun onInterceptTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x; downY = event.y; gestureAxis = 0
                sliderGesture = containsSlider(this, event.x, event.y)
                if (sliderGesture) { super.onInterceptTouchEvent(event); return false }
            }
            MotionEvent.ACTION_MOVE -> if (sliderGesture) {
                if (gestureAxis == 0) {
                    val dx = kotlin.math.abs(event.x - downX); val dy = kotlin.math.abs(event.y - downY)
                    if (dx >= slop && dx >= dy * .8f) gestureAxis = 1
                    else if (dy >= slop * 1.5f && dy >= dx * 1.5f) gestureAxis = 2
                }
                if (gestureAxis != 2) return false
                return super.onInterceptTouchEvent(event)
            }
        }
        val intercept = super.onInterceptTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP || event.actionMasked == MotionEvent.ACTION_CANCEL) {
            sliderGesture = false; gestureAxis = 0
        }
        return intercept
    }

    override fun requestDisallowInterceptTouchEvent(disallowIntercept: Boolean) {
        if (sliderGesture) {
            when (gestureAxis) {
                0 -> return
                1 -> { super.requestDisallowInterceptTouchEvent(true); return }
                2 -> { super.requestDisallowInterceptTouchEvent(false); return }
            }
        }
        super.requestDisallowInterceptTouchEvent(disallowIntercept)
    }

    private fun containsSlider(view: View, x: Float, y: Float): Boolean {
        if (view is CatalogGlassSlider) return x >= 0f && y >= 0f && x < view.width && y < view.height
        if (view !is ViewGroup) return false
        val localX = x + view.scrollX; val localY = y + view.scrollY
        for (index in view.childCount - 1 downTo 0) {
            val child = view.getChildAt(index)
            val childX = localX - child.left; val childY = localY - child.top
            if (childX >= 0f && childY >= 0f && childX < child.width && childY < child.height &&
                containsSlider(child, childX, childY)) return true
        }
        return false
    }
}

/** View interop roots must not become a second clip around an animated lens. */
private fun View.allowGlassOverflow() {
    if (this is ViewGroup) {
        clipChildren = false
        clipToPadding = false
        clipToOutline = false
        for (index in 0 until childCount) getChildAt(index).allowGlassOverflow()
    }
}

@Composable
private fun EditorConfiguration(content: @Composable () -> Unit) {
    // The editor is intentionally dark even when Android is set to a light theme.
    val configuration = LocalConfiguration.current
    val dark = remember(configuration) {
        Configuration(configuration).apply {
            uiMode = (uiMode and Configuration.UI_MODE_NIGHT_MASK.inv()) or Configuration.UI_MODE_NIGHT_YES
        }
    }
    CompositionLocalProvider(LocalConfiguration provides dark, content = content)
}

/** The ambient photo is only behind tools; the main photo canvas remains neutral. */
fun catalogWindow(context: Context, environment: CatalogGlassEnvironment, content: View): ComposeView =
    ComposeView(context).apply {
        id = View.generateViewId()
        clipChildren = false
        clipToPadding = false
        clipToOutline = false
        setContent {
            EditorConfiguration {
                val backdrop = rememberLayerBackdrop()
                SideEffect { environment.backdrop = backdrop }
                Box(Modifier.fillMaxSize()) {
                    Box(Modifier.matchParentSize().layerBackdrop(backdrop).background(Color(EditorPalette.background))) {
                        val bitmap = environment.bitmap
                        if (bitmap != null && !bitmap.isRecycled) {
                            Image(bitmap.asImageBitmap(), null, Modifier.fillMaxSize().blur(36.dp).alpha(.16f), contentScale = ContentScale.Crop)
                        }
                        androidx.compose.foundation.Canvas(Modifier.fillMaxSize()) {
                            drawRect(Color(EditorPalette.background).copy(alpha = .48f))
                            drawRect(Brush.radialGradient(listOf(Color(0x665B402A), Color.Transparent),
                                Offset(size.width * .1f, size.height * .15f), size.maxDimension * .8f))
                            drawCircle(Brush.radialGradient(listOf(Color(0x225D4633), Color.Transparent),
                                Offset(size.width, size.height * .8f), size.width), size.width, Offset(size.width, size.height * .8f))
                        }
                    }
                    AndroidView(factory = { content }, modifier = Modifier.fillMaxSize())
                }
            }
        }
    }

/** Bound a dialog's editor area to its available height, including landscape. */
fun catalogSheet(context: Context, environment: CatalogGlassEnvironment, content: View): FrameLayout =
    object : FrameLayout(context) {
        init {
            clipChildren = false; clipToPadding = false; clipToOutline = false
            addView(catalogWindow(context, environment, content), LayoutParams(-1, -1))
        }
        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            val desired = (510 * resources.displayMetrics.density).roundToInt()
            val available = MeasureSpec.getSize(heightMeasureSpec)
            val height = if (MeasureSpec.getMode(heightMeasureSpec) == MeasureSpec.UNSPECIFIED) desired else minOf(desired, available)
            super.onMeasure(widthMeasureSpec, MeasureSpec.makeMeasureSpec(height, MeasureSpec.EXACTLY))
        }
    }

abstract class CatalogControl(context: Context, protected val environment: CatalogGlassEnvironment) : FrameLayout(context) {
    protected val compose = ComposeView(context)
    protected var enabledState by mutableStateOf(true)
    val componentImplementation = "Backdrop Catalog 2.0.1"
    init {
        clipChildren = false; clipToPadding = false
        defaultFocusHighlightEnabled = false
        compose.id = View.generateViewId()
        compose.clipChildren = false; compose.clipToPadding = false; compose.clipToOutline = false
        addView(compose, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    }
    protected fun content(block: @Composable (Backdrop) -> Unit) {
        compose.setContent {
            EditorConfiguration {
                val backdrop = environment.backdrop ?: rememberCanvasBackdrop { drawRect(Color(EditorPalette.surface)) }
                // A normal alpha layer is an offscreen buffer bounded by this View.
                // ModulateAlpha keeps disabled styling without cropping an enlarged lens.
                Box(Modifier.fillMaxSize().graphicsLayer {
                    alpha = if (enabledState) 1f else .35f
                    compositingStrategy = CompositingStrategy.ModulateAlpha
                    clip = false
                }, contentAlignment = Alignment.Center) { block(backdrop) }
            }
        }
    }
    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        // AndroidComposeView is created lazily after setContent; include that child.
        compose.post { compose.allowGlassOverflow() }
    }
    override fun setEnabled(enabled: Boolean) { super.setEnabled(enabled); enabledState = enabled }
    override fun dispatchTouchEvent(event: MotionEvent): Boolean = if (isEnabled) super.dispatchTouchEvent(event) else false
    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        // A weighted LinearLayout may probe width with UNSPECIFIED before its
        // real pass. Never compose the upstream remembered drag controller with
        // a guessed screen width: its drag range would outlive that first size.
        if (MeasureSpec.getMode(widthMeasureSpec) == MeasureSpec.UNSPECIFIED) {
            val height = if (MeasureSpec.getMode(heightMeasureSpec) == MeasureSpec.UNSPECIFIED)
                (64 * resources.displayMetrics.density).roundToInt() else MeasureSpec.getSize(heightMeasureSpec)
            setMeasuredDimension(0, height)
            return
        }
        val width = widthMeasureSpec
        val height = if (MeasureSpec.getMode(heightMeasureSpec) == MeasureSpec.UNSPECIFIED)
            MeasureSpec.makeMeasureSpec((90 * resources.displayMetrics.density).roundToInt(), MeasureSpec.AT_MOST) else heightMeasureSpec
        if (MeasureSpec.getSize(width) == 0 || MeasureSpec.getSize(height) == 0) {
            setMeasuredDimension(MeasureSpec.getSize(width), MeasureSpec.getSize(height)); return
        }
        super.onMeasure(width, height)
    }
}

/** Public View properties bridge the existing controller, never the animation logic. */
class CatalogGlassButton(context: Context, environment: CatalogGlassEnvironment, val accent: Boolean = false) : CatalogControl(context, environment) {
    var text: CharSequence by mutableStateOf("")
    var textSize: Float by mutableFloatStateOf(12f)
    var iconKind: String? by mutableStateOf(null)
    var maxLines: Int by mutableIntStateOf(1)
    private var foreground by mutableIntStateOf(EditorPalette.text)
    private var action: OnClickListener? = null
    init {
        content { backdrop ->
            BoxWithConstraints(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                // Upstream expands by 4/48 in both axes: a wide button needs more
                // horizontal breathing room than an icon button. Reserve that space
                // inside the Compose View, before the original drawBackdrop modifier.
                val horizontalGutter = 8.dp + maxWidth / 24f
                LiquidButton(
                    onClick = { if (enabledState) performClick() }, backdrop = backdrop,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = horizontalGutter, vertical = 8.dp).semantics {
                        if (!enabledState) disabled()
                        this.contentDescription = this@CatalogGlassButton.contentDescription?.toString() ?: this@CatalogGlassButton.text.toString()
                    }, tint = if (accent) Accent else Color.Unspecified,
                    surfaceColor = if (accent) Color.Unspecified else Color(EditorPalette.accent).copy(alpha = .055f)
                ) {
                    iconKind?.let { kind ->
                        androidx.compose.foundation.Canvas(Modifier.size(18.dp)) {
                            drawIntoCanvas { canvas ->
                                val icon = EditorIcon(kind, if (accent) EditorPalette.background else foreground)
                                icon.setBounds(0, 0, size.width.toInt(), size.height.toInt())
                                icon.draw(canvas.nativeCanvas)
                            }
                        }
                    }
                    if (text.isNotEmpty()) BasicText(text.toString(), style = TextStyle(Color(if (accent) EditorPalette.background else foreground), textSize.sp, fontWeight = FontWeight.Medium), maxLines = maxLines, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
    fun setTextColor(color: Int) { foreground = color }
    override fun setOnClickListener(listener: OnClickListener?) { action = listener; isClickable = true }
    override fun performClick(): Boolean {
        if (!isEnabled) return false
        super.performClick(); action?.onClick(this); return action != null
    }
}

class CatalogGlassSlider(context: Context, environment: CatalogGlassEnvironment) : CatalogControl(context, environment) {
    interface OnSeekBarChangeListener {
        fun onProgressChanged(bar: CatalogGlassSlider?, value: Int, fromUser: Boolean)
        fun onStartTrackingTouch(bar: CatalogGlassSlider?)
        fun onStopTrackingTouch(bar: CatalogGlassSlider?)
    }
    var max: Int by mutableIntStateOf(200)
    private var current by mutableIntStateOf(0)
    // Preserve sub-step movement; rounding each pointer delta loses slow drags
    // on the 0..9 manual grain slider.
    private var continuousValue by mutableFloatStateOf(0f)
    var progress: Int
        get() = current
        set(value) { continuousValue=value.coerceIn(0,max).toFloat(); change(value, false) }
    var keyProgressIncrement = 1
    private var listener: OnSeekBarChangeListener? = null
    private var tracking = false
    init {
        isFocusable = true; isFocusableInTouchMode = true
        descendantFocusability = FOCUS_BLOCK_DESCENDANTS
        content { backdrop ->
            LiquidSlider(
                value = { continuousValue }, onValueChange = {
                    if (enabledState) { continuousValue=it.coerceIn(0f,max.toFloat());change(continuousValue.roundToInt(),true) }
                },
                valueRange = 0f..max.coerceAtLeast(1).toFloat(), visibilityThreshold = .01f, backdrop = backdrop, accentOverride = Accent,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 36.dp, vertical = 16.dp).semantics {
                    this.contentDescription = this@CatalogGlassSlider.contentDescription?.toString() ?: "滑块"
                    progressBarRangeInfo = ProgressBarRangeInfo(current.toFloat(), 0f..max.toFloat(), max - 1)
                    if (!enabledState) disabled()
                    setProgress { if (enabledState) { continuousValue=it.coerceIn(0f,max.toFloat());change(it.roundToInt(), true); true } else false }
                }
            )
        }
    }
    fun setOnSeekBarChangeListener(value: OnSeekBarChangeListener) { listener = value }
    private fun change(value: Int, fromUser: Boolean) {
        val bounded = value.coerceIn(0, max)
        if (bounded != current) { current = bounded; listener?.onProgressChanged(this, bounded, fromUser) }
    }
    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (!isEnabled) return false
        if (event.actionMasked == MotionEvent.ACTION_DOWN) {
            tracking = true
            listener?.onStartTrackingTouch(this)
        }
        val result = super.dispatchTouchEvent(event)
        if (event.actionMasked == MotionEvent.ACTION_UP || event.actionMasked == MotionEvent.ACTION_CANCEL) {
            post { if (tracking) { tracking = false; listener?.onStopTrackingTouch(this) } }
        }
        return result
    }
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (isEnabled && keyCode in intArrayOf(KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT)) {
            val next=(progress + if (keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) keyProgressIncrement else -keyProgressIncrement).coerceIn(0,max)
            continuousValue=next.toFloat();change(next,true)
            return true
        }
        return super.onKeyDown(keyCode, event)
    }
    override fun onInitializeAccessibilityNodeInfo(info: AccessibilityNodeInfo) {
        super.onInitializeAccessibilityNodeInfo(info)
        info.className = "android.widget.SeekBar"
        info.rangeInfo = AccessibilityNodeInfo.RangeInfo.obtain(AccessibilityNodeInfo.RangeInfo.RANGE_TYPE_INT, 0f, max.toFloat(), progress.toFloat())
        info.addAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS)
    }
    override fun performAccessibilityAction(action: Int, arguments: Bundle?): Boolean {
        if (isEnabled && action == AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_PROGRESS.id && arguments != null) {
            continuousValue=arguments.getFloat(AccessibilityNodeInfo.ACTION_ARGUMENT_PROGRESS_VALUE).coerceIn(0f,max.toFloat())
            change(continuousValue.roundToInt(), true)
            return true
        }
        return super.performAccessibilityAction(action, arguments)
    }
}

class CatalogGlassToggle(context: Context, environment: CatalogGlassEnvironment) : CatalogControl(context, environment) {
    var text: String by mutableStateOf("")
    private var checkedValue: Boolean by mutableStateOf(false)
    var onCheckedChange: ((Boolean) -> Unit)? = null
    var isChecked: Boolean
        get() = checkedValue
        set(value) {
            if (checkedValue != value) { checkedValue = value; onCheckedChange?.invoke(value) }
        }
    init {
        content { backdrop ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                BasicText(text, Modifier.weight(1f), TextStyle(TextColor, 15.sp))
                LiquidToggle(selected = { isChecked }, onSelect = { if (enabledState) isChecked = it }, backdrop = backdrop, accentOverride = Accent,
                    modifier = Modifier.padding(horizontal = 24.dp, vertical = 18.dp).semantics {
                        contentDescription = this@CatalogGlassToggle.text
                        stateDescription = if (isChecked) "已开启" else "已关闭"
                        if (!enabledState) disabled()
                        onClick { if (enabledState) { isChecked = !isChecked; true } else false }
                    })
            }
        }
    }
}

class CatalogGlassTabs(context: Context, environment: CatalogGlassEnvironment, private val onSelect: (Int) -> Unit) : CatalogControl(context, environment) {
    var selectedIndex: Int by mutableIntStateOf(0)
    init {
        content { backdrop ->
            LiquidBottomTabs(selectedTabIndex = { selectedIndex }, onTabSelected = { index ->
                if (enabledState && index != selectedIndex) { selectedIndex = index; onSelect(index) }
            }, backdrop = backdrop, tabsCount = 3, modifier = Modifier.fillMaxWidth().padding(horizontal = 28.dp, vertical = 18.dp)) {
                listOf("胶片", "颗粒", "收藏").forEachIndexed { index, label ->
                    LiquidBottomTab(onClick = { if (enabledState) { selectedIndex = index; onSelect(index) } }) {
                        BasicText(listOf("▧", "⠿", "☆")[index], style = TextStyle(TextColor, 18.sp))
                        BasicText(label, style = TextStyle(TextColor, 10.sp))
                    }
                }
            }
        }
    }
}
