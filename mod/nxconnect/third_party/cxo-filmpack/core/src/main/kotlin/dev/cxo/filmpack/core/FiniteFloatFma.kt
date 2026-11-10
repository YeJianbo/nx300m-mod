package dev.cxo.filmpack.core

/**
 * Correctly rounded binary32 a*b+c for finite operands, without Android 33's
 * Math.fma. A Float product is exact in Double. TwoSum recovers the addition
 * residual only when Double-to-Float conversion lands on a rounding midpoint.
 * NaN/infinity operands are deliberately outside this helper's contract.
 */
object FiniteFloatFma {
    fun fma(a: Float, b: Float, c: Float): Float {
        require(a.isFinite() && b.isFinite() && c.isFinite()) { "FMA operands must be finite" }
        return unchecked(a, b, c)
    }

    /** Hot-path entry for callers that validate their data before evaluation. */
    internal fun unchecked(a: Float, b: Float, c: Float): Float {
        val product = a.toDouble() * b.toDouble()
        val addend = c.toDouble()
        val sum = product + addend
        val rounded = sum.toFloat()
        // Binary64 has 29 more significand bits than binary32. In the
        // normal Float range a Double can be a Float rounding midpoint only
        // when those low bits are exactly 100...0. All other sums cannot
        // suffer double rounding, so skip adjacent-float/residual work.
        // Subnormals retain the general path; the overflow midpoint also
        // matches this mask and therefore retains the existing handling.
        val magnitudeBits = sum.toRawBits() and Long.MAX_VALUE
        if (magnitudeBits >= 0x3810000000000000L &&
            (magnitudeBits and 0x1fffffffL) != 0x10000000L) return rounded
        if (sum == rounded.toDouble()) return rounded

        // Float overflow's midpoint is finite and exactly representable in Double.
        if (rounded.isInfinite()) {
            val threshold = if (sum > 0.0) 3.4028235677973366e38 else -3.4028235677973366e38
            if (sum != threshold) return rounded
            val residual = residual(product, addend, sum)
            return if ((sum > 0.0 && residual < 0.0) || (sum < 0.0 && residual > 0.0)) {
                if (sum > 0.0) Float.MAX_VALUE else -Float.MAX_VALUE
            } else rounded
        }

        val adjacent = if (sum > rounded.toDouble()) Math.nextUp(rounded) else Math.nextDown(rounded)
        if (adjacent.isInfinite()) return rounded
        val midpoint = (rounded.toDouble() + adjacent.toDouble()) * 0.5
        if (sum != midpoint) return rounded
        val residual = residual(product, addend, sum)
        return if ((adjacent > rounded && residual > 0.0) || (adjacent < rounded && residual < 0.0)) adjacent else rounded
    }

    private fun residual(a: Double, b: Double, sum: Double): Double {
        val bVirtual = sum - a
        val aVirtual = sum - bVirtual
        return (a - aVirtual) + (b - bVirtual)
    }
}
