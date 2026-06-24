package org.streamofworship.android.data.settings

import kotlinx.serialization.Serializable

@Serializable
data class SettingsEnvelope(
    val settings: UserSettings,
)

@Serializable
data class UserSettings(
    val userId: Long? = null,
    val offlineAutoCache: Boolean = true,
    val defaultGapBeats: Double = 2.0,
    val defaultVideoTemplate: String = "dark",
    val defaultResolution: String = "720p",
    val lyricsLoopWindowSeconds: Double = 3.0,
    val defaultFontSizePreset: String = "M",
    val defaultFontFamily: String = "noto_serif_tc",
    val defaultKeyShiftSemitones: Int = 0,
    val timingReviewFont: String = "sans",
)

@Serializable
data class SettingsUpdateRequest(
    val offlineAutoCache: Boolean,
    val defaultGapBeats: Double,
    val defaultVideoTemplate: String,
    val defaultResolution: String,
    val lyricsLoopWindowSeconds: Double,
    val defaultFontSizePreset: String,
    val defaultFontFamily: String,
    val defaultKeyShiftSemitones: Int,
    val timingReviewFont: String,
)

fun UserSettings.toUpdateRequest(): SettingsUpdateRequest =
    SettingsUpdateRequest(
        offlineAutoCache = offlineAutoCache,
        defaultGapBeats = defaultGapBeats,
        defaultVideoTemplate = defaultVideoTemplate,
        defaultResolution = defaultResolution,
        lyricsLoopWindowSeconds = lyricsLoopWindowSeconds,
        defaultFontSizePreset = defaultFontSizePreset,
        defaultFontFamily = defaultFontFamily,
        defaultKeyShiftSemitones = defaultKeyShiftSemitones,
        timingReviewFont = timingReviewFont,
    )
