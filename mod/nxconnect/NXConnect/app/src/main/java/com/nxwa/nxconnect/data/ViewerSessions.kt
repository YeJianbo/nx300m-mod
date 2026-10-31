// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

/** Lists stay in the camera session instead of crossing Android's Binder size limit. */
object ViewerSessions {
    private val lists = mutableMapOf<String, List<MediaGroup>>()

    @Synchronized fun create(images: List<MediaGroup>): String {
        val id = java.util.UUID.randomUUID().toString()
        lists[id] = images.toList()
        return id
    }

    @Synchronized fun get(id: String): List<MediaGroup>? = lists[id]
    @Synchronized fun update(id: String, images: List<MediaGroup>) { if (lists.containsKey(id)) lists[id] = images.toList() }
    @Synchronized fun release(id: String) { lists.remove(id) }
    @Synchronized fun clear() { lists.clear() }
}
