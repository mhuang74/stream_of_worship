package org.streamofworship.android.feature.player

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer

/**
 * Builds a foreground ExoPlayer for video playback whose surface can be attached
 * to [androidx.media3.ui.PlayerView]. The surface is driven by the in-process
 * ExoPlayer directly (unlike a [androidx.media3.session.MediaController], which is
 * only a remote command forwarder and cannot render video frames).
 */
@OptIn(UnstableApi::class)
object VideoExoPlayerFactory {
    fun create(context: Context): ExoPlayer =
        ExoPlayer
            .Builder(context.applicationContext)
            .setHandleAudioBecomingNoisy(true)
            .build()
            .apply {
                setWakeMode(C.WAKE_MODE_NETWORK)
                setVideoScalingMode(C.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            }
}
