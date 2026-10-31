// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import java.util.Locale

enum class DownloadFormat(val label: String) { JPEG("JPEG"), RAW("RAW"), BOTH("JPEG + RAW") }

/** Display grouping never replaces the actual files used for transfer. */
data class MediaGroup(val files: List<MediaEntry>) {
    val jpeg: MediaEntry? get() = files.firstOrNull { it.ext == "jpg" || it.ext == "jpeg" }
    val raw: MediaEntry? get() = files.firstOrNull { it.isRaw }
    val primary: MediaEntry get() = jpeg ?: files.first()
    val key: String get() = primary.relPath
    val name: String get() = primary.name
    val folder: String get() = primary.folder
    val isImage: Boolean get() = primary.isImage
    val isVideo: Boolean get() = primary.isVideo
    val paired: Boolean get() = jpeg != null && raw != null
    val size: Long get() = if (files.all { it.size >= 0 }) files.sumOf { it.size } else -1L
    val sizeText: String? get() = MediaEntry.formatSize(size)
    val kindLabel: String get() = if (paired) "RAW + JPEG" else primary.kindLabel
    val displayName: String get() = if (paired) name.substringBeforeLast('.') else name
    val previewFiles: List<MediaEntry> get() = listOfNotNull(raw, jpeg).ifEmpty { files }

    fun downloadFiles(format: DownloadFormat): List<MediaEntry> = when {
        jpeg == null && raw == null -> files
        format == DownloadFormat.JPEG -> files.filter { it.ext == "jpg" || it.ext == "jpeg" }
        format == DownloadFormat.RAW -> files.filter { it.isRaw }
        else -> files
    }

    companion object {
        /** Only an unambiguous JPEG + SRW in the same directory is paired. */
        fun group(entries: List<MediaEntry>): List<MediaGroup> {
            fun stem(entry: MediaEntry) = entry.folder + "/" + entry.name.substringBeforeLast('.').lowercase(Locale.ROOT)
            val candidates = entries.filter { it.ext in setOf("jpg", "jpeg", "srw") }.groupBy(::stem)
            val used = mutableSetOf<String>()
            return entries.mapNotNull { entry ->
                if (!used.add(entry.relPath)) return@mapNotNull null
                val siblings = candidates[stem(entry)].orEmpty()
                val jpeg = siblings.filter { it.ext == "jpg" || it.ext == "jpeg" }
                val raw = siblings.filter { it.ext == "srw" }
                if (entry.ext in setOf("jpg", "jpeg", "srw") && jpeg.size == 1 && raw.size == 1) {
                    used.add(jpeg.single().relPath);used.add(raw.single().relPath)
                    MediaGroup(listOf(jpeg.single(), raw.single()))
                } else MediaGroup(listOf(entry))
            }
        }
    }
}
