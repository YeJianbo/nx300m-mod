// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MiniJson

/** Values follow the stock UserData mapping and the native shutter-limit bridge. */
object ExposureControls {
    val ISO_STEPS = listOf("one-third", "one-stop")
    val STOCK_AUTO_ISO_MAX = listOf(125, 160, 200, 250, 320, 400, 500, 640, 800, 1000,
        1250, 1600, 2000, 2500, 3200, 6400)
    val AUTO_ISO_MAX = STOCK_AUTO_ISO_MAX + listOf(12800, 25600)
    val FASTEST = listOf("off", "1/60", "1/125", "1/250", "1/500", "1/1000",
        "1/2000", "1/4000", "1/6000", "1/8000")

    enum class IsoMode { AUTO, MANUAL, UNKNOWN }
    enum class MaxStatus { CONFIRMED, POLICY_LIMITED, MISMATCH, UNKNOWN }
    data class AePolicy(
        val available: Boolean?,
        val availableMask: Int?,
        val isoMode: IsoMode,
        val isoSetting: Int?,
        val currentIso: Int?,
        val maxIso: Int?,
        val iqMaxIso: Int?,
        val requestedMaxIso: Int?,
        val shootingMode: Int?,
        val currentSv16: Int?,
        val maxSv16: Int?,
        val policyMatchesRequested: Boolean?,
        val consumptionVerified: Boolean?,
        val aeSolverVerified: Boolean?,
        val physicalExposureVerified: Boolean?,
        val iqDriveMode: Int? = null,
        val aeCaptureMode: Int? = null,
    )
    data class IsoState(
        val step: String?, val expansion: Boolean?, val maximum: Int?,
        val aePolicy: AePolicy? = null,
        val supportedMaximums: List<Int> = STOCK_AUTO_ISO_MAX,
        val extendedReady: Boolean? = null,
    ) {
        val maxStatus: MaxStatus get() {
            val policy = aePolicy ?: return MaxStatus.UNKNOWN
            val requested = maximum ?: policy.requestedMaxIso
            return when {
                policy.policyMatchesRequested == true -> MaxStatus.CONFIRMED
                requested != null && policy.iqMaxIso == requested &&
                    policy.maxIso != null && policy.maxIso < requested -> MaxStatus.POLICY_LIMITED
                policy.policyMatchesRequested == false -> MaxStatus.MISMATCH
                else -> MaxStatus.UNKNOWN
            }
        }
    }
    data class RgbState(val red: Int, val green: Int, val blue: Int, val detail: String) {
        fun channels() = mapOf("red" to red, "green" to green, "blue" to blue)
    }
    data class PictureWizardState(
        val values: Map<String, Int?>,
        val iqMatches: Boolean,
        val scalarModeEnabled: Boolean,
        val iqMode: Int,
    )
    data class Look(val id: String, val label: String, val tableValid: Boolean, val selectable: Boolean)
    data class LooksState(
        val looks: List<Look>, val stock: Boolean, val selectionValid: Boolean,
        val selectedId: String?, val writable: Boolean, val stage: String,
        val forwarded: Boolean, val forwardedMask: Int?,
        val strength: Int = 100, val strengthSupported: Boolean = false,
    )
    val PICTURE_WIZARD_FIELDS = listOf("saturation", "sharpness", "contrast", "color_tone")
    val RGB_FIELDS = listOf("red", "green", "blue")
    private val lookId = Regex("[a-z0-9][a-z0-9_-]{0,62}")

    fun parseIso(raw: String): IsoState {
        val response = response(raw)
        val controls = MiniJson.obj(response["controls"]) ?: throw NxException("相机未返回 ISO 设置")
        fun item(name: String) = MiniJson.obj(controls[name])?.takeIf {
            MiniJson.bool(it, "value_mapping_verified") == true
        }
        val step = item("iso_step")?.let { MiniJson.str(it, "value") }?.takeIf { it in ISO_STEPS }
        val expansion = item("iso_expansion")?.get("value") as? Boolean
        val maxItem = item("auto_iso_max")
        // Older services expose only stock values. Extended values require an
        // explicit list from the camera's active bridge, not an App default.
        val supportedMaximums = if (maxItem?.containsKey("supported_values") == true)
            MiniJson.arr(maxItem["supported_values"])?.mapNotNull { integer(it) }
                ?.filter { it in AUTO_ISO_MAX }?.distinct()?.sorted() ?: emptyList()
        else STOCK_AUTO_ISO_MAX
        val maximum = integer(maxItem?.get("value"))?.takeIf { it in supportedMaximums }
        val policy = MiniJson.obj(response["ae_policy"])?.let { data ->
            val mask = integer(data["available_mask"])?.takeIf { it in 0..31 }
            // IQ setting/max/mode use bits 1/2/4; AE max/current Sv use bits 8/16.
            fun available(bit: Int) = mask != null && (mask and bit) == bit
            fun positive(name: String) = integer(data[name])?.takeIf { it > 0 }
            val requestedMax = positive("requested_max_iso")
            AePolicy(
                available = data["available"] as? Boolean ?: mask?.let { it != 0 },
                availableMask = mask,
                isoMode = if (!available(1)) IsoMode.UNKNOWN else when (MiniJson.str(data, "iso_mode")) {
                    "auto" -> IsoMode.AUTO
                    "manual" -> IsoMode.MANUAL
                    else -> IsoMode.UNKNOWN
                },
                isoSetting = if (available(1)) integer(data["iso_setting"])?.takeIf { it >= 0 } else null,
                currentIso = if (available(16)) positive("current_iso") else null,
                maxIso = if (available(8)) positive("max_iso") else null,
                iqMaxIso = if (available(2)) positive("iq_max_iso") else null,
                requestedMaxIso = requestedMax,
                shootingMode = if (available(4)) integer(data["shooting_mode"]) else null,
                currentSv16 = if (available(16)) integer(data["current_sv16"]) else null,
                maxSv16 = if (available(8)) integer(data["max_sv16"]) else null,
                policyMatchesRequested = if (available(10) && requestedMax != null)
                    data["policy_matches_requested"] as? Boolean else null,
                consumptionVerified = response["ae_policy_consumption_verified"] as? Boolean,
                aeSolverVerified = data["ae_solver_verified"] as? Boolean,
                physicalExposureVerified = data["physical_exposure_verified"] as? Boolean,
                iqDriveMode = if (available(4)) integer(data["iq_drive_mode"] ?: data["shooting_mode"]) else null,
                aeCaptureMode = integer(data["ae_capture_mode"])?.takeIf { it >= 0 },
            )
        }
        return IsoState(step, expansion, maximum, policy, supportedMaximums,
            maxItem?.get("extended_ready") as? Boolean)
    }

    fun parseShutter(raw: String): String {
        val value = MiniJson.str(response(raw), "fastest_target")
        return value?.takeIf { it in FASTEST } ?: throw NxException("相机返回的最快快门值无法识别")
    }

    fun parseDialLock(raw: String): Boolean = response(raw)["enabled"] as? Boolean
        ?: throw NxException("拨盘锁读回无效")

    fun parseRgb(raw: String): RgbState {
        val data = response(raw)
        fun channel(name: String) = integer(data[name])?.takeIf { it in 0..200 }
            ?: throw NxException("RGB $name 读回无效")
        fun matrix(prefix: String, label: String): String = when {
            integer(data["${prefix}_base_available"]) !in 0..1 -> "$label：原厂矩阵状态未知"
            integer(data["${prefix}_base_available"]) == 0 -> "$label：尚未收到原厂矩阵"
            integer(data["${prefix}_setter_result"]) == null -> "$label：原厂返回值未知"
            integer(data["${prefix}_setter_result"]) != 0 -> "$label：原厂返回 ${integer(data["${prefix}_setter_result"])}"
            integer(data["${prefix}_modified"]) == 1 -> "$label：已应用增益"
            integer(data["${prefix}_modified"]) != 0 -> "$label：增益状态未知"
            integer(data["active"]) == 1 -> "$label：未应用增益"
            integer(data["active"]) == 0 -> "$label：原厂矩阵"
            else -> "$label：增益状态未知"
        }
        return RgbState(channel("red"), channel("green"), channel("blue"),
            matrix("preview", "预览") + "\n" + matrix("capture", "拍摄"))
    }

    fun validatePictureWizard(name: String, value: Int) {
        require(name in PICTURE_WIZARD_FIELDS && value in if (name == "color_tone") -1..359 else -4..4) {
            "无效的原厂画面参数"
        }
    }

    fun parsePictureWizard(raw: String): PictureWizardState {
        val data = response(raw)
        val values = MiniJson.obj(data["values"]) ?: throw NxException("相机未返回原厂画面参数")
        val decoded = PICTURE_WIZARD_FIELDS.associateWith { name ->
            integer(values[name])?.takeIf { it in if (name == "color_tone") -1..359 else -4..4 }
        }
        return PictureWizardState(decoded,
            data["iq_matches"] as? Boolean ?: throw NxException("缺少 IQ 读回状态"),
            data["scalar_mode_enabled"] as? Boolean ?: throw NxException("缺少原厂模式状态"),
            integer(data["iq_mode"]) ?: throw NxException("原厂 IQ 模式无效"))
    }

    fun validateLookId(id: String) { require(lookId.matches(id)) { "无效的 LUT ID" } }

    fun parseLooks(raw: String): LooksState {
        val data = response(raw)
        val rows = MiniJson.arr(data["looks"]) ?: throw NxException("相机未返回已部署 LUT 列表")
        if (rows.size > 256) throw NxException("LUT 列表超出支持范围")
        val looks = rows.map { row ->
            val item = MiniJson.obj(row) ?: throw NxException("LUT 条目无效")
            val id = MiniJson.str(item, "id") ?: throw NxException("LUT 缺少 ID")
            validateLookId(id)
            Look(id, MiniJson.str(item, "label")?.takeIf { it.isNotBlank() } ?: id,
                item["table_valid"] as? Boolean ?: throw NxException("缺少 LUT 表状态"),
                item["selectable"] as? Boolean ?: throw NxException("缺少 LUT 可选状态"))
        }
        if (looks.map { it.id }.distinct().size != looks.size) throw NxException("LUT ID 重复")
        val selection = MiniJson.obj(data["selection"]) ?: throw NxException("缺少 LUT 当前选择")
        val hook = MiniJson.obj(data["hook"]) ?: throw NxException("缺少 LUT 桥状态")
        val status = MiniJson.obj(data["status"]) ?: throw NxException("缺少原厂 LUT 状态")
        val strengthSupported = data["strength_supported"] == true
        val strength = if (strengthSupported && selection["strength"] != null)
            integer(selection["strength"])?.takeIf { it in 0..100 }
                ?: throw NxException("LUT 强度读回无效")
        else 100
        return LooksState(looks,
            selection["stock"] as? Boolean ?: throw NxException("LUT 原厂选择状态无效"),
            selection["valid"] as? Boolean ?: throw NxException("LUT 当前选择状态无效"),
            MiniJson.str(selection, "id"),
            hook["ready"] as? Boolean ?: throw NxException("LUT 桥就绪状态无效"),
            MiniJson.str(status, "stage") ?: throw NxException("缺少 LUT 阶段"),
            status["forwarded"] as? Boolean ?: throw NxException("缺少 LUT 转发状态"),
            integer(status["forwarded_mask"]), strength, strengthSupported)
    }

    private fun integer(value: Any?): Int? = when (value) {
        is Int -> value
        is Long -> value.takeIf { it in Int.MIN_VALUE..Int.MAX_VALUE }?.toInt()
        else -> null
    }

    fun validateIso(name: String, value: Any): String = when (name) {
        "iso_step" -> (value as? String)?.takeIf { it in ISO_STEPS }
        "iso_expansion" -> (value as? Boolean)?.toString()
        "auto_iso_max" -> (value as? Int)?.takeIf { it in AUTO_ISO_MAX }?.toString()
        else -> null
    } ?: throw NxException("无效的 ISO 设置")

    private fun response(raw: String): Map<String, Any?> {
        val value = MiniJson.parseObject(raw)
        MiniJson.str(value, "error")?.let { throw NxException(it) }
        return value
    }
}
