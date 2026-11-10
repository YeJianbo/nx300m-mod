package dev.cxo.filmpack.core

/** Recovered placement prefix of native 0x599578; not the full texture affine.
 * Native positions: random=0, top=1, bottom=2, left=3, right=4.
 * The ratio is consumed by full prepare as a translation coefficient.
 */
object NativeEdgePlacement {
    data class Result(val translationRatio: Float, val clockwiseTurns: Int) {
        fun nativeMatrix(): FloatArray = when ((4 - clockwiseTurns) and 3) {
            0 -> floatArrayOf(1f,0f,0f, 0f,1f,0f, 0f,0f,1f)
            1 -> floatArrayOf(0f,1f,0f, -1f,0f,0f, 0f,0f,1f)
            2 -> floatArrayOf(-1f,0f,0f, 0f,-1f,0f, 0f,0f,1f)
            else -> floatArrayOf(0f,-1f,0f, 1f,0f,0f, 0f,0f,1f)
        }
    }
    private fun next15(value: Int): Int = ((value * 0x343fd + 0x269ec3) ushr 16) and 0x7fff
    fun resolve(seed: Long, nativePosition: Int): Result {
        require(nativePosition in 0..4)
        val first = next15(seed.toInt())
        val quarter = when (nativePosition) {
            0 -> next15(first) and 3
            1 -> 0
            2 -> 2
            3 -> 1
            else -> 3
        }
        return Result((first % 101).toFloat() / 100f, (4 - quarter) and 3)
    }
}
