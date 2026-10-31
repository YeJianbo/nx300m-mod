// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.util.LruCache
import androidx.exifinterface.media.ExifInterface
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.data.MediaGroup
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream

/** The same bounded loader serves directory covers, the grid and the viewer. */
object ThumbLoader {
    private val maxMemoryKb = (Runtime.getRuntime().maxMemory() / 1024).toInt()
    private val cacheSizeKb = (maxMemoryKb / 6).coerceIn(48 * 1024, 160 * 1024)
    private val memory = object : LruCache<String, Bitmap>(cacheSizeKb) {
        override fun sizeOf(key: String, value: Bitmap): Int = maxOf(1, value.byteCount / 1024)
    }
    private val gate = Semaphore(4)
    private class PendingImage(val mutex: Mutex = Mutex(), var users: Int = 0)
    private val inflight = mutableMapOf<String, PendingImage>()

    private fun key(group: MediaGroup, width: Int, full: Boolean): String =
        "${NxApp.camera?.baseUrl}|${group.files.joinToString { "${it.relPath}:${it.size}:${it.modified}" }}@$width:$full"

    fun cached(group: MediaGroup, width: Int, full: Boolean = false): Bitmap? = memory.get(key(group, width, full))

    fun peekAny(group: MediaGroup): Bitmap? {
        for (width in intArrayOf(160, 320, 400, 1024)) cached(group, width)?.let { return it }
        return null
    }

    suspend fun load(group: MediaGroup, width: Int = 320, full: Boolean = false): Bitmap? = withContext(Dispatchers.IO) {
        if (group.isVideo) return@withContext null
        val camera = NxApp.camera ?: return@withContext null
        val cacheKey = key(group, width, full)
        memory.get(cacheKey)?.let { return@withContext it }
        val pending = synchronized(inflight) {
            inflight.getOrPut(cacheKey) { PendingImage() }.also { it.users++ }
        }
        try {
            pending.mutex.withLock {
                memory.get(cacheKey)?.let { return@withLock it }
                val bitmap = gate.withPermit {
                    currentCoroutineContext().ensureActive()
                    if (camera.profile.galleryPreview) {
                        for (file in group.previewFiles) {
                            val decoded = attempt {
                                decode(camera.fetchPreview(file.relPath, if (full) "full" else "grid"), width)
                            }
                            if (decoded != null) return@withPermit decoded
                        }
                    } else {
                        // Legacy CGI accepts ordinary images, never RAW originals.
                        val source = group.jpeg ?: group.files.firstOrNull { it.isPlainImage }
                        if (source != null) {
                            val decoded = attempt { decode(camera.fetchThumb(source.relPath, minOf(width, 1024), 82), width) }
                            if (decoded != null) return@withPermit decoded
                        }
                    }
                    val source = group.jpeg ?: group.files.firstOrNull { it.isPlainImage }
                    if (source == null || (!full && source.size > 12L * 1024 * 1024)) return@withPermit null
                    // A failed preview can fall back to an ordinary image. RAW
                    // stays on the download path and is never decoded here.
                    attempt { decode(camera.fetchImageFile(source), width) }
                }
                currentCoroutineContext().ensureActive()
                if (bitmap != null) memory.put(cacheKey, bitmap)
                bitmap
            }
        } finally {
            // A cancelled waiter must not remove the lock still used by its owner
            // or other waiters, otherwise a new caller starts a duplicate download.
            synchronized(inflight) {
                pending.users--
                if (pending.users == 0) inflight.remove(cacheKey)
            }
        }
    }

    private suspend fun attempt(block: suspend () -> Bitmap?): Bitmap? = try {
        block().also { currentCoroutineContext().ensureActive() }
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (_: Exception) {
        null
    }

    private fun decode(bytes: ByteArray, maxEdge: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        // Decode at or above the requested edge; rounding down first can leave a
        // 2048px viewer with only 1368px and permanently soften cached thumbnails.
        val target = maxEdge.coerceAtLeast(1)
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= target) sample *= 2
        val sampled = BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
            BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
        val edge = maxOf(sampled.width, sampled.height)
        val decoded = if (edge > target) {
            val ratio = target.toDouble() / edge
            Bitmap.createScaledBitmap(sampled, maxOf(1, kotlin.math.round(sampled.width * ratio).toInt()),
                maxOf(1, kotlin.math.round(sampled.height * ratio).toInt()), true).also {
                if (it !== sampled) sampled.recycle()
            }
        } else sampled
        val orientation = try {
            ExifInterface(ByteArrayInputStream(bytes)).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
        } catch (_: Exception) { ExifInterface.ORIENTATION_NORMAL }
        // The server preserves or supplies EXIF. Apply it once; HTTP metadata
        // describes the same orientation and must not rotate the image again.
        val matrix = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.postScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.postRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> matrix.postScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> { matrix.postRotate(90f);matrix.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.postRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> { matrix.postRotate(-90f);matrix.postScale(-1f, 1f) }
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.postRotate(270f)
            else -> return decoded
        }
        return Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true).also {
            if (it !== decoded) decoded.recycle()
        }
    }

    fun clear() = memory.evictAll()
}
