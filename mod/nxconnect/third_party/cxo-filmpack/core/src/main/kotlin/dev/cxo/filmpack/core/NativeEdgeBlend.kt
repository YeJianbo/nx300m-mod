package dev.cxo.filmpack.core

/** Recovered Blender mode 6, verified against owned original 8-bit RGB/RGBA calls.
 * This primitive does not establish the caller's color space, geometry or toning. */
object NativeEdgeBlend {
    fun applyArgb(base:Int, texture:Int, opacity:Float, grayscale:Boolean):Int {
        val alpha=(((texture ushr 24)*opacity.coerceIn(0f,1f))+.5f).toInt().coerceIn(0,255)
        return applyWithAlpha(base,texture,alpha,grayscale)
    }
    private fun applyWithAlpha(base:Int,texture:Int,alpha:Int,grayscale:Boolean):Int {
        if(alpha==0)return base
        var r=(texture ushr 16) and 255
        var g=(texture ushr 8) and 255
        var b=texture and 255
        if(grayscale) { val gray=(r*77+g*150+b*29) ushr 8;r=gray;g=gray;b=gray }
        return (base and -0x1000000) or (channel((base ushr 16) and 255,r,alpha) shl 16) or
            (channel((base ushr 8) and 255,g,alpha) shl 8) or channel(base and 255,b,alpha)
    }
    fun applyBilinear(base:Int,p00:Int,p10:Int,p01:Int,p11:Int,dx:Float,dy:Float,opacity:Float,grayscale:Boolean):Int {
        val a=(p00 ushr 24).toFloat();val b=(p10 ushr 24).toFloat()
        val c=(p01 ushr 24).toFloat();val d=(p11 ushr 24).toFloat()
        val left=a+dy*(c-a);val right=b+dy*(d-b)
        // Opacity multiplies fractional sampled alpha before integer rounding.
        val alpha=((left+dx*(right-left))*opacity.coerceIn(0f,1f)+.5f).toInt().coerceIn(0,255)
        return applyWithAlpha(base,interpolate(p00,p10,p01,p11,dx,dy),alpha,grayscale)
    }
    private fun channel(base:Int,texture:Int,alpha:Int):Int {
        val target=(base+2*texture-255).coerceIn(0,255)
        return base+(target-base)*alpha/255
    }
    /** Original Blender interpolates straight RGBA, then quantizes RGB and alpha. */
    fun interpolate(p00:Int,p10:Int,p01:Int,p11:Int,dx:Float,dy:Float):Int {
        var output=0
        var shift=0
        while(shift<=24) {
            val a=(p00 ushr shift) and 255;val b=(p10 ushr shift) and 255
            val c=(p01 ushr shift) and 255;val d=(p11 ushr shift) and 255
            val left=a+dy*(c-a);val right=b+dy*(d-b)
            output=output or ((left+dx*(right-left)+.5f).toInt().coerceIn(0,255) shl shift)
            shift+=8
        }
        return output
    }
}
