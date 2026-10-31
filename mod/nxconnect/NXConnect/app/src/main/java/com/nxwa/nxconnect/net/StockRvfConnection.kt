// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull

/** App-owned control session. Call its lifecycle methods on the main thread. */
class StockRvfConnection(
    private val scope: CoroutineScope,
    private val isCurrentCamera: (NxCamera) -> Boolean,
    private val createClient: (NxCamera) -> StockRvfClient,
    private val log: (String) -> Unit,
) {
    enum class Phase { UNAVAILABLE, IDLE, PAIRING, CONNECTED, FAILED, CANCELLED, RECONNECT_REQUIRED }

    data class State(
        val camera: NxCamera? = null,
        val phase: Phase = Phase.UNAVAILABLE,
        val message: String = "原厂遥控未连接",
        val session: StockRvfClient.Session? = null,
    )

    private val mutableState = MutableStateFlow(State())
    val state = mutableState.asStateFlow()
    private var client: StockRvfClient? = null
    private var pairing: Job? = null
    private var heartbeat: Job? = null
    private var generation = 0L
    private var capturing: StockRvfClient? = null

    fun captureInProgress(camera: NxCamera): Boolean =
        capturing?.let { clientFor(camera) === it } == true

    /** The stock Shot stops MPEG while exposing; keep the control session owned until it returns. */
    suspend fun shot(camera: NxCamera, expected: StockRvfClient): StockRvfClient.ShotResult {
        check(clientFor(camera) === expected && state.value.phase == Phase.CONNECTED) { "原厂遥控未连接" }
        check(capturing == null) { "上一张照片仍在拍摄" }
        capturing = expected
        log("原厂 Shot 开始")
        try {
            val result = withTimeoutOrNull(60_000) { expected.shot() }
                ?: throw StockRvfClient.ProtocolException("原厂拍摄等待超时，请检查机身回放，避免重复拍摄")
            log("原厂 Shot 响应成功")
            return result
        } finally {
            if (capturing === expected) capturing = null
        }
    }

    fun attach(camera: NxCamera) {
        clearClient()
        val supported = camera.profile.geometry == OsdGeometry.NX300M
        mutableState.value = State(camera, if (supported) Phase.IDLE else Phase.UNAVAILABLE)
        if (supported) request(camera)
    }

    fun detach() {
        clearClient()
        mutableState.value = State()
    }

    /** Reuses pending/established sessions. Terminal states require an explicit user retry. */
    fun request(camera: NxCamera, explicitRetry: Boolean = false) {
        val previous = mutableState.value
        if (previous.camera !== camera || !isCurrentCamera(camera) || previous.phase == Phase.UNAVAILABLE) return
        if (previous.phase == Phase.PAIRING || previous.phase == Phase.CONNECTED) return
        if (previous.phase != Phase.IDLE && !explicitRetry) return
        clearClient()
        val token = generation
        val pending = createClient(camera)
        client = pending
        mutableState.value = State(camera, Phase.PAIRING,
            "原厂待允许：请在相机的遥控取景器中允许这台手机连接")
        pairing = scope.launch(start = CoroutineStart.LAZY) {
            try {
                val session = withTimeout(60_000) { pending.connect() }
                if (!current(camera, pending, token)) return@launch
                pairing = null
                mutableState.value = State(camera, Phase.CONNECTED, "原厂已连接", session)
                log("原厂遥控 GetInfomation 已成功")
                if ("HeartBeat" in session.supportedActions) startHeartbeat(camera, pending, token)
            } catch (_: TimeoutCancellationException) {
                fail(camera, pending, token, "原厂遥控连接超时。请在相机选择 Wi-Fi → 遥控取景器，并允许手机连接后重试")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                fail(camera, pending, token,
                    "原厂遥控连接失败：${e.message}。请确认相机选择的是遥控取景器，可手动重试")
            }
        }.also { it.start() }
    }

    /** Includes a pending client so views can isolate Mod input while the body asks for consent. */
    fun clientFor(camera: NxCamera?): StockRvfClient? =
        client.takeIf { camera != null && mutableState.value.camera === camera && isCurrentCamera(camera) }

    fun cancel(camera: NxCamera, message: String = "原厂连接已取消，可手动重试") {
        if (mutableState.value.camera !== camera) return
        clearClient()
        mutableState.value = State(camera, Phase.CANCELLED, message)
    }

    /** Closing an RVF video GET may terminate the stock AP; never keep a successful control badge. */
    fun videoClosed(camera: NxCamera, expected: StockRvfClient, message: String) {
        if (clientFor(camera) !== expected) return
        clearClient()
        mutableState.value = State(camera, Phase.RECONNECT_REQUIRED, message)
    }

    private fun current(camera: NxCamera, expected: StockRvfClient, token: Long) =
        generation == token && clientFor(camera) === expected

    private fun fail(camera: NxCamera, expected: StockRvfClient, token: Long, message: String) {
        if (!current(camera, expected, token)) return
        clearClient()
        mutableState.value = State(camera, Phase.FAILED, message)
        log(message)
    }

    private fun startHeartbeat(camera: NxCamera, expected: StockRvfClient, token: Long) {
        heartbeat = scope.launch {
            var failures = 0
            while (isActive && current(camera, expected, token)) {
                delay(3_000)
                try { expected.heartbeat(); failures = 0 }
                catch (e: CancellationException) { throw e }
                catch (e: Exception) {
                    if (!current(camera, expected, token)) return@launch
                    log("原厂遥控保活失败：${e.message}")
                    if (++failures >= 2) {
                        fail(camera, expected, token, "原厂遥控已失去响应，可手动重试")
                        return@launch
                    }
                }
            }
        }
    }

    private fun clearClient() {
        generation++
        capturing = null
        pairing?.cancel(); pairing = null
        heartbeat?.cancel(); heartbeat = null
        client?.close(); client = null
    }
}
