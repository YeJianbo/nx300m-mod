package dev.cxo.filmpack.app

import android.app.ActivityManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.net.Uri
import android.provider.OpenableColumns
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.io.Closeable
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.util.concurrent.CancellationException
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean

internal object NativeRawBridge {
    init {
        try {
            System.loadLibrary("cxo_raw")
        } catch (error: UnsatisfiedLinkError) {
            throw ExceptionInInitializerError("RAW native decoder unavailable: ${error.message}")
        }
    }

    external fun decode(inputPath: String, outputPath: String, cancellation: RawCancellationProbe): String

    /** Raw camera-linear RGB16 boundary for the independent DxO RAW color pipeline. */
    external fun decodeCamera(inputPath: String, outputPath: String, cancellation: RawCancellationProbe): String

    /** Apple three-channel Linear DNG, developed with its embedded profile; null for other RAWs. */
    external fun decodeAppleProRaw(inputPath: String, outputPath: String, cancellation: RawCancellationProbe): String?

    /** Metadata-only RAW probe for TIFF streams whose provider lost name/MIME. */
    external fun probeRaw(inputPath: String, cancellation: RawCancellationProbe): Boolean
}

internal class RawCancellationProbe(private val cancelled: () -> Boolean) {
    fun isCancelled(): Boolean = cancelled() || Thread.currentThread().isInterrupted
}

/** Headerless native RGB16LE cache. The source layout is unrotated; export can stream rows. */
internal class RawSrgb16Photo private constructor(
    private val rgbFile: File,
    val width: Int,
    val height: Int,
    val orientation: Int,
    val model: String,
    val rawBps: Int,
    val colorDomain: String,
    internal val metadata: JSONObject,
) : Closeable {
    val displayWidth: Int get() = if (orientation >= 5) height else width
    val displayHeight: Int get() = if (orientation >= 5) width else height

    private val rowBytes = width.toLong() * 6L
    private val file = RandomAccessFile(rgbFile, "r")
    private val bytes = ByteArray(width * 6)
    private val shorts = ShortArray(width * 3)
    private var closed = false

    fun readRows(top: Int, count: Int, dst: ShortArray) {
        check(!closed) { "RAW photo is closed" }
        require(top >= 0 && count > 0 && top.toLong() + count <= height)
        require(dst.size >= width * count * 3)
        file.seek(top.toLong() * rowBytes)
        for (row in 0 until count) {
            file.readFully(bytes)
            var byteIndex = 0
            var shortIndex = row * width * 3
            repeat(width * 3) {
                dst[shortIndex++] = ((bytes[byteIndex].toInt() and 255) or
                    ((bytes[byteIndex + 1].toInt() and 255) shl 8)).toShort()
                byteIndex += 2
            }
        }
    }

    private fun previewInternal(maxEdge: Int, maxPixels: Long, cancelled: () -> Boolean, level: Int?): Bitmap {
        require(colorDomain == "srgb_encoded") { "Linear DNG must pass its RAW rendering pipeline" }
        require(maxEdge > 0 && maxPixels > 0)
        val target = if (level != null) {
            require(level in 0..30 && (width shr level) > 0 && (height shr level) > 0) {
                "Invalid RAW preview level"
            }
            val divisor = 1 shl level
            width / divisor to height / divisor
        } else {
            val edgeScale = minOf(1.0, maxEdge.toDouble() / maxOf(width, height))
            val pixelScale = minOf(1.0, kotlin.math.sqrt(maxPixels.toDouble() / (width.toDouble() * height)))
            val scale = minOf(edgeScale, pixelScale)
            maxOf(1, (width * scale).toInt()) to maxOf(1, (height * scale).toInt())
        }
        val outWidth = target.first.coerceAtLeast(1)
        val outHeight = target.second.coerceAtLeast(1)
        val bitmap = Bitmap.createBitmap(outWidth, outHeight, Bitmap.Config.ARGB_8888)
        val argb = IntArray(outWidth)
        try {
            for (dy in 0 until outHeight) {
                checkCancelled(cancelled)
                val sourceY = (dy.toLong() * height / outHeight).toInt().coerceIn(0, height - 1)
                readRows(sourceY, 1, shorts)
                for (dx in 0 until outWidth) {
                    val sourceX = (dx.toLong() * width / outWidth).toInt().coerceIn(0, width - 1)
                    val index = sourceX * 3
                    val r = (shorts[index].toInt() and 0xffff) ushr 8
                    val g = (shorts[index + 1].toInt() and 0xffff) ushr 8
                    val b = (shorts[index + 2].toInt() and 0xffff) ushr 8
                    argb[dx] = -0x1000000 or (r shl 16) or (g shl 8) or b
                }
                bitmap.setPixels(argb, 0, outWidth, 0, dy, outWidth, 1)
            }
            checkCancelled(cancelled)
            return applyOrientation(bitmap)
        } catch (failure: Throwable) {
            bitmap.recycle()
            throw failure
        }
    }

    fun preview(maxEdge: Int, maxPixels: Long, cancelled: () -> Boolean = { false }, level: Int? = null): Bitmap =
        previewInternal(maxEdge, maxPixels, cancelled, level)

    internal fun applyOrientation(source: Bitmap): Bitmap {
        if (orientation == 1) return source
        val matrix = Matrix()
        when (orientation) {
            2 -> matrix.setScale(-1f, 1f)
            3 -> matrix.setRotate(180f)
            4 -> matrix.setScale(1f, -1f)
            5 -> { matrix.setRotate(90f); matrix.postScale(-1f, 1f) }
            6 -> matrix.setRotate(90f)
            7 -> { matrix.setRotate(-90f); matrix.postScale(-1f, 1f) }
            8 -> matrix.setRotate(-90f)
            else -> return source
        }
        val rotated = Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, false)
        if (rotated !== source) source.recycle()
        return rotated
    }

    override fun close() {
        if (!closed) {
            closed = true
            file.close()
        }
    }

    companion object {
        private const val MAX_RGB_BYTES = 360_000_000L
        private fun checkCancelled(cancelled: () -> Boolean) {
            if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("RAW 操作已取消")
        }

        internal fun open(rgbFile: File, metadata: JSONObject): RawSrgb16Photo {
            val width = metadata.getInt("width")
            val height = metadata.getInt("height")
            val orientation = metadata.optInt("orientation", 1).coerceIn(1, 8)
            val rawBps = metadata.getInt("rawBps")
            require(width in 1..65535 && height in 1..65535 && rawBps in 1..16) { "RAW native metadata unsupported" }
            val expected = width.toLong() * height * 6L
            require(expected <= MAX_RGB_BYTES) { "RAW RGB16 cache exceeds 360MB limit" }
            require(rgbFile.isFile && rgbFile.length() == expected) { "RAW RGB16 cache is incomplete" }
            val domain = metadata.optString("colorDomain", "srgb_encoded")
            require(domain == "srgb_encoded" || domain == "prophoto_linear_q15") { "Unsupported RGB16 cache domain" }
            return RawSrgb16Photo(rgbFile, width, height, orientation, metadata.optString("model"), rawBps, domain, metadata)
        }
    }
}

/** Headerless camera-linear RGB16LE cache. It deliberately has no display preview method:
 * camera-domain values must pass the RAW color pipeline before becoming a Bitmap. */
internal class RawCameraLinear16Photo private constructor(
    private val rgbFile: File,
    val width: Int,
    val height: Int,
    val orientation: Int,
    val model: String,
    val make: String,
    val rawBps: Int,
    val black: Int,
    val maximumAfterProcess: Int,
    val dataMaximumAfterProcess: Int,
    val camMul: FloatArray,
    val preMul: FloatArray,
    /** Identifies this immutable decoded cache, including reimports of the same URI. */
    internal val decodeId: String,
) : Closeable {
    val displayWidth: Int get() = if (orientation >= 5) height else width
    val displayHeight: Int get() = if (orientation >= 5) width else height
    private val rowBytes = width.toLong() * 6L
    private val file = RandomAccessFile(rgbFile, "r")
    private val bytes = ByteArray(width * 6)
    private var closed = false

    fun readRows(top: Int, count: Int, dst: ShortArray) {
        check(!closed) { "RAW camera photo is closed" }
        require(top >= 0 && count > 0 && top.toLong() + count <= height)
        require(dst.size >= width * count * 3)
        file.seek(top.toLong() * rowBytes)
        for (row in 0 until count) {
            file.readFully(bytes)
            var byteIndex = 0
            var shortIndex = row * width * 3
            repeat(width * 3) {
                dst[shortIndex++] = ((bytes[byteIndex].toInt() and 255) or
                    ((bytes[byteIndex + 1].toInt() and 255) shl 8)).toShort()
                byteIndex += 2
            }
        }
    }

    override fun close() {
        if (!closed) { closed = true; file.close() }
    }

    companion object {
        private const val MAX_RGB_BYTES = 360_000_000L

        internal fun open(rgbFile: File, metadata: JSONObject): RawCameraLinear16Photo {
            require(metadata.optString("colorDomain") == "camera_linear_rgb16") {
                "Expected camera-linear RGB16 metadata"
            }
            val width = metadata.getInt("width")
            val height = metadata.getInt("height")
            val expected = width.toLong() * height * 6L
            require(width in 1..65535 && height in 1..65535 && expected <= MAX_RGB_BYTES)
            require(rgbFile.isFile && rgbFile.length() == expected) { "RAW camera RGB16 cache is incomplete" }
            val rawBps = metadata.getInt("rawBps")
            val black = metadata.getInt("black")
            val maximum = metadata.getInt("maximumAfterProcess")
            val dataMaximum = metadata.getInt("dataMaximumAfterProcess")
            require(rawBps in 1..16) { "RAW bit depth unsupported" }
            require(black >= 0 && maximum > 0 && dataMaximum > 0) { "RAW level metadata invalid" }
            fun floats(name: String): FloatArray {
                val a = metadata.getJSONArray(name)
                require(a.length() == 4)
                return FloatArray(4) { a.getDouble(it).toFloat().also { value -> require(value.isFinite()) } }
            }
            return RawCameraLinear16Photo(
                rgbFile, width, height, metadata.optInt("orientation", 1).coerceIn(1, 8),
                metadata.optString("model"), metadata.optString("make"), rawBps, black,
                maximum, dataMaximum,
                floats("camMul"), floats("preMul"),
                metadata.optString("decodeId", rgbFile.absolutePath),
            )
        }
    }
}

internal class RawPhotoStore(context: Context) : Closeable {
    private val context = context.applicationContext
    private val directory = File(this.context.cacheDir, "raw-srgb16/${UUID.randomUUID()}")
    private val inputFile = File(directory, "source.raw")
    private val rgbFile = File(directory, "image.rgb16le")
    private val metadataFile = File(directory, "image.json")
    private var stagedUri: String? = null
    private var currentUri: String? = null
    private var currentMetadata: JSONObject? = null
    private var closed = false

    init {
        val root = directory.parentFile ?: directory
        if (CLEANED.compareAndSet(false, true)) {
            root.listFiles()?.forEach { child -> if (child.isDirectory) child.deleteRecursively() }
        }
        directory.mkdirs()
    }

    @Synchronized
    fun openIfRaw(uri: Uri, cancelled: () -> Boolean = { false }): RawSrgb16Photo? {
        check(!closed) { "RAW photo store is closed" }
        checkCancelled(cancelled)
        val key = uri.toString()
        if (key == currentUri && currentMetadata?.optString("colorDomain") == "srgb_encoded" && rgbFile.isFile) {
            return RawSrgb16Photo.open(rgbFile, currentMetadata!!)
        }
        clearCurrent(deleteFiles = true)
        val probe = RawCancellationProbe(cancelled)
        try {
            if (!stageRawSource(uri, probe)) return null
            val output = NativeRawBridge.decode(inputFile.absolutePath, rgbFile.absolutePath, probe)
            probe.isCancelled().let { if (it) throw CancellationException("RAW 解码已取消") }
            val metadata = JSONObject(output)
            val photo = RawSrgb16Photo.open(rgbFile, metadata)
            metadataFile.writeText(metadata.toString())
            inputFile.delete()
            currentUri = key
            currentMetadata = metadata
            return photo
        } catch (failure: Throwable) {
            clearCurrent(deleteFiles = true)
            inputFile.delete(); rgbFile.delete(); metadataFile.delete()
            throw when (failure) {
                is UnsatisfiedLinkError, is ExceptionInInitializerError -> IllegalStateException("RAW native decoder unavailable", failure)
                else -> failure
            }
        }
    }

    /** Explicit profiled Linear DNG route, separate from camera-calibrated Bayer RAW. */
    @Synchronized
    fun openAppleProRaw(uri: Uri, cancelled: () -> Boolean = { false }): RawSrgb16Photo? {
        check(!closed) { "RAW photo store is closed" }
        checkCancelled(cancelled)
        val key = uri.toString()
        if (key == currentUri && rgbFile.isFile) {
            return if (currentMetadata?.optString("colorDomain") == "prophoto_linear_q15")
                RawSrgb16Photo.open(rgbFile, currentMetadata!!) else null
        }
        val probe = RawCancellationProbe(cancelled)
        try {
            if (stagedUri != key || !inputFile.isFile) {
                clearCurrent(deleteFiles = true)
                if (!stageRawSource(uri, probe)) return null
                stagedUri = key
            }
            val output = NativeRawBridge.decodeAppleProRaw(inputFile.absolutePath, rgbFile.absolutePath, probe)
                ?: return null // Keep the staged source for the calibrated RAW decoder.
            checkCancelled(cancelled)
            val metadata = JSONObject(output).put("decodeId", UUID.randomUUID().toString())
            val photo = RawSrgb16Photo.open(rgbFile, metadata)
            metadataFile.writeText(metadata.toString())
            inputFile.delete(); stagedUri = null
            currentUri = key; currentMetadata = metadata
            Log.i("CXOPerf", "Apple-ProRAW decoded ${photo.width}x${photo.height} embeddedProfile=true")
            return photo
        } catch (failure: Throwable) {
            clearCurrent(deleteFiles = true)
            throw failure
        }
    }

    /** Decode a supported RAW into the explicit camera-linear boundary for the RAW renderer.
     * openIfRaw() is retained for decoder research/tests; product RAW entries do not fall back to it. */
    @Synchronized
    fun openCameraIfRaw(uri: Uri, cancelled: () -> Boolean = { false }): RawCameraLinear16Photo? {
        check(!closed) { "RAW photo store is closed" }
        checkCancelled(cancelled)
        val start = SystemClock.elapsedRealtime()
        val key = uri.toString()
        if (key == currentUri && currentMetadata?.optString("colorDomain") == "camera_linear_rgb16" && rgbFile.isFile) {
            return RawCameraLinear16Photo.open(rgbFile, currentMetadata!!).also {
                Log.i("CXOPerf", "RAW-decode cacheHit=true elapsedMs=${SystemClock.elapsedRealtime() - start}")
            }
        }
        val alreadyStaged = stagedUri == key && inputFile.isFile
        if (!alreadyStaged) clearCurrent(deleteFiles = true)
        val probe = RawCancellationProbe(cancelled)
        try {
            if (!alreadyStaged && !stageRawSource(uri, probe)) return null
            val staged = SystemClock.elapsedRealtime()
            val output = NativeRawBridge.decodeCamera(inputFile.absolutePath, rgbFile.absolutePath, probe)
            probe.isCancelled().let { if (it) throw CancellationException("RAW 解码已取消") }
            val metadata = JSONObject(output).put("decodeId", UUID.randomUUID().toString())
            val photo = RawCameraLinear16Photo.open(rgbFile, metadata)
            metadataFile.writeText(metadata.toString())
            inputFile.delete(); stagedUri = null
            currentUri = key
            currentMetadata = metadata
            Log.i("CXOPerf", "RAW-decode cacheHit=false stageMs=${staged - start} nativeMs=${SystemClock.elapsedRealtime() - staged} elapsedMs=${SystemClock.elapsedRealtime() - start}")
            return photo
        } catch (failure: Throwable) {
            clearCurrent(deleteFiles = true)
            inputFile.delete(); stagedUri = null; rgbFile.delete(); metadataFile.delete()
            throw when (failure) {
                is UnsatisfiedLinkError, is ExceptionInInitializerError -> IllegalStateException("RAW native decoder unavailable", failure)
                else -> failure
            }
        }
    }

    @Synchronized
    private fun copySource(uri: Uri, probe: RawCancellationProbe) {
        val resolver = context.contentResolver
        val input = resolver.openInputStream(uri) ?: throw IOException("无法读取 RAW 文件")
        input.use { source ->
            FileOutputStream(inputFile).use { output ->
                val buffer = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    if (probe.isCancelled()) throw CancellationException("RAW 导入已取消")
                    val count = source.read(buffer)
                    if (count < 0) break
                    total += count
                    if (total > MAX_INPUT_BYTES) throw IOException("RAW 文件超过 256MB 限制")
                    output.write(buffer, 0, count)
                }
                output.fd.sync()
            }
        }
    }

    /** Use provider identity as a hint, never as proof that a TIFF is rendered RGB.
     * Unknown TIFF streams are probed by LibRaw metadata before Android may decode
     * them, so opaque document names cannot silently select an embedded JPEG.
     */
    private fun stageRawSource(uri: Uri, probe: RawCancellationProbe): Boolean {
        val input = context.contentResolver.openInputStream(uri) ?: return false
        val header = ByteArray(12)
        val count = input.use { stream ->
            var offset = 0
            while (offset < header.size) {
                if (probe.isCancelled()) throw CancellationException("RAW 导入已取消")
                val n = stream.read(header, offset, header.size - offset)
                if (n < 0) break
                if (n == 0) {
                    val one = stream.read()
                    if (one < 0) break
                    header[offset++] = one.toByte()
                } else offset += n
            }
            offset
        }
        if (count < 4) return false
        // A rendered JPEG/PNG remains RGB even if its filename has a RAW suffix.
        if ((header[0] == 0xff.toByte() && header[1] == 0xd8.toByte()) ||
            header.copyOfRange(0, 4).contentEquals(byteArrayOf(0x89.toByte(), 0x50, 0x4e, 0x47))) return false
        val prefix = header.copyOfRange(0, 4)
        val rw2 = prefix.contentEquals(byteArrayOf(0x49, 0x49, 0x55, 0))
        val tiff = prefix.contentEquals(byteArrayOf(0x49, 0x49, 0x2a, 0)) ||
            prefix.contentEquals(byteArrayOf(0x4d, 0x4d, 0, 0x2a))
        val declaredRaw = rw2 || hasRawIdentity(uri)
        if (!tiff && !declaredRaw) return false
        if (declaredRaw) checkCapacity()
        copySource(uri, probe)
        if (!declaredRaw && !NativeRawBridge.probeRaw(inputFile.absolutePath, probe)) {
            inputFile.delete()
            return false
        }
        if (!declaredRaw) checkCapacity()
        return true
    }

    private fun hasRawIdentity(uri: Uri): Boolean {
        val resolver = context.contentResolver
        val name = if (uri.scheme == "content") {
            try {
                resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                    val column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    if (column >= 0 && cursor.moveToFirst()) cursor.getString(column) else null
                }
            } catch (_: Exception) { null }
        } else uri.lastPathSegment
        fun rawName(value: String?): Boolean = value != null &&
            RAW_EXTENSIONS.any { value.endsWith(it, ignoreCase = true) }
        if (rawName(name) || rawName(uri.lastPathSegment)) return true
        val mime = try { resolver.getType(uri) } catch (_: Exception) { null }
        return RAW_MIME_TYPES.any { it.equals(mime, ignoreCase = true) }
    }

    private fun checkCapacity() {
        val stat = android.os.StatFs(directory.absolutePath)
        require(stat.availableBytes >= MIN_FREE_BYTES) { "存储空间不足，RAW 解码至少需要约 1GB 可用空间" }
        val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memory = ActivityManager.MemoryInfo()
        manager.getMemoryInfo(memory)
        require(memory.availMem >= MIN_FREE_BYTES) { "可用内存不足，RAW 解码至少需要约 1GB" }
    }

    private fun clearCurrent(deleteFiles: Boolean) {
        stagedUri = null
        currentUri = null
        currentMetadata = null
        if (deleteFiles) {
            inputFile.delete(); rgbFile.delete(); metadataFile.delete()
        }
    }

    /** A new import must reread the provider, even when its URI was reused. */
    @Synchronized
    fun invalidate() {
        check(!closed) { "RAW photo store is closed" }
        clearCurrent(deleteFiles = true)
    }

    private fun checkCancelled(cancelled: () -> Boolean) {
        if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("RAW 操作已取消")
    }

    @Synchronized
    override fun close() {
        if (!closed) {
            closed = true
            clearCurrent(deleteFiles = true)
            directory.delete()
        }
    }

    companion object {
        // Identity hints only. LibRaw validates the full file and the decoder
        // enforces its sensor/size limits; this list is not a camera acceptance list.
        private val RAW_EXTENSIONS = listOf(
            ".rw2", ".arw", ".nef", ".dng", ".nrw", ".cr2", ".cr3", ".crw", ".raf",
            ".orf", ".pef", ".srw", ".sr2", ".srf", ".rwl", ".3fr", ".fff", ".iiq",
            ".mos", ".mrw", ".x3f", ".raw", ".erf", ".kdc", ".dcr",
        )
        private val RAW_MIME_TYPES = listOf(
            "image/x-panasonic-rw2", "image/x-sony-arw", "image/x-nikon-nef", "image/x-nikon-nrw",
            "image/x-adobe-dng", "image/dng", "image/x-canon-cr2", "image/x-canon-cr3",
            "image/x-canon-crw", "image/x-fuji-raf", "image/x-olympus-orf", "image/x-pentax-pef",
            "image/x-samsung-srw", "image/x-raw", "image/x-dcraw",
        )
        private val CLEANED = AtomicBoolean(false)
        private const val MAX_INPUT_BYTES = 256L * 1024L * 1024L
        private const val MIN_FREE_BYTES = 1L * 1024L * 1024L * 1024L
    }
}
