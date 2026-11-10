// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import android.webkit.MimeTypeMap
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/** Direct, permission-scoped access to nx300m-mod on a removable SD card. */
class CardLutLibrary private constructor(
    private val context: Context,
    val treeUri: Uri,
) {
    data class Look(
        val id: String,
        val label: String,
        val directoryPresent: Boolean,
        val groups: List<CardLutFormat.Group>,
        val favorite: Boolean,
        val isBlend: Boolean,
    )

    data class Snapshot(
        val rootLabel: String,
        val looks: List<Look>,
        val groups: List<CardLutFormat.Group>,
        val favorites: Set<String>,
        val protectedId: String,
    ) {
        fun count(groupId: String) = looks.count { look -> look.groups.any { it.id == groupId } }
    }

    data class ImportResult(val id: String, val label: String, val grid: Int)

    data class BlendSource(val id: String, val label: String, val grid: Int)

    data class BlendResult(val id: String, val label: String, val grid: Int)

    data class BlendInput(
        val id: String,
        val label: String,
        val data: CubeLut.Data,
        val snapshot: ByteArray,
    )

    private data class BuiltBlend(
        val cube: ByteArray,
        val direct: ByteArray,
        val composed: ByteArray,
        val metadata: org.json.JSONObject,
        val recipe: org.json.JSONObject,
        val sourceA: ByteArray,
        val sourceB: ByteArray,
        val reference: ByteArray?,
    )

    private val resolver: ContentResolver = context.contentResolver
    private val selectedUri: Uri
    private val selectedName: String
    private val moduleUri: Uri
    private val moduleName: String
    private val looksUri: Uri
    private val looksTxtUri: Uri
    private val stateUri: Uri
    private val backups = File(context.filesDir, "card-lut-backups").apply { mkdirs() }
    private val childrenCache = HashMap<String, List<Doc>>()

    private data class Doc(val id: String, val name: String, val mime: String, val size: Long?)

    private data class Originals(
        val catalog: ByteArray,
        val favorites: ByteArray?,
        val groupsJson: ByteArray?,
        val groupsCamera: ByteArray?,
    )

    init {
        val treeId = DocumentsContract.getTreeDocumentId(treeUri)
        selectedUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, treeId)
        selectedName = document(selectedUri).name
        val direct = selectedName == MODULE
        moduleUri = if (direct) selectedUri else
            child(selectedUri, MODULE, DocumentsContract.Document.MIME_TYPE_DIR)
                ?: error("所选目录中没有 $MODULE。请选择 SD 卡根目录或 $MODULE 文件夹")
        moduleName = document(moduleUri).name
        looksUri = child(moduleUri, "looks", DocumentsContract.Document.MIME_TYPE_DIR)
            ?: error("$moduleName 缺少 looks 文件夹")
        looksTxtUri = child(moduleUri, "looks.txt", "text/plain")
            ?: error("$moduleName 缺少 looks.txt")
        stateUri = child(moduleUri, ".state", DocumentsContract.Document.MIME_TYPE_DIR)
            ?: createDirectory(moduleUri, ".state")
    }

    fun refresh(): Snapshot {
        // SAF also changes from another app instance or desktop, so refresh must bypass prior listings.
        childrenCache.clear()
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val favorites = CardLutFormat.parseFavorites(readTextOrNull(stateFileOrNull("lut-favorites")), rows)
        val groups = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
        val dirs = children(looksUri).filter { it.mime == DocumentsContract.Document.MIME_TYPE_DIR }
            .mapTo(HashSet(), Doc::name)
        val byId = groups.groups.associateBy(CardLutFormat.Group::id)
        val looks = rows.map { row ->
            Look(row.id, row.label, row.id in dirs,
                groups.members[row.id].orEmpty().mapNotNull(byId::get), row.id in favorites,
                row.id.startsWith("blend-"))
        }
        return Snapshot(moduleName, looks, groups.groups, favorites, BASE_ID)
    }

    fun exportMaster(id: String, target: Uri) {
        CardLutFormat.validateLookId(id)
        require(id != BASE_ID) { "基础 LUT 不支持导出" }
        val directory = requiredLookDirectory(id)
        val source = child(directory, "source.cube", "text/plain")
            ?: error("此 LUT 没有保留 source.cube；可导出生成表或重新导入原始 .cube")
        resolver.openInputStream(source)?.use { input ->
            resolver.openOutputStream(target, "wt")?.use { output -> input.copyTo(output) }
                ?: error("无法写入所选位置")
        } ?: error("无法读取 LUT 原文件")
    }

    fun blendSources(): List<BlendSource> {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        return rows.mapNotNull { row ->
            val directory = child(looksUri, row.id, DocumentsContract.Document.MIME_TYPE_DIR) ?: return@mapNotNull null
            val source = child(directory, "source.cube", "text/plain") ?: return@mapNotNull null
            val metadata = child(directory, "look.json", "application/json")?.let { uri ->
                runCatching { org.json.JSONObject(readText(uri, MAX_TEXT_BYTES)) }.getOrNull()
            } ?: return@mapNotNull null
            if (metadata.optString("source_kind") == "blend-linear-srgb") return@mapNotNull null
            if (metadata.optString("input_space") != "srgb" ||
                metadata.optString("output_space") != "srgb") return@mapNotNull null
            BlendSource(row.id, row.label, metadata.optInt("source_grid", 0))
        }
    }

    fun createBlend(sourceA: String?, sourceB: String?, amountB: Double, label: String,
                    referenceImage: ByteArray? = null,
                    onProgress: (String) -> Unit = {}): BlendResult {
        onProgress("读取 A/B 源 LUT")
        val a = loadBlendSource(sourceA)
        val b = loadBlendSource(sourceB)
        return createBlendFromInputs(a, b, amountB, label, referenceImage, onProgress)
    }

    fun createBlendFromInputs(a: BlendInput, b: BlendInput, amountB: Double, label: String,
                              referenceImage: ByteArray? = null,
                              onProgress: (String) -> Unit = {}): BlendResult {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        require(rows.size < 256) { "相机目录已达 256 项上限" }
        val normalized = CardLutFormat.validateLookLabel(label)
        val id = "blend-" + UUID.randomUUID().toString().replace("-", "").take(16)
        val built = buildBlend(id, normalized, a, b, amountB, referenceImage, onProgress)
        val oldCatalog = readBytes(looksTxtUri, MAX_TEXT_BYTES)
        val oldGroupsJson = readBytesOrNull(stateFileOrNull("lut-groups.json"))
        val oldGroupsCamera = readBytesOrNull(stateFileOrNull("lut-groups.txt"))
        backupText("blend", "looks.txt", oldCatalog)
        val directory = createDirectory(looksUri, id)
        try {
            writeBlendPackage(directory, built, onProgress)
            val next = rows + CardLutFormat.Row(id, normalized,
                listOf(id, normalized, "0", "0", "0", "0", "0", "0", "0"))
            onProgress("更新并回读 looks.txt")
            writeVerified(looksTxtUri, CardLutFormat.catalogText(next).toByteArray(Charsets.UTF_8))
            var groups = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
            val selfMade = groups.groups.firstOrNull { it.label == "自制" }
                ?: CardLutFormat.Group(CardLutFormat.createGroupId(), "自制").also { created ->
                    require(groups.groups.size < 32) { "相机端最多支持 32 个自定义分类" }
                    groups = groups.copy(groups = groups.groups + created)
                    writeGroups(groups, "lut-blend-group-create")
                }
            val members = LinkedHashMap(groups.members).also { it[id] = listOf(selfMade.id) }
            writeGroups(groups.copy(members = members), "lut-blend-group-assign")
        } catch (error: Throwable) {
            restore(looksTxtUri, oldCatalog)
            val groupsJson = child(stateUri, "lut-groups.json", "application/json")
            val groupsCamera = child(stateUri, "lut-groups.txt", "text/plain")
            if (oldGroupsJson == null) {
                groupsJson?.let { runCatching { DocumentsContract.deleteDocument(resolver, it) } }
            } else if (groupsJson != null) writeVerified(groupsJson, oldGroupsJson)
            if (oldGroupsCamera == null) {
                groupsCamera?.let { runCatching { DocumentsContract.deleteDocument(resolver, it) } }
            } else if (groupsCamera != null) writeVerified(groupsCamera, oldGroupsCamera)
            try { DocumentsContract.deleteDocument(resolver, directory) } catch (_: Exception) { }
            invalidate(looksUri)
            throw error
        }
        invalidate(looksUri)
        refresh()
        return BlendResult(id, normalized, CubeLut.BLEND_GRID)
    }

    fun updateBlend(id: String, amountB: Double, label: String,
                    referenceImage: ByteArray? = null,
                    onProgress: (String) -> Unit = {}): BlendResult {
        val normalized = CardLutFormat.validateLookLabel(label)
        val directory = requiredLookDirectory(id)
        val recipeFile = child(directory, "blend.json", "application/json")
            ?: error("该 LUT 没有可编辑的混合配方")
        val recipe = org.json.JSONObject(readText(recipeFile, MAX_TEXT_BYTES))
        val previousMetadata = child(directory, "look.json", "application/json")?.let { uri ->
            org.json.JSONObject(readText(uri, MAX_TEXT_BYTES))
        }
        require(recipe.optString("algorithm") == CubeLut.BLEND_ALGORITHM) { "混合算法版本不受支持" }
        val sources = child(directory, "sources", DocumentsContract.Document.MIME_TYPE_DIR)
            ?: error("混合配方缺少源快照")
        val aFile = child(sources, "a.cube", "text/plain") ?: error("缺少 A 源快照")
        val bFile = child(sources, "b.cube", "text/plain") ?: error("缺少 B 源快照")
        val aBytes = readBytes(aFile, MAX_CUBE_BYTES)
        val bBytes = readBytes(bFile, MAX_CUBE_BYTES)
        val aId = recipe.getJSONObject("a").optString("id", "__original__")
        val bId = recipe.getJSONObject("b").optString("id", "__original__")
        val a = BlendInput(aId, recipe.getJSONObject("a").optString("label", "A"),
            aBytes.inputStream().reader(Charsets.UTF_8).use(CubeLut::read), aBytes)
        val b = BlendInput(bId, recipe.getJSONObject("b").optString("label", "B"),
            bBytes.inputStream().reader(Charsets.UTF_8).use(CubeLut::read), bBytes)
        val built = buildBlend(id, normalized, a, b, amountB, referenceImage, onProgress)
        previousMetadata?.optString("created_at")?.takeIf { it.isNotBlank() }?.let {
            built.metadata.put("created_at", it)
        }
        built.metadata.put("updated_at", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.ROOT).format(Date()))
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val index = rows.indexOfFirst { it.id == id }
        require(index >= 0) { "混合风格已不在 LUT 目录中" }
        val oldCatalog = readBytes(looksTxtUri, MAX_TEXT_BYTES)
        val old = mutableMapOf<String, ByteArray?>()
        for (name in listOf("source.cube", "table.d4lut", "table-stock3d.d4lut", "look.json", "blend.json", "reference.jpg")) {
            old[name] = child(directory, name, "")?.let { readBytesOrNull(it) }
        }
        backupText("blend-edit", "looks.txt", oldCatalog)
        val backup = newBackup("blend-edit-$id")
        copyDocument(directory, File(backup, id))
        try {
            onProgress("替换并回读混合成品")
            writeNew(directory, "source.cube", built.cube)
            writeNew(directory, "table.d4lut", built.direct)
            writeNew(directory, "table-stock3d.d4lut", built.composed)
            writeNew(directory, "blend.json", (built.recipe.toString(2) + "\n").toByteArray(Charsets.UTF_8))
            writeNew(directory, "look.json", (built.metadata.toString(2) + "\n").toByteArray(Charsets.UTF_8))
            built.reference?.let { writeNew(directory, "reference.jpg", it) }
            val nextRows = rows.toMutableList().also { it[index] = it[index].withLabel(normalized) }
            writeVerified(looksTxtUri, CardLutFormat.catalogText(nextRows).toByteArray(Charsets.UTF_8))
        } catch (error: Throwable) {
            restore(looksTxtUri, oldCatalog)
            for ((name, data) in old) {
                if (data != null) {
                    val uri = child(directory, name, "") ?: createFile(directory, name, "application/octet-stream")
                    writeVerified(uri, data)
                }
            }
            throw error
        }
        return BlendResult(id, normalized, CubeLut.BLEND_GRID).also { refresh() }
    }

    fun blendRecipe(id: String): org.json.JSONObject {
        val directory = requiredLookDirectory(id)
        val recipe = child(directory, "blend.json", "application/json")
            ?: error("该 LUT 没有可编辑的混合配方")
        return org.json.JSONObject(readText(recipe, MAX_TEXT_BYTES))
    }

    fun blendInputs(sourceA: String?, sourceB: String?): Pair<CubeLut.Data, CubeLut.Data> {
        val a = loadBlendSource(sourceA)
        val b = loadBlendSource(sourceB)
        return Pair(CubeLut.resample(a.data), CubeLut.resample(b.data))
    }

    fun snapshotBlendInputs(id: String): Pair<BlendInput, BlendInput> {
        val directory = requiredLookDirectory(id)
        val recipe = blendRecipe(id)
        val sources = child(directory, "sources", DocumentsContract.Document.MIME_TYPE_DIR)
            ?: error("混合配方缺少源快照")
        val aFile = child(sources, "a.cube", "text/plain") ?: error("缺少 A 源快照")
        val bFile = child(sources, "b.cube", "text/plain") ?: error("缺少 B 源快照")
        val aBytes = readBytes(aFile, MAX_CUBE_BYTES)
        val bBytes = readBytes(bFile, MAX_CUBE_BYTES)
        val a = BlendInput(recipe.getJSONObject("a").optString("id", "original"),
            recipe.getJSONObject("a").optString("label", "A"),
            aBytes.inputStream().reader(Charsets.UTF_8).use(CubeLut::read), aBytes)
        val b = BlendInput(recipe.getJSONObject("b").optString("id", "original"),
            recipe.getJSONObject("b").optString("label", "B"),
            bBytes.inputStream().reader(Charsets.UTF_8).use(CubeLut::read), bBytes)
        return Pair(a, b)
    }

    fun setFavorites(ids: Set<String>, favorite: Boolean): Snapshot {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val known = rows.mapTo(HashSet(), CardLutFormat.Row::id)
        require(ids.isNotEmpty() && ids.all { it in known }) { "所选 LUT 已发生变化，请刷新" }
        val previous = CardLutFormat.parseFavorites(readTextOrNull(stateFileOrNull("lut-favorites")), rows).toMutableSet()
        if (favorite) previous += ids else previous -= ids
        val text = CardLutFormat.favoriteText(previous, rows)
        val old = readBytesOrNull(stateFileOrNull("lut-favorites"))
        backupText("favorite", "lut-favorites", old)
        writeVerified(stateFile("lut-favorites", create = true), text.toByteArray(Charsets.UTF_8))
        val actual = CardLutFormat.parseFavorites(readTextOrNull(stateFileOrNull("lut-favorites")), rows)
        if (ids.any { (it in actual) != favorite }) {
            restoreOptional(stateFile("lut-favorites"), old)
            error("收藏写入后回读不一致")
        }
        return refresh()
    }

    fun assignGroups(ids: Set<String>, groupIds: Set<String>): Snapshot {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val state = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
        val knownLooks = rows.mapTo(HashSet(), CardLutFormat.Row::id)
        val knownGroups = state.groups.mapTo(HashSet(), CardLutFormat.Group::id)
        require(ids.isNotEmpty() && ids.all { it in knownLooks }) { "所选 LUT 已发生变化，请刷新" }
        require(groupIds.all { it in knownGroups }) { "所选分类已发生变化，请刷新" }
        val members = LinkedHashMap(state.members)
        ids.forEach { id ->
            val ordered = state.groups.map(CardLutFormat.Group::id).filter { it in groupIds }
            if (ordered.isEmpty()) members.remove(id) else members[id] = ordered
        }
        writeGroups(state.copy(members = members), "lut-group-assign")
        return refresh()
    }

    fun createGroup(label: String): Snapshot {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val state = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
        val normalized = CardLutFormat.validateGroupLabel(label)
        require(state.groups.none { it.label.equals(normalized, ignoreCase = true) }) { "已有同名分类" }
        require(state.groups.size < 32) { "相机端最多支持 32 个自定义分类" }
        writeGroups(state.copy(groups = state.groups + CardLutFormat.Group(CardLutFormat.createGroupId(), normalized)),
            "lut-group-create")
        return refresh()
    }

    fun renameGroup(groupId: String, label: String): Snapshot {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val state = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
        val normalized = CardLutFormat.validateGroupLabel(label)
        require(state.groups.any { it.id == groupId }) { "分类已不存在，请刷新" }
        require(state.groups.none { it.id != groupId && it.label.equals(normalized, ignoreCase = true) }) { "已有同名分类" }
        writeGroups(state.copy(groups = state.groups.map {
            if (it.id == groupId) it.copy(label = normalized) else it
        }), "lut-group-rename")
        return refresh()
    }

    fun deleteGroup(groupId: String): Snapshot {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val state = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
        require(state.groups.any { it.id == groupId }) { "分类已不存在，请刷新" }
        val members = LinkedHashMap<String, List<String>>()
        state.members.forEach { (id, selected) ->
            val remaining = selected - groupId
            if (remaining.isNotEmpty()) members[id] = remaining
        }
        writeGroups(state.copy(groups = state.groups.filterNot { it.id == groupId }, members = members),
            "lut-group-delete")
        return refresh()
    }

    fun renameLook(id: String, label: String): Snapshot {
        require(id != BASE_ID) { "原图微调是相机基准项，不能删除或改名" }
        val normalized = CardLutFormat.validateLookLabel(label)
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val index = rows.indexOfFirst { it.id == id }
        require(index >= 0) { "LUT 已不存在，请刷新" }
        val directory = requiredLookDirectory(id)
        val metadataUri = child(directory, "look.json", "application/json")
        val oldCatalog = readBytes(looksTxtUri, MAX_TEXT_BYTES)
        val oldMetadata = metadataUri?.let { readBytesOrNull(it) }
        backupText("rename", "looks.txt", oldCatalog)
        val nextRows = rows.toMutableList().also { it[index] = it[index].withLabel(normalized) }
        try {
            if (metadataUri != null && oldMetadata != null) {
                val root = org.json.JSONObject(String(oldMetadata, Charsets.UTF_8)).put("label", normalized)
                writeVerified(metadataUri, (root.toString(2) + "\n").toByteArray(Charsets.UTF_8))
            }
            writeVerified(looksTxtUri, CardLutFormat.catalogText(nextRows).toByteArray(Charsets.UTF_8))
        } catch (error: Throwable) {
            restore(metadataUri, oldMetadata)
            restore(looksTxtUri, oldCatalog)
            throw error
        }
        return refresh()
    }

    /** Copies the complete look directory to app-private backup before unlinking it. */
    fun deleteLook(id: String): Snapshot {
        require(id != BASE_ID) { "原图微调是相机基准项，不能删除或改名" }
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        require(rows.any { it.id == id }) { "LUT 已不存在，请刷新" }
        val lookDir = requiredLookDirectory(id)
        val favorites = CardLutFormat.parseFavorites(readTextOrNull(stateFileOrNull("lut-favorites")), rows) - id
        val groups = CardLutFormat.parseGroups(readTextOrNull(stateFileOrNull("lut-groups.json")), rows)
        val members = LinkedHashMap(groups.members).also { it.remove(id) }
        val nextRows = rows.filterNot { it.id == id }
        val old = originals()
        val backup = newBackup("delete-$id")
        copyDocument(lookDir, File(backup, id))
        try {
            writeVerified(looksTxtUri, CardLutFormat.catalogText(nextRows).toByteArray(Charsets.UTF_8))
            writeVerified(stateFile("lut-favorites", create = true),
                CardLutFormat.favoriteText(favorites, nextRows).toByteArray(Charsets.UTF_8))
            writeGroupsRaw(groups.copy(members = members))
        } catch (error: Throwable) {
            restoreOriginals(old)
            throw error
        }
        try { DocumentsContract.deleteDocument(resolver, lookDir) }
        catch (_: Exception) {
            try { DocumentsContract.renameDocument(resolver, lookDir, ".removed-$id-${stamp()}") }
            catch (_: Exception) { /* The row is gone; the private backup remains authoritative. */ }
        }
        invalidate(looksUri)
        val actual = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        check(actual.none { it.id == id }) { "删除后目录回读仍有此 LUT" }
        return refresh()
    }

    fun importCube(input: InputStream, filename: String, onProgress: (String) -> Unit = {}): ImportResult {
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        require(rows.size < 256) { "相机目录已达 256 项上限" }
        val temp = File.createTempFile("nx-lut-", ".cube", context.cacheDir)
        try {
            onProgress("读取 .cube")
            input.use { incoming -> temp.outputStream().use { outgoing ->
                val buffer = ByteArray(64 * 1024)
                var total = 0L
                while (true) {
                    val count = incoming.read(buffer)
                    if (count < 0) break
                    total += count
                    require(total <= MAX_CUBE_BYTES) { "LUT 文件不能超过 64 MB" }
                    outgoing.write(buffer, 0, count)
                }
            } }
            onProgress("解析 .cube 格点")
            val data = temp.reader(Charsets.UTF_8).use(CubeLut::read)
            onProgress("已解析 ${data.grid}×${data.grid}×${data.grid} 格点")
            val label = CardLutFormat.validateLookLabel(
                data.title?.takeIf(String::isNotBlank)?.trim()
                    ?: filename.substringBeforeLast('.', filename).ifBlank { "导入 LUT" })
            onProgress("检查原厂 9×9×9 基准表")
            val baseline = child(requiredLookDirectory(BASE_ID), "table-stock3d.d4lut", "application/octet-stream")
                ?: error("卡内缺少原厂 3D 基准表，不能生成可用的 Still 组合表")
            val stock = readBytes(baseline, 4380)
            validateD4(stock, "原厂 3D 基准表")
            onProgress("转换直接表和原厂组合表")
            val direct = CubeLut.compileD4(data)
            val composed = CubeLut.compileD4(data, stock)
            val source = temp.readBytes()
            val id = "import-" + UUID.randomUUID().toString().replace("-", "").take(16)
            backupText("import", "looks.txt", readBytes(looksTxtUri, MAX_TEXT_BYTES))
            val directory = createDirectory(looksUri, id)
            try {
                onProgress("写入并回读 source.cube")
                writeNew(directory, "source.cube", source)
                onProgress("写入并回读直接 9×9×9 表")
                writeNew(directory, "table.d4lut", direct)
                onProgress("写入并回读原厂组合表")
                writeNew(directory, "table-stock3d.d4lut", composed)
                val metadata = org.json.JSONObject()
                    .put("id", id).put("label", label).put("source_kind", "user-cube")
                    .put("input_space", "srgb").put("output_space", "srgb")
                    .put("master", "source.cube").put("native_table", "table.d4lut")
                    .put("native_format", org.json.JSONObject()
                        .put("grid", 9).put("channels", 3).put("bits", 10).put("bytes", 4380))
                    .put("calibration", org.json.JSONObject().put("camera", "NX300M").put("status", "unmeasured"))
                    .put("runtime_status", "compiled-not-loaded")
                    .put("native_stock_3d_table", "table-stock3d.d4lut")
                    .put("compiler", org.json.JSONObject().put("method", "trilinear-lattice")
                        .put("source_grid", data.grid)
                        .put("tables", org.json.JSONObject()
                            .put("table.d4lut", sha256(direct))
                            .put("table-stock3d.d4lut", sha256(composed))))
                onProgress("写入并回读元数据")
                writeNew(directory, "look.json", (metadata.toString(2) + "\n").toByteArray(Charsets.UTF_8))
                val next = rows + CardLutFormat.Row(id, label, listOf(id, label, "0", "0", "0", "0", "0", "0", "0"))
                onProgress("更新并回读 looks.txt")
                writeVerified(looksTxtUri, CardLutFormat.catalogText(next).toByteArray(Charsets.UTF_8))
            } catch (error: Throwable) {
                try { DocumentsContract.deleteDocument(resolver, directory) } catch (_: Exception) { }
                invalidate(looksUri)
                throw error
            }
            invalidate(looksUri)
            return ImportResult(id, label, data.grid)
        } finally {
            temp.delete()
        }
    }

    fun preparedBlendInput(id: String?): BlendInput = loadBlendSource(id)

    private fun loadBlendSource(id: String?): BlendInput {
        if (id.isNullOrBlank() || id == "__original__") {
            val identity = CubeLut.identity(title = "原图")
            return BlendInput("__original__", "原图", identity, cubeBytes(identity))
        }
        val rows = CardLutFormat.parseCatalog(readText(looksTxtUri, MAX_TEXT_BYTES))
        val row = rows.firstOrNull { it.id == id } ?: error("混合源已不存在，请刷新")
        val directory = requiredLookDirectory(id)
        val source = child(directory, "source.cube", "text/plain")
            ?: error("“${row.label}”没有保留 source.cube，不能参与混合")
        val metadata = child(directory, "look.json", "application/json")?.let { uri ->
            org.json.JSONObject(readText(uri, MAX_TEXT_BYTES))
        } ?: error("“${row.label}”缺少元数据")
        require(metadata.optString("source_kind") != "blend-linear-srgb") {
            "首版不允许把已混合风格继续当作输入源"
        }
        require(metadata.optString("input_space") == "srgb" &&
            metadata.optString("output_space") == "srgb") {
            "“${row.label}”未明确声明 sRGB 输入和输出域"
        }
        val snapshot = readBytes(source, MAX_CUBE_BYTES)
        val data = snapshot.inputStream().reader(Charsets.UTF_8).use(CubeLut::read)
        CubeLut.requireCanonical(data)
        return BlendInput(id, row.label, data, snapshot)
    }

    private fun buildBlend(id: String, label: String, a: BlendInput,
                           b: BlendInput, amountB: Double, referenceImage: ByteArray?,
                           onProgress: (String) -> Unit): BuiltBlend {
        require(amountB.isFinite() && amountB in 0.0..1.0) { "混合比例须在 0–100%" }
        onProgress("在统一 33×33×33 网格采样")
        val blended = CubeLut.blend(a.data, b.data, amountB, title = label)
        val cube = cubeBytes(blended)
        onProgress("检查原厂 9×9×9 基准表")
        val baseline = child(requiredLookDirectory(BASE_ID), "table-stock3d.d4lut", "application/octet-stream")
            ?: error("卡内缺少原厂 3D 基准表，不能生成可用的 Still 组合表")
        val stock = readBytes(baseline, 4380)
        validateD4(stock, "原厂 3D 基准表")
        require(sha256(stock) == STOCK_3D_SHA256) { "原厂 3D 基准表不匹配" }
        onProgress("编译直接表和原厂组合表")
        val direct = CubeLut.compileD4(blended, roundNearest = true)
        val composed = CubeLut.compileD4(blended, stock, roundNearest = true)
        val recipe = org.json.JSONObject()
            .put("schema", 1)
            .put("mode", "linear")
            .put("algorithm", CubeLut.BLEND_ALGORITHM)
            .put("working_space", CubeLut.BLEND_WORKING_SPACE)
            .put("grid", CubeLut.BLEND_GRID)
            .put("amount_b", amountB)
            .put("a", org.json.JSONObject()
                .put("id", a.id).put("label", a.label).put("original", a.id == "__original__")
                .put("content_sha256", sha256(a.snapshot)).put("snapshot", "sources/a.cube")
                .put("source_grid", a.data.grid))
            .put("b", org.json.JSONObject()
                .put("id", b.id).put("label", b.label).put("original", b.id == "__original__")
                .put("content_sha256", sha256(b.snapshot)).put("snapshot", "sources/b.cube")
                .put("source_grid", b.data.grid))
            .put("output_sha256", sha256(cube))
        if (referenceImage != null) recipe.put("reference", "reference.jpg")
        val metadata = org.json.JSONObject()
            .put("id", id).put("label", label).put("source_kind", "blend-linear-srgb")
            .put("description", "两款 sRGB LUT 按比例生成的独立风格")
            .put("input_space", "srgb").put("output_space", "srgb")
            .put("source_grid", CubeLut.BLEND_GRID).put("source_sha256", sha256(cube))
            .put("master", "source.cube").put("recipe", "blend.json")
            .put("blend_recipe", recipe)
            .put("native_table", "table.d4lut")
            .put("native_stock_3d_table", "table-stock3d.d4lut")
            .put("native_format", org.json.JSONObject()
                .put("grid", 9).put("channels", 3).put("bits", 10).put("bytes", 4380))
            .put("calibration", org.json.JSONObject().put("camera", "NX300M").put("status", "unmeasured"))
            .put("runtime_status", "compiled-not-loaded")
            .put("created_at", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.ROOT).format(Date()))
            .put("compiler", org.json.JSONObject().put("method", "round")
                .put("tables", org.json.JSONObject()
                    .put("table.d4lut", sha256(direct))
                    .put("table-stock3d.d4lut", sha256(composed))))
        return BuiltBlend(cube, direct, composed, metadata, recipe, a.snapshot, b.snapshot, referenceImage)
    }

    private fun cubeBytes(data: CubeLut.Data): ByteArray {
        val output = ByteArrayOutputStream()
        output.writer(Charsets.UTF_8).use { writer -> CubeLut.writeCube(data, writer) }
        return output.toByteArray()
    }

    private fun writeBlendPackage(directory: Uri, built: BuiltBlend,
                                  onProgress: (String) -> Unit) {
        onProgress("写入并回读 source.cube")
        writeNew(directory, "source.cube", built.cube)
        onProgress("写入并回读 9×9×9 直接表")
        writeNew(directory, "table.d4lut", built.direct)
        onProgress("写入并回读原厂组合表")
        writeNew(directory, "table-stock3d.d4lut", built.composed)
        onProgress("写入并回读源快照")
        val sources = createDirectory(directory, "sources")
        writeNew(sources, "a.cube", built.sourceA)
        writeNew(sources, "b.cube", built.sourceB)
        writeNew(directory, "blend.json", (built.recipe.toString(2) + "\n").toByteArray(Charsets.UTF_8))
        onProgress("写入并回读混合配方")
        writeNew(directory, "look.json", (built.metadata.toString(2) + "\n").toByteArray(Charsets.UTF_8))
        built.reference?.let { writeNew(directory, "reference.jpg", it) }
    }

    private fun writeGroups(state: CardLutFormat.GroupState, operation: String) {
        backupText(operation, "lut-groups.json", readBytesOrNull(stateFileOrNull("lut-groups.json")))
        writeGroupsRaw(state)
    }

    private fun writeGroupsRaw(state: CardLutFormat.GroupState) {
        val oldJson = readBytesOrNull(stateFileOrNull("lut-groups.json"))
        val oldCamera = readBytesOrNull(stateFileOrNull("lut-groups.txt"))
        val json = CardLutFormat.groupsJson(state).toByteArray(Charsets.UTF_8)
        val camera = CardLutFormat.cameraGroupsText(state).toByteArray(Charsets.UTF_8)
        try {
            writeVerified(stateFile("lut-groups.json", create = true), json)
            writeVerified(stateFile("lut-groups.txt", create = true), camera)
        } catch (error: Throwable) {
            restoreOptional(stateFile("lut-groups.json"), oldJson)
            restoreOptional(stateFile("lut-groups.txt"), oldCamera)
            throw error
        }
    }

    private fun originals() = Originals(
        readBytes(looksTxtUri, MAX_TEXT_BYTES),
        readBytesOrNull(stateFileOrNull("lut-favorites")),
        readBytesOrNull(stateFileOrNull("lut-groups.json")),
        readBytesOrNull(stateFileOrNull("lut-groups.txt")),
    )

    private fun restoreOriginals(old: Originals) {
        restore(looksTxtUri, old.catalog)
        restoreOptional(stateFileOrNull("lut-favorites"), old.favorites)
        restoreOptional(stateFileOrNull("lut-groups.json"), old.groupsJson)
        restoreOptional(stateFileOrNull("lut-groups.txt"), old.groupsCamera)
    }

    private fun stateFile(name: String, create: Boolean = false): Uri {
        val mime = MIME_TYPE_MAP.getMimeTypeFromExtension(name.substringAfterLast('.', "txt")) ?: "text/plain"
        return child(stateUri, name, mime)
            ?: if (create) createFile(stateUri, name, mime) else
                throw NoSuchElementException("卡上缺少 .state/$name")
    }

    private fun stateFileOrNull(name: String): Uri? {
        val mime = MIME_TYPE_MAP.getMimeTypeFromExtension(name.substringAfterLast('.', "txt")) ?: "text/plain"
        val fallback = when {
            name.endsWith(".json") -> "$name.txt"
            '.' !in name -> "$name.txt"
            else -> null
        }
        return child(stateUri, name, mime) ?: fallback?.let { child(stateUri, it, mime) }
    }

    private fun requiredLookDirectory(id: String): Uri =
        child(looksUri, id, DocumentsContract.Document.MIME_TYPE_DIR)
            ?: error("卡上缺少 looks/$id")

    private fun document(uri: Uri): Doc = resolver.query(uri, DOC_COLUMNS, null, null, null)?.use { cursor ->
        require(cursor.moveToFirst()) { "无法读取所选目录" }
        Doc(cursor.getString(0), cursor.getString(1), cursor.getString(2),
            if (cursor.isNull(3)) null else cursor.getLong(3))
    } ?: error("系统文件选择器没有返回目录信息")

    private fun children(parent: Uri): List<Doc> {
        childrenCache[parent.toString()]?.let { return it }
        val id = DocumentsContract.getDocumentId(parent)
        val uri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, id)
        return (resolver.query(uri, DOC_COLUMNS, null, null, null)?.use { cursor ->
            buildList {
                while (cursor.moveToNext()) add(Doc(cursor.getString(0), cursor.getString(1), cursor.getString(2),
                    if (cursor.isNull(3)) null else cursor.getLong(3)))
            }
        } ?: emptyList()).also { childrenCache[parent.toString()] = it }
    }

    private fun child(parent: Uri, name: String, expectedMime: String, type: String = ""): Uri? {
        val matches = children(parent).filter { it.name == name }
        if (matches.isEmpty()) return null
        val doc = matches.firstOrNull { type.isEmpty() || it.mime == type } ?: matches.first()
        if (type.isNotEmpty() && doc.mime != type) error("$name 不是预期目录")
        return DocumentsContract.buildDocumentUriUsingTree(treeUri, doc.id)
    }

    private fun createDirectory(parent: Uri, name: String): Uri =
        (DocumentsContract.createDocument(resolver, parent, DocumentsContract.Document.MIME_TYPE_DIR, name)
            ?: error("无法在卡上创建目录 $name")).also { invalidate(parent) }

    private fun createFile(parent: Uri, name: String, mime: String): Uri {
        val created = DocumentsContract.createDocument(resolver, parent, mime, name)
            ?: error("无法在卡上创建 $name")
        invalidate(parent)
        val named = if (document(created).name == name) created else
            DocumentsContract.renameDocument(resolver, created, name)
                ?: error("卡不支持把新建文件命名为 $name")
        invalidate(parent)
        check(document(named).name == name) { "创建后文件名不一致：$name" }
        return named
    }

    private fun writeNew(parent: Uri, name: String, data: ByteArray) {
        val uri = child(parent, name, "") ?: createFile(parent, name, MIME_TYPE_MAP.getMimeTypeFromExtension(name.substringAfterLast('.', "bin")) ?: "application/octet-stream")
        writeVerified(uri, data)
    }

    private fun writeVerified(uri: Uri, data: ByteArray) {
        resolver.openOutputStream(uri, "wt")?.use { stream ->
            stream.write(data)
            stream.flush()
        } ?: error("无法写入卡上文件")
        check(readBytes(uri, maxOf(MAX_TEXT_BYTES, data.size + 1L)).contentEquals(data)) { "写入后回读不一致" }
    }

    private fun readText(uri: Uri, limit: Long): String = String(readBytes(uri, limit), Charsets.UTF_8)
    private fun readTextOrNull(uri: Uri?): String? = readBytesOrNull(uri)?.toString(Charsets.UTF_8)

    private fun readBytesOrNull(uri: Uri?): ByteArray? {
        if (uri == null || documentOrNull(uri) == null) return null
        return readBytes(uri, MAX_FILE_BYTES)
    }

    private fun documentOrNull(uri: Uri): Doc? = resolver.query(uri, DOC_COLUMNS, null, null, null)?.use {
        if (it.moveToFirst()) Doc(it.getString(0), it.getString(1), it.getString(2),
            if (it.isNull(3)) null else it.getLong(3)) else null
    }

    private fun readBytes(uri: Uri, limit: Long): ByteArray = resolver.openInputStream(uri)?.use { input ->
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(64 * 1024)
        var total = 0L
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            total += count
            require(total <= limit) { "卡上文件超过允许大小" }
            output.write(buffer, 0, count)
        }
        output.toByteArray()
    } ?: error("无法读取卡上文件")

    private fun backupText(operation: String, name: String, data: ByteArray?) {
        val directory = newBackup(operation)
        if (data == null) File(directory, "$name.missing.txt").writeText("missing before operation\n")
        else checkedWrite(File(directory, name), data)
    }

    private fun newBackup(operation: String): File {
        val safe = operation.replace(Regex("[^A-Za-z0-9_-]+"), "_").take(48)
        return File(backups, "${stamp()}-$safe-${UUID.randomUUID().toString().take(6)}").apply {
            check(mkdirs() || isDirectory) { "无法创建手机端备份" }
        }
    }

    private fun copyDocument(uri: Uri, target: File): Long {
        val doc = document(uri)
        if (doc.mime == DocumentsContract.Document.MIME_TYPE_DIR) {
            require(target.mkdirs() || target.isDirectory)
            var total = 0L
            children(uri).forEach { item -> total += copyDocument(
                DocumentsContract.buildDocumentUriUsingTree(treeUri, item.id), File(target, item.name)) }
            return total
        }
        target.parentFile?.mkdirs()
        val total = resolver.openInputStream(uri)?.use { input -> target.outputStream().use { input.copyTo(it) } }
            ?: error("无法备份 ${doc.name}")
        check(target.length() == total) { "备份 ${doc.name} 后大小不一致" }
        return total
    }

    private fun checkedWrite(file: File, data: ByteArray) {
        file.parentFile?.mkdirs()
        file.outputStream().use { it.write(data) }
        check(file.readBytes().contentEquals(data)) { "手机端备份回读不一致" }
    }

    private fun restore(uri: Uri?, data: ByteArray?) { if (uri != null && data != null) writeVerified(uri, data) }
    private fun restoreOptional(uri: Uri?, data: ByteArray?) { if (uri != null && data != null) writeVerified(uri, data) }

    private fun invalidate(parent: Uri) { childrenCache.remove(parent.toString()) }

    private fun validateD4(data: ByteArray, name: String) {
        require(data.size == 4380) { "$name 必须为 4380 字节" }
        val words = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
        for (index in 0 until 2190) {
            val value = words.short.toInt() and 0xffff
            require(value <= 1023) { "$name 含超过 10 bit 的值" }
            if (index % 730 == 729) require(value == 0) { "$name 填充字不为零" }
        }
    }

    private fun sha256(data: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(data)
        .joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun stamp(): String = SimpleDateFormat("yyyyMMdd'T'HHmmssSSS", Locale.ROOT).format(Date())

    companion object {
        private const val MODULE = "nx300m-mod"
        private const val BASE_ID = "nx300-adjust-base"
        private const val MAX_TEXT_BYTES = 4L * 1024 * 1024
        private const val MAX_CUBE_BYTES = 64L * 1024 * 1024
        private const val MAX_FILE_BYTES = 256L * 1024 * 1024
        private const val STOCK_3D_SHA256 = "8e90e5a7528928585c86e4fe7e74d689ed96abd6cbb37f94bce234ddca09a07a"
        private val DOC_COLUMNS = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
        )
        private val MIME_TYPE_MAP = MimeTypeMap.getSingleton()

        fun open(context: Context, treeUri: Uri) = CardLutLibrary(context, treeUri)
    }
}
