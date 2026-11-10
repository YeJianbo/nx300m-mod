package dev.cxo.filmpack.app

import dev.cxo.filmpack.core.FixedSrgbEntryLut
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Optional bulk sampling0 entry kernel. All other film stages remain in Kotlin.
 *
 * The private direct buffer owns an immutable copy of the validated grid. JNI
 * receives the buffer itself on every synchronous call, so its local JNI
 * reference keeps the allocation alive even while parallel tiles are running.
 * There is no native handle to free and no close/render race.
 */
class NativeEntryTransform private constructor(private val grid: ByteBuffer) {
    fun render(source: ShortArray, destination: FloatArray, width: Int, xOffset: Int = 0) {
        require(source.size % 3 == 0 && source.size == destination.size)
        require(width > 0 && xOffset >= 0)
        NativeEntryBridge.render(grid, source, destination, width, xOffset)
    }

    companion object {
        /** Tests the ABI of the loaded code, rather than the device's ABI list. */
        val isSupported: Boolean get() = NativeEntryBridge.supported

        /** Returns null when explicitly disabled or the optional ARM64 library is unavailable. */
        fun create(grid: FloatArray, enabled: Boolean = true): NativeEntryTransform? {
            if (!enabled || !isSupported) return null
            require(grid.size == FixedSrgbEntryLut.GRID_FLOATS) { "Expected 65³ float4 entry LUT" }
            for (i in grid.indices) {
                require(grid[i].isFinite() && grid[i] in 0f..32767f) { "Unbounded entry node" }
                if (i % 4 == 3) require(grid[i] == 0f) { "Nonzero entry padding" }
            }
            val bytes = ByteBuffer.allocateDirect(grid.size * Float.SIZE_BYTES).order(ByteOrder.nativeOrder())
            bytes.asFloatBuffer().put(grid)
            return NativeEntryTransform(bytes.asReadOnlyBuffer().order(ByteOrder.nativeOrder()))
        }
    }
}

private object NativeEntryBridge {
    val supported: Boolean = try {
        System.loadLibrary("cxo_entry")
        isAArch64()
    } catch (_: LinkageError) {
        false
    } catch (_: SecurityException) {
        false
    }

    private external fun isAArch64(): Boolean
    external fun render(grid: ByteBuffer, source: ShortArray, destination: FloatArray, width: Int, xOffset: Int)
}
