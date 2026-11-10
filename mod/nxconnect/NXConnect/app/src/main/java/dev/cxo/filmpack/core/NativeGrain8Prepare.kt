package dev.cxo.filmpack.core

/**
 * Recovered square RGB8 patch preparation at an explicitly supplied scale. The caller
 * supplies native output orientation, patch scale and matching
 * ICC LUT. This does not select film format, preview zoom, or output coordinates.
 */
object NativeGrain8Prepare {
    data class Result(val bytes: ByteArray, val size: Int, val period: Int)

    /** Supports the native 1000-square patch with reduction, identity and cubic enlargement. */
    fun prepare(
        source: ByteArray,
        scale: Float,
        colorLut: NativeGrain8ColorLut,
        seed: Long = 0,
        nativeOutputOrientation: Int = 0,
    ): Result {
        require(source.size == 1000 * 1000 * 3) { "Expected native 1000-square RGB8 patch" }
        require(scale.isFinite() && scale > 0f) { "Expected positive finite patch scale" }
        require(seed in 0L..0xFFFFFFFFL) { "Expected unsigned 32-bit seed" }
        require(nativeOutputOrientation in 0..7) { "Expected native orientation in 0..7" }
        val size = (scale * 1000f + 0.5f).toInt()
        require(size.toLong() * size <= Int.MAX_VALUE.toLong() / 3) { "Prepared patch is too large" }
        val margin = (scale * 3f + 0.5f).toInt()
        require(size > margin + 1) { "Patch is too small for periodic edges" }

        val rotated = if (nativeOutputOrientation == 0) source else
            NativeGrain8Orientation.rotateForGrain(source, 1000, 1000, nativeOutputOrientation).bytes
        val offsets = NativeGrain8Seed.offsets(seed, 1000, 1000)
        val shifted = if (seed == 0L) rotated else NativeGrain8Shift.apply(
            rotated, 1000, 1000, offsets.x.toLong(), offsets.y.toLong(),
        )
        val resized = if (scale > 1f) {
            NativeGrain8Cubic.resize(shifted, 1000, 1000, size, size)
        } else {
            val ratio = 1000f / size.toFloat()
            val intermediate = if (ratio >= 2f) {
                ((ratio / ratio.toInt().toFloat()) * size.toFloat() + 0.5f).toInt()
            } else 1000
            val mean = if (intermediate == 1000) shifted else NativeGrain8Resample.meanDownsample(
                shifted, 1000, 1000, intermediate, intermediate,
            )
            if (intermediate == size) mean else NativeGrain8Resample.linearResize(
                mean, intermediate, intermediate, size, size,
            )
        }
        val converted = colorLut.apply(resized, size, size)
        val periodic = NativeGrain8Periodic.apply(converted, size, scale)
        return Result(periodic.bytes, size, periodic.period)
    }
}
