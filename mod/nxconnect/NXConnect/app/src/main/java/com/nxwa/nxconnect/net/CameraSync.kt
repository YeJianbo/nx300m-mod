// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.data.MediaEntry
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/** Explicit JPEG writeback. The daemon retains the original; readback verifies every byte. */
object CameraSync {
    sealed class SyncResult {
        object Success : SyncResult()
        class Failed(val reason: String) : SyncResult()
    }

    suspend fun syncToCamera(
        entry: MediaEntry,
        localFile: File,
        camera: NxCamera? = NxApp.camera,
        onProgress: (String) -> Unit = {},
    ): SyncResult = withContext(Dispatchers.IO) {
        try {
            check(camera != null && NxApp.camera === camera) { "相机连接已改变，请重新同步" }
            require(entry.ext in setOf("jpg", "jpeg")) { "只支持 JPEG，RAW 文件不会修改" }
            onProgress("正在保留原始备份并上传 JPEG…")
            camera.uploadMedia(entry, localFile) { sent, total ->
                if (total > 0) onProgress("正在上传 JPEG：${sent * 100 / total}%")
            }
            onProgress("正在从相机读回并核对文件…")
            localFile.inputStream().buffered().use { expected ->
                camera.downloadMedia(entry, { _, _ -> }) { bytes, count ->
                    val buffer = ByteArray(count)
                    var filled = 0
                    while (filled < count) {
                        val n = expected.read(buffer, filled, count - filled)
                        check(n > 0) { "相机读回文件比上传文件更长，原始备份仍保留" }
                        filled += n
                    }
                    check((0 until count).all { buffer[it] == bytes[it] }) {
                        "相机读回内容与上传文件不一致，原始备份仍保留"
                    }
                }
                check(expected.read() == -1) { "相机读回文件不完整，原始备份仍保留" }
            }
            check(camera.galleryEditStatus(entry).backupAvailable) { "相机未确认原始备份，请检查文件状态" }
            NxApp.log("JPEG 信息已同步并完整读回：${entry.relPath}")
            SyncResult.Success
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            SyncResult.Failed(e.message ?: "同步失败，请检查相机文件状态")
        }
    }
}
