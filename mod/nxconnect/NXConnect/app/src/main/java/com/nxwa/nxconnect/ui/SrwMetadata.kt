// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import org.json.JSONObject
import java.io.File

object SrwMetadata {
    private fun changes(json: String): Map<String, String?> = JSONObject(json).let { data ->
        data.keys().asSequence().associateWith { key -> if (data.isNull(key)) null else data.getString(key) }
    }
    fun write(file: File, json: String) = SrwExifEditor.write(file, changes(json))
    fun verify(file: File, json: String) = SrwExifEditor.verify(file, changes(json))
}
