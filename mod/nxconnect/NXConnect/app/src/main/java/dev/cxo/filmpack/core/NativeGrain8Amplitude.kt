package dev.cxo.filmpack.core

/** Builds the captured native 256-entry amplitude table from a byte table. */
object NativeGrain8Amplitude {
    private const val ENTRY_COUNT = 256
    private const val MAX_COEFFICIENT = 60
    private const val MAX_INTENSITY = 200

    fun build(table: ByteArray, coefficient: Int, intensity: Int): IntArray {
        require(table.size == ENTRY_COUNT) { "Expected 256 amplitude bytes" }
        require(coefficient in 1..MAX_COEFFICIENT) { "Coefficient must be 1..60" }
        require(intensity in 0..MAX_INTENSITY) { "Intensity must be 0..200" }
        if (intensity == 0) return IntArray(ENTRY_COUNT)

        val normalized = intensity.toFloat() * 0.01f
        val coefficientFloat = coefficient.toFloat()
        return IntArray(ENTRY_COUNT) { index ->
            val scaledByte = table[index].toInt() and 0xff
            val normalizedByte = scaledByte.toFloat() * normalized
            val coefficientProduct = normalizedByte * coefficientFloat
            (coefficientProduct + 0.5f).toInt()
        }.also { it[0] = 0 } // Engine 0x5de048 clears the first amplitude after construction.
    }
}
