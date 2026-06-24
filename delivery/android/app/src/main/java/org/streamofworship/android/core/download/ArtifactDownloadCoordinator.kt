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

    suspend fun applyResult(
        renderJobId: String,
        kind: org.streamofworship.android.data.offline.OfflineArtifactKind,
        result: ArtifactDownloadResult,
    ): OfflineArtifactMetadata =
        when (result) {
            is ArtifactDownloadResult.Queued ->
                cacheRepository.markQueued(
                    renderJobId = renderJobId,
                    kind = kind,
                    remoteUrl = "",
                    signedUrlExpiresAt = null,
                    downloadId = result.downloadId,
                    nowEpochMillis = clockMillis(),
                )
            is ArtifactDownloadResult.Downloading ->
                cacheRepository.markDownloading(
                    renderJobId = renderJobId,
                    kind = kind,
                    bytesDownloaded = result.progress.bytesDownloaded,
                    totalBytes = result.progress.totalBytes,
                    nowEpochMillis = clockMillis(),
                )
            is ArtifactDownloadResult.Completed ->
                cacheRepository.markCached(
                    renderJobId = renderJobId,
                    kind = kind,
                    localUri = result.localUri,
                    bytesDownloaded = result.bytesDownloaded,
                    totalBytes = result.totalBytes,
                    nowEpochMillis = clockMillis(),
                )
            is ArtifactDownloadResult.Failed ->
                cacheRepository.markFailed(
                    renderJobId = renderJobId,
                    kind = kind,
                    message = result.message,
                    nowEpochMillis = clockMillis(),
                )
        }
}
