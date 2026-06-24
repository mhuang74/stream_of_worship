package org.streamofworship.android.feature.player

import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.streamofworship.android.data.offline.FileOfflineCacheRepository
import org.streamofworship.android.data.offline.OfflineArtifactKind
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest
import org.streamofworship.android.data.playback.PlaybackRepository
import org.streamofworship.android.data.playback.SignedUrlResponse
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset

@OptIn(ExperimentalCoroutinesApi::class)
class PlayerViewModelTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

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

    @Test
    fun `uses cached artifact before requesting remote signed url`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath())
            repository.markCached(
                renderJobId = "job-1",
                kind = OfflineArtifactKind.Video,
                localUri = "file:///cached/job-1.mp4",
                bytesDownloaded = 100L,
                totalBytes = 100L,
                nowEpochMillis = 1L,
            )
            val controller = FakePlayerController(durationMillis = 120_000)
            val playbackRepository = CountingPlaybackRepository()
            val viewModel =
                PlayerViewModel(
                    renderJobId = "job-1",
                    repository = playbackRepository,
                    controller = controller,
                    offlineCacheRepository = repository,
                    scope = backgroundScope,
                    tickerMillis = 0,
                )

            viewModel.load()
            runCurrent()

            assertEquals("file:///cached/job-1.mp4", viewModel.uiState.value.mediaUrl)
            assertEquals(OfflinePlaybackState.Cached, viewModel.uiState.value.offlineState)
            assertEquals(0, playbackRepository.videoUrlCalls)
        }

    @Test
    fun `expired signed url reports retry state and retry refreshes playback`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val playbackRepository =
                CountingPlaybackRepository(
                    videoUrls =
                        ArrayDeque(
                            listOf(
                                SignedUrlResponse("https://r2/expired.mp4", "2025-01-01T00:00:00Z"),
                                SignedUrlResponse("https://r2/fresh.mp4", "2026-01-01T00:00:00Z"),
                            ),
                        ),
                )
            val viewModel =
                PlayerViewModel(
                    renderJobId = "job-1",
                    repository = playbackRepository,
                    controller = controller,
                    clock = Clock.fixed(Instant.parse("2025-06-01T00:00:00Z"), ZoneOffset.UTC),
                    scope = backgroundScope,
                    tickerMillis = 0,
                )

            viewModel.load()
            runCurrent()
            assertEquals(OfflinePlaybackState.ExpiredSignedUrl, viewModel.uiState.value.offlineState)

            viewModel.load()
            runCurrent()
            assertEquals("https://r2/fresh.mp4", viewModel.uiState.value.mediaUrl)
            assertEquals(OfflinePlaybackState.Missing, viewModel.uiState.value.offlineState)
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

private class CountingPlaybackRepository(
    private val videoUrls: ArrayDeque<SignedUrlResponse> =
        ArrayDeque(listOf(SignedUrlResponse("https://r2/video.mp4", "2027-01-01T00:00:00.000Z"))),
) : FakePlaybackRepository() {
    var videoUrlCalls = 0

    override suspend fun renderedVideoUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse {
        videoUrlCalls += 1
        return videoUrls.removeFirst()
    }
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

internal open class FakePlaybackRepository : PlaybackRepository {
    override suspend fun renderedAudioUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse = SignedUrlResponse("https://r2/audio.mp3", "2027-01-01T00:00:00.000Z")

    override suspend fun renderedVideoUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse = SignedUrlResponse("https://r2/video.mp4", "2027-01-01T00:00:00.000Z")

    override suspend fun renderedChaptersUrl(renderJobId: String): SignedUrlResponse =
        SignedUrlResponse("https://r2/chapters.json", "2027-01-01T00:00:00.000Z")

    override suspend fun sourceAudioUrl(hashPrefix: String): SignedUrlResponse =
        SignedUrlResponse("https://r2/source.mp3", "2027-01-01T00:00:00.000Z")

    override suspend fun sourceLrcUrl(hashPrefix: String): SignedUrlResponse =
        SignedUrlResponse("https://r2/source.lrc", "2027-01-01T00:00:00.000Z")

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
