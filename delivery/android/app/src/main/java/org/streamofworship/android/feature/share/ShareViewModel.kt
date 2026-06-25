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
import org.streamofworship.android.core.download.canonicalTitle
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.data.offline.OfflineArtifactKind
import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.offline.OfflineArtifactStatus
import org.streamofworship.android.data.playback.PlaybackRepository
import org.streamofworship.android.data.render.RenderRepository

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
    private val renderRepository: RenderRepository,
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
        if (mutableState.value.isLoading) return
        mutableState.update { it.copy(isLoading = true, message = null) }
        launchScope.launch {
            runCatching { shareRepository.createRenderShare(renderJobId, mutableState.value.allowDownload) }
                .onSuccess { token -> mutableState.update { it.copy(isLoading = false, shareToken = token) } }
                .onFailure { error -> mutableState.update { it.copy(isLoading = false, message = error.statusMessage()) } }
        }
    }

    fun loadDownloadUrls() {
        if (mutableState.value.isLoading) return
        mutableState.update { it.copy(isLoading = true, message = null) }
        launchScope.launch {
            val sizes =
                runCatching { renderRepository.getArtifactSizes(renderJobId) }
                    .getOrElse { error ->
                        mutableState.update { it.copy(isLoading = false, message = error.statusMessage()) }
                        return@launch
                    }
            // Only enqueue the kinds that actually exist for this render job so audio-only or
            // video-only renders do not fail the entire batch with a 404 on the missing artifact.
            val pendingKinds = buildList {
                if (sizes.mp3SizeBytes != null) add(OfflineArtifactKind.Audio)
                if (sizes.mp4SizeBytes != null) add(OfflineArtifactKind.Video)
            }
            val collectedDownloads = mutableMapOf<OfflineArtifactKind, OfflineArtifactMetadata>()
            val audioUrls = mutableListOf<String>()
            val videoUrls = mutableListOf<String>()
            val failures = mutableListOf<String>()
            // Each kind is enqueued in its own runCatching so a transient failure on one
            // artifact does not block the other; per-kind errors are surfaced together.
            pendingKinds.forEach { kind ->
                runCatching {
                    val signedUrl =
                        if (kind == OfflineArtifactKind.Audio) {
                            playbackRepository.renderedAudioUrl(renderJobId, "attachment")
                        } else {
                            playbackRepository.renderedVideoUrl(renderJobId, "attachment")
                        }
                    val request =
                        ArtifactDownloadRequest(
                            renderJobId = renderJobId,
                            kind = kind,
                            url = signedUrl.url,
                            expiresAt = signedUrl.expiresAt,
                            title = "",
                        ).let { it.copy(title = it.canonicalTitle()) }
                    DownloadOutcome(signedUrl.url, downloadCoordinator?.enqueue(request))
                }.onSuccess { outcome ->
                    when (kind) {
                        OfflineArtifactKind.Audio -> audioUrls += outcome.url
                        OfflineArtifactKind.Video -> videoUrls += outcome.url
                        else -> Unit
                    }
                    outcome.metadata?.let { collectedDownloads[it.kind] = it }
                    if (outcome.metadata?.status == OfflineArtifactStatus.Failed) {
                        failures += "${kind.name}: ${outcome.metadata.failureMessage ?: "download failed"}"
                    }
                }.onFailure { error ->
                    failures += "${kind.name}: ${error.statusMessage()}"
                }
            }
            mutableState.update {
                it.copy(
                    isLoading = false,
                    audioUrl = audioUrls.firstOrNull(),
                    videoUrl = videoUrls.firstOrNull(),
                    downloads = collectedDownloads,
                    message = failures.takeIf { it.isNotEmpty() }?.joinToString(", "),
                )
            }
        }
    }
}

private data class DownloadOutcome(
    val url: String,
    val metadata: OfflineArtifactMetadata?,
)

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Share request failed"
    }
