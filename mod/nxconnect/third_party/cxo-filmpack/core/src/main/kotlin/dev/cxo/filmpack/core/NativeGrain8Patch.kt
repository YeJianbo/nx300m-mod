package dev.cxo.filmpack.core

/** Recovered Q15/RGB8-patch leaf kernel, Engine FUN_005e14e8.
 * Prepared patch and amplitude map are immutable copies, reusable across tiles.
 * This does not load film resources or implement patch preparation, image scale,
 * colour management or random seed selection. Evidence: grain-20261101/kernel-i15-patch8.txt.
 */
class NativeGrain8Patch(
    patch: ByteArray,
    private val patchWidth: Int,
    patchHeight: Int,
    private val period: Int,
    amplitudeMap: IntArray,
    color: Boolean = true,
    neutralizeRedBlue: Boolean = false,
) {
    private val pixels = patch.copyOf()
    private val amplitude = amplitudeMap.copyOf()
    private val monochrome = !color || neutralizeRedBlue

    init {
        require(patchWidth > 0 && patchHeight > 0) { "Patch dimensions must be positive" }
        require(period > 0 && period <= minOf(patchWidth, patchHeight)) { "Invalid patch period" }
        val samples = patchWidth.toLong() * patchHeight
        require(samples <= Int.MAX_VALUE / 3 && pixels.size.toLong() == samples * 3) {
            "Expected packed RGB8 patch"
        }
        require(amplitude.size == 256 && amplitude.all { it in 0..Int.MAX_VALUE / 128 }) {
            "Expected 256 bounded nonnegative amplitude values"
        }
    }

    /**
     * Bridge from the film core's normalized internal encoded RGB to the Q15
     * grain node. This performs no ICC conversion: input and output stay in
     * the same internal working profile. Quantization uses ties-to-even.
     * Film-core integer-Q15/32767 values round-trip exactly.
     * Supports in-place Float buffers and preserves global tile coordinates.
     */
    fun renderNormalizedRgb(
        source: FloatArray, destination: FloatArray, width: Int,
        xOffset: Int = 0, yOffset: Int = 0,
    ) {
        require(source.size % 3 == 0 && destination.size == source.size) { "Expected equal packed RGB buffers" }
        require(source.all { it.isFinite() && it in 0f..1f }) { "Expected normalized internal RGB" }
        val q15 = ShortArray(source.size) { index ->
            Math.rint((source[index] * 32767f).toDouble()).toInt().toShort()
        }
        // The Q15 entry validates shape and coordinates before writing; Float
        // destination remains untouched if any input contract is invalid.
        renderRgbQ15(q15, q15, width, xOffset, yOffset)
        for (index in q15.indices) destination[index] = q15[index].toFloat() / 32767f
    }

    /** Packed RGB values 0..32767; supports in-place rendering. Coordinates are
     * global image coordinates, so separately processed tiles keep the same phase.
     * Every input is validated before the first destination write.
     */
    fun renderRgbQ15(
        source: ShortArray, destination: ShortArray, width: Int,
        xOffset: Int = 0, yOffset: Int = 0,
    ) {
        require(source.size % 3 == 0 && destination.size == source.size) { "Expected equal packed RGB buffers" }
        require(width > 0 && source.size / 3 % width == 0) { "Width must divide the pixel count" }
        val height = source.size / 3 / width
        require(xOffset >= 0 && yOffset >= 0 &&
            xOffset.toLong() + width - 1 <= Int.MAX_VALUE &&
            yOffset.toLong() + maxOf(0, height - 1) <= Int.MAX_VALUE) { "Invalid global coordinates" }
        require(source.all { it >= 0 }) { "Expected nonnegative Q15 input" }
        for (row in 0 until height) {
            val patchY = (yOffset + row) % period
            for (column in 0 until width) {
                val index = (row * width + column) * 3
                val red = source[index].toInt()
                val green = source[index + 1].toInt()
                val blue = source[index + 2].toInt()
                val luma = (77 * red + 153 * green + 26 * blue) shr 15
                if (luma < 1) {
                    destination[index] = red.toShort()
                    destination[index + 1] = green.toShort()
                    destination[index + 2] = blue.toShort()
                    continue
                }
                val patchIndex = (patchY * patchWidth + (xOffset + column) % period) * 3
                val strength = amplitude[luma]
                val gr = (pixels[patchIndex + 1].toInt() and 255) - 127
                val rr = if (monochrome) gr else (pixels[patchIndex].toInt() and 255) - 127
                val br = if (monochrome) gr else (pixels[patchIndex + 2].toInt() and 255) - 127
                destination[index] = (red + ((strength * rr) shr 1)).coerceIn(0, 32767).toShort()
                destination[index + 1] = (green + ((strength * gr) shr 1)).coerceIn(0, 32767).toShort()
                destination[index + 2] = (blue + ((strength * br) shr 1)).coerceIn(0, 32767).toShort()
            }
        }
    }

    /** Process a raw-layout tile as if the full image were oriented first.
     * The patch must already be prepared for outputOrientation. Returned pixels
     * keep the raw tile layout, suitable for an encoder that retains EXIF.
     * This is a coordinate transform, not grain-then-rotate equivalence. */
    fun renderRgbQ15InSourceLayout(
        source: ShortArray, destination: ShortArray, width: Int,
        sourceWidth: Int, sourceHeight: Int, outputOrientation: Int,
        xOffset: Int = 0, yOffset: Int = 0,
    ) {
        require(source.size == destination.size && source.all { it >= 0 }) { "Expected equal nonnegative Q15 buffers" }
        renderInSourceLayout(source.size,width,sourceWidth,sourceHeight,outputOrientation,xOffset,yOffset,
            read = { source[it] }, write = { index,value -> destination[index] = value })
    }

    /** Normalized working-profile counterpart; ICC conversion stays after grain. */
    fun renderNormalizedRgbInSourceLayout(
        source: FloatArray, destination: FloatArray, width: Int,
        sourceWidth: Int, sourceHeight: Int, outputOrientation: Int,
        xOffset: Int = 0, yOffset: Int = 0,
    ) {
        require(source.size == destination.size && source.all { it.isFinite() && it in 0f..1f }) {
            "Expected equal normalized working-profile buffers"
        }
        renderInSourceLayout(source.size,width,sourceWidth,sourceHeight,outputOrientation,xOffset,yOffset,
            read = { Math.rint((source[it]*32767f).toDouble()).toInt().toShort() },
            write = { index,value -> destination[index] = value.toFloat()/32767f })
    }

    private inline fun renderInSourceLayout(
        components: Int, width: Int, sourceWidth: Int, sourceHeight: Int, orientation: Int,
        x: Int, y: Int, read: (Int) -> Short, write: (Int,Short) -> Unit,
    ) {
        require(width > 0 && components > 0 && components % 3 == 0 && components/3 % width == 0) { "Invalid source tile shape" }
        val height = components/3/width
        require(sourceWidth > 0 && sourceHeight > 0 && orientation in 0..7 && x >= 0 && y >= 0 &&
            x.toLong()+width <= sourceWidth && y.toLong()+height <= sourceHeight) { "Invalid source tile coordinates" }
        val dw = if (orientation >= 4) height else width
        val dh = if (orientation >= 4) width else height
        val ox = when (orientation) {
            0,3 -> x
            1,2 -> sourceWidth-x-width
            4,7 -> y
            else -> sourceHeight-y-height
        }
        val oy = when (orientation) {
            0,1 -> y
            2,3 -> sourceHeight-y-height
            4,5 -> x
            else -> sourceWidth-x-width
        }
        val tile = ShortArray(components)
        for (dy in 0 until dh) for (dx in 0 until dw) {
            val sx = when (orientation) {
                0,3 -> dx
                1,2 -> width-1-dx
                4,5 -> dy
                else -> width-1-dy
            }
            val sy = when (orientation) {
                0,1 -> dy
                2,3 -> height-1-dy
                4,7 -> dx
                else -> height-1-dx
            }
            val from = (sy*width+sx)*3; val to = (dy*dw+dx)*3
            repeat(3) { channel -> tile[to+channel] = read(from+channel) }
        }
        renderRgbQ15(tile,tile,dw,ox,oy)
        // All reads and validation finish before writing; source/destination may alias.
        for (dy in 0 until dh) for (dx in 0 until dw) {
            val sx = when (orientation) {
                0,3 -> dx
                1,2 -> width-1-dx
                4,5 -> dy
                else -> width-1-dy
            }
            val sy = when (orientation) {
                0,1 -> dy
                2,3 -> height-1-dy
                4,7 -> dx
                else -> height-1-dx
            }
            val to = (sy*width+sx)*3; val from = (dy*dw+dx)*3
            repeat(3) { channel -> write(to+channel,tile[from+channel]) }
        }
    }
}
