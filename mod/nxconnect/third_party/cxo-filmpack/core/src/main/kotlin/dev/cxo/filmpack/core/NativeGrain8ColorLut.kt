package dev.cxo.filmpack.core

/**
 * RGB8 application of the policy-3 Q15 CMM's 65³ float4 table. This reproduces
 * libCorrectionsNEON's RGB8 interpolation, including its first-lane FMA order,
 * overlapping last block, and nearest-even quantization. It does not build ICC
 * profiles or perform resampling/periodic edge preparation.
 *
 * Grid order is [blue][green][red][component], with red varying fastest. The
 * native initializer repeats node 63 at guard node 64 on each axis.
 */
class NativeGrain8ColorLut(grid: FloatArray) {
    private val nodes: FloatArray = grid.copyOf().also { copy ->
        require(copy.size == GRID_FLOATS) { "RGB8 LUT must contain 65³ float4 nodes" }
        require(copy.all { it.isFinite() && it >= 0f && it <= 255f }) {
            "RGB8 LUT components must be finite and in 0..255"
        }
    }

    fun copyGrid(): FloatArray = nodes.copyOf()

    /** Returns fresh packed RGB8 bytes and leaves the source unchanged. */
    fun apply(source: ByteArray, width: Int, height: Int): ByteArray {
        require(width > 0 && height > 0) { "image dimensions must be positive" }
        val pixels = width.toLong() * height.toLong()
        require(pixels <= Int.MAX_VALUE.toLong() / 3L) { "RGB8 image is too large" }
        require(source.size == (pixels * 3L).toInt()) { "source must contain packed RGB8 pixels" }
        val output = ByteArray(source.size)
        for (y in 0 until height) {
            if (width < 4) {
                // The fallback's intermediate row is extended to four pixels;
                // active pixels retain lanes 0, 1, 2, rather than shifting left.
                for (x in 0 until width) pixel(source, output, (y * width + x) * 3, x == 0)
            } else {
                var x = 0
                while (x < width) {
                    val start = minOf(x, width - 4)
                    for (lane in 0 until 4) {
                        pixel(source, output, (y * width + start + lane) * 3, lane == 0)
                    }
                    x = start + 4
                }
            }
        }
        return output
    }

    private fun pixel(source: ByteArray, output: ByteArray, offset: Int, laneZero: Boolean) {
        val red = source[offset].toInt() and 255
        val green = source[offset + 1].toInt() and 255
        val blue = source[offset + 2].toInt() and 255
        val wr = FRACTION[red]; val wg = FRACTION[green]; val wb = FRACTION[blue]
        val base = ((LOW[blue] * 65 + LOW[green]) * 65 + LOW[red]) * 4
        for (channel in 0 until 3) {
            val n = base + channel
            val lowRedLowGreen = if (laneZero) {
                FiniteFloatFma.unchecked(nodes[n + BLUE_STRIDE], wb, nodes[n] * (1f - wb))
            } else blend(nodes[n], nodes[n + BLUE_STRIDE], wb)
            val lowRedHighGreen = blend(nodes[n + GREEN_STRIDE], nodes[n + GREEN_STRIDE + BLUE_STRIDE], wb)
            val highRedLowGreen = blend(nodes[n + 4], nodes[n + 4 + BLUE_STRIDE], wb)
            val highRedHighGreen = blend(nodes[n + 4 + GREEN_STRIDE], nodes[n + 4 + GREEN_STRIDE + BLUE_STRIDE], wb)
            val lowRed = blend(lowRedLowGreen, lowRedHighGreen, wg)
            val highRed = blend(highRedLowGreen, highRedHighGreen, wg)
            val value = blend(lowRed, highRed, wr)
            output[offset + channel] = Math.rint(value.toDouble()).toInt().coerceIn(0, 255).toByte()
        }
    }

    private fun blend(low: Float, high: Float, weight: Float): Float =
        FiniteFloatFma.unchecked(1f - weight, low, high * weight)

    companion object {
        const val GRID_FLOATS: Int = 65 * 65 * 65 * 4
        private const val BLUE_STRIDE: Int = 65 * 65 * 4
        private const val GREEN_STRIDE: Int = 65 * 4
        private val COORDINATE = FloatArray(256) { it.toFloat() * Float.fromBits(0x3e7cfcfd) }
        private val LOW = IntArray(256) { COORDINATE[it].toInt() }
        private val FRACTION = FloatArray(256) { COORDINATE[it] - LOW[it].toFloat() }
    }
}
