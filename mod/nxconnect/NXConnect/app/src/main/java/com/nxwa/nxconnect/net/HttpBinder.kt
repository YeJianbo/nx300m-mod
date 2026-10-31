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

import java.net.HttpURLConnection
import java.net.URL

/**
 * 把连接绑定到某个网络。
 *
 * 抽成接口是为了让协议层保持纯 JVM：
 *  - 相机热点没有外网，Android 上必须用 ConnectivityManager 把流量钉在相机所在 Wi-Fi，
 *    对应实现 [com.nxwa.nxconnect.net.AndroidHttpBinder]；
 *  - 单元测试里不需要绑定，用 [None] 即可，于是 NxCamera / NxHttp 可以脱离 Android 运行。
 */
interface HttpBinder {
    fun bind(conn: HttpURLConnection)

    /** 在建立连接时选择网络；旧实现仍可通过 bind 配置连接。 */
    fun openConnection(url: URL): HttpURLConnection =
        (url.openConnection() as HttpURLConnection).also { bind(it) }

    /** 测试与桌面环境的空实现 */
    object None : HttpBinder {
        override fun bind(conn: HttpURLConnection) = Unit
    }
}
