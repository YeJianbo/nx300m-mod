package dev.cxo.filmpack.app

import android.content.Context
import android.net.Uri
import android.content.res.AssetManager
import dev.cxo.filmpack.core.NativeGrain8Resources
import java.io.Closeable
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.CancellationException

/** Worker-owned, bounded and atomic import of the supported external pack.
 * A failed/cancelled replacement never changes the installed pack or loader.
 * Only the original verified v1 bytes are admitted, including every source.
 * The caller must use this store from its worker; opening the bundled 130 MB
 * asset is deliberately synchronous so it cannot accidentally run on UI. */
class GrainPackStore(context: Context) : Closeable {
    private val context = context.applicationContext
    private val directory = File(this.context.filesDir,"grain").apply { mkdirs() }
    private val installed = File(directory,"native-grain8-v1.cxograinpack")
    private var resources: NativeGrain8Resources? = null
    private var loader: PhotoGrainLoader? = null

    fun openInstalled(): List<String> {
        ensureInstalled()
        return openResources().catalog.definitions().map { it.identifier }
    }

    fun importPack(uri: Uri, progress: (Int) -> Unit, cancelled: () -> Boolean): List<String> {
        val temporary = File.createTempFile("import-", ".part",directory)
        var candidate: NativeGrain8Resources? = null
        try {
            fun checkCancellation() {
                if (cancelled() || Thread.currentThread().isInterrupted) throw CancellationException("已取消资源包导入")
            }
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L; var last = -1
            checkCancellation()
            requireNotNull(context.contentResolver.openInputStream(uri)) { "无法读取资源包" }.use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(64*1024)
                    while (true) {
                        checkCancellation()
                        val count = input.read(buffer)
                        if (count == -1) break
                        total += count
                        require(total <= PACK_BYTES) { "不是支持的颗粒资源包或文件过大" }
                        digest.update(buffer,0,count); output.write(buffer,0,count)
                        val percent = (total*99/PACK_BYTES).toInt()
                        if (percent != last) { last=percent; progress(percent) }
                    }
                    output.fd.sync()
                }
            }
            require(total == PACK_BYTES && digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) } == PACK_SHA256) {
                "资源包不完整或版本不支持，请重新选择原始颗粒资源包"
            }
            checkCancellation()
            candidate = NativeGrain8Resources.open(temporary)
            // Build the LUT before committing so a preparation/allocation failure
            // cannot replace a working installation. Same-directory rename is atomic.
            val newLoader = PhotoGrainLoader.fromResources(candidate)
            val identifiers = candidate.catalog.definitions().map { it.identifier }
            checkCancellation()
            // This rename is the commit point. Cancellation is checked before
            // it; after it the successful installation is retained.
            check(temporary.renameTo(installed)) { "无法安装资源包，请检查存储空间" }
            loader?.clear(); resources?.close()
            resources=candidate; loader=newLoader; candidate=null
            progress(100)
            return identifiers
        } finally {
            candidate?.close(); temporary.delete()
        }
    }

    fun prepare(request: PhotoGrainRequest): PhotoGrainLayer {
        if (loader == null) {
            ensureInstalled()
            openResources()
        }
        return requireNotNull(loader).prepare(request)
    }

    private fun openResources(): NativeGrain8Resources {
        resources?.let { return it }
        require(installed.isFile) { "缺少内置颗粒资源包" }
        val opened = NativeGrain8Resources.open(installed)
        try {
            loader=PhotoGrainLoader.fromResources(opened); resources=opened
            return opened
        } catch (failure: Throwable) { opened.close(); throw failure }
    }

    /** Reuse a verified installation, otherwise restore the canonical bundled
     * asset. Never remove the previous file before the verified atomic commit. */
    private fun ensureInstalled() {
        if (resources != null && loader != null) return
        if (installed.isFile) {
            val verified = try {
                requireVerified(installed)
                true
            } catch (_: IllegalArgumentException) {
                false // Wrong length/hash: recover from the shipped canonical bytes.
            } catch (_: IOException) {
                false // An unreadable installation may also be replaced atomically.
            }
            if (verified) return
        }
        val temporary = File.createTempFile("bundled-", ".part", directory)
        var candidate: NativeGrain8Resources? = null
        try {
            copyAndVerify(context.assets, BUNDLED_ASSET, temporary)
            candidate = NativeGrain8Resources.open(temporary)
            val newLoader = PhotoGrainLoader.fromResources(candidate)
            require(temporary.renameTo(installed)) { "无法安装内置颗粒资源包，请检查存储空间" }
            loader?.clear(); resources?.close()
            resources = candidate; loader = newLoader; candidate = null
        } finally {
            candidate?.close()
            temporary.delete()
        }
    }

    private fun requireVerified(file: File) {
        require(file.length() == PACK_BYTES) { "已安装颗粒资源包大小不正确" }
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count == -1) break
                digest.update(buffer, 0, count)
            }
        }
        require(digest.hexDigest() == PACK_SHA256) { "已安装颗粒资源包校验失败" }
    }

    private fun copyAndVerify(assets: AssetManager, name: String, destination: File) {
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        assets.open(name, AssetManager.ACCESS_STREAMING).use { input ->
            FileOutputStream(destination).use { output ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    val count = input.read(buffer)
                    if (count == -1) break
                    total += count
                    require(total <= PACK_BYTES) { "内置颗粒资源包大小不正确" }
                    digest.update(buffer, 0, count)
                    output.write(buffer, 0, count)
                }
                output.fd.sync()
            }
        }
        require(total == PACK_BYTES && digest.hexDigest() == PACK_SHA256) {
            "内置颗粒资源包校验失败"
        }
    }

    override fun close() {
        loader?.clear(); loader=null; resources?.close(); resources=null
    }

    companion object {
        const val BUNDLED_ASSET = "native-grain8-v1.cxograinpack"
        const val PACK_BYTES = 129_743_465L
        const val PACK_SHA256 = "4772ae8a5ba44192595b8f9b87c5fc8e7ad949cb14882046517cff0d0d14e21e"
    }
}

private fun MessageDigest.hexDigest(): String = digest().joinToString("") { "%02x".format(it.toInt() and 255) }
