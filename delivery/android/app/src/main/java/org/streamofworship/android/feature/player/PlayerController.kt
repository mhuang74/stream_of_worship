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

    fun setEventListener(listener: PlayerEventListener?) {}

    fun interface PlayerEventListener {
        fun onEvent(event: PlayerEvent)
    }
}

sealed interface PlayerEvent {
    data class IsPlayingChanged(val isPlaying: Boolean) : PlayerEvent

    data class Error(val message: String) : PlayerEvent

    data object PositionDiscontinuity : PlayerEvent
}
