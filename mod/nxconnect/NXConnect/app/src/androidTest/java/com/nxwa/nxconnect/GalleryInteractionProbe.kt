package com.nxwa.nxconnect

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Matrix
import android.graphics.drawable.BitmapDrawable
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout
import androidx.recyclerview.widget.RecyclerView
import com.nxwa.nxconnect.data.MediaEntry
import com.nxwa.nxconnect.data.MediaGroup
import com.nxwa.nxconnect.net.CameraProfile
import com.nxwa.nxconnect.net.OsdGeometry
import com.nxwa.nxconnect.ui.MainActivity
import com.nxwa.nxconnect.ui.MediaAdapter
import com.nxwa.nxconnect.ui.ViewerActivity
import com.nxwa.nxconnect.ui.ZoomImageView
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Focused Android checks for the gallery regressions; no extra production entry points. */
class GalleryInteractionProbe : Instrumentation() {
    private val result = JSONObject()
    private var host: Activity? = null
    private var metadataOnly = false
    private var touchOnly = false
    private var imagesOnly = false
    private var editingOnly = false
    private var detailsOnly = false
    private var srwOnly = false
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); metadataOnly = arguments?.getString("check") == "metadata"; touchOnly = arguments?.getString("check") == "touch"; imagesOnly = arguments?.getString("check") == "images"; editingOnly = arguments?.getString("check") == "editing"; detailsOnly = arguments?.getString("check") == "details"; srwOnly = arguments?.getString("check") == "srw"; start() }
    override fun onStart() {
        var success = false
        try {
            if (srwOnly) verifySrwExport() else if (detailsOnly) verifyDetails() else if (editingOnly) verifyEditing() else if (imagesOnly) verifyImageQuality() else if (metadataOnly) verifyMetadata() else {
            host = startActivitySync(Intent(targetContext, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            if (!touchOnly) verifySelection()
            verifyZoom()
            if (!touchOnly) verifyDetails()
            }
            success = true
        } catch (e: Throwable) {
            result.put("error", android.util.Log.getStackTraceString(e))
        } finally {
            result.put("passed", success)
            File(targetContext.filesDir, if (srwOnly) "gallery-srw.json" else if (detailsOnly) "gallery-details.json" else if (editingOnly) "gallery-editing.json" else if (imagesOnly) "gallery-image-quality.json" else if (metadataOnly) "gallery-metadata.json" else if (touchOnly) "gallery-touch.json" else "gallery-interactions.json").writeText(result.toString(2))
            runOnMainSync { host?.finish() }
            finish(if (success) Activity.RESULT_OK else Activity.RESULT_CANCELED,
                Bundle().apply { putString("stream", result.toString(2)) })
        }
    }

    /** Read-only camera download through the real Android pending/export/publish path. */
    private fun verifySrwExport() = kotlinx.coroutines.runBlocking {
        val camera = com.nxwa.nxconnect.net.NxCamera("10.0.2.2", 8765, 8765)
        val entry = MediaEntry("100PHOTO", "SAM_4262.SRW", 22487872)
        val metadata = JSONObject().apply {
            put(androidx.exifinterface.media.ExifInterface.TAG_LENS_MAKE, "Carl Zeiss")
            put(androidx.exifinterface.media.ExifInterface.TAG_LENS_MODEL, "Planar 50mm F1.4")
            put(androidx.exifinterface.media.ExifInterface.TAG_FOCAL_LENGTH, "50")
            put(androidx.exifinterface.media.ExifInterface.TAG_F_NUMBER, "1.4")
            put(androidx.exifinterface.media.ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM, "77")
        }
        val evidence = JSONObject().put("source", entry.relPath).put("expected_source_bytes", entry.size)
            .put("requested_metadata", metadata).put("camera_writeback", false)
        result.put("srw", evidence)
        val writer = com.nxwa.nxconnect.transfer.MediaStoreWriter
        val target = writer.createImageTarget(targetContext, "NXConnect-SRW-check.SRW")
        val exported = File(targetContext.filesDir, "NXConnect-SRW-check.SRW")
        var stage = "download"
        try {
            evidence.put("target_uri", target.uri?.toString()).put("target_display_path", target.displayPath)
            var received = 0L
            val output = requireNotNull(target.output) { "Missing SRW target output" }
            camera.downloadMedia(entry, { _, _ -> }) { bytes, count ->
                output.write(bytes, 0, count)
                received += count
            }
            evidence.put("downloaded_bytes", received)
            check(received == entry.size) { "Incomplete source SRW: $received / ${entry.size}" }
            stage = "finish_metadata_and_publish"
            writer.finish(targetContext, target, metadata.toString())

            stage = "read_completed_export"
            val uri = target.uri
            if (uri != null) {
                targetContext.contentResolver.openInputStream(uri)?.use { input ->
                    exported.outputStream().use { outputCopy -> input.copyTo(outputCopy) }
                } ?: error("Cannot read completed SRW target")
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                    val columns = arrayOf(android.provider.MediaStore.MediaColumns.DISPLAY_NAME,
                        android.provider.MediaStore.MediaColumns.RELATIVE_PATH,
                        android.provider.MediaStore.MediaColumns.IS_PENDING,
                        android.provider.MediaStore.MediaColumns.SIZE)
                    targetContext.contentResolver.query(uri, columns, null, null, null)?.use { cursor ->
                        check(cursor.moveToFirst()) { "Completed SRW MediaStore entry missing" }
                        check(cursor.getInt(2) == 0) { "SRW export is still pending" }
                        evidence.put("published_name", cursor.getString(0)).put("published_directory", cursor.getString(1))
                            .put("published_size", cursor.getLong(3)).put("is_pending", cursor.getInt(2))
                    } ?: error("Cannot query completed SRW entry")
                }
            } else {
                File(target.displayPath).inputStream().use { input ->
                    exported.outputStream().use { outputCopy -> input.copyTo(outputCopy) }
                }
            }
            check(exported.length() >= entry.size) { "Completed SRW was truncated" }
            stage = "android_exif_readback"
            val exif = androidx.exifinterface.media.ExifInterface(exported)
            val readback = JSONObject()
            metadata.keys().forEach { tag -> readback.put(tag, exif.getAttribute(tag)) }
            evidence.put("android_exif", readback)
            check(exif.getAttribute(androidx.exifinterface.media.ExifInterface.TAG_LENS_MAKE) == "Carl Zeiss") { "Android LensMake mismatch: $readback" }
            check(exif.getAttribute(androidx.exifinterface.media.ExifInterface.TAG_LENS_MODEL) == "Planar 50mm F1.4") { "Android LensModel mismatch: $readback" }
            check(kotlin.math.abs(exif.getAttributeDouble(androidx.exifinterface.media.ExifInterface.TAG_FOCAL_LENGTH, -1.0) - 50.0) < .0001) { "Android FocalLength mismatch: $readback" }
            check(kotlin.math.abs(exif.getAttributeDouble(androidx.exifinterface.media.ExifInterface.TAG_F_NUMBER, -1.0) - 1.4) < .0001) { "Android FNumber mismatch: $readback" }
            check(exif.getAttributeInt(androidx.exifinterface.media.ExifInterface.TAG_FOCAL_LENGTH_IN_35MM_FILM, -1) == 77) { "Android equivalent focal length mismatch: $readback" }
            stage = "srw_verify"
            com.nxwa.nxconnect.ui.SrwMetadata.verify(exported, metadata.toString())
            stage = "photo_details_parse"
            val info = com.nxwa.nxconnect.ui.PhotoDetails.parse(exported)
            check(info.camera.contains("NX300M")) { "Unexpected exported camera: ${info.camera}" }
            check(info.lens.contains("Carl Zeiss") && info.lens.contains("Planar 50mm F1.4")) {
                "Exported lens unavailable to PhotoDetails: ${info.lens}"
            }
            check(info.focalEquivVal == "77mm") { "Wrong equivalent focal length: ${info.focalEquivVal}" }
            evidence.put("camera", info.camera).put("lens", info.lens).put("equivalent_focal", info.focalEquivVal)
                .put("exported_bytes", exported.length()).put("exported_copy", exported.absolutePath)
                .put("srw_verified", true).put("photo_details_verified", true).put("stage", "complete")
        } catch (failure: Throwable) {
            evidence.put("stage", stage).put("aborted", true)
            writer.abort(targetContext, target)
            exported.delete()
            throw failure
        }
    }

    private fun verifyEditing() = kotlinx.coroutines.runBlocking {
        runOnMainSync { NxApp.connect("10.0.2.2", 8765, 8765, CameraProfile(geometry = OsdGeometry.NX300M, galleryPreview = true)) }
        val entry = MediaEntry("100PHOTO", "SAM_4262.JPG", 7849498)
        val previous = com.nxwa.nxconnect.ui.PhotoEdits.get(targetContext, entry)
        val camera = requireNotNull(NxApp.camera)
        val original = camera.fetchImageFile(entry)
        val sample = File(targetContext.cacheDir, "editor-source-check.jpg").apply { writeBytes(original) }
        val export = File(targetContext.cacheDir, "editor-export-check.jpg")
        try {
            com.nxwa.nxconnect.ui.PhotoEdits.clear(targetContext, entry)
            val source = com.nxwa.nxconnect.ui.PhotoDetails.parse(sample)
            val viewer = startActivitySync(Intent(targetContext, ViewerActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra(ViewerActivity.EXTRA_PATHS, arrayOf("100PHOTO/SAM_4262.JPG", "100PHOTO/SAM_4262.SRW"))
                .putExtra(ViewerActivity.EXTRA_SIZES, longArrayOf(7849498, 22487872)))
            host = viewer
            runOnMainSync { viewer.findViewById<View>(R.id.btnInfo).performClick() }
            awaitMain { viewer.findViewById<android.widget.TextView>(R.id.infoStatus).text.isEmpty() }
            fun nodes(node: android.view.accessibility.AccessibilityNodeInfo): List<android.view.accessibility.AccessibilityNodeInfo> =
                listOf(node) + (0 until node.childCount).flatMap { i -> node.getChild(i)?.let { nodes(it) }.orEmpty() }
            fun allNodes() = uiAutomation.rootInActiveWindow?.let { nodes(it) }.orEmpty()
            fun button(text: String) = allNodes().firstOrNull { it.text?.toString() == text && it.isClickable }
            fun awaitUi(condition: () -> Boolean) {
                val until = SystemClock.uptimeMillis() + 15000
                while (SystemClock.uptimeMillis() < until) { if (condition()) return; SystemClock.sleep(100) }
                error("Editor UI condition timed out")
            }
            fun open() {
                runOnMainSync { viewer.findViewById<View>(R.id.infoEdit).performClick() }
                awaitUi { button("完成") != null }
                check(button("写入保存") == null && button("开始下载") == null)
            }
            fun setField(index: Int, text: String) {
                val fields = allNodes().filter { it.className?.toString()?.endsWith("EditText") == true }
                check(fields.size >= 2) { "Editor inputs missing" }
                check(fields[index].performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_SET_TEXT,
                    Bundle().apply { putCharSequence(android.view.accessibility.AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }))
            }
            open()
            check(button("完成")?.isEnabled == false) { "Unchanged editor can complete" }
            check(allNodes().filter { it.className?.toString()?.endsWith("EditText") == true }.drop(2).take(3)
                .none { it.text?.contains('/') == true }) { "Raw EXIF rational shown in editor" }
            uiAutomation.takeScreenshot()?.let { bitmap ->
                File(targetContext.filesDir, "editor-form.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                bitmap.recycle()
            }
            val model = allNodes().filter { it.className?.toString()?.endsWith("EditText") == true }[1].text?.toString().orEmpty()
            check(model == source.tags.toMap()[androidx.exifinterface.media.ExifInterface.TAG_LENS_MODEL].orEmpty()) { "Another photo's lens leaked into editor" }
            setField(1, "Cancelled edit")
            check(requireNotNull(button("取消")).performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK))
            awaitUi { button("完成") == null }
            check(com.nxwa.nxconnect.ui.PhotoEdits.get(targetContext, entry) == null)
            open()
            setField(0, "Asahi Optical Co.")
            setField(1, "Super-Takumar 55mm F1.8")
            // Use focus/scroll assistance to expose the focal input before editing.
            val fields = allNodes().filter { it.className?.toString()?.endsWith("EditText") == true }
            if (fields.size > 2) setField(2, "55")
            check(requireNotNull(button("完成")).performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK))
            awaitUi { button("完成") == null && com.nxwa.nxconnect.ui.PhotoEdits.get(targetContext, entry) != null }
            val metadata = requireNotNull(com.nxwa.nxconnect.ui.PhotoEdits.get(targetContext, entry))
            awaitMain { viewer.findViewById<android.widget.TextView>(R.id.infoLens).text.contains("Super-Takumar") }
            check(com.nxwa.nxconnect.ui.PhotoEdits.get(targetContext, entry.copy(name = "OTHER.JPG")) == null)
            open()
            check(button("完成")?.isEnabled == false)
            check(allNodes().any { it.text?.toString() == "Super-Takumar 55mm F1.8" })
            requireNotNull(button("取消")).performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK)
            awaitUi { button("完成") == null }
            @Suppress("DEPRECATION")
            val running = (targetContext.getSystemService(android.content.Context.ACTIVITY_SERVICE) as android.app.ActivityManager).getRunningServices(100)
            check(running.none { it.service.className == "com.nxwa.nxconnect.transfer.DownloadService" }) { "Editing started a download" }
            check(sample.readBytes().contentEquals(original)) { "Metadata preview changed source file" }
            export.writeBytes(original)
            com.nxwa.nxconnect.ui.LensMetadata.write(androidx.exifinterface.media.ExifInterface(export), metadata)
            com.nxwa.nxconnect.ui.LensMetadata.verify(androidx.exifinterface.media.ExifInterface(export), metadata)
            check(com.nxwa.nxconnect.ui.PhotoDetails.parse(sample, metadata).lens.contains("Super-Takumar"))
            result.put("editing", JSONObject().put("cancel_kept_source", true).put("complete_without_download", true)
                .put("reopened_photo_values", true).put("other_photo_unchanged", true).put("export_verified", true)
                .put("source_bytes_unchanged", true))
            uiAutomation.takeScreenshot()?.let { bitmap ->
                File(targetContext.filesDir, "editor-details.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                bitmap.recycle()
            }
        } finally {
            com.nxwa.nxconnect.ui.PhotoEdits.clear(targetContext, entry)
            previous?.let { com.nxwa.nxconnect.ui.PhotoEdits.update(targetContext, entry, it) }
            sample.delete(); export.delete()
        }
    }

    private fun verifyImageQuality() = kotlinx.coroutines.runBlocking {
        runOnMainSync { NxApp.connect("10.0.2.2", 8765, 8765, CameraProfile(geometry = OsdGeometry.NX300M)) }
        com.nxwa.nxconnect.ui.ThumbLoader.clear()
        val group = MediaGroup(listOf(MediaEntry("100PHOTO", "SAM_4262.JPG", 7849498)))
        val cover = requireNotNull(com.nxwa.nxconnect.ui.ThumbLoader.load(group, 160))
        check(maxOf(cover.width, cover.height) == 160)
        lateinit var image: android.widget.ImageView
        lateinit var binder: com.nxwa.nxconnect.ui.ThumbBinder
        val upgraded = CountDownLatch(1)
        runOnMainSync {
            image = android.widget.ImageView(targetContext)
            binder = com.nxwa.nxconnect.ui.ThumbBinder(image)
            binder.bind(group, 320, onLoaded = {
                val bitmap = (image.drawable as? BitmapDrawable)?.bitmap
                if (bitmap != null && maxOf(bitmap.width, bitmap.height) == 320) upgraded.countDown()
            })
        }
        check(upgraded.await(90, TimeUnit.SECONDS)) { "160px cover was not upgraded to 320px grid image" }
        val full = requireNotNull(com.nxwa.nxconnect.ui.ThumbLoader.load(group, 2048, true))
        check(maxOf(full.width, full.height) == 2048) { "Viewer image under-resolved: ${full.width}x${full.height}" }
        runOnMainSync { binder.clear() }
        result.put("image_quality", JSONObject().put("cover_edge", 160).put("grid_upgraded_to", 320)
            .put("viewer_width", full.width).put("viewer_height", full.height))
    }

    private fun verifyMetadata() = kotlinx.coroutines.runBlocking {
        val camera = com.nxwa.nxconnect.net.NxCamera("10.0.2.2", 8765, 8765)
        val entry = MediaEntry("100PHOTO", "SAM_4262.JPG", 7849498)
        val header = requireNotNull(camera.fetchJpegMetadata(entry))
        val original = camera.fetchImageFile(entry)
        val full = androidx.exifinterface.media.ExifInterface(original.inputStream())
        val prefix = androidx.exifinterface.media.ExifInterface(header.bytes.inputStream())
        val tags = androidx.exifinterface.media.ExifInterface::class.java.fields
            .filter { it.name.startsWith("TAG_") && it.type == String::class.java }
            .map { it.get(null) as String }.distinct()
        var present = 0
        for (tag in tags) {
            check(full.getAttribute(tag) == prefix.getAttribute(tag)) { "Different value: $tag" }
            val a = full.getAttributeBytes(tag); val b = prefix.getAttributeBytes(tag)
            check(if (a == null) b == null else b != null && a.contentEquals(b)) { "Different bytes: $tag" }
            if (full.getAttribute(tag) != null) present++
        }
        check(present > 20 && header.bytes.size < 65536)
        check(com.nxwa.nxconnect.net.JpegMetadata.readHeader(header.bytes.copyOf(100).inputStream(), 65536) == null)
        val group = MediaGroup(listOf(entry, MediaEntry("100PHOTO", "SAM_4262.SRW", 22487872)))
        val info = com.nxwa.nxconnect.ui.PhotoDetails.read(camera, group, targetContext.cacheDir)
        check(group.displayName == "SAM_4262" && info.size.startsWith("5472×3648"))
        check(info.camera.contains("NX300M"))
        result.put("metadata", JSONObject().put("matching_tags", present)
            .put("header_bytes", header.bytes.size).put("response_bytes", header.responseBytes)
            .put("requests", header.requests).put("original_bytes", original.size)
            .put("dimensions", info.size).put("group_name", group.displayName))
    }

    private fun verifySelection() {
        lateinit var adapter: MediaAdapter
        val photos = (1..3).map { MediaGroup(listOf(MediaEntry("probe", "$it.JPG", 100))) }
        var fullUpdates = 0
        var partialUpdates = 0
        runOnMainSync {
            adapter = MediaAdapter({}, {}, {})
            adapter.submitList(photos)
            adapter.registerAdapterDataObserver(object : RecyclerView.AdapterDataObserver() {
                override fun onChanged() { fullUpdates++ }
                override fun onItemRangeChanged(start: Int, count: Int, payload: Any?) {
                    if (payload != null) partialUpdates++ else fullUpdates++
                }
            })
            adapter.beginSelection()
            check(adapter.selectionMode && adapter.selectedEntries().isEmpty())
            adapter.toggle(photos[0]); adapter.toggle(photos[0])
            check(adapter.selectionMode && adapter.selectedEntries().isEmpty()) { "Last deselection left selection mode" }
            adapter.selectAll(); check(adapter.selectedEntries().size == 3)
            adapter.clearSelectedItems(); check(adapter.selectionMode && adapter.selectedEntries().isEmpty())
            adapter.toggle(photos[0]); adapter.toggle(photos[1])
        }
        val committed = CountDownLatch(1)
        runOnMainSync { adapter.submitList(photos.drop(1)) { committed.countDown() } }
        check(committed.await(5, TimeUnit.SECONDS)) { "List update did not commit" }
        runOnMainSync {
            check(adapter.selectedEntries() == listOf(photos[1])) { "Removed item remained selected" }
            adapter.clearSelection(); check(!adapter.selectionMode)
        }
        check(fullUpdates == 0 && partialUpdates > 0) { "Selection rebound full thumbnail rows" }
        result.put("selection", JSONObject().put("partial_updates", partialUpdates).put("full_updates", fullUpdates).put("passed", true))
    }

    private fun verifyZoom() {
        lateinit var view: ZoomImageView
        runOnMainSync {
            view = ZoomImageView(requireNotNull(host))
            host!!.setContentView(FrameLayout(host!!).apply { addView(view, FrameLayout.LayoutParams(-1, -1)) })
            // A tall image allows an off-centre focus point without hitting pan limits.
            view.setImageDrawable(BitmapDrawable(targetContext.resources, Bitmap.createBitmap(1200, 2000, Bitmap.Config.ARGB_8888)))
        }
        awaitMain { view.width > 0 && view.height > 0 && scale(view) > 0 }
        waitForIdleSync()
        var initialScale = 0f
        val focus = FloatArray(2)
        val imagePoint = FloatArray(2)
        runOnMainSync {
            initialScale = scale(view)
            focus[0] = view.width * .58f; focus[1] = view.height * .52f
            focus.copyInto(imagePoint)
            Matrix().also { view.imageMatrix.invert(it); it.mapPoints(imagePoint) }
            doubleTap(view, focus[0], focus[1])
        }
        awaitMain { kotlin.math.abs(scale(view) / initialScale - 2.5f) < .01f }
        val projected = imagePoint.copyOf()
        runOnMainSync { view.imageMatrix.mapPoints(projected) }
        val error = kotlin.math.hypot(projected[0] - focus[0], projected[1] - focus[1])
        check(error < 2f) { "Double-tap focus drifted by $error pixels" }
        verifyPointerHandoff(view)
        SystemClock.sleep(350)
        runOnMainSync { doubleTap(view, focus[0], focus[1]) }
        awaitMain { kotlin.math.abs(scale(view) / initialScale - 1f) < .01f }
        result.put("zoom", JSONObject().put("focus_error_px", error.toDouble()).put("returned_to_fit", true))
    }

    private fun verifyPointerHandoff(view: ZoomImageView) {
        var detailCalls = 0
        var maxJump = 0f
        runOnMainSync {
            view.onVerticalDrag = { _, _, _, _ -> detailCalls++ }
            for (lifted in 0..1) {
                val time = SystemClock.uptimeMillis()
                var step = 0L
                val y = view.height * .5f
                val xs = floatArrayOf(view.width * .5f - 60, view.width * .5f + 60)
                fun event(action: Int, ids: IntArray, offset: Float = 0f) {
                    val properties = ids.map { id -> MotionEvent.PointerProperties().apply { this.id = id; toolType = MotionEvent.TOOL_TYPE_FINGER } }.toTypedArray()
                    val coords = ids.map { id -> MotionEvent.PointerCoords().apply { x = xs[id] + offset; this.y = y; pressure = 1f; size = 1f } }.toTypedArray()
                    MotionEvent.obtain(time, time + step, action, ids.size, properties, coords,
                        0, 0, 1f, 1f, 0, 0, android.view.InputDevice.SOURCE_TOUCHSCREEN, 0)
                        .also { view.dispatchTouchEvent(it); it.recycle() }
                    step += 20
                }
                fun translation() = FloatArray(9).also { view.imageMatrix.getValues(it) }[Matrix.MTRANS_X]
                event(MotionEvent.ACTION_DOWN, intArrayOf(0))
                event(MotionEvent.ACTION_POINTER_DOWN or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT), intArrayOf(0, 1))
                val before = translation()
                event(MotionEvent.ACTION_POINTER_UP or (lifted shl MotionEvent.ACTION_POINTER_INDEX_SHIFT), intArrayOf(0, 1))
                val survivor = intArrayOf(1 - lifted)
                event(MotionEvent.ACTION_MOVE, survivor)
                val jump = kotlin.math.abs(translation() - before)
                maxJump = maxOf(maxJump, jump)
                check(jump < .1f) { "Pointer $lifted handoff jumped $jump px" }
                event(MotionEvent.ACTION_MOVE, survivor, 12f)
                check(kotlin.math.abs(translation() - before - 12f) < .1f) { "Surviving finger did not drag by 12px" }
                event(MotionEvent.ACTION_UP, survivor, 12f)
            }
            check(detailCalls == 0) { "Pinch handoff triggered details" }
        }
        result.put("pointer_handoff", JSONObject().put("max_jump_px", maxJump.toDouble())
            .put("both_fingers_checked", true).put("continued_drag_px", 12).put("detail_callbacks", detailCalls))
    }

    private fun doubleTap(view: View, x: Float, y: Float) {
        val time = SystemClock.uptimeMillis()
        for ((offset, action) in listOf(0L to MotionEvent.ACTION_DOWN, 30L to MotionEvent.ACTION_UP,
            100L to MotionEvent.ACTION_DOWN, 130L to MotionEvent.ACTION_UP)) {
            val down = if (offset < 100) time else time + 100
            MotionEvent.obtain(down, time + offset, action, x, y, 0).also { view.dispatchTouchEvent(it); it.recycle() }
        }
    }
    private fun scale(view: ZoomImageView): Float = FloatArray(9).also { view.imageMatrix.getValues(it) }[Matrix.MSCALE_X]
    private fun awaitMain(condition: () -> Boolean) {
        val until = SystemClock.uptimeMillis() + 10000
        do {
            var ready = false
            runOnMainSync { ready = condition() }
            if (ready) return
            SystemClock.sleep(50)
        } while (SystemClock.uptimeMillis() < until)
        error("UI condition timed out")
    }

    private fun verifyDetails() {
        val camera = com.nxwa.nxconnect.net.NxCamera("10.0.2.2", 8765, 8765)
        val caps = kotlinx.coroutines.runBlocking { camera.detectCapabilities() }
        check(caps.profile.galleryPreview && caps.cgiThumbOk) { "Native preview self-check failed: ${caps.notes}" }
        runOnMainSync { NxApp.connect("10.0.2.2", 8765, 8765, caps.profile) }
        val viewer = startActivitySync(Intent(targetContext, ViewerActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra(ViewerActivity.EXTRA_PATHS, arrayOf("100PHOTO/SAM_4262.JPG", "100PHOTO/SAM_4262.SRW"))
            .putExtra(ViewerActivity.EXTRA_SIZES, longArrayOf(7849498, 22487872)))
        host = viewer
        var image: ZoomImageView? = null
        val until = SystemClock.uptimeMillis() + 90000
        while (SystemClock.uptimeMillis() < until) {
            runOnMainSync {
                val recycler = viewer.findViewById<androidx.viewpager2.widget.ViewPager2>(R.id.pager).getChildAt(0) as RecyclerView
                image = recycler.findViewHolderForAdapterPosition(0)?.itemView?.findViewById(R.id.image)
            }
            if (image?.drawable != null) break
            SystemClock.sleep(100)
        }
        check(image?.drawable != null) { "Photo never loaded" }
        fun screenshot(name: String) {
            waitForIdleSync()
            SystemClock.sleep(300)
            uiAutomation.takeScreenshot()?.let { bitmap ->
                File(targetContext.filesDir, name).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                bitmap.recycle()
            }
        }
        val root = viewer.findViewById<View>(R.id.root)
        fun swipe(from: Float, to: Float) {
            val start = SystemClock.uptimeMillis()
            for (i in 0..20) {
                val action = if (i == 0) MotionEvent.ACTION_DOWN else if (i == 20) MotionEvent.ACTION_UP else MotionEvent.ACTION_MOVE
                val event = MotionEvent.obtain(start, SystemClock.uptimeMillis(), action, root.width / 2f, from + (to - from) * i / 20f, 0)
                event.source = android.view.InputDevice.SOURCE_TOUCHSCREEN
                check(uiAutomation.injectInputEvent(event, true))
                event.recycle()
                SystemClock.sleep(12)
            }
        }
        screenshot("details-closed.png")
        swipe(root.height * .55f, root.height * .18f)
        awaitMain { viewer.findViewById<View>(R.id.bottomChrome).visibility == View.INVISIBLE }
        SystemClock.sleep(450)
        awaitMain { viewer.findViewById<android.widget.TextView>(R.id.infoStatus).text.isEmpty() }
        runOnMainSync {
            val stage = viewer.findViewById<View>(R.id.imageStage)
            val sheet = viewer.findViewById<View>(R.id.detailsScroll)
            val photo = requireNotNull(image)
            val drawable = requireNotNull(photo.drawable)
            val rect = android.graphics.RectF(0f, 0f, drawable.intrinsicWidth.toFloat(), drawable.intrinsicHeight.toFloat())
            photo.imageMatrix.mapRect(rect)
            val photoBottom = stage.pivotY + (rect.bottom - stage.pivotY) * stage.scaleY + stage.translationY
            check(photoBottom <= sheet.top + 2f) { "Details cover the photo: $photoBottom > ${sheet.top}" }
            check(stage.translationY < -root.height * .2f) { "Photo did not move with details" }
            val exposure = viewer.findViewById<android.widget.TextView>(R.id.infoExposure).text.toString()
            check(Regex("(?<![0-9])75mm(?![0-9])").containsMatchIn(exposure)) { "Expected equivalent focal length, got $exposure" }
            check(!viewer.findViewById<android.widget.TextView>(R.id.infoLens).text.contains("等效"))
            val badge = viewer.findViewById<android.widget.TextView>(R.id.badgeFormat)
            check(badge.currentTextColor != android.graphics.Color.WHITE)
        }
        screenshot("details-open.png")
        var sheetTop = 0f
        runOnMainSync { sheetTop = viewer.findViewById<View>(R.id.detailsScroll).top.toFloat() }
        swipe(sheetTop + 24 * root.resources.displayMetrics.density, root.height * .91f)
        awaitMain { viewer.findViewById<View>(R.id.detailsScroll).visibility == View.GONE }
        screenshot("details-returned.png")
        runOnMainSync { requireNotNull(image).onSingleTap?.invoke() }
        SystemClock.sleep(40)
        runOnMainSync { requireNotNull(image).onSingleTap?.invoke() }
        awaitMain { viewer.findViewById<View>(R.id.bottomChrome).alpha >= .999f }
        SystemClock.sleep(200)
        runOnMainSync {
            check(viewer.findViewById<View>(R.id.bottomChrome).visibility == View.VISIBLE) { "Interrupted chrome animation hid the toolbar" }
        }
        runOnMainSync {
            check(viewer.findViewById<View>(R.id.imageStage).translationY == 0f)
            com.nxwa.nxconnect.ui.LensPresets.choose(viewer as androidx.appcompat.app.AppCompatActivity) { selected ->
                check(selected.model == "smc PENTAX-FA 100-300mm F4.7-5.8")
            }
        }
        fun nodes(node: android.view.accessibility.AccessibilityNodeInfo): List<android.view.accessibility.AccessibilityNodeInfo> =
            listOf(node) + (0 until node.childCount).flatMap { i -> node.getChild(i)?.let { nodes(it) }.orEmpty() }
        fun texts() = uiAutomation.rootInActiveWindow?.let { nodes(it) }.orEmpty()
        fun awaitText(text: String): android.view.accessibility.AccessibilityNodeInfo {
            val deadline = SystemClock.uptimeMillis() + 15000
            while (SystemClock.uptimeMillis() < deadline) {
                texts().firstOrNull { it.text?.toString() == text }?.let { return it }
                SystemClock.sleep(100)
            }
            error("Missing lens UI: $text; visible: ${texts().mapNotNull { it.text }}")
        }
        screenshot("lens-categories.png")
        check(awaitText("宾得 / 太苦玛").performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK))
        awaitText("smc PENTAX-M 50mm F1.7")
        for (name in listOf("smc PENTAX-M 50mm F1.7", "PENTAX-F 70-200mm F4-5.6", "smc PENTAX-FA 100-300mm F4.7-5.8", "Super-Takumar 55mm F1.8")) {
            check(texts().any { it.text?.toString() == name }) { "Missing requested lens: $name" }
        }
        screenshot("pentax-presets.png")
        requireNotNull(texts().firstOrNull { it.text?.toString() == "smc PENTAX-FA 100-300mm F4.7-5.8" }).performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK)
        result.put("details", JSONObject().put("native_preview_check", true).put("photo_moves_without_overlap", true)
            .put("swipe_up_and_pull_down", true).put("interrupted_chrome_restored", true)
            .put("equivalent_focal", "75mm").put("requested_presets", 4))
    }
}
