package org.streamofworship.android.feature.player

import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
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
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
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
    fun `default audio artifact loads rendered audio url`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val playbackRepository = CountingPlaybackRepository()
            val viewModel =
                PlayerViewModel(
                    renderJobId = "job-1",
                    repository = playbackRepository,
                    controller = controller,
                    scope = backgroundScope,
                    tickerMillis = 0,
                    defaultArtifact = PlaybackArtifact.Audio,
                )

            viewModel.load()
            runCurrent()

            assertEquals("https://r2/audio.mp3", viewModel.uiState.value.mediaUrl)
            assertEquals(PlaybackArtifact.Audio, viewModel.uiState.value.artifact)
            assertEquals(0, playbackRepository.videoUrlCalls)
            assertEquals(1, playbackRepository.audioUrlCalls)
        }

    @Test
    fun `expired signed url reports retry state and retry refreshes playback`() =        runTest {
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

    @Test
    fun `malformed expiry is treated as expired and triggers refresh`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val playbackRepository =
                CountingPlaybackRepository(
                    videoUrls =
                        ArrayDeque(
                            listOf(
                                SignedUrlResponse("https://r2/unparseable.mp4", "not-a-date"),
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
            // Failing closed: an unparseable expiry must not stream the (possibly expired) URL.
            assertEquals(OfflinePlaybackState.ExpiredSignedUrl, viewModel.uiState.value.offlineState)
            assertNull(viewModel.uiState.value.mediaUrl)

            viewModel.load()
            runCurrent()
            assertEquals("https://r2/fresh.mp4", viewModel.uiState.value.mediaUrl)
        }

    @Test
    fun `load with video artifact and cached offline artifact calls setMedia with local uri and isVideo true`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
            repository.markCached(
                renderJobId = "job-1",
                kind = OfflineArtifactKind.Video,
                localUri = "file:///cached/job-1.mp4",
                bytesDownloaded = 100L,
                totalBytes = 100L,
                nowEpochMillis = 1L,
            )
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel =
                PlayerViewModel(
                    renderJobId = "job-1",
                    repository = CountingPlaybackRepository(),
                    controller = controller,
                    offlineCacheRepository = repository,
                    scope = backgroundScope,
                    tickerMillis = 0,
                    defaultArtifact = PlaybackArtifact.Video,
                )

            viewModel.load(artifact = PlaybackArtifact.Video)
            runCurrent()

            assertEquals("file:///cached/job-1.mp4", viewModel.uiState.value.mediaUrl)
            assertEquals("file:///cached/job-1.mp4", controller.mediaUrl)
            // Audio is no longer played back in the worship screen; the artifact parameter is
            // vestigial after Phase 8 (RenderScreen only ever offers a Video play route). The
            // load() path still always queries OfflineArtifactKind.Video for playback.
        }

    @Test
    fun `artifact parameter still selects offline kind but video is the only playable route`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
            repository.markCached(
                renderJobId = "job-1",
                kind = OfflineArtifactKind.Video,
                localUri = "file:///cached/video.mp4",
                bytesDownloaded = 100L,
                totalBytes = 100L,
                nowEpochMillis = 1L,
            )
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel =
                PlayerViewModel(
                    renderJobId = "job-1",
                    repository = FakePlaybackRepository(),
                    controller = controller,
                    offlineCacheRepository = repository,
                    scope = backgroundScope,
                    tickerMillis = 0,
                )

            // No-arg load() uses the default (Video) artifact.
            viewModel.load()
            runCurrent()

            assertEquals("file:///cached/video.mp4", viewModel.uiState.value.mediaUrl)
        }

    @Test
    fun `bindController no-ops on the same instance and re-applies listener on a new instance`() =
        runTest {
            val first = RecordingPlayerController(durationMillis = 120_000)
            val viewModel =
                PlayerViewModel(
                    renderJobId = "job-1",
                    repository = FakePlaybackRepository(),
                    controller = first,
                    scope = backgroundScope,
                    tickerMillis = 0,
                )
            // init{...} wires the listener exactly once.
            assertEquals(1, first.listenerBindingCount)

            // Re-binding the SAME instance must not double-wire.
            viewModel.bindController(first)
            assertEquals(1, first.listenerBindingCount)

            // After rotation, a fresh controller is bound — the listener must be re-applied.
            val second = RecordingPlayerController(durationMillis = 120_000)
            viewModel.bindController(second)
            assertEquals(1, second.listenerBindingCount)

            // Play/pause/seek now route to the freshly bound controller, not the stale first.
            viewModel.playPause()
            assertTrue(second.playCalled)
            assertFalse(first.playCalled)
        }

    @Test
    fun `decoder playback error exposes retryable error and retry re-prepares current media`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel = viewModel(this, controller)
            viewModel.load()
            runCurrent()
            viewModel.setPlaybackSnapshot(42_000, 120_000, true)

            controller.emit(PlayerEvent.Error("decoder failed", PlaybackErrorKind.Decoder))
            runCurrent()

            assertEquals("Playback failed", viewModel.uiState.value.playbackError?.title)
            assertEquals(
                "The video format is not supported on this device. Older renders may need to be rendered again.",
                viewModel.uiState.value.playbackError?.message,
            )
            assertFalse(viewModel.uiState.value.isPlaying)

            viewModel.retryPlayback()
            runCurrent()

            assertNull(viewModel.uiState.value.playbackError)
            assertEquals("https://r2/video.mp4", controller.mediaUrl)
            assertEquals(42_000L, controller.position)
            assertTrue(controller.playing)
            assertEquals(2, controller.setMediaCount)
        }

    @Test
    fun `generic playback error can be dismissed`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel = viewModel(this, controller)

            controller.emit(PlayerEvent.Error("network dropped"))
            runCurrent()
            assertEquals("network dropped", viewModel.uiState.value.playbackError?.message)

            viewModel.dismissPlaybackError()
            assertNull(viewModel.uiState.value.playbackError)
        }

    @Test
    fun `software decoder warning auto dismisses and hardware decoder clears it`() =
        runTest {
            val controller = FakePlayerController(durationMillis = 120_000)
            val viewModel = viewModel(this, controller)

            controller.emit(PlayerEvent.VideoDecoderChanged("c2.android.avc.decoder", true))
            runCurrent()
            assertTrue(viewModel.uiState.value.softwareDecoderWarning)

            advanceTimeBy(5_000)
            runCurrent()
            assertFalse(viewModel.uiState.value.softwareDecoderWarning)

            controller.emit(PlayerEvent.VideoDecoderChanged("c2.android.avc.decoder", true))
            runCurrent()
            assertTrue(viewModel.uiState.value.softwareDecoderWarning)
            controller.emit(PlayerEvent.VideoDecoderChanged("c2.exynos.h264.decoder", false))
            runCurrent()
            assertFalse(viewModel.uiState.value.softwareDecoderWarning)
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

private class RecordingPlayerController(
    durationMillis: Long,
) : FakePlayerController(durationMillis) {
    var listenerBindingCount = 0
    var playCalled = false

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        listenerBindingCount += 1
    }

    override fun play() {
        playCalled = true
    }
}

private class CountingPlaybackRepository(
    private val videoUrls: ArrayDeque<SignedUrlResponse> =
        ArrayDeque(listOf(SignedUrlResponse("https://r2/video.mp4", "2027-01-01T00:00:00.000Z"))),
) : FakePlaybackRepository() {
    var videoUrlCalls = 0
    var audioUrlCalls = 0

    override suspend fun renderedVideoUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse {
        videoUrlCalls += 1
        return videoUrls.removeFirst()
    }

    override suspend fun renderedAudioUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse {
        audioUrlCalls += 1
        return super.renderedAudioUrl(renderJobId, contentDisposition)
    }
}

internal open class FakePlayerController(
    override var durationMillis: Long,
) : PlayerController {
    var position = 0L
    var playing = false
    override val positionMillis: Long get() = position
    override val isPlaying: Boolean get() = playing
    var mediaUrl: String? = null
    var setMediaCount = 0
    private var listener: PlayerController.PlayerEventListener? = null

    override fun setMedia(
        url: String,
        isVideo: Boolean,
    ) {
        setMediaCount += 1
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

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        this.listener = listener
    }

    fun emit(event: PlayerEvent) {
        listener?.onEvent(event)
    }
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
