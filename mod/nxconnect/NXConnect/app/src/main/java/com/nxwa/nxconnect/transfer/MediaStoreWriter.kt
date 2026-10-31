/*
 * NXConnectForNX500 — 三星 NX500 相机的照片传输与遥控 App
 * Copyright (C) 2026 Katsiy and contributors
 *
 * 本程序是自由软件：你可以依据 GNU Affero 通用公共许可证第 3 版
 * （或任何更新版本）的条款重新分发和/或修改它。
 * 本程序按"有用"的目的分发，但不提供任何担保。
 * 详见随附的 LICENSE 文件，或 <https://www.gnu.org/licenses/>。
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
package com.nxwa.nxconnect.transfer

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import java.io.File
import java.io.OutputStream

/**
 * 把下载到的照片写进系统相册。
 *
 * 分两条路：
 *  - Android 10+：走 MediaStore（无需存储权限），落进 DCIM/NXConnect/
 *  - Android 9 及以下：直接写公共 DCIM 目录（需要 WRITE_EXTERNAL_STORAGE）
 */
object MediaStoreWriter {

    private const val ALBUM = "NXConnect"

    class Target(val uri: Uri?, val output: OutputStream?, val displayPath: String)

    fun createImageTarget(ctx: Context, displayName: String): Target {
        val mime = mimeOf(displayName)
        val kind = collectionFor(displayName)

        // ★ RAW 存到**系统公共下载目录**（Download/NXConnect），不进系统相册。
        // 两点原因：
        //  1. MediaStore 的 Images 集合会校验 MIME，`image/x-samsung-srw` 它不认，
        //     insert 直接返回 null → 用户实测"RAW 下载直接弹出失败"；
        //  2. RAW 本来就不该出现在相册里，用户是要拷到电脑做后期的，
        //     放 Download 目录用文件管理器/数据线都好找。
        // 之前放在 App 专属目录（Android/data/...）确实很麻烦，已改掉。
        if (kind == Collection.RAW) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val relativePath = "${Environment.DIRECTORY_DOWNLOADS}/$ALBUM"
                val values = ContentValues().apply {
                    put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
                    // 用通用二进制类型，避免 MediaStore 因不认识的 MIME 拒绝
                    put(MediaStore.MediaColumns.MIME_TYPE, "application/octet-stream")
                    put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
                    put(MediaStore.MediaColumns.IS_PENDING, 1)
                }
                val uri = ctx.contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                if (uri != null) {
                    val os = ctx.contentResolver.openOutputStream(uri)
                    if (os != null) {
                        return Target(uri, os, "$relativePath/$displayName")
                    }
                    ctx.contentResolver.delete(uri, null, null)
                }
                // MediaStore 不接受时退回直接写文件（老路，至少能下载成功）
                NxLog("RAW 走 MediaStore.Downloads 失败，退回直接写文件")
            }
            val dir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                ALBUM,
            )
            if (!dir.exists() && !dir.mkdirs()) {
                throw IllegalStateException("无法创建目录 ${dir.absolutePath}")
            }
            val f = uniqueFile(dir, displayName)
            return Target(null, f.outputStream(), f.absolutePath)
        }

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val relativePath = "${Environment.DIRECTORY_DCIM}/$ALBUM"
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, displayName)
                put(MediaStore.MediaColumns.MIME_TYPE, mime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val collection = when (kind) {
                Collection.VIDEO -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                else -> MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            }
            val uri = ctx.contentResolver.insert(collection, values)
                ?: throw IllegalStateException(
                    "系统相册拒绝创建条目（MIME=$mime）。请把失败信息反馈给开发者",
                )
            val os = ctx.contentResolver.openOutputStream(uri)
                ?: throw IllegalStateException("无法打开系统相册输出流")
            Target(uri, os, relativePath + "/" + displayName)
        } else {
            val dir = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM),
                ALBUM,
            )
            if (!dir.exists() && !dir.mkdirs()) throw IllegalStateException("无法创建目录 ${dir.absolutePath}")
            val f = uniqueFile(dir, displayName)
            Target(null, f.outputStream(), f.absolutePath)
        }
    }

    /** RAW 的存放目录（用于提示用户去哪里找文件） */
    fun rawDir(ctx: Context): String =
        File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            ALBUM,
        ).absolutePath

    private fun NxLog(msg: String) = com.nxwa.nxconnect.NxApp.log(msg)

    /** 下载结束后调用；Q+ 需要清掉 IS_PENDING 才会出现在相册里 */
    fun finish(ctx: Context, target: Target, lensMetadata: String? = null) {
        target.output?.flush()
        target.output?.close()
        if (lensMetadata != null && (target.displayPath.endsWith(".jpg", true) || target.displayPath.endsWith(".jpeg", true))) {
            if (target.uri != null) {
                ctx.contentResolver.openFileDescriptor(target.uri, "rw")?.use {
                    com.nxwa.nxconnect.ui.LensMetadata.write(androidx.exifinterface.media.ExifInterface(it.fileDescriptor), lensMetadata)
                } ?: error("无法写入镜头信息")
                ctx.contentResolver.openFileDescriptor(target.uri, "r")?.use {
                    com.nxwa.nxconnect.ui.LensMetadata.verify(androidx.exifinterface.media.ExifInterface(it.fileDescriptor), lensMetadata)
                } ?: error("无法读回镜头信息")
            } else {
                com.nxwa.nxconnect.ui.LensMetadata.write(androidx.exifinterface.media.ExifInterface(target.displayPath), lensMetadata)
                com.nxwa.nxconnect.ui.LensMetadata.verify(androidx.exifinterface.media.ExifInterface(target.displayPath), lensMetadata)
            }
        } else if (lensMetadata != null && target.displayPath.endsWith(".srw", true)) {
            writeSrwMetadata(ctx, target, lensMetadata)
        }
        val uri = target.uri ?: run {
            android.media.MediaScannerConnection.scanFile(ctx, arrayOf(target.displayPath), null, null)
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val values = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
            check(ctx.contentResolver.update(uri, values, null, null) > 0) { "无法发布下载文件" }
        }
    }

    private fun writeSrwMetadata(ctx: Context, target: Target, metadata: String) {
        val uri = target.uri
        if (uri == null) {
            val file = File(target.displayPath)
            com.nxwa.nxconnect.ui.SrwMetadata.write(file, metadata)
            com.nxwa.nxconnect.ui.SrwMetadata.verify(file, metadata)
            return
        }

        // SRW needs random access. Stage and verify the complete file before replacing
        // the pending MediaStore entry; finish publishes only after this succeeds.
        val staged = File.createTempFile("nx-srw-metadata-", ".srw", ctx.cacheDir)
        try {
            ctx.contentResolver.openInputStream(uri)?.use { source ->
                staged.outputStream().use { output -> source.copyTo(output) }
            } ?: error("无法读取 RAW 下载文件")
            com.nxwa.nxconnect.ui.SrwMetadata.write(staged, metadata)
            com.nxwa.nxconnect.ui.SrwMetadata.verify(staged, metadata)

            val descriptor = ctx.contentResolver.openFileDescriptor(uri, "rw")
                ?: error("无法写入 RAW 镜头信息")
            ParcelFileDescriptor.AutoCloseOutputStream(descriptor).use { output ->
                output.channel.truncate(0)
                output.channel.position(0)
                staged.inputStream().use { source ->
                    check(source.copyTo(output) == staged.length()) { "RAW 镜头信息写入不完整" }
                }
                output.flush()
                output.fd.sync()
            }
        } finally {
            staged.delete()
        }
    }

    fun abort(ctx: Context, target: Target) {
        try {
            target.output?.close()
        } catch (e: Exception) {
            // 忽略
        }
        val uri = target.uri ?: run { File(target.displayPath).delete(); return }
        try {
            ctx.contentResolver.delete(uri, null, null)
        } catch (e: Exception) {
            // 忽略
        }
    }

    private fun uniqueFile(dir: File, name: String): File {
        var f = File(dir, name)
        if (!f.exists()) return f
        val base = name.substringBeforeLast('.', name)
        val ext = name.substringAfterLast('.', "")
        var i = 1
        while (f.exists() && i < 10_000) {
            val candidate = if (ext.isEmpty()) "$base($i)" else "$base($i).$ext"
            f = File(dir, candidate)
            i++
        }
        return f
    }

    fun mimeOf(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "jpg", "jpeg" -> "image/jpeg"
        "png" -> "image/png"
        "gif" -> "image/gif"
        "bmp" -> "image/bmp"
        "tif", "tiff" -> "image/tiff"
        // RAW：相机端出的是三星 .SRW（单张 19–34MB），也有可能是通用 DNG
        "srw" -> "image/x-samsung-srw"
        "dng" -> "image/x-adobe-dng"
        "cr2" -> "image/x-canon-cr2"
        "cr3" -> "image/x-canon-cr3"
        "nef" -> "image/x-nikon-nef"
        "arw", "sr2" -> "image/x-sony-arw"
        "orf" -> "image/x-olympus-orf"
        "rw2" -> "image/x-panasonic-rw2"
        "raf" -> "image/x-fuji-raf"
        "pef" -> "image/x-pentax-pef"
        "nrw" -> "image/x-nikon-nrw"
        "mp4", "m4v" -> "video/mp4"
        "mov" -> "video/quicktime"
        "avi" -> "video/x-msvideo"
        "mkv" -> "video/x-matroska"
        "3gp" -> "video/3gpp"
        else -> "application/octet-stream"
    }

    /**
     * RAW 该进哪个 MediaStore 集合。
     *
     * 相机出的 .SRW 在 Android 上没有原生解码器，而且单张 19–34MB —— 放进图片集
     * 会让系统相册尝试解码而卡顿，所以归到 Downloads，用户用电脑后期处理更自然。
     * 通用 DNG 则当图片（Android 10+ 认识它）。
     */
    fun collectionFor(name: String): Collection {
        val ext = name.substringAfterLast('.', "").lowercase()
        return when {
            ext in setOf("mp4", "m4v", "mov", "avi", "mkv", "3gp") -> Collection.VIDEO
            ext == "dng" -> Collection.IMAGE
            ext in setOf(
                "srw", "cr2", "cr3", "nef", "arw", "sr2", "orf", "rw2", "raf", "pef", "nrw",
            ) -> Collection.RAW
            else -> Collection.IMAGE
        }
    }

    enum class Collection { IMAGE, VIDEO, RAW }
}
