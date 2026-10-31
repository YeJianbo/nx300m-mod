// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MiniJson

/** Only known wire layouts are accepted; response headers cannot allocate arbitrary canvases. */
enum class OsdGeometry(val width: Int, val height: Int = 480) {
    NX500(720), NX300M(800);
    val columns: Int get() = width / 80
    val blockCount: Int get() = columns * (height / 40)
}

data class CameraProfile(
    val geometry: OsdGeometry = OsdGeometry.NX500,
    val screenMirroring: Boolean = true,
    val sensorLiveview: Boolean = true,
    val lcdControl: Boolean = true,
    val silentShutter: Boolean = true,
    val keyInput: Boolean = true,
    val keepAlive: Boolean = true,
    val timeSync: Boolean = true,
    val shell: Boolean = true,
    val isoControls: Boolean = false,
    val shutterLimit: Boolean = false,
    val dialLock: Boolean = false,
    val colorStyle: Boolean = false,
    val pictureWizard: Boolean = false,
    val looks: Boolean = false,
    val lutSelection: Boolean = false,
    val galleryPreview: Boolean = false,
    val recipes: Boolean = false,
    val recipeStyle: Boolean = false,
    val galleryWriteback: Boolean = false,
    val galleryOriginalBackup: Boolean = false,
) {
    val screenOnly: Boolean get() = screenMirroring && !sensorLiveview
    val hasBodySettings: Boolean get() = isoControls || shutterLimit || dialLock || colorStyle || pictureWizard || looks || recipes || recipeStyle
    val hasStockMenu: Boolean get() = keyInput && screenMirroring
    val hasCameraSettings: Boolean get() = hasBodySettings || hasStockMenu

    companion object {
        fun fromInfo(raw: String): CameraProfile {
            val info = MiniJson.parseObject(raw)
            val nx300 = MiniJson.str(info, "model")?.contains("NX300", true) == true
            val caps = MiniJson.obj(info["capabilities"]) ?: emptyMap()
            val osd = MiniJson.obj(info["osd"]) ?: emptyMap()
            val geometry = if (nx300) OsdGeometry.NX300M else OsdGeometry.NX500
            // Negotiated geometry is bounded to supported devices and fixed 80x40 tiles.
            if (osd.isNotEmpty()) {
                require(MiniJson.int(osd, "width") == geometry.width &&
                    MiniJson.int(osd, "height") == geometry.height &&
                    MiniJson.int(osd, "block_width") == 80 && MiniJson.int(osd, "block_height") == 40) {
                    "相机屏幕布局与机型不匹配"
                }
            }
            fun cap(name: String, fallback: Boolean) = MiniJson.bool(caps, name) ?: fallback
            return CameraProfile(geometry,
                cap("screen_mirroring", true), cap("sensor_liveview", !nx300),
                cap("lcd_control", !nx300), cap("silent_shutter", !nx300),
                cap("key_input", true), cap("keep_alive", true),
                cap("time_sync", !nx300), cap("shell", !nx300),
                cap("iso_controls", false), cap("shutter_limit", false),
                cap("dial_lock", false), cap("color_style", false),
                cap("picture_wizard", false), cap("looks", false), cap("lut_selection", false),
                cap("gallery_preview", false), cap("recipes", false), cap("recipe_style", false),
                cap("gallery_writeback", false), cap("gallery_original_backup", false))
        }
    }
}
