package org.streamofworship.android.core.download

import org.streamofworship.android.data.offline.OfflineArtifactKind

data class ArtifactDownloadRequest(
    val renderJobId: String,
    val kind: OfflineArtifactKind,
    val url: String,
    val expiresAt: String?,
    val title: String,
)

data class ArtifactDownloadProgress(
    val renderJobId: String,
    val kind: OfflineArtifactKind,
    val bytesDownloaded: Long,
    val totalBytes: Long?,
) {
    val progressFraction: Float?
        get() = totalBytes?.takeIf { it > 0L }?.let { (bytesDownloaded.toFloat() / it).coerceIn(0f, 1f) }
}

sealed interface ArtifactDownloadResult {
    data class Queued(val downloadId: Long?) : ArtifactDownloadResult

    data class Downloading(val progress: ArtifactDownloadProgress) : ArtifactDownloadResult

    data class Completed(
        val localUri: String,
        val bytesDownloaded: Long,
        val totalBytes: Long?,
    ) : ArtifactDownloadResult

    data class Failed(val message: String) : ArtifactDownloadResult
}

interface ArtifactDownloadScheduler {
    suspend fun enqueue(request: ArtifactDownloadRequest): Long?
}
