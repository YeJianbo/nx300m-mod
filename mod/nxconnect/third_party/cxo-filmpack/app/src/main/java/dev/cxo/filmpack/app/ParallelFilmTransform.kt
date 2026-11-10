package dev.cxo.filmpack.app

import java.io.Closeable
import java.util.concurrent.Callable
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Export-local bounded row parallelism. Only immutable film assets are shared;
 * every task owns its source, destination and pipeline scratch. Full rows retain
 * sampling0's x modulo-four lanes. JPEG emission remains ordered and serial.
 * The owner must call render serially and close before releasing source resources. */
internal class ParallelFilmTransform(
    private val apply: (ShortArray, FloatArray, Int, Int) -> Unit,
    private val cancelled: () -> Boolean,
    private val after: (FloatArray,Int,Int,Int) -> Unit = { _,_,_,_ -> },
    private val workers: Int = minOf(4,Runtime.getRuntime().availableProcessors()).coerceAtLeast(1),
) : Closeable {
    private val executor = if (workers > 1) Executors.newFixedThreadPool(workers) else null
    private val inputs = arrayOfNulls<ShortArray>(workers)
    private val outputs = arrayOfNulls<FloatArray>(workers)

    fun render(source: ShortArray, destination: FloatArray, width: Int, x: Int, y: Int = 0) {
        require(width > 0 && source.size == destination.size && source.size % (width*3) == 0)
        val rows = source.size/3/width
        val count = minOf(workers,rows)
        if (executor == null || count <= 1) {
            checkCancellation(); apply(source,destination,width,x); after(destination,width,x,y); checkCancellation(); return
        }
        val jobs = (0 until count).map { worker ->
            val start = (rows*worker/count)*width*3
            val end = (rows*(worker+1)/count)*width*3
            val length = end-start
            val input = inputs[worker]?.takeIf { it.size == length } ?: ShortArray(length).also { inputs[worker] = it }
            val output = outputs[worker]?.takeIf { it.size == length } ?: FloatArray(length).also { outputs[worker] = it }
            source.copyInto(input,0,start,end)
            Callable {
                checkCancellation()
                apply(input,output,width,x)
                after(output,width,x,y+start/3/width)
                checkCancellation()
                output.copyInto(destination,start)
            }
        }
        // Ordinary worker failures are observed after every job finishes. Owner
        // interruption is terminal: close joins cancelled workers before unwind.
        try {
            for (future in executor.invokeAll(jobs)) {
                try { future.get() }
                catch (failure: ExecutionException) { throw failure.cause ?: failure }
            }
        } catch (interrupted: InterruptedException) {
            close()
            Thread.currentThread().interrupt()
            throw interrupted
        }
    }

    private fun checkCancellation() {
        if (Thread.currentThread().isInterrupted || cancelled()) throw java.util.concurrent.CancellationException("已取消")
    }

    override fun close() {
        executor ?: return
        executor.shutdownNow()
        var interrupted = false
        try {
            while (true) {
                try { if (executor.awaitTermination(1,TimeUnit.SECONDS)) return }
                catch (_: InterruptedException) { interrupted = true }
            }
        } finally { if (interrupted) Thread.currentThread().interrupt() }
    }
}
