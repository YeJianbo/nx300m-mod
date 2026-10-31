// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.os.Bundle

class ConnectionHelpActivity : OneUiInfoActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val page = OneUiInfoPage(this, "连接帮助")
        page.card("连接相机").addView(page.text(
            "NX300M 插入配套 SD 卡并正常开机，等待遥控服务启动。将拨盘转到 Wi-Fi，选择原厂遥控取景器。在手机系统 WLAN 设置中连接相机热点，再回到首页点“自动查找”。\n\n首页显示“Mod 已连接”后，相册服务即可使用，应用会继续原厂配对；首次请在相机上允许这台手机，首页显示“原厂已连接”后机身进入遥控状态。首页配对不会启动视频。取消或失败后，在首页“更多”中选“重试原厂连接”，或在遥控页点“连接原厂遥控”；原厂配对失败不影响已接通的 Mod。\n\n点击首页“遥控”会复用原厂连接并启动取景。关闭已启动的原厂取景可能结束相机热点，需要重新连接；未启动视频时离开页面会保留原厂控制连接。要调整机身菜单，从首页“相机设置”进入“原厂菜单”。相机热点没有互联网是正常情况，请保持连接。", secondary = true))
        page.card("NFC 碰一碰").addView(page.text(
            "在手机开启 NFC 和 WLAN，保持 NXConnect 首页在前台，再靠近相机的 NFC 标识。读到兼容的配对信息后，按系统提示加入相机 WLAN，识别相机服务后打开相册，无需连接路由器。\n\n如果提示标签尚不兼容，可先用“连接”加入相机 WLAN。NFC 负责读取配对信息，照片和遥控通过 WLAN 传输；相机无线连接与配套服务仍需开启。", secondary = true))
        page.card("通过 Samsung Link 联网").addView(page.text(
            "也可在相机的 Samsung Link 中加入已有 WLAN，让手机连接同一个 WLAN，再回到首页点“自动查找”。需要路由器允许设备互访；访客网络或设备隔离可能阻止连接。\n\n手动连接时填写相机分配到的地址，控制与相册端口均为 8781。这个模式下路由器网关不是相机地址。是否能遥控拍摄仍取决于相机当前模式；建立 WLAN 连接不等于完成原厂遥控配对。本机的 Samsung Link 路径尚待实测。", secondary = true))
        page.card("手动连接").addView(page.text(
            "手机连上相机热点后，在首页打开“手动连接”，输入相机地址。配套 NX300M 服务的控制端口与相册端口均为 8781。相机自建热点时，可在手机 WLAN 详情中查看网关地址，作为相机地址的候选。\n\n如果使用其它遥控服务，端口以该服务的实际配置为准。", secondary = true))
        page.card("连接失败时").addView(page.text(
            "确认相机无线连接已开启、手机仍在相机所在的 WLAN，并允许应用请求的 WLAN 或位置权限。\n\n如果提示相机端遥控服务没有响应，检查配套 SD 卡是否插入、相机端服务是否已启动，以及手动连接中的地址和端口。仅开启原厂 WLAN 不会自动安装或启动 Mod。", secondary = true))
        page.card("手动镜头信息").addView(page.text(
            "在首页选择“手动镜头信息”，选中 JPEG 照片后填写镜头信息。应用会保存带有镜头信息的副本。", secondary = true))
    }
}
