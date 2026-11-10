package dev.cxo.filmpack.app

import android.content.Context
import dev.cxo.filmpack.core.RawCameraBwProvider
import dev.cxo.filmpack.core.RawNegativeProviderCatalog
import dev.cxo.filmpack.core.RawTargetFidelityProfile
import org.json.JSONArray
import org.json.JSONObject

/** Camera tone curves and pre-tone matrices from static body resources. */
internal class RawCameraBwCatalog(context: Context) {
    private data class Camera(
        val tone: FloatArray,
        val fidelity: RawTargetFidelityProfile,
        val jpegBeforeTc: RawTargetFidelityProfile,
    )

    private val cameras: Map<String, Camera>
    private val films: Map<String, RawCameraBwProvider>

    init {
        val root = context.assets.open("raw-camera-bw/catalog.json").bufferedReader()
            .use { JSONObject(it.readText()) }
        require(root.getString("schema") == "CXOCAMERABW1")
        fun curve(name: String): FloatArray {
            require(name.matches(Regex("[A-Za-z0-9_.-]+\\.bin")))
            return context.assets.open("raw-camera-bw/curves/$name").use(RawNegativeProviderCatalog::readQ15Curve)
        }
        val cameraRows = root.getJSONArray("cameras")
        cameras = (0 until cameraRows.length()).associate { i ->
            val row = cameraRows.getJSONObject(i)
            row.getString("id") to Camera(curve(row.getString("tone_curve")),
                profile(row.getJSONObject("fidelity")), profile(row.getJSONObject("jpeg_before_tc")))
        }
        val filmRows = root.getJSONArray("films")
        films = (0 until filmRows.length()).associate { i ->
            val row = filmRows.getJSONObject(i)
            require(row.getBoolean("type_bw") && row.getBoolean("runtime_bw"))
            val id = row.getString("id")
            id to RawCameraBwProvider(id, curve(row.getString("curve")),
                matrix(row.getJSONArray("post_matrix")), row.getBoolean("use_camera_color_matrix"))
        }
        require(cameras.keys == setOf("s1rii", "s9") && films.size == 2)
    }

    fun supports(id: String): Boolean = id in films

    fun supports(cameraId: String, filmId: String): Boolean = cameraId in cameras && filmId in films

    fun bake(cameraId: String, temperature: Float, exposureBiasEv: Float,
             extraMatrix: FloatArray, filmId: String): RawNegativeProviderCatalog.Bake {
        val camera = requireNotNull(cameras[cameraId])
        return requireNotNull(films[filmId]).bake(exposureBiasEv, extraMatrix, camera.tone,
            camera.jpegBeforeTc.matrixAt(temperature), camera.fidelity.matrixAt(temperature))
    }

    private fun matrix(array: JSONArray): FloatArray {
        require(array.length() == 9)
        return FloatArray(9) { array.getDouble(it).toFloat() }.also { require(it.all(Float::isFinite)) }
    }

    private fun profile(row: JSONObject) = RawTargetFidelityProfile(
        matrix(row.getJSONArray("tungsten")), matrix(row.getJSONArray("daylight")),
        row.getDouble("temperature_low").toFloat(), row.getDouble("temperature_high").toFloat(),
    )
}
