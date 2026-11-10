package dev.cxo.filmpack.core

import kotlin.math.max
import kotlin.math.sqrt

/** Native RAW no-film output tone (NEON 0x13234c default tail).
 * Linear working Q15 -> encoded working Q15; output ICC conversion is still required.
 * Same rational mathematics as JPEGTC=1 forward, without JPEG's input inverse.
 */
object RawOriginalTone {
    fun renderQ15(source: ShortArray, destination: ShortArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it >= 0 })
        val x = FloatArray(3)
        for (i in source.indices step 3) {
            repeat(3) { x[it] = source[i + it].toFloat() * (1f / 32767f) }
            var lo = 0; var mid = 1; var hi = 2
            if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
            if (x[mid] > x[hi]) { val t = mid; mid = hi; hi = t }
            if (x[lo] > x[mid]) { val t = lo; lo = mid; mid = t }
            val first = x[lo]; val last = x[hi]
            val a = 11f * first / (1f + 10f * first)
            val b = 11f * last / (1f + 10f * last)
            val t = (sqrt(x[mid]) - sqrt(first)) / max(sqrt(last) - sqrt(first), 1.1920928955078125e-7f)
            val middle = a + (b - a) * t
            destination[i + lo] = encode(a)
            destination[i + mid] = encode(middle)
            destination[i + hi] = encode(b)
        }
    }

    private fun encode(x: Float): Short = Math.rint((x * 32767f).toDouble()).toInt().toShort()
}
