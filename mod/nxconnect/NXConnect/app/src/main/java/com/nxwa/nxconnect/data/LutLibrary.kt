// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import android.content.Context
import android.util.AtomicFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.util.UUID

/** Phone-local masters and display order. A local entry is never a camera installation. */
class LutLibrary(context: Context) {
    data class Entry(val id: String, val label: String, val filename: String, val grid: Int,
                     val importedAt: Long, val domainMin: List<Double>, val domainMax: List<Double>)
    private val directory = File(context.filesDir, "lut-library").apply { mkdirs() }
    private val index = AtomicFile(File(directory, "index.json"))

    @Synchronized fun list(): List<Entry> {
        if (!index.baseFile.exists() && !File(directory, "index.json.bak").exists()) return emptyList()
        val data = JSONObject(index.openRead().bufferedReader(Charsets.UTF_8).use { it.readText() })
        require(data.getInt("version") == 1) { "不支持的 LUT 库版本" }
        val rows = data.getJSONArray("entries")
        return (0 until rows.length()).map { i ->
            val row = rows.getJSONObject(i)
            val id = row.getString("id")
            require(Regex("user-[a-f0-9-]{36}").matches(id)) { "LUT 库条目无效" }
            fun vector(key: String) = row.getJSONArray(key).let { v ->
                require(v.length() == 3) { "LUT 颜色范围无效" }
                (0..2).map { v.getDouble(it) }
            }
            Entry(id, row.getString("label"), row.getString("filename"), row.getInt("grid"),
                row.getLong("importedAt"), vector("domainMin"), vector("domainMax"))
        }.also { entries -> require(entries.map { it.id }.distinct().size == entries.size) { "LUT 库 ID 重复" } }
    }

    @Synchronized fun import(input: InputStream, filename: String): Entry {
        val entries = list()
        require(entries.size < 256) { "手机 LUT 库最多保存 256 项，请先删除不需要的项目" }
        val id = "user-" + UUID.randomUUID().toString()
        val target = File(directory, "$id.cube")
        try {
            input.use { incoming ->
                target.outputStream().use { outgoing ->
                    val buffer = ByteArray(65536)
                    var total = 0L
                    while (true) {
                        val count = incoming.read(buffer)
                        if (count < 0) break
                        total += count
                        require(total <= 64L * 1024 * 1024) { "LUT 文件不能超过 64 MB" }
                        outgoing.write(buffer, 0, count)
                    }
                    outgoing.fd.sync()
                }
            }
            val info = target.reader(Charsets.UTF_8).use(CubeLut::inspect)
            val label = (info.title?.takeIf { it.isNotBlank() } ?: filename.substringBeforeLast('.', filename))
                .replace(Regex("[\\p{Cntrl}|]"), " ").trim().take(80).ifEmpty { "未命名 LUT" }
            val entry = Entry(id, label, filename, info.grid, System.currentTimeMillis(), info.domainMin, info.domainMax)
            save(entries + entry)
            return entry
        } catch (error: Throwable) {
            target.delete()
            throw error
        }
    }

    @Synchronized fun rename(id: String, label: String) {
        val name = label.trim()
        require(name.isNotEmpty() && name.length <= 80 && name.none { it.isISOControl() || it == '|' }) { "名称须为 1–80 个字符，不能包含换行或竖线" }
        val entries = list()
        require(entries.any { it.id == id }) { "LUT 已不存在" }
        save(entries.map { if (it.id == id) it.copy(label = name) else it })
    }

    @Synchronized fun delete(id: String) {
        val entries = list()
        require(entries.any { it.id == id }) { "LUT 已不存在" }
        save(entries.filterNot { it.id == id })
        File(directory, "$id.cube").delete()
    }

    @Synchronized fun move(id: String, direction: Int) {
        val entries = list().toMutableList()
        val from = entries.indexOfFirst { it.id == id }
        require(from >= 0 && direction in listOf(-1, 1)) { "LUT 已不存在" }
        val to = (from + direction).coerceIn(0, entries.lastIndex)
        if (from != to) { val item = entries.removeAt(from); entries.add(to, item); save(entries) }
    }

    @Synchronized fun sort(byName: Boolean) {
        val entries = list()
        save(if (byName) entries.sortedBy { it.label.lowercase(java.util.Locale.ROOT) }
             else entries.sortedByDescending { it.importedAt })
    }

    @Synchronized fun source(id: String): File {
        require(list().any { it.id == id }) { "LUT 已不存在" }
        return File(directory, "$id.cube").also { check(it.isFile) { "LUT 原文件已丢失，请重新导入" } }
    }

    private fun save(entries: List<Entry>) {
        val rows = JSONArray()
        entries.forEach { entry -> rows.put(JSONObject().apply {
            put("id", entry.id); put("label", entry.label); put("filename", entry.filename)
            put("grid", entry.grid); put("importedAt", entry.importedAt)
            put("domainMin", JSONArray(entry.domainMin)); put("domainMax", JSONArray(entry.domainMax))
        }) }
        val data = JSONObject().put("version", 1).put("entries", rows).toString().toByteArray(Charsets.UTF_8)
        val stream = index.startWrite()
        try { stream.write(data); index.finishWrite(stream) }
        catch (error: Throwable) { index.failWrite(stream); throw error }
    }
}
