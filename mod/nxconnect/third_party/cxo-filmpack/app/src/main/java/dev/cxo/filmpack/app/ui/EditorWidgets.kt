package dev.cxo.filmpack.app.ui

import android.content.Context
import android.graphics.*
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.LayerDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

object EditorPalette {
    // Neutral surround belongs only to the photograph; chrome uses warm umber.
    const val photoBackground = 0xFF0D0F12.toInt()
    const val background = 0xFF241B16.toInt()
    const val surface = 0xFF352820.toInt()
    const val text = 0xFFF2E6D4.toInt()
    const val muted = 0xFFBBA994.toInt()
    const val accent = 0xFFD9B780.toInt()
    private val patina by lazy {
        val random = java.util.Random(8671L)
        Bitmap.createBitmap(IntArray(96 * 96) {
            if (random.nextBoolean()) 0x05E1BB8A else 0x05000000
        }, 96, 96, Bitmap.Config.ARGB_8888).apply { density = Bitmap.DENSITY_NONE }
    }
    /** Static, faint patina: cached tile, no animated noise or image processing. */
    fun agedSurface(): Drawable = LayerDrawable(arrayOf(
        GradientDrawable(GradientDrawable.Orientation.TL_BR,
            intArrayOf(0xFF30231B.toInt(), background, 0xFF211813.toInt())),
        BitmapDrawable(null, patina).apply {
            tileModeX = Shader.TileMode.REPEAT; tileModeY = Shader.TileMode.REPEAT
        }
    ))
    fun rounded(color: Int, radius: Float, stroke: Int = Color.TRANSPARENT) = GradientDrawable().apply {
        setColor(color); cornerRadius = radius; setStroke(1, stroke)
    }
}

/** Small vectors drawn in density-independent coordinates; no icon font dependency. */
class EditorIcon(private val kind: String, private val color: Int) : Drawable() {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        this.color = this@EditorIcon.color; strokeWidth = 1.6f
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
    }
    override fun draw(canvas: Canvas) {
        canvas.save(); canvas.translate(bounds.left.toFloat(),bounds.top.toFloat())
        canvas.scale(bounds.width()/24f,bounds.height()/24f)
        when(kind) {
            "open" -> { canvas.drawRoundRect(3f,5f,21f,20f,3f,3f,paint);canvas.drawLine(8f,3f,13f,3f,paint)
                canvas.drawLine(12f,9f,12f,16f,paint);canvas.drawLine(8.5f,12.5f,15.5f,12.5f,paint) }
            "export" -> { canvas.drawLine(12f,15f,12f,3f,paint);canvas.drawLine(8f,7f,12f,3f,paint)
                canvas.drawLine(16f,7f,12f,3f,paint);canvas.drawPath(Path().apply { moveTo(4f,13f);lineTo(4f,20f);lineTo(20f,20f);lineTo(20f,13f) },paint) }
            "compare" -> { canvas.drawRoundRect(3f,4f,21f,20f,3f,3f,paint);canvas.drawLine(12f,2f,12f,22f,paint)
                paint.style=Paint.Style.FILL;canvas.drawRoundRect(5f,6f,10f,18f,1f,1f,paint);paint.style=Paint.Style.STROKE }
            "grain" -> { for (y in 0..3) for (x in 0..3) canvas.drawCircle(5f+x*4.7f+(y%2)*.5f,5f+y*4.7f,if((x+y)%3==0)1.1f else .5f,paint) }
            "film" -> { canvas.drawRoundRect(3f,4f,21f,20f,2f,2f,paint);canvas.drawLine(7f,4f,7f,20f,paint);canvas.drawLine(17f,4f,17f,20f,paint)
                for(y in 7..17 step 5) {canvas.drawLine(3f,y.toFloat(),7f,y.toFloat(),paint);canvas.drawLine(17f,y.toFloat(),21f,y.toFloat(),paint)} }
            "reset" -> {canvas.drawArc(5f,5f,20f,20f,-100f,285f,false,paint);canvas.drawPath(Path().apply {moveTo(3f,5f);lineTo(10f,5f);lineTo(10f,11f)},paint)}
        }
        canvas.restore()
    }
    override fun setAlpha(alpha: Int) { paint.alpha=alpha }
    override fun setColorFilter(filter: ColorFilter?) {paint.colorFilter=filter}
    @Deprecated("Deprecated in Android") override fun getOpacity() = PixelFormat.TRANSLUCENT
}

/** Actual previews are supplied asynchronously; the stock label is not a fake filter preview. */
class FilmCard(context: Context, val token: String, name: String, val accent: Int) : LinearLayout(context) {
    val thumbnail = ImageView(context)
    private val nameLabel = TextView(context)
    private val d=resources.displayMetrics.density
    private fun dp(n:Int)=(n*d+.5f).toInt()
    init {
        orientation=VERTICAL; setPadding(dp(3),dp(3),dp(3),dp(5)); gravity=Gravity.CENTER
        val frame=FrameLayout(context).apply { background=EditorPalette.rounded(0xFF403127.toInt(),dp(10).toFloat());clipToOutline=true }
        thumbnail.scaleType=ImageView.ScaleType.CENTER_CROP
        frame.addView(thumbnail, FrameLayout.LayoutParams(-1,-1))
        frame.addView(TextView(context).apply {text="−  −  −";textSize=9f;setTextColor(accent);gravity=Gravity.CENTER
            background=EditorPalette.rounded(0xB02B2019.toInt(),dp(5).toFloat()) },FrameLayout.LayoutParams(-1,dp(17),Gravity.BOTTOM))
        addView(frame,LayoutParams(-1,dp(66)))
        nameLabel.text=name.replace("Fujifilm ","").replace("Kodak ","")
        nameLabel.setTextColor(EditorPalette.muted);nameLabel.textSize=10f;nameLabel.gravity=Gravity.CENTER
        nameLabel.maxLines=1;nameLabel.ellipsize=android.text.TextUtils.TruncateAt.END
        addView(nameLabel,LayoutParams(-1,dp(24)))
        contentDescription="选择 $name";isFocusable=true
        markSelected(false)
    }
    fun markSelected(selected:Boolean) {
        isSelected=selected
        background=EditorPalette.rounded(if(selected)0x244F3926 else Color.TRANSPARENT,dp(14).toFloat(),if(selected)EditorPalette.accent else Color.TRANSPARENT)
        nameLabel.setTextColor(if(selected)EditorPalette.accent else EditorPalette.muted)
    }
}
