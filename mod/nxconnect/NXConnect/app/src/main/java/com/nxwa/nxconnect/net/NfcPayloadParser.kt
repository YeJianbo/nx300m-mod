package com.nxwa.nxconnect.net

import android.content.Intent
import android.nfc.NdefMessage
import android.nfc.NdefRecord
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.tech.Ndef
import java.net.URI
import java.net.URLDecoder
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.util.Locale

/** A Wi-Fi credential is not proof of camera identity. Verify the camera after joining. */
data class NfcWifiCredential(
    val ssid: String,
    val password: String = "",
    val authType: Int = AUTH_TYPE_WPA2_PSK,
    val isWpa3: Boolean = false
) {
    companion object {
        const val AUTH_TYPE_OPEN = 0x0001
        const val AUTH_TYPE_WPA_PSK = 0x0002
        const val AUTH_TYPE_WPA2_PSK = 0x0020
        const val AUTH_TYPE_WPA2_WPA_MIXED = 0x0022
    }
}

/** Reads standard WSC and our explicit URI format; NX300M's factory tag is not yet verified. */
object NfcPayloadParser {
    private const val MIME_WFA_WSC = "application/vnd.wfa.wsc"
    private const val ATTR_CREDENTIAL = 0x100E
    private const val ATTR_SSID = 0x1045
    private const val ATTR_AUTH_TYPE = 0x1003
    private const val ATTR_ENCR_TYPE = 0x100F
    private const val ATTR_NETWORK_KEY = 0x1027

    /** May read a tag over NFC; call on an IO thread. No tag writes are performed. */
    @Suppress("DEPRECATION")
    fun parse(intent: Intent?): NfcWifiCredential? = inspect(intent).credential

    @Suppress("DEPRECATION")
    fun inspect(intent: Intent?): NfcTagInspection {
        if (intent == null) return NfcTagInspection()
        val messages = intent.getParcelableArrayExtra(NfcAdapter.EXTRA_NDEF_MESSAGES)
            ?.mapNotNull { it as? NdefMessage }.orEmpty()
        if (messages.isNotEmpty()) return combine(messages.map { inspectMessage(it) })
        return intent.getParcelableExtra<Tag>(NfcAdapter.EXTRA_TAG)?.let { inspect(it) } ?: NfcTagInspection()
    }

    /** Reader mode and TAG_DISCOVERED do not necessarily include EXTRA_NDEF_MESSAGES. */
    fun parse(tag: Tag): NfcWifiCredential? = inspect(tag).credential

    fun inspect(tag: Tag): NfcTagInspection {
        val ndef = Ndef.get(tag) ?: return NfcTagInspection()
        return try {
            val message = ndef.cachedNdefMessage ?: run {
                ndef.connect()
                ndef.ndefMessage
            }
            message?.let { inspectMessage(it) } ?: NfcTagInspection(ndefAvailable = true)
        } finally {
            runCatching { ndef.close() }
        }
    }

    private fun combine(parts: List<NfcTagInspection>) = NfcTagInspection(
        parts.firstNotNullOfOrNull { it.credential }, parts.flatMap { it.hints }.distinct(),
        parts.flatMap { it.records }, parts.any { it.ndefAvailable })

    private fun inspectMessage(message: NdefMessage): NfcTagInspection = combine(message.records.map { record ->
        inspectRecordPayload(record.tnf.toInt(), record.type, record.payload,
            runCatching { record.toUri()?.toString() }.getOrNull())
    })

    /** Pure payload entry point for protocol checks; Android's URI decoding can be supplied separately. */
    internal fun inspectRecordPayload(tnf: Int, type: ByteArray, payload: ByteArray, uriText: String? = null): NfcTagInspection {
        val rawType = String(type, StandardCharsets.US_ASCII)
        val wsc = tnf == 2 && rawType.equals(MIME_WFA_WSC, true)
        val text = uriText ?: when {
            tnf == 1 && rawType == "T" && payload.isNotEmpty() -> {
                val status = payload[0].toInt() and 255
                val offset = 1 + (status and 63)
                if (status and 64 != 0 || offset >= payload.size) null else strictText(
                    payload.copyOfRange(offset, payload.size), if (status and 128 == 0) StandardCharsets.UTF_8 else StandardCharsets.UTF_16)
            }
            tnf == 1 && rawType == "U" && payload.firstOrNull() == 0.toByte() -> strictText(payload.copyOfRange(1, payload.size))
            tnf == 3 -> strictText(type)
            else -> null
        }
        val explicit = text?.let { uriValues(it) }
        val credential = if (wsc) parseWscPayload(payload) else text?.let { parseUriPayload(it) }
        val hints = when {
            wsc -> wscHint(payload)?.let { listOf(it) }.orEmpty()
            explicit != null -> listOf(uriHint(explicit))
            else -> emptyList()
        }
        val format = when { wsc -> "wifi-wsc"; explicit != null -> "nxconnect-uri"; else -> "unsupported" }
        // Absolute-URI record types may contain a whole private URL. Keep only the category.
        val diagnosticType = if (tnf == 3) "absolute-uri" else rawType.take(96).map { if (it.code in 32..126) it else '?' }.joinToString("")
        return NfcTagInspection(credential, hints,
            listOf(NfcRecordDiagnostic(tnf, diagnosticType, payload.size, format, credential != null)), true)
    }

    private fun strictText(bytes: ByteArray, encoding: java.nio.charset.Charset = StandardCharsets.UTF_8): String? = runCatching {
        encoding.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()
    }.getOrNull()

    private data class Attribute(val type: Int, val value: ByteArray)

    private fun attributes(payload: ByteArray): List<Attribute>? {
        val buffer = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val result = mutableListOf<Attribute>()
        while (buffer.hasRemaining()) {
            if (buffer.remaining() < 4) return null
            val type = buffer.short.toInt() and 0xffff
            val length = buffer.short.toInt() and 0xffff
            if (length > buffer.remaining()) return null
            result += Attribute(type, ByteArray(length).also { buffer.get(it) })
        }
        return result
    }

    fun parseWscPayload(payload: ByteArray?): NfcWifiCredential? {
        val outer = payload?.let { attributes(it) } ?: return null
        // Each Credential is independent. Never combine one network's SSID with another's key.
        val containers = outer.filter { it.type == ATTR_CREDENTIAL }
        val candidates = if (containers.isEmpty()) listOf(outer) else containers.mapNotNull { attributes(it.value) }
        return candidates.firstNotNullOfOrNull { fields ->
            val known = setOf(ATTR_SSID, ATTR_AUTH_TYPE, ATTR_ENCR_TYPE, ATTR_NETWORK_KEY)
            if (fields.filter { it.type in known }.groupingBy { it.type }.eachCount().values.any { it != 1 }) return@firstNotNullOfOrNull null
            fun value(type: Int): ByteArray? = fields.filter { it.type == type }.singleOrNull()?.value
            fun number(type: Int): Int? = value(type)?.takeIf { it.size == 2 }?.let {
                ByteBuffer.wrap(it).order(ByteOrder.BIG_ENDIAN).short.toInt() and 0xffff
            }
            val ssid = value(ATTR_SSID)?.let { strictText(it) } ?: return@firstNotNullOfOrNull null
            val auth = number(ATTR_AUTH_TYPE) ?: return@firstNotNullOfOrNull null
            val keyBytes = value(ATTR_NETWORK_KEY)
            val key = if (keyBytes == null) "" else strictText(keyBytes) ?: return@firstNotNullOfOrNull null
            val encryption = number(ATTR_ENCR_TYPE)
            if (fields.any { it.type == ATTR_ENCR_TYPE } && encryption == null) return@firstNotNullOfOrNull null
            if (auth == NfcWifiCredential.AUTH_TYPE_OPEN && encryption != null && encryption != 1) return@firstNotNullOfOrNull null
            if (auth != NfcWifiCredential.AUTH_TYPE_OPEN && encryption != null && encryption !in setOf(4, 8, 12)) return@firstNotNullOfOrNull null
            if (auth != NfcWifiCredential.AUTH_TYPE_OPEN && auth != NfcWifiCredential.AUTH_TYPE_WPA2_PSK &&
                auth != NfcWifiCredential.AUTH_TYPE_WPA2_WPA_MIXED) return@firstNotNullOfOrNull null
            credential(ssid, key, auth, false)
        }
    }

    /** Only nxconnect://connect is actionable; an arbitrary web URL is not a pairing tag. */
    private fun uriValues(text: String): Map<String, String>? = runCatching {
        val uri = URI(text)
        if (!uri.scheme.equals("nxconnect", true) || !uri.host.equals("connect", true) ||
            uri.userInfo != null || uri.port != -1 || uri.fragment != null || (!uri.path.isNullOrEmpty() && uri.path != "/")) return null
        val values = linkedMapOf<String, String>()
        for (part in uri.rawQuery.orEmpty().split('&').filter { it.isNotEmpty() }) {
            val key = URLDecoder.decode(part.substringBefore('='), "UTF-8")
            val value = URLDecoder.decode(part.substringAfter('=', ""), "UTF-8")
            if (values.put(key, value) != null) return null
        }
        if ("pwd" in values && "password" in values) return null
        values
    }.getOrNull()

    internal fun parseUriPayload(text: String): NfcWifiCredential? = runCatching {
        val values = uriValues(text) ?: return null
        val ssid = values["ssid"] ?: return null
        val password = values["pwd"] ?: values["password"] ?: ""
        val auth = values["auth"]?.lowercase() ?: if (password.isEmpty()) "open" else "wpa2"
        if (auth !in setOf("open", "wpa2", "wpa3")) return null
        credential(ssid, password,
            if (auth == "open") NfcWifiCredential.AUTH_TYPE_OPEN else NfcWifiCredential.AUTH_TYPE_WPA2_PSK,
            auth == "wpa3")
    }.getOrNull()

    private fun model(value: String?): String? = value?.trim()?.uppercase(Locale.ROOT)?.removePrefix("SAMSUNG ")
        ?.takeIf { it in setOf("NX300M", "NX300", "NX500", "NX1") }

    private fun mac(value: String?): String? {
        val compact = value?.replace(":", "")?.replace("-", "") ?: return null
        if (!compact.matches(Regex("[0-9A-Fa-f]{12}")) || compact.all { it == '0' } || compact.all { it == 'f' || it == 'F' } ||
            compact.take(2).toInt(16) and 1 != 0) return null
        return compact.uppercase(Locale.ROOT).chunked(2).joinToString(":")
    }

    private fun uriHint(values: Map<String, String>): NfcCameraHint {
        val address = mac(values["mac"])
        val suffix = values["mac_suffix"]?.takeIf { it.matches(Regex("[0-9A-Fa-f]{6}")) }?.uppercase(Locale.ROOT)
        return NfcCameraHint(model(values["model"]), address, address?.replace(":", "")?.takeLast(6) ?: suffix,
            "nxconnect://connect", "nxconnect-uri")
    }

    private fun wscHint(payload: ByteArray): NfcCameraHint? {
        val outer = attributes(payload) ?: return null
        val containers = outer.filter { it.type == ATTR_CREDENTIAL }
        val fields = outer + containers.singleOrNull()?.let { attributes(it.value) }.orEmpty()
        fun field(type: Int) = fields.filter { it.type == type }.singleOrNull()?.value
        val model = model(field(0x1023)?.let { strictText(it) })
            ?: model(field(0x1024)?.let { strictText(it) })
        val address = field(0x1020)?.takeIf { it.size == 6 }?.joinToString("") { "%02x".format(it.toInt() and 255) }?.let { mac(it) }
        if (model == null && address == null) return null
        return NfcCameraHint(model, address, address?.replace(":", "")?.takeLast(6), source = "wifi-wsc")
    }

    private fun credential(ssid: String, key: String, auth: Int, wpa3: Boolean): NfcWifiCredential? {
        if (ssid.toByteArray(StandardCharsets.UTF_8).size !in 1..32 || '\u0000' in ssid || '\u0000' in key) return null
        if (auth == NfcWifiCredential.AUTH_TYPE_OPEN) {
            if (key.isNotEmpty()) return null
        } else if (key.length !in 8..63 || !key.all { it.code in 32..126 }) return null
        return NfcWifiCredential(ssid, key, auth, wpa3)
    }
}
