package org.streamofworship.android.core.download

import org.streamofworship.android.data.offline.OfflineArtifactKind

data class ArtifactDownloadRequest(
    val renderJobId: String,
    val kind: OfflineArtifactKind,
    val url: String,
    val expiresAt: String?,
    val title: String,
) {
    companion object {
        const val TITLE_PREFIX = "stream-of-worship-"
    }
}

interface ArtifactDownloadScheduler {
    suspend fun enqueue(request: ArtifactDownloadRequest): Long?
}

/**
 * Build the canonical, human-readable DownloadManager notification title for an artifact.
 * The title is parseable back into (renderJobId, kind) via [parseArtifactDownloadTitle]
 * so the completion receiver can recover ownership without relying solely on the download id.
 */
fun ArtifactDownloadRequest.canonicalTitle(): String =
    "${ArtifactDownloadRequest.TITLE_PREFIX}$renderJobId-${kind.name.lowercase()}"

/**
 * Recover the (renderJobId, kind) pair encoded by [canonicalTitle]. Returns null when the
 * title does not match the expected shape, so callers can fall back to download-id lookup.
 */
fun parseArtifactDownloadTitle(title: String): Pair<String, OfflineArtifactKind>? {
    if (!title.startsWith(ArtifactDownloadRequest.TITLE_PREFIX)) return null
    val body = title.removePrefix(ArtifactDownloadRequest.TITLE_PREFIX)
    return OfflineArtifactKind.entries.firstNotNullOfOrNull { kind ->
        val suffix = "-${kind.name.lowercase()}"
        if (body.endsWith(suffix)) body.removeSuffix(suffix) to kind else null
    }
}
