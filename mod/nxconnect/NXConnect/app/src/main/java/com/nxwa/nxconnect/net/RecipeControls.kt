// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MiniJson
import java.io.IOException

/** The recipe bridge controls one seven-value batch and reports each stage separately. */
object RecipeControls {
    val FIELDS = listOf("red", "green", "blue", "hue", "saturation", "sharpness", "contrast")
    val LABELS = listOf("R", "G", "B", "H", "SAT", "SHARP", "CONTRAST")
    val STAGES = listOf("rgb", "chroma", "sharpness", "contrast")
    val STAGE_LABELS = listOf("RGB", "色相／饱和度", "锐度", "对比度")
    val NEUTRAL = listOf(100, 100, 100, 10, 10, 10, 10)
    data class Recipe(val id: String, val label: String, val values: List<Int>)
    data class Catalogue(val recipes: List<Recipe>, val ready: Boolean)
    data class Stage(val available: Boolean, val applied: Boolean, val error: Int)
    data class State(
        val generation: Long, val enabled: Boolean, val values: List<Int>,
        val previewMask: Int, val captureMask: Int, val superseded: Boolean,
        val preview: List<Stage>, val capture: List<Stage>,
        val stock: Boolean, val selectionMatches: Boolean, val selectedId: String?, val selectedLabel: String?,
        val bridgeResult: Int,
    ) {
        val initialStock: Boolean get() = stock && generation == 1L && previewMask == 0 && captureMask == 0
        val previewApplied: Boolean get() = previewMask == 15 && !superseded
        val captureApplied: Boolean get() = captureMask == 15 && !superseded
        val awaitingPreview: Boolean get() = !initialStock && !superseded && !previewApplied && preview.none { it.error != 0 }
    }
    class Failure(message: String, val readback: State? = null, val mutationMayHaveApplied: Boolean = false,
                  cause: Throwable? = null) : IOException(message, cause)

    fun describe(values: List<Int>): String = LABELS.zip(values).joinToString(" · ") { (label, value) -> "$label $value" }

    fun validateId(id: String) {
        require(id.length in 1..63 && id.all { it.code in 32..126 && it != '|' && it != '"' && it != '\\' }) { "配方 ID 无效" }
    }

    private fun integer(value: Any?): Int? = when (value) {
        is Int -> value
        is Long -> value.takeIf { it in Int.MIN_VALUE..Int.MAX_VALUE }?.toInt()
        else -> null
    }
    private fun bool(data: Map<String, Any?>, name: String): Boolean = data[name] as? Boolean
        ?: throw NxException("配方状态缺少 $name")
    private fun values(data: Map<String, Any?>): List<Int> {
        val values = MiniJson.obj(data["values"]) ?: throw NxException("缺少配方七值")
        return FIELDS.mapIndexed { index, key -> integer(values[key])?.takeIf { it in 0..(if (index < 3) 200 else 20) }
            ?: throw NxException("配方 $key 读回无效") }
    }

    fun parseCatalogue(raw: String): Catalogue {
        val data = MiniJson.parseObject(raw)
        MiniJson.str(data, "error")?.let { throw NxException(it) }
        val entries = MiniJson.arr(data["recipes"]) ?: throw NxException("缺少已部署配方目录")
        if (entries.size > 256) throw NxException("配方目录超出支持范围")
        val recipes = entries.map { row ->
            val item = MiniJson.obj(row) ?: throw NxException("配方条目无效")
            val id = MiniJson.str(item, "id") ?: throw NxException("缺少配方 ID")
            validateId(id)
            Recipe(id, MiniJson.str(item, "label")?.takeIf { it.isNotBlank() } ?: id, values(item))
        }
        if (recipes.map { it.id }.distinct().size != recipes.size) throw NxException("配方 ID 重复")
        return Catalogue(recipes, bool(data, "ready"))
    }

    fun parseState(raw: String): State {
        val data = MiniJson.parseObject(raw)
        MiniJson.str(data, "error")?.let { throw failure(raw, NxException(it)) }
        return state(data)
    }
    private fun state(data: Map<String, Any?>): State {
        val generation = when (val value = data["generation"]) { is Int -> value.toLong(); is Long -> value; else -> null }
            ?.takeIf { it in 1L..0xffffffffL }
            ?: throw NxException("配方代次无效")
        val enabled = bool(data, "enabled")
        val superseded = bool(data, "superseded")
        fun mask(name: String) = integer(data[name])?.takeIf { it in 0..15 } ?: throw NxException("配方 $name 无效")
        val previewMask = mask("preview_applied_mask")
        val captureMask = mask("capture_applied_mask")
        val stages = MiniJson.obj(data["stages"]) ?: throw NxException("缺少配方阶段状态")
        fun route(name: String, applied: Int): List<Stage> {
            val available = mask("${name}_available_mask")
            if ((applied and available) != applied) throw NxException("配方阶段掩码不一致")
            val route = MiniJson.obj(stages[name]) ?: throw NxException("缺少$name 配方阶段")
            return STAGES.mapIndexed { index, key ->
                val item = MiniJson.obj(route[key]) ?: throw NxException("缺少$name/$key 配方阶段")
                val bit = 1 shl index
                val result = Stage(bool(item, "available"), bool(item, "applied"), integer(item["error"])
                    ?: throw NxException("缺少$name/$key 原厂返回值"))
                if (result.available != ((available and bit) != 0) || result.applied != (!superseded && (applied and bit) != 0) ||
                    (result.error != 0 && result.applied)) throw NxException("配方$name/$key 阶段读回不一致")
                result
            }
        }
        val preview = route("preview", previewMask)
        val capture = route("capture", captureMask)
        val selection = MiniJson.obj(data["selection"]) ?: throw NxException("缺少当前配方选择")
        val stock = bool(selection, "stock")
        val matches = bool(selection, "matches")
        val id = MiniJson.str(selection, "id")?.also(::validateId)
        if (stock != (!enabled && !superseded) || (matches && (!enabled || superseded || id == null))) throw NxException("当前配方选择读回不一致")
        if (bool(data, "preview_applied") != (previewMask == 15 && !superseded) ||
            bool(data, "capture_applied") != (captureMask == 15 && !superseded)) throw NxException("配方应用状态不一致")
        return State(generation, enabled, values(data), previewMask, captureMask, superseded, preview, capture,
            stock, matches, id, MiniJson.str(selection, "label"), integer(data["bridge_result"]) ?: throw NxException("缺少配方批次结果"))
    }

    fun failure(raw: String?, cause: Exception, mutation: Boolean = false): Failure {
        val data = try { raw?.let(MiniJson::parseObject) } catch (_: Exception) { null }
        val readback = try { MiniJson.obj(data?.get("readback"))?.let(::state) } catch (_: Exception) { null }
        return Failure(data?.let { MiniJson.str(it, "error") } ?: cause.message ?: "无法读取配方响应", readback,
            data?.get("mutation_may_have_been_applied") as? Boolean ?: mutation, cause)
    }

    fun stageError(code: Int): String = when (code) {
        -110 -> "等待原厂输入（$code）"
        -111 -> "系数超出机身范围（$code）"
        -112 -> "当前模式不支持（$code）"
        -114 -> "需恢复原厂色彩调整（$code）"
        else -> "机身错误 $code"
    }
}
