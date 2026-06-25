package org.streamofworship.android.feature.player

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@OptIn(UnstableApi::class)
@RunWith(RobolectricTestRunner::class)
class VideoExoPlayerFactoryTest {
    @Test
    fun `video renderers factory enables decoder fallback`() {
        val factory = createVideoRenderersFactory(ApplicationProvider.getApplicationContext<Context>())

        assertTrue(factory.privateBoolean("enableDecoderFallback"))
        assertEquals(
            DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER,
            factory.privateInt("extensionRendererMode"),
        )
    }

    @Test
    fun `video exo player factory creates player`() {
        val player = VideoExoPlayerFactory.create(ApplicationProvider.getApplicationContext())

        try {
            assertEquals(0L, player.duration.coerceAtLeast(0L))
        } finally {
            player.release()
        }
    }
}

private fun DefaultRenderersFactory.privateBoolean(fieldName: String): Boolean =
    privateField(fieldName).getBoolean(this)

private fun DefaultRenderersFactory.privateInt(fieldName: String): Int =
    privateField(fieldName).getInt(this)

private fun DefaultRenderersFactory.privateField(fieldName: String) =
    DefaultRenderersFactory::class.java.getDeclaredField(fieldName).apply { isAccessible = true }
