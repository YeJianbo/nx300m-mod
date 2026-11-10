package dev.cxo.filmpack.app

import android.graphics.Bitmap
import dev.cxo.filmpack.core.NativeGrain8Geometry

/** A decoder-owned bitmap with explicit original and current-level grids.
 * Uncropped JPEG/PNG is admitted by this API, with EXIF applied once. Android
 * resampling is not claimed to reproduce the native engine's image pyramid. */
class PhotoGrainPreview internal constructor(
    val bitmap: Bitmap,
    val sourceWidth: Int,
    val sourceHeight: Int,
    val invocationScale: Int,
    val sourceExifOrientation: Int = 1,
) {
    init {
        require(sourceWidth > 0 && sourceHeight > 0 && invocationScale in 0..2)
        require(sourceExifOrientation in 1..8)
        val rawWidth = sourceWidth shr invocationScale; val rawHeight = sourceHeight shr invocationScale
        require(bitmap.width == if (sourceExifOrientation >= 5) rawHeight else rawWidth)
        require(bitmap.height == if (sourceExifOrientation >= 5) rawWidth else rawHeight)
    }

    fun request(
        grainIdentifier: String, format: NativeGrain8Geometry.Format, manualSize: Float,
        intensity: Int, seed: Long, color: Boolean, neutralizeRedBlue: Boolean,
    ) = PhotoGrainRequest(sourceWidth,sourceHeight,sourceExifOrientation,grainIdentifier,format,manualSize,
        intensity,seed,color,neutralizeRedBlue,invocationScale)
}
