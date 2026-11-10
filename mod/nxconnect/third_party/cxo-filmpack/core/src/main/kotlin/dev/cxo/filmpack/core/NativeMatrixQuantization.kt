package dev.cxo.filmpack.core

/** Recovered NEON ForceRowSumTo1 (0x4e1944): Float arithmetic and Q16 rounding.
 * Only the first two elements are rounded to Q16; the last is sequential (1-a)-b.
 * Invalid/nonfinite/overflowing operations are rejected rather than emulating ARM exceptions.
 * Source and destination may be the same nine-element array. Matrix3 never calls this implicitly.
 */
object NativeMatrixQuantization {
    fun forceRowSumsToOne(source: FloatArray, destination: FloatArray) {
        require(source.size==9 && destination.size==9)
        require(source.all { it.isFinite() })
        for (row in 0..2) row(source,row*3,null) // preflight before writing partial output
        for (row in 0..2) row(source,row*3,destination)
    }
    private fun row(source: FloatArray, offset: Int, out: FloatArray?) {
        val a=source[offset]; val b=source[offset+1]; val c=source[offset+2]
        val sum=(a+b)+c
        require(sum.isFinite() && sum!=0f) { "Zero or nonfinite row sum" }
        val scale=65536f/sum
        require(scale.isFinite()) { "Row normalization scale overflow" }
        val qa=roundSignedInt(a*scale); val qb=roundSignedInt(b*scale)
        val first=qa.toFloat()/65536f; val second=qb.toFloat()/65536f
        val third=(1f-first)-second
        require(first.isFinite() && second.isFinite() && third.isFinite())
        if (out!=null) { out[offset]=first; out[offset+1]=second; out[offset+2]=third }
    }
    private fun roundSignedInt(x: Float): Int {
        require(x.isFinite()) { "Nonfinite FCVTNS input" }
        val rounded=Math.rint(x.toDouble()) // nearest, ties to even
        require(rounded>=Int.MIN_VALUE.toDouble() && rounded<=Int.MAX_VALUE.toDouble()) {
            "FCVTNS input outside supported signed-int32 domain"
        }
        return rounded.toInt()
    }
}
