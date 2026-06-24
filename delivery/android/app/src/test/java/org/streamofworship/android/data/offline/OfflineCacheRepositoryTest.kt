package org.streamofworship.android.data.offline

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class OfflineCacheRepositoryTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun `marks completed render artifacts as explicitly available and persists cached files`() =
        runTest {
            val storage = temporaryFolder.newFolder("offline").toPath().resolve("artifacts.json")
            val repository = FileOfflineCacheRepository(storageFile = storage, clockMillis = { 1000L })

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

            val reloaded = FileOfflineCacheRepository(storageFile = storage)
            val cached = reloaded.getArtifact("job-1", OfflineArtifactKind.Video)

            assertEquals(listOf(OfflineArtifactKind.Audio, OfflineArtifactKind.Chapters, OfflineArtifactKind.Video), available.map { it.kind }.sortedBy { it.name })
            assertEquals(OfflineArtifactStatus.Cached, cached?.status)
            assertEquals("file:///downloads/job-1.mp4", cached?.localUri)
            assertTrue(cached?.isPlayableOffline == true)
        }

    @Test
    fun `records download progress and failures`() =
        runTest {
            val repository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath())

            repository.markQueued("job-1", OfflineArtifactKind.Audio, "https://r2/audio.mp3", "2026-01-01T00:00:00Z", 10L, 1L)
            repository.markDownloading("job-1", OfflineArtifactKind.Audio, 50L, 100L, 2L)
            val failed = repository.markFailed("job-1", OfflineArtifactKind.Audio, "No network", 3L)

            assertEquals(OfflineArtifactStatus.Failed, failed.status)
            assertEquals(50L, failed.bytesDownloaded)
            assertEquals("No network", failed.failureMessage)
        }
}
