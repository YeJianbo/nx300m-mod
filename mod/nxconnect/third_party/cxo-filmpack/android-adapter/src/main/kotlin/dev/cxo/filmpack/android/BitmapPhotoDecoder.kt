package dev.cxo.filmpack.android

import android.annotation.TargetApi
import android.content.ContentResolver
import android.graphics.Bitmap
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import java.nio.ByteBuffer

/**
 * Android's managed photo import boundary for the numeric FilmPack pipeline.
 *
 * API 28+ ImageDecoder performs container decoding and embedded ICC handling;
 * the result is kept as a software RGBA_F16 bitmap. Android may expose the
 * F16 storage as its scRGB-nl color space even after an sRGB target request;
 * the adapter explicitly converts that color to sRGB before quantization.
 * This deliberately leaves EXIF orientation to [ExifOrientation] because
 * orientation is metadata, not a color conversion.
 */
object BitmapPhotoDecoder {
    /** Decode a content URI into a software, sRGB, RGBA_F16 bitmap. */
    @TargetApi(Build.VERSION_CODES.P)
    fun decodeSrgbF16(contentResolver: ContentResolver, uri: Uri): Bitmap {
        require(Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            "ImageDecoder photo import requires API 28 or newer"
        }
        return decodeSource(ImageDecoder.createSource(contentResolver, uri))
    }

    /** Decode encoded bytes, useful for tests and app-owned document readers. */
    @TargetApi(Build.VERSION_CODES.P)
    fun decodeSrgbF16(encoded: ByteArray): Bitmap {
        require(Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            "ImageDecoder photo import requires API 28 or newer"
        }
        return decodeSource(ImageDecoder.createSource(ByteBuffer.wrap(encoded)))
    }

    @TargetApi(Build.VERSION_CODES.P)
    private fun decodeSource(source: ImageDecoder.Source): Bitmap {
        val decoded = ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.memorySizePolicy = ImageDecoder.MEMORY_POLICY_LOW_RAM
            decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
        }
        // ImageDecoder chooses its native config from the source. Copying to
        // RGBA_F16 gives the adapter one stable float boundary even when an
        // ordinary 8-bit container was decoded; embedded wide-gamut/16-bit
        // sources retain their decoded precision through this copy.
        if (decoded.config == Bitmap.Config.RGBA_F16) return decoded
        val converted = decoded.copy(Bitmap.Config.RGBA_F16, true)
        decoded.recycle()
        return converted
    }
}
