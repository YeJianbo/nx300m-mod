package dev.cxo.filmpack.app.post

import android.content.Context
import android.content.SharedPreferences
import android.graphics.Bitmap
import dev.cxo.filmpack.core.PostProcessSettings
import dev.cxo.filmpack.core.PostProcessTransform
import dev.cxo.filmpack.core.ToneCurvePreset

/** Immutable settings captured together for each preview and export. */
data class PostEditingSettings(
    val development: PostProcessSettings = PostProcessSettings(),
    val edge: EdgeDefectSettings = EdgeDefectSettings(),
) {
    val isIdentity: Boolean get() = development.isIdentity && (edge.id.isEmpty() || edge.intensity == 0)
    fun save(p: SharedPreferences) {
        val d=development
        p.edit().putFloat("post.ev",d.exposureEv).putInt("post.brightness",d.brightness)
            .putInt("post.contrast",d.contrast).putInt("post.temperature",d.temperature)
            .putInt("post.tint",d.tint).putInt("post.hue",d.hue).putInt("post.saturation",d.saturation)
            .putInt("post.vibrance",d.vibrance).putInt("post.highlights",d.highlights)
            .putInt("post.shadows",d.shadows).putInt("post.whites",d.whites).putInt("post.blacks",d.blacks)
            .putString("post.curve",d.curve.name).putString("post.edge.id",edge.id)
            .putInt("post.edge.intensity",edge.intensity).putString("post.edge.position",edge.position.name)
            .putLong("post.edge.seed",edge.seed).apply()
    }
    companion object {
        fun read(p: SharedPreferences): PostEditingSettings {
            fun value(k:String)=p.getInt("post.$k",0).coerceIn(-100,100)
            return PostEditingSettings(PostProcessSettings(
                exposureEv=p.getFloat("post.ev",0f).takeIf {it.isFinite()}?.coerceIn(-3f,3f) ?: 0f,
                brightness=value("brightness"),contrast=value("contrast"),temperature=value("temperature"),
                tint=value("tint"),hue=p.getInt("post.hue",0).coerceIn(-180,180),
                saturation=value("saturation"),vibrance=value("vibrance"),highlights=value("highlights"),
                shadows=value("shadows"),whites=value("whites"),blacks=value("blacks"),
                curve=ToneCurvePreset.entries.firstOrNull {it.name==p.getString("post.curve","")} ?: ToneCurvePreset.LINEAR),
                EdgeDefectSettings(p.getString("post.edge.id","") ?: "",p.getInt("post.edge.intensity",50).coerceIn(0,100),
                    EdgePosition.entries.firstOrNull {it.name==p.getString("post.edge.position","")} ?: EdgePosition.ALL,
                    p.getLong("post.edge.seed",0)))
        }
    }
}

/** Bounded rows in display sRGB, after the film/ICC pipeline. */
class PostEditingRenderer(context:Context, settings:PostEditingSettings, private val width:Int,
                          height:Int, orientation:Int=1) {
    private val development=PostProcessTransform(settings.development)
    private val edge=EdgeDefects.prepare(context,settings.edge,width,height,orientation)
    fun apply(pixels:IntArray,top:Int,count:Int=pixels.size) {
        development.applyArgbInPlace(pixels,count)
        if(edge!=null) {
            var offset=0
            while(offset<count) {
                val size=minOf(width,count-offset)
                edge.applyRow(pixels,top+offset/width,0,size,offset)
                offset+=size
            }
        }
    }
}

fun applyPostEditing(context:Context,bitmap:Bitmap,settings:PostEditingSettings,cancelled:()->Boolean):Bitmap {
    if(settings.isIdentity)return bitmap
    val target=if(bitmap.isMutable)bitmap else bitmap.copy(Bitmap.Config.ARGB_8888,true).also {bitmap.recycle()}
    try {
      val renderer=PostEditingRenderer(context,settings,target.width,target.height)
      val rows=16
      val pixels=IntArray(target.width*rows)
      for(top in 0 until target.height step rows) {
        if(cancelled())throw java.util.concurrent.CancellationException()
        val count=minOf(rows,target.height-top)
        target.getPixels(pixels,0,target.width,0,top,target.width,count)
        renderer.apply(pixels,top,target.width*count)
        target.setPixels(pixels,0,target.width,0,top,target.width,count)
      }
      return target
    } catch(failure:Throwable) {
        target.recycle();throw failure
    }
}
