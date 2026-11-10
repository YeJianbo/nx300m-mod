// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import kotlin.math.floor
import kotlin.math.pow

data class RawRenderParams(
    val exposureEv: Float = 0f,
    val contrast: Int = 0,
    val highlights: Int = 0,
    val shadows: Int = 0,
    val temperature: Int = 0,
    val tint: Int = 0,
    val saturation: Int = 0,
    val lutStrength: Int = 100,
) {
    init {
        require(exposureEv in -3f..3f) { "曝光须在 -3 至 +3 EV" }
        require(contrast in -100..100) { "对比度须在 -100 至 100" }
        require(highlights in -100..100) { "高光须在 -100 至 100" }
        require(shadows in -100..100) { "阴影须在 -100 至 100" }
        require(temperature in -100..100) { "色温须在 -100 至 100" }
        require(tint in -100..100) { "色调须在 -100 至 100" }
        require(saturation in -100..100) { "饱和度须在 -100 至 100" }
        require(lutStrength in 0..100) { "LUT 强度须在 0 至 100" }
    }

    fun isNeutral() = exposureEv == 0f && contrast == 0 && highlights == 0 && shadows == 0 &&
        temperature == 0 && tint == 0 && saturation == 0
}

/** CPU rendering after LibRaw has unpacked the sensor data and camera white balance. */
object RawRenderPipeline {
    private const val LINEAR_STEPS = 4096
    private val srgbToLinear = FloatArray(256) { value ->
        val x = value / 255f
        if (x <= 0.04045f) x / 12.92f else ((x + 0.055f) / 1.055f).pow(2.4f)
    }
    private val linearToSrgb = FloatArray(LINEAR_STEPS + 1) { index ->
        val x = index.toFloat() / LINEAR_STEPS
        if (x <= 0.0031308f) 12.92f * x else 1.055f * x.pow(1f / 2.4f) - 0.055f
    }

    fun render(input: IntArray, params: RawRenderParams, lut: CubeLut.Data? = null): IntArray {
        val output = IntArray(input.size)
        renderInto(input, output, params, lut)
        return output
    }

    fun renderInto(
        input: IntArray,
        output: IntArray,
        params: RawRenderParams,
        lut: CubeLut.Data? = null,
        shouldContinue: () -> Boolean = { true },
    ): Boolean {
        require(output.size == input.size) { "输入和输出像素数量不一致" }
        if (params.isNeutral() && (lut == null || params.lutStrength == 0)) {
            input.copyInto(output)
            return true
        }

        val exposure = 2f.pow(params.exposureEv)
        val contrast = params.contrast / 100f
        val highlights = params.highlights / 100f
        val shadows = params.shadows / 100f
        val warmth = params.temperature / 100f
        val tint = params.tint / 100f
        val saturation = params.saturation / 100f
        val lutMix = if (lut == null) 0f else params.lutStrength / 100f
        val redBalance = 1f + warmth * 0.18f + tint * 0.10f
        val greenBalance = 1f - tint * 0.16f
        val blueBalance = 1f - warmth * 0.18f + tint * 0.10f

        for (index in input.indices) {
            if (index and 0x3fff == 0 && !shouldContinue()) return false
            val pixel = input[index]
            var red = srgbToLinear[pixel shr 16 and 0xff] * exposure * redBalance
            var green = srgbToLinear[pixel shr 8 and 0xff] * exposure * greenBalance
            var blue = srgbToLinear[pixel and 0xff] * exposure * blueBalance

            if (highlights != 0f || shadows != 0f) {
                val luma = 0.2126f * red + 0.7152f * green + 0.0722f * blue
                val highWeight = smoothStep(0.35f, 0.95f, luma)
                val shadowWeight = 1f - smoothStep(0.005f, 0.48f, luma)
                val gain = 1f + highlights * 0.45f * highWeight + shadows * 0.55f * shadowWeight
                red *= gain
                green *= gain
                blue *= gain
            }

            red = encodeSrgb(red)
            green = encodeSrgb(green)
            blue = encodeSrgb(blue)

            if (contrast != 0f) {
                val factor = 1f + contrast * 0.8f
                red = (red - 0.5f) * factor + 0.5f
                green = (green - 0.5f) * factor + 0.5f
                blue = (blue - 0.5f) * factor + 0.5f
            }

            if (saturation != 0f) {
                val luma = 0.2126f * red + 0.7152f * green + 0.0722f * blue
                val factor = 1f + saturation
                red = luma + (red - luma) * factor
                green = luma + (green - luma) * factor
                blue = luma + (blue - luma) * factor
            }

            red = red.coerceIn(0f, 1f)
            green = green.coerceIn(0f, 1f)
            blue = blue.coerceIn(0f, 1f)

            if (lut != null && lutMix > 0f) {
                val size = lut.grid
                val x = ((red - lut.domainMin[0]) / (lut.domainMax[0] - lut.domainMin[0]))
                    .coerceIn(0.0, 1.0) * (size - 1)
                val y = ((green - lut.domainMin[1]) / (lut.domainMax[1] - lut.domainMin[1]))
                    .coerceIn(0.0, 1.0) * (size - 1)
                val z = ((blue - lut.domainMin[2]) / (lut.domainMax[2] - lut.domainMin[2]))
                    .coerceIn(0.0, 1.0) * (size - 1)
                val x0 = floor(x).toInt().coerceAtMost(size - 2)
                val y0 = floor(y).toInt().coerceAtMost(size - 2)
                val z0 = floor(z).toInt().coerceAtMost(size - 2)
                val dx = x - x0
                val dy = y - y0
                val dz = z - z0
                var mappedRed = 0f
                var mappedGreen = 0f
                var mappedBlue = 0f
                for (dzBit in 0..1) for (dyBit in 0..1) for (dxBit in 0..1) {
                    val offset = (((z0 + dzBit) * size + y0 + dyBit) * size + x0 + dxBit) * 3
                    val weight = ((if (dxBit == 0) 1.0 - dx else dx) *
                        (if (dyBit == 0) 1.0 - dy else dy) *
                        (if (dzBit == 0) 1.0 - dz else dz)).toFloat()
                    mappedRed += lut.rgb[offset] * weight
                    mappedGreen += lut.rgb[offset + 1] * weight
                    mappedBlue += lut.rgb[offset + 2] * weight
                }
                red += (mappedRed.coerceIn(0f, 1f) - red) * lutMix
                green += (mappedGreen.coerceIn(0f, 1f) - green) * lutMix
                blue += (mappedBlue.coerceIn(0f, 1f) - blue) * lutMix
            }

            output[index] = (pixel and 0xff000000.toInt()) or
                (toByte(red) shl 16) or (toByte(green) shl 8) or toByte(blue)
        }
        return true
    }

    private fun encodeSrgb(linear: Float): Float {
        val position = linear.coerceIn(0f, 1f) * LINEAR_STEPS
        val lower = floor(position).toInt().coerceIn(0, LINEAR_STEPS - 1)
        val fraction = position - lower
        return linearToSrgb[lower] + (linearToSrgb[lower + 1] - linearToSrgb[lower]) * fraction
    }

    private fun smoothStep(edge0: Float, edge1: Float, value: Float): Float {
        val x = ((value - edge0) / (edge1 - edge0)).coerceIn(0f, 1f)
        return x * x * (3f - 2f * x)
    }

    private fun toByte(value: Float): Int = (value.coerceIn(0f, 1f) * 255f + 0.5f).toInt()
}
