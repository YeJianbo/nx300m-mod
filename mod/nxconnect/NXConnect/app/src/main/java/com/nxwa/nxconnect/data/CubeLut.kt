// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import java.io.Reader

/** Validate a standard 3D cube without retaining millions of RGB samples in memory. */
object CubeLut {
    data class Info(val grid: Int, val title: String?, val domainMin: List<Double>, val domainMax: List<Double>)

    fun inspect(reader: Reader): Info {
        var grid: Int? = null
        var title: String? = null
        var low: List<Double>? = null
        var high: List<Double>? = null
        var samples = 0
        val whitespace = Regex("\\s+")
        reader.buffered().useLines { lines ->
            lines.forEachIndexed { index, original ->
                require(original.length <= 4096) { "第 ${index + 1} 行过长" }
                val trimmed = original.removePrefix("\uFEFF").trim()
                if (trimmed.startsWith("TITLE ") || trimmed.startsWith("TITLE\t")) {
                    require(title == null && samples == 0) { "TITLE 重复或位置无效" }
                    title = Regex("^TITLE\\s+\"([^\"]*)\"\\s*(?:#.*)?$").matchEntire(trimmed)
                        ?.groupValues?.get(1) ?: error("第 ${index + 1} 行的 TITLE 无效")
                    return@forEachIndexed
                }
                val line = trimmed.substringBefore('#').trim()
                if (line.isEmpty()) return@forEachIndexed
                val parts = line.split(whitespace)
                fun vector(): List<Double> {
                    require(parts.size == 4) { "第 ${index + 1} 行需要三个数值" }
                    return parts.drop(1).map { it.toDoubleOrNull()?.takeIf(Double::isFinite)
                        ?: error("第 ${index + 1} 行含有无效数值") }
                }
                when (parts[0]) {
                    "LUT_1D_SIZE" -> error("暂不支持 1D LUT 或带 1D shaper 的文件")
                    "LUT_3D_SIZE" -> {
                        require(grid == null && samples == 0 && parts.size == 2) { "LUT_3D_SIZE 重复或位置无效" }
                        grid = parts[1].toIntOrNull()?.takeIf { it in 2..129 }
                            ?: error("3D LUT 网格须为 2–129")
                    }
                    "DOMAIN_MIN" -> {
                        require(low == null && samples == 0) { "DOMAIN_MIN 重复或位置无效" }
                        low = vector()
                    }
                    "DOMAIN_MAX" -> {
                        require(high == null && samples == 0) { "DOMAIN_MAX 重复或位置无效" }
                        high = vector()
                    }
                    else -> {
                        val size = grid ?: error("缺少 LUT_3D_SIZE")
                        require(parts.size == 3 && parts.all { value -> value.toDoubleOrNull()?.isFinite() == true }) {
                            "第 ${index + 1} 行不是有效的 RGB 节点"
                        }
                        samples++
                        require(samples <= size * size * size) { "LUT 节点数量超过网格大小" }
                    }
                }
            }
        }
        val size = grid ?: error("缺少 LUT_3D_SIZE")
        require(samples == size * size * size) { "LUT 节点不完整：需要 ${size * size * size}，实际 $samples" }
        val min = low ?: listOf(0.0, 0.0, 0.0)
        val max = high ?: listOf(1.0, 1.0, 1.0)
        require(min.indices.all { min[it] < max[it] }) { "DOMAIN_MIN 必须小于 DOMAIN_MAX" }
        return Info(size, title, min, max)
    }
}
