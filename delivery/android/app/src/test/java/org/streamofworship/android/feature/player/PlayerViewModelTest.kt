package org.streamofworship.android.feature.player

import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest
import org.streamofworship.android.data.playback.PlaybackRepository
import org.streamofworship.android.data.playback.SignedUrlResponse

@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelTest {
    @Test
    fun `loads media and tracks current chapter and line`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel = viewModel(this, controller)

            viewModel.load()
            runCurrent()
            controller.position = 70_000
            viewModel.setPlaybackSnapshot(70_000, 120_000, false)

            assertEquals("https://r2/video.mp4", viewModel.uiState.value.mediaUrl)
            assertEquals("Second", viewModel.uiState.value.currentChapter?.title)
            assertEquals("line two", viewModel.uiState.value.currentLine?.text)
        }

    @Test
    fun `play seek skip and chapter jumps update controller`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel = viewModel(this, controller)
            viewModel.load()
            runCurrent()

            viewModel.playPause()
            assertTrue(controller.playing)
            viewModel.skipBy(10_000)
            assertEquals(10_000L, controller.position)
            viewModel.nextChapter()
            assertEquals(60_000L, controller.position)
            viewModel.skipBy(20_000)
            viewModel.previousChapter()
            assertEquals(60_000L, controller.position)
            viewModel.playPause()
            assertFalse(controller.playing)
        }

    private fun viewModel(
        scope: TestScope,
        controller: FakePlayerController,
    ): PlayerViewModel =
        PlayerViewModel(
            renderJobId = "job-1",
            repository = FakePlaybackRepository(),
            controller = controller,
            scope = scope.backgroundScope,
            tickerMillis = 0,
        )
}

internal class FakePlayerController(
    override var durationMillis: Long,
) : PlayerController {
    var position = 0L
    var playing = false
    override val positionMillis: Long get() = position
    override val isPlaying: Boolean get() = playing
    var mediaUrl: String? = null

    override fun setMedia(
        url: String,
        isVideo: Boolean,
    ) {
        mediaUrl = url
    }

    override fun play() {
        playing = true
    }

    override fun pause() {
        playing = false
    }

    override fun seekTo(positionMillis: Long) {
        position = positionMillis
    }

    override fun release() = Unit
}

internal class FakePlaybackRepository : PlaybackRepository {
    override suspend fun renderedAudioUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse = SignedUrlResponse("https://r2/audio.mp3", "2026-01-01T00:00:00.000Z")

    override suspend fun renderedVideoUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse = SignedUrlResponse("https://r2/video.mp4", "2026-01-01T00:00:00.000Z")

    override suspend fun renderedChaptersUrl(renderJobId: String): SignedUrlResponse =
        SignedUrlResponse("https://r2/chapters.json", "2026-01-01T00:00:00.000Z")

    override suspend fun sourceAudioUrl(hashPrefix: String): SignedUrlResponse =
        SignedUrlResponse("https://r2/source.mp3", "2026-01-01T00:00:00.000Z")

    override suspend fun sourceLrcUrl(hashPrefix: String): SignedUrlResponse =
        SignedUrlResponse("https://r2/source.lrc", "2026-01-01T00:00:00.000Z")

    override suspend fun chapters(renderJobId: String): PlaybackManifest =
        PlaybackManifest(
            totalDurationMillis = 120_000,
            generatedAt = "2026-01-01T00:00:00.000Z",
            chapters =
                listOf(
                    PlaybackChapter(1, "First", 0, 60_000, listOf(PlaybackLine("line one", 1_000))),
                    PlaybackChapter(2, "Second", 60_000, 120_000, listOf(PlaybackLine("line two", 65_000))),
                ),
        )
}
