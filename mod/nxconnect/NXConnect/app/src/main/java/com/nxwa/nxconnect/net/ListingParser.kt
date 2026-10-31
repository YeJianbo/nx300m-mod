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
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MediaEntry

/**
 * 解析 mongoose（daemon 的目录页）与 busybox httpd 的目录列表。
 *
 * mongoose 6.5 的真机输出是一张**带大小列的表格**（真机抓回来的原文，节选）：
 * ```html
 * <td><a href="100PHOTO/">100PHOTO/</a></td><td>17-Feb-2015 00:54</td><td name=-1>[DIRECTORY]</td>
 * ```
 * 文件行则是 `<td><a href="SAM_4282.JPG">SAM_4282.JPG</a></td><td>06-Feb-2026 02:43</td><td>7712754</td>`
 *
 * 这很关键：8080 的 `dirlist` **只给文件名、不给大小**，而相机里全是 20–34MB 的 RAW，
 * 用户点下载前必须知道要拉多少数据 —— 大小就从这里取。
 *
 * busybox httpd 的格式更简单（`<a href="name">name</a>` 加日期），没有大小列，
 * 所以解析器对两种格式都要能工作。
 */
object ListingParser {

    data class Entry(
        val name: String,
        val isDirectory: Boolean,
        val size: Long? = null,
        val modified: String? = null,
    )

    data class Parsed(
        val dirs: List<String>,
        val files: List<String>,
        /** 文件名 → 字节数（仅 mongoose 表格格式有） */
        val sizes: Map<String, Long>,
        /** 文件名 → 修改时间原文（如 "06-Feb-2026 02:43"） */
        val modified: Map<String, String>,
    ) {
        val isEmpty: Boolean get() = dirs.isEmpty() && files.isEmpty()
    }

    private val HREF = Regex("""href\s*=\s*["']?([^"'>\s]+)["']?""", RegexOption.IGNORE_CASE)
    private val ROW = Regex("""<tr[^>]*>(.*?)</tr>""", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
    private val CELL = Regex("""<td[^>]*>(.*?)</td>""", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))

    /**
     * 去标签。
     *
     * ★ 必须非贪婪：`<[^>]+>` 这种写法会从第一个 `<` 一路吃到**行内最后一个 `>`**，
     * 把整行内容吞掉（实测踩到过——大小与时间全部解析成 null）。
     */
    private val TAG = Regex("""<[^>]*?>""")

    /**
     * 解析目录页。
     * @param onlyMedia 为 true 时只保留媒体文件（照片/RAW/视频）
     */
    fun parse(html: String, onlyMedia: Boolean = true): Parsed? {
        if (!html.contains("<a ", ignoreCase = true)) return null

        val dirs = LinkedHashSet<String>()
        val files = LinkedHashSet<String>()
        val sizes = LinkedHashMap<String, Long>()
        val modified = LinkedHashMap<String, String>()

        // ---- 1) 表格格式（mongoose）：逐行取 链接 / 时间 / 大小 ----
        for (row in ROW.findAll(html)) {
            // ★ 顺序很重要：**先从原始单元格取 href，再去标签**。
            // 反过来做会把 `<a href="x">x</a>` 整段剥掉，href 永远取不到，
            // 结果就是"文件名能拿到（走兜底路径）但大小与时间全丢"（实测踩到过）。
            val rawCells = CELL.findAll(row.groupValues[1]).map { it.groupValues[1].trim() }.toList()
            if (rawCells.isEmpty()) continue

            val href = HREF.find(rawCells[0])?.groupValues?.get(1) ?: continue
            val info = resolve(href) ?: continue
            val name = info.first ?: continue

            val cells = rawCells.map { stripTags(it) }
            val isDir = info.second || cells.any { it.contains("[DIRECTORY]", ignoreCase = true) }
            if (isDir) {
                dirs += name
                continue
            }
            if (onlyMedia && !MediaEntry.isMedia(name)) continue

            files += name
            // 大小：找第一个纯数字单元格（[DIRECTORY] 与日期都会被排除）
            for ((i, cell) in cells.withIndex()) {
                if (i == 0) continue
                val value = cell.replace(",", "").trim()
                if (value.isNotEmpty() && value.all { it.isDigit() }) {
                    value.toLongOrNull()?.let { sizes[name] = it }
                    break
                }
            }
            // 时间：形如 06-Feb-2026 02:43
            cells.firstOrNull { DATE.containsMatchIn(it) }?.let { modified[name] = it }
        }

        // ---- 2) 无表格的简单格式（busybox httpd / mongoose 无脚本时）----
        if (dirs.isEmpty() && files.isEmpty()) {
            for (m in HREF.findAll(html)) {
                val info = resolve(m.groupValues[1]) ?: continue
                val name = info.first ?: continue
                if (info.second) dirs += name
                else if (!onlyMedia || MediaEntry.isMedia(name)) files += name
            }
        }

        if (dirs.isEmpty() && files.isEmpty()) return null
        return Parsed(
            dirs = dirs.sortedWith(compareByDescending<String> { MediaEntry.sequenceOf(it) ?: -1 }.thenByDescending { it }),
            files = files.sortedWith(compareByDescending<String> { MediaEntry.sequenceOf(it) ?: -1 }.thenByDescending { it }),
            sizes = sizes,
            modified = modified,
        )
    }

    /** 只取大小表（给已从 dirlist 拿到文件名列表的场景补大小用） */
    fun parseSizes(html: String): Map<String, Long> = parse(html, onlyMedia = false)?.sizes ?: emptyMap()

    private val DATE = Regex("""\d{1,2}-[A-Za-z]{3}-\d{4}""")

    /**
     * 把 href 归一化成 (文件名, 是否目录)。
     * 处理绝对 URL、绝对路径、相对路径、`./`、`../`、查询串与百分号编码。
     */
    private fun resolve(hrefRaw: String): Pair<String?, Boolean>? {
        var href = hrefRaw.trim()
        if (href.isEmpty()) return null
        // mongoose 表头有 <a href=# rel=0>Name</a> 这类排序锚点，必须排除
        if (href.startsWith("#")) return null
        if (href.startsWith("http://") || href.startsWith("https://")) {
            href = href.substringAfter("://").substringAfter('/', "")
        }
        if (href.contains('?') || href.contains('#')) return null
        if (href.startsWith("/api/")) return null

        val decoded = try {
            java.net.URLDecoder.decode(href, "UTF-8")
        } catch (e: Exception) {
            href
        }
        val isDir = decoded.endsWith("/")
        val name = decoded.trim('/').substringAfterLast('/')
        if (name.isEmpty() || name == "." || name == ".." || name.startsWith(".")) return null
        return Pair(name, isDir)
    }

    private fun stripTags(s: String): String = TAG.replace(s, "").replace("&nbsp;", " ").trim()
}
