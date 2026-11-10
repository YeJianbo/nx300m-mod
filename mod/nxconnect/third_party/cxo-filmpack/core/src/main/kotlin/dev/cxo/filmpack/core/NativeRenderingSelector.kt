package dev.cxo.filmpack.core

/** Ordinary Engine ColorRenderingContext constructor selection (0x4b22b4).
 * Input integers are native rendering-type IDs, not catalog or UI indices.
 * This does not observe the selected FilmPack export settings.
 */
object NativeRenderingSelector {
    enum class ColorModel(val nativeValue: Int) { RGB(0), HSV(1) }

    fun colorModelForRenderingType(renderingType: Int): ColorModel = when (renderingType) {
        0x3fe, // KodakEIR
        0x6e7, // KodakHIE
        0x772 -> ColorModel.RGB // CineTealAndPink
        else -> ColorModel.HSV
    }
}
