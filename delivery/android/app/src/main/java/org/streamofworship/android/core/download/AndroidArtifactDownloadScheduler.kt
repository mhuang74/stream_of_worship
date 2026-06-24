package org.streamofworship.android.core.download

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.streamofworship.android.data.offline.OfflineArtifactKind

class AndroidArtifactDownloadScheduler(
    context: Context,
) : ArtifactDownloadScheduler {
    private val appContext = context.applicationContext

    override suspend fun enqueue(request: ArtifactDownloadRequest): Long? {
        val work =
            OneTimeWorkRequestBuilder<ArtifactDownloadWorker>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build(),
                )
                .setInputData(request.toWorkData())
                .addTag(tagFor(request.renderJobId, request.kind))
                .build()
        WorkManager.getInstance(appContext).enqueue(work)
        return null
    }

    companion object {
        fun tagFor(
            renderJobId: String,
            kind: OfflineArtifactKind,
        ): String = "artifact-download-$renderJobId-${kind.name}"
    }
}

class ArtifactDownloadWorker(
    context: Context,
    params: WorkerParameters,
) : Worker(context, params) {
    override fun doWork(): Result {
        val request = inputData.toArtifactDownloadRequest() ?: return Result.failure()
        return try {
            val downloadRequest =
                DownloadManager.Request(Uri.parse(request.url))
                    .setTitle(request.title)
                    .setMimeType(request.kind.mimeType)
                    .setAllowedOverMetered(true)
                    .setAllowedOverRoaming(false)
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalPublicDir(
                        Environment.DIRECTORY_DOWNLOADS,
                        "${request.title}.${request.kind.extension}",
                    )
            applicationContext
                .getSystemService(DownloadManager::class.java)
                .enqueue(downloadRequest)
            Result.success()
        } catch (_: IllegalArgumentException) {
            Result.failure()
        } catch (_: SecurityException) {
            Result.failure()
        }
    }
}

private const val KeyRenderJobId = "renderJobId"
private const val KeyKind = "kind"
private const val KeyUrl = "url"
private const val KeyExpiresAt = "expiresAt"
private const val KeyTitle = "title"

private fun ArtifactDownloadRequest.toWorkData(): Data =
    Data.Builder()
        .putString(KeyRenderJobId, renderJobId)
        .putString(KeyKind, kind.name)
        .putString(KeyUrl, url)
        .putString(KeyExpiresAt, expiresAt)
        .putString(KeyTitle, title)
        .build()

private fun Data.toArtifactDownloadRequest(): ArtifactDownloadRequest? {
    val renderJobId = getString(KeyRenderJobId)?.takeIf { it.isNotBlank() } ?: return null
    val kind = getString(KeyKind)?.let { runCatching { OfflineArtifactKind.valueOf(it) }.getOrNull() } ?: return null
    val url = getString(KeyUrl)?.takeIf { it.isNotBlank() } ?: return null
    val title = getString(KeyTitle)?.takeIf { it.isNotBlank() } ?: "Stream of Worship $renderJobId"
    return ArtifactDownloadRequest(
        renderJobId = renderJobId,
        kind = kind,
        url = url,
        expiresAt = getString(KeyExpiresAt),
        title = title,
    )
}
