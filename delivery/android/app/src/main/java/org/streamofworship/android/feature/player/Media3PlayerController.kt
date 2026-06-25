package org.streamofworship.android.feature.player

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer

class Media3PlayerController private constructor(
    internal val player: MediaPlayerFacade,
) : PlayerController {
    constructor(context: Context) : this(AndroidExoPlayerFacade(ExoPlayer.Builder(context).build()))

    constructor(player: ExoPlayer) : this(AndroidExoPlayerFacade(player))

    internal constructor(fakePlayer: MediaPlayerFacade, @Suppress("UNUSED_PARAMETER") forTest: Unit = Unit) : this(fakePlayer)

    val exoPlayer: ExoPlayer?
        get() = (player as? AndroidExoPlayerFacade)?.exoPlayer

    override val durationMillis: Long
        get() = player.durationMillis.takeIf { it > 0 } ?: 0L

    override val positionMillis: Long
        get() = player.positionMillis.coerceAtLeast(0L)

    override val isPlaying: Boolean
        get() = player.isPlaying

    override fun setMedia(
        url: String,
        isVideo: Boolean,
    ) {
        player.setMedia(url)
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

internal interface MediaPlayerFacade {
    val durationMillis: Long

    val positionMillis: Long

    val isPlaying: Boolean

    fun setMedia(url: String)

    fun prepare()

    fun play()

    fun pause()

    fun seekTo(positionMillis: Long)

    fun release()
}

private class AndroidExoPlayerFacade(
    val exoPlayer: ExoPlayer,
) : MediaPlayerFacade {
    override val durationMillis: Long
        get() = exoPlayer.duration

    override val positionMillis: Long
        get() = exoPlayer.currentPosition

    override val isPlaying: Boolean
        get() = exoPlayer.isPlaying

    override fun setMedia(url: String) {
        exoPlayer.setMediaItem(MediaItem.fromUri(url))
    }

    override fun prepare() {
        exoPlayer.prepare()
    }

    override fun play() {
        exoPlayer.play()
    }

    override fun pause() {
        exoPlayer.pause()
    }

    override fun seekTo(positionMillis: Long) {
        exoPlayer.seekTo(positionMillis)
    }

    override fun release() {
        exoPlayer.release()
    }
}
