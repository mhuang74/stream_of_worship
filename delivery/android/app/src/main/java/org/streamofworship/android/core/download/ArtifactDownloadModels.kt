package org.streamofworship.android.core.download

import org.streamofworship.android.data.offline.OfflineArtifactKind

data class ArtifactDownloadRequest(
    val renderJobId: String,
    val kind: OfflineArtifactKind,
    val url: String,
    val expiresAt: String?,
    val title: String,
)

interface ArtifactDownloadScheduler {
    suspend fun enqueue(request: ArtifactDownloadRequest): Long?
}
