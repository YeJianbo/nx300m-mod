package dev.cxo.filmpack.app

import dev.cxo.filmpack.core.NativeGrain8Patch
import dev.cxo.filmpack.core.NativeGrain8Orientation

/** Prepared for a single explicit photograph coordinate grid (full-size or a native level).
 * No implicit rescaling or EXIF reinterpretation is performed.
 * Resource selection, source hashing, preparation and context flags remain
 * explicit at the caller until the product grain loader/UI is connected. */
class PhotoGrainLayer(
    val width: Int, val height: Int, internal val kernel: NativeGrain8Patch,
    val sourceWidth: Int = width, val sourceHeight: Int = height,
    val sourceExifOrientation: Int = 1, val invocationScale: Int = 0,
) {
    init {
        require(width > 0 && height > 0 && sourceWidth > 0 && sourceHeight > 0 && invocationScale in 0..2)
        val orientation = NativeGrain8Orientation.fromExifDisplayTransform(sourceExifOrientation)
        require(width == if (orientation >= 4) sourceHeight else sourceWidth)
        require(height == if (orientation >= 4) sourceWidth else sourceHeight)
    }
    internal fun requireDimensions(width: Int, height: Int) {
        require(this.width == width && this.height == height) { "颗粒坐标尺寸与照片不一致，请按原尺寸重新准备" }
    }
    internal fun requireSource(width: Int, height: Int, orientation: Int) {
        require(invocationScale == 0 && sourceWidth == width && sourceHeight == height && sourceExifOrientation == orientation) {
            "颗粒与保存照片的原始尺寸或方向不一致，请重新准备"
        }
    }
    internal fun renderSourceLayout(source: FloatArray, destination: FloatArray, width: Int, x: Int, y: Int) {
        if (sourceExifOrientation == 1) kernel.renderNormalizedRgb(source,destination,width,x,y)
        else kernel.renderNormalizedRgbInSourceLayout(source,destination,width,sourceWidth,sourceHeight,
            NativeGrain8Orientation.fromExifDisplayTransform(sourceExifOrientation),x,y)
    }

    /** Apply a prepared preview layer to an unrotated, downsampled RAW row.
     * The kernel was prepared at [invocationScale], so its coordinate grid is
     * the current-level source grid rather than the full-resolution grid. */
    internal fun renderPreviewSourceLayout(source: FloatArray, destination: FloatArray, width: Int,
                                           x: Int, y: Int) {
        val levelWidth = sourceWidth
        val levelHeight = sourceHeight
        require(x >= 0 && y >= 0 && x + width <= levelWidth && y < levelHeight) {
            "RAW grain row $x+$width,$y exceeds ${levelWidth}x$levelHeight"
        }
        if (sourceExifOrientation == 1) kernel.renderNormalizedRgb(source,destination,width,x,y)
        else kernel.renderNormalizedRgbInSourceLayout(source,destination,width,levelWidth,levelHeight,
            NativeGrain8Orientation.fromExifDisplayTransform(sourceExifOrientation),x,y)
    }
}
