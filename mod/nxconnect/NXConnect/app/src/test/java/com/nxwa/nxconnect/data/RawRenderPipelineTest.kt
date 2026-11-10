package com.nxwa.nxconnect.data

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RawRenderPipelineTest {
    @Test
    fun neutralRenderingPreservesEveryByteAndAlpha() {
        val input = intArrayOf(0x00112233, 0x80abcdef.toInt(), 0xff123456.toInt())
        assertArrayEquals(input, RawRenderPipeline.render(input, RawRenderParams()))
    }

    @Test
    fun positiveExposureBrightensAndPreservesAlpha() {
        val input = intArrayOf(0x7f777777)
        val output = RawRenderPipeline.render(input, RawRenderParams(exposureEv = 1f))[0]
        assertTrue((output shr 16 and 0xff) > 0x77)
        assertEquals(0x7f, output ushr 24)
    }

    @Test
    fun negativeSaturationProducesNeutralChannels() {
        val input = intArrayOf(0xffd05020.toInt())
        val output = RawRenderPipeline.render(input, RawRenderParams(saturation = -100))[0]
        val red = output shr 16 and 0xff
        val green = output shr 8 and 0xff
        val blue = output and 0xff
        assertTrue(kotlin.math.abs(red - green) <= 1)
        assertTrue(kotlin.math.abs(green - blue) <= 1)
    }

    @Test
    fun identityLutAtFullStrengthIsNeutral() {
        val input = intArrayOf(0xff345678.toInt(), 0xffabcdef.toInt())
        val output = RawRenderPipeline.render(input, RawRenderParams(lutStrength = 100), CubeLut.identity())
        assertArrayEquals(input, output)
    }
}
