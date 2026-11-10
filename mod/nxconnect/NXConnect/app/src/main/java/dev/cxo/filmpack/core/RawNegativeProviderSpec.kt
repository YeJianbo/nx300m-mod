package dev.cxo.filmpack.core

/**
 * Static RAW provider data shared across source photos and camera white balances.
 *
 * exposureOffsetEv is extracted from the RAW provider's exposure addition. It
 * can differ from the similarly named JPEG ColorMode initializer adjustment.
 * The caller supplies the source camera's exposure bias; no camera bias or
 * captured photo parameters are embedded here.
 */
class RawNegativeProviderSpec(
    val targetProfile: RawTargetFidelityProfile,
    val exposureOffsetEv: Float,
) {
    init {
        require(exposureOffsetEv.isFinite()) { "RAW provider exposure offset must be finite" }
    }

    fun providerExposureEv(sourceCameraExposureBiasEv: Float): Float {
        require(sourceCameraExposureBiasEv.isFinite()) { "Source camera exposure bias must be finite" }
        return (sourceCameraExposureBiasEv + exposureOffsetEv).also {
            require(it.isFinite()) { "RAW provider exposure overflow" }
        }
    }

    fun targetFidelity(temperature: Float): FloatArray = targetProfile.matrixAt(temperature)
}
