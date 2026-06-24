package org.streamofworship.android.feature.player

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer

class Media3PlayerController(
    context: Context,
) : PlayerController {
    val player: ExoPlayer = ExoPlayer.Builder(context).build()

    override val durationMillis: Long
        get() = player.duration.takeIf { it > 0 } ?: 0L

    override val positionMillis: Long
        get() = player.currentPosition.coerceAtLeast(0L)

    override val isPlaying: Boolean
        get() = player.isPlaying

    override fun setMedia(
        url: String,
        isVideo: Boolean,
    ) {
        player.setMediaItem(MediaItem.fromUri(url))
        player.prepare()
    }

    override fun play() {
        player.play()
    }

    override fun pause() {
        player.pause()
    }

    override fun seekTo(positionMillis: Long) {
        player.seekTo(positionMillis.coerceAtLeast(0L))
    }

    override fun release() {
        player.release()
    }
}
