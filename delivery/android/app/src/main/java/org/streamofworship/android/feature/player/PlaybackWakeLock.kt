package org.streamofworship.android.feature.player

import android.content.Context
import android.os.PowerManager

class PlaybackWakeLock private constructor(
    private val handle: WakeLockHandle,
) {
    constructor(context: Context) : this(
        AndroidWakeLockHandle(
            (context.getSystemService(Context.POWER_SERVICE) as PowerManager).newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                "StreamOfWorship:Playback",
            ),
        ),
    )

    internal constructor(handle: WakeLockHandle, @Suppress("UNUSED_PARAMETER") forTest: Unit = Unit) : this(handle)

    fun update(isPlaying: Boolean) {
        if (isPlaying && !handle.isHeld) {
            handle.acquire()
        } else if (!isPlaying && handle.isHeld) {
            handle.release()
        }
    }

    fun release() {
        if (handle.isHeld) handle.release()
    }
}

internal interface WakeLockHandle {
    val isHeld: Boolean

    fun acquire()

    fun release()
}

private class AndroidWakeLockHandle(
    private val wakeLock: PowerManager.WakeLock,
) : WakeLockHandle {
    override val isHeld: Boolean
        get() = wakeLock.isHeld

    override fun acquire() {
        wakeLock.acquire()
    }

    override fun release() {
        wakeLock.release()
    }
}
