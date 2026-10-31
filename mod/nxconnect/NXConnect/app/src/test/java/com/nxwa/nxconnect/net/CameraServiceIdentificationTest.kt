// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test

class CameraServiceIdentificationTest {
    @Test
    fun `service starts after first refusal and success cancels other probes`() = runBlocking {
        withTimeout(2_000) {
            var round = 0
            var otherCancelled = false
            val otherStarted = CompletableDeferred<Unit>()
            val result = identifyCameraService(
                isCurrent = { true },
                candidates = { round++; listOf("camera", "other") },
                probe = { address ->
                    when {
                        round == 1 -> null
                        address == "camera" -> { otherStarted.await(); "NX300M" }
                        else -> try {
                            otherStarted.complete(Unit)
                            awaitCancellation()
                        } finally { otherCancelled = true }
                    }
                },
                retryDelayMillis = 1,
            )
            assertEquals("NX300M", result)
            assertEquals(2, round)
            assertTrue("The successful round must cancel remaining probes", otherCancelled)
        }
    }

    @Test
    fun `gateway arriving after DHCP is included in a refreshed round`() = runBlocking {
        var gateway = "192.168.107.1"
        var snapshots = 0
        val probed = mutableListOf<String>()
        val result = identifyCameraService(
            isCurrent = { true },
            candidates = { snapshots++; listOf(gateway) },
            probe = { address ->
                probed += address
                if (address == "192.168.107.254") "NX300M"
                else { gateway = "192.168.107.254"; null }
            },
            timeoutMillis = 2_000,
            retryDelayMillis = 1,
        )
        assertEquals("NX300M", result)
        assertEquals(2, snapshots)
        assertEquals(listOf("192.168.107.1", "192.168.107.254"), probed)
    }

    @Test
    fun `owner cancellation stops probing without a completion`() = runBlocking {
        withTimeout(2_000) {
            val started = CompletableDeferred<Unit>()
            var probeCancelled = false
            var completed = false
            val attempt = async {
                identifyCameraService<String>(
                    isCurrent = { true },
                    candidates = { listOf("camera") },
                    probe = {
                        try { started.complete(Unit); awaitCancellation() }
                        finally { probeCancelled = true }
                    },
                )
                completed = true
            }
            started.await()
            attempt.cancelAndJoin()
            assertTrue(probeCancelled)
            assertFalse(completed)
        }
    }

    @Test
    fun `connection lost while probing rejects a late successful response`() = runBlocking {
        var current = true
        var snapshots = 0
        val result = identifyCameraService(
            isCurrent = { current },
            candidates = { snapshots++; listOf("camera") },
            probe = { current = false; "NX300M" },
            timeoutMillis = 2_000,
            retryDelayMillis = 1,
        )
        assertNull(result)
        assertEquals(1, snapshots)
    }
}
