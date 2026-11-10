package dev.cxo.filmpack.core

import kotlin.math.abs

/** Engine SetRawRenderingData matrix/EV construction (4b399c).
 *
 * Provider outputs are explicit inputs. A camera's sensor-to-working fidelity
 * matrix is a different value from targetFidelity and must not be substituted.
 * No captured photo, camera endpoint, or film-specific value is stored here.
 */
class RawRenderingParameters private constructor(
    val renderingEv: Float,
    private val pre: FloatArray,
    private val post: FloatArray,
) {
    fun preMatrix(): FloatArray = pre.copyOf()
    fun postMatrix(): FloatArray = post.copyOf()

    companion object {
        fun fromProvider(
            sensorExposureBiasEv: Float,
            providerExposureEv: Float,
            targetFidelity: FloatArray,
            targetM1: FloatArray,
            targetM2: FloatArray,
            feature5Enabled: Boolean = false,
        ): RawRenderingParameters {
            require(sensorExposureBiasEv.isFinite() && providerExposureEv.isFinite())
            val fidelity = checked(targetFidelity)
            val m1 = checked(targetM1)
            val m2 = checked(targetM2)
            val renderingEv = sensorExposureBiasEv - providerExposureEv
            require(renderingEv.isFinite())
            val pre = multiply(m1, inverse(multiply(feature5Matrix(feature5Enabled), fidelity)))
            require(pre.all(Float::isFinite)) { "RAW pre matrix overflow" }
            return RawRenderingParameters(renderingEv, pre, m2)
        }

        internal fun feature5Matrix(enabled: Boolean): FloatArray = if (enabled) floatArrayOf(
            0.72188163f, 0.08861878f, 0.18949962f,
            0.13322704f, 0.83578175f, 0.03099118f,
            0.02482087f, 0.06490827f, 0.91027087f,
        ) else floatArrayOf(
            0.83072674f, 0.12698068f, 0.04236905f,
            0.09652022f, 0.89108580f, 0.01238098f,
            0.02198894f, 0.03270674f, 0.94508755f,
        )

        private fun checked(input: FloatArray): FloatArray {
            require(input.size == 9 && input.all(Float::isFinite)) { "Expected finite 3x3 RAW provider matrix" }
            return input.copyOf()
        }

        private fun multiply(left: FloatArray, right: FloatArray): FloatArray = FloatArray(9) { i ->
            val row = i / 3 * 3; val col = i % 3
            ((left[row] * right[col] + 0f) + left[row + 1] * right[col + 3]) + left[row + 2] * right[col + 6]
        }

        private fun inverse(m: FloatArray): FloatArray {
            val a = m[0]; val b = m[1]; val c = m[2]
            val d = m[3]; val e = m[4]; val f = m[5]
            val g = m[6]; val h = m[7]; val i = m[8]
            val aa = e * i - f * h; val ab = c * h - b * i; val ac = b * f - c * e
            val ba = f * g - d * i; val bb = a * i - c * g; val bc = c * d - a * f
            val ca = d * h - e * g; val cb = b * g - a * h; val cc = a * e - b * d
            // Native 4b399c expands the six determinant terms before the
            // reciprocal. Regrouping into three cofactor products changes
            // Float32 rounding and can move a grid node across clamp validity.
            var determinant = ((-e * c) * g) + ((b * f) * g)
            determinant += (d * c) * h
            determinant -= (a * f) * h
            determinant -= (b * d) * i
            determinant += (a * e) * i
            require(determinant.isFinite() && abs(determinant) >= java.lang.Float.MIN_NORMAL) {
                "Singular RAW provider matrix"
            }
            val scale = 1f / determinant
            require(scale.isFinite()) { "RAW provider inverse overflow" }
            return floatArrayOf(aa, ab, ac, ba, bb, bc, ca, cb, cc).also { out ->
                for (n in out.indices) out[n] *= scale
                require(out.all(Float::isFinite)) { "RAW provider inverse overflow" }
            }
        }
    }
}
