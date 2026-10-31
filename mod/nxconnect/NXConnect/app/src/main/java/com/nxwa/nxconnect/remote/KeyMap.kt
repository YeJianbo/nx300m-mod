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
package com.nxwa.nxconnect.remote

/**
 * 相机按键 → X11 keysym 映射。
 *
 * 逐字来自相机端 `web_root/js/keyinput.js`，注入路径为
 * `GET /api/v1/input/inject?key=<keysym>`（相机端走 libXtst 注入 X 事件）。
 *
 * 拍摄语义：
 *  - [S1] 半按快门（对焦） = Super_L
 *  - [S2] 全按快门（拍摄） = Super_R
 *  - 另有更直接的 `GET /api/v1/shutter/normal|silent`（NX500 静音快门）
 */
object KeyMap {
    const val UP = "KP_Up"
    const val DOWN = "KP_Down"
    const val LEFT = "KP_Left"
    const val RIGHT = "KP_Right"
    const val OK = "KP_Enter"
    const val MENU = "Menu"
    const val DEL = "KP_Delete"

    /** 快门半按/对焦 */
    const val S1 = "Super_L"

    /** 快门全按/拍摄 */
    const val S2 = "Super_R"

    const val AEL = "XF86Favorites"
    const val REC = "XF86WebCam"
    const val FN = "XF86HomePage"

    /** NX500 的 EV 键；NX1 上是 XF86WWW */
    const val EV_NX500 = "XF86Reload"
    const val EV_NX1 = "XF86WWW"

    const val PB = "XF86Tools"
    const val AF_MODE = "XF86TaskPane"
    const val WB = "XF86Launch6"
    const val ISO = "XF86Launch7"
    const val AF_ON = "XF86Launch9"
    const val LIGHT = "XF86TouchpadToggle"
    const val MF_ZOOM = "XF86TouchpadOff"
    const val WIFI = "XF86Mail"

    const val JOG1_CW = "XF86ScrollUp"
    const val JOG1_CCW = "XF86ScrollDown"
    const val JOG2_CW = "parenleft"
    const val JOG2_CCW = "parenright"
    const val JOG_CW = "XF86AudioNext"
    const val JOG_CCW = "XF86AudioPrev"

    const val MODE_P = "XF86MailForward"
    const val MODE_A = "XF86Save"
    const val MODE_S = "XF86Documents"
    const val MODE_M = "XF86Battery"
    const val MODE_CUSTOM1 = "XF86WLAN"
    const val MODE_CUSTOM2 = "XF86Bluetooth"

    // ---- 以下为 A/B/C 组补充，同样逐字来自 keyinput.js ----

    /** 测光模式 */
    const val METER = "Hiragana_Katakana"

    /** 景深预览 */
    const val DEPTH = "Henkan_Mode"

    /** 智能模式 / 场景模式 / SAS */
    const val MODE_SMART = "XF86Reply"
    const val MODE_SCENE = "XF86Send"
    const val MODE_SAS = "XF86KbdBrightnessDown"

    /** 驱动模式 */
    const val DRIVE_SINGLE = "XF86Search"
    const val DRIVE_CONTI_N = "XF86Go"
    const val DRIVE_CONTI_H = "XF86Finance"
    const val DRIVE_TIMER = "XF86Game"
    const val DRIVE_BRACKET = "XF86Shop"

    const val WHEEL_CW = "Left"
    const val WHEEL_CCW = "Right"

    /**
     * 按键分组定义，供遥控面板渲染。
     *
     * `hold = true` 的项表示应当用**按下/松开**语义（keydown/keyup）而不是单击：
     * 相机上这些键本来就是"按住期间生效"的，例如 AE 锁定、曝光补偿调节。
     * 用单击会变成"按一下就松"，与机身操作手感不一致。
     */
    data class KeyDef(val label: String, val keysym: String, val hold: Boolean = false)

    // NX300M 1.15: stock Xmodmap and UI_Event_Convert. The directional
    // shortcuts share physical keys; ISO/AF are not the NX500 dedicated keys.
    val NX300_DPAD: List<KeyDef?> = listOf(
        null, KeyDef("DISP · ↑", UP), null,
        KeyDef("驱动 · ←", LEFT), KeyDef("OK", OK), KeyDef("AF · →", RIGHT),
        null, KeyDef("ISO · ↓", DOWN), null,
    )
    val NX300_BODY: List<KeyDef> = listOf(
        KeyDef("MENU / 返回", MENU), KeyDef("Fn", FN),
        KeyDef("± 曝光补偿", EV_NX500, hold = true), KeyDef("视频录制", REC),
        KeyDef("回放", PB), KeyDef("删除", DEL),
        KeyDef("半按对焦", S1, hold = true),
    )
    val NX300_DIALS: List<KeyDef> = listOf(
        KeyDef("主拨轮 ↶", JOG1_CCW), KeyDef("主拨轮 ↷", JOG1_CW),
        KeyDef("后转盘 ↶", WHEEL_CCW), KeyDef("后转盘 ↷", WHEEL_CW),
    )
    val NX300_MODES: List<KeyDef> = listOf(
        KeyDef("P", "F7"), KeyDef("A", "F8"),
        KeyDef("S", "F9"), KeyDef("M", "F10"),
        KeyDef("智能自动", "F6"), KeyDef("智能场景", "F4"),
        KeyDef("镜头优先", "F1"), KeyDef("Wi-Fi", "F3"),
    )

    /** A 组：曝光相关专用键 */
    val GROUP_EXPOSURE: List<KeyDef> = listOf(
        KeyDef("ISO", ISO),
        KeyDef("白平衡", WB),
        KeyDef("测光", METER),
        KeyDef("对焦模式", AF_MODE),
        KeyDef("对焦触发", AF_ON),
        KeyDef("景深预览", DEPTH),
        KeyDef("曝光补偿", EV_NX500, hold = true),
        KeyDef("AE 锁定", AEL, hold = true),
    )

    /** C 组：拍摄模式与驱动模式 */
    val GROUP_MODE: List<KeyDef> = listOf(
        KeyDef("P", MODE_P),
        KeyDef("A", MODE_A),
        KeyDef("S", MODE_S),
        KeyDef("M", MODE_M),
        KeyDef("智能", MODE_SMART),
        KeyDef("场景", MODE_SCENE),
        KeyDef("SAS", MODE_SAS),
        KeyDef("自定义1", MODE_CUSTOM1),
        KeyDef("自定义2", MODE_CUSTOM2),
        KeyDef("单张", DRIVE_SINGLE),
        KeyDef("连拍", DRIVE_CONTI_N),
        KeyDef("高速连拍", DRIVE_CONTI_H),
        KeyDef("定时", DRIVE_TIMER),
        KeyDef("包围", DRIVE_BRACKET),
    )

    /** B 组：方向键 + 转盘 + 滚轮 */
    val GROUP_NAV: List<KeyDef> = listOf(
        KeyDef("▲", UP),
        KeyDef("◀", LEFT),
        KeyDef("▶", RIGHT),
        KeyDef("▼", DOWN),
        KeyDef("OK", OK),
        KeyDef("MENU", MENU),
        KeyDef("Fn", FN),
        KeyDef("播放", PB),
        KeyDef("删除", DEL),
        KeyDef("转盘 −", JOG_CCW),
        KeyDef("转盘 +", JOG_CW),
        KeyDef("副盘 −", JOG1_CCW),
        KeyDef("副盘 +", JOG1_CW),
        KeyDef("滚轮 −", WHEEL_CCW),
        KeyDef("滚轮 +", WHEEL_CW),
        KeyDef("录制", REC),
    )

    /** 兼容旧引用：所有按键的扁平列表（显示名 → keysym） */
    val PANEL: List<Pair<String, String>> =
        (GROUP_EXPOSURE + GROUP_MODE + GROUP_NAV).map { it.label to it.keysym }
}
