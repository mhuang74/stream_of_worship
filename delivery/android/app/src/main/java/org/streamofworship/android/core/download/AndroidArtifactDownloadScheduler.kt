package org.streamofworship.android.core.download

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
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

/**
 * Receives [DownloadManager.ACTION_DOWNLOAD_COMPLETE] broadcasts. Registered at runtime by
 * [org.streamofworship.android.SowApplication] because apps targeting Android 14+ (SDK 35)
 * cannot receive this system broadcast via a manifest-declared receiver. Work is dispatched
 * off the main thread via [goAsync] so disk I/O never blocks the broadcast quota.
 */
class ArtifactDownloadCompletionReceiver : BroadcastReceiver() {
    override fun onReceive(
        context: Context,
        intent: Intent,
    ) {
        if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
        val downloadId = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L).takeIf { it >= 0L } ?: return
        val pendingResult = goAsync()
        val appContext = context.applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            try {
                handleDownloadComplete(appContext, downloadId)
            } catch (_: Throwable) {
                // Swallow errors so the receiver never crashes the process; the next attempt
                // or manual retry will re-evaluate cache state.
            } finally {
                pendingResult.finish()
            }
        }
    }

    private suspend fun handleDownloadComplete(
        context: Context,
        downloadId: Long,
    ) {
        val manager = context.getSystemService(DownloadManager::class.java)
        val repository = FileOfflineCacheRepository(context)
        manager.query(DownloadManager.Query().setFilterById(downloadId)).use { cursor ->
            if (!cursor.moveToFirst()) return
            val status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
            val bytes = cursor.getLongOrNull(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR)
            val totalBytes = cursor.getLongOrNull(DownloadManager.COLUMN_TOTAL_SIZE_BYTES)?.takeIf { it >= 0L }
            // Look up the cached metadata by the recorded download id first. Fall back to the
            // renderJobId+kind encoded in the download title when the id has not been persisted
            // yet (closes the enqueue/markQueued race window for instantaneous downloads).
            val metadata =
                repository.findArtifactByDownloadId(downloadId)
                    ?: cursor
                        .getStringOrNull(DownloadManager.COLUMN_TITLE)
                        ?.let(::parseArtifactDownloadTitle)
                        ?.let { (renderJobId, kind) -> repository.getArtifact(renderJobId, kind) }
                        ?.takeIf { it.downloadId == null || it.downloadId == downloadId }
                        ?: return
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

private fun android.database.Cursor.getLongOrNull(columnName: String): Long? {
    val index = getColumnIndex(columnName)
    return if (index >= 0 && !isNull(index)) getLong(index) else null
}

private fun android.database.Cursor.getStringOrNull(columnName: String): String? {
    val index = getColumnIndex(columnName)
    return if (index >= 0 && !isNull(index)) getString(index) else null
}
