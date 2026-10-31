// SPDX-License-Identifier: AGPL-3.0-or-later
package com.nxwa.nxconnect.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.w3c.dom.Document
import org.w3c.dom.Element
import org.xml.sax.InputSource
import org.xml.sax.SAXException
import java.io.ByteArrayInputStream
import java.io.Closeable
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Original NX300 RVF session; independent of the mod's screen and gallery HTTP client. */
class StockRvfClient(
    host: String,
    clientIdentity: String,
    descriptorPort: Int = 7676,
    private val pairingTimeoutMs: Int = 60_000,
    private val requestTimeoutMs: Int = 8_000,
    private val connectTimeoutMs: Int = 3_000,
    private val binder: HttpBinder = HttpBinder.None,
) : Closeable {
    private companion object {
        // HttpURLConnection may hold internal locks during a read. Neither cancellation nor
        // UI close() must wait for those locks or for a blocked worker to finish unwinding.
        val requestScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val cleanupScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        fun disconnectLater(connection: HttpURLConnection) {
            cleanupScope.launch { runCatching { connection.disconnect() } }
        }
    }

    data class Session(
        val serviceType: String,
        val controlUrl: String,
        val scpdUrl: String,
        val highQualityUrl: String?,
        val lowQualityUrl: String?,
        val supportedActions: Set<String>,
    )

    data class ShotResult(val photoUrl: String?, val availableShots: Int?, val raw: String)

    class ProtocolException(message: String, val httpStatus: Int? = null, val faultCode: String? = null) : IOException(message)

    private data class Service(val type: String, val control: URI, val scpd: URI)
    private data class Response(val status: Int, val body: ByteArray)
    private val descriptor = URI("http", null, host.trim().removeSurrounding("[", "]"), descriptorPort, "/smp_2_", null, null)
    private val userAgent: String
    private val mutex = Mutex()
    private val heartbeatMutex = Mutex()
    private val pendingLock = Any()
    private val pending = mutableMapOf<HttpURLConnection, () -> Unit>()
    private var closed = false
    @Volatile private var session: Session? = null
    private var arguments: Map<String, List<String>> = emptyMap()

    init {
        require(descriptor.host != null && descriptorPort in 1..65535) { "无效的相机地址或原厂描述端口" }
        require(pairingTimeoutMs > 0 && requestTimeoutMs > 0 && connectTimeoutMs > 0)
        val identity = clientIdentity.removePrefix("SEC_RVF_")
        require(identity.matches(Regex("[A-Za-z0-9:_-]{1,64}"))) { "无效的 RVF 客户端身份" }
        userAgent = "SEC_RVF_$identity"
    }

    suspend fun connect(): Session = mutex.withLock {
        ensureOpen()
        session?.let { return@withLock it }
        // This GET may wait for the camera's physical Allow/Reject confirmation.
        val description = getXml(descriptor, pairingTimeoutMs)
        val base = description.documentElement.childText("URLBase")
            ?.let { cameraUrl(descriptor, it) } ?: descriptor
        val service = description.documentElement.descendants("service").firstNotNullOfOrNull { element ->
            val type = element.childText("serviceType") ?: return@firstNotNullOfOrNull null
            if (!type.matches(Regex("urn:[A-Za-z0-9_.:-]+:service:ContentDirectory:[1-9][0-9]*"))) {
                return@firstNotNullOfOrNull null
            }
            Service(type,
                cameraUrl(base, element.requiredChild("controlURL")),
                cameraUrl(base, element.requiredChild("SCPDURL")))
        } ?: throw ProtocolException("原厂设备描述没有 ContentDirectory 服务")
        val scpd = getXml(service.scpd, requestTimeoutMs)
        val actionList = scpd.documentElement.child("actionList")
            ?: throw ProtocolException("原厂服务没有动作列表")
        val declared = actionList.children("action").associate { action ->
            action.requiredChild("name") to action.child("argumentList")?.children("argument")
                .orEmpty().filter { it.childText("direction").equals("in", true) }
                .map { it.requiredChild("name") }
        }
        if ("GetInfomation" !in declared) throw ProtocolException("原厂服务未声明 GetInfomation")
        val info = soap(service, declared, "GetInfomation", mapOf("GPSINFO" to "UNKNOWN"))
        val established = Session(service.type, service.control.toString(), service.scpd.toString(),
            info.second.documentElement.firstText("QualityHighUrl")?.let { cameraUrl(service.control, it).toString() },
            info.second.documentElement.firstText("QualityLowUrl")?.let { cameraUrl(service.control, it).toString() },
            declared.keys.toSet())
        ensureOpen()
        arguments = declared
        session = established
        established
    }

    // Shot can wait for a long exposure. Keep-alive must not queue behind that request.
    suspend fun heartbeat(durationSeconds: Int = 30) = heartbeatMutex.withLock {
        require(durationSeconds in 1..3600)
        action("HeartBeat", mapOf("Duration" to durationSeconds.toString()))
        Unit
    }

    suspend fun focus(): String? = mutex.withLock {
        action("MULTIAF").second.documentElement.firstText("AFSTATUS")
    }

    suspend fun shot(): ShotResult = mutex.withLock {
        val (raw, response) = action("Shot", timeoutMs = 60_000)
        val current = session ?: throw ProtocolException("原厂遥控会话已结束")
        val photo = response.documentElement.firstText("AFSHOTRESULT")?.takeIf {
            it.startsWith("http:", true) || it.startsWith("/")
        }?.let { cameraUrl(URI(current.controlUrl), it).toString() }
        ShotResult(photo, response.documentElement.firstText("AVAILSHOTS")?.toIntOrNull(), raw)
    }

    /** Cancels this instance permanently; create a new instance to retry pairing. */
    fun cancel() = close()

    override fun close() {
        val requests = synchronized(pendingLock) {
            if (closed) return
            closed = true
            session = null
            pending.values.toList()
        }
        requests.forEach { it() }
    }

    private fun ensureOpen() {
        synchronized(pendingLock) { if (closed) throw CancellationException("原厂遥控连接已取消") }
    }

    private suspend fun action(name: String, values: Map<String, String> = emptyMap(),
                               timeoutMs: Int = requestTimeoutMs): Pair<String, Document> {
        ensureOpen()
        val current = session ?: throw ProtocolException("请先建立原厂遥控会话")
        return soap(Service(current.serviceType, URI(current.controlUrl), URI(current.scpdUrl)), arguments, name, values, timeoutMs)
    }

    private suspend fun soap(service: Service, declared: Map<String, List<String>>, name: String,
                             values: Map<String, String>, timeoutMs: Int = requestTimeoutMs): Pair<String, Document> {
        val names = declared[name] ?: throw UnsupportedOperationException("相机未声明 $name")
        // Argument names originate in the SCPD, but still must be valid XML names.
        val args = names.joinToString("") { arg ->
            if (!arg.matches(Regex("[A-Za-z_][A-Za-z0-9_.-]*"))) throw ProtocolException("原厂参数名无效")
            val value = values[arg] ?: throw ProtocolException("$name 需要未支持的参数 $arg")
            "<$arg>${escape(value)}</$arg>"
        }
        val body = """<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:$name xmlns:u="${escape(service.type)}">$args</u:$name></s:Body></s:Envelope>"""
        val response = request(service.control, timeoutMs, "POST", body.toByteArray(Charsets.UTF_8),
            mapOf("SOAPACTION" to "\"${service.type}#$name\"", "Content-Type" to "text/xml; charset=\"utf-8\""))
        val xml = responseXml(response)
        if (xml.documentElement.descendants("${name}Response").isEmpty()) {
            throw ProtocolException("原厂 $name 响应缺少 ${name}Response", response.status)
        }
        return response.body.toString(Charsets.UTF_8) to xml
    }

    private suspend fun getXml(url: URI, timeoutMs: Int): Document = responseXml(request(url, timeoutMs))

    private fun responseXml(response: Response): Document {
        val doc = try { parseXml(response.body) } catch (e: Exception) {
            throw ProtocolException("原厂响应不是有效 XML（HTTP ${response.status}）：${e.message}", response.status)
        }
        val fault = doc.documentElement.descendants("Fault").firstOrNull()
        if (fault != null) {
            val code = fault.firstText("errorCode") ?: fault.firstText("faultcode")
            val message = fault.firstText("errorDescription") ?: fault.firstText("faultstring") ?: "未知 SOAP 错误"
            throw ProtocolException("原厂遥控拒绝请求${code?.let { "（$it）" }.orEmpty()}：$message", response.status, code)
        }
        if (response.status !in 200..299) throw ProtocolException("原厂遥控 HTTP ${response.status}", response.status)
        return doc
    }

    private suspend fun request(url: URI, timeoutMs: Int, method: String = "GET", body: ByteArray? = null,
                                headers: Map<String, String> = emptyMap()): Response = suspendCancellableCoroutine { continuation ->
        val connection = binder.openConnection(url.toURL())
        val cancelRequest = {
            continuation.cancel(CancellationException("原厂遥控连接已取消"))
            Unit
        }
        val registered = synchronized(pendingLock) {
            if (closed) false else { pending[connection] = cancelRequest; true }
        }
        if (!registered) {
            continuation.cancel(CancellationException("原厂遥控连接已取消"))
            disconnectLater(connection)
            return@suspendCancellableCoroutine
        }
        val worker = requestScope.launch(start = CoroutineStart.LAZY) {
            try {
                currentCoroutineContext().ensureActive()
                ensureOpen()
                connection.connectTimeout = connectTimeoutMs
                connection.readTimeout = timeoutMs
                connection.instanceFollowRedirects = false
                connection.useCaches = false
                connection.requestMethod = method
                connection.setRequestProperty("User-Agent", userAgent)
                connection.setRequestProperty("Access-Method", "manual")
                connection.setRequestProperty("Connection", "close")
                headers.forEach { (key, value) -> connection.setRequestProperty(key, value) }
                currentCoroutineContext().ensureActive()
                if (body != null) {
                    connection.doOutput = true
                    connection.setFixedLengthStreamingMode(body.size)
                    connection.outputStream.use { it.write(body) }
                }
                val status = connection.responseCode
                if (status in 300..399) throw ProtocolException("原厂服务重定向被拒绝（HTTP $status）", status)
                val stream = if (status >= 400) connection.errorStream else connection.inputStream
                val bytes = stream?.use {
                    val output = java.io.ByteArrayOutputStream()
                    val buffer = ByteArray(8192)
                    while (true) {
                        val count = it.read(buffer)
                        if (count < 0) break
                        if (output.size() + count > 1_048_576) throw ProtocolException("原厂 XML 响应过大", status)
                        output.write(buffer, 0, count)
                    }
                    output.toByteArray()
                } ?: ByteArray(0)
                if (continuation.isActive) continuation.resume(Response(status, bytes))
            } catch (e: Exception) {
                if (continuation.isActive) continuation.resumeWithException(e)
            } finally {
                synchronized(pendingLock) { pending.remove(connection) }
                connection.disconnect()
            }
        }
        continuation.invokeOnCancellation {
            worker.cancel()
            synchronized(pendingLock) { pending.remove(connection) }
            disconnectLater(connection)
        }
        worker.start()
    }

    private fun cameraUrl(base: URI, value: String): URI {
        val result = try { base.resolve(value.trim()) } catch (e: IllegalArgumentException) {
            throw ProtocolException("原厂服务 URL 无效")
        }
        if (!result.scheme.equals("http", true) || result.host == null || result.userInfo != null ||
            result.fragment != null || result.port !in -1..65535 || result.port == 0 ||
            !result.host.removeSurrounding("[", "]").equals(descriptor.host.removeSurrounding("[", "]"), true)) {
            throw ProtocolException("拒绝非本相机的原厂服务 URL")
        }
        return result
    }

    private fun parseXml(bytes: ByteArray): Document {
        // Stock XML is UTF-8. Reject alternate null-containing encodings and all DTDs before parsing;
        // Android/JVM parsers differ in which optional SAX hardening flags they support.
        val text = bytes.toString(Charsets.UTF_8)
        if ('\u0000' in text || Regex("<!\\s*(DOCTYPE|ENTITY)", RegexOption.IGNORE_CASE).containsMatchIn(text)) {
            throw ProtocolException("原厂 XML 包含不允许的 DTD 或编码")
        }
        val factory = DocumentBuilderFactory.newInstance().apply {
            isNamespaceAware = true
            isExpandEntityReferences = false
            runCatching { isXIncludeAware = false }
            runCatching { setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true) }
            runCatching { setFeature("http://apache.org/xml/features/disallow-doctype-decl", true) }
            runCatching { setFeature("http://xml.org/sax/features/external-general-entities", false) }
            runCatching { setFeature("http://xml.org/sax/features/external-parameter-entities", false) }
        }
        return factory.newDocumentBuilder().apply {
            setEntityResolver { _, _ -> throw SAXException("禁止外部 XML 实体") }
        }.parse(InputSource(ByteArrayInputStream(bytes)))
    }

    private fun Element.children(name: String): List<Element> = (0 until childNodes.length)
        .mapNotNull { childNodes.item(it) as? Element }.filter { (it.localName ?: it.nodeName) == name }
    private fun Element.child(name: String): Element? = children(name).firstOrNull()
    private fun Element.childText(name: String): String? = child(name)?.textContent?.trim()?.takeIf { it.isNotEmpty() }
    private fun Element.requiredChild(name: String): String = childText(name) ?: throw ProtocolException("原厂 XML 缺少 $name")
    private fun Element.descendants(name: String): List<Element> = getElementsByTagNameNS("*", name)
        .let { nodes -> (0 until nodes.length).mapNotNull { nodes.item(it) as? Element } }
    private fun Element.firstText(name: String): String? = descendants(name).firstOrNull()?.textContent?.trim()?.takeIf { it.isNotEmpty() }
    private fun escape(value: String) = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\"", "&quot;").replace("'", "&apos;")
}
