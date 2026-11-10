package dev.cxo.filmpack.core

import java.io.DataInputStream
import java.io.InputStream

/** Static RAW provider data. No captured WB matrices or baked photo grids are stored here. */
class RawNegativeProviderCatalog private constructor(private val films: Map<String, Film>) {
    val ids: Set<String> get() = films.keys.toSet()
    fun film(id: String): Film? = films[id]

    class Film internal constructor(
        val id: String,
        private val provider: RawNegativeProviderSpec,
        private val m1: FloatArray,
        private val m2: FloatArray,
        val typeBw: Boolean,
        val runtimeBw: Boolean,
        val requiresAdditionalColorStage: Boolean,
        private val renderingScales: RawRenderingScales,
        curveNames: List<String>,
    ) {
        private val names = curveNames.toList()
        val curveNames: List<String> get() = names.toList()

        /** Provider matrix at the selected intensity, with no color filter. */
        fun scaleFilterMatrix(intensity: Int = 100): FloatArray = renderingScales.defaultMatrix(intensity)

        fun colorStage(sceneIntent: Float, intensity: Int = 100): RawScaleFilterStage? {
            require(intensity in 0..200) { "Expected rendering intensity in 0..200" }
            // Native disables the optional stage at zero; even an identity
            // matrix with protection enabled would change saturated RGB.
            return if (requiresAdditionalColorStage && intensity > 0) {
                RawScaleFilterStage(scaleFilterMatrix(intensity), sceneIntent)
            } else null
        }

        fun parameters(temperature: Float, sourceCameraExposureBiasEv: Float): RawRenderingParameters =
            RawRenderingParameters.fromProvider(
                sourceCameraExposureBiasEv, provider.providerExposureEv(sourceCameraExposureBiasEv),
                provider.targetFidelity(temperature), m1, m2,
            )

        /** RGB providers share the static parameter contract, but use the RGB
         * baker instead of the HSV residual grid and post-curve construction.
         */
        fun rgbContext(
            temperature: Float,
            sourceCameraExposureBiasEv: Float,
            extraMatrix: FloatArray,
            readCurve: (String) -> FloatArray,
        ): RawRgbContext {
            require(extraMatrix.size == 9 && extraMatrix.all(Float::isFinite))
            val parameters = parameters(temperature, sourceCameraExposureBiasEv)
            return RawRgbContext(sourceCameraExposureBiasEv, parameters.renderingEv, runtimeBw,
                parameters.preMatrix(), parameters.postMatrix(), extraMatrix, filmCurves(readCurve))
        }

        private fun filmCurves(readCurve: (String) -> FloatArray): Array<FloatArray> =
            names.map { readCurve(it).copyOf() }.toTypedArray().also { curves ->
                require(curves.all { c -> c.size == 32769 && c.all { it.isFinite() && it in 0f..32767f } })
            }

        /** The caller supplies the actual camera exposure and the engine extra matrix. */
        fun bake(
            temperature: Float,
            sourceCameraExposureBiasEv: Float,
            extraMatrix: FloatArray,
            readCurve: (String) -> FloatArray,
        ): Bake {
            val parameters = parameters(temperature, sourceCameraExposureBiasEv)
            val curves = filmCurves(readCurve)
            val post = parameters.postMatrix()
            val forward = if (curves.size == 1) {
                arrayOf(NativePostCurves.buildForwardPostCurve(curves[0], parameters.renderingEv))
            } else {
                NativePostCurves.buildChannelForwardPostCurves(curves, parameters.renderingEv, post, extraMatrix)
            }
            val inverse = Array(forward.size) { NativePostCurves.inverseComposedPostCurve(forward[it]) }
            val raw = RawHsvGridBaker.bake(RawHsvBakeParameters(
                sourceCameraExposureBiasEv, parameters.renderingEv, parameters.preMatrix(), post,
                extraMatrix, curves, inverse, typeBw,
            ))
            val repaired = RawHsvGridBaker.repair(raw.grid, skipSaturationClamp = typeBw)
            require(repaired.all(Float::isFinite)) { "Nonfinite RAW provider grid: $id" }
            return Bake(parameters, curves, forward, inverse, raw.grid, repaired, runtimeBw)
        }
    }

    data class Bake(
        val parameters: RawRenderingParameters,
        val filmCurves: Array<FloatArray>,
        val forwardCurves: Array<FloatArray>,
        val inverseCurves: Array<FloatArray>,
        val preRepairGrid: FloatArray,
        val repairedGrid: FloatArray,
        val runtimeBw: Boolean,
    ) {
        fun renderer(colorStage: RawScaleFilterStage? = null): RawHsvFilmRenderer = RawHsvFilmRenderer(
            repairedGrid, forwardCurves[0], allowNegativeMultipliers = true,
            channelPostCurves = forwardCurves.takeIf { it.size == 3 }, runtimeBw = runtimeBw,
            colorStage = colorStage,
        )
    }

    companion object {
        fun read(input: InputStream): RawNegativeProviderCatalog = DataInputStream(input.buffered()).use { data ->
            val magic = ByteArray(8).also(data::readFully)
            require(magic.contentEquals("CXORNP01".toByteArray(Charsets.US_ASCII)))
            require(data.intLE() == 3) { "Unsupported RAW provider catalog" }
            val profileCount = data.intLE().also { require(it in 1..32) }
            val profiles = LinkedHashMap<Int, RawTargetFidelityProfile>()
            repeat(profileCount) {
                val id = data.intLE()
                val low = data.floatLE(); val high = data.floatLE()
                val profile = RawTargetFidelityProfile(data.matrix(), data.matrix(), low, high)
                require(profiles.put(id, profile) == null) { "Duplicate RAW target profile" }
            }
            val filmCount = data.intLE().also { require(it in 1..512) }
            val films = LinkedHashMap<String, Film>()
            repeat(filmCount) {
                val id = data.string()
                val profile = requireNotNull(profiles[data.intLE()]) { "Unknown RAW target profile" }
                val offset = data.floatLE()
                val typeBw = data.flag(); val runtimeBw = data.flag()
                val requiresAdditionalColorStage = data.flag()
                val scales = RawRenderingScales(data.floatLE(), data.floatLE(), data.floatLE())
                require(scales.requiresColorStage == requiresAdditionalColorStage) { "RAW provider scales flag mismatch" }
                val count = data.intLE().also { require(it == 1 || it == 3) }
                val curves = List(count) { data.string().also { name ->
                    require(name.matches(Regex("[A-Za-z0-9_.-]+\\.bin"))) { "Invalid RAW curve name" }
                } }
                val m1 = data.matrix(); val m2 = data.matrix()
                val film = Film(id, RawNegativeProviderSpec(profile, offset), m1, m2, typeBw, runtimeBw, requiresAdditionalColorStage, scales, curves)
                require(films.put(id, film) == null) { "Duplicate RAW provider film" }
            }
            require(data.read() == -1) { "Trailing RAW provider catalog data" }
            RawNegativeProviderCatalog(films)
        }

        fun readQ15Curve(input: InputStream): FloatArray = DataInputStream(input.buffered()).use { data ->
            val result = FloatArray(32769)
            for (i in 0 until 32768) {
                val code = java.lang.Short.reverseBytes(data.readShort()).toInt()
                require(code >= 0) { "Signed-negative RAW film curve" }
                result[i] = code.toFloat()
            }
            result[32768] = result[32767]
            require(data.read() == -1) { "Trailing RAW curve data" }
            result
        }

        private fun DataInputStream.intLE(): Int = Integer.reverseBytes(readInt())
        private fun DataInputStream.floatLE(): Float = Float.fromBits(intLE()).also { require(it.isFinite()) }
        private fun DataInputStream.matrix(): FloatArray = FloatArray(9) { floatLE() }
        private fun DataInputStream.flag(): Boolean = when (intLE()) { 0 -> false; 1 -> true; else -> error("Invalid RAW flag") }
        private fun DataInputStream.string(): String {
            val length = intLE().also { require(it in 1..256) }
            return ByteArray(length).also(::readFully).toString(Charsets.UTF_8)
        }
    }
}
