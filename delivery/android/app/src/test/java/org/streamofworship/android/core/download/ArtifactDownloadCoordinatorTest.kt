package org.streamofworship.android.core.download

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
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
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
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
                        title = "stream-of-worship-job-1-video",
                    ),
                )

            assertEquals(OfflineArtifactStatus.Queued, metadata.status)
            assertEquals(42L, metadata.downloadId)
            assertEquals("https://r2/video.mp4", metadata.remoteUrl)
        }

    @Test
    fun `enqueue persists queued row before scheduling so the receiver always finds it`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
            // The scheduler inspects the cache repository at enqueue-time and records what it
            // observed: the queued row must already exist (with downloadId null) before the
            // download id is known, closing the enqueue/markQueued race window.
            val scheduler =
                object : ArtifactDownloadScheduler {
                    var observedStatusAtEnqueue: OfflineArtifactStatus? = null
                    var observedDownloadIdAtEnqueue: Long? = Long.MAX_VALUE

                    override suspend fun enqueue(request: ArtifactDownloadRequest): Long {
                        val row = repository.getArtifact(request.renderJobId, request.kind)
                        observedStatusAtEnqueue = row?.status
                        observedDownloadIdAtEnqueue = row?.downloadId
                        return 77L
                    }
                }
            val coordinator =
                ArtifactDownloadCoordinator(
                    cacheRepository = repository,
                    scheduler = scheduler,
                    clockMillis = { 2000L },
                )

            val metadata =
                coordinator.enqueue(
                    ArtifactDownloadRequest(
                        renderJobId = "job-1",
                        kind = OfflineArtifactKind.Audio,
                        url = "https://r2/audio.mp3",
                        expiresAt = "2026-01-01T00:00:00Z",
                        title = "stream-of-worship-job-1-audio",
                    ),
                )

            assertEquals(OfflineArtifactStatus.Queued, scheduler.observedStatusAtEnqueue)
            assertNull("downloadId must not be set yet when scheduler runs", scheduler.observedDownloadIdAtEnqueue)
            assertEquals(77L, metadata.downloadId)
        }

    @Test
    fun `enqueue failure records failed metadata`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = kotlinx.coroutines.test.UnconfinedTestDispatcher())
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

    @Test
    fun `canonical title parses back into render job id and kind`() {
        val request =
            ArtifactDownloadRequest(
                renderJobId = "job-1",
                kind = OfflineArtifactKind.Video,
                url = "https://r2/video.mp4",
                expiresAt = null,
                title = "placeholder",
            )
        val title = request.canonicalTitle()

        assertEquals("stream-of-worship-job-1-video", title)
        assertEquals("job-1" to OfflineArtifactKind.Video, parseArtifactDownloadTitle(title))
        assertEquals("job-1" to OfflineArtifactKind.Audio, parseArtifactDownloadTitle("stream-of-worship-job-1-audio"))
        // A render job id that itself contains dashes is still recovered.
        assertEquals("uuid-like-123" to OfflineArtifactKind.Video, parseArtifactDownloadTitle("stream-of-worship-uuid-like-123-video"))
        assertNull(parseArtifactDownloadTitle("unrelated title"))
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

