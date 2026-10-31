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

import com.nxwa.nxconnect.NxApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket

/**
 * 相机 shell 通道（telnet，root 免密）。
 *
 * 为什么需要它：daemon 是个用户态程序，很多信息它给不了 ——
 *  - `/api/v1/camera/status` 的 `battery_percent` 恒为 -1（硬件不暴露百分比）
 *  - 8080 的 `capdtm-api` 在真机上全部返回空字符串
 *  - 无法设置相机时间
 *
 * 而相机端 nx-remote-controller-mod 装机时会**无条件拉起 telnetd**（23 端口，root 免密），
 * 这是它的"远程排查通路"（见 `scripts/onboard.sh`）。App 借此拿到真实系统信息。
 *
 * 实现要点（实测踩坑）：
 *  - 登录提示是 `drime5 login:`，必须**等提示符出现后**再发命令，否则命令会被登录流程吞掉
 *  - 相机上没有 `timeout` 命令（busybox 未编入），不能靠它兜底
 *  - 只做短连接：一次连接跑完所有命令就读到 EOF
 */
object NxShell {

    const val PORT = 23
    private const val USER = "root"
    private const val PASSWORD = ""   // mod 的 telnetd 免密

    /** 一次查询的总体超时 */
    private const val CONNECT_TIMEOUT_MS = 4_000
    private const val SO_TIMEOUT_MS = 1_500
    private const val TOTAL_TIMEOUT_MS = 12_000

    class ShellResult(
        val ok: Boolean,
        val output: String,
        val error: String? = null,
    ) {
        /** 从输出里取某个标记后的第一行 */
        fun after(marker: String): String? = output
            .substringAfter(marker, "")
            .lineSequence()
            .firstOrNull { it.isNotBlank() }
            ?.trim()

        /** 取形如 NAME=value 的结果 */
        fun value(name: String): String? = Regex("""(?m)^\s*${Regex.escape(name)}=?(.*)$""")
            .find(output)
            ?.groupValues?.get(1)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
    }

    /**
     * 连上相机跑一批 shell 命令。
     * 失败时返回 ok=false 且带上可读原因，绝不抛异常给调用方。
     */
    suspend fun run(ip: String, commands: List<String>, timeoutMs: Long = TOTAL_TIMEOUT_MS.toLong()): ShellResult =
        withContext(Dispatchers.IO) {
            var socket: Socket? = null
            try {
                socket = Socket().apply {
                    connect(InetSocketAddress(ip, PORT), CONNECT_TIMEOUT_MS)
                    soTimeout = SO_TIMEOUT_MS
                }
                val reader = TelnetReader(socket.getInputStream())
                val output = socket.getOutputStream()

                // 1) 等登录提示（reader 会自动滤掉 IAC 协商字节）
                if (!reader.await("login:", 6_000)) {
                    return@withContext ShellResult(false, "", "未看到登录提示（telnet 23 端口可能未开放）")
                }
                reader.clear()
                send(output, USER)

                // 2) 等 shell 提示符。相机免密，如果真出现 Password 提示也直接回车
                if (!reader.awaitPrompt(6_000)) {
                    if (reader.buffer().contains("assword", ignoreCase = true)) {
                        send(output, PASSWORD)
                        if (!reader.awaitPrompt(6_000)) {
                            return@withContext ShellResult(false, "", "登录后未出现 shell 提示符")
                        }
                    } else {
                        return@withContext ShellResult(false, "", "登录后未出现 shell 提示符")
                    }
                }
                reader.clear()

                // 3) 逐条执行。
                //
                // ★ 两个真机实测的坑：
                //  a) 相机 telnetd 会先发 IAC 协商（\xff\xfd\x01 等）**并开启回显**，
                //     所以原始流里既混着协议字节、也有我们发出去的命令回显；
                //  b) 因此不能靠"数标记出现次数"提取输出。
                //  做法：滤掉 IAC 与回显行（回显行等于我们刚发的那条命令），
                //  每执行一条就等提示符回来，这样边界最可靠。
                val sb = StringBuilder()
                for (cmd in commands) {
                    reader.clear()
                    send(output, cmd)
                    val text = reader.untilPrompt(timeoutMs = 8_000)
                    val clean = text.lines()
                        .filterNot { it.trim() == cmd.trim() }   // 丢掉回显
                        .filterNot { it.contains("echo ") && it.contains("NX_") }
                        .joinToString("\n")
                        .trim()
                    sb.append(clean).append('\n')
                }
                ShellResult(true, sb.toString())
            } catch (e: Exception) {
                ShellResult(false, "", "${e.javaClass.simpleName}: ${e.message}")
            } finally {
                try {
                    socket?.close()
                } catch (e: Exception) {
                    // 忽略
                }
            }
        }

    /**
     * telnet 读取器：负责剥掉 IAC 协商序列，并按提示符/超时切分输出。
     *
     * IAC 处理只做最小必要的事：把 `IAC <cmd> <opt>`（三字节）与 `IAC <cmd>`（两字节）
     * 整段丢弃。相机端发的是 WILL/DO/WONT/DONT，没有子协商，够用了。
     */
    private class TelnetReader(private val input: InputStream) {

        private val sb = StringBuilder()

        /** 读一次底层数据并做 IAC 过滤 */
        private fun pump(): Boolean {
            val buf = ByteArray(2048)
            val n = try {
                input.read(buf)
            } catch (e: java.net.SocketTimeoutException) {
                return true      // 暂时没数据，连接还在
            } catch (e: Exception) {
                return false
            }
            if (n < 0) return false
            var i = 0
            while (i < n) {
                val b = buf[i].toInt() and 0xFF
                if (b == IAC) {
                    if (i + 1 >= n) break
                    val cmd = buf[i + 1].toInt() and 0xFF
                    // WILL/WONT/DO/DONT 各带一个选项字节；其余（如 GA）只有两字节
                    i += if (cmd in 0xFB..0xFE) 3 else 2
                    continue
                }
                sb.append(b.toChar())
                i++
            }
            return true
        }

        fun await(needle: String, timeoutMs: Long): Boolean {
            val deadline = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < deadline) {
                if (sb.contains(needle)) return true
                if (!pump()) return false
            }
            return sb.contains(needle)
        }

        /**
         * 等 shell 提示符出现。
         *
         * ★ 不能用 `contains("# ")`：实测后续提示符是 `[root@drime5 ~]#`（**没有尾随空格**，
         * 因为内核把紧跟其后的空格吞掉了），只有首个提示符带空格。
         * 用正则匹配行尾的 `]#` 才可靠。
         */
        fun awaitPrompt(timeoutMs: Long): Boolean {
            val deadline = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < deadline) {
                if (PROMPT_AT_END.containsMatchIn(stripAnsi(sb.toString()))) return true
                if (!pump()) return false
            }
            return PROMPT_AT_END.containsMatchIn(stripAnsi(sb.toString()))
        }

        /** 读到出现提示符或超时；返回期间累积的文本（已剥掉提示符与颜色码） */
        fun untilPrompt(timeoutMs: Long): String {
            val deadline = System.currentTimeMillis() + timeoutMs
            while (System.currentTimeMillis() < deadline) {
                if (PROMPT_AT_END.containsMatchIn(stripAnsi(sb.toString()))) break
                if (!pump()) break
            }
            val text = sb.toString()
            sb.setLength(0)
            return stripPrompt(text)
        }

        fun buffer(): String = sb.toString()

        fun clear() {
            sb.setLength(0)
        }

        private fun stripAnsi(s: String): String =
            s.replace(Regex("""\u001B\[[0-9;]*[A-Za-z]"""), "")

        private fun stripPrompt(text: String): String {
            val s = stripAnsi(text)
            // 提示符形如 `[root@drime5 ~]# `，位于输出末尾；把它连同之前的一小段一起切掉
            val m = PROMPT_AT_END.find(s)
            val body = if (m != null) s.substring(0, m.range.first) else s
            return body.replace("\r", "").trim()
        }

        companion object {
            private const val IAC = 255

            /** 行尾提示符，如 `[root@drime5 ~]#` 或 `#` */
            private val PROMPT_AT_END = Regex("""(?m)(\[[^\]]*\]\s*)?#\s*$""")
        }
    }

    private fun send(out: OutputStream, line: String) {
        out.write((line + "\n").toByteArray(Charsets.UTF_8))
        out.flush()
    }

    /**
     * 设置相机时间。
     *
     * ★ 用**墙上时间字符串**，不用 `@epoch`（真机踩过的坑，用户实测报过）：
     * `date -s @<epoch>` 设的是绝对时间（UTC），显示时要经相机时区换算。
     * 而相机时区实测是 **KST(UTC+9)**，用户实际在 UTC+8 —— 结果相机比手机**快 1 小时**。
     * 改成 `date -s "YYYY-MM-DD HH:MM:SS"` 后，相机按自己时区解释该字符串，
     * **显示时间就等于传入的字符串**，与手机一致（真机验证：设置后读回完全一致）。
     *
     * 传 null 表示用手机当前本地时间。
     *
     * @return (是否成功, 说明)
     */
    suspend fun setTime(ip: String, epochSeconds: Long? = null): Pair<Boolean, String> {
        // 手机本地墙上时间（用户看到的那个时间）
        val wall = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            .format(java.util.Date(epochSeconds?.times(1000) ?: System.currentTimeMillis()))

        val res = run(
            ip,
            listOf(
                // 双引号包住，避免空格被当成多个参数
                "date -s \"$wall\"; echo SET_RC=\$?",
                "date '+%Y-%m-%d %H:%M:%S'",
            ),
        )
        if (!res.ok) return false to (res.error ?: "shell 不可用")
        val rc = res.output.lines()
            .firstOrNull { it.trim().startsWith("SET_RC=") }
            ?.substringAfter('=')
            ?.trim()
        if (rc != "0") {
            return false to "date -s 失败（rc=${rc ?: "无输出"}）"
        }
        val readBack = res.output.lines()
            .firstOrNull { it.matches(Regex("""\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}""")) }
            ?.trim()
        NxApp.log("相机时间已同步：目标 $wall，读回 $readBack")
        return true to (readBack ?: wall)
    }

    /**
     * 读相机当前时间与**手机本地时间**的偏差（秒）。
     *
     * 注意比较的是"墙上时间"而不是 UTC 绝对时间 —— 用户关心的是相机显示的时间
     * 是否与手机一致，而不是底层 epoch 是否相同（相机时区可能设错）。
     */
    suspend fun wallClockDriftSeconds(ip: String): Long? {
        val res = run(ip, listOf("date '+%Y-%m-%d %H:%M:%S'"))
        if (!res.ok) return null
        val line = res.output.lines()
            .firstOrNull { it.matches(Regex("""\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}""")) }
            ?.trim()
            ?: return null
        return try {
            val fmt = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            val cam = fmt.parse(line) ?: return null
            // 手机本地"墙上时间"对应的 epoch
            val phoneWall = fmt.parse(fmt.format(java.util.Date()))!!
            (cam.time - phoneWall.time) / 1000
        } catch (e: Exception) {
            null
        }
    }
}
