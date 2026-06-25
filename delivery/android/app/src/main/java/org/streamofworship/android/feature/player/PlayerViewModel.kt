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
import org.streamofworship.android.data.offline.OfflineArtifactKind
import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.offline.OfflineCacheRepository
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest
import org.streamofworship.android.data.playback.PlaybackRepository
import org.streamofworship.android.data.playback.SignedUrlResponse
import java.time.Clock
import java.time.Instant

enum class PlaybackArtifact {
    Video,
    Audio,
}

enum class OfflinePlaybackState {
    Unknown,
    Cached,
    Missing,
    ExpiredSignedUrl,
    Remote,
}

data class PlayerUiState(
    val artifact: PlaybackArtifact = PlaybackArtifact.Video,
    val mediaUrl: String? = null,
    val manifest: PlaybackManifest? = null,
    val cachedArtifact: OfflineArtifactMetadata? = null,
    val offlineState: OfflinePlaybackState = OfflinePlaybackState.Unknown,
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
    private val offlineCacheRepository: OfflineCacheRepository? = null,
    private val clock: Clock = Clock.systemUTC(),
    private val scope: CoroutineScope? = null,
    private val tickerMillis: Long = 500,
    private val defaultArtifact: PlaybackArtifact = PlaybackArtifact.Video,
) : ViewModel() {
    private val mutableState = MutableStateFlow(PlayerUiState(artifact = defaultArtifact))
    val uiState: StateFlow<PlayerUiState> = mutableState
    private var ticker: Job? = null

    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    fun load(artifact: PlaybackArtifact = defaultArtifact) {
        launchScope.launch {
            mutableState.update {
                it.copy(
                    isLoading = true,
                    artifact = artifact,
                    mediaUrl = null,
                    offlineState = OfflinePlaybackState.Unknown,
                    message = null,
                )
            }
            val result =
                runCatching {
                    val kind = artifact.offlineKind()
                    val cached = offlineCacheRepository?.getArtifact(renderJobId, kind)
                    val manifest = runCatching { repository.chapters(renderJobId) }.getOrNull()
                    if (cached?.isPlayableOffline == true) {
                        controller.setMedia(cached.localUri.orEmpty(), artifact == PlaybackArtifact.Video)
                        PlaybackLoadResult(
                            url = cached.localUri.orEmpty(),
                            manifest = manifest,
                            cachedArtifact = cached,
                            offlineState = OfflinePlaybackState.Cached,
                        )
                    } else {
                        val signedUrl =
                            if (artifact == PlaybackArtifact.Video) {
                                repository.renderedVideoUrl(renderJobId)
                            } else {
                                repository.renderedAudioUrl(renderJobId)
                            }
                        if (signedUrl.isExpired(clock)) {
                            PlaybackLoadResult(
                                url = null,
                                manifest = manifest,
                                cachedArtifact = cached,
                                offlineState = OfflinePlaybackState.ExpiredSignedUrl,
                            )
                        } else {
                            controller.setMedia(signedUrl.url, artifact == PlaybackArtifact.Video)
                            PlaybackLoadResult(
                                url = signedUrl.url,
                                manifest = manifest,
                                cachedArtifact = cached,
                                offlineState = if (cached == null) OfflinePlaybackState.Missing else OfflinePlaybackState.Remote,
                            )
                        }
                    }
                }
            result.onSuccess { loaded ->
                mutableState.update {
                    it.copy(
                        mediaUrl = loaded.url,
                        manifest = loaded.manifest,
                        cachedArtifact = loaded.cachedArtifact,
                        offlineState = loaded.offlineState,
                        durationMillis = loaded.manifest?.totalDurationMillis ?: controller.durationMillis,
                        isLoading = false,
                        message =
                            when (loaded.offlineState) {
                                OfflinePlaybackState.ExpiredSignedUrl -> "Playback link expired. Retry to refresh it."
                                OfflinePlaybackState.Missing -> "Not cached on this device. Streaming with a fresh link."
                                else -> null
                            },
                    )
                }
                if (loaded.url != null) startTicker()
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
        // Pause the polling ticker while playback is idle/paused to avoid burning CPU and
        // driving unnecessary recompositions while the screen sits on the back stack. The
        // ticker relaunches on the next play.
        if (controller.isPlaying) startTicker() else stopTicker()
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
                // Only tick while the controller reports active playback so the loop self
                // terminates when playback ends or pauses, instead of running forever.
                while (controller.isPlaying) {
                    syncFromController()
                    delay(tickerMillis)
                }
            }
    }

    private fun stopTicker() {
        ticker?.cancel()
        ticker = null
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
        stopTicker()
        controller.release()
        super.onCleared()
    }
}

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Playback failed"
    }

private data class PlaybackLoadResult(
    val url: String?,
    val manifest: PlaybackManifest?,
    val cachedArtifact: OfflineArtifactMetadata?,
    val offlineState: OfflinePlaybackState,
)

private fun PlaybackArtifact.offlineKind(): OfflineArtifactKind =
    when (this) {
        PlaybackArtifact.Video -> OfflineArtifactKind.Video
        PlaybackArtifact.Audio -> OfflineArtifactKind.Audio
    }

private fun SignedUrlResponse.isExpired(clock: Clock): Boolean =
    runCatching { Instant.parse(expiresAt).isBefore(Instant.now(clock)) || Instant.parse(expiresAt) == Instant.now(clock) }
        .getOrDefault(false)
