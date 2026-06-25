package org.streamofworship.android

import android.app.Application
import android.app.DownloadManager
import android.content.Context
import android.content.IntentFilter
import android.os.Build
import org.streamofworship.android.core.download.ArtifactDownloadCompletionReceiver

class SowApplication : Application() {
    private val downloadReceiver = ArtifactDownloadCompletionReceiver()
    private var downloadReceiverRegistered = false

    override fun onCreate() {
        super.onCreate()
        // Apps targeting Android 14+ (SDK 35) cannot receive ACTION_DOWNLOAD_COMPLETE via a
        // manifest-declared receiver, so register it at runtime instead.
        val filter = IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(downloadReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(downloadReceiver, filter)
        }
        downloadReceiverRegistered = true
    }

    override fun onTerminate() {
        if (downloadReceiverRegistered) {
            unregisterReceiver(downloadReceiver)
            downloadReceiverRegistered = false
        }
        super.onTerminate()
    }
}
