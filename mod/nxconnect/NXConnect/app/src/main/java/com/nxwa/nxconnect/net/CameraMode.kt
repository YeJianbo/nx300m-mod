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

/**
 * 相机的三种可达形态。
 *
 * 逆向结论（子代理协议报告 + 源码核实）：
 *  - **原厂 NX500 固件里没有 80 端口 HTTP 服务**，Tizen 缺 telnet/http/ftp 可执行文件；
 *  - 装了 mewlips nx-remote-controller-mod / NX-KS / NX-WA 之后才有 `/api/v1/` 与 8080 CGI；
 *  - 完全不装 mod 也有一条路：SD 卡根放 `autoexec.sh` 起 busybox httpd 服务 SD 卡，
 *    此时 80 端口就是普通文件服务器，目录页可解析。
 *
 * App 需要同时兼容这三种形态，所以运行期做能力探测而不是假设。
 */
enum class CameraMode {
    /** 装了 mod：80 端口 daemon（`/api/v1/` 控制接口）+ 8080 CGI（实时目录/缩略图） */
    FULL_MOD,

    /** 只有 80 端口静态文件服务（busybox httpd 或 daemon 的目录页） */
    STATIC_ONLY,

    /** 什么都没有，只有官方 UPnP/SOAP 通路（7676/7679）—— 本 App 暂不支持 */
    STOCK,
    ;

    val canLiveView: Boolean get() = this == FULL_MOD
    val canInjectKeys: Boolean get() = this == FULL_MOD
    val canRealTimeDirList: Boolean get() = this == FULL_MOD

    val description: String
        get() = when (this) {
            FULL_MOD -> "完全体：可实时列目录、缩略图、取景、快门"
            STATIC_ONLY -> "简化体：只能列目录与下载原图（未装 mod 或 CGI 未启动）"
            STOCK -> "原厂固件：无 HTTP 服务，需先启用相机端服务"
        }
}

/**
 * 运行期能力探测结果。
 */
data class CameraCapabilities(
    val mode: CameraMode,
    val daemonOk: Boolean,
    val cgiDirlistOk: Boolean,
    val cgiThumbOk: Boolean,
    val notes: List<String> = emptyList(),
    val profile: CameraProfile = CameraProfile(),
) {
    val summary: String
        get() = buildString {
            if (profile.screenOnly && daemonOk) {
                append("可用：相机屏幕镜像、遥控按键")
                if (cgiDirlistOk) append("、相册与原图下载")
            } else append(mode.description)
            val missing: List<String> = buildList {
                if (!daemonOk) this.add("/api/v1 控制接口")
                if (!cgiDirlistOk) this.add("实时目录")
                if (!cgiThumbOk) this.add("缩略图")
            }
            if (missing.isNotEmpty()) append("；缺少：").append(missing.joinToString("、"))
        }
}
