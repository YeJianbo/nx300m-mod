// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import com.nxwa.nxconnect.data.MiniJson

object ModManagement {
    data class Module(val id: String, val name: String, val installed: Boolean, val mapped: Boolean?, val ready: Boolean?)
    data class Status(val model: String, val firmware: String, val root: String, val sdComplete: Boolean,
        val processVerified: Boolean, val modules: List<Module>, val launchPolicy: LaunchPolicy? = null)
    data class LaunchPolicy(val configuredProfile: String?, val nextProfile: String?, val disabled: Boolean?,
        val filesComplete: Boolean?, val loaderAvailable: Boolean?, val unavailableModules: List<String>)
    data class Plan(val stockFilePresent: Boolean, val modSteps: List<String>, val stockSteps: List<String>)

    private fun response(raw: String): Map<String, Any?> = MiniJson.parseObject(raw).also {
        if (MiniJson.bool(it, "ok") != true) throw NxException(MiniJson.str(it, "error") ?: "管理接口返回无效")
    }
    fun profileLabel(value: String?): String = when (value) {
        "stock" -> "原厂"
        "services" -> "仅服务"
        "controls" -> "相机控制"
        "color" -> "色彩与配方"
        "full" -> "完整功能"
        else -> "未提供"
    }
    private fun knownProfile(value: String?) = value?.takeIf { it in setOf("stock", "services", "controls", "color", "full") }
    fun status(raw: String): Status {
        val data = response(raw)
        val modules = MiniJson.arr(data["modules"])?.map { value ->
            val item = MiniJson.obj(value) ?: throw NxException("模块列表无效")
            Module(MiniJson.str(item, "id") ?: throw NxException("模块缺少标识"),
                MiniJson.str(item, "name") ?: "模块", MiniJson.bool(item, "installed_file") == true,
                MiniJson.bool(item, "mapped"), MiniJson.bool(item, "ready"))
        } ?: throw NxException("相机未提供模块状态")
        val policy = MiniJson.obj(data["launch_policy"])?.let {
            LaunchPolicy(knownProfile(MiniJson.str(it, "configured_profile")), knownProfile(MiniJson.str(it, "next_exec_profile")),
                MiniJson.bool(it, "disabled_marker_present"), MiniJson.bool(it, "selected_files_complete"),
                MiniJson.bool(it, "loader_available"), MiniJson.stringList(it["unavailable_modules"]))
        }
        return Status(MiniJson.str(data, "model") ?: "未知型号", MiniJson.str(data, "firmware") ?: "未知版本",
            MiniJson.str(data, "resource_root").orEmpty(), MiniJson.bool(data, "sd_service_complete") == true,
            MiniJson.bool(data, "camera_process_verified") == true, modules, policy)
    }
    fun plan(raw: String): Plan = response(raw).let {
        Plan(MiniJson.bool(it, "stock_firmware_file_present") == true,
            MiniJson.stringList(it["mod_steps"]), MiniJson.stringList(it["stock_steps"]))
    }
}
