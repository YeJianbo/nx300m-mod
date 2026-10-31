// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Context
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.data.MediaEntry
import com.nxwa.nxconnect.data.MediaGroup
import org.json.JSONObject

/** Metadata is kept per actual file so a JPEG writeback cannot clear pending SRW edits. */
object PhotoEdits {
    fun supports(entry: MediaEntry) = entry.ext in setOf("jpg", "jpeg", "srw")
    private fun key(entry: MediaEntry, cameraUrl: String?) = "$cameraUrl|${entry.relPath}|${entry.size}"
    private fun pendingKey(entry: MediaEntry, cameraUrl: String?) = "pending|$cameraUrl|${entry.relPath}"
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("photo_edits", Context.MODE_PRIVATE)

    fun get(ctx: Context, entry: MediaEntry, cameraUrl: String? = NxApp.camera?.baseUrl): String? = prefs(ctx).let {
        it.getString(pendingKey(entry, cameraUrl), null) ?: it.getString(key(entry, cameraUrl), null)
    }

    fun update(ctx: Context, entry: MediaEntry, changes: String) {
        updateEntries(ctx, listOf(entry), changes)
    }

    /** Editing a paired photo updates both its JPEG and SRW export copies. */
    fun update(ctx: Context, group: MediaGroup, changes: String) {
        // Older app versions stored only the JPEG's edits for paired photos.
        val legacyJpegEdits = group.jpeg?.let { get(ctx, it) }
        updateEntries(ctx, group.files.filter(::supports), changes, legacyJpegEdits)
    }

    private fun updateEntries(ctx: Context, entries: List<MediaEntry>, changes: String, legacyJpegEdits: String? = null) {
        val incoming = JSONObject(changes)
        val preferences = prefs(ctx)
        val cameraUrl = NxApp.camera?.baseUrl
        preferences.edit().apply {
            entries.forEach { entry ->
                val prior = get(ctx, entry, cameraUrl)
                    ?: legacyJpegEdits.takeIf { entry.ext == "srw" }
                val merged = JSONObject(prior ?: "{}")
                incoming.keys().forEach { tag -> merged.put(tag, incoming.get(tag)) }
                if (preferences.contains(pendingKey(entry, cameraUrl))) putString(pendingKey(entry, cameraUrl), merged.toString())
                else putString(key(entry, cameraUrl), merged.toString())
            }
        }.apply()
    }

    /** A lost write acknowledgement leaves the new byte count unknown across gallery refreshes. */
    fun move(ctx: Context, from: MediaEntry, to: MediaEntry, uncertain: Boolean, cameraUrl: String? = NxApp.camera?.baseUrl) {
        val edits = get(ctx, from, cameraUrl)
        prefs(ctx).edit().apply {
            remove(key(from, cameraUrl))
            if (edits != null) {
                if (uncertain) putString(pendingKey(to, cameraUrl), edits)
                else putString(key(to, cameraUrl), edits)
            }
        }.apply()
    }

    fun clear(ctx: Context, entry: MediaEntry, cameraUrl: String? = NxApp.camera?.baseUrl) {
        prefs(ctx).edit().remove(key(entry, cameraUrl)).remove(pendingKey(entry, cameraUrl)).apply()
    }

    fun clear(ctx: Context, group: MediaGroup, cameraUrl: String? = NxApp.camera?.baseUrl) {
        prefs(ctx).edit().apply {
            group.files.forEach { entry -> remove(key(entry, cameraUrl)); remove(pendingKey(entry, cameraUrl)) }
        }.apply()
    }
}
