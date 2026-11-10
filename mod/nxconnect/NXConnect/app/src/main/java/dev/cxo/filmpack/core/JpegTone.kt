package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.sqrt

/** Scalar transcription of recovered JPEGTC=1 mathematics, using Double.
 * Inputs/outputs are normalized [0,1] at this internal stage, not implicitly sRGB.
 * Native floating-point operation rounding and the complete rendering pipeline are not reproduced.
 */
object JpegTone {
    const val EPSILON = 1.1920928955078125E-7 // float32 bits 0x34000000 = 2^-23

    fun forward(r: Double, g: Double, b: Double, out: DoubleArray, offset: Int = 0) =
        map(r, g, b, out, offset, false)

    fun inverse(r: Double, g: Double, b: Double, out: DoubleArray, offset: Int = 0) =
        map(r, g, b, out, offset, true)

    private fun map(r: Double, g: Double, b: Double, out: DoubleArray, offset: Int, inverse: Boolean) {
        checkStorage(out, offset)
        require(r in 0.0..1.0 && g in 0.0..1.0 && b in 0.0..1.0) {
            "Expected finite normalized RGB at the recovered internal tone stage"
        }
        val lo: Double; val mid: Double; val hi: Double
        val il: Int; val im: Int; val ih: Int
        if (r <= g) {
            if (g <= b) { lo=r; mid=g; hi=b; il=0; im=1; ih=2 }
            else if (r <= b) { lo=r; mid=b; hi=g; il=0; im=2; ih=1 }
            else { lo=b; mid=r; hi=g; il=2; im=0; ih=1 }
        } else {
            if (r <= b) { lo=g; mid=r; hi=b; il=1; im=0; ih=2 }
            else if (g <= b) { lo=g; mid=b; hi=r; il=1; im=2; ih=0 }
            else { lo=b; mid=g; hi=r; il=2; im=1; ih=0 }
        }
        val low: Double; val middle: Double; val high: Double
        if (inverse) {
            low=lo/(11.0-10.0*lo); high=hi/(11.0-10.0*hi)
            val t=(mid-lo)/max(hi-lo, EPSILON)
            val root=sqrt(low)+(sqrt(high)-sqrt(low))*t
            middle=root*root
        } else {
            low=11.0*lo/(1.0+10.0*lo); high=11.0*hi/(1.0+10.0*hi)
            val t=(sqrt(mid)-sqrt(lo))/max(sqrt(hi)-sqrt(lo), EPSILON)
            middle=low+(high-low)*t
        }
        out[offset+il]=low; out[offset+im]=middle; out[offset+ih]=high
    }

    /** Recovered internal ApplyRenderingLUTData blend, not a mapping from the product UI slider.
     * Finite strengths outside [0,1] are accepted because this formula itself does not establish
     * the caller's allowable parameter range. Output is clamped after the blend.
     */
    fun blendAndClamp(
        originalR: Double, originalG: Double, originalB: Double,
        renderedR: Double, renderedG: Double, renderedB: Double, strength: Double,
        out: DoubleArray, offset: Int = 0,
    ) {
        checkStorage(out, offset)
        require(originalR in 0.0..1.0 && originalG in 0.0..1.0 && originalB in 0.0..1.0)
        require(renderedR in 0.0..1.0 && renderedG in 0.0..1.0 && renderedB in 0.0..1.0 && strength.isFinite())
        out[offset]=(originalR+(renderedR-originalR)*strength).coerceIn(0.0,1.0)
        out[offset+1]=(originalG+(renderedG-originalG)*strength).coerceIn(0.0,1.0)
        out[offset+2]=(originalB+(renderedB-originalB)*strength).coerceIn(0.0,1.0)
    }

    /** Recovered IsRenderingBW weights; applies at the documented pre-output-tone stage. */
    fun monochrome(r: Double, g: Double, b: Double, out: DoubleArray, offset: Int = 0) {
        checkStorage(out, offset)
        require(r in 0.0..1.0 && g in 0.0..1.0 && b in 0.0..1.0)
        val gray=r*0.390625+g*0.546875+b*0.0625
        out[offset]=gray; out[offset+1]=gray; out[offset+2]=gray
    }

    private fun checkStorage(out: DoubleArray, offset: Int) {
        require(offset >= 0 && offset <= out.size-3) { "Output requires three available elements" }
    }
}

/** Bounded 32768-entry sample-unit table interpolation, normalized by 32767.
 * This is not ConvertLutToFloatAndExtend or inverse-curve construction. No extrapolation
 * or hidden guard element is assumed; the exact endpoint reads the final sample directly.
 */
class Q15Table(sampleUnits: FloatArray) {
    private val samples=sampleUnits.copyOf()
    init {
        require(samples.size==32768 && samples.all { it.isFinite() })
    }
    fun sample(x: Double): Double {
        require(x in 0.0..1.0) { "Expected a finite normalized coordinate" }
        val p=x*32767.0; val i=p.toInt()
        if (i==32767) return samples[i].toDouble()/32767.0
        val t=p-i
        return (samples[i].toDouble()+t*(samples[i+1].toDouble()-samples[i].toDouble()))/32767.0
    }
}
