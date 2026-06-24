package org.streamofworship.android.data.offline

import kotlinx.serialization.Serializable

enum class OfflineArtifactKind(
    val apiName: String,
    val extension: String,
    val mimeType: String,
) {
    Audio("audio", "mp3", "audio/mpeg"),
    Video("video", "mp4", "video/mp4"),
    Chapters("json", "json", "application/json"),
}

enum class OfflineArtifactStatus {
    Available,
    Queued,
    Downloading,
    Cached,
    Failed,
    Stale,
}

@Serializable
data class OfflineArtifactMetadata(
    val renderJobId: String,
    val kind: OfflineArtifactKind,
    val status: OfflineArtifactStatus,
    val localUri: String? = null,
    val remoteUrl: String? = null,
    val signedUrlExpiresAt: String? = null,
    val downloadId: Long? = null,
    val bytesDownloaded: Long = 0L,
    val totalBytes: Long? = null,
    val failureMessage: String? = null,
    val updatedAtEpochMillis: Long,
) {
    val cacheKey: String
        get() = cacheKey(renderJobId, kind)

    val isPlayableOffline: Boolean
        get() = status == OfflineArtifactStatus.Cached && localUri != null

    companion object {
        fun cacheKey(
            renderJobId: String,
            kind: OfflineArtifactKind,
        ): String = "${renderJobId}:${kind.name}"
    }
}

data class CompletedRenderArtifacts(
    val renderJobId: String,
    val audioAvailable: Boolean,
    val videoAvailable: Boolean,
    val chaptersAvailable: Boolean = true,
)
