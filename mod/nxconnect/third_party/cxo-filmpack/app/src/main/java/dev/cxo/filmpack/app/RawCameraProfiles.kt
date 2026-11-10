package dev.cxo.filmpack.app

import android.content.Context
import dev.cxo.filmpack.core.RawFidelityProfile
import dev.cxo.filmpack.core.RawWhiteBalance
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale

/** Camera calibration is selected explicitly, before any film provider is built. */
internal class RawCameraProfiles(context: Context) {
    private val context = context.applicationContext
    private val profiles = HashMap<String, Camera>()
    private data class Entry(val id: String, val fidelityAsset: String, val wbAsset: String)
    private val entries: Map<String, Entry> by lazy {
        val byModel = HashMap<String, Entry>()
        val ids = HashSet<String>()
        val bodyIds = HashSet<Int>()
        val catalogPaths = listOf("raw/cameras.json", "raw/batch-generated/cameras.json")
        for (path in catalogPaths) {
            val root = this.context.assets.open(path).bufferedReader().use { JSONObject(it.readText()) }
            require(root.getString("schema") == "CXOCAMERAS1")
            val rows = root.getJSONArray("cameras")
            for (i in 0 until rows.length()) {
                val row = rows.getJSONObject(i)
                val entry = Entry(row.getString("id"), row.getString("fidelity"), row.getString("whiteBalance"))
                require(ids.add(entry.id) && bodyIds.add(row.getInt("bodyId")))
                require(entry.id.matches(Regex("[A-Za-z0-9_.-]+")))
                require(listOf(entry.fidelityAsset, entry.wbAsset).all(::isSafeAssetPath))
                val models = row.getJSONArray("models")
                require(models.length() > 0)
                for (j in 0 until models.length()) {
                    val model = models.getString(j).trim().lowercase(Locale.ROOT)
                    require(model.isNotEmpty() && byModel.put(model, entry) == null) { "Duplicate RAW model alias" }
                }
            }
        }
        byModel
    }

    private fun isSafeAssetPath(path: String): Boolean = path.split('/').all { component ->
        component.isNotEmpty() && component != "." && component != ".." &&
            component.matches(Regex("[A-Za-z0-9_.-]+"))
    }

    data class Camera(
        val id: String,
        val fidelity: RawFidelityProfile,
        val whiteBalance: RawWhiteBalance,
        val exposureBiasEv: Float,
    )

    @Synchronized
    fun forModel(model: String, make: String = ""): Camera? {
        val key = model.trim().lowercase(Locale.ROOT)
        // LibRaw separates Nikon's make from its model (Z 6_2); the DxO
        // catalog keeps NIKON Z 6_2. Preserve both identities without guessing
        // a manufacturer from an otherwise ambiguous model string.
        val qualified = "${make.trim()} ${model.trim()}".trim().lowercase(Locale.ROOT)
        // LibRaw simplify_make_model also removes Fuji's FinePix prefix and
        // Leica's DIGITAL CAMERA suffix. Reverse only these documented,
        // manufacturer-specific rewrites when exact catalog lookup misses.
        val legacyModel = when (make.trim().lowercase(Locale.ROOT)) {
            "fujifilm" -> "finepix $key"
            "leica" -> "$key digital camera"
            else -> null
        }
        val entry = entries[key] ?: entries[qualified] ?: legacyModel?.let(entries::get) ?: return null
        return profiles.getOrPut(entry.id) { load(entry) }
    }

    private fun load(entry: Entry): Camera {
        val json = context.assets.open("raw/${entry.fidelityAsset}").bufferedReader().use { JSONObject(it.readText()) }
        fun matrix(name: String): FloatArray {
            val rows = json.getJSONArray(name)
            require(rows.length() == 3)
            return FloatArray(9) { i -> rows.getJSONArray(i / 3).getDouble(i % 3).toFloat() }
        }
        val fidelity = RawFidelityProfile(matrix("fidelityTungsten"), matrix("fidelityDaylight"),
            json.getDouble("tempLow").toFloat(), json.getDouble("tempHigh").toFloat())
        val bias = json.getDouble("exposureBiasEv").toFloat().also { require(it.isFinite()) }
        val buffer = ByteBuffer.wrap(context.assets.open("raw/${entry.wbAsset}").use { it.readBytes() })
            .order(ByteOrder.LITTLE_ENDIAN)
        val magic = ByteArray(8).also(buffer::get)
        require(magic.contentEquals("CXOWBG01".toByteArray())) { "Unsupported RAW WB asset" }
        val nt = buffer.int; val ni = buffer.int; val ns = buffer.int
        require(nt in 2..128 && ni in 2..128 && ns == nt * ni * 3)
        val temperatures = FloatArray(nt) { buffer.float }
        val tints = FloatArray(ni) { buffer.float }
        val scales = FloatArray(ns) { buffer.float }
        require(!buffer.hasRemaining())
        return Camera(entry.id, fidelity, RawWhiteBalance(temperatures, tints, scales), bias)
    }
}
