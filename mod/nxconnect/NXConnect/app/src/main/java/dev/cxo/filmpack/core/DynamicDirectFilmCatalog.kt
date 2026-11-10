package dev.cxo.filmpack.core

import java.io.InputStream
import java.security.MessageDigest
import java.util.Collections

/**
 * Separate, review-only catalog for the five recovered direct HSV initializers.
 *
 * The context is deliberately explicit: feature5=false, JPEG TC=0, runtime BW
 * false, and shared postcurve kind 0. These rows are not part of the fixed 147
 * catalog and do not claim selection for any other dispatch context.
 */
class DynamicDirectFilmPreset internal constructor(
    val id: String,
    val renderingType: Int,
    val providerName: String,
    val providerRed: Float,
    val providerGreen: Float,
    val providerBlue: Float,
    val assetFile: String,
    val assetSha256: String,
    val feature5Enabled: Boolean = false,
    val jpegToneMode: Int = 0,
    val runtimeBw: Boolean = false,
    val curveKind: Int = 0,
) {
    val providerGains: FloatArray
        get() = floatArrayOf(providerRed, providerGreen, providerBlue)

    init {
        require(id.matches(ID))
        require(providerName == "Astia" || providerName == "Provia" ||
            providerName == "Velvia" || providerName == "Ekta100VS" || providerName == "Kod64")
        require(providerRed.isFinite() && providerGreen.isFinite() && providerBlue.isFinite())
        require(providerRed >= 0f && providerGreen >= 0f && providerBlue >= 0f)
        require(assetFile == "$id.cxohsv" && assetSha256.matches(SHA))
        require(!feature5Enabled && jpegToneMode == 0 && !runtimeBw && curveKind == 0)
    }

    companion object {
        private val ID = Regex("ColorMode[A-Za-z0-9_]{1,100}")
        private val SHA = Regex("[0-9a-f]{64}")
    }
}

class DynamicDirectFilmData internal constructor(
    val preset: DynamicDirectFilmPreset,
    val film: HsvFilmLut,
) {
    /** Construct the fixed-entry/direct-film pipeline without changing catalogs. */
    fun pipeline(entry: FixedSrgbEntryLut): FixedSrgbFilmPipeline {
        val base = FilmRgbBaseScale(
            preset.id, preset.renderingType,
            preset.providerRed, preset.providerGreen, preset.providerBlue,
        )
        val wb = NativeWhiteBalanceRgb.create(base)
        return FixedSrgbFilmPipeline(entry, film, wb)
    }
}

class DynamicDirectFilmCatalog private constructor(rows: List<DynamicDirectFilmPreset>) {
    val presets: List<DynamicDirectFilmPreset> = Collections.unmodifiableList(ArrayList(rows))
    private val byId = presets.associateBy { it.id }

    fun find(id: String): DynamicDirectFilmPreset? = byId[id]

    fun load(id: String, openAsset: (String) -> InputStream): DynamicDirectFilmData {
        val preset = requireNotNull(find(id)) { "Unsupported dynamic direct film: $id" }
        val bytes = openAsset(preset.assetFile).use { it.readBytes() }
        require(sha256(bytes) == preset.assetSha256) { "Dynamic direct asset hash mismatch: $id" }
        // The recovered direct candidates preserve the generator's exact 32768
        // endpoint in the extended Q15 curves. Keep that representation here;
        // the ordinary fixed catalog remains strict at 32767.
        val film = HsvFilmLutIO.read(bytes.inputStream(), allowEndpoint32768 = true)
        require(film.jpegToneMode == preset.jpegToneMode &&
            film.runtimeBw == preset.runtimeBw && !film.repairBwType && film.postCurveChannels == 1) {
            "Dynamic direct asset context mismatch: $id"
        }
        return DynamicDirectFilmData(preset, film)
    }

    companion object {
        private val rows = listOf(
            DynamicDirectFilmPreset(
                "ColorModeArgentiqueAstia", 1000, "Astia", 1f, 1f, 1f,
                "ColorModeArgentiqueAstia.cxohsv",
                "ef4ef839cb9d1445b83469a5edab83dc5d1452534fad5411036066753c61832a",
            ),
            DynamicDirectFilmPreset(
                "ColorModeArgentiqueProvia", 1001, "Provia", 1f, 1f, 1f,
                "ColorModeArgentiqueProvia.cxohsv",
                "0803e103322c0e9d69018257209ca531de47b31b5e6bacf2fae3d66ccc733ed6",
            ),
            DynamicDirectFilmPreset(
                "ColorModeArgentiqueVelvia", 1002, "Velvia", 1f, 1f, 1f,
                "ColorModeArgentiqueVelvia.cxohsv",
                "9aef1a8d88beb309ff743c3c72993527282c02ebd07f8aee7fec574618275d9d",
            ),
            DynamicDirectFilmPreset(
                "ColorModeArgentiqueEkta100VS", 1003, "Ekta100VS",
                1.096523404121399f, 1.026210904121399f, 1.029726505279541f,
                "ColorModeArgentiqueEkta100VS.cxohsv",
                "6a9970c3326ed5a647b2d093ee9203c42df387b00996a3febe1772047fde49ab",
            ),
            DynamicDirectFilmPreset(
                "ColorModeArgentiqueKod64", 1004, "Kod64",
                1.0369819402694702f, 1.0109899044036865f, 1.0429127216339111f,
                "ColorModeArgentiqueKod64.cxohsv",
                "4a565e8179ec91e843a4ba403cc69fdffbe85c275e0465c43af9204a360ea14c",
            ),
        )

        fun create(): DynamicDirectFilmCatalog = DynamicDirectFilmCatalog(rows)

        private fun sha256(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it.toInt() and 255) }
    }
}
