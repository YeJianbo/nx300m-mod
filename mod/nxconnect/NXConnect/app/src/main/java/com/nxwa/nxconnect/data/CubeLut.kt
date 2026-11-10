// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import java.io.Reader
import java.io.Writer
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Locale

/** Validate a standard 3D cube without retaining millions of RGB samples in memory. */
object CubeLut {
    data class Info(val grid: Int, val title: String?, val domainMin: List<Double>, val domainMax: List<Double>)
    data class Data(val grid: Int, val title: String?, val domainMin: List<Double>, val domainMax: List<Double>,
                    val rgb: FloatArray)

    const val BLEND_GRID = 33
    const val BLEND_ALGORITHM = "linear-srgb-output-v1"
    const val BLEND_WORKING_SPACE = "sRGB encoded output RGB, numerically blended"

    private data class Parsed(
        val grid: Int,
        val title: String?,
        val domainMin: List<Double>,
        val domainMax: List<Double>,
        val rgb: FloatArray?,
    )

    private val titlePattern = Regex("^TITLE\\s+\"([^\"]*)\"\\s*(?:#.*)?$")

    fun inspect(reader: Reader): Info {
        val parsed = parse(reader, retainSamples = false)
        return Info(parsed.grid, parsed.title, parsed.domainMin, parsed.domainMax)
    }

    /** Retains the source samples so an SD-card import can produce the native 9^3 table. */
    fun read(reader: Reader): Data {
        val parsed = parse(reader, retainSamples = true)
        return Data(parsed.grid, parsed.title, parsed.domainMin, parsed.domainMax,
            parsed.rgb ?: error("LUT 节点为空"))
    }

    private fun parse(reader: Reader, retainSamples: Boolean): Parsed {
        val text = reader.buffered().use { it.readText() }
        var grid: Int? = null
        var title: String? = null
        var low: List<Double>? = null
        var high: List<Double>? = null
        var rgb: FloatArray? = null
        var samples = 0
        var lineStart = 0
        var lineNumber = 1
        while (lineStart <= text.length) {
            var lineEnd = lineStart
            while (lineEnd < text.length && text[lineEnd] != '\n' && text[lineEnd] != '\r') lineEnd++
            require(lineEnd - lineStart <= 4096) { "第 $lineNumber 行过长" }

            var cursor = skipWhitespace(text, lineStart, lineEnd)
            if (lineNumber == 1 && cursor < lineEnd && text[cursor] == '\uFEFF') {
                cursor = skipWhitespace(text, cursor + 1, lineEnd)
            }
            if (cursor < lineEnd && text[cursor] != '#') {
                val firstEnd = tokenEnd(text, cursor, lineEnd)
                when {
                    tokenEquals(text, cursor, firstEnd, "TITLE") -> {
                        require(title == null && samples == 0) { "TITLE 重复或位置无效" }
                        val comment = text.indexOf('#', firstEnd).takeIf { it in firstEnd until lineEnd } ?: lineEnd
                        val raw = text.substring(cursor, comment).trim()
                        title = titlePattern.matchEntire(raw)?.groupValues?.get(1)
                            ?: error("第 $lineNumber 行的 TITLE 无效")
                    }
                    tokenEquals(text, cursor, firstEnd, "LUT_1D_SIZE") ->
                        error("暂不支持 1D LUT 或带 1D shaper 的文件")
                    tokenEquals(text, cursor, firstEnd, "LUT_3D_SIZE") -> {
                        require(grid == null && samples == 0) { "LUT_3D_SIZE 重复或位置无效" }
                        val token = nextToken(text, firstEnd, lineEnd, lineNumber)
                        requireEndOfLine(text, token.second, lineEnd, lineNumber)
                        val parsed = parseInt(text, token.first, token.second, lineNumber)
                            .takeIf { it in 2..129 }
                            ?: error("3D LUT 网格须为 2–129")
                        grid = parsed
                        if (retainSamples) rgb = FloatArray(parsed * parsed * parsed * 3)
                    }
                    tokenEquals(text, cursor, firstEnd, "DOMAIN_MIN") -> {
                        require(low == null && samples == 0) { "DOMAIN_MIN 重复或位置无效" }
                        low = readVector(text, firstEnd, lineEnd, lineNumber)
                    }
                    tokenEquals(text, cursor, firstEnd, "DOMAIN_MAX") -> {
                        require(high == null && samples == 0) { "DOMAIN_MAX 重复或位置无效" }
                        high = readVector(text, firstEnd, lineEnd, lineNumber)
                    }
                    else -> {
                        val size = grid ?: error("缺少 LUT_3D_SIZE")
                        require(samples < size * size * size) { "LUT 节点数量超过网格大小" }
                        val values = rgb
                        var valueCursor = cursor
                        repeat(3) { channel ->
                            valueCursor = skipWhitespace(text, valueCursor, lineEnd)
                            require(valueCursor < lineEnd && text[valueCursor] != '#') {
                                "第 $lineNumber 行不是有效 RGB 节点"
                            }
                            val end = tokenEnd(text, valueCursor, lineEnd)
                            val value = parseFloat(text, valueCursor, end, lineNumber)
                            if (values != null) values[samples * 3 + channel] = value
                            valueCursor = end
                        }
                        requireEndOfLine(text, valueCursor, lineEnd, lineNumber)
                        samples++
                    }
                }
            }

            if (lineEnd == text.length) break
            if (text[lineEnd] == '\r' && lineEnd + 1 < text.length && text[lineEnd + 1] == '\n') lineEnd++
            lineStart = lineEnd + 1
            lineNumber++
        }
        val size = grid ?: error("缺少 LUT_3D_SIZE")
        require(samples == size * size * size) {
            "LUT 节点不完整：需要 ${size * size * size}，实际 $samples"
        }
        val min = low ?: listOf(0.0, 0.0, 0.0)
        val max = high ?: listOf(1.0, 1.0, 1.0)
        require(min.indices.all { min[it] < max[it] }) { "DOMAIN_MIN 必须小于 DOMAIN_MAX" }
        return Parsed(size, title, min, max, rgb)
    }

    private fun readVector(text: String, from: Int, end: Int, lineNumber: Int): List<Double> {
        val values = ArrayList<Double>(3)
        var cursor = from
        repeat(3) {
            cursor = skipWhitespace(text, cursor, end)
            require(cursor < end && text[cursor] != '#') { "第 $lineNumber 行需要三个数值" }
            val tokenEnd = tokenEnd(text, cursor, end)
            values += parseDouble(text, cursor, tokenEnd, lineNumber)
            cursor = tokenEnd
        }
        requireEndOfLine(text, cursor, end, lineNumber)
        return values
    }

    private fun nextToken(text: String, from: Int, end: Int, lineNumber: Int): Pair<Int, Int> {
        val start = skipWhitespace(text, from, end)
        require(start < end && text[start] != '#') { "第 $lineNumber 行缺少参数" }
        return start to tokenEnd(text, start, end)
    }

    private fun requireEndOfLine(text: String, from: Int, end: Int, lineNumber: Int) {
        val cursor = skipWhitespace(text, from, end)
        require(cursor == end || text[cursor] == '#') { "第 $lineNumber 行参数过多" }
    }

    private fun skipWhitespace(text: String, from: Int, end: Int): Int {
        var cursor = from
        while (cursor < end && text[cursor].isWhitespace()) cursor++
        return cursor
    }

    private fun tokenEnd(text: String, start: Int, end: Int): Int {
        var cursor = start
        while (cursor < end && !text[cursor].isWhitespace() && text[cursor] != '#') cursor++
        return cursor
    }

    private fun tokenEquals(text: String, start: Int, end: Int, expected: String): Boolean {
        return end - start == expected.length && text.regionMatches(start, expected, 0, expected.length)
    }

    private fun parseInt(text: String, start: Int, end: Int, lineNumber: Int): Int? {
        var cursor = start
        var negative = false
        if (cursor < end && (text[cursor] == '+' || text[cursor] == '-')) {
            negative = text[cursor] == '-'
            cursor++
        }
        require(cursor < end) { "第 $lineNumber 行含有无效整数" }
        var value = 0L
        val firstDigit = cursor
        while (cursor < end && text[cursor] in '0'..'9') {
            value = value * 10 + (text[cursor] - '0')
            if (value > Int.MAX_VALUE.toLong() + 1L) return null
            cursor++
        }
        require(cursor == end && cursor > firstDigit) { "第 $lineNumber 行含有无效整数" }
        val signed = if (negative) -value else value
        return signed.takeIf { it in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong() }?.toInt()
    }

    private fun parseFloat(text: String, start: Int, end: Int, lineNumber: Int): Float {
        val value = parseDecimal(text, start, end, lineNumber).toFloat()
        require(value.isFinite()) { "第 $lineNumber 行含有无效数值" }
        return value
    }

    private fun parseDouble(text: String, start: Int, end: Int, lineNumber: Int): Double {
        val value = parseDecimal(text, start, end, lineNumber)
        require(value.isFinite()) { "第 $lineNumber 行含有无效数值" }
        return value
    }

    private fun parseDecimal(text: String, start: Int, end: Int, lineNumber: Int): Double {
        var cursor = start
        var negative = false
        if (cursor < end && (text[cursor] == '+' || text[cursor] == '-')) {
            negative = text[cursor] == '-'
            cursor++
        }
        var mantissa = 0.0
        var digits = 0
        var fractionalDigits = 0
        while (cursor < end && text[cursor] in '0'..'9') {
            mantissa = mantissa * 10.0 + (text[cursor] - '0')
            digits++
            cursor++
        }
        if (cursor < end && text[cursor] == '.') {
            cursor++
            while (cursor < end && text[cursor] in '0'..'9') {
                mantissa = mantissa * 10.0 + (text[cursor] - '0')
                fractionalDigits++
                digits++
                cursor++
            }
        }
        require(digits > 0) { "第 $lineNumber 行含有无效数值" }
        var exponent = 0L
        if (cursor < end && (text[cursor] == 'e' || text[cursor] == 'E')) {
            cursor++
            var exponentNegative = false
            if (cursor < end && (text[cursor] == '+' || text[cursor] == '-')) {
                exponentNegative = text[cursor] == '-'
                cursor++
            }
            val exponentStart = cursor
            while (cursor < end && text[cursor] in '0'..'9') {
                if (exponent < 100000L) exponent = exponent * 10L + (text[cursor] - '0')
                cursor++
            }
            require(cursor > exponentStart) { "第 $lineNumber 行含有无效数值" }
            if (exponentNegative) exponent = -exponent
        }
        require(cursor == end) { "第 $lineNumber 行含有无效数值" }
        if (mantissa == 0.0) return if (negative) -0.0 else 0.0
        val power = exponent - fractionalDigits.toLong()
        val scaled = mantissa * Math.pow(10.0, power.toDouble())
        return if (negative) -scaled else scaled
    }

    fun identity(grid: Int = BLEND_GRID, title: String? = "Original"): Data {
        require(grid in 2..129) { "LUT 网格须为 2–129" }
        val rgb = FloatArray(grid * grid * grid * 3)
        val divisor = (grid - 1).toFloat()
        var offset = 0
        for (b in 0 until grid) for (g in 0 until grid) for (r in 0 until grid) {
            rgb[offset++] = r / divisor
            rgb[offset++] = g / divisor
            rgb[offset++] = b / divisor
        }
        return Data(grid, title, listOf(0.0, 0.0, 0.0), listOf(1.0, 1.0, 1.0), rgb)
    }

    fun resample(source: Data, grid: Int = BLEND_GRID, title: String? = source.title): Data {
        require(grid in 2..129) { "LUT 网格须为 2–129" }
        requireCanonical(source)
        if (source.grid == grid) return source.copy(title = title)
        val target = identity(grid, title)
        val divisor = (grid - 1).toDouble()
        var offset = 0
        for (b in 0 until grid) for (g in 0 until grid) for (r in 0 until grid) {
            val x = r / divisor * (source.grid - 1)
            val y = g / divisor * (source.grid - 1)
            val z = b / divisor * (source.grid - 1)
            val x0 = Math.floor(x).toInt().coerceAtMost(source.grid - 2)
            val y0 = Math.floor(y).toInt().coerceAtMost(source.grid - 2)
            val z0 = Math.floor(z).toInt().coerceAtMost(source.grid - 2)
            val dx = x - x0
            val dy = y - y0
            val dz = z - z0
            var red = 0.0
            var green = 0.0
            var blue = 0.0
            for (dzBit in 0..1) for (dyBit in 0..1) for (dxBit in 0..1) {
                val sampleOffset = (((z0 + dzBit) * source.grid + y0 + dyBit) * source.grid + x0 + dxBit) * 3
                val weight = (if (dxBit == 0) 1.0 - dx else dx) *
                    (if (dyBit == 0) 1.0 - dy else dy) *
                    (if (dzBit == 0) 1.0 - dz else dz)
                red += source.rgb[sampleOffset] * weight
                green += source.rgb[sampleOffset + 1] * weight
                blue += source.rgb[sampleOffset + 2] * weight
            }
            target.rgb[offset++] = red.toFloat()
            target.rgb[offset++] = green.toFloat()
            target.rgb[offset++] = blue.toFloat()
        }
        return target
    }

    /** Numerically blend encoded sRGB outputs; amountB is the second source's weight. */
    fun blend(a: Data, b: Data, amountB: Double, grid: Int = BLEND_GRID,
              title: String = "Blended LUT"): Data {
        require(amountB.isFinite() && amountB in 0.0..1.0) { "混合比例须在 0–100%" }
        val left = resample(a, grid)
        val right = resample(b, grid)
        return blendLattice(left, right, amountB, title)
    }

    /** Blend two already-resampled 33^3 lattices without parsing or fitting again. */
    fun blendLattice(a: Data, b: Data, amountB: Double,
                     title: String = "Blended LUT"): Data {
        require(a.grid == b.grid) { "混合源网格不一致" }
        require(amountB.isFinite() && amountB in 0.0..1.0) { "混合比例须在 0–100%" }
        val output = FloatArray(a.rgb.size)
        val weightA = 1.0 - amountB
        for (index in output.indices) {
            output[index] = (a.rgb[index] * weightA + b.rgb[index] * amountB)
                .coerceIn(0.0, 1.0).toFloat()
        }
        return Data(a.grid, title, listOf(0.0, 0.0, 0.0), listOf(1.0, 1.0, 1.0), output)
    }

    /** Apply a LUT to packed ARGB pixels for a debounced reference-photo preview. */
    fun applyArgb(data: Data, pixels: IntArray): IntArray {
        requireCanonical(data)
        val output = IntArray(pixels.size)
        val size = data.grid
        val scale = size - 1
        for (index in pixels.indices) {
            val pixel = pixels[index]
            val x = (pixel shr 16 and 0xff) / 255.0 * scale
            val y = (pixel shr 8 and 0xff) / 255.0 * scale
            val z = (pixel and 0xff) / 255.0 * scale
            val x0 = Math.floor(x).toInt().coerceAtMost(size - 2)
            val y0 = Math.floor(y).toInt().coerceAtMost(size - 2)
            val z0 = Math.floor(z).toInt().coerceAtMost(size - 2)
            val dx = x - x0; val dy = y - y0; val dz = z - z0
            var redValue = 0.0; var greenValue = 0.0; var blueValue = 0.0
            for (dzBit in 0..1) for (dyBit in 0..1) for (dxBit in 0..1) {
                val source = (((z0 + dzBit) * size + y0 + dyBit) * size + x0 + dxBit) * 3
                val weight = (if (dxBit == 0) 1.0 - dx else dx) *
                    (if (dyBit == 0) 1.0 - dy else dy) *
                    (if (dzBit == 0) 1.0 - dz else dz)
                redValue += data.rgb[source] * weight
                greenValue += data.rgb[source + 1] * weight
                blueValue += data.rgb[source + 2] * weight
            }
            val red = (redValue.coerceIn(0.0, 1.0) * 255.0 + 0.5).toInt()
            val green = (greenValue.coerceIn(0.0, 1.0) * 255.0 + 0.5).toInt()
            val blue = (blueValue.coerceIn(0.0, 1.0) * 255.0 + 0.5).toInt()
            output[index] = (pixel and 0xff000000.toInt()) or (red shl 16) or (green shl 8) or blue
        }
        return output
    }

    fun writeCube(data: Data, writer: Writer, title: String? = data.title) {
        writer.buffered().use { output ->
            val safeTitle = (title.takeUnless { it.isNullOrBlank() } ?: "Blended LUT")
                .replace('"', '\'')
            output.append("TITLE \"").append(safeTitle).append("\"\n")
            output.append("LUT_3D_SIZE ").append(data.grid.toString()).append('\n')
            output.append("DOMAIN_MIN 0.0000000000 0.0000000000 0.0000000000\n")
            output.append("DOMAIN_MAX 1.0000000000 1.0000000000 1.0000000000\n")
            var offset = 0
            repeat(data.grid * data.grid * data.grid) {
                output.append(String.format(Locale.ROOT, "%.10f %.10f %.10f\n",
                    data.rgb[offset], data.rgb[offset + 1], data.rgb[offset + 2]))
                offset += 3
            }
        }
    }

    fun requireCanonical(data: Data) {
        require((0..2).all { kotlin.math.abs(data.domainMin[it]) <= 1e-6 }) { "混合源必须使用 0..1 sRGB 输入域" }
        require((0..2).all { kotlin.math.abs(data.domainMax[it] - 1.0) <= 1e-6 }) { "混合源必须使用 0..1 sRGB 输入域" }
    }

    private fun splitWhitespace(line: String): List<String> {
        val result = ArrayList<String>(4)
        var start = -1
        for (index in line.indices) {
            if (line[index].isWhitespace()) {
                if (start >= 0) {
                    result += line.substring(start, index)
                    start = -1
                }
            } else if (start < 0) start = index
        }
        if (start >= 0) result += line.substring(start)
        return result
    }

    /** NX300M D4 ABI: three 9^3 10-bit planes, each followed by one zero pad word. */
    fun compileD4(data: Data, stockBaseline: ByteArray? = null,
                  roundNearest: Boolean = false): ByteArray {
        val stock = stockBaseline?.let { raw ->
            require(raw.size == 4380) { "原厂 3D 基准表必须为 4380 字节" }
            val words = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN)
            IntArray(2190) { index -> words.short.toInt() and 0xffff }.also { values ->
                require(values.all { it in 0..1023 }) { "原厂 3D 基准表超过 10 bit" }
                require(listOf(729, 1459, 2189).all { values[it] == 0 }) { "原厂 3D 基准表填充字不为零" }
            }
        }
        val values = ByteBuffer.allocate(4380).order(ByteOrder.LITTLE_ENDIAN)
        for (channel in 0..2) {
            for (r in 0..8) for (g in 0..8) for (b in 0..8) {
                val destination = (r * 9 + g) * 9 + b
                val inputR: Double
                val inputG: Double
                val inputB: Double
                if (stock == null) {
                    inputR = r / 8.0; inputG = g / 8.0; inputB = b / 8.0
                } else {
                    inputR = stock[destination] / 1023.0
                    inputG = stock[730 + destination] / 1023.0
                    inputB = stock[1460 + destination] / 1023.0
                }
                val mapped = sample(data, inputR, inputG, inputB)[channel]
                val offset = if (roundNearest) 0.5 else 1e-9
                values.putShort((Math.floor(mapped.coerceIn(0.0, 1.0) * 1023.0 + offset)).toInt().toShort())
            }
            values.putShort(0)
        }
        return values.array()
    }

    private fun sample(data: Data, r: Double, g: Double, b: Double): DoubleArray {
        val size = data.grid
        val coords = doubleArrayOf(
            ((r - data.domainMin[0]) / (data.domainMax[0] - data.domainMin[0])).coerceIn(0.0, 1.0) * (size - 1),
            ((g - data.domainMin[1]) / (data.domainMax[1] - data.domainMin[1])).coerceIn(0.0, 1.0) * (size - 1),
            ((b - data.domainMin[2]) / (data.domainMax[2] - data.domainMin[2])).coerceIn(0.0, 1.0) * (size - 1),
        )
        val base = IntArray(3) { Math.floor(coords[it]).toInt().coerceAtMost(size - 2) }
        val frac = DoubleArray(3) { coords[it] - base[it] }
        val result = DoubleArray(3)
        for (db in 0..1) for (dg in 0..1) for (dr in 0..1) {
            val source = (((base[2] + db) * size + base[1] + dg) * size + base[0] + dr) * 3
            val weight = (if (dr == 0) 1.0 - frac[0] else frac[0]) *
                (if (dg == 0) 1.0 - frac[1] else frac[1]) *
                (if (db == 0) 1.0 - frac[2] else frac[2])
            for (channel in 0..2) result[channel] += data.rgb[source + channel] * weight
        }
        return result
    }
}
