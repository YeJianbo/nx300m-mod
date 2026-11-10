// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.data

import android.graphics.Bitmap
import com.homesoft.photo.libraw.LibRaw
import java.io.File

data class NxRawInfo(
    val make: String,
    val model: String,
    val width: Int,
    val height: Int,
    val iso: Float,
    val shutterSeconds: Float,
    val aperture: Float,
    val focalLength: Float,
    val libRawVersion: String,
)

data class DecodedNxRaw(val bitmap: Bitmap, val info: NxRawInfo, val halfSize: Boolean)

/** LibRaw opens the file and applies its NX300M camera matrix and recorded white balance first. */
object NxRawDecoder {
    fun decode(
        file: File,
        halfSize: Boolean,
        previewMaxDimension: Int = 0,
    ): DecodedNxRaw {
        require(file.isFile) { "RAW 文件已不存在" }
        val raw = LibRaw()
        try {
            val openResult = raw.open(file.absolutePath)
            if (openResult != 0) throw RawDecodeException("打开 RAW", openResult)
            val width = raw.width
            val height = raw.height
            val info = NxRawInfo(
                make = raw.cameraMake.trim(),
                model = raw.cameraModel.trim(),
                width = width,
                height = height,
                iso = raw.isoSpeed,
                shutterSeconds = raw.shutter,
                aperture = raw.aperture,
                focalLength = raw.focalLength,
                libRawVersion = LibRaw.getVersion(),
            )

            raw.setQuality(3)
            raw.setHalfSize(halfSize)
            raw.setCameraWhiteBalance(true)
            raw.setAutoWhiteBalance(false)
            raw.setUseCameraMatrix(LibRaw.USE_CAMERA_MATRIX_DEFAULT)
            raw.setAutoBrightness(false)
            raw.setAutoScale(true)
            raw.setOutputColorSpace(LibRaw.COLORSPACE_SRGB)
            raw.setOutputBps(8)
            raw.setHighlightMode(0)
            raw.setFbddNoiseReduction(0)
            raw.setCaptureScaleMul(false)

            val processResult = raw.dcrawProcess()
            if (processResult != 0) throw RawDecodeException("显影 RAW", processResult)
            var bitmap = raw.getMutableBitmap(Bitmap.Config.ARGB_8888)
                ?: throw IllegalStateException("LibRaw 没有返回图像")
            if (previewMaxDimension > 0) {
                val longest = maxOf(bitmap.width, bitmap.height)
                if (longest > previewMaxDimension) {
                    val scale = previewMaxDimension.toFloat() / longest
                    val scaled = Bitmap.createScaledBitmap(
                        bitmap,
                        (bitmap.width * scale).toInt().coerceAtLeast(1),
                        (bitmap.height * scale).toInt().coerceAtLeast(1),
                        true,
                    )
                    if (scaled !== bitmap) bitmap.recycle()
                    bitmap = scaled
                }
            }
            return DecodedNxRaw(bitmap, info, halfSize)
        } catch (error: RawDecodeException) {
            throw error
        } catch (error: Throwable) {
            throw IllegalStateException("LibRaw 无法解码这个文件：${error.message ?: error.javaClass.simpleName}", error)
        } finally {
            raw.close()
        }
    }
}

class RawDecodeException(stage: String, val resultCode: Int) :
    Exception("${stage}失败（LibRaw $resultCode）${resultHint(resultCode)}")

private fun resultHint(code: Int): String = when (code) {
    -2 -> "：文件或格式不受支持"
    -100007 -> "：内存不足"
    -100008 -> "：输入数据损坏"
    -100009 -> "：无法读取文件"
    -100012 -> "：文件过大"
    else -> ""
}
