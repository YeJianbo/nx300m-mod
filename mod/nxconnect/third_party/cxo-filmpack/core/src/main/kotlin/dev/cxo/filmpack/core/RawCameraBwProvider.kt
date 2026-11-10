package dev.cxo.filmpack.core

/** Camera-aware HIE Filtre / Rollei IR400 RAW providers, default WideGamut path.
 *
 * Inputs are static camera calibration evaluated at the photo's temperature.
 * No photo-captured matrix, intent, or baked grid belongs in this provider.
 * Film strength 100, no optional color filter or grain; both BW flags are true.
 */
class RawCameraBwProvider(
    val id: String,
    filmCurve: FloatArray,
    postMatrix: FloatArray,
    val useCameraColorMatrix: Boolean,
) {
    private val curve = checkedCurve(filmCurve)
    private val post = checkedMatrix(postMatrix)

    /** Native Compose2LUT(int16): film[cameraTone[i]], with no interpolation. */
    fun composeFilmCurve(sourceToneCurve: FloatArray): FloatArray {
        val tone = checkedCurve(sourceToneCurve)
        return FloatArray(32769) { i -> curve[tone[i.coerceAtMost(32767)].toInt()] }
    }

    /** Framework 3fe81c inverse and 3fe908 product, distinct from Engine inverse. */
    fun providerMatrix(sourceJpegBeforeTc: FloatArray, sourceFidelity: FloatArray): FloatArray =
        if (useCameraColorMatrix) {
            multiply(checkedMatrix(sourceJpegBeforeTc), inverse(checkedMatrix(sourceFidelity)))
        } else identity()

    fun bake(
        sourceCameraExposureBiasEv: Float,
        extraMatrix: FloatArray,
        sourceToneCurve: FloatArray,
        sourceJpegBeforeTc: FloatArray,
        sourceFidelity: FloatArray,
    ): RawNegativeProviderCatalog.Bake {
        val parameters = RawRenderingParameters.fromProvider(
            sourceCameraExposureBiasEv, sourceCameraExposureBiasEv, identity(),
            providerMatrix(sourceJpegBeforeTc, sourceFidelity), post,
        )
        val curves = arrayOf(composeFilmCurve(sourceToneCurve))
        val forward = arrayOf(NativePostCurves.buildForwardPostCurve(curves[0], parameters.renderingEv))
        val inverse = arrayOf(NativePostCurves.inverseComposedPostCurve(forward[0]))
        val raw = RawHsvGridBaker.bake(RawHsvBakeParameters(
            sourceCameraExposureBiasEv, parameters.renderingEv, parameters.preMatrix(),
            parameters.postMatrix(), checkedMatrix(extraMatrix), curves, inverse, true,
        ))
        val repaired = RawHsvGridBaker.repair(raw.grid, skipSaturationClamp = true)
        require(repaired.all(Float::isFinite)) { "Nonfinite camera BW provider grid: $id" }
        return RawNegativeProviderCatalog.Bake(parameters, curves, forward, inverse, raw.grid, repaired, true)
    }

    private fun checkedCurve(input: FloatArray): FloatArray {
        require(input.size == 32769 && input.all { it.isFinite() && it in 0f..32767f && it == it.toInt().toFloat() }) {
            "Expected integer Q15 curve plus guard sample"
        }
        require(input[32768] == input[32767]) { "Invalid Q15 curve guard" }
        return input.copyOf()
    }

    private fun checkedMatrix(input: FloatArray): FloatArray {
        require(input.size == 9 && input.all(Float::isFinite)) { "Expected finite camera BW 3x3 matrix" }
        return input.copyOf()
    }

    private fun identity() = floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f)

    private fun multiply(left: FloatArray, right: FloatArray): FloatArray = FloatArray(9) { i ->
        val row = i / 3 * 3; val col = i % 3
        (left[row] * right[col] + left[row + 1] * right[col + 3]) + left[row + 2] * right[col + 6]
    }.also { require(it.all(Float::isFinite)) { "Camera BW matrix overflow" } }

    private fun inverse(m: FloatArray): FloatArray {
        val a = m[0]; val b = m[1]; val c = m[2]
        val d = m[3]; val e = m[4]; val f = m[5]
        val g = m[6]; val h = m[7]; val i = m[8]
        val aa = e * i - h * f
        val negativeAb = i * b - h * c
        val ac = f * b - e * c
        val determinant = (aa * a - d * negativeAb) + g * ac
        require(determinant.isFinite() && determinant != 0f) { "Singular camera BW fidelity matrix" }
        val scale = 1f / determinant
        require(scale.isFinite()) { "Camera BW inverse overflow" }
        return floatArrayOf(
            aa, -negativeAb, ac,
            -(i * d - g * f), i * a - g * c, -(f * a - d * c),
            h * d - e * g, -(h * a - g * b), e * a - d * b,
        ).also { out ->
            for (n in out.indices) out[n] *= scale
            require(out.all(Float::isFinite)) { "Camera BW inverse overflow" }
        }
    }
}
