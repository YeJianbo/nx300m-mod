// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MiniJson
import java.io.InputStream
import java.util.zip.CRC32
import java.util.zip.GZIPInputStream

/** Reads containers without extracting executable files or sending them to a camera. */
object InstallPackageInspector {
    data class Result(val title: String, val detail: String)
    private val modules = setOf("lut_port.so", "ui_mod.so", "exposure_controls.so", "shutter_limit.so",
        "color-style.so", "lcd-capture.so", "service-loader.so", "gallery-share.so", "camera_firmware_mod.so")
    private fun exact(input: InputStream, size: Int): ByteArray = ByteArray(size).also { bytes ->
        var done = 0
        while (done < size) {
            val n = input.read(bytes, done, size - done)
            if (n < 0) throw NxException("文件被截断")
            if (n > 0) done += n
        }
    }
    private fun text(bytes: ByteArray, start: Int, length: Int) =
        bytes.copyOfRange(start, start + length).takeWhile { it != 0.toByte() }.toByteArray().toString(Charsets.UTF_8)
    private fun u32(bytes: ByteArray, offset: Int): Long = (0..3).fold(0L) { v, i ->
        v or ((bytes[offset + i].toLong() and 255L) shl (8 * i))
    }
    private fun discard(input: InputStream, count: Long) {
        var left = count
        val buffer = ByteArray(65536)
        while (left > 0) {
            val n = input.read(buffer, 0, minOf(left, buffer.size.toLong()).toInt())
            if (n < 0) throw NxException("文件被截断")
            if (n > 0) left -= n
        }
    }
    fun mod(input: InputStream): Result = GZIPInputStream(input).use { tar ->
        val entries = linkedMapOf<String, Long>()
        var manifest: Map<String, Any?>? = null
        var launchProfile: String? = null
        var total = 0L
        var members = 0
        while (true) {
            val header = exact(tar, 512)
            if (header.all { it == 0.toByte() }) break
            if (++members > 100) throw NxException("安装包条目过多")
            val check = text(header, 148, 8).trim().toLongOrNull(8) ?: throw NxException("TAR 校验字段无效")
            val actual = header.indices.sumOf { if (it in 148..155) 32L else header[it].toLong() and 255L }
            if (check != actual) throw NxException("TAR 头校验失败")
            val name = text(header, 0, 100)
            val prefix = text(header, 345, 155)
            val path = if (prefix.isEmpty()) name else "$prefix/$name"
            val parts = path.split('/')
            if (path.startsWith('/') || parts.any { it == ".." || it == "." } ||
                parts.firstOrNull() != "nx300m-mod-install-candidate" || path.contains('\\')) throw NxException("安装包路径无效")
            val relative = parts.drop(1).joinToString("/")
            val size = text(header, 124, 12).trim().toLongOrNull(8) ?: throw NxException("TAR 文件大小无效")
            if (size < 0 || size > 32L * 1024 * 1024 || total + size > 64L * 1024 * 1024 || entries.size >= 100)
                throw NxException("安装包超出支持范围")
            if (header[156] != 0.toByte() && header[156] != '0'.code.toByte() && header[156] != '5'.code.toByte())
                throw NxException("安装包包含链接或不支持的条目")
            if (header[156] == '5'.code.toByte()) { if (size != 0L) throw NxException("目录条目无效") }
            else {
                if (entries.put(relative, size) != null) throw NxException("安装包条目重复")
                if (relative == "manifest.json") {
                    if (size > 262144) throw NxException("安装清单过大")
                    manifest = MiniJson.parseObject(exact(tar, size.toInt()).toString(Charsets.UTF_8))
                } else if (relative == "rootfs/usr/lib/nx300m-mod/launch-profile") {
                    if (size > 64) throw NxException("启动配置过长")
                    launchProfile = exact(tar,size.toInt()).toString(Charsets.UTF_8).substringBefore('\n')
                } else discard(tar, size)
            }
            discard(tar, (512 - size % 512) % 512)
            total += 512 + size + (512 - size % 512) % 512
        }
        // Consume gzip's end so its existing CRC check also detects truncation.
        var trailing = 0
        while (true) {
            val value = tar.read()
            if (value < 0) break
            if (value != 0 || ++trailing > 16384) throw NxException("安装包尾部无效")
        }
        val data = manifest ?: throw NxException("未找到统一安装清单，请选择完整安装候选 tar.gz")
        if (MiniJson.int(data, "hook_count") != 9) throw NxException("安装候选不是九模块版本")
        val hooks = MiniJson.arr(data["hooks"])?.map { MiniJson.obj(it) ?: throw NxException("模块清单无效") }.orEmpty()
        if (hooks.map { MiniJson.str(it, "destination")?.substringAfterLast('/') }.toSet() != modules)
            throw NxException("模块清单不匹配 NX300M")
        val required = modules.map { "rootfs/usr/lib/nx300m-mod/$it" } + listOf(
            "original/usr/bin/hib2.sh", "original/opt/share/applications/com.samsung.di-camera-app-nx300.desktop",
            "sd/nx300m-mod-sd.tar.gz")
        if (required.any { it !in entries }) throw NxException("候选缺少模块、原厂恢复文件或配套 SD 包")
        val listed = MiniJson.arr(data["files"])?.associate { value ->
            val item = MiniJson.obj(value) ?: throw NxException("文件清单无效")
            (MiniJson.str(item, "path") ?: throw NxException("清单路径无效")) to (item["bytes"] as? Number)?.toLong()
        } ?: throw NxException("候选没有文件清单")
        if (listed.keys != entries.keys - "manifest.json" || listed.any { (name, size) -> entries[name] != size })
            throw NxException("候选文件与清单不一致")
        val policy = MiniJson.obj(data["launch_policy"])
        val configured = policy?.let { MiniJson.str(it,"default_profile") }
        if (policy != null && (configured !in setOf("stock","services","controls","color","full") || launchProfile != configured))
            throw NxException("默认启动配置与清单不一致")
        val launchDetail = if (policy == null) "未提供默认加载分组" else "默认分组：${ModManagement.profileLabel(configured)}"
        Result("NX300M 九模块开发安装候选", "${entries.size} 个文件 · 含原厂恢复文件和完整 SD 包\n$launchDetail\n已在手机读取并检查容器与清单。尚未传入相机，也未写入固件。")
    }
    fun firmware(input: InputStream): Result {
        val header = exact(input, 144)
        if (text(header, 0, 4) != "SLP" || text(header, 12, 16) != "NX300M" || header[44] != 1.toByte())
            throw NxException("请选择 NX300M 的完整 SLP 固件容器")
        val markers = listOf(0xffffffffL, 0x7fffffffL, 0x65ffffffL, 0x543fffffL, 0x4321ffffL, 0x32100fffL)
        var end = 144L
        fun partition(row: ByteArray, start: Int, marker: Long) {
            val size = u32(row, start)
            val expected = u32(row, start + 4)
            if (size <= 0 || size > 512L * 1024 * 1024 || u32(row, start + 8) != end || u32(row, start + 12) != marker)
                throw NxException("固件分区布局无效")
            val crc = CRC32()
            var left = size
            val buffer = ByteArray(65536)
            while (left > 0) {
                val n = input.read(buffer, 0, minOf(left, buffer.size.toLong()).toInt())
                if (n < 0) throw NxException("固件分区被截断")
                if (n > 0) { crc.update(buffer, 0, n); left -= n }
            }
            if ((crc.value xor 0xffffffffL) != expected) throw NxException("固件分区 CRC 校验失败")
            end += size
        }
        for (i in 0..4) partition(header, 64 + 16 * i, markers[i])
        if (u32(header, 45) != end) throw NxException("固件尾部分区位置无效")
        val tail = exact(input, 16)
        end += 16
        partition(tail, 0, markers[5])
        if (input.read() != -1) throw NxException("固件尾部存在多余数据")
        return Result("NX300M 固件容器 ${text(header, 4, 8)}", "分区布局与原有 CRC 校验通过 · $end 字节\n这些检查不证明来源。请确认它是三星未修改的原厂包，再按相机升级说明操作。")
    }
}
