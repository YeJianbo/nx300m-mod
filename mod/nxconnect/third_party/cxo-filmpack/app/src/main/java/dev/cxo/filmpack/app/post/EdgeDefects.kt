package dev.cxo.filmpack.app.post

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import org.json.JSONArray
import dev.cxo.filmpack.core.NativeEdgeBlend
import dev.cxo.filmpack.core.NativeEdgePlacement
import kotlin.math.floor
import kotlin.math.max

/** Original resources and recovered mode-6 blend. Geometry and output-space
 * integration have separate evidence; not a full DxO effect parity claim. */
enum class EdgePosition { ALL, LEFT, TOP, RIGHT, BOTTOM }
data class EdgeDefectSettings(val id:String="",val intensity:Int=50,
    val position:EdgePosition=EdgePosition.ALL,val seed:Long=0L)
data class EdgeDefectChoice(val id:String,val name:String,val isNotEdge:Boolean,val grayscale:Boolean,
    val asset:String) { override fun toString()=name }

object EdgeDefects {
    @Volatile private var cachedCatalog:List<EdgeDefectChoice>?=null
    fun catalog(context:Context):List<EdgeDefectChoice> = cachedCatalog ?: synchronized(this) {
        cachedCatalog ?: JSONArray(context.assets.open("edge-defects/catalog.json").bufferedReader().use {it.readText()}).let { a ->
            List(a.length()) { i -> a.getJSONObject(i).let {
                EdgeDefectChoice(it.getString("id"),it.getString("name"),it.optBoolean("isNotEdge"),it.optBoolean("grayscale"),it.getString("asset"))
            }}.also {cachedCatalog=it}
        }
    }
    fun prepare(context:Context,settings:EdgeDefectSettings,width:Int,height:Int,orientation:Int=1):Renderer? {
        require(width>0 && height>0)
        if(settings.id.isEmpty() || settings.intensity<=0)return null
        val choice=catalog(context).firstOrNull {it.id==settings.id}
            ?: throw IllegalArgumentException("Unknown edge defect: ${settings.id}")
        // Keep decoding bounded regardless of photograph dimensions; original
        // texture files stay intact in assets. Both preview/export use same map.
        val bounds=BitmapFactory.Options().apply {inJustDecodeBounds=true}
        context.assets.open(choice.asset).use {BitmapFactory.decodeStream(it,null,bounds)}
        var sample=1
        while(max(bounds.outWidth,bounds.outHeight)/sample>1024)sample*=2
        val options=BitmapFactory.Options().apply {inSampleSize=sample;inPreferredConfig=Bitmap.Config.ARGB_8888;inScaled=false;inPremultiplied=false}
        val bitmap=context.assets.open(choice.asset).use {BitmapFactory.decodeStream(it,null,options)}
            ?: error("Cannot decode edge defect ${choice.id}")
        val pixels=IntArray(bitmap.width*bitmap.height)
        bitmap.getPixels(pixels,0,bitmap.width,0,0,bitmap.width,bitmap.height)
        val renderer=Renderer(pixels,bitmap.width,bitmap.height,choice,settings,width,height,orientation)
        bitmap.recycle()
        return renderer
    }

    /** Immutable texture state. applyRow is safe on independent worker buffers. */
    class Renderer internal constructor(private val texture:IntArray,private val tw:Int,private val th:Int,
        private val choice:EdgeDefectChoice,settings:EdgeDefectSettings,
        private val width:Int,private val height:Int,private val orientation:Int) {
        private val opacity=settings.intensity.coerceIn(0,100)/100f
        private val placement=NativeEdgePlacement.resolve(settings.seed,when(settings.position) {
            EdgePosition.ALL -> 0;EdgePosition.TOP -> 1;EdgePosition.BOTTOM -> 2
            EdgePosition.LEFT -> 3;EdgePosition.RIGHT -> 4
        })
        // Seed/quarter-turn prefix is recovered. Full affine scaling and its
        // translationRatio are not restored yet; normalized placement stays approximate.
        private val edge=placement.clockwiseTurns
        private val rotation=placement.clockwiseTurns
        private val displayWidth=if(orientation in 5..8)height else width
        private val displayHeight=if(orientation in 5..8)width else height
        private val depth=if(edge==0 || edge==2)
            (displayWidth.toFloat()*th/tw/displayHeight).coerceAtMost(1f) else
            (displayHeight.toFloat()*th/tw/displayWidth).coerceAtMost(1f)

        fun applyRow(pixels:IntArray,y:Int,xOffset:Int=0,count:Int=pixels.size,offset:Int=0) {
            require(offset>=0 && count>=0 && offset+count<=pixels.size)
            require(y in 0 until height && xOffset>=0 && xOffset+count<=width)
            for(i in 0 until count) {
                val nx=(xOffset+i+.5f)/width;val ny=(y+.5f)/height
                // EXIF source -> displayed image, including mirrored orientations.
                val x:Float;val yy:Float
                when(orientation) {
                    2->{x=1-nx;yy=ny};3->{x=1-nx;yy=1-ny};4->{x=nx;yy=1-ny}
                    5->{x=ny;yy=nx};6->{x=1-ny;yy=nx};7->{x=1-ny;yy=1-nx};8->{x=ny;yy=1-nx}
                    else->{x=nx;yy=ny}
                }
                var u:Float;var v:Float
                if(choice.isNotEdge) {
                    when(rotation) {1->{u=yy;v=1-x};2->{u=1-x;v=1-yy};3->{u=1-yy;v=x};else->{u=x;v=yy}}
                } else {
                    when(edge) {0->{u=x;v=yy/depth};1->{u=yy;v=(1-x)/depth};2->{u=1-x;v=(1-yy)/depth};else->{u=1-yy;v=x/depth}}
                    if(v>1f)continue
                }
                pixels[offset+i]=sampleAndBlend(pixels[offset+i],u,v)
            }
        }
        private fun sampleAndBlend(base:Int,u:Float,v:Float):Int {
            val fx=(u.coerceIn(0f,1f)*(tw-1));val fy=(v.coerceIn(0f,1f)*(th-1))
            val x0=floor(fx).toInt();val y0=floor(fy).toInt();val x1=(x0+1).coerceAtMost(tw-1);val y1=(y0+1).coerceAtMost(th-1)
            val dx=fx-x0;val dy=fy-y0
            val p00=texture[y0*tw+x0];val p10=texture[y0*tw+x1];val p01=texture[y1*tw+x0];val p11=texture[y1*tw+x1]
            return NativeEdgeBlend.applyBilinear(base,p00,p10,p01,p11,dx,dy,opacity,choice.grayscale)
        }
    }
}
