package com.nxwa.nxconnect.net

/** Tag claims help the user match a device; they are never verified camera identity or WLAN credentials. */
data class NfcCameraHint(
    val model: String? = null,
    val macAddress: String? = null,
    val macSuffix: String? = null,
    val pairingUri: String? = null,
    val source: String,
)

/** Metadata only: no raw payload, password, SSID, tag UID or URI query is retained here. */
data class NfcRecordDiagnostic(
    val tnf: Int,
    val type: String,
    val payloadBytes: Int,
    val format: String,
    val credentialAvailable: Boolean,
)

data class NfcTagInspection(
    val credential: NfcWifiCredential? = null,
    val hints: List<NfcCameraHint> = emptyList(),
    val records: List<NfcRecordDiagnostic> = emptyList(),
    val ndefAvailable: Boolean = false,
)
