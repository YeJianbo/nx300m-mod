/*
 * Shared format selection; the existing file list and download path are preserved.
 *
 * SPDX-License-Identifier: AGPL-3.0-or-later
 */
package com.nxwa.nxconnect.ui

import android.view.LayoutInflater
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.card.MaterialCardView
import com.nxwa.nxconnect.R
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.data.DownloadFormat
import com.nxwa.nxconnect.data.MediaEntry
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.databinding.DialogDownloadFormatBinding

object MediaDownloads {
    private fun sizeText(files: List<MediaEntry>): String =
        if (files.all { it.size >= 0 }) MediaEntry.formatSize(files.sumOf { it.size }).orEmpty() else "大小未知"

    fun choose(
        activity: AppCompatActivity,
        groups: List<MediaGroup>,
        onDownload: (List<MediaEntry>, String?) -> Unit
    ) {
        if (groups.isEmpty()) return
        val camera = NxApp.camera ?: return
        val hasJpeg = groups.any { it.jpeg != null }
        val hasRaw = groups.any { it.raw != null }

        fun download(files: List<MediaEntry>) {
            // The chooser can outlive a connection; never apply old paths or edits to a new camera.
            if (NxApp.camera !== camera) {
                Toast.makeText(activity, "相机连接已改变，请重新选择下载", Toast.LENGTH_SHORT).show()
                return
            }
            // Migrate paired edits only for the actual RAWs being exported.
            val paths = files.map { it.relPath }.toSet()
            groups.forEach { group ->
                val raw = group.raw
                if (raw?.ext == "srw" && raw.relPath in paths && PhotoEdits.get(activity, raw) == null &&
                    group.jpeg?.let { PhotoEdits.get(activity, it) } != null) {
                    PhotoEdits.update(activity, group, "{}")
                }
            }
            onDownload(files, null)
        }

        if (hasJpeg && hasRaw) {
            val dialog = NxBottomSheetDialog(activity)
            val binding = DialogDownloadFormatBinding.inflate(LayoutInflater.from(activity))
            dialog.setContentView(binding.root)

            val jpegFiles = groups.flatMap { it.downloadFiles(DownloadFormat.JPEG) }.distinctBy { it.relPath }
            val rawFiles = groups.flatMap { it.downloadFiles(DownloadFormat.RAW) }.distinctBy { it.relPath }
            val bothFiles = groups.flatMap { it.downloadFiles(DownloadFormat.BOTH) }.distinctBy { it.relPath }

            binding.txtJpegSize.text = "${jpegFiles.size} 个文件 · ${sizeText(jpegFiles)}"
            binding.txtRawSize.text = "${rawFiles.size} 个文件 · ${sizeText(rawFiles)}"
            binding.txtBothSize.text = "${bothFiles.size} 个文件 · ${sizeText(bothFiles)}"

            var selectedFormat = DownloadFormat.JPEG

            fun updateSelection(format: DownloadFormat) {
                selectedFormat = format
                binding.rbJpeg.isChecked = (format == DownloadFormat.JPEG)
                binding.rbRaw.isChecked = (format == DownloadFormat.RAW)
                binding.rbBoth.isChecked = (format == DownloadFormat.BOTH)

                fun highlightCard(card: MaterialCardView, selected: Boolean) {
                    val background = ContextCompat.getColor(
                        activity,
                        if (selected) R.color.nx_surface_variant else R.color.nx_surface
                    )
                    card.setCardBackgroundColor(background)
                    card.strokeWidth = 0
                }

                highlightCard(binding.cardJpeg, format == DownloadFormat.JPEG)
                highlightCard(binding.cardRaw, format == DownloadFormat.RAW)
                highlightCard(binding.cardBoth, format == DownloadFormat.BOTH)
            }

            updateSelection(DownloadFormat.JPEG)

            binding.cardJpeg.setOnClickListener { updateSelection(DownloadFormat.JPEG) }
            binding.cardRaw.setOnClickListener { updateSelection(DownloadFormat.RAW) }
            binding.cardBoth.setOnClickListener { updateSelection(DownloadFormat.BOTH) }

            NxMotion.touchBounce(binding.btnConfirmDownload)
            binding.btnCancelDownload.setOnClickListener { dialog.dismiss() }

            binding.btnConfirmDownload.setOnClickListener {
                dialog.dismiss()
                val targetFiles = when (selectedFormat) {
                    DownloadFormat.JPEG -> jpegFiles
                    DownloadFormat.RAW -> rawFiles
                    DownloadFormat.BOTH -> bothFiles
                }
                download(targetFiles)
            }

            dialog.show()
        } else {
            val allFiles = groups.flatMap { it.files }.distinctBy { it.relPath }
            download(allFiles)
        }
    }
}
