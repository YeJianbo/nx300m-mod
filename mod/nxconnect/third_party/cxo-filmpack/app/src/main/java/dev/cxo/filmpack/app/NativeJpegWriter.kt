package dev.cxo.filmpack.app

import dev.cxo.filmpack.core.StreamingJpegWriter
import java.io.OutputStream
import java.util.function.Consumer

/** Optional streaming JPEG codec. Colour, alpha compositing and orientation of
 * the supplied pixels remain the caller's responsibility, just as in the core
 * writer. EXIF orientation is metadata; this codec never rotates pixels.
 */
internal object NativeJpegWriter {
    val isSupported: Boolean get() = NativeJpegBridge.supported

    fun write(
        output: OutputStream,
        width: Int,
        height: Int,
        quality: Int = 98,
        exifOrientation: Int = 1,
        nativeEnabled: Boolean = true,
        readRows: (top: Int, count: Int, argb: IntArray) -> Unit,
    ) {
        validate(width, height, quality, exifOrientation)
        // Select before touching the stream. Never retry an interrupted or
        // partially written native export using another encoder.
        if (nativeEnabled && width <= 65500 && height <= 65500 && isSupported) {
            writeNative(output, width, height, quality, exifOrientation, readRows)
        } else {
            StreamingJpegWriter.write(output, width, height, quality, exifOrientation, readRows)
        }
    }

    /** Native-only entry for differential tests; unavailable codecs fail before I/O. */
    fun writeNative(
        output: OutputStream,
        width: Int,
        height: Int,
        quality: Int = 98,
        exifOrientation: Int = 1,
        readRows: (top: Int, count: Int, argb: IntArray) -> Unit,
    ) {
        validate(width, height, quality, exifOrientation)
        require(width <= 65500 && height <= 65500) { "Native JPEG dimensions must be 1..65500" }
        check(isSupported) { "Native JPEG encoder is unavailable in this process" }
        var nextTop = 0
        // Consumer.accept is a platform interface, so callback lookup does not
        // depend on an application method name surviving code shrinking.
        val rows = Consumer<IntArray> { argb ->
            check(nextTop < height && argb.size == width * 8)
            val count = minOf(8, height - nextTop)
            readRows(nextTop, count, argb)
            nextTop += count
        }
        NativeJpegBridge.encode(output, width, height, quality, exifOrientation, rows)
        check(nextTop == height) { "Native JPEG encoder did not consume all rows" }
        // The caller owns the stream. Flush only after a complete native image;
        // callback, I/O and cancellation exceptions retain their original identity.
        output.flush()
    }

    private fun validate(width: Int, height: Int, quality: Int, orientation: Int) {
        require(width in 1..65535 && height in 1..65535) { "JPEG dimensions must be 1..65535" }
        require(quality in 1..100) { "JPEG quality must be 1..100" }
        require(orientation in 1..8) { "EXIF orientation must be 1..8" }
    }
}

private object NativeJpegBridge {
    val supported: Boolean = try {
        System.loadLibrary("cxo_jpeg")
        isAArch64()
    } catch (_: LinkageError) {
        false
    } catch (_: SecurityException) {
        false
    }

    private external fun isAArch64(): Boolean
    external fun encode(output: OutputStream, width: Int, height: Int, quality: Int,
        orientation: Int, rows: Consumer<IntArray>)
}
