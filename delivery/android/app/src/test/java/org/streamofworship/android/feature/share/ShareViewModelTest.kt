package org.streamofworship.android.feature.share

import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.streamofworship.android.feature.player.FakePlaybackRepository
import androidx.test.ext.junit.runners.AndroidJUnit4

@RunWith(AndroidJUnit4::class)
@OptIn(ExperimentalCoroutinesApi::class)
class ShareViewModelTest {
    @Test
    fun `creates share token and loads signed download urls`() =
        runTest {
            val shareRepository = FakeShareRepository()
            val viewModel =
                ShareViewModel(
                    renderJobId = "job-1",
                    shareRepository = shareRepository,
                    playbackRepository = FakePlaybackRepository(),
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
        }

    @Test
    fun `builds android share intent payload`() {
        val intent = buildShareTextIntent("https://app/share/tok")

        assertEquals(android.content.Intent.ACTION_SEND, intent.action)
        assertEquals("text/plain", intent.type)
        assertEquals("https://app/share/tok", intent.getStringExtra(android.content.Intent.EXTRA_TEXT))
    }
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
