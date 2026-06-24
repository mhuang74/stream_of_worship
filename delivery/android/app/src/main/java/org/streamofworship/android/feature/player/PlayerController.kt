package org.streamofworship.android.feature.player

interface PlayerController {
    val durationMillis: Long
    val positionMillis: Long
    val isPlaying: Boolean

    fun setMedia(
        url: String,
        isVideo: Boolean,
    )

    fun play()

    fun pause()

    fun seekTo(positionMillis: Long)

    fun release()
}
