package dev.cxo.filmpack.core

/** Converts LibRaw's explicit output_color=0, linear-gamma RGB16 carrier to DxO Q15.
 *
 * This is a boundary conversion only. It assumes post-black `maximumAfterProcess`,
 * 16-bit output and no LibRaw adaptive maximum scaling. The maximum and source bit
 * depth are properties of each decoded file; no camera/photo constant is embedded.
 */
object RawCameraNormalization {
    private const val RGB16_MAX = 65535.0
    private const val Q15_MAX = 32767.0

    fun render(source: ShortArray, destination: ShortArray, maximumAfterProcess: Int, rawBps: Int) {
        require(source.size % 3 == 0 && destination.size == source.size)
        require(maximumAfterProcess in 1..65535) { "maximumAfterProcess outside RGB16 domain" }
        require(rawBps in 1..16) { "rawBps outside supported domain" }
        val scale = maximumAfterProcess.toDouble() * Math.scalb(1.0, 15 - rawBps) / RGB16_MAX
        for (i in source.indices) {
            val code = source[i].toInt() and 0xffff
            destination[i] = Math.rint(code * scale).toInt().coerceIn(0, 32767).toShort()
        }
    }

    fun render(source: ShortArray, maximumAfterProcess: Int, rawBps: Int): ShortArray =
        ShortArray(source.size).also { render(source, it, maximumAfterProcess, rawBps) }
}
