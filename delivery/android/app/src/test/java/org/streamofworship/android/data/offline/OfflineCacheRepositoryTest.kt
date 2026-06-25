package org.streamofworship.android.data.offline

import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import kotlin.io.path.createDirectories
import kotlin.io.path.writeText

class OfflineCacheRepositoryTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    private val testDispatcher = UnconfinedTestDispatcher()

    @Test
    fun `marks completed render artifacts as explicitly available and persists cached files`() =
        runTest {
            val storage = temporaryFolder.newFolder("offline").toPath().resolve("artifacts.json")
            val repository = FileOfflineCacheRepository(storageFile = storage, clockMillis = { 1000L }, ioDispatcher = testDispatcher)

            val available =
                repository.markCompletedArtifacts(
                    CompletedRenderArtifacts(
                        renderJobId = "job-1",
                        audioAvailable = true,
                        videoAvailable = true,
                    ),
                )
            repository.markCached(
                renderJobId = "job-1",
                kind = OfflineArtifactKind.Video,
                localUri = "file:///downloads/job-1.mp4",
                bytesDownloaded = 200L,
                totalBytes = 200L,
                nowEpochMillis = 2000L,
            )

            val reloaded = FileOfflineCacheRepository(storageFile = storage, ioDispatcher = testDispatcher)
            val cached = reloaded.getArtifact("job-1", OfflineArtifactKind.Video)

            assertEquals(listOf(OfflineArtifactKind.Audio, OfflineArtifactKind.Chapters, OfflineArtifactKind.Video), available.map { it.kind }.sortedBy { it.name })
            assertEquals(OfflineArtifactStatus.Cached, cached?.status)
            assertEquals("file:///downloads/job-1.mp4", cached?.localUri)
            assertTrue(cached?.isPlayableOffline == true)
        }

    @Test
    fun `records queued download ids and failures`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath(), ioDispatcher = testDispatcher)

            repository.markQueued("job-1", OfflineArtifactKind.Audio, "https://r2/audio.mp3", "2026-01-01T00:00:00Z", 10L, 1L)
            val failed = repository.markFailed("job-1", OfflineArtifactKind.Audio, "No network", 3L)
            val byDownloadId = repository.findArtifactByDownloadId(10L)

            assertEquals(OfflineArtifactStatus.Failed, failed.status)
            assertEquals("job-1", byDownloadId?.renderJobId)
            assertEquals("No network", failed.failureMessage)
        }

    @Test
    fun `corrupted cache resets cleanly instead of throwing to callers`() =
        runTest {
            val storage = temporaryFolder.newFolder("offline").toPath().resolve("artifacts.json")
            storage.parent?.createDirectories()
            // Simulate a partial write left by a process kill mid-flush.
            storage.writeText("{ this is no longer valid json")

            val repository = FileOfflineCacheRepository(storageFile = storage, ioDispatcher = testDispatcher)

            assertEquals(emptyList<OfflineArtifactMetadata>(), repository.listArtifacts("job-1"))
            assertEquals(null, repository.getArtifact("job-1", OfflineArtifactKind.Audio))
            // Writing after a corrupt read must succeed and recover the cache.
            repository.markQueued(
                renderJobId = "job-1",
                kind = OfflineArtifactKind.Audio,
                remoteUrl = "https://r2/audio.mp3",
                signedUrlExpiresAt = "2026-01-01T00:00:00Z",
                downloadId = 7L,
                nowEpochMillis = 1L,
            )
            assertEquals(
                OfflineArtifactStatus.Queued,
                repository.getArtifact("job-1", OfflineArtifactKind.Audio)?.status,
            )
        }
}
