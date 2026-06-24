package org.streamofworship.android.feature.share

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.streamofworship.android.core.download.ArtifactDownloadCoordinator
import org.streamofworship.android.core.download.ArtifactDownloadRequest
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.data.offline.OfflineArtifactKind
import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.playback.PlaybackRepository

data class ShareUiState(
    val allowDownload: Boolean = false,
    val shareToken: ShareToken? = null,
    val audioUrl: String? = null,
    val videoUrl: String? = null,
    val downloads: Map<OfflineArtifactKind, OfflineArtifactMetadata> = emptyMap(),
    val isLoading: Boolean = false,
    val message: String? = null,
)

class ShareViewModel(
    private val renderJobId: String,
    private val shareRepository: ShareRepository,
    private val playbackRepository: PlaybackRepository,
    private val downloadCoordinator: ArtifactDownloadCoordinator? = null,
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
                val audioDownload =
                    downloadCoordinator?.enqueue(
                        ArtifactDownloadRequest(
                            renderJobId = renderJobId,
                            kind = OfflineArtifactKind.Audio,
                            url = audio.url,
                            expiresAt = audio.expiresAt,
                            title = "stream-of-worship-$renderJobId-audio",
                        ),
                    )
                val videoDownload =
                    downloadCoordinator?.enqueue(
                        ArtifactDownloadRequest(
                            renderJobId = renderJobId,
                            kind = OfflineArtifactKind.Video,
                            url = video.url,
                            expiresAt = video.expiresAt,
                            title = "stream-of-worship-$renderJobId-video",
                        ),
                    )
                DownloadUrls(audio.url, video.url, listOfNotNull(audioDownload, videoDownload))
            }.onSuccess { downloads ->
                mutableState.update {
                    it.copy(
                        isLoading = false,
                        audioUrl = downloads.audioUrl,
                        videoUrl = downloads.videoUrl,
                        downloads = downloads.metadata.associateBy { metadata -> metadata.kind },
                    )
                }
            }.onFailure { error ->
                mutableState.update { it.copy(isLoading = false, message = error.statusMessage()) }
            }
        }
    }
}

private data class DownloadUrls(
    val audioUrl: String,
    val videoUrl: String,
    val metadata: List<OfflineArtifactMetadata>,
)

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Share request failed"
    }
