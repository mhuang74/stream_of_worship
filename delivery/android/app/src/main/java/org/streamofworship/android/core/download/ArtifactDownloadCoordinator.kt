package org.streamofworship.android.core.download

import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.offline.OfflineArtifactStatus
import org.streamofworship.android.data.offline.OfflineCacheRepository

class ArtifactDownloadCoordinator(
    private val cacheRepository: OfflineCacheRepository,
    private val scheduler: ArtifactDownloadScheduler,
    private val clockMillis: () -> Long = { System.currentTimeMillis() },
) {
    suspend fun enqueue(request: ArtifactDownloadRequest): OfflineArtifactMetadata {
        // Persist the queued metadata BEFORE asking DownloadManager to enqueue the request so
        // the completion receiver can always find the row even if the broadcast arrives before
        // the download id has been recorded (small/fast downloads, retries on already-cached
        // files). The receiver also falls back to the renderJobId+kind encoded in the title.
        val queued =
            cacheRepository.markQueued(
                renderJobId = request.renderJobId,
                kind = request.kind,
                remoteUrl = request.url,
                signedUrlExpiresAt = request.expiresAt,
                downloadId = null,
                nowEpochMillis = clockMillis(),
            )
        return try {
            val downloadId = scheduler.enqueue(request)
            cacheRepository.upsert(
                queued.copy(
                    status = OfflineArtifactStatus.Queued,
                    downloadId = downloadId,
                    updatedAtEpochMillis = clockMillis(),
                ),
            )
        } catch (error: Throwable) {
            cacheRepository.markFailed(
                renderJobId = request.renderJobId,
                kind = request.kind,
                message = error.message ?: "Download could not be queued.",
                nowEpochMillis = clockMillis(),
            )
        }
    }
}
