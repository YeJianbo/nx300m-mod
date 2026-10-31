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

import com.nxwa.nxconnect.data.MediaEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 目录页解析测试。
 *
 * HTML 片段是**真机抓回来的原文**（NX500 / mongoose 6.5 / 192.168.5.39）。
 * 用户相机里 `100PHOTO` 有 95 个文件：91 个 `.SRW`（19–34MB）与 4 个 `.JPG`（7.4–9.2MB）。
 * 8080 的 dirlist 不给大小，大小只能从这里取，所以这个解析器是"下载前知道体积"的关键。
 */
class ListingParserTest {

    /** 真机 80 端口 `/DCIM/` 的响应（节选头部与表格部分，结构完整） */
    private val realRootHtml = """
        <html><head><title>Index of /DCIM/</title><script>function srt(tb, sc, so, d) {...}</script>
        <style>th,td {text-align: left; padding-right: 1em; font-family: monospace; }</style></head>
        <body><h1>Index of /DCIM/</h1>
        <table cellpadding=0><thead><tr><th><a href=# rel=0>Name</a></th><th><a href=# rel=1>Modified</a</th><th><a href=# rel=2>Size</a></th></tr><tr><td colspan=3><hr></td></tr>
        </thead>
        <tbody id=tb><tr><td><a href="100PHOTO/">100PHOTO/</a></td><td>17-Feb-2015 00:54</td><td name=-1>[DIRECTORY]</td></tr>
        </tbody><tr><td colspan=3><hr></td></tr>
        </table>
        <address>Mongoose/6.5</address>
        </body></html>
    """.trimIndent()

    /** 真机 `/DCIM/100PHOTO` 的文件行（取自 FTP 实测大小） */
    private val realFolderHtml = """
        <html><head><title>Index of /DCIM/100PHOTO</title></head>
        <body><h1>Index of /DCIM/100PHOTO</h1>
        <table cellpadding=0><thead><tr><th><a href=# rel=0>Name</a></th><th><a href=# rel=1>Modified</a</th><th><a href=# rel=2>Size</a></th></tr></thead>
        <tbody id=tb>
        <tr><td><a href="SAM_4258.SRW">SAM_4258.SRW</a></td><td>08-Jan-2026 06:04</td><td>32102672</td></tr>
        <tr><td><a href="SAM_4282.JPG">SAM_4282.JPG</a></td><td>06-Feb-2026 02:43</td><td>7712754</td></tr>
        <tr><td><a href="SAM_4359.JPG">SAM_4359.JPG</a></td><td>16-Feb-2026 15:54</td><td>9223935</td></tr>
        <tr><td><a href="SAM_4359.SRW">SAM_4359.SRW</a></td><td>16-Feb-2026 15:54</td><td>32814969</td></tr>
        </tbody></table>
        <address>Mongoose/6.5</address></body></html>
    """.trimIndent()

    /**
     * 回归测试：曾经因为"先 stripTags 再取 href"导致大小与时间全部丢失。
     * 这个用例就是钉住这个顺序，防止以后再改回去。
     */
    @Test
    fun `必须从原始单元格取 href 而不是去标签之后`() {
        val html = """<html><body><table><tbody>
            <tr><td><a href="SAM_1.JPG">SAM_1.JPG</a></td><td>06-Feb-2026 02:43</td><td>7712754</td></tr>
            </tbody></table></body></html>"""
        val parsed = ListingParser.parse(html, onlyMedia = false)
        assertNotNull(parsed)
        assertEquals("文件名必须能取到", listOf("SAM_1.JPG"), parsed!!.files)
        assertEquals("大小必须能取到", 7712754L, parsed.sizes["SAM_1.JPG"])
        assertEquals("时间必须能取到", "06-Feb-2026 02:43", parsed.modified["SAM_1.JPG"])
    }

    @Test
    fun `解析真机 DCIM 根目录 只得到 100PHOTO 目录`() {
        val parsed = ListingParser.parse(realRootHtml)
        assertNotNull(parsed)
        assertEquals(listOf("100PHOTO"), parsed!!.dirs)
        assertTrue("根目录下不应有媒体文件", parsed.files.isEmpty())
        assertTrue("目录不应被算进大小表", parsed.sizes.isEmpty())
    }

    @Test
    fun `解析真机目录页 取到每个文件的真实字节数`() {
        val parsed = ListingParser.parse(realFolderHtml, onlyMedia = false)
        assertNotNull(parsed)
        assertEquals(4, parsed!!.files.size)
        assertEquals(32102672L, parsed.sizes["SAM_4258.SRW"])
        assertEquals(7712754L, parsed.sizes["SAM_4282.JPG"])
        assertEquals(9223935L, parsed.sizes["SAM_4359.JPG"])
        assertEquals(32814969L, parsed.sizes["SAM_4359.SRW"])
    }

    @Test
    fun `解析出修改时间`() {
        val parsed = ListingParser.parse(realFolderHtml, onlyMedia = false)!!
        assertEquals("08-Jan-2026 06:04", parsed.modified["SAM_4258.SRW"])
        assertEquals("16-Feb-2026 15:54", parsed.modified["SAM_4359.JPG"])
    }

    @Test
    fun `按序号倒序排列 最新在前`() {
        val parsed = ListingParser.parse(realFolderHtml, onlyMedia = false)!!
        assertEquals(
            listOf("SAM_4359.SRW", "SAM_4359.JPG", "SAM_4282.JPG", "SAM_4258.SRW"),
            parsed.files,
        )
    }

    @Test
    fun `RAW 与视频被识别为媒体 JPG 也是`() {
        assertTrue(MediaEntry.isMedia("SAM_4258.SRW"))
        assertTrue(MediaEntry.isMedia("SAM_4282.JPG"))
        assertTrue(MediaEntry.isMedia("MOV_3100.MP4"))
        assertFalse("非媒体扩展名应被过滤", MediaEntry.isMedia("notes.txt"))
        assertFalse(MediaEntry.isMedia("README"))
    }

    @Test
    fun `RAW 单张 30MB 能显示成人类可读体积`() {
        assertEquals("30.6 MB", MediaEntry("100PHOTO", "SAM_4258.SRW", 32102672L).sizeText)
        assertEquals("7.4 MB", MediaEntry("100PHOTO", "SAM_4282.JPG", 7712754L).sizeText)
        assertEquals("1.50 GB", MediaEntry("100PHOTO", "big.SRW", 1610612736L).sizeText)
        assertNull("未知大小不应瞎显示", MediaEntry("100PHOTO", "x.JPG").sizeText)
    }

    @Test
    fun `RAW 与普通图片被区分开`() {
        val raw = MediaEntry("100PHOTO", "SAM_4258.SRW")
        val jpg = MediaEntry("100PHOTO", "SAM_4282.JPG")
        val mp4 = MediaEntry("100PHOTO", "MOV_3100.MP4")

        assertTrue(raw.isRaw)
        assertFalse("RAW 不属于 thumb-cgi 白名单", raw.isPlainImage)
        assertTrue("但仍是图片类别", raw.isImage)
        assertEquals("RAW", raw.kindLabel)

        assertTrue(jpg.isPlainImage)
        assertFalse(jpg.isRaw)
        assertEquals("JPG", jpg.kindLabel)

        assertTrue(mp4.isVideo)
        assertFalse(mp4.isImage)
        assertEquals("视频", mp4.kindLabel)
    }

    @Test
    fun `简单链接格式 busybox httpd 也能解析`() {
        val html = """
            <html><body><pre>
            <a href="100PHOTO/">100PHOTO/</a>  17-Feb-2015 00:54
            <a href="SAM_4258.SRW">SAM_4258.SRW</a>  08-Jan-2026 06:04
            <a href="notes.txt">notes.txt</a>  08-Jan-2026 06:05
            </pre></body></html>
        """.trimIndent()
        val parsed = ListingParser.parse(html)
        assertNotNull(parsed)
        assertEquals(listOf("100PHOTO"), parsed!!.dirs)
        assertEquals(listOf("SAM_4258.SRW"), parsed.files)
    }

    @Test
    fun `路径穿越与隐藏项被忽略`() {
        val html = """
            <html><body>
            <a href="../">../</a>
            <a href=".thumbcache/">.thumbcache/</a>
            <a href="/api/v1/camera/info">info</a>
            <a href="SAM_1.JPG?x=1">SAM_1.JPG?x=1</a>
            <a href="SAM_2.JPG">SAM_2.JPG</a>
            </body></html>
        """.trimIndent()
        val parsed = ListingParser.parse(html)
        assertNotNull(parsed)
        assertTrue("不应出现父目录或隐藏目录", parsed!!.dirs.isEmpty())
        assertEquals(listOf("SAM_2.JPG"), parsed.files)
    }

    @Test
    fun `目录行的 size 单元格不会被当成文件大小`() {
        val sizes = ListingParser.parseSizes(realRootHtml)
        assertTrue("目录不带大小", sizes.isEmpty())
    }

    @Test
    fun `空目录返回 null 而不是空对象`() {
        assertNull(ListingParser.parse("<html><body>no links here</body></html>"))
    }
}
