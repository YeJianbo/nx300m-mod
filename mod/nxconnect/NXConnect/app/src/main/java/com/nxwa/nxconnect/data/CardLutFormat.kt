// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/** Pure format rules shared by the SD-card browser and camera-side files. */
object CardLutFormat {
    data class Row(val id: String, val label: String, val fields: List<String>) {
        fun withLabel(value: String) = copy(label = value, fields = fields.toMutableList().also { it[1] = value })
        fun line() = fields.joinToString("|")
    }

    data class Group(val id: String, val label: String)

    data class GroupState(
        val groups: List<Group> = emptyList(),
        val members: LinkedHashMap<String, List<String>> = LinkedHashMap(),
    )

    private val lookId = Regex("[A-Za-z0-9_-]{1,63}")
    private val groupId = Regex("group-[0-9a-f]{12}")

    fun validateLookId(id: String) {
        require(lookId.matches(id)) { "LUT ID 无效：$id" }
    }

    fun validateLookLabel(value: String): String {
        val label = value.trim()
        require(label.isNotEmpty()) { "名称不能为空" }
        require(label.toByteArray(Charsets.UTF_8).size <= 120) { "名称最多 120 字节" }
        require(label.none { it.isISOControl() || it == '|' }) { "名称不能包含竖线、换行或控制字符" }
        return label
    }

    fun validateGroupLabel(value: String): String {
        val label = value.trim()
        require(label.isNotEmpty()) { "分类名称不能为空" }
        require(label.toByteArray(Charsets.UTF_8).size <= 80) { "分类名称最多 80 字节" }
        require(label.none { it.isISOControl() || it == '|' }) { "分类名称不能包含竖线、换行或控制字符" }
        return label
    }

    fun parseCatalog(raw: String): List<Row> {
        val rows = raw.removePrefix("\uFEFF").lineSequence()
            .filter { it.isNotBlank() }
            .mapIndexed { index, line ->
                val fields = line.split('|')
                require(fields.size == 9) { "looks.txt 第 ${index + 1} 行不是 9 列" }
                require(fields.all(String::isNotEmpty)) { "looks.txt 第 ${index + 1} 行有空字段" }
                validateLookId(fields[0])
                validateLookLabel(fields[1])
                Row(fields[0], fields[1], fields)
            }.toList()
        require(rows.map(Row::id).distinct().size == rows.size) { "looks.txt 存在重复 LUT ID" }
        require(rows.size <= 256) { "looks.txt 超过相机上限 256 项" }
        return rows
    }

    fun catalogText(rows: List<Row>): String {
        require(rows.map(Row::id).distinct().size == rows.size) { "LUT ID 重复" }
        return rows.joinToString("\n", postfix = "\n", transform = Row::line)
    }

    fun parseGroups(raw: String?, rows: List<Row>): GroupState {
        if (raw.isNullOrBlank()) return GroupState()
        val root = JSONObject(raw)
        require(root.getInt("schema") == 1) { "LUT 分类文件版本不受支持" }
        val entries = root.getJSONArray("groups")
        val groups = ArrayList<Group>(entries.length())
        val ids = HashSet<String>()
        val labels = HashSet<String>()
        for (index in 0 until entries.length()) {
            val item = entries.getJSONObject(index)
            val id = item.getString("id")
            val label = validateGroupLabel(item.getString("label"))
            require(groupId.matches(id)) { "LUT 分类 ID 无效" }
            require(ids.add(id)) { "LUT 分类 ID 重复" }
            require(labels.add(label.lowercase(Locale.ROOT))) { "LUT 分类名称重复" }
            groups += Group(id, label)
        }
        require(groups.size <= 32) { "相机端最多支持 32 个自定义分类" }
        val knownLooks = rows.mapTo(HashSet(), Row::id)
        val knownGroups = ids
        val members = LinkedHashMap<String, List<String>>()
        val rawMembers = root.getJSONObject("members")
        rawMembers.keys().asSequence().forEach { id ->
            if (id !in knownLooks) return@forEach
            val selected = rawMembers.getJSONArray(id)
            val memberGroups = ArrayList<String>()
            for (index in 0 until selected.length()) {
                val group = selected.getString(index)
                require(group in knownGroups) { "LUT 分类归属引用不存在分类" }
                if (group !in memberGroups) memberGroups += group
            }
            val ordered = groups.map(Group::id).filter { it in memberGroups }
            if (ordered.isNotEmpty()) members[id] = ordered
        }
        return GroupState(groups, members)
    }

    fun groupsJson(state: GroupState): String {
        val root = JSONObject().put("schema", 1)
        val groups = JSONArray()
        state.groups.forEach { group ->
            groups.put(JSONObject().put("id", group.id).put("label", group.label))
        }
        root.put("groups", groups)
        val members = JSONObject()
        state.groups.map(Group::id).let { order ->
            state.members.forEach { (id, selected) ->
                val normalized = order.filter { it in selected }
                if (normalized.isNotEmpty()) members.put(id, JSONArray(normalized))
            }
        }
        root.put("members", members)
        return root.toString(2) + "\n"
    }

    fun cameraGroupsText(state: GroupState): String = buildString {
        appendLine("NXUTG1")
        state.groups.forEach { appendLine("G|${it.id}|${it.label}") }
        val groupOrder = state.groups.map(Group::id)
        state.members.forEach { (id, selected) ->
            val ordered = groupOrder.filter { it in selected }
            if (ordered.isNotEmpty()) appendLine("L|$id|${ordered.joinToString("|")}")
        }
    }

    fun favoriteText(selected: Set<String>, rows: List<Row>): String =
        rows.map(Row::id).filter { it in selected }.joinToString("\n", postfix = if (selected.isEmpty()) "" else "\n")

    fun parseFavorites(raw: String?, rows: List<Row>): Set<String> {
        if (raw.isNullOrBlank()) return emptySet()
        val known = rows.mapTo(HashSet(), Row::id)
        return raw.split(Regex("\\s+")).filter(String::isNotBlank).filterTo(LinkedHashSet()) { it in known }
    }

    fun createGroupId(): String =
        "group-" + java.util.UUID.randomUUID().toString().replace("-", "").take(12)
}
