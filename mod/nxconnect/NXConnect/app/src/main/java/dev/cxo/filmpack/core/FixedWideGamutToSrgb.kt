package dev.cxo.filmpack.core

import java.security.MessageDigest

/** Fixed output ICC pair: DxO WideGamut832f32 -> source sRGB046968.
 * Source-built LCMS2.17, perceptual intent, flags0x40. Apply after film/grain,
 * before display/JPEG packing. This is not a general output-profile selector. */
class FixedWideGamutToSrgb private constructor(private val transform: LcmsOptimizedQ15Transform) {
    fun render(source: FloatArray, destination: FloatArray) = transform.renderNormalizedQ15ToRgb16(source, destination)

    companion object {
        const val RESOURCE_SHA256 = "5fd87a9b8e7788aa5036343eef70281121c543ad0c030b3b13178a0c3497b21c"
        fun read(bytes: ByteArray): FixedWideGamutToSrgb {
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            require(digest == RESOURCE_SHA256) { "Unsupported output ICC transform" }
            return FixedWideGamutToSrgb(LcmsOptimizedQ15Transform.read(bytes))
        }
    }
}
