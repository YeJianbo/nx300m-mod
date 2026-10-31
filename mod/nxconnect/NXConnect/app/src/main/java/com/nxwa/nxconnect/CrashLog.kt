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
package com.nxwa.nxconnect

import android.content.Context
import android.os.Build
import android.util.Log
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 崩溃落盘。
 *
 * 为什么需要：真机实测出现"切低画质 → UI 卡 1 秒 → 直接返回上一级、连接中断"，
 * 这是**进程被杀**（崩溃）而不是 App 主动退出。没有 logcat 就无从定位，
 * 所以把未捕获异常写进文件，下次启动时弹给用户看，用户截图即可反馈。
 */
object CrashLog {

    private const val TAG = "NxCrash"
    private const val FILE_NAME = "last_crash.txt"
    private const val MAX_BYTES = 64 * 1024

    private fun file(ctx: Context): File = File(ctx.filesDir, FILE_NAME)

    /** 在 Application.onCreate 里尽早安装 */
    fun install(ctx: Context) {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                write(ctx, thread, throwable)
            } catch (e: Exception) {
                Log.e(TAG, "写崩溃日志失败", e)
            }
            // 交回系统默认处理，保证进程正常终止（否则会卡住不退出）
            previous?.uncaughtException(thread, throwable)
        }
    }

    /** 记录一次"非崩溃但值得追查"的异常（例如取景线程的致命错误） */
    fun record(ctx: Context, where: String, throwable: Throwable) {
        try {
            write(ctx, Thread.currentThread(), throwable, where)
        } catch (e: Exception) {
            Log.e(TAG, "记录异常失败", e)
        }
    }

    private fun write(ctx: Context, thread: Thread, throwable: Throwable, where: String? = null) {
        val sw = StringWriter()
        throwable.printStackTrace(PrintWriter(sw))
        val stamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

        val text = buildString {
            append("时间: ").append(stamp).append('\n')
            append("版本: ").append(BuildConfig.VERSION_NAME)
            append(" (").append(BuildConfig.VERSION_CODE).append(") build ")
            append(BuildConfig.BUILD_STAMP).append('\n')
            where?.let { append("位置: ").append(it).append('\n') }
            append("线程: ").append(thread.name).append('\n')
            append("机型: ").append(Build.MANUFACTURER).append(' ').append(Build.MODEL)
            append("  Android ").append(Build.VERSION.RELEASE)
            append(" (API ").append(Build.VERSION.SDK_INT).append(")\n")
            append("相机: ").append(NxApp.cameraIp ?: "-")
            append("  daemon:").append(NxApp.daemonPort).append(" cgi:").append(NxApp.cgiPort).append('\n')
            append("--- 异常 ---\n")
            append(sw.toString())
            append("\n--- 应用日志（最近） ---\n")
            append(NxApp.logs().joinToString("\n"))
            append('\n')
        }

        val f = file(ctx)
        f.writeText(text.take(MAX_BYTES))
        Log.e(TAG, "已记录到 ${f.absolutePath}")
    }

    /** 读取上次崩溃记录；没有则返回 null */
    fun readLast(ctx: Context): String? {
        val f = file(ctx)
        if (!f.exists() || f.length() == 0L) return null
        return try {
            f.readText()
        } catch (e: Exception) {
            null
        }
    }

    fun clear(ctx: Context) {
        try {
            file(ctx).delete()
        } catch (e: Exception) {
            // 忽略
        }
    }
}
