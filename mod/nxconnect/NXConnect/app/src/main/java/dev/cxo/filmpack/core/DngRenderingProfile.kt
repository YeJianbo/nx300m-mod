package dev.cxo.filmpack.core

import kotlin.math.pow

/** Fixed color-space conversions, independent of camera and photograph.
 * Source: Engine EIR constructor 0x4b235c, RODATA 0x1c333a0/0x1c333e8
 * (feature5=false). Row normalization is NEON ForceRowSumTo1 0x4e1944.
 */
object DngRenderingProfile {
    private val proPhotoToWorking = forceRows(floatArrayOf(
        1.1245099306106567f, .011668859981000423f, -.13610242307186127f,
        -.06999372690916061f, 1.0800426006317139f, -.01006184983998537f,
        .006401530001312494f, -.05441591143608093f, 1.047797679901123f,
    ))
    private val workingToProPhoto = forceRows(floatArrayOf(
        .8883839249610901f, -.003786009969189763f, .11540208011865616f,
        .05755028873682022f, .9260923266410828f, .016357380896806717f,
        -.0024387999437749386f, .04811844974756241f, .9543203711509705f,
    ))

    fun sensorToWorking(): FloatArray = proPhotoToWorking.copyOf()
    /** RAW HSV bake +0x15c8; distinct from Original's +0x1610 space matrix. */
    fun filmExtraMatrix(): FloatArray = FloatArray(9).also {
        NativeMatrixQuantization.forceRowSumsToOne(RawRenderingParameters.feature5Matrix(false), it)
    }
    fun workingToProPhotoMatrix(): FloatArray = workingToProPhoto.copyOf()
    fun originalExtraMatrix(): FloatArray = proPhotoToWorking.copyOf()

    private val original by lazy {
        val identity = floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f)
        val matrixStage = DngColorMatrixStage(identity, 32767f, 0f)
        val grid = FloatArray(65 * 65 * 65 * 4)
        val pixel = FloatArray(3)
        fun linear(index: Int): Float {
            val x = index.coerceAtMost(63) / 63f
            return x / (11f - 10f * x)
        }
        for (b in 0..64) for (g in 0..64) for (r in 0..64) {
            matrixStage.renderNormalized(linear(r), linear(g), linear(b), pixel, 0)
            val offset = ((b * 65 + g) * 65 + r) * 4
            for (c in 0..2) grid[offset + c] = pixel[c].pow(1f / 2.2f) * 32767f
        }
        val lut = DngRenderingLutStage(grid)
        val extra = originalExtraMatrix()
        val renderer: (ShortArray, ShortArray) -> Unit = { source, destination ->
            matrixStage.renderQ15(source, destination)
            for (i in destination.indices step 3) {
                val r = destination[i].toFloat()
                val g = destination[i + 1].toFloat()
                val b = destination[i + 2].toFloat()
                for (c in 0..2) {
                    val value = ((r * extra[c * 3] + 0f) + g * extra[c * 3 + 1]) + b * extra[c * 3 + 2]
                    destination[i + c] = Math.rint(value.coerceIn(0f, 32767f).toDouble()).toInt().toShort()
                }
            }
            lut.renderQ15(destination, destination)
        }
        renderer
    }

    /** Neutral DNG import rendering in the fixed output working space.
     * Grid values are computed from the rendering mathematics, never image data.
     */
    fun originalRenderer(): (ShortArray, ShortArray) -> Unit = original

    private fun forceRows(matrix: FloatArray): FloatArray = matrix.copyOf().also { result ->
        for (i in 0..6 step 3) {
            val scale = 65536f / ((matrix[i] + matrix[i + 1]) + matrix[i + 2])
            result[i] = Math.rint((matrix[i] * scale).toDouble()).toFloat() / 65536f
            result[i + 1] = Math.rint((matrix[i + 1] * scale).toDouble()).toFloat() / 65536f
            result[i + 2] = (1f - result[i]) - result[i + 1]
        }
    }
}
