// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** DHCP can complete before routes and the camera's HTTP service are ready. */
internal suspend fun <T : Any> identifyCameraService(
    isCurrent: () -> Boolean,
    candidates: () -> List<String>,
    probe: suspend (String) -> T?,
    timeoutMillis: Long = 20_000,
    retryDelayMillis: Long = 750,
): T? = withTimeoutOrNull(timeoutMillis) {
    while (isCurrent()) {
        currentCoroutineContext().ensureActive()
        // Refresh the snapshot on every round; a gateway may arrive after IPv4.
        val addresses = candidates().distinct()
        val found = coroutineScope {
            val results = Channel<T?>(addresses.size.coerceAtLeast(1))
            val jobs = addresses.map { address ->
                launch {
                    val result = try {
                        if (isCurrent()) probe(address) else null
                    } catch (e: CancellationException) {
                        throw e
                    } catch (_: Exception) {
                        null
                    }
                    results.send(result)
                }
            }
            try {
                repeat(jobs.size) {
                    val result = results.receive()
                    if (!isCurrent()) return@coroutineScope null
                    if (result != null) return@coroutineScope result
                }
                null
            } finally {
                jobs.forEach { it.cancel() }
                results.cancel()
            }
        }
        currentCoroutineContext().ensureActive()
        if (!isCurrent()) return@withTimeoutOrNull null
        if (found != null) return@withTimeoutOrNull found
        delay(retryDelayMillis)
    }
    null
}
