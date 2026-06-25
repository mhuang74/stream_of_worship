package org.streamofworship.android.core.download

import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.offline.OfflineCacheRepository

class ArtifactDownloadCoordinator(
    private val cacheRepository: OfflineCacheRepository,
    private val scheduler: ArtifactDownloadScheduler,
    private val clockMillis: () -> Long = { System.currentTimeMillis() },
) {
    suspend fun enqueue(request: ArtifactDownloadRequest): OfflineArtifactMetadata =
        try {
            val downloadId = scheduler.enqueue(request)
            cacheRepository.markQueued(
                renderJobId = request.renderJobId,
                kind = request.kind,
                remoteUrl = request.url,
                signedUrlExpiresAt = request.expiresAt,
                downloadId = downloadId,
                nowEpochMillis = clockMillis(),
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
