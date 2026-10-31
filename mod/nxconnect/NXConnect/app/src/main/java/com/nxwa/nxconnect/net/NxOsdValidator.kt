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
package com.nxwa.nxconnect.net

import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * OSD 响应的**接入层严格校验**（纯 JVM，可被单元测试覆盖）。
 *
 * ## 为什么需要它
 *
 * [NxOsd.parse] 是协议解析器，但它**不校验块坐标**，而且用 `Map<块索引, Block>` 存结果 ——
 * 同索引的记录会互相覆盖。这带来两个风险：
 *
 * 1. 非法坐标（越界 / 未对齐）在解析阶段不会被发现，会一路传到渲染层
 * 2. 一条非法记录可能被**后面同索引的合法记录掩盖**，导致校验形同虚设
 *
 * 所以这里在**调用解析器之前**逐条检查原始记录，再解析、最后复核解析结果的索引与长度。
 *
 * ## 固定帧头的另一层意义
 *
 * 帧头必须精确等于连接协商的 `720×480` 或 `800×480`，块为 `80×40`。这不只是契约检查 —— 它同时阻止了
 * "尺寸由响应头决定"所带来的乘法溢出：后续所有定长假设都建立在
 * [RECORD_BYTES] = `8 + 80*40*4` = **12808** 之上。
 *
 * ## 实测校验（真机 NX500 固件 1.12）
 *
 * ```
 * 全量：108 × 12808 + 12 = 1,383,276   ← 与真机实测一致
 * 增量：  4 × 12808 + 12 =    51,244   ← 与真机实测一致
 * ```
 *
 * 其中 `+12` = 8 字节帧头 + 4 字节结束标记 `FFFF FFFF`。
 *
 * ## 与解析器的职责分工
 *
 * - 本对象：**准入**（能进就保证坐标、对齐、索引、长度全部合法）
 * - [NxOsd.parse]：**解码**（按已验证的结构读出像素）
 *
 * 重复的**合法**块仍按原实现"最后一块生效"，这里不改变该行为。
 *
 * 空 HTTP 响应（相机未出流时返回 200 + 空体）**不应**传到这里 ——
 * 那是"暂不可用"，由调用方重试。
 */
object NxOsdValidator {

    const val FRAME_W = NxOsd.EXPECTED_FRAME_W       // 720
    const val FRAME_H = NxOsd.EXPECTED_FRAME_H       // 480
    const val BLOCK_W = NxOsd.EXPECTED_BLOCK_W       // 80
    const val BLOCK_H = NxOsd.EXPECTED_BLOCK_H       // 40
    const val COLUMNS = FRAME_W / BLOCK_W            // 9
    const val ROWS = FRAME_H / BLOCK_H               // 12
    const val BLOCK_COUNT = COLUMNS * ROWS           // 108
    const val BLOCK_BYTES = BLOCK_W * BLOCK_H * 4    // 12800

    /** 每条记录的字节数：x(2) + y(2) + hash(4) + 像素(12800) */
    const val RECORD_BYTES = 8 + BLOCK_BYTES         // 12808

    /** 最小合法长度：8 字节帧头 + 4 字节结束标记（零个块，即"无变化"） */
    const val MIN_BYTES = 12

    /** 结束标记 */
    private const val END = 0xFF.toByte()

    /**
     * 校验并解析。
     *
     * @throws NxOsd.OsdParseException 任何一处不符合契约
     */
    @Throws(NxOsd.OsdParseException::class)
    fun parseStrict(raw: ByteArray, geometry: OsdGeometry = OsdGeometry.NX500): NxOsd.Frame {
        if (raw.size < MIN_BYTES) bad("OSD 响应过短：${raw.size} 字节（至少 $MIN_BYTES）")

        // ---- 帧头必须精确匹配契约 ----
        val header = ByteBuffer.wrap(raw, 0, 8).order(ByteOrder.BIG_ENDIAN)
        val w = header.short.toInt() and 0xFFFF
        val h = header.short.toInt() and 0xFFFF
        val bw = header.short.toInt() and 0xFFFF
        val bh = header.short.toInt() and 0xFFFF
        if (w != geometry.width || h != geometry.height || bw != BLOCK_W || bh != BLOCK_H) {
            bad("OSD 帧头不符合契约：${w}×$h / ${bw}×$bh，应为 ${geometry.width}×${geometry.height} / $BLOCK_W×$BLOCK_H")
        }

        // ---- 长度必须能被"定长记录 + 头尾"整除 ----
        if ((raw.size - MIN_BYTES) % RECORD_BYTES != 0) {
            bad("OSD 记录被截断或含多余字节：${raw.size} 字节")
        }

        // ---- 末尾必须是 FFFF FFFF ----
        val end = raw.size - 4
        for (i in end until raw.size) {
            if (raw[i] != END) bad("OSD 缺少末尾结束标记 FFFF FFFF")
        }

        // ---- 逐条检查坐标：必须在解析前做，否则会被同索引的后续记录掩盖 ----
        var offset = 8
        while (offset < end) {
            val x = ((raw[offset].toInt() and 0xFF) shl 8) or (raw[offset + 1].toInt() and 0xFF)
            val y = ((raw[offset + 2].toInt() and 0xFF) shl 8) or (raw[offset + 3].toInt() and 0xFF)
            validatePosition(x, y, geometry)
            offset += RECORD_BYTES
        }

        // ---- 交给解析器，再复核它的输出 ----
        return NxOsd.parse(raw).also { validateFrame(it, geometry) }
    }

    /** 坐标必须在帧内、且对齐到块的整数倍 */
    fun validatePosition(x: Int, y: Int, geometry: OsdGeometry = OsdGeometry.NX500) {
        if (x !in 0..(geometry.width - BLOCK_W) || y !in 0..(geometry.height - BLOCK_H) ||
            x % BLOCK_W != 0 || y % BLOCK_H != 0
        ) {
            bad("OSD 块坐标越界或未对齐：($x,$y)")
        }
    }

    /** 复核解析结果：尺寸、每个块的索引与像素长度 */
    fun validateFrame(frame: NxOsd.Frame, geometry: OsdGeometry = OsdGeometry.NX500) {
        if (frame.frameW != geometry.width || frame.frameH != geometry.height ||
            frame.blockW != BLOCK_W || frame.blockH != BLOCK_H
        ) {
            bad("OSD 帧尺寸必须为 ${geometry.width}×${geometry.height}，块必须为 $BLOCK_W×$BLOCK_H")
        }
        for ((index, block) in frame.blocks) {
            validatePosition(block.x, block.y, geometry)
            val expectedIndex = (block.y / BLOCK_H) * geometry.columns + block.x / BLOCK_W
            if (index !in 0 until geometry.blockCount || index != expectedIndex) {
                bad("OSD 块索引不匹配：$index，期望 $expectedIndex")
            }
            if (block.rgba.size != BLOCK_BYTES) {
                bad("OSD 块像素长度错误：${block.rgba.size}，期望 $BLOCK_BYTES")
            }
        }
    }

    /** 给定块数时，一条合法响应应有的字节数（供测试与调用方自检） */
    fun expectedSizeForBlocks(blockCount: Int): Int = MIN_BYTES + blockCount * RECORD_BYTES

    private fun bad(message: String): Nothing = throw NxOsd.OsdParseException(message)
}
