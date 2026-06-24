package org.streamofworship.android.feature.settings

import org.streamofworship.android.data.settings.UserSettings

val ValidVideoTemplates = setOf("dark", "gradient_warm", "gradient_blue")
val ValidResolutions = setOf("720p", "1080p")
val ValidFontSizePresets = setOf("S", "M", "L", "XL")
val ValidRenderFonts = setOf("lxgw_wenkai_tc", "chiron_goround_tc", "chocolate_classical_sans", "noto_serif_tc")
val ValidTimingFonts = setOf("sans", "mono", "serif")

fun settingsValidationError(settings: UserSettings): String? =
    when {
        settings.defaultVideoTemplate !in ValidVideoTemplates -> "Choose a valid video template."
        settings.defaultResolution !in ValidResolutions -> "Choose a valid resolution."
        settings.defaultFontSizePreset !in ValidFontSizePresets -> "Choose a valid font size."
        settings.defaultFontFamily !in ValidRenderFonts -> "Choose a valid render font."
        settings.timingReviewFont !in ValidTimingFonts -> "Choose a valid timing review font."
        settings.defaultGapBeats !in 0.0..16.0 -> "Default gap must be between 0 and 16 beats."
        settings.lyricsLoopWindowSeconds !in 1.0..30.0 -> "Lyrics loop window must be between 1 and 30 seconds."
        settings.defaultKeyShiftSemitones !in -6..6 -> "Default key shift must be between -6 and 6 semitones."
        else -> null
    }
