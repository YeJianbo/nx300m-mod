package dev.cxo.filmpack.core

/** Bounded MT19937 seed primitive recovered from the native raw helper. */
object NativeGrain8Seed {
    data class OffsetXY(val x: Int, val y: Int)

    /** Seed zero is the caller's explicit no-randomization path. */
    fun offsets(seed: Long, width: Int, height: Int): OffsetXY {
        require(seed in 0L..0xFFFFFFFFL) { "seed must be an unsigned 32-bit value" }
        require(width > 0 && height > 0) { "dimensions must be positive" }
        if (seed == 0L) return OffsetXY(0, 0)
        val sampler = BoundedSampler(seed)
        return OffsetXY(sampler.nextInt(0, width - 1), sampler.nextInt(0, height - 1))
    }

    /** Public for deterministic JVM/instrumentation verification of the raw leaf. */
    class BoundedSampler(seed: Long) {
        private val state = LongArray(624)
        private var index = 0

        init {
            require(seed in 0L..0xFFFFFFFFL) { "seed must be an unsigned 32-bit value" }
            state[0] = seed
            for (i in 1 until state.size) {
                state[i] = (i.toLong() +
                    (state[i - 1] xor (state[i - 1] ushr 30)) * 1812433253L) and MASK
            }
        }

        fun nextInt(lower: Int, upper: Int): Int {
            require(lower >= 0 && upper >= lower) { "bounds must be non-negative and ordered" }
            val difference = upper.toLong() - lower.toLong()
            if (difference == 0L) return lower
            val span = difference + 1L
            val leadingZeroes = Integer.numberOfLeadingZeros(span.toInt())
            val width = if (((span shl leadingZeroes) and 0x7FFFFFFFL) != 0L) 32 else 31
            val bits = width - leadingZeroes
            var groups = bits ushr 5
            if ((bits and 31) != 0) groups++
            val quotient = if (groups <= bits) bits / groups else 0
            val mask = if (groups <= bits) MASK ushr ((-quotient) and 31) else 0L
            while (true) {
                val candidate = nextRaw() and mask
                if (candidate < span) return (lower.toLong() + candidate).toInt()
            }
        }

        private fun nextRaw(): Long {
            val current = index
            val next = (current + 1) % state.size
            val value = state[next]
            val twist = if ((value and 1L) != 0L) 0x9908B0DFL else 0L
            val mixed = twist xor state[(current + 0x18D) % state.size] xor
                (((value and 0x7FFFFFFEL) or (state[current] and 0x80000000L)) ushr 1)
            state[current] = mixed and MASK
            index = next
            var result = state[current]
            result = result xor (result ushr 11)
            result = result xor ((result and 0x013A58ADL) shl 7)
            result = result xor ((result and 0x0001DF8CL) shl 15)
            return (result xor (result ushr 18)) and MASK
        }

        private companion object { const val MASK = 0xFFFFFFFFL }
    }
}
