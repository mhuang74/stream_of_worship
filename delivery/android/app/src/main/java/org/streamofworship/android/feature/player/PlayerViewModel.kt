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
    val playbackError: PlaybackUiError? = null,
    val softwareDecoderWarning: Boolean = false,
) {
    val currentChapter: PlaybackChapter?
        get() = manifest?.chapterAt(positionMillis)

    val currentLine: PlaybackLine?
        get() = manifest?.currentLineAt(positionMillis)
}

data class PlaybackUiError(
    val kind: PlaybackErrorKind,
    val message: String,
) {
    val title: String
        get() =
            when (kind) {
                PlaybackErrorKind.Decoder -> "Playback failed"
                PlaybackErrorKind.Generic -> "Playback"
            }
}

class PlayerViewModel(
    internal val renderJobId: String,
    private val repository: PlaybackRepository,
    controller: PlayerController,
    private val offlineCacheRepository: OfflineCacheRepository? = null,
    private val clock: Clock = Clock.systemUTC(),
    private val scope: CoroutineScope? = null,
    private val tickerMillis: Long = 500,
    private val defaultArtifact: PlaybackArtifact = PlaybackArtifact.Video,
) : ViewModel() {
    // Mutable so the surviving ViewModel can be re-bound to a fresh controller after rotation
    // (the old controller is released by PlayerScreen's DisposableEffect; play/pause/seek
    // commands must target the live ExoPlayer, not the stale one captured at construction).
    private var controller: PlayerController = controller
    private val mutableState = MutableStateFlow(PlayerUiState(artifact = defaultArtifact))
    val uiState: StateFlow<PlayerUiState> = mutableState
    private var ticker: Job? = null
    private var softwareDecoderWarningDismissal: Job? = null
    private val eventListener =
        PlayerController.PlayerEventListener { event ->
            when (event) {
                is PlayerEvent.IsPlayingChanged -> {
                    if (event.isPlaying) startTicker() else stopTicker()
                    syncFromController()
                }
                is PlayerEvent.Error -> {
                    stopTicker()
                    mutableState.update {
                        it.copy(
                            isLoading = false,
                            isPlaying = false,
                            message = null,
                            playbackError = event.toPlaybackUiError(),
                        )
                    }
                }
                is PlayerEvent.VideoDecoderChanged -> {
                    updateSoftwareDecoderWarning(event.softwareDecoderActive)
                }
                PlayerEvent.PositionDiscontinuity -> syncFromController()
            }
        }

    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    init {
        controller.setEventListener(eventListener)
    }

    /**
     * Binds the surviving ViewModel to a (potentially fresh) [PlayerController]. Used by the
     * screen after a configuration change: `viewModel(key = jobId)` returns the retained
     * ViewModel whose previously-bound controller has been released, so any play/pause/seek
     * command would be forwarded to a dead ExoPlayer. The rebind re-applies the event listener
     * on the new controller so playback/position/error events still reach the UI.
     *
     * No-op when [newController] is the same instance already bound (e.g. first navigation,
     * where the constructor already wired the listener).
     */
    fun bindController(newController: PlayerController) {
        if (newController === controller) return
        controller = newController
        newController.setEventListener(eventListener)
    }

    fun load(artifact: PlaybackArtifact = defaultArtifact) {
        launchScope.launch {
            mutableState.update {
                it.copy(
                    isLoading = true,
                    artifact = artifact,
                    mediaUrl = null,
                    offlineState = OfflinePlaybackState.Unknown,
                    message = null,
                    playbackError = null,
                    softwareDecoderWarning = false,
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
                        playbackError = null,
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
                mutableState.update {
                    it.copy(
                        isLoading = false,
                        message = error.statusMessage(),
                        playbackError = null,
                    )
                }
            }
        }
    }

    fun retryPlayback() {
        val state = mutableState.value
        val url = state.mediaUrl
        if (url == null) {
            mutableState.update { it.copy(playbackError = null, message = null, isLoading = true) }
            load(state.artifact)
            return
        }

        val resumePosition = state.positionMillis
        mutableState.update {
            it.copy(
                playbackError = null,
                message = null,
                isLoading = false,
            )
        }
        controller.setMedia(url, state.artifact == PlaybackArtifact.Video)
        if (resumePosition > 0L) {
            controller.seekTo(resumePosition)
        }
        controller.play()
        syncFromController()
        startTicker()
    }

    fun dismissPlaybackError() {
        mutableState.update { it.copy(playbackError = null) }
    }

    fun playPause() {
        if (controller.isPlaying) {
            controller.pause()
            syncFromController()
            stopTicker()
        } else {
            controller.play()
            syncFromController()
            // Start the ticker unconditionally so the UI tracks playback even before the
            // service-bound controller reports STATE_READY / isPlaying=true. The loop self
            // terminates when playback ends, and the Player.Listener wired from the ExoPlayer
            // re-arms and stops the ticker on asynchronous state changes.
            startTicker()
        }
    }

    /**
     * Pauses playback unconditionally. Used by the screen's lifecycle observer when the app
     * goes to the background (video playback has no background-audio requirement).
     */
    fun pause() {
        if (controller.isPlaying) {
            controller.pause()
            syncFromController()
            stopTicker()
        }
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
                // Tick at a coarse cadence while playback is active. The Player.Listener
                // wired from the ExoPlayer drives the immediate state transitions; the loop
                // is only a coarse fallback that terminates itself once playback stops.
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

    private fun updateSoftwareDecoderWarning(active: Boolean) {
        softwareDecoderWarningDismissal?.cancel()
        softwareDecoderWarningDismissal = null
        if (!active) {
            mutableState.update { it.copy(softwareDecoderWarning = false) }
            return
        }
        mutableState.update { it.copy(softwareDecoderWarning = true) }
        softwareDecoderWarningDismissal =
            launchScope.launch {
                delay(SOFTWARE_DECODER_WARNING_MILLIS)
                mutableState.update { it.copy(softwareDecoderWarning = false) }
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
        stopTicker()
        softwareDecoderWarningDismissal?.cancel()
        controller.setEventListener(null)
        controller.release()
        super.onCleared()
    }
}

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Playback failed"
    }

private fun PlayerEvent.Error.toPlaybackUiError(): PlaybackUiError =
    PlaybackUiError(
        kind = kind,
        message =
            when (kind) {
                PlaybackErrorKind.Decoder ->
                    "The video format is not supported on this device. Older renders may need to be rendered again."
                PlaybackErrorKind.Generic -> message
            },
    )

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

private const val SOFTWARE_DECODER_WARNING_MILLIS = 5_000L

private fun SignedUrlResponse.isExpired(clock: Clock): Boolean =
    runCatching {
        val expiry = Instant.parse(expiresAt)
        expiry.isBefore(Instant.now(clock)) || expiry == Instant.now(clock)
    }.getOrDefault(true)
