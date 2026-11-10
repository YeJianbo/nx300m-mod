package dev.cxo.filmpack.core

/**
 * Pure Kotlin evaluator of an LCMS 2.17 optimized RGB16 CLUT.
 *
 * The resource is compiled offline from ICC profiles by the retained, unmodified
 * LittleCMS 2.17 source. It contains no observed DxO pixels. This reader accepts
 * a single integer CLUT stage; other pipeline shapes fail explicitly.
 *
 * Integer arithmetic follows cmsintrp.c TetrahedralInterp16 and
 * lcms2_internal.h _cmsToFixedDomain, including signed Int wrap and shifts.
 * LittleCMS is Copyright (c) 1998-2024 Marti Maria Saguer, MIT licensed;
 * see THIRD_PARTY_NOTICES/LittleCMS-2.17.txt.
 */
class LcmsOptimizedQ15Transform private constructor(
    private val grid: IntArray,
    private val table: IntArray,
) {
    /** Signed Q15 input uses the independently recovered DxO-to-LCMS packing. */
    fun transform(input: ShortArray, output: ShortArray = ShortArray(3)): ShortArray {
        require(input.size == 3 && output.size >= 3) { "Expected RGB triplet" }
        val encoded = IntArray(3) { c ->
            val positive = input[c].toInt().coerceAtLeast(0)
            2 * positive + (positive shr 14)
        }
        val codes = transformUnsigned16(encoded)
        for (c in 0..2) output[c] = (codes[c] ushr 1).toShort()
        return output
    }

    fun convert(rgb: FloatArray): FloatArray = ColorManagementQ15.convert(rgb) { transform(it) }

    /** LCMS RGB16 integer domain; exposed for independent interpolation checks. */
    fun transformUnsigned16(input: IntArray, output: IntArray = IntArray(3)): IntArray {
        require(input.size == 3 && output.size >= 3 && input.all { it in 0..65535 })
        evaluateUnsigned16(input, output, IntArray(3), IntArray(3), IntArray(3))
        return output
    }

    /** Packed normalized working-Q15 input to normalized RGB16 output.
     * Scratch is reused for the tile; no per-pixel array allocations.
     * This leaves output in the transform's destination ICC domain. */
    fun renderNormalizedQ15ToRgb16(source: FloatArray, destination: FloatArray) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(source.all { it.isFinite() && it in 0f..1f }) { "Expected normalized working RGB" }
        val encoded = IntArray(3); val codes = IntArray(3)
        val fractions = IntArray(3); val steps = IntArray(3); val order = IntArray(3)
        for (offset in source.indices step 3) {
            for (c in 0..2) {
                val q = Math.rint((source[offset+c]*32767f).toDouble()).toInt()
                encoded[c] = 2*q + (q shr 14)
            }
            evaluateUnsigned16(encoded, codes, fractions, steps, order)
            for (c in 0..2) destination[offset+c] = codes[c].toFloat()/65535f
        }
    }

    private val stride = intArrayOf(grid[1] * grid[2] * 3, grid[2] * 3, 3)

    private fun evaluateUnsigned16(input: IntArray, output: IntArray, fractions: IntArray, steps: IntArray, order: IntArray) {
        var base = 0
        for (c in 0..2) {
            val product = input[c] * (grid[c] - 1)
            val fixed = product + (product + 0x7fff) / 0xffff
            base += (fixed shr 16) * stride[c]
            fractions[c] = fixed and 0xffff
            steps[c] = if (input[c] == 0xffff) 0 else stride[c]
        }
        // Exactly the six tetrahedra from LCMS; equal fractions share a face.
        val x = fractions[0]; val y = fractions[1]; val z = fractions[2]
        val first: Int; val second: Int; val third: Int
        if (x >= y) {
            if (y >= z) { first=0; second=1; third=2 }
            else if (z >= x) { first=2; second=0; third=1 }
            else { first=0; second=2; third=1 }
        } else {
            if (x >= z) { first=1; second=0; third=2 }
            else if (y >= z) { first=1; second=2; third=0 }
            else { first=2; second=1; third=0 }
        }
        order[0]=first; order[1]=second; order[2]=third
        for (channel in 0..2) {
            val initial = table[base + channel]
            var previous = initial
            var index = base + channel
            var rest = 0
            for (axis in order) {
                index += steps[axis]
                val next = table[index]
                rest += (next - previous) * fractions[axis]
                previous = next
            }
            rest += 0x8001
            output[channel] = (initial + ((rest + (rest shr 16)) shr 16)) and 0xffff
        }
    }

    companion object {
        /** Read CXOLCMS1 compiled pipeline. Version and shape are strict. */
        fun read(bytes: ByteArray): LcmsOptimizedQ15Transform {
            var offset = 0
            fun u32(): Int {
                require(offset + 4 <= bytes.size) { "Truncated LCMS resource" }
                var value = 0L
                repeat(4) { i -> value = value or ((bytes[offset++].toLong() and 255) shl (i * 8)) }
                require(value <= Int.MAX_VALUE) { "Unsupported LCMS field" }
                return value.toInt()
            }
            require(bytes.size >= 16 && String(bytes, 0, 8, Charsets.US_ASCII) == "CXOLCMS1")
            offset = 8
            require(u32() == 2170) { "Expected LittleCMS 2.17 resource" }
            require(u32() == 1 && u32() == 0x636c7574) { "Expected one optimized CLUT stage" }
            require(u32() == 3 && u32() == 3) { "Expected RGB to RGB CLUT" }
            val grid = IntArray(3) { u32().also { require(it in 2..255) } }
            val entries = u32()
            require(entries.toLong() == grid.fold(3L) { product, n -> product * n }) { "Invalid CLUT dimensions" }
            require(offset.toLong() + entries * 2L == bytes.size.toLong()) { "Invalid CLUT payload size" }
            val table = IntArray(entries) {
                val lo = bytes[offset++].toInt() and 255
                lo or ((bytes[offset++].toInt() and 255) shl 8)
            }
            return LcmsOptimizedQ15Transform(grid, table)
        }
    }
}
