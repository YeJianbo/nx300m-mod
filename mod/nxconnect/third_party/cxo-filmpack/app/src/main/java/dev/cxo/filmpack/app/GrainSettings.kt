package dev.cxo.filmpack.app

import android.content.SharedPreferences
import dev.cxo.filmpack.core.NativeGrain8Geometry

/** Immutable settings captured for each preview/save. Empty identifier follows
 * the selected film only when an exact verified grain identifier exists. */
data class GrainSettings(
    val enabled: Boolean = false,
    val identifier: String = "",
    val intensity: Int = 100,
    val format: NativeGrain8Geometry.Format = NativeGrain8Geometry.Format.SMALL,
    val manualSize: Float = 1f,
    val seed: Long = 17,
) {
    fun resolve(film: FilmChoice, identifiers: List<String>): String {
        val selected = identifier.ifEmpty { film.token }
        require(selected in identifiers) { "此效果没有跟随颗粒，请在颗粒设置中明确选择一种颗粒" }
        return selected
    }

    fun save(preferences: SharedPreferences) {
        preferences.edit().putBoolean("grain.enabled",enabled).putString("grain.id",identifier)
            .putInt("grain.intensity",intensity).putString("grain.format",format.name)
            .putFloat("grain.manual",manualSize).putLong("grain.seed",seed).apply()
    }

    companion object {
        fun read(preferences: SharedPreferences) = GrainSettings(
            preferences.getBoolean("grain.enabled",false),preferences.getString("grain.id","") ?: "",
            preferences.getInt("grain.intensity",100).coerceIn(0,200),
            NativeGrain8Geometry.Format.entries.firstOrNull { it.name == preferences.getString("grain.format","") }
                ?: NativeGrain8Geometry.Format.SMALL,
            preferences.getFloat("grain.manual",1f).takeIf { it.isFinite() }?.coerceIn(.5f,5f) ?: 1f,
            preferences.getLong("grain.seed",17).coerceIn(0,0xFFFFFFFFL))
    }
}
