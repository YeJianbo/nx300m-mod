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

/** DCIM 下的一个目录（相机按 100PHOTO/101PHOTO… 递增命名） */
data class DcimFolder(
    val name: String,
    /** 该目录里媒体文件的数量；-1 表示尚未统计 */
    val mediaCount: Int = -1,
    val coverUrl: String? = null,
)

/** 一个媒体条目（照片 / 视频 / RAW） */
data class MediaEntry(
    val folder: String,
    val name: String,
    val size: Long = -1L,
    /** 相机端给的修改时间原文（如 "06-Feb-2026 02:43"）；来自 mongoose 目录页 */
    val modified: String? = null,
) {
    val ext: String get() = name.substringAfterLast('.', "").lowercase()

    val isVideo: Boolean get() = ext in VIDEO_EXTS

    /** 相机端 thumb-cgi 白名单内的普通图片格式 */
    val isPlainImage: Boolean get() = ext in IMAGE_EXTS

    /** RAW originals remain downloadable; supported cameras expose embedded JPEG previews. */
    val isRaw: Boolean get() = ext in RAW_EXTS

    val isImage: Boolean get() = isPlainImage || isRaw

    /** 人类可读大小；未知时返回 null */
    val sizeText: String?
        get() = formatSize(size)

    /** 给 UI 显示的短标签：RAW / MP4 / JPG */
    val kindLabel: String
        get() = when {
            isRaw -> "RAW"
            isVideo -> "视频"
            else -> ext.uppercase()
        }

    /** 占位格里给用户的下一步提示 */
    val placeholderHint: String
        get() = when {
            isRaw -> "点击可下载"
            isVideo -> "点击可下载"
            else -> ""
        }

    /** DCIM 相对路径，如 100PHOTO/SAM_0001.JPG —— dirlist/thumb CGI 用的就是这种形式 */
    val relPath: String get() = if (folder.isEmpty()) name else "$folder/$name"

    /** 原图下载 URL 路径（端口 80 daemon 的静态文件服务） */
    val filePath: String get() = "/DCIM/$relPath"

    companion object {
        /** 相机端 thumb-cgi 明确白名单内的格式 */
        val IMAGE_EXTS = setOf("jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff")

        /** RAW originals supported by the file download path. */
        val RAW_EXTS = setOf("srw", "dng", "cr2", "cr3", "nef", "arw", "sr2", "orf", "rw2", "raf", "pef", "nrw")

        val VIDEO_EXTS = setOf("mp4", "mov", "m4v", "avi", "mkv", "3gp")

        fun formatSize(bytes: Long): String? = when {
            bytes < 0 -> null
            bytes >= 1024L * 1024 * 1024 -> "%.2f GB".format(bytes / 1024.0 / 1024 / 1024)
            bytes >= 1024L * 1024 -> "%.1f MB".format(bytes / 1024.0 / 1024)
            bytes >= 1024 -> "%.0f KB".format(bytes / 1024.0)
            else -> "$bytes B"
        }

        /** dirlist 会把目录里所有带扩展名的文件都报出来，媒体过滤在客户端做 */
        fun isMedia(name: String): Boolean {
            val ext = name.substringAfterLast('.', "").lowercase()
            return ext in IMAGE_EXTS || ext in RAW_EXTS || ext in VIDEO_EXTS
        }

        /** 相机文件名的数字序号，用于"最新在前"的排序（非 SAM 命名时退化为字典序） */
        fun sequenceOf(name: String): Int? =
            Regex("""(\d{3,})""").find(name)?.groupValues?.get(1)?.toIntOrNull()
    }
}

/** 相机信息（/api/v1/camera/info 的宽松解析结果） */
data class CameraInfo(
    val model: String? = null,
    val firmware: String? = null,
    val mac: String? = null,
    val raw: String = "",
)

/**
 * 相机状态（`/api/v1/camera/status`）。
 *
 * 真机实测响应（NX500 / 1.12）：
 * ```json
 * {"battery_charging":false,"battery_percent":-1,"battery_level":3,"drive":"UNKNOWN",
 *  "mode":"UNKNOWN","hevc":"off","cameras":[{"ip":"192.168.5.39","packet":"NX_REMOTE|0.9|NX500|1.12|02:30:0d:00:00:01|"}]}
 * ```
 * 注意：**`battery_percent = -1` 是"未知"哨兵值**，不是真的 -1%，要报未知。
 * `drive`/`mode` 在相机不支持时也会返回字符串 `"UNKNOWN"`。
 */
data class CameraStatus(
    val batteryPercent: Int? = null,
    val batteryLevel: Int? = null,
    val charging: Boolean? = null,
    val mode: String? = null,
    val drive: String? = null,
    val hevc: String? = null,
    /** 相机通过 UDP 广播发现的其它相机（daemon 把结果透出在这里） */
    val cameras: List<String> = emptyList(),
    val raw: String = "",
)

/** dirlist CGI 的返回模型 */
data class DirListing(
    val path: String,
    val dirs: List<String>,
    val files: List<String>,
    val ok: Boolean = true,
    val err: String? = null,
    /** Actual file sizes from native dirlist or the legacy HTML directory page. */
    val sizes: Map<String, Long> = emptyMap(),
    /** 文件名 → 修改时间原文 */
    val modified: Map<String, String> = emptyMap(),
)
