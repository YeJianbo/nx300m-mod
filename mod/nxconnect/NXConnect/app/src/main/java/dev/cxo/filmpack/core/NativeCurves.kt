package dev.cxo.filmpack.core

/** Static transcription of recovered int16 LUT helpers; values are Q15 sample units.
 * These helpers do not normalize RGB, run the full film pipeline, or infer its order.
 * Empty/range checks reject unsafe native inputs. Float extension preserves raw bits.
 */
object NativeCurves {
    const val SAMPLE_COUNT = 32768

    /** Signed storage is preserved, including negative and decreasing samples. */
    fun convertLutToFloatAndExtend(values: ShortArray): FloatArray {
        require(values.isNotEmpty()) { "Cannot extend an empty LUT" }
        return FloatArray(values.size + 1) {
            values[minOf(it, values.lastIndex)].toFloat()
        }
    }

    /** Duplicate the last sample; no extrapolation or interpolation. */
    fun extendLut(values: FloatArray): FloatArray {
        require(values.isNotEmpty()) { "Cannot extend an empty LUT" }
        return values.copyOf(values.size + 1).also { it[values.size] = values.last() }
    }

    fun isLutIncreasing(values: ShortArray): Boolean {
        require(values.isNotEmpty()) { "Cannot classify an empty LUT" }
        return (0 until values.lastIndex).all { values[it] <= values[it + 1] }
    }

    /** Nondecreasing, with plateaus allowed; a NaN in an adjacent pair fails. */
    fun isLutIncreasing(values: FloatArray): Boolean {
        require(values.isNotEmpty()) { "Cannot classify an empty LUT" }
        return (0 until values.lastIndex).all { values[it] <= values[it + 1] }
    }

    /** first(second(x)); both arrays must contain valid indices into their equal size. */
    fun composeInt16(first: ShortArray, second: ShortArray): ShortArray {
        require(first.size == second.size) { "LUT sizes differ" }
        val size = first.size
        require(first.all { it.toInt() in 0 until size } &&
            second.all { it.toInt() in 0 until size }) { "LUT sample is not a valid index" }
        return ShortArray(size) { first[second[it].toInt()] }
    }

    /** Recovered int16 InverseLutAndExtend, including its duplicated three-sample tail.
     * Accepts 32768 samples or a 32769-element extended table. The guard participates
     * in validation, but only the first 32768 samples construct the inverse.
     * Nondecreasing and nonnegative samples are required; do not sort a film curve.
     * Every arithmetic intermediate below is Float, matching the static reference.
     */
    fun inverseInt16LutAndExtend(values: ShortArray): FloatArray {
        require(values.size == SAMPLE_COUNT || values.size == SAMPLE_COUNT + 1) {
            "Inverse requires 32768 or 32769 samples"
        }
        require(isLutIncreasing(values)) { "Inverse requires a nondecreasing LUT including guard" }
        require(values.all { it >= 0 }) { "Inverse requires Q15 values in 0..32767" }
        val out = FloatArray(SAMPLE_COUNT + 1)
        for (i in 0 until SAMPLE_COUNT - 1) {
            val low = values[i].toInt()
            val high = values[i + 1].toInt()
            if (high > low) {
                val scale = 1f / (high - low).toFloat()
                for (y in low..high) {
                    val fraction = (y - low).toFloat() * scale
                    out[y] = fraction + i.toFloat()
                }
            }
        }
        for (y in values[SAMPLE_COUNT - 1].toInt() until SAMPLE_COUNT) out[y] = 32767f
        out[SAMPLE_COUNT - 1] = out[SAMPLE_COUNT - 2]
        out[SAMPLE_COUNT] = out[SAMPLE_COUNT - 2]
        return out
    }
}
