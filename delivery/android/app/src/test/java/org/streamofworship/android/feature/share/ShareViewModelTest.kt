package org.streamofworship.android.feature.share

import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.streamofworship.android.core.download.ArtifactDownloadCoordinator
import org.streamofworship.android.core.download.ArtifactDownloadRequest
import org.streamofworship.android.core.download.ArtifactDownloadScheduler
import org.streamofworship.android.data.offline.FileOfflineCacheRepository
import org.streamofworship.android.data.offline.OfflineArtifactKind
import org.streamofworship.android.data.offline.OfflineArtifactStatus
import org.streamofworship.android.data.render.ArtifactSizes
import org.streamofworship.android.data.render.RenderRepository
import org.streamofworship.android.feature.player.FakePlaybackRepository
import androidx.test.ext.junit.runners.AndroidJUnit4

@RunWith(AndroidJUnit4::class)
@OptIn(ExperimentalCoroutinesApi::class)
class ShareViewModelTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun `creates share token and loads signed download urls`() =
        runTest {
            val shareRepository = FakeShareRepository()
            val cacheRepository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
            val viewModel =
                ShareViewModel(
                    renderJobId = "job-1",
                    shareRepository = shareRepository,
                    playbackRepository = FakePlaybackRepository(),
                    renderRepository = FakeRenderRepository(),
                    downloadCoordinator =
                        ArtifactDownloadCoordinator(
                            cacheRepository = cacheRepository,
                            scheduler = FakeDownloadScheduler(),
                        ),
                    scope = this,
                )

            viewModel.setAllowDownload(true)
            viewModel.createShare()
            advanceUntilIdle()
            viewModel.loadDownloadUrls()
            advanceUntilIdle()

            assertTrue(shareRepository.createdAllowDownload)
            assertEquals("https://app/share/tok", viewModel.uiState.value.shareToken?.shareUrl)
            assertEquals("https://r2/audio.mp3", viewModel.uiState.value.audioUrl)
            assertEquals("https://r2/video.mp4", viewModel.uiState.value.videoUrl)
            assertEquals(OfflineArtifactStatus.Queued, viewModel.uiState.value.downloads[OfflineArtifactKind.Audio]?.status)
            assertEquals(OfflineArtifactStatus.Queued, viewModel.uiState.value.downloads[OfflineArtifactKind.Video]?.status)
        }

    @Test
    fun `skips missing artifact kind when render produced only one type`() =
        runTest {
            val cacheRepository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
            val viewModel =
                ShareViewModel(
                    renderJobId = "job-1",
                    shareRepository = FakeShareRepository(),
                    playbackRepository = FakePlaybackRepository(),
                    renderRepository = FakeRenderRepository(mp3SizeBytes = null),
                    downloadCoordinator =
                        ArtifactDownloadCoordinator(
                            cacheRepository = cacheRepository,
                            scheduler = FakeDownloadScheduler(),
                        ),
                    scope = this,
                )

            viewModel.loadDownloadUrls()
            advanceUntilIdle()

            assertNull(viewModel.uiState.value.message)
            assertNull(viewModel.uiState.value.audioUrl)
            assertEquals("https://r2/video.mp4", viewModel.uiState.value.videoUrl)
            assertEquals(null, viewModel.uiState.value.downloads[OfflineArtifactKind.Audio])
            assertEquals(OfflineArtifactStatus.Queued, viewModel.uiState.value.downloads[OfflineArtifactKind.Video]?.status)
        }

    @Test
    fun `enqueue failure on one artifact does not block the other`() =
        runTest {
            val cacheRepository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
            val viewModel =
                ShareViewModel(
                    renderJobId = "job-1",
                    shareRepository = FakeShareRepository(),
                    playbackRepository = FakePlaybackRepository(),
                    renderRepository = FakeRenderRepository(),
                    downloadCoordinator =
                        ArtifactDownloadCoordinator(
                            cacheRepository = cacheRepository,
                            scheduler = FailingForKindScheduler(OfflineArtifactKind.Audio),
                        ),
                    scope = this,
                )

            viewModel.loadDownloadUrls()
            advanceUntilIdle()

            assertEquals("https://r2/video.mp4", viewModel.uiState.value.videoUrl)
            assertEquals(OfflineArtifactStatus.Queued, viewModel.uiState.value.downloads[OfflineArtifactKind.Video]?.status)
            assertEquals(OfflineArtifactStatus.Failed, viewModel.uiState.value.downloads[OfflineArtifactKind.Audio]?.status)
            assertTrue(viewModel.uiState.value.message?.contains("Audio") == true)
        }

    @Test
    fun `builds android share intent payload`() {
        val intent = buildShareTextIntent("https://app/share/tok")

        assertEquals(android.content.Intent.ACTION_SEND, intent.action)
        assertEquals("text/plain", intent.type)
        assertEquals("https://app/share/tok", intent.getStringExtra(android.content.Intent.EXTRA_TEXT))
    }
}

private class FakeDownloadScheduler : ArtifactDownloadScheduler {
    override suspend fun enqueue(request: ArtifactDownloadRequest): Long = 99L
}

private class FailingForKindScheduler(
    private val failingKind: OfflineArtifactKind,
) : ArtifactDownloadScheduler {
    override suspend fun enqueue(request: ArtifactDownloadRequest): Long {
        if (request.kind == failingKind) error("cannot enqueue ${request.kind}")
        return 99L
    }
}

private class FakeRenderRepository(
    private val mp3SizeBytes: Long? = 1024L,
    private val mp4SizeBytes: Long? = 2048L,
) : RenderRepository {
    override suspend fun createRenderJob(
        songsetId: String,
        config: org.streamofworship.android.data.render.RenderFormConfig,
    ) = throw UnsupportedOperationException()

    override suspend fun getRenderJob(id: String) = throw UnsupportedOperationException()

    override suspend fun cancelRenderJob(id: String) = throw UnsupportedOperationException()

    override suspend fun getArtifactSizes(id: String): ArtifactSizes =
        ArtifactSizes(renderJobId = id, mp3SizeBytes = mp3SizeBytes, mp4SizeBytes = mp4SizeBytes)
}

private class FakeShareRepository : ShareRepository {
    var createdAllowDownload = false

    override suspend fun createRenderShare(
        renderJobId: String,
        allowDownload: Boolean,
    ): ShareToken {
        createdAllowDownload = allowDownload
        return ShareToken(token = "tok", shareUrl = "https://app/share/tok", songsetId = "set-1", renderJobId = renderJobId, allowDownload = allowDownload)
    }

    override suspend fun createSongsetShare(
        songsetId: String,
        allowDownload: Boolean,
    ): ShareToken = ShareToken(token = "tok", shareUrl = "https://app/share/tok", songsetId = songsetId, allowDownload = allowDownload)

    override suspend fun listShares(
        songsetId: String?,
        renderJobId: String?,
    ): List<ShareToken> = emptyList()
}
