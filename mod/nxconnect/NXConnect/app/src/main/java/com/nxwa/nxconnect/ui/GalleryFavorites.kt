package com.nxwa.nxconnect.ui
import android.content.Context
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.data.MediaGroup
object GalleryFavorites {
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("gallery_favorites", 0)
    private fun key(group: MediaGroup) = "${NxApp.camera?.baseUrl}|${group.key}"
    fun contains(ctx: Context, group: MediaGroup) = prefs(ctx).getBoolean(key(group), false)
    fun toggle(ctx: Context, group: MediaGroup): Boolean {
        val selected = !contains(ctx, group)
        prefs(ctx).edit().putBoolean(key(group), selected).apply()
        return selected
    }
}
