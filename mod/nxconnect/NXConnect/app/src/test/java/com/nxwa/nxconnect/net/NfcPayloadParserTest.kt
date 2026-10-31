package com.nxwa.nxconnect.net

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class NfcPayloadParserTest {
    private fun attribute(type: Int, value: ByteArray): ByteArray =
        ByteBuffer.allocate(4 + value.size).order(ByteOrder.BIG_ENDIAN)
            .putShort(type.toShort()).putShort(value.size.toShort()).put(value).array()

    private fun number(value: Int) = ByteBuffer.allocate(2).putShort(value.toShort()).array()
    private fun ssid(value: String) = attribute(0x1045, value.toByteArray(Charsets.UTF_8))
    private fun key(value: String) = attribute(0x1027, value.toByteArray(Charsets.UTF_8))
    private fun auth(value: Int = NfcWifiCredential.AUTH_TYPE_WPA2_PSK) = attribute(0x1003, number(value))
    private fun credential(value: ByteArray) = attribute(0x100e, value)

    @Test fun wscPreservesSsidAndPasswordWhitespace() {
        val parsed = NfcPayloadParser.parseWscPayload(credential(ssid(" NX300M ") + key(" pass1234 ") + auth()))
        assertNotNull(parsed)
        assertEquals(" NX300M ", parsed!!.ssid)
        assertEquals(" pass1234 ", parsed.password)
    }

    @Test fun wscDoesNotCombineIncompleteCredentials() {
        val payload = credential(ssid("first") + auth()) + credential(key("secondkey") + auth())
        assertNull(NfcPayloadParser.parseWscPayload(payload))
    }

    @Test fun wscKeepsFirstCompleteCredentialTogether() {
        val payload = credential(ssid("first") + key("firstkey") + auth()) +
            credential(ssid("second") + key("secondkey") + auth())
        val parsed = NfcPayloadParser.parseWscPayload(payload)
        assertEquals("first", parsed!!.ssid)
        assertEquals("firstkey", parsed.password)
    }

    @Test fun malformedOrUnsupportedWscIsNotAnOpenNetwork() {
        assertNull(NfcPayloadParser.parseWscPayload(ssid("AP_NX300M") + byteArrayOf(0x10, 0x27, 0, 8, 1)))
        assertNull(NfcPayloadParser.parseWscPayload(ssid("AP_NX300M")))
        assertNull(NfcPayloadParser.parseWscPayload(ssid("AP_NX300M") + key("12345678") + auth(0x40)))
        assertNull(NfcPayloadParser.parseWscPayload(ssid("AP_NX300M") + key("12345678") + auth(NfcWifiCredential.AUTH_TYPE_OPEN)))
    }

    @Test fun ordinaryUrlsAndTextsCannotTriggerPairing() {
        assertNull(NfcPayloadParser.parseUriPayload("https://example.com/?ssid=AP_NX300M&pwd=12345678"))
        assertNull(NfcPayloadParser.parseUriPayload("AP_NX300M;12345678"))
        assertNull(NfcPayloadParser.parseUriPayload("nxconnect://other?ssid=AP_NX300M"))
        assertNull(NfcPayloadParser.parseUriPayload("nxconnect://connect?ssid=one&ssid=two"))
    }

    @Test fun explicitUriPreservesCredentialsAndAuth() {
        val parsed = NfcPayloadParser.parseUriPayload("nxconnect://connect?ssid=NX%20Camera&pwd=%20pass1234%20&auth=wpa2")
        assertEquals("NX Camera", parsed!!.ssid)
        assertEquals(" pass1234 ", parsed.password)
        assertFalse(parsed.isWpa3)
        assertEquals(NfcWifiCredential.AUTH_TYPE_OPEN,
            NfcPayloadParser.parseUriPayload("nxconnect://connect?ssid=NX&auth=open")!!.authType)
        assertNull(NfcPayloadParser.parseUriPayload("nxconnect://connect?ssid=NX&auth=wpa2"))
    }

    @Test fun malformedUtf8AndAmbiguousCredentialUrisAreRejected() {
        assertNull(NfcPayloadParser.parseWscPayload(credential(
            attribute(0x1045, byteArrayOf(0xc3.toByte(), 0x28)) + key("12345678") + auth())))
        assertNull(NfcPayloadParser.parseUriPayload("nxconnect://connect?ssid=NX&pwd=12345678&password=87654321"))
        assertNull(NfcPayloadParser.parseUriPayload("nxconnect://connect?ssid=NX&auth=open#other"))
    }

    @Test fun standardWscModelAndMacAreHintsWithoutInventingCredentials() {
        val payload = attribute(0x1023, "SAMSUNG NX300M".toByteArray()) +
            attribute(0x1020, byteArrayOf(0x00, 0x11, 0x22, 0x33, 0x44, 0x55))
        val result = NfcPayloadParser.inspectRecordPayload(2, "application/vnd.wfa.wsc".toByteArray(), payload)
        assertNull(result.credential)
        assertTrue(result.ndefAvailable)
        assertEquals("NX300M", result.hints.single().model)
        assertEquals("00:11:22:33:44:55", result.hints.single().macAddress)
        assertEquals("334455", result.hints.single().macSuffix)
        assertNull(result.hints.single().pairingUri)
        assertFalse(result.records.single().credentialAvailable)
    }

    @Test fun explicitNxconnectHintRetainsOnlyPairingEntryNotQuery() {
        val uri = "nxconnect://connect?model=NX300M&mac_suffix=A1B2C3"
        val result = NfcPayloadParser.inspectRecordPayload(1, byteArrayOf('U'.code.toByte()), byteArrayOf(0) + uri.toByteArray())
        assertNull(result.credential)
        assertEquals("NX300M", result.hints.single().model)
        assertEquals("A1B2C3", result.hints.single().macSuffix)
        assertEquals("nxconnect://connect", result.hints.single().pairingUri)
        assertEquals("nxconnect-uri", result.records.single().format)
    }

    @Test fun unknownTagDiagnosticsDoNotPromoteUrlsOrTextToCameraHints() {
        val uri = "https://example.com/?model=NX300M&mac=001122334455&pwd=privatepass"
        val absolute = NfcPayloadParser.inspectRecordPayload(3, uri.toByteArray(), byteArrayOf())
        assertNull(absolute.credential)
        assertTrue(absolute.hints.isEmpty())
        assertEquals("absolute-uri", absolute.records.single().type)
        assertFalse(absolute.records.toString().contains("privatepass"))
        val text = byteArrayOf(2) + "enNX300M MAC 001122334455".toByteArray()
        val ordinary = NfcPayloadParser.inspectRecordPayload(1, byteArrayOf('T'.code.toByte()), text)
        assertNull(ordinary.credential)
        assertTrue(ordinary.hints.isEmpty())
        val network = NfcPayloadParser.inspectRecordPayload(2, "application/vnd.wfa.wsc".toByteArray(),
            credential(ssid("private-network") + key("privatepass") + auth()))
        assertNotNull(network.credential)
        assertFalse(network.records.toString().contains("private-network"))
        assertFalse(network.records.toString().contains("privatepass"))
    }
}
