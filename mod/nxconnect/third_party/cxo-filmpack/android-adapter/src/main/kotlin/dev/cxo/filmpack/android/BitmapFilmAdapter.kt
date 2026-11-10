package dev.cxo.filmpack.android

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.ColorSpace
import android.os.Build
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import dev.cxo.filmpack.core.FilmLut
import dev.cxo.filmpack.core.FixedSrgbFilmPipeline
import dev.cxo.filmpack.core.FixedSrgbRgbFilmPipeline
import dev.cxo.filmpack.core.Interpolation
import dev.cxo.filmpack.core.OutputSpace
import dev.cxo.filmpack.core.Rgb16TiffWriter
import kotlin.math.pow
import kotlin.math.roundToInt

/** Metadata crossing the Android image boundary. */
data class BitmapFrameMetadata(
    val sourceWidth: Int,
    val sourceHeight: Int,
    val outputWidth: Int,
    val outputHeight: Int,
    val exifOrientation: Int,
    val hasAlpha: Boolean,
    val config: Bitmap.Config?,
    val premultiplied: Boolean,
    /** Source ColorSpace is reported; Bitmap.getPixel returns Android sRGB color ints. */
    val colorSpaceName: String?,
)

/** A tile callback that connects Android pixels to the pure-Kotlin numeric core. */
fun interface RgbTileTransform {
    /** Process the first pixelCount packed, unpremultiplied RGB float triplets. */
    fun apply(source: FloatArray, destination: FloatArray, pixelCount: Int)
}

/**
 * Tile callback for the profile-bound unsigned16 carrier path. ARGB_8888
 * sources use exact 8-bit-to-16-bit expansion; RGBA_F16 sources retain their
 * float components and are converted to Android sRGB before quantization.
 */
fun interface Srgb16TileTransform {
    /** Process packed RGB carrier values and preserve the image-space x origin. */
    fun apply(
        source: ShortArray,
        destination: FloatArray,
        pixelCount: Int,
        originX: Int,
        originY: Int,
        rowWidth: Int,
    )
}

/**
 * Bounded-memory Bitmap adapter for FilmPack's numeric pipeline.
 *
 * The adapter owns Android Bitmap/EXIF/alpha handling. It passes only RGB
 * floats to [RgbTileTransform], so the core remains free of Android and I/O.
 * Android's getPixel converts source colors to sRGB color ints. This is an
 * 8-bit path and does not implement the target application's ICC/CMM pipeline.
 */
class BitmapFilmAdapter(
    tileWidth: Int = DEFAULT_TILE_SIZE,
    tileHeight: Int = DEFAULT_TILE_SIZE,
) {
    private val tileWidth: Int
    private val tileHeight: Int

    init {
        require(tileWidth > 0 && tileHeight > 0) { "Tile dimensions must be positive" }
        require(tileWidth <= MAX_TILE_DIMENSION && tileHeight <= MAX_TILE_DIMENSION) {
            "Tile dimensions exceed the adapter memory limit"
        }
        require(tileWidth.toLong() * tileHeight <= MAX_TILE_PIXELS) {
            "Tile area exceeds the adapter memory limit"
        }
        this.tileWidth = tileWidth
        this.tileHeight = tileHeight
    }

    fun metadata(source: Bitmap, exifOrientation: Int = ExifOrientation.NORMAL): BitmapFrameMetadata {
        require(!source.isRecycled) { "Source Bitmap is recycled" }
        require(source.config != Bitmap.Config.HARDWARE) { "Hardware Bitmaps cannot be read by getPixel" }
        val orientation = ExifOrientation.validate(exifOrientation)
        return BitmapFrameMetadata(
            sourceWidth = source.width,
            sourceHeight = source.height,
            outputWidth = ExifOrientation.outputWidth(source.width, source.height, orientation),
            outputHeight = ExifOrientation.outputHeight(source.width, source.height, orientation),
            exifOrientation = orientation,
            hasAlpha = source.hasAlpha(),
            config = source.config,
            premultiplied = source.isPremultiplied,
            colorSpaceName = source.colorSpace?.name,
        )
    }

    /**
     * Render [source] through bounded RGB tiles. The result is ARGB_8888 and
     * premultiplied, preserving the source alpha byte exactly (including 0).
     * As with Android's premultiplied storage, RGB under zero alpha is not
     * preserved. Source and result Bitmaps remain whole-image allocations;
     * only the adapter's temporary arrays are bounded by the tile limit.
     */
    fun render(
        source: Bitmap,
        exifOrientation: Int = ExifOrientation.NORMAL,
        transform: RgbTileTransform,
    ): Bitmap {
        val metadata = metadata(source, exifOrientation)
        val output = Bitmap.createBitmap(metadata.outputWidth, metadata.outputHeight, Bitmap.Config.ARGB_8888)
        output.setPremultiplied(true)
        val tilePixels = tileWidth * tileHeight
        val sourceRgb = FloatArray(tilePixels * 3)
        val destinationRgb = FloatArray(tilePixels * 3)
        val alpha = IntArray(tilePixels)
        val packed = IntArray(tilePixels)

        var top = 0
        while (top < metadata.outputHeight) {
            val copyHeight = minOf(tileHeight, metadata.outputHeight - top)
            var left = 0
            while (left < metadata.outputWidth) {
                val copyWidth = minOf(tileWidth, metadata.outputWidth - left)
                val count = copyWidth * copyHeight
                var index = 0
                var y = 0
                while (y < copyHeight) {
                    var x = 0
                    while (x < copyWidth) {
                        val outX = left + x
                        val outY = top + y
                        val sourceX = ExifOrientation.sourceX(
                            source.width, source.height, metadata.exifOrientation, outX, outY,
                        )
                        val sourceY = ExifOrientation.sourceY(
                            source.width, source.height, metadata.exifOrientation, outX, outY,
                        )
                        val argb = source.getPixel(sourceX, sourceY)
                        val a = argb ushr 24
                        alpha[index] = a
                        val rgb = index * 3
                        // getPixel returns an unpremultiplied ColorInt after
                        // quantized premultiplied storage, not raw premultiplied RGB.
                        sourceRgb[rgb] = Color.red(argb) / 255f
                        sourceRgb[rgb + 1] = Color.green(argb) / 255f
                        sourceRgb[rgb + 2] = Color.blue(argb) / 255f
                        index++
                        x++
                    }
                    y++
                }
                transform.apply(sourceRgb, destinationRgb, count)
                var i = 0
                while (i < count) {
                    val rgb = i * 3
                    val r = checkedChannel(destinationRgb[rgb])
                    val g = checkedChannel(destinationRgb[rgb + 1])
                    val b = checkedChannel(destinationRgb[rgb + 2])
                    val a = alpha[i]
                    // setPixels accepts non-premultiplied color ints and handles storage.
                    packed[i] = Color.argb(a, r, g, b)
                    i++
                }
                output.setPixels(packed, 0, copyWidth, left, top, copyWidth, copyHeight)
                left += copyWidth
            }
            top += copyHeight
        }
        return output
    }

    /**
     * Render through the width-aware unsigned16 carrier path. ARGB_8888 source
     * pixels are expanded from Android sRGB ColorInt bytes. RGBA_F16 pixels
     * are read as Android [Color] values, converted to sRGB, and quantized to
     * unsigned16. Tile origin keeps sampling0's native four-pixel lane
     * continuous across tile edges.
     */
    fun renderSrgb16(
        source: Bitmap,
        exifOrientation: Int = ExifOrientation.NORMAL,
        transform: Srgb16TileTransform,
    ): Bitmap {
        val metadata = metadata(source, exifOrientation)
        val output = Bitmap.createBitmap(metadata.outputWidth, metadata.outputHeight, Bitmap.Config.ARGB_8888)
        output.setPremultiplied(true)
        val tilePixels = tileWidth * tileHeight
        val sourceRgb = ShortArray(tilePixels * 3)
        val destinationRgb = FloatArray(tilePixels * 3)
        val alpha = IntArray(tilePixels)
        val packed = IntArray(tilePixels)
        val legacyF16Reader = if (
            source.config == Bitmap.Config.RGBA_F16 && Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
        ) F16BitmapReader(source) else null

        var completed = false
        try {
            var top = 0
            while (top < metadata.outputHeight) {
                val copyHeight = minOf(tileHeight, metadata.outputHeight - top)
                var left = 0
                while (left < metadata.outputWidth) {
                    val copyWidth = minOf(tileWidth, metadata.outputWidth - left)
                    val count = copyWidth * copyHeight
                    var index = 0
                    var y = 0
                    while (y < copyHeight) {
                        var x = 0
                        while (x < copyWidth) {
                            val outX = left + x
                            val outY = top + y
                            val sourceX = ExifOrientation.sourceX(
                                source.width, source.height, metadata.exifOrientation, outX, outY,
                            )
                            val sourceY = ExifOrientation.sourceY(
                                source.width, source.height, metadata.exifOrientation, outX, outY,
                            )
                            val argb = source.getPixel(sourceX, sourceY)
                            val a: Int
                            val rgb = index * 3
                            if (source.config == Bitmap.Config.RGBA_F16) {
                                val converted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                                    source.getColor(sourceX, sourceY).toSrgb()
                                } else {
                                    requireNotNull(legacyF16Reader).read(sourceX, sourceY)
                                }
                                a = quantize8(converted.alpha())
                                sourceRgb[rgb] = quantize16(converted.red()).toShort()
                                sourceRgb[rgb + 1] = quantize16(converted.green()).toShort()
                                sourceRgb[rgb + 2] = quantize16(converted.blue()).toShort()
                            } else {
                                a = argb ushr 24
                                sourceRgb[rgb] = (Color.red(argb) * 257).toShort()
                                sourceRgb[rgb + 1] = (Color.green(argb) * 257).toShort()
                                sourceRgb[rgb + 2] = (Color.blue(argb) * 257).toShort()
                            }
                            alpha[index] = a
                            index++
                            x++
                        }
                        y++
                    }
                    transform.apply(sourceRgb, destinationRgb, count, left, top, copyWidth)
                    var i = 0
                    while (i < count) {
                        val rgb = i * 3
                        val r = checkedChannel(destinationRgb[rgb])
                        val g = checkedChannel(destinationRgb[rgb + 1])
                        val b = checkedChannel(destinationRgb[rgb + 2])
                        packed[i] = Color.argb(alpha[i], r, g, b)
                        i++
                    }
                    output.setPixels(packed, 0, copyWidth, left, top, copyWidth, copyHeight)
                    left += copyWidth
                }
                top += copyHeight
            }
            completed = true
            return output
        } finally {
            if (!completed) output.recycle()
        }
    }

    /**
     * Stream a profile-bound 16-bit TIFF directly from the transformed rows.
     * The writer receives one output row at a time, while the adapter still
     * processes bounded horizontal tiles so sampling0 lane origins stay exact.
     */
    fun writeSrgb16Tiff(
        source: Bitmap,
        output: OutputStream,
        iccProfile: ByteArray? = null,
        exifOrientation: Int = ExifOrientation.NORMAL,
        software: String = "CXO FilmPack",
        transform: Srgb16TileTransform,
    ) {
        val metadata = metadata(source, exifOrientation)
        val channels = if (metadata.hasAlpha) 4 else 3
        val tilePixels = tileWidth
        val sourceRgb = ShortArray(tilePixels * 3)
        val destinationRgb = FloatArray(tilePixels * 3)
        val alpha = IntArray(tilePixels)
        val row = ShortArray(metadata.outputWidth * channels)
        val legacyF16Reader = if (
            source.config == Bitmap.Config.RGBA_F16 && Build.VERSION.SDK_INT < Build.VERSION_CODES.Q
        ) F16BitmapReader(source) else null

        Rgb16TiffWriter.write(output, metadata.outputWidth, metadata.outputHeight, channels, iccProfile, software) { outY, destination ->
            var left = 0
            while (left < metadata.outputWidth) {
                val copyWidth = minOf(tileWidth, metadata.outputWidth - left)
                val count = copyWidth
                var index = 0
                while (index < count) {
                    val outX = left + index
                    val sourceX = ExifOrientation.sourceX(
                        source.width, source.height, metadata.exifOrientation, outX, outY,
                    )
                    val sourceY = ExifOrientation.sourceY(
                        source.width, source.height, metadata.exifOrientation, outX, outY,
                    )
                    val argb = source.getPixel(sourceX, sourceY)
                    val rgb = index * 3
                    if (source.config == Bitmap.Config.RGBA_F16) {
                        val converted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            source.getColor(sourceX, sourceY).toSrgb()
                        } else {
                            requireNotNull(legacyF16Reader).read(sourceX, sourceY)
                        }
                        alpha[index] = quantize8(converted.alpha())
                        sourceRgb[rgb] = quantize16(converted.red()).toShort()
                        sourceRgb[rgb + 1] = quantize16(converted.green()).toShort()
                        sourceRgb[rgb + 2] = quantize16(converted.blue()).toShort()
                    } else {
                        alpha[index] = argb ushr 24
                        sourceRgb[rgb] = (Color.red(argb) * 257).toShort()
                        sourceRgb[rgb + 1] = (Color.green(argb) * 257).toShort()
                        sourceRgb[rgb + 2] = (Color.blue(argb) * 257).toShort()
                    }
                    index++
                }
                transform.apply(sourceRgb, destinationRgb, count, left, outY, copyWidth)
                for (i in 0 until count) {
                    val rgb = i * 3
                    val out = (left + i) * channels
                    row[out] = quantize16(destinationRgb[rgb]).toShort()
                    row[out + 1] = quantize16(destinationRgb[rgb + 1]).toShort()
                    row[out + 2] = quantize16(destinationRgb[rgb + 2]).toShort()
                    if (channels == 4) row[out + 3] = (alpha[i] * 257).toShort()
                }
                left += copyWidth
            }
            row.copyInto(destination)
        }
    }

    /** Convenience bridge to the recovered sampling0 entry and HSV film path. */
    fun renderExactSampling0(
        source: Bitmap,
        pipeline: FixedSrgbFilmPipeline,
        exifOrientation: Int = ExifOrientation.NORMAL,
    ): Bitmap = renderSrgb16(source, exifOrientation) { rgb, destination, _, originX, _, rowWidth ->
        pipeline.renderRgb16ExactSampling0(rgb, destination, rowWidth, originX)
    }

    /** RGB-film counterpart of [renderExactSampling0]. */
    fun renderExactSampling0(
        source: Bitmap,
        pipeline: FixedSrgbRgbFilmPipeline,
        exifOrientation: Int = ExifOrientation.NORMAL,
    ): Bitmap = renderSrgb16(source, exifOrientation) { rgb, destination, _, originX, _, rowWidth ->
        pipeline.renderRgb16ExactSampling0(rgb, destination, rowWidth, originX)
    }

    /** Convenience bridge for the existing [FilmLut] numeric API. */
    fun render(
        source: Bitmap,
        lut: FilmLut,
        exifOrientation: Int = ExifOrientation.NORMAL,
        interpolation: Interpolation = Interpolation.TETRAHEDRAL,
    ): Bitmap = render(source, exifOrientation) { rgb, destination, pixelCount ->
        lut.renderRgb(rgb, destination, pixelCount, OutputSpace.SRGB, interpolation)
    }

    private fun checkedChannel(value: Float): Int {
        require(value.isFinite()) { "RGB transform produced a non-finite channel" }
        return (value.coerceIn(0f, 1f) * 255f).roundToInt()
    }

    private fun Color.toSrgb(): Color {
        val srgb = ColorSpace.get(ColorSpace.Named.SRGB)
        return if (colorSpace == srgb) this else convert(srgb)
    }

    private fun quantize16(value: Float): Int = Math.rint(
        (value.coerceIn(0f, 1f) * 65535f).toDouble(),
    ).toInt().coerceIn(0, 65535)

    private fun quantize8(value: Float): Int = Math.rint(
        (value.coerceIn(0f, 1f) * 255f).toDouble(),
    ).toInt().coerceIn(0, 255)

    /** API26-28 fallback for RGBA_F16, before Bitmap.getColor was added. */
    private class F16BitmapReader(source: Bitmap) {
        private val rowBytes = source.rowBytes
        private val premultiplied = source.isPremultiplied
        private val srgb = ColorSpace.get(ColorSpace.Named.SRGB)
        private val connector = source.colorSpace?.let {
            if (it == srgb) null else ColorSpace.connect(it, srgb)
        }
        private val bytes = ByteBuffer.allocate(source.byteCount).order(ByteOrder.nativeOrder()).also {
            source.copyPixelsToBuffer(it)
        }

        fun read(x: Int, y: Int): Color {
            val base = y * rowBytes + x * 8
            val alpha = half(bytes.getShort(base + 6).toInt() and 0xffff).coerceIn(0f, 1f)
            var r = half(bytes.getShort(base).toInt() and 0xffff)
            var g = half(bytes.getShort(base + 2).toInt() and 0xffff)
            var b = half(bytes.getShort(base + 4).toInt() and 0xffff)
            if (premultiplied && alpha > 0f) {
                r /= alpha; g /= alpha; b /= alpha
            }
            val converted = connector?.transform(r, g, b) ?: floatArrayOf(r, g, b)
            return Color.valueOf(converted[0], converted[1], converted[2], alpha, srgb)
        }

        private fun half(bits: Int): Float {
            val sign = (bits ushr 15) and 1
            val exponent = (bits ushr 10) and 0x1f
            val mantissa = bits and 0x3ff
            val value = when (exponent) {
                0 -> if (mantissa == 0) 0f else (mantissa / 1024f) * 2f.pow(-14)
                0x1f -> if (mantissa == 0) Float.POSITIVE_INFINITY else Float.NaN
                else -> (1f + mantissa / 1024f) * 2f.pow(exponent - 15)
            }
            return if (sign == 0) value else -value
        }
    }

    companion object {
        const val DEFAULT_TILE_SIZE = 128
        const val MAX_TILE_DIMENSION = 2048
        /** Limits temporary RGB/alpha/pixel arrays to 32 MiB (plus array headers). */
        const val MAX_TILE_PIXELS = 1_048_576
    }
}
