package org.streamofworship.android.data.render

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

enum class RenderTemplate(
    val value: String,
    val label: String,
) {
    Dark("dark", "Dark"),
    GradientWarm("gradient_warm", "Gradient Warm"),
    GradientBlue("gradient_blue", "Gradient Blue"),
}

enum class RenderResolution(
    val value: String,
    val label: String,
) {
    Hd720("720p", "720p (HD)"),
    FullHd1080("1080p", "1080p (Full HD)"),
}

enum class RenderFontFamily(
    val value: String,
    val label: String,
) {
    Traditional("lxgw_wenkai_tc", "Traditional"),
    Elegant("chiron_goround_tc", "Elegant"),
    Modern("chocolate_classical_sans", "Modern"),
    Classic("noto_serif_tc", "Classic"),
}

enum class RenderFontSize(
    val value: String,
    val label: String,
    val pixels: Int,
) {
    Small("S", "Small (32px)", 32),
    Medium("M", "Medium (48px)", 48),
    Large("L", "Large (64px)", 64),
    ExtraLarge("XL", "Extra Large (80px)", 80),
}

val RenderTitleCardDurations = listOf(5, 10, 15, 20, 25, 30)

@Serializable
data class RenderFormConfig(
    val audioEnabled: Boolean = true,
    val videoEnabled: Boolean = true,
    val template: String = RenderTemplate.Dark.value,
    val resolution: String = RenderResolution.Hd720.value,
    val fontSizePreset: String = RenderFontSize.Medium.value,
    val fontFamily: String = RenderFontFamily.Classic.value,
    val includeTitleCard: Boolean = false,
    val titleCardDurationSeconds: Int = 10,
    val titleCardLines: List<String> = emptyList(),
)

@Serializable
data class CreateRenderJobRequest(
    val songsetId: String,
    val template: String = RenderTemplate.Dark.value,
    val resolution: String = RenderResolution.Hd720.value,
    val audioEnabled: Boolean = true,
    val videoEnabled: Boolean = true,
    val fontSizePreset: String = RenderFontSize.Medium.value,
    val fontFamily: String = RenderFontFamily.Classic.value,
    val includeTitleCard: Boolean = false,
    val titleCardDurationSeconds: Int? = null,
    val titleCardLines: List<String>? = null,
)

@Serializable
data class RenderJob(
    val id: String,
    val songsetId: String,
    val userId: Long? = null,
    val status: RenderJobStatus,
    val phase: RenderPhase? = null,
    val phaseIndex: Int? = null,
    val totalPhases: Int? = null,
    val elapsedSeconds: Double? = null,
    val errorMessage: String? = null,
    val estimatedTotalSeconds: Double? = null,
    val totalDurationSeconds: Double? = null,
    val startedAt: String? = null,
    val template: String = RenderTemplate.Dark.value,
    val resolution: String = RenderResolution.Hd720.value,
    val audioEnabled: Boolean = true,
    val videoEnabled: Boolean = true,
    val fontSizePreset: String = RenderFontSize.Medium.value,
    val fontFamily: String = RenderFontFamily.Classic.value,
    val includeTitleCard: Boolean = false,
    val titleCardDurationSeconds: Double? = null,
    val titleCardLines: List<String>? = null,
    val mp3R2Key: String? = null,
    val mp4R2Key: String? = null,
    val chaptersR2Key: String? = null,
    val songCount: Int? = null,
    val songsetDurationSeconds: Double? = null,
    val createdAt: String? = null,
    val updatedAt: String? = null,
    val completedAt: String? = null,
) {
    val isActive: Boolean
        get() = status == RenderJobStatus.Queued || status == RenderJobStatus.Running

    val hasPlayableArtifacts: Boolean
        get() = status == RenderJobStatus.Completed && (mp3R2Key != null || mp4R2Key != null)
}

@Serializable
enum class RenderJobStatus {
    @SerialName("queued")
    Queued,

    @SerialName("running")
    Running,

    @SerialName("completed")
    Completed,

    @SerialName("failed")
    Failed,

    @SerialName("cancelled")
    Cancelled,
}

@Serializable
enum class RenderPhase {
    @SerialName("preparing")
    Preparing,

    @SerialName("mixing_audio")
    MixingAudio,

    @SerialName("rendering_frames")
    RenderingFrames,

    @SerialName("encoding_video")
    EncodingVideo,

    @SerialName("uploading")
    Uploading,

    @SerialName("completed")
    Completed,
}

@Serializable
data class ArtifactSizes(
    val renderJobId: String,
    val mp3SizeBytes: Long? = null,
    val mp4SizeBytes: Long? = null,
)

data class ActiveRenderConflict(
    val jobId: String? = null,
    val estimatedTotalSeconds: Double? = null,
    val config: ConflictRenderConfig? = null,
    val message: String,
)

@Serializable
data class ConflictRenderConfig(
    val audioEnabled: Boolean? = null,
    val videoEnabled: Boolean? = null,
    val fontFamily: String? = null,
)

class ActiveRenderConflictException(
    val conflict: ActiveRenderConflict,
) : RuntimeException(conflict.message)
