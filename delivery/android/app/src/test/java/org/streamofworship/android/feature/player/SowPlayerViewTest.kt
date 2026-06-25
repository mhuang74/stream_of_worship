package org.streamofworship.android.feature.player

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@OptIn(UnstableApi::class)
@RunWith(RobolectricTestRunner::class)
class SowPlayerViewTest {
    @Test
    fun `inline player view uses shared playback surface settings without native controls`() {
        val view =
            createSowPlayerView(
                context = ApplicationProvider.getApplicationContext<Context>(),
                player = null,
                mode = SowPlayerViewMode.Inline,
                diagnostics =
                    PlaybackDiagnostics(
                        renderJobId = "job-1",
                        artifact = PlaybackArtifact.Video,
                    ),
            )

        assertEquals(AspectRatioFrameLayout.RESIZE_MODE_FIT, view.getResizeMode())
        assertFalse(view.getUseController())
        assertFalse(view.getUseArtwork())
        assertNull(view.getDefaultArtwork())
        assertEquals(PlayerView.ARTWORK_DISPLAY_MODE_OFF, view.getArtworkDisplayMode())
        assertEquals(PlayerView.SHOW_BUFFERING_WHEN_PLAYING, view.privateInt("showBuffering"))
        assertTrue(view.privateBoolean("enableComposeSurfaceSyncWorkaround"))
    }

    @Test
    fun `fullscreen player view keeps native controls enabled`() {
        val view =
            createSowPlayerView(
                context = ApplicationProvider.getApplicationContext<Context>(),
                player = null,
                mode = SowPlayerViewMode.Fullscreen,
                diagnostics =
                    PlaybackDiagnostics(
                        renderJobId = "job-1",
                        artifact = PlaybackArtifact.Video,
                    ),
            )

        assertTrue(view.getUseController())
        assertEquals(AspectRatioFrameLayout.RESIZE_MODE_FIT, view.getResizeMode())
        assertTrue(view.privateBoolean("enableComposeSurfaceSyncWorkaround"))
    }
}

private fun PlayerView.privateBoolean(fieldName: String): Boolean =
    privateField(fieldName).getBoolean(this)

private fun PlayerView.privateInt(fieldName: String): Int =
    privateField(fieldName).getInt(this)

private fun PlayerView.privateField(fieldName: String) =
    PlayerView::class.java.getDeclaredField(fieldName).apply { isAccessible = true }
