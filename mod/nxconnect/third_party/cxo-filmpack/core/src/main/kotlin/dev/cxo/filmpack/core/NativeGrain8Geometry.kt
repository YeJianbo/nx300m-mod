package dev.cxo.filmpack.core

import kotlin.math.max

/** Recovered size scheduling for uncropped images, invocation scales0..2 and divisor1. */
object NativeGrain8Geometry {
    enum class Format(val nativeValue: Int, val token: String) {
        SMALL(0, "24x36"), MEDIUM(1, "Medium"), LARGE(2, "Large"), MANUAL(3, "Manual");

        companion object {
            fun fromToken(token: String): Format = entries.firstOrNull { it.token == token }
                ?: throw IllegalArgumentException("Unsupported native grain format: $token")
        }
    }

    data class Result(val nativeSize: Int, val scale: Float, val size: Int, val period: Int)

    /**
     * Manual size is truncated after Float multiplication by20. A nonzero size is
     * required in Manual mode; native size-zero fallback, crop and preview zoom
     * remain outside this API. Other formats derive size from the image's long edge.
     */
    fun resolve(
        grainEnum: Int, format: Format, imageWidth: Int, imageHeight: Int, manualSize: Float = 5f,
        invocationScale: Int = 0,
    ): Result {
        require(invocationScale in 0..2) { "Supported invocation scales are0..2" }
        require(grainEnum > 0 && imageWidth > 0 && imageHeight > 0)
        val nativeSize = if (format == Format.MANUAL) {
            require(manualSize.isFinite() && manualSize > 0f)
            val scaled = manualSize * 20f
            require(scaled < (Int.MAX_VALUE - 112).toFloat()) { "Manual grain size is too large" }
            scaled.toInt().also { require(it > 0) { "Manual grain size must not quantize to zero" } }
        } else {
            val longEdge = max(imageWidth, imageHeight)
            require(longEdge <= Int.MAX_VALUE / 45) { "Image dimension exceeds bounded native arithmetic" }
            val density = when (format) {
                Format.SMALL -> longEdge / 48
                Format.MEDIUM -> longEdge / 64
                Format.LARGE -> longEdge * 45 / 4800
                else -> error("Manual handled separately")
            }
            (density.toFloat() * 1.5f - 25f).toInt().coerceIn(20, 200)
        }
        val adjusted = nativeSize + if (grainEnum == 1023) 112 else 0
        // Original Engine 0x5de974..0x5de9a0 uses separate FMUL/FADD/FDIV.
        val fullScale = (adjusted.toFloat() * Float.fromBits(0x3f2aaaab) + Float.fromBits(0x41855550)) / 100f
        val scale = fullScale / (1 shl invocationScale).toFloat()
        val size = (scale * 1000f + 0.5f).toInt()
        require(size > 1 && size.toLong() * size <= Int.MAX_VALUE.toLong() / 3) { "Prepared patch is too large" }
        val margin = (scale * 3f + 0.5f).toInt()
        require(margin < size - 1)
        return Result(nativeSize, scale, size, size - margin - 1)
    }
}
