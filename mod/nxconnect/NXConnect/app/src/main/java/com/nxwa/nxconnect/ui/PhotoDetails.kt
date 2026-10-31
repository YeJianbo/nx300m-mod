package com.nxwa.nxconnect.ui

import androidx.exifinterface.media.ExifInterface
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.data.MediaEntry
import com.nxwa.nxconnect.net.NxCamera
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.math.round

data class SourcePhotoInfo(
    val date: String,
    val camera: String,
    val size: String,
    val exposure: String,
    val lens: String,
    val tags: List<Pair<String, String>>,
    // Separate values let the details layout wrap metadata without splitting its units.
    val apertureVal: String = "F--",
    val shutterVal: String = "--s",
    val focalVal: String = "--mm",
    val focalEquivVal: String = "",
    val isoVal: String = "ISO --",
    val evVal: String = "0.0 EV",
    val resolution: String = "",
    val megaPixels: String = "",
    val isManualLens: Boolean = false
)

object PhotoDetails {
    suspend fun read(camera: NxCamera, group: MediaGroup, cache: File, edits: String? = null): SourcePhotoInfo = withContext(Dispatchers.IO) {
        val source = group.jpeg ?: group.primary
        val temp = File.createTempFile("source-exif-", ".${source.name.substringAfterLast('.')}", cache)
        try {
            val header = if (source.ext in setOf("jpg", "jpeg")) camera.fetchJpegMetadata(source) else null
            if (header != null) temp.writeBytes(header.bytes)
            else temp.outputStream().use { output ->
                camera.downloadMedia(source, { _, _ -> }, { bytes, count -> output.write(bytes, 0, count) })
            }
            parse(temp, edits)
        } finally {
            temp.delete()
        }
    }

    fun parse(file: File, edits: String? = null): SourcePhotoInfo {
        val exif = ExifInterface(file)
        edits?.let { LensMetadata.applyAttributes(exif, it) }
        fun attr(tag: String) = exif.getAttribute(tag)?.takeIf { it.isNotBlank() }

        val rawDate = attr(ExifInterface.TAG_DATETIME_ORIGINAL) ?: attr(ExifInterface.TAG_DATETIME)
        val date = rawDate?.let {
            runCatching {
                val parsed = SimpleDateFormat("yyyy:MM:dd HH:mm:ss", Locale.US).parse(it)
                SimpleDateFormat("yyyy年M月d日EEEE · HH:mm", Locale.SIMPLIFIED_CHINESE).format(requireNotNull(parsed))
            }.getOrDefault(it)
        } ?: "拍摄时间未记录"

        val make = attr(ExifInterface.TAG_MAKE)?.trim().orEmpty()
        val modelName = attr(ExifInterface.TAG_MODEL)?.trim().orEmpty()
        val camera = when {
            make.isNotEmpty() && modelName.isNotEmpty() -> {
                if (modelName.contains(make, ignoreCase = true)) modelName else "$make $modelName"
            }
            modelName.isNotEmpty() -> modelName
            make.isNotEmpty() -> make
            else -> "相机型号未记录"
        }

        var w = exif.getAttributeInt(ExifInterface.TAG_PIXEL_X_DIMENSION, 0)
        var h = exif.getAttributeInt(ExifInterface.TAG_PIXEL_Y_DIMENSION, 0)
        if (w <= 0) w = exif.getAttributeInt(ExifInterface.TAG_IMAGE_WIDTH, 0)
        if (h <= 0) h = exif.getAttributeInt(ExifInterface.TAG_IMAGE_LENGTH, 0)
        if (w == 0 || h == 0) {
            val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
            android.graphics.BitmapFactory.decodeFile(file.path, bounds)
            w = bounds.outWidth
            h = bounds.outHeight
        }

        val mpVal = if (w > 0 && h > 0) "${"%.1f".format(Locale.US, w.toDouble() * h / 1000000)} MP" else ""
        val resolution = if (w > 0 && h > 0) "$w × $h" else ""
        val size = if (resolution.isNotEmpty()) "$resolution  ·  $mpVal" else ""

        // 光圈
        var apertureVal = "F--"
        var fNumber = exif.getAttributeDouble(ExifInterface.TAG_F_NUMBER, 0.0)
        if (fNumber <= 0.0) {
            attr(ExifInterface.TAG_F_NUMBER)?.toDoubleOrNull()?.let { fNumber = it }
        }
        if (fNumber > 0.0) {
            apertureVal = if (fNumber >= 10 || (fNumber * 10) % 10.0 == 0.0) {
                "F${"%.1f".format(Locale.US, fNumber).removeSuffix(".0")}"
            } else {
                "F${"%.1f".format(Locale.US, fNumber)}"
            }
        }

        // 快门
        var shutterVal = "--s"
        val shutter = exif.getAttributeDouble(ExifInterface.TAG_EXPOSURE_TIME, 0.0)
        if (shutter > 0.0) {
            shutterVal = if (shutter < 1.0) {
                val denom = round(1.0 / shutter).toInt()
                "1/$denom s"
            } else if (shutter % 1.0 == 0.0) {
                "${shutter.toInt()} s"
            } else {
                "${"%.1f".format(Locale.US, shutter)} s"
            }
        }

        // Only these NX bodies have a known crop factor; other cameras require the EXIF equivalent tag.
        val focal = exif.getAttributeDouble(ExifInterface.TAG_FOCAL_LENGTH, 0.0)
        val equivExif = exif.getAttributeInt(ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM, 0)
        val normalizedModel = modelName.uppercase(Locale.US).replace(Regex("[^A-Z0-9]"), "")
        val isNx300 = normalizedModel in setOf("NX300", "NX300M", "SAMSUNGNX300", "SAMSUNGNX300M")
        val equiv35 = if (equivExif > 0) equivExif else if (focal > 0 && isNx300) kotlin.math.floor(focal * 1.538 + 0.5).toInt() else 0
        // A 35 mm equivalent alone does not establish the physical focal length.
        val focalVal = if (focal > 0) "${"%.1f".format(Locale.US, focal).removeSuffix(".0")} mm" else "--mm"
        val focalEquivVal = if (equiv35 > 0) {
            "${equiv35}mm"
        } else ""

        // ISO
        var isoVal = "ISO --"
        val iso = attr(ExifInterface.TAG_PHOTOGRAPHIC_SENSITIVITY) ?: attr("ISOSpeedRatings")
        if (!iso.isNullOrBlank()) {
            isoVal = "ISO $iso"
        }

        // 曝光补偿
        var evVal = "0.0 EV"
        val ev = exif.getAttributeDouble(ExifInterface.TAG_EXPOSURE_BIAS_VALUE, 0.0)
        evVal = when {
            ev > 0.0 -> "+${"%.1f".format(Locale.US, ev)} EV"
            ev < 0.0 -> "${"%.1f".format(Locale.US, ev)} EV"
            else -> "0.0 EV"
        }

        // 镜头型号清洗：严格去除末尾冗余括号（例如 "(50mm F1.4)"、"()"、"(Manual)" 等）
        val lensMake = attr(ExifInterface.TAG_LENS_MAKE)?.trim().orEmpty()
        val lensModel = attr(ExifInterface.TAG_LENS_MODEL)?.trim().orEmpty()
        val rawLensText = when {
            lensMake.isNotEmpty() && lensModel.isNotEmpty() -> {
                if (lensModel.contains(lensMake, ignoreCase = true)) lensModel else "$lensMake $lensModel"
            }
            lensModel.isNotEmpty() -> lensModel
            lensMake.isNotEmpty() -> lensMake
            else -> ""
        }
        val cleanedLensText = rawLensText.replace(Regex("\\s*\\([^)]*\\)$"), "").trim()

        val isManual = cleanedLensText.isEmpty() || fNumber <= 0.0 || focal <= 0.0
        val finalLens = when {
            cleanedLensText.isNotEmpty() -> cleanedLensText
            isManual -> "镜头信息未记录"
            else -> "相机未记录镜头型号"
        }
        val lensFull = finalLens

        val exposureSummary = listOf(
            apertureVal.takeIf { it != "F--" },
            shutterVal.takeIf { it != "--s" },
            focalVal.takeIf { it != "--mm" },
            isoVal.takeIf { it != "ISO --" },
            evVal
        ).filterNotNull().joinToString("  |  ")

        val tags = ExifInterface::class.java.fields.asSequence()
            .filter { it.name.startsWith("TAG_") && it.type == String::class.java }
            .mapNotNull { runCatching { it.get(null) as String }.getOrNull() }
            .distinct()
            .mapNotNull { tag ->
                attr(tag)?.let { value ->
                    tag to if (tag == ExifInterface.TAG_MAKER_NOTE) {
                        "${exif.getAttributeBytes(tag)?.size ?: value.length} 字节厂商数据"
                    } else value
                }
            }
            .sortedBy { it.first }
            .toList()

        return SourcePhotoInfo(
            date = date,
            camera = camera,
            size = size,
            exposure = exposureSummary,
            lens = lensFull,
            tags = tags,
            apertureVal = apertureVal,
            shutterVal = shutterVal,
            focalVal = focalVal,
            focalEquivVal = focalEquivVal,
            isoVal = isoVal,
            evVal = evVal,
            resolution = resolution,
            megaPixels = mpVal,
            isManualLens = isManual
        )
    }
}
