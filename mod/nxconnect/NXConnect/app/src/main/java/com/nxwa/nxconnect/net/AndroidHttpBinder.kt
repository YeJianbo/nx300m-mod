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

import android.content.Context
import android.net.Network
import java.net.HttpURLConnection
import java.net.URL

/**
 * Android 实现：把请求绑定到相机所在的网络。
 *
 * 在构造时捕获网络，后续请求不受发现流程或进程默认路由变化影响。
 */
class AndroidHttpBinder(private val network: Network?) : HttpBinder {

    @Suppress("UNUSED_PARAMETER")
    constructor(ctx: Context, network: Network? = NetworkBinder.boundNetwork) : this(network)

    override fun openConnection(url: URL): HttpURLConnection =
        (network?.openConnection(url) ?: url.openConnection()) as HttpURLConnection

    override fun bind(conn: HttpURLConnection) = Unit
}
