package org.streamofworship.android.feature.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

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
}

private class FakeMediaPlayerFacade(
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
