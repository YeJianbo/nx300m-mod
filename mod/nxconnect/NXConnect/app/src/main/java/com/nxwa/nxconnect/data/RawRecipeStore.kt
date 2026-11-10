// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import android.content.Context
import android.util.AtomicFile
import org.json.JSONObject
import java.io.File
import java.util.UUID

data class RawRecipe(
    val id: String,
    val name: String,
    val sourceUri: String,
    val sourceName: String,
    val createdAt: Long,
    val updatedAt: Long,
    val params: RawRenderParams,
    val lutScope: String?,
    val lutId: String?,
    val lutLabel: String?,
    val filmId: String?,
    val filmLabel: String?,
    val filmStrength: Int,
)

class RawRecipeStore(context: Context) {
    private val directory = File(context.filesDir, "raw-recipes").apply { mkdirs() }

    fun list(): List<RawRecipe> = directory.listFiles { file -> file.isFile && file.name.endsWith(".json") }
        .orEmpty()
        .mapNotNull { file -> runCatching { read(file) }.getOrNull() }
        .sortedByDescending(RawRecipe::updatedAt)

    fun get(id: String): RawRecipe? {
        validateId(id)
        val file = File(directory, "$id.json")
        return file.takeIf(File::isFile)?.let(::read)
    }

    fun save(recipe: RawRecipe): RawRecipe {
        validateId(recipe.id)
        val name = recipe.name.trim()
        require(name.isNotEmpty() && name.length <= 80 && name.none { it.isISOControl() }) {
            "配方名称须为 1–80 个字符，且不能包含换行"
        }
        require(recipe.sourceUri.isNotBlank()) { "配方缺少 RAW 来源" }
        val normalized = recipe.copy(name = name)
        val target = File(directory, "${normalized.id}.json")
        val atomic = AtomicFile(target)
        val stream = atomic.startWrite()
        try {
            stream.write((toJson(normalized).toString(2) + "\n").toByteArray(Charsets.UTF_8))
            atomic.finishWrite(stream)
        } catch (error: Throwable) {
            atomic.failWrite(stream)
            throw error
        }
        return normalized
    }

    fun delete(id: String) {
        validateId(id)
        if (!File(directory, "$id.json").delete()) {
            throw IllegalStateException("无法删除这项配方")
        }
    }

    fun newId(): String = UUID.randomUUID().toString()

    private fun read(file: File): RawRecipe {
        val root = JSONObject(file.readText(Charsets.UTF_8))
        val schema = root.getInt("schema")
        require(schema in 1..2) { "RAW 配方版本不受支持" }
        val params = root.getJSONObject("params")
        val lut = root.optJSONObject("lut")
        val film = root.optJSONObject("film")
        val filmStrength = film?.optInt("strength", 100) ?: 100
        require(filmStrength in 0..200) { "RAW 配方胶片强度无效" }
        return RawRecipe(
            id = root.getString("id"),
            name = root.getString("name"),
            sourceUri = root.getString("source_uri"),
            sourceName = root.getString("source_name"),
            createdAt = root.getLong("created_at"),
            updatedAt = root.getLong("updated_at"),
            params = RawRenderParams(
                exposureEv = params.getDouble("exposure_ev").toFloat(),
                contrast = params.getInt("contrast"),
                highlights = params.getInt("highlights"),
                shadows = params.getInt("shadows"),
                temperature = params.getInt("temperature"),
                tint = params.getInt("tint"),
                saturation = params.getInt("saturation"),
                lutStrength = params.getInt("lut_strength"),
            ),
            lutScope = lut?.optString("scope")?.takeIf { it.isNotBlank() },
            lutId = lut?.optString("id")?.takeIf { it.isNotBlank() },
            lutLabel = lut?.optString("label")?.takeIf { it.isNotBlank() },
            filmId = film?.optString("id")?.takeIf { it.isNotBlank() },
            filmLabel = film?.optString("label")?.takeIf { it.isNotBlank() },
            filmStrength = filmStrength,
        ).also { validateId(it.id) }
    }

    private fun toJson(recipe: RawRecipe) = JSONObject()
        .put("schema", 2)
        .put("id", recipe.id)
        .put("name", recipe.name)
        .put("source_uri", recipe.sourceUri)
        .put("source_name", recipe.sourceName)
        .put("created_at", recipe.createdAt)
        .put("updated_at", recipe.updatedAt)
        .put("params", JSONObject()
            .put("exposure_ev", recipe.params.exposureEv)
            .put("contrast", recipe.params.contrast)
            .put("highlights", recipe.params.highlights)
            .put("shadows", recipe.params.shadows)
            .put("temperature", recipe.params.temperature)
            .put("tint", recipe.params.tint)
            .put("saturation", recipe.params.saturation)
            .put("lut_strength", recipe.params.lutStrength))
        .put("lut", recipe.lutId?.let { id ->
            JSONObject()
                .put("scope", recipe.lutScope ?: "phone")
                .put("id", id)
                .put("label", recipe.lutLabel ?: id)
        })
        .put("film", recipe.filmId?.let { id ->
            JSONObject()
                .put("id", id)
                .put("label", recipe.filmLabel ?: id)
                .put("strength", recipe.filmStrength)
        })
        .put("engine", JSONObject()
            .put("raw", "LibRaw 0.22.3")
            .put("pipeline", "camera-wb-linear-srgb-lut-cxo-film-v1")
            .put("film_engine", "CXO FilmPack 1.0.1"))

    private fun validateId(id: String) {
        require(Regex("[a-f0-9-]{36}").matches(id)) { "RAW 配方 ID 无效" }
    }
}
