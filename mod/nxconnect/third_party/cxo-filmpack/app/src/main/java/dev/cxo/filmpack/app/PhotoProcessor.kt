package dev.cxo.filmpack.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.ColorSpace
import android.graphics.ImageDecoder
import android.graphics.Matrix
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.provider.DocumentsContract
import android.util.Log
import dev.cxo.filmpack.android.BitmapFilmAdapter
import dev.cxo.filmpack.android.ExifOrientation
import dev.cxo.filmpack.android.Srgb16TileTransform
import dev.cxo.filmpack.core.*
import org.json.JSONArray
import dev.cxo.filmpack.app.post.*
import java.io.File
import java.io.IOException
import java.util.concurrent.CancellationException
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.math.sqrt

data class FilmChoice(
    val id: String, val token: String, val name: String, val category: String,
    val experimental: Boolean, val model: String, val asset: String,
    val baseRed: Float, val baseGreen: Float, val baseBlue: Float,
) {
    override fun toString(): String = name + if (experimental) " · 实验" else ""
}

data class ExportResult(val width: Int, val height: Int, val elapsedMillis: Long, val resized: Boolean)

/** Frozen colour core, with managed Android import and cancellable tiled processing.
 * Call decoding/render/export from a worker thread. This product path supports
 * a prepared full-resolution grain layer before output ICC conversion.
 * Grain is optional; source orientation is applied exactly once.
 */
class PhotoProcessor(context: Context, private val exportWorkers: Int = 4,
                     private val useNativeEntry: Boolean = true, private val useNativeJpeg: Boolean = true) : java.io.Closeable {
    private val context = context.applicationContext
    private val resolver = this.context.contentResolver
    private val rawStoreDelegate = lazy { RawPhotoStore(this.context) }
    private val rawStore by rawStoreDelegate
    private val heifStoreDelegate = lazy { HeifJpegStore(this.context) }
    private val heifStore by heifStoreDelegate
    private val rawPipeline by lazy { RawRenderPipeline(this.context) }
    val films: List<FilmChoice> = loadFilms()
    private val entry by lazy {
        this.context.assets.open("films/srgb-entry-pix65.cxoentry").buffered().use { FixedSrgbEntryLutIO.read(it) }
    }
    private val nativeEntry by lazy {
        if (useNativeEntry && NativeEntryTransform.isSupported) NativeEntryTransform.create(entry.copyGrid()) else null
    }
    internal val nativeEntryEnabled: Boolean get() = nativeEntry != null
    private val outputCmm by lazy {
        FixedWideGamutToSrgb.read(this.context.assets.open("films/output-cmm/widegamut-to-srgb.lcmsbin").use { it.readBytes() })
    }
    private val grainContexts by lazy {
        NativeGrain8RenderingContext.read(this.context.assets.open("films/no-toning-context.cxoctx").use { it.readBytes() })
    }
    private var cachedId: String? = null
    private var cachedLut: Any? = null
    private var cachedIntensity = -1
    private var cachedTransform: ((ShortArray, FloatArray, Int, Int) -> Unit)? = null

    private fun loadFilms(): List<FilmChoice> {
        val rows = JSONArray(context.assets.open("films.json").bufferedReader().use { it.readText() })
        return List(rows.length()) { index ->
            val row = rows.getJSONObject(index)
            FilmChoice(row.getString("id"),row.getString("token"),row.getString("name"),
                row.getString("category"),row.getBoolean("experimental"),row.getString("model"),
                row.getString("asset"),row.getDouble("baseRed").toFloat(),
                row.getDouble("baseGreen").toFloat(),row.getDouble("baseBlue").toFloat())
        }.also { rows -> require(rows.isNotEmpty() && rows.map { it.id }.toSet().size == rows.size) }
    }

    /** Worker-only: invalidate cached input when the user chooses a photograph anew. */
    internal fun beginImport() {
        if (heifStoreDelegate.isInitialized()) heifStore.invalidate()
    }

    fun decodePreview(uri: Uri, maxEdge: Int = 1280, cancelled: () -> Boolean = { false }): Bitmap {
        checkCancelled(cancelled)
        heifStore.normalize(uri, cancelled)?.let { return decodePreview(it, maxEdge, cancelled) }
        rawStore.invalidate()
        rawStore.openAppleProRaw(uri, cancelled)?.use { return decodeDngPreview(it, maxEdge, 2_000_000L, cancelled) }
        rawStore.openCameraIfRaw(uri,cancelled)?.use { raw ->
            rawPipeline.requireSupported(raw, null)
            return rawPipeline.renderPreview(raw, null, maxEdge, 2_000_000L, cancelled)
        }
        checkCancelled(cancelled)
        return decode(uri,maxEdge,2_000_000L).bitmap
    }

    /** Resolve flags from the active film rendering type, independently of the
     * explicit grain source. The photo app has no toning stage enabled. */
    fun grainRequest(preview: PhotoGrainPreview, film: FilmChoice, grainIdentifier: String,
                     format: NativeGrain8Geometry.Format, manualSize: Float, intensity: Int, seed: Long,
                     fullResolution: Boolean = false): PhotoGrainRequest {
        val flags = grainContexts.resolve(film.token)
        val request = preview.request(grainIdentifier,format,manualSize,intensity,seed,flags.color,flags.neutralizeRedBlue)
        return if (fullResolution) request.copy(invocationScale=0) else request
    }

    /** Bounded grain-aware preview: preserve original geometry and use an
     * explicit native level, rather than infer grain size from a resized bitmap.
     * Standard EXIF display transform; levels0..2 without crop/zoom. Four million
     * pixels bound each ARGB buffer to 16MB and admit 51MP sources at level2,
     * without inventing an unsupported native level3. */
    fun decodeGrainPreview(uri: Uri, maxEdge: Int = 4096, maxPixels: Long = 4_000_000L,
                           cancelled: () -> Boolean = { false }): PhotoGrainPreview {
        require(maxEdge > 0 && maxPixels in 1..4_000_000L) { "颗粒预览尺寸超出支持范围" }
        checkCancelled(cancelled)
        heifStore.normalize(uri, cancelled)?.let { return decodeGrainPreview(it, maxEdge, maxPixels, cancelled) }
        rawStore.invalidate()
        rawStore.openAppleProRaw(uri, cancelled)?.use { raw ->
            val level = (0..2).firstOrNull { scale ->
                val w = raw.width shr scale; val h = raw.height shr scale
                w > 0 && h > 0 && max(w, h) <= maxEdge && w.toLong() * h <= maxPixels
            } ?: throw IllegalArgumentException("照片需要尚未支持的颗粒预览级别")
            return PhotoGrainPreview(decodeDngPreview(raw, maxEdge, maxPixels, cancelled, level),
                raw.width, raw.height, level, raw.orientation)
        }
        rawStore.openCameraIfRaw(uri, cancelled)?.use { raw ->
            rawPipeline.requireSupported(raw, null)
            val level = (0..2).firstOrNull { scale ->
                val w = raw.width shr scale; val h = raw.height shr scale
                w > 0 && h > 0 && max(w, h) <= maxEdge && w.toLong() * h <= maxPixels
            } ?: throw IllegalArgumentException("照片需要尚未支持的颗粒预览级别")
            val width = raw.width shr level; val height = raw.height shr level
            val bitmap = rawPipeline.renderPreview(raw, null, max(width, height),
                width.toLong() * height, cancelled,
                targetDisplaySize = if (raw.orientation >= 5) height to width else width to height)
            return PhotoGrainPreview(bitmap, raw.width, raw.height, level, raw.orientation)
        }
        val metadata = requireNotNull(RegionSrgbPhoto.open(context,uri)) {
            "颗粒预览暂只支持JPEG或PNG照片"
        }.use { source ->
            Triple(source.width,source.height,source.orientation)
        }
        val (sourceWidth,sourceHeight,orientation) = metadata
        val level = (0..2).firstOrNull { scale ->
            val w = sourceWidth shr scale; val h = sourceHeight shr scale
            w > 0 && h > 0 && max(w,h) <= maxEdge && w.toLong()*h <= maxPixels
        } ?: throw IllegalArgumentException("照片需要尚未支持的颗粒预览级别")
        val rawWidth = sourceWidth shr level; val rawHeight = sourceHeight shr level
        val width = if (orientation >= 5) rawHeight else rawWidth
        val height = if (orientation >= 5) rawWidth else rawHeight
        val displayWidth = if (orientation >= 5) sourceHeight else sourceWidth
        val displayHeight = if (orientation >= 5) sourceWidth else sourceHeight
        var bitmap = if (Build.VERSION.SDK_INT >= 28) {
            ImageDecoder.decodeBitmap(ImageDecoder.createSource(resolver,uri)) { decoder,info,_ ->
                require(info.size.width == displayWidth && info.size.height == displayHeight) { "读取期间照片尺寸已改变" }
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
                decoder.setTargetSize(width,height)
            }
        } else {
            val options = BitmapFactory.Options().apply {
                inSampleSize = 1 shl level
                inPreferredConfig = Bitmap.Config.ARGB_8888
                inPreferredColorSpace = ColorSpace.get(ColorSpace.Named.SRGB)
            }
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it,null,options) }
                ?: throw IOException("无法读取颗粒预览照片")
        }
        try {
            if (Build.VERSION.SDK_INT < 28) {
                if (bitmap.width != rawWidth || bitmap.height != rawHeight) {
                    val scaled = Bitmap.createScaledBitmap(bitmap,rawWidth,rawHeight,true)
                    if (scaled !== bitmap) bitmap.recycle()
                    bitmap = scaled
                }
                val matrix = Matrix().apply {
                    when (orientation) {
                        2 -> setScale(-1f,1f)
                        3 -> setRotate(180f)
                        4 -> setScale(1f,-1f)
                        5 -> { setRotate(90f); postScale(-1f,1f) }
                        6 -> setRotate(90f)
                        7 -> { setRotate(-90f); postScale(-1f,1f) }
                        8 -> setRotate(-90f)
                    }
                }
                if (!matrix.isIdentity) {
                    val oriented = Bitmap.createBitmap(bitmap,0,0,bitmap.width,bitmap.height,matrix,false)
                    if (oriented !== bitmap) bitmap.recycle()
                    bitmap = oriented
                }
            }
            // Older BitmapFactory decoders can round sampled odd extents up.
            // Explicitly enforce the observed native grid; interpolation remains Android's.
            if (bitmap.width != width || bitmap.height != height) {
                val scaled = Bitmap.createScaledBitmap(bitmap,width,height,true)
                if (scaled !== bitmap) bitmap.recycle()
                bitmap = scaled
            }
            if (bitmap.config != Bitmap.Config.ARGB_8888) {
                val converted = bitmap.copy(Bitmap.Config.ARGB_8888,false) ?: throw IOException("无法创建颗粒预览缓冲区")
                bitmap.recycle(); bitmap = converted
            }
            return PhotoGrainPreview(bitmap,sourceWidth,sourceHeight,level,orientation)
        } catch (failure: Throwable) {
            bitmap.recycle(); throw failure
        }
    }

    private data class Decoded(val bitmap: Bitmap, val resized: Boolean)

    private fun dimensions(width: Int, height: Int, maxEdge: Int, maxPixels: Long): Pair<Int,Int> {
        require(width > 0 && height > 0) { "无法读取照片尺寸" }
        val scale = minOf(1.0,maxEdge.toDouble()/max(width,height),
            sqrt(maxPixels.toDouble()/(width.toLong()*height)))
        return maxOf(1,(width*scale).roundToInt()) to maxOf(1,(height*scale).roundToInt())
    }

    private fun decode(uri: Uri, maxEdge: Int, maxPixels: Long): Decoded {
        if (Build.VERSION.SDK_INT >= 28) {
            var resized = false
            val bitmap = ImageDecoder.decodeBitmap(ImageDecoder.createSource(resolver,uri)) { decoder,info,_ ->
                val (width,height) = dimensions(info.size.width,info.size.height,maxEdge,maxPixels)
                resized = width != info.size.width || height != info.size.height
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB))
                decoder.setTargetSize(width,height)
            }
            // Keep a predictable 8-bit photo working buffer. The managed decoder
            // already converts the embedded profile and applies EXIF orientation.
            if (bitmap.config == Bitmap.Config.ARGB_8888) return Decoded(bitmap,resized)
            val copy = bitmap.copy(Bitmap.Config.ARGB_8888,false)
                ?: throw IOException("无法创建照片工作缓冲区")
            bitmap.recycle()
            return Decoded(copy,resized)
        }
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        (resolver.openInputStream(uri) ?: throw IOException("无法打开照片")).use {
            BitmapFactory.decodeStream(it,null,bounds)
        }
        val (width,height) = dimensions(bounds.outWidth,bounds.outHeight,maxEdge,maxPixels)
        var sample = 1
        while (bounds.outWidth/(sample*2) >= width && bounds.outHeight/(sample*2) >= height) sample *= 2
        val options = BitmapFactory.Options().apply {
            inSampleSize = sample
            inPreferredConfig = Bitmap.Config.ARGB_8888
            inPreferredColorSpace = ColorSpace.get(ColorSpace.Named.SRGB)
        }
        var bitmap = resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it,null,options) }
            ?: throw IOException("不支持此照片格式或文件已损坏")
        if (bitmap.width != width || bitmap.height != height) {
            val scaled = Bitmap.createScaledBitmap(bitmap,width,height,true)
            if (scaled !== bitmap) bitmap.recycle()
            bitmap = scaled
        }
        val orientation = resolver.openInputStream(uri)?.use { ExifOrientation.readJpeg(it) } ?: 1
        val matrix = Matrix().apply {
            when (orientation) {
                2 -> setScale(-1f,1f)
                3 -> setRotate(180f)
                4 -> setScale(1f,-1f)
                5 -> { setRotate(90f); postScale(-1f,1f) }
                6 -> setRotate(90f)
                7 -> { setRotate(-90f); postScale(-1f,1f) }
                8 -> setRotate(-90f)
            }
        }
        if (!matrix.isIdentity) {
            val oriented = Bitmap.createBitmap(bitmap,0,0,bitmap.width,bitmap.height,matrix,true)
            if (oriented !== bitmap) bitmap.recycle()
            bitmap = oriented
        }
        return Decoded(bitmap,width != bounds.outWidth || height != bounds.outHeight)
    }

    @Synchronized
    internal fun transform(film: FilmChoice, intensity: Int): (ShortArray,FloatArray,Int,Int) -> Unit {
        require(intensity in 0..200) { "强度必须在 0 到 200 之间" }
        if (film.id == cachedId && intensity == cachedIntensity) return requireNotNull(cachedTransform)
        val lut = if (film.id == cachedId) requireNotNull(cachedLut) else when (film.model) {
            "rgb" -> context.assets.open(film.asset).buffered().use { RgbFilmLutIO.read(it) }
            "hsv", "direct", "provider-bw" -> context.assets.open(film.asset).buffered().use {
                HsvFilmLutIO.read(it,allowEndpoint32768 = film.model == "direct")
            }
            else -> throw IOException("该胶片暂不可用")
        }
        // Same operation order as NativeWhiteBalanceRgb.create and the two
        // withWideGamutIntensity factories. WB continues above100; film blend caps at1.
        val t = intensity.toFloat()/100f
        val gains = NativeWhiteBalanceRgb.fromGains(
            (1f-t)+t*film.baseRed, (1f-t)+t*film.baseGreen, (1f-t)+t*film.baseBlue)
        val acceleratedEntry: ((ShortArray,FloatArray,Int,Int) -> Unit)? = nativeEntry?.let { accelerator ->
            { src,dst,width,x -> accelerator.render(src,dst,width,x) }
        }
        val render: (ShortArray,FloatArray,Int,Int) -> Unit = when (lut) {
            is RgbFilmLut -> {
                val pipeline = FixedSrgbRgbFilmPipeline(entry,lut,gains,minOf(t,1f),acceleratedEntry)
                val function: (ShortArray,FloatArray,Int,Int) -> Unit =
                    { src,dst,width,x ->
                        pipeline.renderRgb16ExactSampling0(src,dst,width,x)
                    }
                function
            }
            is HsvFilmLut -> {
                val pipeline = FixedSrgbFilmPipeline(entry,lut,gains,minOf(t,1f),acceleratedEntry)
                val function: (ShortArray,FloatArray,Int,Int) -> Unit =
                    { src,dst,width,x ->
                        pipeline.renderRgb16ExactSampling0(src,dst,width,x)
                    }
                function
            }
            else -> throw IOException("该胶片暂不可用")
        }
        cachedId = film.id; cachedLut = lut; cachedIntensity = intensity; cachedTransform = render
        return render
    }

    fun render(source: Bitmap, film: FilmChoice, progress: (Int) -> Unit, cancelled: () -> Boolean,
               intensity: Int = 100, grain: PhotoGrainLayer? = null, rawUri: Uri? = null,
               post: PostEditingSettings = PostEditingSettings()): Bitmap {
        checkCancelled(cancelled)
        require(intensity in 0..200) { "强度必须在 0 到 200 之间" }
        if (rawUri != null) {
            rawStore.openAppleProRaw(rawUri, cancelled)?.use { raw ->
                grain?.requireDimensions(source.width, source.height)
                val result = renderProfiledDngPreview(raw, source.width, source.height,
                    dngTransform(raw, film, intensity, cancelled), grain, cancelled)
                progress(100)
                return applyPostEditing(context, result, post, cancelled)
            }
            rawStore.openCameraIfRaw(rawUri, cancelled)?.use { raw ->
                rawPipeline.requireSupported(raw, film)
                progress(10)
                grain?.requireDimensions(source.width, source.height)
                val result = rawPipeline.renderPreview(raw, film,
                    max(source.width, source.height).coerceAtLeast(1),
                    source.width.toLong() * source.height, cancelled, intensity,
                    targetDisplaySize = source.width to source.height) { row, width, y ->
                    grain?.renderPreviewSourceLayout(row, row, width, 0, y)
                }
                val edited=applyPostEditing(context,result,post,cancelled)
                progress(100)
                return edited
            }
        }
        grain?.requireDimensions(source.width,source.height)
        val apply = transform(film,intensity)
        val total = source.width.toLong()*source.height
        var completed = 0L; var last = -1
        val result = BitmapFilmAdapter(256,16).renderSrgb16(source,transform = Srgb16TileTransform { input,output,count,x,y,width ->
            checkCancelled(cancelled)
            val src = if (input.size == count*3) input else input.copyOf(count*3)
            val dst = if (output.size == count*3) output else FloatArray(count*3)
            apply(src,dst,width,x)
            grain?.kernel?.renderNormalizedRgb(dst,dst,width,x,y)
            outputCmm.render(dst,dst)
            if (dst !== output) dst.copyInto(output)
            completed += count
            val percent = (completed*100/total).toInt()
            if (percent != last) { last = percent; progress(percent) }
            checkCancelled(cancelled)
        })
        return applyPostEditing(context,result,post,cancelled)
    }

    fun export(sourceUri: Uri, film: FilmChoice, outputUri: Uri,
               progress: (Int) -> Unit, cancelled: () -> Boolean, intensity: Int = 100,
               grain: PhotoGrainLayer? = null, post: PostEditingSettings = PostEditingSettings(), settings: ExportSettings = ExportSettings()): ExportResult {
        require(sourceUri != outputUri) { "请选择新的保存位置，以保留原照片" }
        require(intensity in 0..200) { "强度必须在 0 到 200 之间" }
        val start = SystemClock.elapsedRealtime()
        var decoded: Bitmap? = null; var rendered: Bitmap? = null
        try {
            checkCancelled(cancelled); progress(0)
            heifStore.normalize(sourceUri, cancelled)?.let { normalized ->
                val result = export(normalized, film, outputUri, progress, cancelled, intensity, grain, post, settings)
                return result.copy(elapsedMillis = SystemClock.elapsedRealtime() - start)
            }
            rawStore.openAppleProRaw(sourceUri, cancelled)?.use { source ->
                return exportRgb16Rows(source.width, source.height, source.orientation,
                    source::readRows, dngTransform(source, film, intensity, cancelled), "Apple-ProRAW", film, outputUri,
                    progress, cancelled, grain, start, post, settings)
            }
            rawStore.openCameraIfRaw(sourceUri, cancelled)?.use { source ->
                rawPipeline.requireSupported(source, film)
                return exportRawCamera(source, film, outputUri, progress, cancelled, intensity, grain, start, post, settings)
            }
            // JPEG/PNG can be decoded in bounded strips and encoded MCU-by-MCU.
            // Preserve EXIF orientation in the output rather than repeatedly
            // decoding full-height narrow columns for rotated source JPEGs.
            RegionSrgbPhoto.open(context,sourceUri)?.use { source ->
                if (grain != null) {
                    grain.requireSource(source.width,source.height,source.orientation)
                }
                val apply = transform(film,intensity)
                val width = source.width
                val postRenderer=if(post.isIdentity)null else PostEditingRenderer(context,post,width,source.height,source.orientation)
                var readNanos = 0L; var packNanos = 0L; var computeNanos = 0L; var quantizeNanos = 0L
                val streamingStart = System.nanoTime()
                // Four tasks each receive up to eight complete rows. The bounded
                // batch avoids waking and parking workers for every JPEG MCU row.
                val batchRows = (524_288/width/8*8).coerceIn(8,32)
                var input = ShortArray(width*batchRows*3)
                var output = FloatArray(width*batchRows*3)
                val pixels = IntArray(width*batchRows)
                var batchTop = -1
                ParallelFilmTransform(apply,cancelled,after = { tile,w,x,y ->
                    grain?.renderSourceLayout(tile,tile,w,x,y)
                    outputCmm.render(tile,tile)
                },workers=exportWorkers).use { parallel ->
                    resolver.openOutputStream(outputUri,"wt")?.use { stream ->
                        ExportImageWriter.write(stream,width,source.height,source.orientation,settings,nativeJpegEnabled=useNativeJpeg,cancelled=cancelled) { top,count,argb ->
                            checkCancelled(cancelled)
                            if (batchTop < 0 || top >= batchTop+batchRows) {
                                batchTop = top
                                val rows = minOf(batchRows,source.height-top)
                                var tick = System.nanoTime()
                                source.readRows(top,rows,pixels)
                                readNanos += System.nanoTime()-tick
                                tick = System.nanoTime()
                                val size = width*rows*3
                                if (input.size != size) { input = ShortArray(size); output = FloatArray(size) }
                                for (i in 0 until width*rows) {
                                    val pixel = pixels[i]; val offset = i*3
                                    input[offset] = (Color.red(pixel)*257).toShort()
                                    input[offset+1] = (Color.green(pixel)*257).toShort()
                                    input[offset+2] = (Color.blue(pixel)*257).toShort()
                                }
                                packNanos += System.nanoTime()-tick
                                tick = System.nanoTime()
                                parallel.render(input,output,width,0,top)
                                computeNanos += System.nanoTime()-tick
                                tick = System.nanoTime()
                                for (i in 0 until width*rows) {
                                    val offset = i*3; val alpha = Color.alpha(pixels[i])
                                    fun channel(index: Int): Int {
                                        val value = output[offset+index]
                                        require(value.isFinite()) { "胶片计算产生无效色彩" }
                                        val encoded = (value.coerceIn(0f,1f)*255f).roundToInt()
                                        return (encoded*alpha+255*(255-alpha)+127)/255
                                    }
                                    pixels[i] = Color.rgb(channel(0),channel(1),channel(2))
                                }
                                postRenderer?.apply(pixels,top,width*rows)
                                quantizeNanos += System.nanoTime()-tick
                            }
                            pixels.copyInto(argb,0,(top-batchTop)*width,(top-batchTop+count)*width)
                            progress(((top+count).toLong()*98/source.height).toInt())
                            checkCancelled(cancelled)
                        }
                    } ?: throw IOException("无法写入所选位置")
                }
                val streamingNanos = System.nanoTime()-streamingStart
                Log.i("CXOPerf", "${film.token} ${width}x${source.height} " +
                    "readMs=${readNanos/1_000_000} packMs=${packNanos/1_000_000} " +
                    "filmGrainCmmMs=${computeNanos/1_000_000} quantizeMs=${quantizeNanos/1_000_000} " +
                    "encodeIoOtherMs=${(streamingNanos-readNanos-packNanos-computeNanos-quantizeNanos)/1_000_000} " +
                    "totalMs=${SystemClock.elapsedRealtime()-start}")
                checkCancelled(cancelled); progress(100)
                val dimensions = settings.dimensions(source.displayWidth,source.displayHeight)
                return ExportResult(dimensions.first,dimensions.second,SystemClock.elapsedRealtime()-start,false)
            }
            // Reserve room for source + destination + decode/codec transients and
            // the retained small previews. Large photos are explicitly reported resized.
            require(grain == null) { "颗粒保存需要支持原尺寸分区读取的照片" }
            val pixels = minOf(32_000_000L,maxOf(1_000_000L,Runtime.getRuntime().maxMemory()*35/100/12))
            val photo = decode(sourceUri,Int.MAX_VALUE,pixels)
            decoded = photo.bitmap
            checkCancelled(cancelled); progress(5)
            val processed = render(photo.bitmap,film,{ progress(5+it*90/100) },cancelled,intensity,post=post)
            rendered = processed
            // JPEG has no alpha: composite transparency over white in place.
            if (processed.hasAlpha()) flattenOverWhite(processed,cancelled)
            checkCancelled(cancelled); progress(97)
            resolver.openOutputStream(outputUri,"wt")?.use { stream ->
                ExportImageWriter.write(stream,processed.width,processed.height,1,settings,nativeJpegEnabled=useNativeJpeg,cancelled=cancelled) { top,count,argb ->
                    checkCancelled(cancelled)
                    processed.getPixels(argb,0,processed.width,0,top,processed.width,count)
                }
                stream.flush()
            } ?: throw IOException("无法写入所选位置")
            checkCancelled(cancelled); progress(100)
            val dimensions = settings.dimensions(processed.width,processed.height)
            return ExportResult(dimensions.first,dimensions.second,SystemClock.elapsedRealtime()-start,photo.resized)
        } catch (failure: Throwable) {
            deleteOutput(outputUri)
            throw failure
        } finally {
            rendered?.recycle(); decoded?.recycle()
        }
    }

    private fun dngTransform(source: RawSrgb16Photo, film: FilmChoice?, intensity: Int,
                             cancelled: () -> Boolean): (ShortArray, FloatArray, Int, Int) -> Unit =
        rawPipeline.prepareDngRows(source, film, intensity,
            dev.cxo.filmpack.core.DngRenderingProfile.sensorToWorking(),
            dev.cxo.filmpack.core.DngRenderingProfile.filmExtraMatrix(),
            dev.cxo.filmpack.core.DngRenderingProfile.originalRenderer(), cancelled)

    private fun decodeDngPreview(source: RawSrgb16Photo, maxEdge: Int, maxPixels: Long,
                                 cancelled: () -> Boolean, level: Int? = null): Bitmap {
        val scale = minOf(1.0, maxEdge.toDouble() / max(source.width, source.height),
            kotlin.math.sqrt(maxPixels.toDouble() / (source.width.toLong() * source.height)))
        val width = level?.let { source.width shr it } ?: max(1, (source.width * scale).toInt())
        val height = level?.let { source.height shr it } ?: max(1, (source.height * scale).toInt())
        return renderProfiledDngPreview(source,
            if (source.orientation >= 5) height else width,
            if (source.orientation >= 5) width else height,
            dngTransform(source, null, 100, cancelled), null, cancelled)
    }

    private fun renderProfiledDngPreview(source: RawSrgb16Photo, displayWidth: Int, displayHeight: Int,
                                         apply: (ShortArray, FloatArray, Int, Int) -> Unit,
                                         grain: PhotoGrainLayer?, cancelled: () -> Boolean): Bitmap {
        val width = if (source.orientation >= 5) displayHeight else displayWidth
        val height = if (source.orientation >= 5) displayWidth else displayHeight
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val row = ShortArray(source.width * 3)
        val sampled = ShortArray(width * 3)
        val working = FloatArray(sampled.size)
        val pixels = IntArray(width)
        try {
            for (y in 0 until height) {
                checkCancelled(cancelled)
                source.readRows((y.toLong() * source.height / height).toInt(), 1, row)
                for (x in 0 until width) {
                    val from = (x.toLong() * source.width / width).toInt() * 3
                    row.copyInto(sampled, x * 3, from, from + 3)
                }
                apply(sampled, working, width, 0)
                grain?.renderPreviewSourceLayout(working, working, width, 0, y)
                outputCmm.render(working, working)
                for (x in 0 until width) {
                    fun channel(c: Int) = (working[x * 3 + c].coerceIn(0f, 1f) * 255f).roundToInt()
                    pixels[x] = Color.rgb(channel(0), channel(1), channel(2))
                }
                bitmap.setPixels(pixels, 0, width, 0, y, width, 1)
            }
            checkCancelled(cancelled)
            return source.applyOrientation(bitmap)
        } catch (failure: Throwable) { bitmap.recycle(); throw failure }
    }

    private class RawExportScratch(size: Int) {
        val normalized = ShortArray(size)
        val working = ShortArray(size)
        val encoded = ShortArray(size)
    }

    /** Prepare one immutable RAW plan and give each row worker private scratch. */
    private fun exportRawCamera(source: RawCameraLinear16Photo, film: FilmChoice, outputUri: Uri,
                                progress: (Int) -> Unit, cancelled: () -> Boolean, intensity: Int,
                                grain: PhotoGrainLayer?, start: Long, post: PostEditingSettings, settings: ExportSettings): ExportResult {
        val apply = rawPipeline.prepareRows(source, film, intensity, cancelled)
        val scratch = ThreadLocal<RawExportScratch>()
        return exportRgb16Rows(source.width, source.height, source.orientation, source::readRows,
            { rawInput, output, _, _ ->
                val local = scratch.get()?.takeIf { it.encoded.size == rawInput.size }
                    ?: RawExportScratch(rawInput.size).also { scratch.set(it) }
                apply(rawInput, local.normalized, local.working, local.encoded)
                for (i in output.indices) output[i] = local.encoded[i].toInt().coerceIn(0, 32767) / 32767f
            }, "RAW-camera", film, outputUri, progress, cancelled, grain, start, post, settings)
    }

    /** Both decoded RGB16 sources share the source-size grain/CMM/post/export stages. */
    private fun exportRgb16Rows(width: Int, height: Int, orientation: Int,
                               readRows: (Int, Int, ShortArray) -> Unit,
                               apply: (ShortArray, FloatArray, Int, Int) -> Unit, route: String,
                               film: FilmChoice, outputUri: Uri, progress: (Int) -> Unit,
                               cancelled: () -> Boolean, grain: PhotoGrainLayer?, start: Long,
                               post: PostEditingSettings, settings: ExportSettings): ExportResult {
        grain?.requireSource(width, height, orientation)
        var readNanos = 0L; var computeNanos = 0L; var packNanos = 0L
        val streamStart = System.nanoTime()
        val postRenderer = if (post.isIdentity) null else PostEditingRenderer(context, post, width, height, orientation)
        val batchRows = (524_288 / width / 8 * 8).coerceIn(8, 32)
        var input = ShortArray(width * batchRows * 3)
        var floats = FloatArray(input.size)
        val pixels = IntArray(width * batchRows)
        var batchTop = -1
        ParallelFilmTransform(apply = apply, cancelled = cancelled, after = { tile, w, x, y ->
            // Grain stays in source coordinates, before the output ICC stage.
            grain?.renderSourceLayout(tile, tile, w, x, y)
            outputCmm.render(tile, tile)
        }, workers = exportWorkers).use { parallel ->
            resolver.openOutputStream(outputUri, "wt")?.use { stream ->
                ExportImageWriter.write(stream, width, height, orientation, settings,
                    nativeJpegEnabled = useNativeJpeg, cancelled = cancelled) { top, count, argb ->
                    checkCancelled(cancelled)
                    if (batchTop < 0 || top >= batchTop + batchRows) {
                        batchTop = top
                        val rows = minOf(batchRows, height - top)
                        val size = width * rows * 3
                        if (input.size != size) { input = ShortArray(size); floats = FloatArray(size) }
                        var tick = System.nanoTime()
                        readRows(top, rows, input)
                        readNanos += System.nanoTime() - tick
                        tick = System.nanoTime()
                        parallel.render(input, floats, width, 0, top)
                        computeNanos += System.nanoTime() - tick
                        tick = System.nanoTime()
                        for (i in 0 until width * rows) {
                            val o = i * 3
                            pixels[i] = Color.rgb((floats[o].coerceIn(0f, 1f) * 255f).roundToInt(),
                                (floats[o + 1].coerceIn(0f, 1f) * 255f).roundToInt(),
                                (floats[o + 2].coerceIn(0f, 1f) * 255f).roundToInt())
                        }
                        postRenderer?.apply(pixels,top,width*rows)
                        packNanos += System.nanoTime() - tick
                    }
                    pixels.copyInto(argb, 0, (top - batchTop) * width, (top - batchTop + count) * width)
                    progress(((top + count).toLong() * 98 / height).toInt())
                }
            } ?: throw IOException("无法写入所选位置")
        }
        checkCancelled(cancelled); progress(100)
        val streamNanos = System.nanoTime() - streamStart
        Log.i("CXOPerf", "$route ${film.token} ${width}x${height} workers=$exportWorkers " +
            "readMs=${readNanos/1_000_000} filmGrainCmmMs=${computeNanos/1_000_000} packMs=${packNanos/1_000_000} " +
            "encodeIoOtherMs=${(streamNanos-readNanos-computeNanos-packNanos)/1_000_000} totalMs=${SystemClock.elapsedRealtime() - start}")
        val dimensions = settings.dimensions(if (orientation >= 5) height else width, if (orientation >= 5) width else height)
        return ExportResult(dimensions.first,dimensions.second,SystemClock.elapsedRealtime() - start,false)
    }

    private fun flattenOverWhite(bitmap: Bitmap, cancelled: () -> Boolean) {
        val row = IntArray(bitmap.width)
        for (y in 0 until bitmap.height) {
            checkCancelled(cancelled)
            bitmap.getPixels(row,0,row.size,0,y,row.size,1)
            for (i in row.indices) {
                val pixel = row[i]; val alpha = Color.alpha(pixel)
                if (alpha != 255) {
                    fun channel(value: Int) = (value*alpha+255*(255-alpha)+127)/255
                    row[i] = Color.rgb(channel(Color.red(pixel)),channel(Color.green(pixel)),channel(Color.blue(pixel)))
                }
            }
            bitmap.setPixels(row,0,row.size,0,y,row.size,1)
        }
        bitmap.setHasAlpha(false)
    }

    private fun checkCancelled(cancelled: () -> Boolean) {
        if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("已取消")
    }

    internal fun deleteOutput(uri: Uri) {
        runCatching {
            when {
                uri.scheme == "file" -> File(requireNotNull(uri.path)).delete()
                DocumentsContract.isDocumentUri(context,uri) -> DocumentsContract.deleteDocument(resolver,uri)
                else -> resolver.delete(uri,null,null)
            }
        }
    }

    /** Call on the processing worker after queued work has observed cancellation. */
    override fun close() {
        if (heifStoreDelegate.isInitialized()) heifStore.close()
        if (rawStoreDelegate.isInitialized()) rawStore.close()
    }
}
