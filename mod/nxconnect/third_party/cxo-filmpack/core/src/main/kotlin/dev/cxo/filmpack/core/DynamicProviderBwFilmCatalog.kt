package dev.cxo.filmpack.core

import java.io.InputStream
import java.security.MessageDigest

/** Conditional review assets: WideGamut, feature5=false, Custom RGB WB2/body-base0,
 * TC1, type-BW and runtime-BW true, shared postcurve, no optional filters or grain.
 * These two entries remain separate from the fixed 147 and the five direct films.
 */
data class DynamicProviderBwFilmPreset internal constructor(
    val id: String,
    val renderingType: Int,
    val assetSha256: String,
) {
    val assetFile: String get() = "$id.cxohsv"
}

class DynamicProviderBwFilmData internal constructor(
    val preset: DynamicProviderBwFilmPreset,
    val film: HsvFilmLut,
) {
    /** The two class vtable+0x10 methods statically supply identity RGB gains. */
    fun pipeline(entry: FixedSrgbEntryLut, intensity: Int = 100): FixedSrgbFilmPipeline =
        FixedSrgbFilmPipeline.withWideGamutIntensity(entry, film,
            FilmRgbBaseScale(preset.id, preset.renderingType, 1f, 1f, 1f), intensity)
}

class DynamicProviderBwFilmCatalog private constructor() {
    val presets: List<DynamicProviderBwFilmPreset> = listOf(
        DynamicProviderBwFilmPreset("ColorModeArgentiqueKodakHIEFiltre", 1770,
            "9c4d03b7ab6a8a285f77104d2b805d14c922ec9b5af62ba41a68ffa3bb9287af"),
        DynamicProviderBwFilmPreset("ColorModeArgentiqueRolleiIR400", 1768,
            "bdc5051f12e1af474d9af3f21f41be8a16c110dcd33f3b7fd0014ef1b8ad76c5"),
    )

    fun find(id: String): DynamicProviderBwFilmPreset? = presets.find { it.id == id }

    fun load(id: String, openAsset: (String) -> InputStream): DynamicProviderBwFilmData {
        val preset = requireNotNull(find(id)) { "Unsupported conditional provider-BW film: $id" }
        val bytes = openAsset(preset.assetFile).use { it.readBytes() }
        val sha = MessageDigest.getInstance("SHA-256").digest(bytes)
            .joinToString("") { "%02x".format(it.toInt() and 255) }
        require(sha == preset.assetSha256) { "Provider-BW asset hash mismatch" }
        val film = HsvFilmLutIO.read(bytes.inputStream())
        require(film.jpegToneMode == 1 && film.repairBwType && film.runtimeBw && film.postCurveChannels == 1)
        return DynamicProviderBwFilmData(preset, film)
    }

    companion object {
        fun create(): DynamicProviderBwFilmCatalog = DynamicProviderBwFilmCatalog()
    }
}
