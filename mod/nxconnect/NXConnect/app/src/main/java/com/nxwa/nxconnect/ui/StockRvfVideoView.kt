package com.nxwa.nxconnect.ui

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.media.MediaCodec
import android.media.MediaFormat
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.AttributeSet
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.widget.FrameLayout
import com.nxwa.nxconnect.net.HttpBinder
import com.nxwa.nxconnect.net.RiffVideoReader
import java.io.BufferedInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.ByteBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/** RVF AVI playback only. It never guesses a stream URL or overlays camera-screen coordinates. */
class StockRvfVideoView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : FrameLayout(context, attrs), SurfaceHolder.Callback {
    private val main = Handler(Looper.getMainLooper())
    private val generation = AtomicLong()
    private val sessionLock = Any()
    // Only teardown/start barriers are serialized here. The AVI read loop owns a separate worker.
    // No socket close, codec release, or wait is allowed on the UI thread.
    private val transitions = ThreadPoolExecutor(0, 1, 10L, TimeUnit.SECONDS,
        LinkedBlockingQueue<Runnable>()) { task ->
        Thread(task, "nx-rvf-transitions").apply { isDaemon = true }
    }
    private val surfaceView = SurfaceView(context)
    @Volatile private var current: Session? = null
    private data class Request(val ticket: Long, val url: String, val onStatus: (String) -> Unit,
        val onClosed: ((String) -> Unit)?)
    private var requested: Request? = null // Main thread, including surface callbacks.
    private var videoWidth = 0
    private var videoHeight = 0

    /** Optional same network binder used by the already validated RVF client. Set before start. */
    var httpBinder: HttpBinder = HttpBinder.None

    /** Unexpected stream/surface closure, captured per request so old workers cannot close a replacement. */
    var onStreamClosed: ((String) -> Unit)? = null

    /** Includes connection/header/decoder setup; status reports separately confirm the first frame. */
    val isRunning: Boolean get() = current?.let { !it.cancelled && !it.finished } == true

    /** Stock Shot pauses MPEG. Allow its bounded wait without closing/reopening the video GET. */
    fun setCaptureInProgress(active: Boolean) {
        current?.captureDeadlineMs = SystemClock.elapsedRealtime() + if (active) 68_000 else 8_000
    }

    init {
        setBackgroundColor(Color.BLACK)
        addView(surfaceView, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT, Gravity.CENTER))
        surfaceView.holder.addCallback(this)
        contentDescription = "相机原厂实时取景"
    }

    /** URL must come from StockRvfClient's same-host validated GetInfomation response. */
    fun start(url: String, onStatus: (String) -> Unit) {
        val ticket = generation.incrementAndGet()
        val onClosed = onStreamClosed
        closeCurrent()
        main.post {
            if (generation.get() != ticket) return@post
            requested = Request(ticket, url, onStatus, onClosed)
            videoWidth = 0
            videoHeight = 0
            fitSurface()
            if (surfaceView.holder.surface.isValid) launch(requested!!)
            else onStatus("等待取景显示区域…")
        }
    }

    /** Cancels immediately; HTTP/decoder teardown runs off the UI thread before a replacement starts. */
    fun stop() {
        val ticket = generation.incrementAndGet()
        closeCurrent()
        main.post { if (generation.get() == ticket) requested = null }
    }

    override fun surfaceCreated(holder: SurfaceHolder) {
        requested?.takeIf { it.ticket == generation.get() }?.let(::launch)
    }

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) = Unit

    override fun surfaceDestroyed(holder: SurfaceHolder) {
        requested?.let { endRequest(it, "原厂取景显示区域已关闭") } ?: stop()
    }

    override fun onDetachedFromWindow() {
        requested?.let { endRequest(it, "原厂取景显示区域已关闭") } ?: stop()
        super.onDetachedFromWindow()
    }

    private fun endRequest(request: Request, reason: String) {
        if (generation.get() != request.ticket || requested?.ticket != request.ticket) return
        stop()
        request.onStatus(reason)
        request.onClosed?.invoke(reason)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        fitSurface()
    }

    private fun fitSurface() {
        if (width <= 0 || height <= 0) return
        val size = if (videoWidth > 0 && videoHeight > 0) {
            val scale = minOf(width.toFloat() / videoWidth, height.toFloat() / videoHeight)
            (videoWidth * scale).toInt().coerceAtLeast(1) to (videoHeight * scale).toInt().coerceAtLeast(1)
        } else width to height
        val old = surfaceView.layoutParams as LayoutParams
        if (old.width != size.first || old.height != size.second) {
            surfaceView.layoutParams = LayoutParams(size.first, size.second, Gravity.CENTER)
        }
    }

    private fun setVideoSize(session: Session, w: Int, h: Int) {
        if (w !in 1..4096 || h !in 1..4096) throw IOException("不支持的取景尺寸 $w×$h")
        main.post {
            if (!session.isCurrent()) return@post
            videoWidth = w
            videoHeight = h
            fitSurface()
        }
    }

    private fun launch(request: Request) {
        val session = Session(request, httpBinder)
        if (request.ticket != generation.get() || current != null) return
        request.onStatus("正在等待取景资源…")
        synchronized(sessionLock) {
            if (request.ticket != generation.get() || current != null) return
            current = session
            main.postDelayed({ session.checkReadDeadline() }, 1_000)
            transitions.execute {
                if (session.isCurrent()) {
                    Thread({ session.run() }, "nx-stock-rvf-${request.ticket}").apply { isDaemon = true }.start()
                } else session.notStarted()
            }
        }
    }

    private fun closeCurrent() {
        synchronized(sessionLock) {
            val old = current ?: return
            current = null
            old.cancel()
            // Queue under the same small state lock as launch, so a replacement can never
            // pass the old session's socket/codec/rendering cleanup barrier.
            transitions.execute { old.finishBeforeReplacement() }
        }
    }

    private inner class Session(private val request: Request, private val binder: HttpBinder) {
        @Volatile var captureDeadlineMs = 0L
        @Volatile private var lastReadMs = SystemClock.elapsedRealtime()

        fun checkReadDeadline() {
            if (!isCurrent()) return
            if (SystemClock.elapsedRealtime() >= maxOf(lastReadMs + 8_000, captureDeadlineMs)) {
                endRequest(request, "原厂取景未就绪：等待画面超时")
            } else {
                main.postDelayed({ checkReadDeadline() }, 1_000)
            }
        }

        private fun readWithProgress(read: () -> Int): Int {
            if (!isCurrent()) throw IOException("原厂取景已停止")
            return read().also { if (it >= 0) lastReadMs = SystemClock.elapsedRealtime() }
        }
        @Volatile var cancelled = false
            private set
        @Volatile var finished = false
            private set
        private val ioLock = Any()
        private val codecLock = Any()
        private val done = CountDownLatch(1)
        private var connection: HttpURLConnection? = null
        private var input: InputStream? = null
        private var decoder: MediaCodec? = null
        private var firstFrame = false
        private val bufferInfo = MediaCodec.BufferInfo()

        fun isCurrent(): Boolean = !cancelled && generation.get() == request.ticket && current === this

        private fun status(text: String) {
            main.post { if (isCurrent()) request.onStatus(text) }
        }

        fun run() {
            var closedReason: String? = null
            try {
                if (!isCurrent()) return
                status("正在连接原厂取景…")
                val url = URL(request.url)
                if (url.protocol !in listOf("http", "https") || url.host.isBlank() || url.userInfo != null) {
                    throw IOException("无效的原厂取景地址")
                }
                val conn = binder.openConnection(url)
                conn.connectTimeout = 6000
                // Android HTTP bodies may become unusable after a socket read timeout.
                // Keep that timeout beyond Shot; the independent watchdog enforces idle 8s.
                conn.readTimeout = 68_000
                conn.instanceFollowRedirects = false // Never escape the RVF client's host validation.
                conn.useCaches = false
                conn.setRequestProperty("Accept", "video/x-msvideo, video/avi, */*")
                conn.setRequestProperty("Accept-Encoding", "identity")
                conn.setRequestProperty("Connection", "close")
                synchronized(ioLock) {
                    if (!isCurrent()) { conn.disconnect(); return }
                    connection = conn
                }
                val code = conn.responseCode
                // NX cameras use 206 for a continuous stream without a requested Range.
                if (code !in 200..299) throw IOException("原厂取景响应 HTTP $code")
                val source = conn.inputStream
                lastReadMs = SystemClock.elapsedRealtime()
                val body = BufferedInputStream(object : InputStream() {
                    override fun read(): Int = readWithProgress { source.read() }
                    override fun read(bytes: ByteArray, offset: Int, length: Int): Int =
                        readWithProgress { source.read(bytes, offset, length) }
                    override fun close() = source.close()
                }, 32 * 1024)
                synchronized(ioLock) {
                    if (!isCurrent()) { body.close(); return }
                    input = body
                }
                val reader = RiffVideoReader(body)
                val format = reader.readHeader()
                if (!isCurrent()) return
                setVideoSize(this, format.width, format.height)
                status("已收到 ${format.codec} ${format.width}×${format.height}，等待解码画面…")
                if (format.codec == RiffVideoReader.Codec.H264) createDecoder(format)
                val started = System.nanoTime()
                while (isCurrent()) {
                    val packet = reader.nextFrame() ?: throw IOException("原厂取景流已结束")
                    if (!isCurrent()) return
                    when (format.codec) {
                        RiffVideoReader.Codec.H264 -> decodeH264(packet, (System.nanoTime() - started) / 1000)
                        RiffVideoReader.Codec.MJPG -> decodeJpeg(packet)
                    }
                }
            } catch (e: Exception) {
                if (isCurrent()) closedReason = "原厂取景未就绪：${e.message ?: e.javaClass.simpleName}"
            } finally {
                try { releaseResources() } finally {
                    finished = true
                    done.countDown()
                    closedReason?.let { reason -> main.post { endRequest(request, reason) } }
                }
            }
        }

        private fun createDecoder(format: RiffVideoReader.Format) = synchronized(codecLock) {
            if (!isCurrent()) return@synchronized
            val media = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, format.width, format.height)
            media.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, RiffVideoReader.MAX_FRAME_BYTES)
            format.codecSpecificData.forEachIndexed { index, bytes ->
                media.setByteBuffer("csd-$index", ByteBuffer.wrap(bytes))
            }
            val codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            decoder = codec // Release even if configure/start fails.
            codec.configure(media, surfaceView.holder.surface, null, 0)
            codec.setVideoScalingMode(MediaCodec.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            codec.start()
        }

        private fun decodeH264(packet: ByteArray, timestampUs: Long) {
            val deadline = System.nanoTime() + 3_000_000_000L
            var queued = false
            while (!queued && isCurrent()) {
                synchronized(codecLock) {
                    val codec = decoder ?: return
                    if (!isCurrent()) return
                    drain(codec)
                    val index = codec.dequeueInputBuffer(10_000)
                    if (index >= 0) {
                        val buffer = codec.getInputBuffer(index) ?: throw IOException("H264输入缓冲区不可用")
                        if (packet.size > buffer.capacity()) throw IOException("H264帧超过设备解码缓冲区")
                        buffer.clear()
                        buffer.put(packet)
                        codec.queueInputBuffer(index, 0, packet.size, timestampUs, 0)
                        queued = true
                    }
                    drain(codec)
                }
                if (!queued && System.nanoTime() > deadline) throw IOException("H264解码器等待输入超时")
            }
        }

        private fun drain(codec: MediaCodec) {
            repeat(32) {
                if (!isCurrent()) return
                when (val index = codec.dequeueOutputBuffer(bufferInfo, 0)) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val format = codec.outputFormat
                        val w = format.getInteger(MediaFormat.KEY_WIDTH)
                        val h = format.getInteger(MediaFormat.KEY_HEIGHT)
                        val croppedW = if (format.containsKey("crop-left") && format.containsKey("crop-right"))
                            format.getInteger("crop-right") - format.getInteger("crop-left") + 1 else w
                        val croppedH = if (format.containsKey("crop-top") && format.containsKey("crop-bottom"))
                            format.getInteger("crop-bottom") - format.getInteger("crop-top") + 1 else h
                        setVideoSize(this, croppedW, croppedH)
                    }
                    MediaCodec.INFO_TRY_AGAIN_LATER -> return
                    else -> if (index >= 0) {
                        val render = (bufferInfo.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0
                        codec.releaseOutputBuffer(index, render)
                        if (render) frameDecoded("H.264")
                    }
                }
            }
        }

        private fun decodeJpeg(packet: ByteArray) {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(packet, 0, packet.size, bounds)
            if (bounds.outWidth !in 1..4096 || bounds.outHeight !in 1..4096) {
                throw IOException("MJPG图像尺寸无效")
            }
            val bitmap = BitmapFactory.decodeByteArray(packet, 0, packet.size)
                ?: throw IOException("MJPG图像无法解码")
            try {
                // stop waits for an old JPEG draw to finish before a replacement session starts.
                synchronized(codecLock) {
                    if (!isCurrent()) return
                    setVideoSize(this, bitmap.width, bitmap.height)
                    val canvas = surfaceView.holder.lockCanvas() ?: return
                    var displayed = false
                    try {
                        if (isCurrent()) {
                            canvas.drawColor(Color.BLACK)
                            val scale = minOf(canvas.width.toFloat() / bitmap.width, canvas.height.toFloat() / bitmap.height)
                            val w = bitmap.width * scale
                            val h = bitmap.height * scale
                            val x = (canvas.width - w) / 2f
                            val y = (canvas.height - h) / 2f
                            canvas.drawBitmap(bitmap, null, RectF(x, y, x + w, y + h), Paint(Paint.FILTER_BITMAP_FLAG))
                            displayed = true
                        }
                    } finally { surfaceView.holder.unlockCanvasAndPost(canvas) }
                    if (displayed) frameDecoded("MJPG")
                }
            } finally { bitmap.recycle() }
        }

        private fun frameDecoded(codec: String) {
            if (!firstFrame) {
                firstFrame = true
                status("实时取景 · $codec（已解码画面）")
            }
        }

        fun cancel() {
            cancelled = true
        }

        fun notStarted() {
            finished = true
            done.countDown()
        }

        fun finishBeforeReplacement() {
            // Disconnect first to unblock a pending HTTP read, then wait only on this
            // background transition worker until the old decoder/draw worker has exited.
            releaseResources()
            done.await()
        }

        private fun releaseResources() {
            val resources = synchronized(ioLock) {
                (connection to input).also { connection = null; input = null }
            }
            runCatching { resources.first?.disconnect() }
            runCatching { resources.second?.close() }
            synchronized(codecLock) {
                decoder?.let { codec ->
                    decoder = null
                    runCatching { codec.stop() }
                    runCatching { codec.release() }
                }
            }
        }
    }
}
