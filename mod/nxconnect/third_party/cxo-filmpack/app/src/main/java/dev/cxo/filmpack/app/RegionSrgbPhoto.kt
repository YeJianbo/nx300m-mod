package dev.cxo.filmpack.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapRegionDecoder
import android.graphics.ColorSpace
import android.graphics.Rect
import android.media.ExifInterface
import android.net.Uri
import java.io.Closeable
import java.io.IOException
import java.io.InputStream

/** Sequential raw-layout sRGB rows with at most two megapixels of decoded source retained.
 * JPEG/PNG only: other containers retain the managed bounded-bitmap fallback.
 * JPEG EXIF orientation is retained for the encoder to write once in its output.
 */
@Suppress("DEPRECATION")
internal class RegionSrgbPhoto private constructor(
    private val input: InputStream,
    private val decoder: BitmapRegionDecoder,
    val orientation: Int,
) : Closeable {
    val width: Int = decoder.width
    val height: Int = decoder.height
    val displayWidth: Int get() = if (orientation >= 5) height else width
    val displayHeight: Int get() = if (orientation >= 5) width else height
    private val stripHeight = minOf(256,maxOf(16,(2_097_152/width/16)*16))
    private var strip: Bitmap? = null
    private var stripTop = -1
    private val options = BitmapFactory.Options().apply {
        inPreferredConfig = Bitmap.Config.ARGB_8888
        inPreferredColorSpace = ColorSpace.get(ColorSpace.Named.SRGB)
    }

    fun readRows(top: Int, count: Int, argb: IntArray) {
        require(top >= 0 && count > 0 && top+count <= height && argb.size >= width*count)
        var row = top
        while (row < top+count) {
            var current = strip
            if (current == null || row !in stripTop until stripTop+current.height) {
                current?.recycle(); strip = null
                stripTop = row/stripHeight*stripHeight
                current = decoder.decodeRegion(Rect(0,stripTop,width,minOf(height,stripTop+stripHeight)),options)
                    ?: throw IOException("无法读取照片区域")
                strip = current
                require(current.width == width && current.height > 0) { "照片区域尺寸错误" }
            }
            val rows = minOf(top+count-row,stripTop+current.height-row)
            current.getPixels(argb,(row-top)*width,width,0,row-stripTop,width,rows)
            row += rows
        }
    }

    override fun close() {
        strip?.recycle(); strip = null
        decoder.recycle()
        input.close()
    }

    companion object {
        fun open(context: Context, uri: Uri): RegionSrgbPhoto? {
            val resolver = context.contentResolver
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it,null,bounds) }
            if (bounds.outMimeType !in listOf("image/jpeg","image/png") ||
                bounds.outWidth !in 1..65535 || bounds.outHeight !in 1..65535) return null
            // PNG normally has no orientation; use the platform parser for
            // supported EXIF-bearing sources without making the decoder rotate.
            val orientation = try {
                resolver.openInputStream(uri)?.use {
                    ExifInterface(it).getAttributeInt(ExifInterface.TAG_ORIENTATION,1).takeIf { value -> value in 1..8 }
                } ?: 1
            } catch (_: IOException) { 1 } catch (_: IllegalArgumentException) { 1 }
            val input = resolver.openInputStream(uri) ?: throw IOException("无法打开照片")
            try {
                val decoder = BitmapRegionDecoder.newInstance(input,false) ?: run { input.close(); return null }
                return RegionSrgbPhoto(input,decoder,orientation)
            } catch (_: IOException) {
                input.close(); return null
            } catch (_: IllegalArgumentException) {
                input.close(); return null
            }
        }
    }
}
