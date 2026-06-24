package org.streamofworship.android.feature.share

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.data.playback.PlaybackRepository

data class ShareUiState(
    val allowDownload: Boolean = false,
    val shareToken: ShareToken? = null,
    val audioUrl: String? = null,
    val videoUrl: String? = null,
    val isLoading: Boolean = false,
    val message: String? = null,
)

class ShareViewModel(
    private val renderJobId: String,
    private val shareRepository: ShareRepository,
    private val playbackRepository: PlaybackRepository,
    private val scope: CoroutineScope? = null,
) : ViewModel() {
    private val mutableState = MutableStateFlow(ShareUiState())
    val uiState: StateFlow<ShareUiState> = mutableState
    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    fun setAllowDownload(value: Boolean) {
        mutableState.update { it.copy(allowDownload = value) }
    }

    fun createShare() {
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, message = null) }
            runCatching { shareRepository.createRenderShare(renderJobId, mutableState.value.allowDownload) }
                .onSuccess { token -> mutableState.update { it.copy(isLoading = false, shareToken = token) } }
                .onFailure { error -> mutableState.update { it.copy(isLoading = false, message = error.statusMessage()) } }
        }
    }

    fun loadDownloadUrls() {
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, message = null) }
            runCatching {
                val audio = playbackRepository.renderedAudioUrl(renderJobId, "attachment")
                val video = playbackRepository.renderedVideoUrl(renderJobId, "attachment")
                audio.url to video.url
            }.onSuccess { (audio, video) ->
                mutableState.update { it.copy(isLoading = false, audioUrl = audio, videoUrl = video) }
            }.onFailure { error ->
                mutableState.update { it.copy(isLoading = false, message = error.statusMessage()) }
            }
        }
    }
}

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Share request failed"
    }
