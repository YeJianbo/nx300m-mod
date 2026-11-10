package dev.cxo.filmpack.core

/** Native raw-patch orientation semantics, independent of Android bitmap/EXIF conventions. */
object NativeGrain8Orientation {
    data class Result(val bytes: ByteArray, val width: Int, val height: Int)

    /** Native OrientationType 0..7, including flips; always returns an owned copy. */
    fun apply(source: ByteArray, width: Int, height: Int, orientation: Int): Result {
        require(width > 0 && height > 0) { "dimensions must be positive" }
        require(width.toLong() * height <= Int.MAX_VALUE.toLong() / 3) { "image buffer is too large" }
        require(source.size == width * height * 3) { "source must be packed RGB8" }
        require(orientation in 0..7) { "native orientation must be in 0..7" }
        val dw = if (orientation >= 4) height else width
        val dh = if (orientation >= 4) width else height
        val output = ByteArray(source.size)
        for (dy in 0 until dh) for (dx in 0 until dw) {
            val sx: Int
            val sy: Int
            when (orientation) {
                0 -> { sx = dx; sy = dy }
                1 -> { sx = width - 1 - dx; sy = dy }
                2 -> { sx = width - 1 - dx; sy = height - 1 - dy }
                3 -> { sx = dx; sy = height - 1 - dy }
                4 -> { sx = dy; sy = dx }
                5 -> { sx = dy; sy = height - 1 - dx }
                6 -> { sx = width - 1 - dy; sy = height - 1 - dx }
                else -> { sx = width - 1 - dy; sy = dx }
            }
            val from = (sy * width + sx) * 3
            val to = (dy * dw + dx) * 3
            output[to] = source[from]
            output[to + 1] = source[from + 1]
            output[to + 2] = source[from + 2]
        }
        return Result(output, dw, dh)
    }

    /** The grain node extracts rotation and drops the output's flip component. */
    fun rotateForGrain(source: ByteArray, width: Int, height: Int, outputOrientation: Int): Result =
        counterClockwise(source, width, height, grainCounterClockwiseTurns(outputOrientation))

    fun counterClockwise(source: ByteArray, width: Int, height: Int, turns: Int): Result {
        require(turns in 0..3) { "counterclockwise turns must be in 0..3" }
        return apply(source, width, height, intArrayOf(0, 7, 2, 5)[turns])
    }

    fun grainCounterClockwiseTurns(outputOrientation: Int): Int {
        require(outputOrientation in 0..7) { "native orientation must be in 0..7" }
        return intArrayOf(0, 0, 2, 0, 3, 3, 1, 1)[outputOrientation]
    }

    /** Native OutputOrientation serialized values; these are case-sensitive. */
    fun fromOutputParameter(value: String): Int = when (value) {
        "None" -> 0
        "HorizontalFlip" -> 1
        "180CW" -> 2
        "VerticalFlip" -> 3
        "90CWAndHorizontalFlip" -> 4
        "90CW" -> 5
        "270CWAndHorizontalFlip" -> 6
        "270CW" -> 7
        else -> throw IllegalArgumentException("unsupported native OutputOrientation: $value")
    }

    /** Recovered native conversion table; caller must establish its metadata boundary. */
    fun fromNativeExifValue(value: Int): Int {
        require(value in 1..8) { "EXIF orientation must be in 1..8" }
        return intArrayOf(0, 1, 2, 3, 6, 5, 4, 7)[value - 1]
    }

    /** Match the standard EXIF display pixel operation to our native leaf mapping.
     * This is an explicit output-normalization choice, not the engine's raw EXIF
     * conversion table above (whose5/7 entries differ). Source EXIF by itself
     * does not change the engine's OutputOrientation=None grain coordinates. */
    fun fromExifDisplayTransform(value: Int): Int {
        require(value in 1..8) { "EXIF orientation must be in 1..8" }
        return value-1
    }
}
