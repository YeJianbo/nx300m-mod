// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.ui

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.nxwa.nxconnect.NxApp
import com.nxwa.nxconnect.net.ExposureControls
import com.nxwa.nxconnect.net.InstallPackageInspector
import com.nxwa.nxconnect.net.ModManagement
import com.nxwa.nxconnect.net.NxCamera
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Effect controls use existing APIs. Package import stays on the phone. */
class ModManagerActivity : OneUiInfoActivity() {
    private lateinit var page: OneUiInfoPage
    private lateinit var connection: TextView
    private lateinit var feedback: TextView
    private lateinit var modules: LinearLayout
    private lateinit var lookDetail: TextView
    private lateinit var packageDetail: TextView
    private lateinit var planDetail: TextView
    private lateinit var policyDetail: TextView
    private lateinit var chooseLook: MaterialButton
    private lateinit var disableLook: MaterialButton
    private lateinit var resetRecipe: MaterialButton
    private val buttons = mutableListOf<MaterialButton>()
    private var busy = false
    private var stockPicker = false
    private var looks: ExposureControls.LooksState? = null
    private var loadedCamera: NxCamera? = null
    private val picker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) inspect(uri, stockPicker)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        stockPicker = savedInstanceState?.getBoolean("stockPicker") ?: false
        page = OneUiInfoPage(this, "Mod 与固件")
        page.card().also { card ->
            connection = page.text("正在读取相机…").also(card::addView)
            feedback = page.text("", secondary = true).also(card::addView)
            button(card, "刷新状态") { refresh() }
        }
        modules = page.card("机内模块")
        page.card("启动配置").also { card ->
            policyDetail = page.text("连接后读取下次启动配置。", secondary = true).also(card::addView)
        }
        page.card("色彩效果").also { card ->
            lookDetail = page.text("读取 LUT 状态后可选择或关闭效果。", secondary = true).also(card::addView)
            chooseLook = button(card, "LUT 库") { chooseLut() }
            disableLook = button(card, "关闭 LUT，使用原厂色彩") {
                perform("正在关闭 LUT…") {
                    val state = onConnected { it.selectLook(null) }
                    feedback.text = if (state.forwarded) "原厂 LUT 选择已转发" else "已选择原厂 LUT；等待成像链处理"
                    loadCamera()
                }
            }
            resetRecipe = button(card, "恢复原厂配方") {
                val target = loadedCamera?.takeIf { it === NxApp.camera } ?: return@button
                NxDialogBuilder(this).setTitle("恢复原厂配方？")
                    .setMessage("将配方七项参数恢复为原厂中性值。LUT 选择单独管理。")
                    .setNegativeButton("取消", null).setPositiveButton("恢复") { _, _ ->
                        perform("正在恢复配方…") {
                            onConnected(target) { it.resetRecipe() }
                            feedback.text = "配方七项参数已读回原厂中性值"
                            loadCamera()
                        }
                    }.show()
            }
        }
        page.card("安装包准备").also { card ->
            packageDetail = page.text("选择完整开发安装候选 tar.gz，或 NX300M 固件文件。导入只在手机读取检查，不会传入相机。", secondary = true).also(card::addView)
            button(card, "检查 Mod 安装候选") { stockPicker = false; picker.launch(arrayOf("*/*")) }
            button(card, "检查 NX300M 固件文件") { stockPicker = true; picker.launch(arrayOf("*/*")) }
        }
        page.card("准备与安装").also { card ->
            planDetail = page.text(defaultPlan(), secondary = true).also(card::addView)
        }
        refresh()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("stockPicker", stockPicker)
        super.onSaveInstanceState(outState)
    }

    private fun button(parent: LinearLayout, title: String, action: () -> Unit): MaterialButton =
        MaterialButton(this).apply {
            text = title
            isAllCaps = false
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = page.dp(10) }
            setOnClickListener { if (!busy) action() }
            parent.addView(this)
            buttons.add(this)
        }

    private fun checkCurrent(camera: NxCamera) {
        check(NxApp.camera === camera) { "相机连接已变化，请刷新状态后重试" }
    }

    private suspend fun <T> onConnected(expected: NxCamera? = loadedCamera, action: suspend (NxCamera) -> T): T {
        val camera = expected ?: error("请先连接相机并刷新状态")
        checkCurrent(camera)
        return action(camera).also { checkCurrent(camera) }
    }

    private fun controls() {
        buttons.forEach { it.isEnabled = !busy }
        val current = loadedCamera?.takeIf { it === NxApp.camera }
        chooseLook.isEnabled = !busy
        disableLook.isEnabled = !busy && current != null && looks?.writable == true
        resetRecipe.isEnabled = !busy && current?.profile?.recipeStyle == true
    }

    private fun perform(message: String, action: suspend () -> Unit) {
        if (busy) return
        busy = true
        feedback.text = message
        controls()
        lifecycleScope.launch {
            try { action() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (failure: Exception) {
                if (loadedCamera !== NxApp.camera) { loadedCamera = null; looks = null }
                feedback.text = failure.message ?: "操作未完成，请重试"
            }
            finally { busy = false; controls() }
        }
    }

    private fun refresh() = perform("正在读取…") {
        val complete = loadCamera()
        feedback.text = if (complete) "状态已刷新" else if (NxApp.camera == null) "请先连接相机" else "部分状态读取失败，请刷新重试"
    }

    private suspend fun loadCamera(): Boolean {
        loadedCamera = null
        looks = null
        connection.text = "正在读取相机…"
        modules.removeAllViews()
        modules.addView(page.text("正在读取模块状态…", secondary = true))
        lookDetail.text = "尚未读取 LUT 状态"
        policyDetail.text = "尚未读取启动配置"
        planDetail.text = defaultPlan()
        val camera = NxApp.camera
        if (camera == null) {
            connection.text = "尚未连接相机"
            modules.removeAllViews()
            modules.addView(page.text("连接后显示机内模块的文件与实际加载状态。", secondary = true))
            return false
        }
        var complete = true
        camera.info()
        checkCurrent(camera)
        try {
            val state = camera.readModStatus()
            checkCurrent(camera)
            connection.text = "${state.model} · 固件 ${state.firmware}\nSD 服务${if (state.sdComplete) "完整" else "资源不完整"} · ${state.root}"
            renderModules(state)
            renderPolicy(state)
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) {
            checkCurrent(camera)
            complete = false
            connection.text = "已连接 ${camera.ip}:${camera.port}"
            modules.removeAllViews()
            modules.addView(page.text("当前服务未提供模块管理状态。请更新配套 remote 后刷新。", secondary = true))
            policyDetail.text = "启动配置读取失败：${error.message ?: "请刷新重试"}"
        }
        try {
            val plan = camera.readModInstallPlan()
            checkCurrent(camera)
            planDetail.text = "Mod\n" + plan.modSteps.mapIndexed { i, line -> "${i + 1}. $line" }.joinToString("\n\n") +
                "\n\n原厂固件\n" + plan.stockSteps.mapIndexed { i, line -> "${i + 1}. $line" }.joinToString("\n\n") +
                if (plan.stockFilePresent) "\n\nSD 根目录已发现 nx300m.bin；尚未核验其内容或来源。" else ""
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { checkCurrent(camera); complete = false; planDetail.text = defaultPlan() }
        var lookError: String? = null
        try {
            looks = if (camera.profile.looks) camera.readLooks() else null
            checkCurrent(camera)
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) {
            checkCurrent(camera)
            complete = false
            looks = null
            lookError = "LUT 状态读取失败：${error.message ?: "请刷新重试"}"
        }
        lookDetail.text = looks?.let { state ->
            val selected = state.looks.firstOrNull { it.id == state.selectedId }?.label ?: "未识别的 LUT"
            (if (state.stock) "原厂 LUT" else selected) + "\n" +
                if (state.forwarded) "选择已转发 · ${state.stage}" else "选择状态：${state.stage}"
        } ?: lookError ?: "当前相机未提供可读取的 LUT 库"
        loadedCamera = camera
        return complete
    }

    private fun renderPolicy(state: ModManagement.Status) {
        val policy = state.launchPolicy
        if (policy == null) {
            policyDetail.text = "当前服务未提供启动配置；当前加载情况见机内模块。"
            return
        }
        val lines = mutableListOf<String>()
        policy.configuredProfile?.let { lines += "已配置：${ModManagement.profileLabel(it)}" }
        policy.nextProfile?.let { lines += "下次启动检查：${ModManagement.profileLabel(it)}" }
        if (policy.disabled == true) lines += "停用标记已开启，下次启动跳过项目模块。"
        if (policy.loaderAvailable == false) lines += "加载脚本不可用，下次启动不注入项目模块。"
        if (policy.filesComplete == false) {
            val names = policy.unavailableModules.map { id -> state.modules.firstOrNull { it.id == id }?.name ?: id }
            lines += "所选分组文件不完整" + if (names.isEmpty()) "" else "：${names.joinToString("、")}"
        }
        if (policy.filesComplete == null || policy.loaderAvailable == null) lines += "当前服务未提供完整的加载文件检查。"
        if (state.processVerified) lines += "当前已加载 ${state.modules.count { it.mapped == true }}/${state.modules.size} 项"
        else lines += "当前相机进程尚未确认"
        lines += "配置在相机程序下次启动时检查；休眠恢复会保留原有模块。"
        policyDetail.text = lines.joinToString("\n\n")
    }

    private fun renderModules(state: ModManagement.Status) {
        modules.removeAllViews()
        modules.addView(page.text(if (state.processVerified) "当前已加载 ${state.modules.count { it.mapped == true }} 项" else "相机进程状态待确认", 17f))
        state.modules.forEach { module ->
            val detail = when {
                module.ready == true -> "接口已就绪"
                module.mapped == true -> "相机进程已加载"
                module.mapped == false -> if (module.installed) "文件已安装，当前未加载" else "当前未加载，未发现内置文件"
                module.installed -> "文件已安装，进程状态未知"
                else -> "未发现内置文件，进程状态未知"
            }
            page.row(modules, module.name, detail)
        }
        modules.addView(page.text("模块文件与当前效果分别管理。进程内模块的更新或卸载需要重新加载相机程序，并处理对应休眠快照。", 13f, true))
    }

    private fun chooseLut() {
        OneUiTransitions.open(this, Intent(this, LutLibraryActivity::class.java)
            .putExtra("camera_tab", NxApp.camera?.profile?.looks == true))
    }

    private fun inspect(uri: Uri, stock: Boolean) = perform("正在读取并检查安装包…") {
        val result = withContext(Dispatchers.IO) {
            contentResolver.openInputStream(uri)?.use { input ->
                if (stock) InstallPackageInspector.firmware(input) else InstallPackageInspector.mod(input)
            } ?: error("无法读取所选文件")
        }
        try { contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        catch (_: SecurityException) { /* Some document providers grant only this session. */ }
        packageDetail.text = result.title + "\n\n" + result.detail
        feedback.text = "检查完成；未向相机发送或安装文件"
    }

    private fun defaultPlan() = "Mod\n1. 检查完整九模块安装候选及原厂恢复文件。\n2. 将配套 nx300m-mod 目录准备到 SD 根目录。\n3. 机内 rootfs 增量与休眠快照需要已验证的机身安装入口；当前 App 没有远程刷机执行器。\n\n原厂固件\n1. 从三星对应 NX300M 支持页取得未修改的原包，核对型号和升级说明。\n2. 充足充电、备份照片，按说明准备 SD 根目录的 nx300m.bin。\n3. 在相机原厂固件更新入口执行，期间保持供电。Mod 候选不是原厂升级包。"
}
