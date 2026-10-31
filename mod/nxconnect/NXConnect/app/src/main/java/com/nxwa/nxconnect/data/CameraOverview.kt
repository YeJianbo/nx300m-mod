/*
 * NXConnectForNX500 — 三星 NX500 相机的照片传输与遥控 App
 * Copyright (C) 2026 Katsiy and contributors
 *
 * 本程序是自由软件：你可以依据 GNU Affero 通用公共许可证第 3 版
 * （或任何更新版本）的条款重新分发和/或修改它。
 * 本程序按"有用"的目的分发，但不提供任何担保。
 * 详见随附的 LICENSE 文件，或 <https://www.gnu.org/licenses/>。
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
package com.nxwa.nxconnect.data

/** 相机概览（主界面展示用）。字段拿不到时为 null，UI 会显示"未知"而不是瞎猜。 */
data class CameraOverview(
    val ip: String,
    // ---- 来自 daemon HTTP（可靠）----
    val model: String? = null,
    val firmware: String? = null,
    val mac: String? = null,
    val batteryLevel: Int? = null,
    val batteryPercent: Int? = null,
    val charging: Boolean? = null,
    val mode: String? = null,
    val drive: String? = null,
    // ---- 来自 shell（装了 mod 才有 telnet）----
    val cameraTime: String? = null,
    val timeDriftSeconds: Long? = null,
    val sdFreeText: String? = null,
    val sdTotalText: String? = null,
    val uptime: String? = null,
    // ---- 数据来源与限制说明 ----
    val shellAvailable: Boolean = false,
    val notes: List<String> = emptyList(),
) {
    /** 电量档位 0..4 → 可读文本 */
    val batteryText: String
        get() = when {
            batteryPercent != null && batteryPercent >= 0 -> "$batteryPercent%"
            batteryLevel != null -> "档位 $batteryLevel/4"
            else -> "未知"
        }

    val chargingText: String
        get() = when (charging) {
            true -> "充电中"
            false -> "电池供电"
            null -> ""
        }

    /** 时间偏差的可读描述 */
    val timeDriftText: String?
        get() = timeDriftSeconds?.let { d ->
            val abs = kotlin.math.abs(d)
            when {
                abs < 5 -> "已同步"
                abs < 60 -> "相差 ${abs}秒"
                abs < 3600 -> "相差 ${abs / 60}分钟"
                abs < 86400 -> "相差 ${abs / 3600}小时"
                else -> "相差 ${abs / 86400}天"
            }
        }

    val timeNeedsSync: Boolean
        get() = (timeDriftSeconds?.let { kotlin.math.abs(it) > 60 }) ?: false
}
