package dev.cxo.filmpack.core

/**
 * Native periodic-edge leaf from FUN_005dfaf8. This accepts a prepared square
 * RGB8 patch only; it is not full patch preparation, scaling, seed, or grain
 * integration.
 */
object NativeGrain8Periodic {
    data class Result(val bytes: ByteArray, val period: Int)

    /** Returns a fresh RGB8 patch and the native edge period. */
    fun apply(source: ByteArray, size: Int, scale: Float): Result {
        require(size > 1) { "periodic patch must have at least two pixels per side" }
        require(scale.isFinite() && scale >= 0f) { "scale must be finite and non-negative" }
        val sourceBytes = checkedBytes(size)
        require(source.size == sourceBytes) { "source must contain square packed RGB8 pixels" }
        val marginFloat = scale * 3f + 0.5f
        require(marginFloat <= Int.MAX_VALUE.toFloat()) { "scale is too large" }
        val margin = marginFloat.toInt()
        require(margin < size - 1) { "periodic margin must be smaller than size - 1" }
        val period = size - margin - 1
        require(period > 0) { "period must be positive" }

        val output = source.copyOf()
        val inverseMargin = if (margin == 0) 0f else 1f / margin.toFloat()
        if (margin == 0) return Result(output, period)
        for (channel in 0 until 3) {
            for (row in 0 until size) {
                for (column in 0..margin) {
                    val left = output[(row * size + column) * 3 + channel].toInt() and 0xff
                    val right = output[(row * size + column + period) * 3 + channel].toInt() and 0xff
                    val weighted = (column * left + (margin - column) * right).toFloat()
                    output[(row * size + column) * 3 + channel] = (inverseMargin * weighted).toInt().coerceIn(0, 255).toByte()
                }
            }
            for (rowOffset in 0..margin) {
                for (column in margin until size) {
                    val top = output[(rowOffset * size + column) * 3 + channel].toInt() and 0xff
                    val bottom = output[((rowOffset + period) * size + column) * 3 + channel].toInt() and 0xff
                    val weighted = (rowOffset * top + (margin - rowOffset) * bottom).toFloat()
                    output[(rowOffset * size + column) * 3 + channel] = (inverseMargin * weighted).toInt().coerceIn(0, 255).toByte()
                }
            }
        }
        return Result(output, period)
    }

    private fun checkedBytes(size: Int): Int {
        val pixels = size.toLong() * size.toLong()
        require(pixels <= Int.MAX_VALUE.toLong() / 3L) { "periodic patch is too large" }
        return (pixels * 3L).toInt()
    }
}
