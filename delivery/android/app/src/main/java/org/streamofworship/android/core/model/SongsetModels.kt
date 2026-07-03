package org.streamofworship.android.core.model

import kotlinx.serialization.Serializable

const val SongsetMaxSongs = 5
const val SongsetMaxDurationSeconds = 1500

@Serializable
data class SongsetSummary(
    val id: String,
    val name: String,
    val description: String? = null,
    val createdAt: String,
    val updatedAt: String,
    val latestRenderJobId: String? = null,
    val lastFailedRenderJobId: String? = null,
    val lastCompletedRenderJobId: String? = null,
    val itemCount: Int = 0,
    val durationSeconds: Double? = null,
    val renderState: RenderState = RenderState.Unrendered,
    val renderErrorMessage: String? = null,
    val failedAt: String? = null,
) {
    val isArtifactsStale: Boolean
        get() = renderState == RenderState.Stale
}

@Serializable
data class SongsetsPage(
    val songsets: List<SongsetSummary> = emptyList(),
    val total: Int = 0,
)

@Serializable
data class TransitionSettings(
    val gapBeats: Double? = null,
    val crossfadeEnabled: Int? = null,
    val crossfadeDurationSeconds: Double? = null,
    val keyShiftSemitones: Int? = null,
    val tempoRatio: Double? = null,
) {
    companion object {
        val Default =
            TransitionSettings(
                gapBeats = 0.0,
                crossfadeEnabled = 0,
                crossfadeDurationSeconds = 0.0,
                keyShiftSemitones = 0,
                tempoRatio = 1.0,
            )
    }
}

@Serializable
data class SongsetItemSong(
    val id: String,
    val title: String,
    val composer: String? = null,
    val lyricist: String? = null,
    val albumName: String? = null,
    val musicalKey: String? = null,
    val effectiveKey: String? = null,
    val effectiveKeySource: String? = null,
    val effectiveKeyStartRoot: String? = null,
    val effectiveKeyEndRoot: String? = null,
    val effectiveKeyMode: String? = null,
)

@Serializable
data class SongsetItemRecording(
    val contentHash: String,
    val durationSeconds: Double? = null,
    val tempoBpm: Double? = null,
    val musicalKey: String? = null,
    val effectiveKey: String? = null,
    val effectiveKeySource: String? = null,
    val effectiveKeyStartRoot: String? = null,
    val effectiveKeyEndRoot: String? = null,
    val effectiveKeyMode: String? = null,
    val r2AudioUrl: String? = null,
)

@Serializable
data class SongsetItem(
    val id: String,
    val songId: String,
    val recordingHashPrefix: String? = null,
    val position: Int,
    val gapBeats: Double? = null,
    val crossfadeEnabled: Int? = null,
    val crossfadeDurationSeconds: Double? = null,
    val keyShiftSemitones: Int? = null,
    val tempoRatio: Double? = null,
    val markedLineCount: Int = 0,
    val song: SongsetItemSong? = null,
    val recording: SongsetItemRecording? = null,
) {
    val transitionSettings: TransitionSettings
        get() =
            TransitionSettings(
                gapBeats = gapBeats ?: TransitionSettings.Default.gapBeats,
                crossfadeEnabled = crossfadeEnabled ?: TransitionSettings.Default.crossfadeEnabled,
                crossfadeDurationSeconds =
                    crossfadeDurationSeconds ?: TransitionSettings.Default.crossfadeDurationSeconds,
                keyShiftSemitones = keyShiftSemitones ?: TransitionSettings.Default.keyShiftSemitones,
                tempoRatio = tempoRatio ?: TransitionSettings.Default.tempoRatio,
            )
}

@Serializable
data class SongsetDetail(
    val id: String,
    val name: String,
    val description: String? = null,
    val createdAt: String,
    val updatedAt: String,
    val latestRenderJobId: String? = null,
    val lastFailedRenderJobId: String? = null,
    val lastCompletedRenderJobId: String? = null,
    val itemCount: Int = 0,
    val durationSeconds: Double? = null,
    val renderState: RenderState = RenderState.Unrendered,
    val renderErrorMessage: String? = null,
    val failedAt: String? = null,
    val items: List<SongsetItem> = emptyList(),
) {
    fun summary(): SongsetSummary =
        SongsetSummary(
            id = id,
            name = name,
            description = description,
            createdAt = createdAt,
            updatedAt = updatedAt,
            latestRenderJobId = latestRenderJobId,
            lastFailedRenderJobId = lastFailedRenderJobId,
            lastCompletedRenderJobId = lastCompletedRenderJobId,
            itemCount = itemCount,
            durationSeconds = durationSeconds,
            renderState = renderState,
            renderErrorMessage = renderErrorMessage,
            failedAt = failedAt,
        )
}

fun SongsetDetail.withItemsMarkedStale(items: List<SongsetItem>): SongsetDetail =
    copy(
        items = items.sortedBy { it.position },
        itemCount = items.size,
        durationSeconds = items.sumOf { it.recording?.durationSeconds ?: 0.0 }.takeIf { it > 0.0 },
        renderState = RenderState.Stale,
        renderErrorMessage = null,
        failedAt = null,
    )
