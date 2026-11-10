package dev.cxo.filmpack.core

import kotlin.math.*

/** Independent CXO editing presets, not recovered DxO curves. */
enum class ToneCurvePreset { LINEAR, SOFT_CONTRAST, STRONG_CONTRAST, MATTE }

/** Output-referred creative controls. Temperature is relative, not RAW Kelvin.
 * Native FilmPack numeric equivalence has not been established. */
data class PostProcessSettings(
    val exposureEv: Float = 0f, val brightness: Int = 0, val contrast: Int = 0,
    val temperature: Int = 0, val tint: Int = 0, val hue: Int = 0,
    val saturation: Int = 0, val vibrance: Int = 0, val highlights: Int = 0,
    val shadows: Int = 0, val whites: Int = 0, val blacks: Int = 0,
    val curve: ToneCurvePreset = ToneCurvePreset.LINEAR,
) {
    init {
        require(exposureEv.isFinite() && exposureEv in -3f..3f)
        require(hue in -180..180)
        require(listOf(brightness,contrast,temperature,tint,saturation,vibrance,highlights,shadows,whites,blacks).all { it in -100..100 })
    }
    val isIdentity: Boolean get() = exposureEv == 0f && brightness == 0 && contrast == 0 &&
        temperature == 0 && tint == 0 && hue == 0 && saturation == 0 && vibrance == 0 &&
        highlights == 0 && shadows == 0 && whites == 0 && blacks == 0 && curve == ToneCurvePreset.LINEAR
}

/** Immutable, thread-safe 65-cubed output sRGB LUT. Identity bypass is exact.
 * Preview/export share this transform; alpha is retained. This is CXO's first
 * independent implementation, not a recovered native development pipeline. */
class PostProcessTransform(val settings: PostProcessSettings) {
    private val grid = if (settings.isIdentity) FloatArray(0) else bake()
    fun applyArgbInPlace(pixels: IntArray, count: Int = pixels.size) {
        require(count in 0..pixels.size)
        if (settings.isIdentity) return
        for (i in 0 until count) pixels[i] = apply(pixels[i])
    }
    private fun apply(pixel: Int): Int {
        val rr=(pixel ushr 16 and 255)*(64f/255f)
        val gg=(pixel ushr 8 and 255)*(64f/255f)
        val bb=(pixel and 255)*(64f/255f)
        val r=min(rr.toInt(),63);val g=min(gg.toInt(),63);val b=min(bb.toInt(),63)
        val fr=rr-r;val fg=gg-g;val fb=bb-b
        val base=((r*65+g)*65+b)*3
        val sr=12675;val sg=195;val sb=3
        val p1:Int;val p2:Int;val f1:Float;val f2:Float;val f3:Float
        if(fr>=fg) {
            if(fg>=fb) {p1=base+sr;p2=p1+sg;f1=fr;f2=fg;f3=fb}
            else if(fr>=fb) {p1=base+sr;p2=p1+sb;f1=fr;f2=fb;f3=fg}
            else {p1=base+sb;p2=p1+sr;f1=fb;f2=fr;f3=fg}
        } else {
            if(fr>=fb) {p1=base+sg;p2=p1+sr;f1=fg;f2=fr;f3=fb}
            else if(fg>=fb) {p1=base+sg;p2=p1+sb;f1=fg;f2=fb;f3=fr}
            else {p1=base+sb;p2=p1+sg;f1=fb;f2=fg;f3=fr}
        }
        val end=base+sr+sg+sb
        fun channel(c:Int):Int {
            val x=grid[base+c]+f1*(grid[p1+c]-grid[base+c])+f2*(grid[p2+c]-grid[p1+c])+f3*(grid[end+c]-grid[p2+c])
            return (x*255f).roundToInt().coerceIn(0,255)
        }
        return (pixel and -0x1000000) or (channel(0) shl 16) or (channel(1) shl 8) or channel(2)
    }

    private fun bake(): FloatArray {
        val out=FloatArray(65*65*65*3)
        val gain=2.0.pow(settings.exposureEv.toDouble())
        val warmth=settings.temperature/100.0;val tint=settings.tint/100.0
        val gains=doubleArrayOf(2.0.pow(.45*warmth+.15*tint),2.0.pow(-.30*tint),2.0.pow(-.45*warmth+.15*tint))
        val norm=.2126*gains[0]+.7152*gains[1]+.0722*gains[2]
        val ch=Array(3) { FloatArray(65) }
        for(c in 0..2) for(i in 0..64) {
            val x=i/64.0
            val lin=if(x<=.04045) x/12.92 else ((x+.055)/1.055).pow(2.4)
            val v=(lin*gain*gains[c]/norm).coerceIn(0.0,1.0)
            ch[c][i]=(if(v<=.0031308) 12.92*v else 1.055*v.pow(1.0/2.4)-.055).toFloat()
        }
        var index=0
        for(ri in 0..64) for(gi in 0..64) for(bi in 0..64) {
            var r=ch[0][ri];var g=ch[1][gi];var b=ch[2][bi]
            val y=.2126f*r+.7152f*g+.0722f*b;val delta=tone(y)-y
            r=(r+delta).coerceIn(0f,1f);g=(g+delta).coerceIn(0f,1f);b=(b+delta).coerceIn(0f,1f)
            if(settings.hue!=0 || settings.saturation!=0 || settings.vibrance!=0) {
                val hi=max(r,max(g,b));val lo=min(r,min(g,b));val d=hi-lo;val light=(hi+lo)*.5f
                var sat=if(d==0f) 0f else d/(1f-abs(2f*light-1f))
                var h=when {d==0f->0f;hi==r->(g-b)/d+if(g<b) 6f else 0f;hi==g->(b-r)/d+2f;else->(r-g)/d+4f}/6f
                h=((h+settings.hue/360f)%1f+1f)%1f
                val s=settings.saturation/100f
                sat=(sat*(1f+s)).coerceIn(0f,1f)
                sat=(sat+settings.vibrance/100f*sat*(1f-sat)).coerceIn(0f,1f)
                val chroma=(1f-abs(2f*light-1f))*sat;val hp=h*6f;val x=chroma*(1f-abs(hp%2f-1f));val m=light-chroma*.5f
                when(hp.toInt()) {
                    0->{r=chroma;g=x;b=0f};1->{r=x;g=chroma;b=0f};2->{r=0f;g=chroma;b=x}
                    3->{r=0f;g=x;b=chroma};4->{r=x;g=0f;b=chroma};else->{r=chroma;g=0f;b=x}
                }
                r+=m;g+=m;b+=m
            }
            out[index++]=r.coerceIn(0f,1f);out[index++]=g.coerceIn(0f,1f);out[index++]=b.coerceIn(0f,1f)
        }
        return out
    }
    private fun tone(input:Float):Float {
        val x=input.coerceIn(0f,1f);val low=(1f-x)*(1f-x);val high=x*x
        var v=x+settings.brightness/100f*.65f*x*(1f-x)
        v+=settings.shadows/100f*.85f*x*low
        v+=settings.highlights/100f*.85f*(1f-x)*high
        v+=settings.blacks/100f*.20f*low*low
        v+=settings.whites/100f*.20f*high*high
        v=v.coerceIn(0f,1f)
        v+=settings.contrast/100f*.45f*(v-.5f)*(1f-4f*(v-.5f)*(v-.5f))
        v=v.coerceIn(0f,1f)
        return when(settings.curve) {
            ToneCurvePreset.LINEAR->v
            ToneCurvePreset.SOFT_CONTRAST->v+.20f*(v-.5f)*(1f-4f*(v-.5f)*(v-.5f))
            ToneCurvePreset.STRONG_CONTRAST->v+.45f*(v-.5f)*(1f-4f*(v-.5f)*(v-.5f))
            ToneCurvePreset.MATTE->.06f+.90f*v
        }.coerceIn(0f,1f)
    }
}
