package dev.cxo.filmpack.app

/** Both formats store 8-bit sRGB samples. TIFF preserves these samples losslessly. */
enum class ExportFormat(val mimeType: String, val extension: String) {
    JPEG("image/jpeg", "jpg"),
    TIFF("image/tiff", "tif"),
}

data class ExportSettings(
    val format: ExportFormat = ExportFormat.JPEG,
    val jpegQuality: Int = 98,
    /** Zero keeps the original size; positive values cap the longest stored edge. */
    val maxLongEdge: Int = 0,
) {
    init {
        require(jpegQuality in 1..100) { "JPEG quality must be 1..100" }
        require(maxLongEdge >= 0) { "Maximum long edge must be non-negative" }
    }

    /** Stored dimensions before applying EXIF orientation. Never enlarges. */
    fun dimensions(width: Int, height: Int): Pair<Int, Int> {
        require(width > 0 && height > 0) { "Image dimensions must be positive" }
        val longest = maxOf(width, height)
        if (maxLongEdge == 0 || longest <= maxLongEdge) return width to height
        fun scaled(edge: Int): Int =
            ((edge.toLong() * maxLongEdge + longest / 2L) / longest).toInt().coerceAtLeast(1)
        return scaled(width) to scaled(height)
    }
}
