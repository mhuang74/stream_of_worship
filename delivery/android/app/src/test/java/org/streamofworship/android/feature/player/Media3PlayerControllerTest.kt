package org.streamofworship.android.feature.player

import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class Media3PlayerControllerTest {
    @Test
    fun `forwards media setup and playback commands`() {
        val player = FakeMediaPlayerFacade(duration = 60_000, position = -100)
        val controller = Media3PlayerController(player, Unit)

        controller.setMedia("https://example.com/render.mp4", isVideo = true)
        controller.play()
        controller.seekTo(-500)
        controller.pause()
        controller.release()

        assertEquals("https://example.com/render.mp4", player.mediaUrl)
        assertTrue(player.prepared)
        assertEquals(0L, player.seekPosition)
        assertTrue(player.playCalled)
        assertTrue(player.pauseCalled)
        assertTrue(player.releaseCalled)
        assertEquals(60_000L, controller.durationMillis)
        assertEquals(0L, controller.positionMillis)
        // Fake facade backs the controller, so no underlying PlayerView host is exposed.
        assertNull(controller.playerView)
    }

    @Test
    fun `wake lock acquires on play and releases on pause and cleanup`() {
        val handle = FakeWakeLockHandle()
        val wakeLock = PlaybackWakeLock(handle, Unit)

        wakeLock.update(true)
        wakeLock.update(true)
        wakeLock.update(false)
        wakeLock.update(false)
        wakeLock.update(true)
        wakeLock.release()

        assertEquals(2, handle.acquireCount)
        assertEquals(2, handle.releaseCount)
        assertFalse(handle.isHeld)
    }

    @Test
    fun `direct player facade exposes the underlying player view and releases cleanly`() {
        val exoPlayer = ExoPlayer.Builder(ApplicationProvider.getApplicationContext()).build()
        val controller = Media3PlayerController(exoPlayer)
        try {
            assertSame(exoPlayer, controller.playerView)
            // Listener registration must not crash; silence is fine since the underlying
            // player has nothing to play yet.
            controller.setEventListener(PlayerController.PlayerEventListener { })
        } finally {
            controller.release()
        }
    }

    @Test
    fun `video exo player factory returns a playable player with zero duration before media is set`() {
        val exoPlayer = VideoExoPlayerFactory.create(ApplicationProvider.getApplicationContext())
        try {
            // A freshly-created ExoPlayer has no media, so its duration must be UNKNOWN (<= 0).
            assertEquals(0L, exoPlayer.duration.coerceAtLeast(0L))
        } finally {
            exoPlayer.release()
        }
    }

    @Test
    fun `video exo player factory controller reports zero duration before media is set`() {
        val exoPlayer = VideoExoPlayerFactory.create(ApplicationProvider.getApplicationContext())
        val controller = Media3PlayerController(exoPlayer)
        try {
            assertEquals(0L, controller.durationMillis)
        } finally {
            controller.release()
        }
    }

    @Test
    fun `direct player facade sets file uri media without throwing`() {
        val exoPlayer = VideoExoPlayerFactory.create(ApplicationProvider.getApplicationContext())
        val controller = Media3PlayerController(exoPlayer)
        try {
            // Robolectric cannot truly decode video, but setMedia + prepare must not throw and
            // should not leave the controller in an erroring configuration.
            controller.setMedia("file:///data/local/tmp/test.mp4", isVideo = true)
        } finally {
            controller.release()
        }
    }
}

internal class FakeMediaPlayerFacade(
    private val duration: Long,
    private val position: Long,
) : MediaPlayerFacade {
    var mediaUrl: String? = null
    var prepared = false
    var playCalled = false
    var pauseCalled = false
    var releaseCalled = false
    var seekPosition: Long? = null
    override val durationMillis: Long get() = duration
    override val positionMillis: Long get() = position
    override val isPlaying: Boolean get() = playCalled && !pauseCalled

    override fun setMedia(url: String) {
        mediaUrl = url
    }

    override fun prepare() {
        prepared = true
    }

    override fun play() {
        playCalled = true
    }

    override fun pause() {
        pauseCalled = true
    }

    override fun seekTo(positionMillis: Long) {
        seekPosition = positionMillis
    }

    override fun release() {
        releaseCalled = true
    }

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) = Unit
}

private class FakeWakeLockHandle : WakeLockHandle {
    override var isHeld: Boolean = false
    var acquireCount = 0
    var releaseCount = 0

    override fun acquire() {
        acquireCount += 1
        isHeld = true
    }

    override fun release() {
        releaseCount += 1
        isHeld = false
    }
}
