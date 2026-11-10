package dev.cxo.filmpack.app

import android.app.ActivityManager
import android.content.Context
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.os.StatFs
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.UUID

/** One worker-owned, physically oriented sRGB JPEG bridge for platform HEIF decoding.
 * Original bytes are never modified. HDR/gain maps and >8-bit samples are not retained.
 */
internal class HeifJpegStore(private val context: Context) : AutoCloseable {
    private val directory = File(context.cacheDir, "heif-jpeg/${UUID.randomUUID()}")
    init {
        val root = requireNotNull(directory.parentFile)
        if (CLEANED.compareAndSet(false, true)) root.listFiles()?.forEach { it.deleteRecursively() }
        check(directory.mkdirs()) { "无法创建HEIC缓存目录" }
    }
    private var sourceKey: String? = null
    private var cached: File? = null

    /** Inspect the actual ftyp box; names/MIME must never route a renamed RAW around admission. */
    internal fun isHeif(uri: Uri): Boolean {
        val header = ByteArray(4096)
        val count = context.contentResolver.openInputStream(uri)?.use { input ->
            var read = 0
            while (read < header.size) {
                val amount = input.read(header, read, header.size - read)
                if (amount < 0) break
                if (amount == 0) {
                    val one = input.read()
                    if (one < 0) break
                    header[read++] = one.toByte()
                } else read += amount
            }
            read
        } ?: throw IOException("无法读取照片")
        if (count < 16 || String(header, 4, 4, Charsets.US_ASCII) != "ftyp") return false
        val boxSize = ByteBuffer.wrap(header, 0, 4).order(ByteOrder.BIG_ENDIAN).int.toLong() and 0xffffffffL
        if (boxSize !in 16L..4096L || boxSize > count || (boxSize - 16) % 4 != 0L) return false
        val brands = mutableSetOf(String(header, 8, 4, Charsets.US_ASCII))
        var offset = 16
        while (offset + 4 <= boxSize) {
            brands.add(String(header, offset, 4, Charsets.US_ASCII)); offset += 4
        }
        return brands.none { it == "avif" || it == "avis" } &&
            brands.any { it in HEIF_BRANDS }
    }

    @Synchronized fun normalize(uri: Uri, cancelled: () -> Boolean): Uri? {
        checkCancelled(cancelled)
        if (uri == cached?.let(Uri::fromFile)) return null
        if (sourceKey == uri.toString() && cached?.isFile == true) return Uri.fromFile(cached)
        if (!isHeif(uri)) return null
        if (Build.VERSION.SDK_INT < 28) throw IOException("HEIC处理需要Android 9或以上及系统HEIC解码器")
        invalidate()
        checkCancelled(cancelled)
        val bitmap = try {
            ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, uri)) { decoder, info, _ ->
                checkCancelled(cancelled)
                val pixels = info.size.width.toLong() * info.size.height
                require(pixels in 1..80_000_000L && info.size.width <= 65500 && info.size.height <= 65500) {
                    "HEIC照片超过8000万像素或尺寸限制"
                }
                val memory = ActivityManager.MemoryInfo()
                (context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(memory)
                require(!memory.lowMemory && memory.availMem >= pixels * 12 + 64L * 1024 * 1024) {
                    "可用内存不足，无法按原尺寸转换HEIC，请关闭其它应用后重试"
                }
                require(StatFs(context.cacheDir.path).availableBytes >= pixels * 4 + 32L * 1024 * 1024) {
                    "缓存空间不足，无法转换HEIC"
                }
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
            }
        } catch (failure: OutOfMemoryError) {
            throw IOException("HEIC解码内存不足，请关闭其它应用后重试", failure)
        } catch (failure: IOException) {
            throw IOException("系统无法按原尺寸解码这张HEIC，请先转换为JPEG或PNG后导入", failure)
        }
        try {
            checkCancelled(cancelled)
            val file = File.createTempFile("cxo-heif-", ".jpg", directory)
            try {
                file.outputStream().buffered().use { output ->
                    ExportImageWriter.write(output, bitmap.width, bitmap.height,
                        settings = ExportSettings(jpegQuality = 100), cancelled = cancelled) { top, count, argb ->
                        bitmap.getPixels(argb, 0, bitmap.width, 0, top, bitmap.width, count)
                    }
                }
                checkCancelled(cancelled)
                cached = file; sourceKey = uri.toString()
                return Uri.fromFile(file)
            } catch (failure: Throwable) { file.delete(); throw failure }
        } finally { bitmap.recycle() }
    }

    /** Called for a fresh picker import even if its provider reuses the same URI. */
    @Synchronized fun invalidate() { cached?.delete(); cached = null; sourceKey = null }
    private fun checkCancelled(cancelled: () -> Boolean) {
        if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("已取消")
    }
    override fun close() { invalidate(); directory.delete() }

    companion object {
        private val CLEANED = AtomicBoolean()
        private val HEIF_BRANDS = setOf("heic", "heix", "hevc", "hevx", "heim", "heis", "hevm", "hevs", "mif1", "msf1")
    }
}
