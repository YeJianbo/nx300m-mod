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

/**
 * 极小 JSON 解析器（纯 JVM，无依赖）。
 *
 * 为什么不用 `org.json`：它是 Android 平台类，在 JVM 单元测试里被桩掉（返回默认值），
 * 于是**协议解析逻辑实际上从未被测试执行过**——这是实测发现的问题。
 * 换成自带实现后，同一份代码在真机与单元测试里行为一致，且可被真实断言覆盖。
 *
 * 相机端返回的都是结构良好的 JSON（dirlist / camera info / status / capdtm），
 * 所以这里只实现 RFC 8259 的常规子集：对象、数组、字符串（含转义与 \uXXXX）、
 * 数字、true/false/null。不支持注释与尾随逗号（相机也不会发）。
 */
object MiniJson {

    /** 解析失败时抛这个异常，调用方据此降级 */
    class JsonException(message: String, cause: Throwable? = null) : Exception(message, cause)

    fun parse(text: String): Any? {
        val p = Parser(text)
        p.skipWs()
        val v = p.parseValue()
        p.skipWs()
        return v
    }

    fun parseObject(text: String): Map<String, Any?> {
        return when (val v = parse(text)) {
            is Map<*, *> -> v.entries.associate { (k, value) -> k.toString() to value }
            else -> throw JsonException("顶层不是对象：${text.take(80)}")
        }
    }

    // ------------------------------------------------------------ 取值助手

    @Suppress("UNCHECKED_CAST")
    fun obj(v: Any?): Map<String, Any?>? = v as? Map<String, Any?>

    fun arr(v: Any?): List<Any?>? = v as? List<Any?>

    fun str(m: Map<String, Any?>, key: String): String? = m[key] as? String

    fun int(m: Map<String, Any?>, key: String): Int? = when (val v = m[key]) {
        is Int -> v
        is Long -> v.toInt()
        is Double -> v.toInt()
        is String -> v.toIntOrNull()
        else -> null
    }

    fun bool(m: Map<String, Any?>, key: String): Boolean? = when (val v = m[key]) {
        is Boolean -> v
        is String -> v.equals("true", true)
        else -> null
    }

    /** 按候选键顺序取字符串，返回第一个非空值 */
    fun strAny(m: Map<String, Any?>, vararg keys: String): String? =
        keys.firstNotNullOfOrNull { k -> str(m, k)?.takeIf { it.isNotBlank() && it != "null" } }

    /** 按候选键顺序取整数，返回第一个有效值 */
    fun intAny(m: Map<String, Any?>, vararg keys: String): Int? =
        keys.firstNotNullOfOrNull { k -> int(m, k) }

    /** 按候选键顺序取布尔，返回第一个有效值 */
    fun boolAny(m: Map<String, Any?>, vararg keys: String): Boolean? =
        keys.firstNotNullOfOrNull { k -> bool(m, k) }

    /** 字符串数组（元素非字符串时跳过） */
    fun stringList(v: Any?): List<String> = arr(v)?.mapNotNull { it as? String } ?: emptyList()

    // ---------------------------------------------------------------- 解析器

    private class Parser(private val s: String) {
        private var i = 0

        fun skipWs() {
            while (i < s.length && s[i].isWhitespace()) i++
        }

        fun parseValue(): Any? {
            skipWs()
            if (i >= s.length) throw JsonException("输入意外结束")
            return when (s[i]) {
                '{' -> parseObjectBody()
                '[' -> parseArrayBody()
                '"' -> parseString()
                't' -> literal("true", true)
                'f' -> literal("false", false)
                'n' -> literal("null", null)
                else -> parseNumber()
            }
        }

        private fun literal(word: String, value: Any?): Any? {
            if (!s.startsWith(word, i)) {
                throw JsonException("位置 $i 处期望 $word，实际：${s.substring(i, minOf(s.length, i + 8))}")
            }
            i += word.length
            return value
        }

        private fun parseObjectBody(): Map<String, Any?> {
            expect('{')
            val out = LinkedHashMap<String, Any?>()
            skipWs()
            if (peek() == '}') {
                i++
                return out
            }
            while (true) {
                skipWs()
                val key = parseString()
                skipWs()
                expect(':')
                out[key] = parseValue()
                skipWs()
                when (val c = peek()) {
                    ',' -> i++
                    '}' -> {
                        i++
                        return out
                    }

                    else -> throw JsonException("对象里位置 $i 处出现意外字符 '$c'")
                }
            }
        }

        private fun parseArrayBody(): List<Any?> {
            expect('[')
            val out = ArrayList<Any?>()
            skipWs()
            if (peek() == ']') {
                i++
                return out
            }
            while (true) {
                out.add(parseValue())
                skipWs()
                when (val c = peek()) {
                    ',' -> i++
                    ']' -> {
                        i++
                        return out
                    }

                    else -> throw JsonException("数组里位置 $i 处出现意外字符 '$c'")
                }
            }
        }

        private fun parseString(): String {
            expect('"')
            val sb = StringBuilder()
            while (true) {
                if (i >= s.length) throw JsonException("字符串未闭合")
                when (val c = s[i]) {
                    '"' -> {
                        i++
                        return sb.toString()
                    }

                    '\\' -> {
                        i++
                        if (i >= s.length) throw JsonException("转义符后意外结束")
                        when (val e = s[i]) {
                            '"' -> sb.append('"')
                            '\\' -> sb.append('\\')
                            '/' -> sb.append('/')
                            'b' -> sb.append('\b')
                            'f' -> sb.append('\u000C')
                            'n' -> sb.append('\n')
                            'r' -> sb.append('\r')
                            't' -> sb.append('\t')
                            'u' -> {
                                if (i + 4 >= s.length) throw JsonException("\\u 转义不完整")
                                val hex = s.substring(i + 1, i + 5)
                                val code = hex.toIntOrNull(16)
                                    ?: throw JsonException("非法 \\u 转义：$hex")
                                sb.append(code.toChar())
                                i += 4
                            }

                            else -> throw JsonException("未知转义 \\$e")
                        }
                        i++
                    }

                    else -> {
                        sb.append(c)
                        i++
                    }
                }
            }
        }

        private fun parseNumber(): Any {
            val start = i
            if (peek() == '-' || peek() == '+') i++
            while (i < s.length && (s[i].isDigit() || s[i] == '.' || s[i] == 'e' || s[i] == 'E' ||
                    s[i] == '-' || s[i] == '+')
            ) {
                i++
            }
            val raw = s.substring(start, i)
            if (raw.isEmpty()) throw JsonException("位置 $start 处期望一个值")
            return raw.toLongOrNull()?.let { if (it in Int.MIN_VALUE..Int.MAX_VALUE) it.toInt() else it }
                ?: raw.toDoubleOrNull()
                ?: throw JsonException("非法数字：$raw")
        }

        private fun peek(): Char = if (i < s.length) s[i] else '\u0000'

        private fun expect(c: Char) {
            if (peek() != c) throw JsonException("位置 $i 处期望 '$c'，实际 '${peek()}'")
            i++
        }
    }
}
