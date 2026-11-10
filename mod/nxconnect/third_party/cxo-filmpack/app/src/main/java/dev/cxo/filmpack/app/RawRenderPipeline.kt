package dev.cxo.filmpack.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Matrix
import dev.cxo.filmpack.core.FixedWideGamutToSrgb
import dev.cxo.filmpack.core.RawCameraNormalization
import dev.cxo.filmpack.core.RawColorIntentEstimator
import dev.cxo.filmpack.core.RawOriginalTone
import dev.cxo.filmpack.core.RawNegativeProviderCatalog
import dev.cxo.filmpack.core.RawRgbContext
import dev.cxo.filmpack.core.RawRgbGridBaker
import dev.cxo.filmpack.core.RawRgbFilmRenderer
import dev.cxo.filmpack.core.RawSensorToWorking
import java.util.LinkedHashMap
import java.util.concurrent.CancellationException
import kotlin.math.max
import kotlin.math.sqrt

/** Camera-linear RAW path with independent source-camera calibration and
 * provider parameters constructed at the actual white-balance temperature.
 */
internal class RawRenderPipeline(context: Context) {
    private val context = context.applicationContext
    private val cameraProfiles = RawCameraProfiles(this.context)
    private val runtimeBakes = object : LinkedHashMap<String, RawNegativeProviderCatalog.Bake>(4, .75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, RawNegativeProviderCatalog.Bake>?): Boolean = size > 3
    }
    private val rgbContexts = object : LinkedHashMap<String, RawRgbContext>(3, .75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, RawRgbContext>?): Boolean = size > 2
    }
    // A renderer owns a copy of its 65³ grid. Keep it across row batches rather
    // than rebuilding/copying several megabytes for every export chunk.
    private val rgbRenderers = object : LinkedHashMap<String, RawRgbFilmRenderer>(3, .75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, RawRgbFilmRenderer>?): Boolean = size > 2
    }
    private val negativeProviders by lazy {
        context.assets.open("raw-negative-providers/catalog.cxornp").use(RawNegativeProviderCatalog::read)
    }
    private val fixedProviders by lazy {
        context.assets.open("raw-static-providers/catalog.cxornp").use(RawNegativeProviderCatalog::read)
    }
    private val specialProviders by lazy {
        context.assets.open("raw-special-providers/catalog.cxornp").use(RawNegativeProviderCatalog::read)
    }
    private val cameraBwProviders by lazy { RawCameraBwCatalog(context) }
    // Statistics belong to the decoded photo, not the WB/grid cache. Reuse the
    // bounded camera-domain sample throughout previews and export row batches.
    private var intentSourceId: String? = null
    private var intentSample: RawColorIntentEstimator.SampledImage? = null
    private var sceneRenderingIntent: Float? = null
    private val sceneIntents = HashMap<String, Float>()
    private var dngSampleId: String? = null
    private var dngSample: RawColorIntentEstimator.SampledImage? = null
    private val outputCmm by lazy {
        FixedWideGamutToSrgb.read(context.assets.open("films/output-cmm/widegamut-to-srgb.lcmsbin").use { it.readBytes() })
    }

    fun supports(source: RawCameraLinear16Photo, film: FilmChoice?): Boolean {
        val camera = cameraProfiles.forModel(source.model, source.make) ?: return false
        return film == null || providerFor(film) != null || cameraBwProviders.supports(camera.id, film.id)
    }

    fun requireSupported(source: RawCameraLinear16Photo, film: FilmChoice?) {
        if (cameraProfiles.forModel(source.model, source.make) == null) {
            throw java.io.IOException("暂不支持这台相机的 RAW：${source.make} ${source.model}（缺少机身标定）")
        }
        if (film != null && !supports(source, film)) {
            throw java.io.IOException("该相机暂不支持「${film.name}」的 RAW 效果，请更换胶片")
        }
    }

    private fun providerFor(film: FilmChoice): RawNegativeProviderCatalog.Film? =
        negativeProviders.film(film.id) ?: fixedProviders.film(film.id) ?: specialProviders.film(film.id)

    /** Linear DNG enters after AdobeColorPart1, with its own source-space
     * matrices. It shares the recovered RAW film providers, never the RGB entry. */
    @Synchronized
    fun prepareDngRows(source: RawSrgb16Photo, film: FilmChoice?, intensity: Int,
                       sensorMatrix: FloatArray, extraMatrix: FloatArray,
                       original: (ShortArray, ShortArray) -> Unit,
                       cancelled: () -> Boolean): (ShortArray, FloatArray, Int, Int) -> Unit {
        require(source.colorDomain == "prophoto_linear_q15")
        require(intensity in 0..200)
        checkCancelled(cancelled)
        val provider = film?.let { providerFor(it)
            ?: throw java.io.IOException("该胶片尚未支持 ProRAW：${it.name}") }
        val temperature = source.metadata.getDouble("dngTemperature").toFloat()
        val key = "DNG:${film?.id}@${temperature.toRawBits()}:${extraMatrix.contentHashCode()}"
        val rgb = if (film != null && film.id in RGB_IDS) {
            synchronized(this) { rgbContexts.getOrPut(key) {
                requireNotNull(provider).rgbContext(temperature, 0f, extraMatrix, ::readCurve)
            } }.let { rgbRendererFor(key to it) }
        } else null
        val bake = if (provider != null && rgb == null) synchronized(this) {
            runtimeBakes.getOrPut(key) { provider.bake(temperature, 0f, extraMatrix, ::readCurve) }
        } else null
        val runtimeBw = provider?.runtimeBw ?: false
        val needsColor = provider?.requiresAdditionalColorStage == true && intensity > 0
        val intents = if (film != null && (!runtimeBw || needsColor)) {
            val id = source.metadata.getString("decodeId")
            if (dngSampleId != id) { dngSampleId = id; dngSample = null }
            val sample = dngSample ?: RawColorIntentEstimator.meanDownsampleQ15(source.width, source.height,
                { y, target -> source.readRows(y, 1, target) }, cancelled).also { dngSample = it }
            RawColorIntentEstimator.estimateIntentsSampledQ15(sample.pixels, sensorMatrix, -.84f,
                if (needsColor) provider.scaleFilterMatrix(intensity) else null,
                estimateRenderingIntent = !runtimeBw, isCancelled = cancelled)
        } else null
        val sensor = if (film != null) RawSensorToWorking(sensorMatrix, 0f,
            if (!runtimeBw) intents?.rendering?.let(RawColorIntentEstimator::throughNativePercent) ?: 0f else 0f) else null
        val renderer = bake?.renderer(if (needsColor) provider.colorStage(
            RawColorIntentEstimator.throughNativePercent(requireNotNull(intents?.scalesAndFilter)), intensity) else null)
        val scratch = ThreadLocal<Array<ShortArray>>()
        return { input, output, _, _ ->
            checkCancelled(cancelled)
            val buffers = scratch.get()?.takeIf { it[0].size == input.size }
                ?: arrayOf(ShortArray(input.size), ShortArray(input.size)).also(scratch::set)
            if (sensor == null) original(input, buffers[1]) else {
                sensor.renderQ15(input, buffers[0])
                if (rgb != null) rgb.renderQ15(buffers[0], buffers[1], intensity)
                else requireNotNull(renderer).renderQ15(buffers[0], buffers[1], intensity)
            }
            for (i in output.indices) output[i] = buffers[1][i].toInt().coerceIn(0, 32767) / 32767f
            checkCancelled(cancelled)
        }
    }

    fun renderPreview(source: RawCameraLinear16Photo, film: FilmChoice?, maxEdge: Int,
                      maxPixels: Long, cancelled: () -> Boolean, intensity: Int = 100,
                      targetDisplaySize: Pair<Int, Int>? = null,
                      beforeOutputCmm: ((FloatArray, Int, Int) -> Unit)? = null): Bitmap {
        require(intensity in 0..200) { "强度必须在 0 到 200 之间" }
        require(supports(source, film)) { "当前 RAW 机身或胶片尚未进入受控核心路径" }
        val scale = minOf(1.0, maxEdge.toDouble() / max(source.width, source.height),
            sqrt(maxPixels.toDouble() / (source.width.toLong() * source.height)))
        val target = targetDisplaySize?.let { (width, height) ->
            require(width > 0 && height > 0 && max(width, height) <= maxEdge && width.toLong() * height <= maxPixels)
            if (source.orientation >= 5) height to width else width to height
        }
        val outWidth = target?.first ?: max(1, (source.width * scale).toInt())
        val outHeight = target?.second ?: max(1, (source.height * scale).toInt())
        require(outWidth <= source.width && outHeight <= source.height)
        checkCancelled(cancelled)
        val transform = transform(source, film, intensity, cancelled)
        val bitmap = Bitmap.createBitmap(outWidth, outHeight, Bitmap.Config.ARGB_8888)
        val sourceRow = ShortArray(source.width * 3)
        val sampled = ShortArray(outWidth * 3)
        val normalized = ShortArray(sampled.size)
        val working = ShortArray(sampled.size)
        val encoded = ShortArray(sampled.size)
        val cmm = FloatArray(sampled.size)
        val sourceOffsets = IntArray(outWidth) { dx ->
            (dx.toLong() * source.width / outWidth).toInt().coerceIn(0, source.width - 1) * 3
        }
        val argb = IntArray(outWidth)
        try {
            for (dy in 0 until outHeight) {
                checkCancelled(cancelled)
                val sourceY = (dy.toLong() * source.height / outHeight).toInt().coerceIn(0, source.height - 1)
                source.readRows(sourceY, 1, sourceRow)
                // The RAW colour chain is pointwise. Select the same nearest
                // samples before rendering instead of computing discarded columns.
                // Scene statistics still use their independent full-source sampler.
                for (dx in 0 until outWidth) {
                    val from = sourceOffsets[dx]
                    val to = dx * 3
                    sampled[to] = sourceRow[from]
                    sampled[to + 1] = sourceRow[from + 1]
                    sampled[to + 2] = sourceRow[from + 2]
                }
                transform(sampled, normalized, working, encoded)
                for (i in encoded.indices) cmm[i] = encoded[i].toInt() / 32767f
                beforeOutputCmm?.invoke(cmm, outWidth, dy)
                outputCmm.render(cmm, cmm)
                for (dx in 0 until outWidth) {
                    val i = dx * 3
                    argb[dx] = Color.rgb(q8(cmm[i]), q8(cmm[i + 1]), q8(cmm[i + 2]))
                }
                bitmap.setPixels(argb, 0, outWidth, 0, dy, outWidth, 1)
            }
            return applyOrientation(bitmap, source.orientation)
        } catch (failure: Throwable) {
            bitmap.recycle(); throw failure
        }
    }

    private fun applyOrientation(source: Bitmap, orientation: Int): Bitmap {
        if (orientation == 1) return source
        val matrix = Matrix()
        when (orientation) {
            2 -> matrix.setScale(-1f, 1f)
            3 -> matrix.setRotate(180f)
            4 -> matrix.setScale(1f, -1f)
            5 -> { matrix.setRotate(90f); matrix.postScale(-1f, 1f) }
            6 -> matrix.setRotate(90f)
            7 -> { matrix.setRotate(-90f); matrix.postScale(-1f, 1f) }
            8 -> matrix.setRotate(-90f)
            else -> return source
        }
        val result = Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, false)
        if (result !== source) source.recycle()
        return result
    }

    fun renderRows(source: RawCameraLinear16Photo, film: FilmChoice?, input: ShortArray,
                   normalized: ShortArray, working: ShortArray, encoded: ShortArray,
                   intensity: Int = 100, cancelled: () -> Boolean) {
        prepareRows(source, film, intensity, cancelled)(input, normalized, working, encoded)
    }

    /** Prepare once per export, then reuse immutable parameters across its row batches.
     * Scene intent is resolved on the owner thread before the returned plan is used. */
    fun prepareRows(source: RawCameraLinear16Photo, film: FilmChoice?, intensity: Int = 100,
                    cancelled: () -> Boolean): (ShortArray, ShortArray, ShortArray, ShortArray) -> Unit {
        require(intensity in 0..200) { "强度必须在 0 到 200 之间" }
        require(supports(source, film)) { "当前 RAW 机身或胶片尚未进入受控核心路径" }
        checkCancelled(cancelled)
        val apply = transform(source, film, intensity, cancelled)
        return { input, normalized, working, encoded ->
            checkCancelled(cancelled)
            apply(input, normalized, working, encoded)
            checkCancelled(cancelled)
        }
    }

    /** Same scene statistic for preview and export. No captured photo constants. */
    @Synchronized
    internal fun colorIntent(source: RawCameraLinear16Photo, film: FilmChoice, intensity: Int = 100, cancelled: () -> Boolean): Float {
        checkCancelled(cancelled)
        val camera = requireNotNull(cameraProfiles.forModel(source.model, source.make))
        val provider = requireNotNull(providerFor(film))
        require(intensity in 0..200)
        if (!provider.requiresAdditionalColorStage || intensity == 0) return 0f
        val key = "${film.id}:${minOf(intensity, 100)}"
        selectIntentSource(source)
        sceneIntents[key]?.let { return it }
        val sampled = sampleFor(source, cancelled)
        val temperature = camera.whiteBalance.estimate(source.camMul).temperatureKelvin
        val intents = RawColorIntentEstimator.estimateIntentsSampledQ15(
            sampled.pixels, camera.fidelity.sensorToWorking(temperature), camera.exposureBiasEv - .84f,
            provider.scaleFilterMatrix(intensity), estimateRenderingIntent = sceneRenderingIntent == null,
            isCancelled = cancelled,
        )
        intents.rendering?.let { sceneRenderingIntent = RawColorIntentEstimator.throughNativePercent(it) }
        return RawColorIntentEstimator.throughNativePercent(requireNotNull(intents.scalesAndFilter))
            .also { sceneIntents[key] = it }
    }

    /** Scene statistic before the rendering consumer's BW/Original gate. */
    @Synchronized
    internal fun renderingIntent(source: RawCameraLinear16Photo, cancelled: () -> Boolean): Float {
        checkCancelled(cancelled)
        selectIntentSource(source)
        sceneRenderingIntent?.let { return it }
        val camera = requireNotNull(cameraProfiles.forModel(source.model, source.make))
        val temperature = camera.whiteBalance.estimate(source.camMul).temperatureKelvin
        val intents = RawColorIntentEstimator.estimateIntentsSampledQ15(
            sampleFor(source, cancelled).pixels, camera.fidelity.sensorToWorking(temperature),
            camera.exposureBiasEv - .84f, isCancelled = cancelled,
        )
        return RawColorIntentEstimator.throughNativePercent(requireNotNull(intents.rendering))
            .also { sceneRenderingIntent = it }
    }

    private fun selectIntentSource(source: RawCameraLinear16Photo) {
        if (intentSourceId != source.decodeId) {
            intentSourceId = source.decodeId
            intentSample = null
            sceneRenderingIntent = null
            sceneIntents.clear()
        }
    }

    @Synchronized
    private fun sampleFor(source: RawCameraLinear16Photo, cancelled: () -> Boolean): RawColorIntentEstimator.SampledImage {
        checkCancelled(cancelled)
        selectIntentSource(source)
        return intentSample ?: run {
            val rawRow = ShortArray(source.width * 3)
            RawColorIntentEstimator.meanDownsampleQ15(source.width, source.height, { y, normalized ->
                source.readRows(y, 1, rawRow)
                RawCameraNormalization.render(rawRow, normalized, source.maximumAfterProcess, source.rawBps)
            }, cancelled).also { intentSample = it }
        }
    }

    private fun transform(source: RawCameraLinear16Photo, film: FilmChoice?, intensity: Int, cancelled: () -> Boolean): (ShortArray, ShortArray, ShortArray, ShortArray) -> Unit {
        val camera = requireNotNull(cameraProfiles.forModel(source.model, source.make))
        val temperature = camera.whiteBalance.estimate(source.camMul).temperatureKelvin
        val matrix = camera.fidelity.sensorToWorking(temperature)
        val rgbContext = film?.takeIf { it.id in RGB_IDS }?.let { rgbContextFor(camera, temperature, it) }
        val bake = film?.takeIf { rgbContext == null }?.let { bakeFor(camera, temperature, it) }
        val runtimeBw = rgbContext?.second?.runtimeBw ?: bake?.runtimeBw ?: false
        val sensor = RawSensorToWorking(
            matrix,
            exposureEv = camera.exposureBiasEv,
            // Original does not consume rendering intent; BW disables it at
            // the rendering consumer even when the estimator has a result.
            highlightControl = if (film != null && !runtimeBw) renderingIntent(source, cancelled) else 0f,
        )
        val rgbRenderer = rgbContext?.let { rgbRendererFor(it) }
        val renderer = bake?.let { prepared ->
            val selected = requireNotNull(film)
            val stage = providerFor(selected)?.let { provider ->
                if (provider.requiresAdditionalColorStage && intensity > 0) provider.colorStage(colorIntent(source, selected, intensity, cancelled), intensity) else null
            }
            prepared.renderer(stage)
        }
        return { input, normalized, working, encoded ->
            RawCameraNormalization.render(input, normalized, source.maximumAfterProcess, source.rawBps)
            sensor.renderQ15(normalized, working)
            when {
                rgbRenderer != null -> rgbRenderer.renderQ15(working, encoded, intensity)
                renderer != null -> renderer.renderQ15(working, encoded, intensity)
                else -> RawOriginalTone.renderQ15(working, encoded)
            }
            for (i in encoded.indices) encoded[i] = encoded[i].coerceAtLeast(0).coerceAtMost(32767)
        }
    }

    private fun q8(value: Float): Int = (value.coerceIn(0f, 1f) * 255f + .5f).toInt()

    private fun checkCancelled(cancelled: () -> Boolean) {
        if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("RAW 操作已取消")
    }

    @Synchronized
    private fun bakeFor(camera: RawCameraProfiles.Camera, temperature: Float, film: FilmChoice): RawNegativeProviderCatalog.Bake {
        val key = "${camera.id}:${film.id}@${temperature.toRawBits()}:${camera.exposureBiasEv.toRawBits()}"
        runtimeBakes[key]?.let { return it }
        val extra = camera.fidelity.engineExtraMatrix()
        val bake = if (cameraBwProviders.supports(film.id)) {
            cameraBwProviders.bake(camera.id, temperature, camera.exposureBiasEv, extra, film.id)
        } else {
            requireNotNull(providerFor(film)).bake(temperature, camera.exposureBiasEv, extra, ::readCurve)
        }
        return bake.also { runtimeBakes[key] = it }
    }

    private fun readCurve(name: String): FloatArray =
        context.assets.open("raw-films/curves/$name").use(RawNegativeProviderCatalog::readQ15Curve)

    @Synchronized
    private fun rgbContextFor(camera: RawCameraProfiles.Camera, temperature: Float, film: FilmChoice): Pair<String, RawRgbContext> {
        val key = "${camera.id}:${film.id}@rgb:${temperature.toRawBits()}:${camera.exposureBiasEv.toRawBits()}"
        val parameters = rgbContexts.getOrPut(key) {
            requireNotNull(providerFor(film)).rgbContext(temperature, camera.exposureBiasEv,
                camera.fidelity.engineExtraMatrix(), ::readCurve)
        }
        return key to parameters
    }

    @Synchronized
    private fun rgbRendererFor(asset: Pair<String, RawRgbContext>): RawRgbFilmRenderer =
        rgbRenderers.getOrPut(asset.first) {
            RawRgbFilmRenderer(RawRgbGridBaker.bake(asset.second.bakeParameters()), asset.second.runtimeBw)
        }

    companion object {
        private val RGB_IDS = setOf(
            "ColorModeArgentiqueKodakEIR", "ColorModeArgentiqueKodakHIE", "ColorModeCineTealAndPink",
        )
    }
}
