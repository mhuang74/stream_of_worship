package org.streamofworship.android.core.download

import android.app.DownloadManager
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.streamofworship.android.data.offline.OfflineArtifactKind

@RunWith(RobolectricTestRunner::class)
class AndroidArtifactDownloadSchedulerTest {
    @Test
    fun `enqueue submits download manager request and returns id`() =
        runTest {
            val context = ApplicationProvider.getApplicationContext<Context>()
            val scheduler = AndroidArtifactDownloadScheduler(context)

            val downloadId =
                scheduler.enqueue(
                    ArtifactDownloadRequest(
                        renderJobId = "job-1",
                        kind = OfflineArtifactKind.Audio,
                        url = "https://example.com/audio.mp3",
                        expiresAt = "2026-01-01T00:00:00Z",
                        title = "audio-title",
                    ),
                )

            assertNotNull(downloadId)
            val manager = context.getSystemService(DownloadManager::class.java)
            manager.query(DownloadManager.Query().setFilterById(downloadId ?: -1L)).use { cursor ->
                assertEquals(1, cursor.count)
            }
        }

    @Test(expected = IllegalArgumentException::class)
    fun `enqueue rejects malformed urls before metadata is marked queued`() =
        runTest {
            AndroidArtifactDownloadScheduler(ApplicationProvider.getApplicationContext())
                .enqueue(
                    ArtifactDownloadRequest(
                        renderJobId = "job-1",
                        kind = OfflineArtifactKind.Video,
                        url = "not a url",
                        expiresAt = null,
                        title = "video-title",
                    ),
                )
        }
}
