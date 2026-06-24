package org.streamofworship.android.feature.player

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest
import org.streamofworship.android.data.playback.PlaybackRepository

enum class PlaybackArtifact {
    Video,
    Audio,
}

data class PlayerUiState(
    val artifact: PlaybackArtifact = PlaybackArtifact.Video,
    val mediaUrl: String? = null,
    val manifest: PlaybackManifest? = null,
    val positionMillis: Long = 0L,
    val durationMillis: Long = 0L,
    val isPlaying: Boolean = false,
    val isFullscreen: Boolean = false,
    val isLoading: Boolean = false,
    val message: String? = null,
) {
    val currentChapter: PlaybackChapter?
        get() = manifest?.chapterAt(positionMillis)

    val currentLine: PlaybackLine?
        get() = manifest?.currentLineAt(positionMillis)
}

class PlayerViewModel(
    private val renderJobId: String,
    private val repository: PlaybackRepository,
    private val controller: PlayerController,
    private val scope: CoroutineScope? = null,
    private val tickerMillis: Long = 500,
) : ViewModel() {
    private val mutableState = MutableStateFlow(PlayerUiState())
    val uiState: StateFlow<PlayerUiState> = mutableState
    private var ticker: Job? = null

    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    fun load(artifact: PlaybackArtifact = PlaybackArtifact.Video) {
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, artifact = artifact, message = null) }
            val result =
                runCatching {
                    val signedUrl =
                        if (artifact == PlaybackArtifact.Video) {
                            repository.renderedVideoUrl(renderJobId)
                        } else {
                            repository.renderedAudioUrl(renderJobId)
                        }
                    val manifest = runCatching { repository.chapters(renderJobId) }.getOrNull()
                    controller.setMedia(signedUrl.url, artifact == PlaybackArtifact.Video)
                    signedUrl.url to manifest
                }
            result.onSuccess { (url, manifest) ->
                mutableState.update {
                    it.copy(
                        mediaUrl = url,
                        manifest = manifest,
                        durationMillis = manifest?.totalDurationMillis ?: controller.durationMillis,
                        isLoading = false,
                    )
                }
                startTicker()
            }.onFailure { error ->
                mutableState.update { it.copy(isLoading = false, message = error.statusMessage()) }
            }
        }
    }

    fun playPause() {
        if (controller.isPlaying) {
            controller.pause()
        } else {
            controller.play()
        }
        syncFromController()
    }

    fun seekTo(positionMillis: Long) {
        controller.seekTo(positionMillis.coerceIn(0L, effectiveDuration()))
        syncFromController()
    }

    fun skipBy(deltaMillis: Long) {
        seekTo(mutableState.value.positionMillis + deltaMillis)
    }

    fun nextChapter() {
        val state = mutableState.value
        val next = state.manifest?.chapters?.firstOrNull { it.startMillis > state.positionMillis + 500 }
        next?.let { seekTo(it.startMillis) }
    }

    fun previousChapter() {
        val state = mutableState.value
        val previous =
            state.manifest
                ?.chapters
                ?.lastOrNull { it.startMillis < state.positionMillis - 2_000 }
                ?: state.currentChapter
        previous?.let { seekTo(it.startMillis) }
    }

    fun jumpToChapter(chapter: PlaybackChapter) {
        seekTo(chapter.startMillis)
    }

    fun jumpToLine(line: PlaybackLine) {
        seekTo(line.startMillis)
    }

    fun toggleFullscreen() {
        mutableState.update { it.copy(isFullscreen = !it.isFullscreen) }
    }

    fun setPlaybackSnapshot(
        positionMillis: Long,
        durationMillis: Long,
        isPlaying: Boolean,
    ) {
        mutableState.update {
            it.copy(
                positionMillis = positionMillis.coerceAtLeast(0L),
                durationMillis = durationMillis.coerceAtLeast(0L),
                isPlaying = isPlaying,
            )
        }
    }

    private fun startTicker() {
        if (tickerMillis <= 0) return
        ticker?.cancel()
        ticker =
            launchScope.launch {
                while (true) {
                    syncFromController()
                    delay(tickerMillis)
                }
            }
    }

    private fun syncFromController() {
        mutableState.update {
            it.copy(
                positionMillis = controller.positionMillis,
                durationMillis = maxOf(controller.durationMillis, it.manifest?.totalDurationMillis ?: 0L, it.durationMillis),
                isPlaying = controller.isPlaying,
            )
        }
    }

    private fun effectiveDuration(): Long =
        maxOf(mutableState.value.durationMillis, controller.durationMillis, mutableState.value.manifest?.totalDurationMillis ?: 0L)

    override fun onCleared() {
        ticker?.cancel()
        controller.release()
        super.onCleared()
    }
}

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Playback failed"
    }
