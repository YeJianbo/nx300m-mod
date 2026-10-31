// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.media.MediaScannerConnection
import android.net.Uri
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.exifinterface.media.ExifInterface
import androidx.lifecycle.lifecycleScope
import com.nxwa.nxconnect.transfer.MediaStoreWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

/**
 * 镜头源信息 (EXIF) 补录与写入引擎。
 * 针对 NX300M 转接老镜头无触点痛点，无损写入镜头品牌、型号、实际焦距、35mm等效焦距与拍摄光圈。
 * 不对 JPEG 像素进行二次压缩，毫秒级直接修改 EXIF 数据段。
 */
object LensMetadata {

    fun prompt(
        activity: AppCompatActivity,
        count: Int,
        initialMake: String = "",
        initialModel: String = "",
        initialFocal: String = "",
        initialAperture: String = "",
        initialMetadata: String? = null,
        displayName: String? = null,
        onDismiss: (() -> Unit)? = null,
        apply: (String) -> Unit
    ) = PhotoMetadataEditor.show(
        activity, count, initialMake, initialModel, initialFocal, initialAperture,
        initialMetadata, displayName, onDismiss, apply,
    )

    /**
     * 无损修改 EXIF tags 并保存。
     */
    fun applyAttributes(exif: ExifInterface, json: String) {
        val data = JSONObject(json)
        val allowed = setOf(
            ExifInterface.TAG_LENS_MAKE,
            ExifInterface.TAG_LENS_MODEL,
            ExifInterface.TAG_FOCAL_LENGTH,
            ExifInterface.TAG_F_NUMBER,
            ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM,
            ExifInterface.TAG_DATETIME_ORIGINAL,
            ExifInterface.TAG_USER_COMMENT
        )
        for (tag in allowed) {
            if (data.has(tag)) {
                if (data.isNull(tag)) { exif.setAttribute(tag, null); continue }
                val value = data.getString(tag)
                val encoded = if (tag == ExifInterface.TAG_FOCAL_LENGTH) {
                    val scaled = java.math.BigDecimal(value).stripTrailingZeros()
                    val denominator = java.math.BigInteger.TEN.pow(scaled.scale().coerceAtLeast(0))
                    val numerator = scaled.multiply(java.math.BigDecimal(denominator)).toBigIntegerExact()
                    "$numerator/$denominator"
                } else value
                exif.setAttribute(tag, encoded)
            }
        }
    }

    fun write(exif: ExifInterface, json: String) {
        applyAttributes(exif, json)
        exif.saveAttributes()
        val data = JSONObject(json)
        if (data.has(ExifInterface.TAG_FOCAL_LENGTH) && !data.isNull(ExifInterface.TAG_FOCAL_LENGTH)) {
            check(kotlin.math.abs(exif.getAttributeDouble(ExifInterface.TAG_FOCAL_LENGTH, -1.0) - data.getString(ExifInterface.TAG_FOCAL_LENGTH).toDouble()) < .0001) { "实际焦距写入失败" }
        }
    }

    /**
     * 校验写入后的 EXIF tags 是否与传入的 JSON 一致。
     */
    fun verify(exif: ExifInterface, json: String) {
        val data = JSONObject(json)
        data.keys().forEach { tag ->
            if (data.isNull(tag)) {
                check(exif.getAttribute(tag) == null) { "镜头信息未清除：$tag" }
                return@forEach
            }
            val expected = data.getString(tag)
            val numeric = tag in setOf(
                ExifInterface.TAG_FOCAL_LENGTH,
                ExifInterface.TAG_F_NUMBER,
                ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM
            )
            val matches = if (numeric) {
                kotlin.math.abs(exif.getAttributeDouble(tag, -1.0) - expected.toDouble()) < .0001
            } else {
                exif.getAttribute(tag) == expected
            }
            check(matches) { "镜头信息保存后读回不一致：$tag" }
        }
    }

    /**
     * 对本地文件就地无损写入并刷新媒体库。
     */
    fun writeLocalFile(activity: AppCompatActivity, file: File, json: String, onDone: ((Boolean) -> Unit)? = null) {
        activity.lifecycleScope.launch(Dispatchers.IO) {
            val ok = runCatching {
                if (file.extension.equals("srw", true)) {
                    SrwMetadata.write(file, json)
                    SrwMetadata.verify(file, json)
                } else {
                    require(file.extension.lowercase() in setOf("jpg", "jpeg")) { "仅支持 JPEG／SRW" }
                    val exif = ExifInterface(file)
                    write(exif, json)
                    verify(exif, json)
                }
                MediaScannerConnection.scanFile(activity, arrayOf(file.absolutePath), arrayOf(MediaStoreWriter.mimeOf(file.name)), null)
                true
            }.getOrDefault(false)
            withContext(Dispatchers.Main) {
                if (ok) Toast.makeText(activity, "镜头源信息已成功写入！", Toast.LENGTH_SHORT).show()
                else Toast.makeText(activity, "写入镜头信息失败", Toast.LENGTH_SHORT).show()
                onDone?.invoke(ok)
            }
        }
    }

    /**
     * 批量编辑所选 Uri 的副本。
     */
    fun editCopies(activity: AppCompatActivity, uris: List<Uri>, onCompleted: (() -> Unit)? = null) {
        if (uris.isEmpty()) return
        prompt(activity, uris.size) { metadata ->
            val progress = NxDialogBuilder(activity)
                .setTitle("正在写入镜头信息")
                .setMessage("0 / ${uris.size}")
                .setCancelable(false)
                .create()
            progress.show()

            activity.lifecycleScope.launch {
                var ok = 0
                var failed = 0
                var firstError: String? = null
                try {
                    for ((index, uri) in uris.withIndex()) {
                        try {
                            withContext(Dispatchers.IO) {
                                val resolver = activity.contentResolver
                                val name = resolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                                    if (it.moveToFirst()) it.getString(0) else null
                                } ?: "photo.jpg"
                                val extension = name.substringAfterLast('.', "").lowercase()
                                require(extension in setOf("jpg", "jpeg", "srw")) { "仅支持 JPEG／SRW" }
                                val target = MediaStoreWriter.createImageTarget(activity, name.substringBeforeLast('.') + "_lens.$extension")
                                try {
                                    resolver.openInputStream(uri)?.use { it.copyTo(requireNotNull(target.output)) }
                                        ?: error("无法读取照片")
                                    MediaStoreWriter.finish(activity, target, metadata)
                                } catch (e: Exception) {
                                    MediaStoreWriter.abort(activity, target)
                                    throw e
                                }
                            }
                            ok++
                        } catch (e: Exception) {
                            if (e is kotlinx.coroutines.CancellationException) throw e
                            failed++
                            if (firstError == null) firstError = e.message
                        }
                        progress.setMessage("${index + 1} / ${uris.size}")
                    }
                } finally { progress.dismiss() }
                NxDialogBuilder(activity)
                    .setTitle("镜头信息写入完成")
                    .setMessage("成功 $ok 张，失败 $failed 张。\nJPEG：DCIM/NXConnect\nSRW：Download/NXConnect" + (firstError?.let { "\n$it" } ?: ""))
                    .setPositiveButton("完成") { _, _ -> onCompleted?.invoke() }
                    .show()
            }
        }
    }
}
