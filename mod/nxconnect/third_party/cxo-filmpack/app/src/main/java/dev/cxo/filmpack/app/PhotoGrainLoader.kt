package dev.cxo.filmpack.app

import dev.cxo.filmpack.core.NativeGrain8Catalog
import dev.cxo.filmpack.core.NativeGrain8ColorLut
import dev.cxo.filmpack.core.NativeGrain8Geometry
import dev.cxo.filmpack.core.NativeGrain8Patch
import dev.cxo.filmpack.core.NativeGrain8Prepare
import dev.cxo.filmpack.core.NativeGrain8Resources
import dev.cxo.filmpack.core.NativeGrain8Orientation

/** Explicit source and native invocation-level request. Flags describe the rendering/toning context,
 * not merely the selected grain identifier. The caller owns a stable uint32 seed. */
data class PhotoGrainRequest(
    val sourceWidth: Int,
    val sourceHeight: Int,
    val sourceExifOrientation: Int,
    val grainIdentifier: String,
    val format: NativeGrain8Geometry.Format,
    val manualSize: Float,
    val intensity: Int,
    val seed: Long,
    val color: Boolean,
    val neutralizeRedBlue: Boolean,
    val invocationScale: Int = 0,
)

/** Load one original packed RGB8 source on demand and retain one prepared patch.
 * Call from a worker thread. This does not select film aliases/context flags,
 * install a resource pack or adapt arbitrary resized previews. GrainPackStore
 * owns installation (including the bundled asset); this class only owns the
 * decoded resource view and its bounded caches. Returned layers
 * use display coordinates at the requested level0..2; source hashes are checked by
 * the catalog. Source dimensions always refer to the original, never the preview. */
class PhotoGrainLoader(
    private val catalog: NativeGrain8Catalog,
    private val colorLut: NativeGrain8ColorLut,
    private val loadSource: (NativeGrain8Catalog.Definition) -> ByteArray,
) {
    private data class Key(val identifier: String, val scaleBits: Int, val seed: Long, val orientation: Int)
    private var sourceIdentifier: String? = null
    private var sourceRgb: ByteArray? = null
    private var preparationKey: Key? = null
    private var preparation: NativeGrain8Prepare.Result? = null

    @Synchronized
    fun prepare(request: PhotoGrainRequest): PhotoGrainLayer {
        require(request.sourceWidth > 0 && request.sourceHeight > 0) { "无法读取原照片尺寸" }
        val orientation = NativeGrain8Orientation.fromExifDisplayTransform(request.sourceExifOrientation)
        require(request.invocationScale in 0..2) { "颗粒预览暂只支持0到2级" }
        val width = request.sourceWidth shr request.invocationScale
        val height = request.sourceHeight shr request.invocationScale
        require(width > 0 && height > 0) { "照片尺寸不足以生成该级预览" }
        require(request.intensity in 0..200) { "颗粒强度必须在 0 到 200 之间" }
        require(request.seed in 0..0xFFFFFFFFL) { "颗粒种子必须为无符号32位整数" }
        val definition = requireNotNull(catalog[request.grainIdentifier]) { "缺少已验证的颗粒资源映射" }
        val geometry = NativeGrain8Geometry.resolve(definition.grainEnum,request.format,
            request.sourceWidth,request.sourceHeight,request.manualSize,request.invocationScale)
        val key = Key(definition.identifier,geometry.scale.toRawBits(),request.seed,orientation)
        val source = if (sourceIdentifier == definition.identifier) requireNotNull(sourceRgb)
            else loadSource(definition).copyOf()
        val prepared = if (preparationKey == key) requireNotNull(preparation)
            else definition.prepare(source,geometry.scale,colorLut,request.seed,orientation)
        // Commit only a successfully hash-checked preparation. A failed resource
        // read/validation leaves the last valid cache usable.
        sourceIdentifier = definition.identifier; sourceRgb = source
        preparationKey = key; preparation = prepared
        val kernel = NativeGrain8Patch(prepared.bytes,prepared.size,prepared.size,prepared.period,
            definition.amplitude(request.intensity),request.color,request.neutralizeRedBlue)
        return PhotoGrainLayer(if (orientation >= 4) height else width,if (orientation >= 4) width else height,
            kernel,width,height,request.sourceExifOrientation,request.invocationScale)
    }

    @Synchronized
    fun clear() {
        sourceIdentifier = null; sourceRgb = null; preparationKey = null; preparation = null
    }

    companion object {
        /** Keep resources open for the lifetime of this loader. The caller
         * supplies/owns its external pack; no resource file is bundled in the APK. */
        fun fromResources(resources: NativeGrain8Resources) = PhotoGrainLoader(resources.catalog,resources.colorLut()) {
            definition -> resources.readSource(definition.grainEnum)
        }
    }
}
