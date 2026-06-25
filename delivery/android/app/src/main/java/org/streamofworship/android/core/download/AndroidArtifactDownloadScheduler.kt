package org.streamofworship.android.core.download

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import kotlinx.coroutines.runBlocking
import org.streamofworship.android.data.offline.FileOfflineCacheRepository
import org.streamofworship.android.data.offline.OfflineArtifactKind

class AndroidArtifactDownloadScheduler(
    context: Context,
) : ArtifactDownloadScheduler {
    private val appContext = context.applicationContext

    override suspend fun enqueue(request: ArtifactDownloadRequest): Long? {
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
        return appContext
            .getSystemService(DownloadManager::class.java)
            .enqueue(downloadRequest)
    }

    companion object {
        fun tagFor(
            renderJobId: String,
            kind: OfflineArtifactKind,
        ): String = "artifact-download-$renderJobId-${kind.name}"
    }
}

class ArtifactDownloadCompletionReceiver : BroadcastReceiver() {
    override fun onReceive(
        context: Context,
        intent: Intent,
    ) {
        if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
        val downloadId = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L).takeIf { it >= 0L } ?: return
        val appContext = context.applicationContext
        val manager = appContext.getSystemService(DownloadManager::class.java)
        val repository = FileOfflineCacheRepository(appContext)
        runBlocking {
            val metadata = repository.findArtifactByDownloadId(downloadId) ?: return@runBlocking
            manager.query(DownloadManager.Query().setFilterById(downloadId)).use { cursor ->
                if (!cursor.moveToFirst()) {
                    repository.markFailed(metadata.renderJobId, metadata.kind, "Download result was unavailable.", System.currentTimeMillis())
                    return@runBlocking
                }
                val status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                val bytes = cursor.getLongOrNull(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR)
                val totalBytes = cursor.getLongOrNull(DownloadManager.COLUMN_TOTAL_SIZE_BYTES)?.takeIf { it >= 0L }
                if (status == DownloadManager.STATUS_SUCCESSFUL) {
                    val localUri = cursor.getStringOrNull(DownloadManager.COLUMN_LOCAL_URI)
                    if (localUri.isNullOrBlank()) {
                        repository.markFailed(metadata.renderJobId, metadata.kind, "Downloaded file location was unavailable.", System.currentTimeMillis())
                    } else {
                        repository.markCached(metadata.renderJobId, metadata.kind, localUri, bytes ?: 0L, totalBytes, System.currentTimeMillis())
                    }
                } else {
                    repository.markFailed(metadata.renderJobId, metadata.kind, "Download failed with status $status.", System.currentTimeMillis())
                }
            }
        }
    }
}

private fun android.database.Cursor.getLongOrNull(columnName: String): Long? {
    val index = getColumnIndex(columnName)
    return if (index >= 0 && !isNull(index)) getLong(index) else null
}

private fun android.database.Cursor.getStringOrNull(columnName: String): String? {
    val index = getColumnIndex(columnName)
    return if (index >= 0 && !isNull(index)) getString(index) else null
}
