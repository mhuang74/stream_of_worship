package org.streamofworship.android.feature.share

import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment

enum class RenderArtifactKind(
    val mimeType: String,
    val extension: String,
) {
    Audio("audio/mpeg", "mp3"),
    Video("video/mp4", "mp4"),
}

fun buildShareTextIntent(shareUrl: String): Intent =
    Intent(Intent.ACTION_SEND)
        .setType("text/plain")
        .putExtra(Intent.EXTRA_TEXT, shareUrl)

fun buildViewArtifactIntent(
    url: String,
    kind: RenderArtifactKind,
): Intent =
    Intent(Intent.ACTION_VIEW)
        .setDataAndType(Uri.parse(url), kind.mimeType)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

fun enqueueArtifactDownload(
    context: Context,
    url: String,
    title: String,
    kind: RenderArtifactKind,
): Long {
    val request =
        DownloadManager.Request(Uri.parse(url))
            .setTitle(title)
            .setMimeType(kind.mimeType)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "$title.${kind.extension}")
    val manager = context.getSystemService(DownloadManager::class.java)
    return manager.enqueue(request)
}
