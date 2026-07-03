package org.streamofworship.android.core.model

import kotlinx.serialization.Serializable

@Serializable
data class Recording(
    val contentHash: String,
    val hashPrefix: String? = null,
    val originalFilename: String? = null,
    val durationSeconds: Double? = null,
    val tempoBpm: Double? = null,
    val musicalKey: String? = null,
    val effectiveKey: String? = null,
    val effectiveKeySource: String? = null,
    val effectiveKeyStartRoot: String? = null,
    val effectiveKeyEndRoot: String? = null,
    val effectiveKeyMode: String? = null,
    val musicalMode: String? = null,
    val loudnessDb: Double? = null,
    val r2AudioUrl: String? = null,
    val r2LrcUrl: String? = null,
    val visibilityStatus: String? = null,
    val analysisStatus: String? = null,
)

@Serializable
data class Song(
    val id: String,
    val title: String,
    val titlePinyin: String? = null,
    val composer: String? = null,
    val lyricist: String? = null,
    val albumName: String? = null,
    val albumSeries: String? = null,
    val musicalKey: String? = null,
    val effectiveKey: String? = null,
    val effectiveKeySource: String? = null,
    val effectiveKeyStartRoot: String? = null,
    val effectiveKeyEndRoot: String? = null,
    val effectiveKeyMode: String? = null,
    val createdAt: String? = null,
    val updatedAt: String? = null,
    val recordings: List<Recording> = emptyList(),
    val similarity: Double? = null,
    val modelVersion: String? = null,
    val matchingSnippet: String? = null,
    val whyThisMatch: List<String> = emptyList(),
) {
    val publishedRecordings: List<Recording>
        get() = recordings.filter { it.visibilityStatus == null || it.visibilityStatus == "published" }
}

@Serializable
data class SongsPage(
    val songs: List<Song> = emptyList(),
    val total: Int = 0,
    val query: String? = null,
)
