package org.streamofworship.android.feature.render

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetMaxDurationSeconds
import org.streamofworship.android.core.model.SongsetMaxSongs
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.data.offline.CompletedRenderArtifacts
import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.offline.OfflineCacheRepository
import org.streamofworship.android.data.render.ActiveRenderConflictException
import org.streamofworship.android.data.render.ArtifactSizes
import org.streamofworship.android.data.render.RenderFormConfig
import org.streamofworship.android.data.render.RenderJob
import org.streamofworship.android.data.render.RenderJobStatus
import org.streamofworship.android.data.render.RenderRepository
import org.streamofworship.android.data.songsets.SongsetsRepository

data class RenderUiState(
    val songset: SongsetDetail? = null,
    val config: RenderFormConfig = RenderFormConfig(),
    val currentJob: RenderJob? = null,
    val artifactSizes: ArtifactSizes? = null,
    val isLoadingSongset: Boolean = false,
    val isSubmitting: Boolean = false,
    val isPolling: Boolean = false,
    val retryCount: Int = 0,
    val offlineArtifacts: List<OfflineArtifactMetadata> = emptyList(),
    val requiresPreviousRenderConfirmation: Boolean = false,
    val validationMessage: String? = null,
    val serverMessage: String? = null,
) {
    val canStartRender: Boolean
        get() = validationError(config, songset) == null && !isSubmitting

    val completedJob: RenderJob?
        get() = currentJob?.takeIf { it.status == RenderJobStatus.Completed }

    val hasArtifacts: Boolean
        get() = currentJob?.hasPlayableArtifacts == true
}

class RenderViewModel(
    private val songsetId: String,
    private val songsetsRepository: SongsetsRepository,
    private val renderRepository: RenderRepository,
    private val offlineCacheRepository: OfflineCacheRepository? = null,
    private val scope: CoroutineScope? = null,
    private val pollIntervalMillis: Long = 2_000,
    private val retryDelayMillis: Long = 1_000,
    private val maxRetries: Int = 10,
    private val maxBackoffMillis: Long = 30_000,
) : ViewModel() {
    private val mutableState = MutableStateFlow(RenderUiState())
    val uiState: StateFlow<RenderUiState> = mutableState
    private var pollJob: Job? = null

    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    fun load() {
        launchScope.launch {
            mutableState.update { it.copy(isLoadingSongset = true, serverMessage = null) }
            runCatching { songsetsRepository.getSongset(songsetId) }
                .onSuccess { songset ->
                    mutableState.update {
                        it.copy(
                            songset = songset,
                            isLoadingSongset = false,
                            requiresPreviousRenderConfirmation = songset.lastCompletedRenderJobId != null,
                        )
                    }
                    songset.latestRenderJobId?.let { jobId -> startPolling(jobId) }
                }.onFailure { error ->
                    mutableState.update {
                        it.copy(
                            isLoadingSongset = false,
                            serverMessage = error.message ?: "Failed to load songset",
                        )
                    }
                }
        }
    }

    fun updateConfig(config: RenderFormConfig) {
        mutableState.update {
            it.copy(config = config, validationMessage = validationError(config, it.songset))
        }
    }

    fun requestRender() {
        val state = mutableState.value
        val validation = validationError(state.config, state.songset)
        if (validation != null) {
            mutableState.update { it.copy(validationMessage = validation) }
            return
        }
        if (state.requiresPreviousRenderConfirmation) {
            mutableState.update { it.copy(validationMessage = "Confirm before replacing the previous render.") }
            return
        }
        submitRender()
    }

    fun confirmPreviousRenderAndStart() {
        mutableState.update { it.copy(requiresPreviousRenderConfirmation = false, validationMessage = null) }
        submitRender()
    }

    fun startPolling(
        jobId: String,
        initialMessage: String? = null,
    ) {
        pollJob?.cancel()
        pollJob =
            launchScope.launch {
                mutableState.update { it.copy(isPolling = true, retryCount = 0, serverMessage = initialMessage) }
                var retries = 0
                // Track whether the initial conflict message has been shown for exactly one
                // iteration so it does not persist for the entire render lifetime.
                var firstResponse = true
                while (true) {
                    val result = runCatching { renderRepository.getRenderJob(jobId) }
                    val job = result.getOrNull()
                    if (job != null) {
                        retries = 0
                        mutableState.update {
                            it.copy(
                                currentJob = job,
                                isPolling = job.isActive,
                                retryCount = 0,
                                serverMessage = if (firstResponse && initialMessage != null) it.serverMessage else null,
                            )
                        }
                        firstResponse = false
                        if (!job.isActive) {
                            if (job.status == RenderJobStatus.Completed) {
                                loadArtifactSizes(job.id)
                            }
                            break
                        }
                        delay(pollIntervalMillis)
                    } else {
                        retries += 1
                        if (retries > maxRetries) {
                            mutableState.update {
                                it.copy(
                                    isPolling = false,
                                    retryCount = retries,
                                    serverMessage =
                                        result.exceptionOrNull()?.statusMessage()
                                            ?: "Status unavailable. Retry manually.",
                                )
                            }
                            break
                        }
                        val backoff =
                            (retryDelayMillis * (1L shl (retries - 1).coerceAtMost(5)))
                                .coerceAtMost(maxBackoffMillis)
                        mutableState.update {
                            it.copy(
                                isPolling = true,
                                retryCount = retries,
                                serverMessage = result.exceptionOrNull()?.statusMessage() ?: "Render status unavailable",
                            )
                        }
                        delay(backoff)
                    }
                }
            }
    }

    fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
        mutableState.update { it.copy(isPolling = false) }
    }

    fun cancelRender() {
        val jobId = mutableState.value.currentJob?.id ?: return
        launchScope.launch {
            runCatching { renderRepository.cancelRenderJob(jobId) }
                .onSuccess { cancelled ->
                    stopPolling()
                    mutableState.update { it.copy(currentJob = cancelled, serverMessage = null) }
                }.onFailure { error ->
                    mutableState.update { it.copy(serverMessage = error.statusMessage()) }
                }
        }
    }

    fun retryPolling() {
        mutableState.value.currentJob?.id?.let { startPolling(it) }
    }

    private fun submitRender() {
        launchScope.launch {
            mutableState.update { it.copy(isSubmitting = true, validationMessage = null, serverMessage = null) }
            runCatching { renderRepository.createRenderJob(songsetId, mutableState.value.config) }
                .onSuccess { job ->
                    mutableState.update { it.copy(isSubmitting = false, currentJob = job) }
                    startPolling(job.id)
                }.onFailure { error ->
                    if (error is ActiveRenderConflictException && error.conflict.jobId != null) {
                        mutableState.update {
                            it.copy(
                                isSubmitting = false,
                                serverMessage = error.conflict.message,
                            )
                        }
                        startPolling(error.conflict.jobId, initialMessage = error.conflict.message)
                    } else {
                        mutableState.update {
                            it.copy(isSubmitting = false, serverMessage = error.statusMessage())
                        }
                    }
                }
        }
    }

    private fun loadArtifactSizes(jobId: String) {
        launchScope.launch {
            runCatching { renderRepository.getArtifactSizes(jobId) }
                .onSuccess { sizes ->
                    val artifacts =
                        offlineCacheRepository?.markCompletedArtifacts(
                            CompletedRenderArtifacts(
                                renderJobId = jobId,
                                audioAvailable = sizes.mp3SizeBytes != null,
                                videoAvailable = sizes.mp4SizeBytes != null,
                            ),
                        ).orEmpty()
                    mutableState.update { it.copy(artifactSizes = sizes, offlineArtifacts = artifacts) }
                }
                .onFailure { error -> mutableState.update { it.copy(serverMessage = error.statusMessage()) } }
        }
    }

    override fun onCleared() {
        stopPolling()
        super.onCleared()
    }
}

fun validationError(
    config: RenderFormConfig,
    songset: SongsetDetail?,
): String? =
    when {
        !config.audioEnabled && !config.videoEnabled -> "Select audio, video, or both."
        songset == null -> null
        songset.items.size > SongsetMaxSongs -> "Songset exceeds maximum of $SongsetMaxSongs songs."
        (songset.durationSeconds ?: 0.0) > SongsetMaxDurationSeconds ->
            "Songset exceeds maximum duration of 25 minutes."
        config.includeTitleCard && config.titleCardDurationSeconds !in 5..30 ->
            "Title card duration must be between 5 and 30 seconds."
        config.includeTitleCard && config.titleCardLines.any { it.length > 200 } ->
            "Title card lines must be 200 characters or fewer."
        config.includeTitleCard && config.titleCardLines.size > 20 ->
            "Title card supports up to 20 lines."
        else -> null
    }

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Render request failed"
    }
