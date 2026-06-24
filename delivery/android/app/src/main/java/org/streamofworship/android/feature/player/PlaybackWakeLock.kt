package org.streamofworship.android.feature.player

import android.content.Context
import android.os.PowerManager

class PlaybackWakeLock(
    context: Context,
) {
    private val wakeLock =
        (context.getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "StreamOfWorship:Playback",
        )

    fun update(isPlaying: Boolean) {
        if (isPlaying && !wakeLock.isHeld) {
            wakeLock.acquire()
        } else if (!isPlaying && wakeLock.isHeld) {
            wakeLock.release()
        }
    }

    fun release() {
        if (wakeLock.isHeld) wakeLock.release()
    }
}
