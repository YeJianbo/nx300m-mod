package dev.cxo.filmpack.core

/** Fresh packed-RGB8 periodic shift recovered from the native raw leaf. */
object NativeGrain8Shift {
    fun apply(source: ByteArray, width: Int, height: Int, offsetX: Long, offsetY: Long): ByteArray {
        require(width > 0 && height > 0) { "dimensions must be positive" }
        require(offsetX >= 0 && offsetY >= 0) { "offsets must be non-negative" }
        val bytes = checkedBytes(width, height)
        require(source.size == bytes) { "source must contain packed RGB8 pixels" }
        val x = (offsetX % width.toLong()).toInt()
        val y = (offsetY % height.toLong()).toInt()
        val output = ByteArray(bytes)
        for (row in 0 until height) {
            val sourceRow = (row + y) % height
            for (column in 0 until width) {
                val sourceColumn = (column + x) % width
                val sourceIndex = ((sourceRow.toLong() * width + sourceColumn) * 3L).toInt()
                val outputIndex = ((row.toLong() * width + column) * 3L).toInt()
                output[outputIndex] = source[sourceIndex]
                output[outputIndex + 1] = source[sourceIndex + 1]
                output[outputIndex + 2] = source[sourceIndex + 2]
            }
        }
        return output
    }

    private fun checkedBytes(width: Int, height: Int): Int {
        val pixels = width.toLong() * height.toLong()
        require(pixels <= Int.MAX_VALUE.toLong() / 3L) { "image buffer is too large" }
        return (pixels * 3L).toInt()
    }
}
