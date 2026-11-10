package dev.cxo.filmpack.core

/** Builds the native 65^3 RGB8 color LUT from the verified Q15 CMM pair. */
object NativeGrain8ColorLutFactory {
    private const val GRID_SIZE = 65
    private const val ACTIVE_LAST = 63
    private const val Q15_INPUT_SCALE = 128.49803161621094f
    private const val Q15_OUTPUT_SCALE = 0.007782219909131527f

    /**
     * Samples the LCMS RGB16 transform at the native RGB8 lattice.
     * Layout is [B][G][R][RGBA], with R changing fastest and alpha padding 0.
     */
    fun fromQ15Transform(transform: LcmsOptimizedQ15Transform): NativeGrain8ColorLut {
        val grid = FloatArray(GRID_SIZE * GRID_SIZE * GRID_SIZE * 4)
        val q15Input = ShortArray(3)
        val q15Output = ShortArray(3)
        for (b in 0..ACTIVE_LAST) {
            for (g in 0..ACTIVE_LAST) {
                for (r in 0..ACTIVE_LAST) {
                    val red = axisValue(r)
                    val green = axisValue(g)
                    val blue = axisValue(b)
                    q15Input[0] = quantize(red)
                    q15Input[1] = quantize(green)
                    q15Input[2] = quantize(blue)
                    transform.transform(q15Input, q15Output)
                    val base = ((b * GRID_SIZE + g) * GRID_SIZE + r) * 4
                    grid[base] = restore(q15Output[0])
                    grid[base + 1] = restore(q15Output[1])
                    grid[base + 2] = restore(q15Output[2])
                    grid[base + 3] = 0f
                }
            }
        }
        for (b in 0 until GRID_SIZE) {
            for (g in 0 until GRID_SIZE) {
                for (r in 0 until GRID_SIZE) {
                    copyCell(grid, b.coerceAtMost(ACTIVE_LAST), g.coerceAtMost(ACTIVE_LAST),
                        r.coerceAtMost(ACTIVE_LAST), b, g, r)
                }
            }
        }
        return NativeGrain8ColorLut(grid)
    }

    private fun copyCell(grid: FloatArray, sourceB: Int, sourceG: Int, sourceR: Int,
                         destinationB: Int, destinationG: Int, destinationR: Int) {
        val source = ((sourceB * GRID_SIZE + sourceG) * GRID_SIZE + sourceR) * 4
        val destination = ((destinationB * GRID_SIZE + destinationG) * GRID_SIZE + destinationR) * 4
        grid[destination] = grid[source]
        grid[destination + 1] = grid[source + 1]
        grid[destination + 2] = grid[source + 2]
        grid[destination + 3] = grid[source + 3]
    }

    private fun axisValue(index: Int): Float = (255f / 63f) * index.toFloat()

    private fun quantize(value: Float): Short =
        (value * Q15_INPUT_SCALE + 0.5f).toInt().coerceIn(0, 32767).toShort()

    private fun restore(value: Short): Float = value.toInt().toFloat() * Q15_OUTPUT_SCALE
}
