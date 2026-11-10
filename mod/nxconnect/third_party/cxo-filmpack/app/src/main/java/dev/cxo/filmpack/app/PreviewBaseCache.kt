package dev.cxo.filmpack.app

import android.graphics.Bitmap
import java.util.concurrent.CancellationException
import kotlin.math.roundToInt

/** Worker-owned film/grain output, before post editing. Never lend its buffers
 * to views or the destructive post renderer. At most one full and one small
 * base are retained. Changing post parameters does not invalidate either. */
internal class PreviewBaseCache(private val interactiveEdge: Int = 640) : java.io.Closeable {
    private var fullKey: Any? = null
    private var full: Bitmap? = null
    private var smallKey: Any? = null
    private var small: Bitmap? = null

    fun frame(key: Any, source: Bitmap, interactive: Boolean, preserveInputGrid: Boolean,
              cancelled: () -> Boolean, renderBase: (Bitmap) -> Bitmap,
              edit: (Bitmap) -> Bitmap): Bitmap {
        fun check() { if (cancelled()) throw CancellationException() }
        check()
        val base = if (!interactive) {
            if (fullKey != key || full == null) {
                val next = renderBase(source)
                try { check() } catch (failure: Throwable) { next.recycle(); throw failure }
                full?.recycle(); full = next; fullKey = key
                small?.recycle(); small = null; smallKey = null
            }
            requireNotNull(full)
        } else {
            if (smallKey != key || small == null) {
                val complete = full?.takeIf { fullKey == key }
                val next = if (complete != null) shrink(complete) else if (preserveInputGrid) {
                    // Grain has an explicit native coordinate grid. Rendering a
                    // freely resized source would change its size/orientation.
                    val rendered = renderBase(source)
                    try { shrink(rendered) } finally { rendered.recycle() }
                } else {
                    val input = shrink(source)
                    try { renderBase(input) } finally { input.recycle() }
                }
                try { check() } catch (failure: Throwable) { next.recycle(); throw failure }
                small?.recycle(); small = next; smallKey = key
            }
            requireNotNull(small)
        }
        check()
        val owned = requireNotNull(base.copy(Bitmap.Config.ARGB_8888, true))
        // edit takes ownership (and recycles on cancellation/failure).
        return edit(owned)
    }

    private fun shrink(source: Bitmap): Bitmap {
        val ratio = minOf(1f, interactiveEdge.toFloat() / maxOf(source.width, source.height))
        val width = maxOf(1, (source.width * ratio).roundToInt())
        val height = maxOf(1, (source.height * ratio).roundToInt())
        return if (width == source.width && height == source.height)
            requireNotNull(source.copy(Bitmap.Config.ARGB_8888, false))
        else Bitmap.createScaledBitmap(source, width, height, true).apply { density = source.density }
    }

    override fun close() {
        full?.recycle(); small?.recycle()
        full = null; small = null; fullKey = null; smallKey = null
    }
}
