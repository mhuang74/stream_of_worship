package org.streamofworship.android.core.download

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.streamofworship.android.data.offline.FileOfflineCacheRepository
import org.streamofworship.android.data.offline.OfflineArtifactKind
import org.streamofworship.android.data.offline.OfflineArtifactStatus

class ArtifactDownloadCoordinatorTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun `enqueue records queued download metadata`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath())
            val coordinator =
                ArtifactDownloadCoordinator(
                    cacheRepository = repository,
                    scheduler = FakeScheduler(downloadId = 42L),
                    clockMillis = { 1000L },
                )

            val metadata =
                coordinator.enqueue(
                    ArtifactDownloadRequest(
                        renderJobId = "job-1",
                        kind = OfflineArtifactKind.Video,
                        url = "https://r2/video.mp4",
                        expiresAt = "2026-01-01T00:00:00Z",
                        title = "video",
                    ),
                )

            assertEquals(OfflineArtifactStatus.Queued, metadata.status)
            assertEquals(42L, metadata.downloadId)
            assertEquals("https://r2/video.mp4", metadata.remoteUrl)
        }

    @Test
    fun `enqueue failure records failed metadata`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath())
            val coordinator =
                ArtifactDownloadCoordinator(
                    cacheRepository = repository,
                    scheduler = FailingScheduler(),
                    clockMillis = { 2000L },
                )

            val metadata =
                coordinator.enqueue(
                    ArtifactDownloadRequest(
                        renderJobId = "job-1",
                        kind = OfflineArtifactKind.Audio,
                        url = "https://r2/audio.mp3",
                        expiresAt = "2026-01-01T00:00:00Z",
                        title = "audio",
                    ),
                )

            assertEquals(OfflineArtifactStatus.Failed, metadata.status)
            assertEquals("boom", metadata.failureMessage)
        }
}

private class FakeScheduler(
    private val downloadId: Long? = null,
) : ArtifactDownloadScheduler {
    override suspend fun enqueue(request: ArtifactDownloadRequest): Long? = downloadId
}

private class FailingScheduler : ArtifactDownloadScheduler {
    override suspend fun enqueue(request: ArtifactDownloadRequest): Long? {
        error("boom")
    }
}
