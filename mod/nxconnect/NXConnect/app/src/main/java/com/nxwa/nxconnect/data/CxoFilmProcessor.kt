// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import android.content.Context
import android.graphics.Bitmap
import android.os.Build
import dev.cxo.filmpack.core.FixedSrgbEntryLutIO
import dev.cxo.filmpack.core.FixedSrgbFilmPipeline
import dev.cxo.filmpack.core.FixedSrgbRgbFilmPipeline
import dev.cxo.filmpack.core.FixedWideGamutToSrgb
import dev.cxo.filmpack.core.HsvFilmLut
import dev.cxo.filmpack.core.HsvFilmLutIO
import dev.cxo.filmpack.core.NativeWhiteBalanceRgb
import dev.cxo.filmpack.core.RgbFilmLut
import dev.cxo.filmpack.core.RgbFilmLutIO
import java.io.Closeable
import kotlin.math.roundToInt
import org.json.JSONArray

data class CxoFilmChoice(
    val id: String,
    val token: String,
    val name: String,
    val category: String,
    val experimental: Boolean,
    val model: String,
    val asset: String,
    val baseRed: Float,
    val baseGreen: Float,
    val baseBlue: Float,
) {
    fun displayName(): String = if (experimental) "$name · 实验" else name
}

/** NXConnect adapter around CXO FilmPack's fixed sRGB film core. */
class CxoFilmProcessor(context: Context) : Closeable {
    private val context = context.applicationContext
    private val assets = this.context.assets

    @Volatile
    private var closed = false

    private val entry by lazy {
        assets.open(ENTRY_ASSET).buffered().use(FixedSrgbEntryLutIO::read)
    }
    private val outputCmm by lazy {
        FixedWideGamutToSrgb.read(assets.open(OUTPUT_CMM_ASSET).use { it.readBytes() })
    }

    private var cachedFilmId: String? = null
    private var cachedFilmLut: Any? = null

    fun loadCatalog(): List<CxoFilmChoice> {
        check(!closed) { "胶片引擎已关闭" }
        val rows = JSONArray(assets.open(CATALOG_ASSET).bufferedReader().use { it.readText() })
        val films = List(rows.length()) { index ->
            val row = rows.getJSONObject(index)
            CxoFilmChoice(
                id = row.getString("id"),
                token = row.getString("token"),
                name = row.getString("name"),
                category = row.getString("category"),
                experimental = row.getBoolean("experimental"),
                model = row.getString("model"),
                asset = row.getString("asset"),
                baseRed = row.getDouble("baseRed").toFloat(),
                baseGreen = row.getDouble("baseGreen").toFloat(),
                baseBlue = row.getDouble("baseBlue").toFloat(),
            )
        }
        require(films.isNotEmpty() && films.map(CxoFilmChoice::id).toSet().size == films.size) {
            "胶片目录为空或包含重复 ID"
        }
        return films
    }

    /** Applies the complete film colour chain in-place to a full-width pixel band. */
    fun renderInPlace(
        pixels: IntArray,
        width: Int,
        film: CxoFilmChoice?,
        intensity: Int,
        shouldContinue: () -> Boolean = { true },
    ): Boolean {
        check(!closed) { "胶片引擎已关闭" }
        require(pixels.isNotEmpty() && width > 0 && pixels.size % width == 0)
        require(intensity in 0..200) { "胶片强度须在 0 至 200" }
        if (film == null || intensity == 0) return true

        val transform = createTransform(film, intensity)
        val rowsPerBand = (262_144 / width).coerceIn(1, 256)
        val capacity = minOf(pixels.size, width * rowsPerBand)
        val sourceRgb = ShortArray(capacity * 3)
        val destinationRgb = FloatArray(capacity * 3)
        var offset = 0
        while (offset < pixels.size) {
            if (!shouldContinue()) return false
            val count = minOf(capacity, pixels.size - offset)
            renderChunk(pixels, offset, count, width, transform, sourceRgb, destinationRgb)
            offset += count
        }
        return true
    }

    fun renderInPlace(
        bitmap: Bitmap,
        film: CxoFilmChoice?,
        intensity: Int,
        shouldContinue: () -> Boolean = { true },
    ): Boolean {
        require(Build.VERSION.SDK_INT < Build.VERSION_CODES.O || bitmap.config != Bitmap.Config.HARDWARE) {
            "硬件位图不能用于胶片显影"
        }
        val rowsPerBand = (262_144 / bitmap.width).coerceIn(1, 256)
        val pixels = IntArray(bitmap.width * rowsPerBand)
        val sourceRgb = ShortArray(pixels.size * 3)
        val destinationRgb = FloatArray(pixels.size * 3)
        val transform = film?.takeIf { intensity > 0 }?.let { createTransform(it, intensity) }
        if (transform == null) return true
        var top = 0
        while (top < bitmap.height) {
            val rows = minOf(rowsPerBand, bitmap.height - top)
            val count = bitmap.width * rows
            if (!shouldContinue()) return false
            bitmap.getPixels(pixels, 0, bitmap.width, 0, top, bitmap.width, rows)
            renderChunk(pixels, 0, count, bitmap.width, transform, sourceRgb, destinationRgb)
            bitmap.setPixels(pixels, 0, bitmap.width, 0, top, bitmap.width, rows)
            top += rows
        }
        return true
    }

    private fun renderChunk(
        pixels: IntArray,
        offset: Int,
        count: Int,
        width: Int,
        transform: FilmTransform,
        sourceRgb: ShortArray,
        destinationRgb: FloatArray,
    ) {
        for (index in 0 until count) {
            val pixel = pixels[offset + index]
            val rgb = index * 3
            sourceRgb[rgb] = ((pixel shr 16 and 0xff) * 257).toShort()
            sourceRgb[rgb + 1] = ((pixel shr 8 and 0xff) * 257).toShort()
            sourceRgb[rgb + 2] = ((pixel and 0xff) * 257).toShort()
        }
        transform.render(sourceRgb, destinationRgb, width, 0)
        outputCmm.render(destinationRgb, destinationRgb)
        for (index in 0 until count) {
            val rgb = index * 3
            val alpha = pixels[offset + index] and 0xff000000.toInt()
            val red = quantize(destinationRgb[rgb])
            val green = quantize(destinationRgb[rgb + 1])
            val blue = quantize(destinationRgb[rgb + 2])
            pixels[offset + index] = alpha or (red shl 16) or (green shl 8) or blue
        }
    }

    @Synchronized
    private fun createTransform(film: CxoFilmChoice, intensity: Int): FilmTransform {
        val lut = if (film.id == cachedFilmId) {
            requireNotNull(cachedFilmLut)
        } else {
            val loaded = when (film.model) {
                "rgb" -> assets.open(resolveFilmAsset(film)).buffered().use(RgbFilmLutIO::read)
                "hsv", "direct", "provider-bw" -> assets.open(resolveFilmAsset(film)).buffered().use {
                    HsvFilmLutIO.read(it, allowEndpoint32768 = film.model == "direct")
                }
                else -> error("不支持的胶片类型：${film.model}")
            }
            cachedFilmId = film.id
            cachedFilmLut = loaded
            loaded
        }
        val strength = intensity / 100f
        val gains = NativeWhiteBalanceRgb.fromGains(
            (1f - strength) + strength * film.baseRed,
            (1f - strength) + strength * film.baseGreen,
            (1f - strength) + strength * film.baseBlue,
        )
        return when (lut) {
            is HsvFilmLut -> FixedSrgbFilmPipeline(
                entry,
                lut,
                gains,
                minOf(strength, 1f),
            ).let { pipeline ->
                FilmTransform { source, destination, width, xOffset ->
                    pipeline.renderRgb16ExactSampling0(source, destination, width, xOffset)
                }
            }
            is RgbFilmLut -> FixedSrgbRgbFilmPipeline(
                entry,
                lut,
                gains,
                minOf(strength, 1f),
            ).let { pipeline ->
                FilmTransform { source, destination, width, xOffset ->
                    pipeline.renderRgb16ExactSampling0(source, destination, width, xOffset)
                }
            }
            else -> error("胶片资产类型无效")
        }
    }

    private fun resolveFilmAsset(film: CxoFilmChoice): String {
        require(film.asset.startsWith("films/")) { "胶片资产路径无效" }
        return "$ASSET_ROOT/${film.asset}"
    }

    override fun close() {
        closed = true
        cachedFilmId = null
        cachedFilmLut = null
    }

    private fun interface FilmTransform {
        fun render(source: ShortArray, destination: FloatArray, width: Int, xOffset: Int)
    }

    private fun quantize(value: Float): Int = (value.coerceIn(0f, 1f) * 255f).roundToInt()

    private companion object {
        const val ASSET_ROOT = "cxo-filmpack"
        const val CATALOG_ASSET = "$ASSET_ROOT/films.json"
        const val ENTRY_ASSET = "$ASSET_ROOT/films/srgb-entry-pix65.cxoentry"
        const val OUTPUT_CMM_ASSET = "$ASSET_ROOT/films/output-cmm/widegamut-to-srgb.lcmsbin"
    }
}
