package org.streamofworship.android.data.playback

import kotlinx.serialization.Serializable

enum class SignedUrlFileType(
    val value: String,
) {
    RenderedAudio("audio"),
    RenderedVideo("video"),
    RenderedChapters("json"),
    SourceAudio("audio"),
    SourceLrc("lrc"),
}

@Serializable
data class SignedUrlResponse(
    val url: String,
    val expiresAt: String,
    val cacheControl: String? = null,
)

@Serializable
data class ChaptersManifestDto(
    val chapters: List<ChapterDto> = emptyList(),
    val totalDurationSeconds: Double = 0.0,
    val generatedAt: String? = null,
)

@Serializable
data class ChapterDto(
    val position: Int,
    val songTitle: String,
    val startSeconds: Double,
    val endSeconds: Double,
    val lines: List<ChapterLineDto> = emptyList(),
)

@Serializable
data class ChapterLineDto(
    val text: String,
    val startSeconds: Double,
)

data class PlaybackManifest(
    val chapters: List<PlaybackChapter>,
    val totalDurationMillis: Long,
    val generatedAt: String?,
) {
    fun chapterAt(positionMillis: Long): PlaybackChapter? =
        chapters.lastOrNull { positionMillis >= it.startMillis }
            ?.takeIf { positionMillis < it.endMillis || it == chapters.last() && positionMillis == it.endMillis }

    fun currentLineAt(positionMillis: Long): PlaybackLine? =
        chapterAt(positionMillis)?.lines?.lastOrNull { positionMillis >= it.startMillis }
}

data class PlaybackChapter(
    val position: Int,
    val title: String,
    val startMillis: Long,
    val endMillis: Long,
    val lines: List<PlaybackLine>,
)

data class PlaybackLine(
    val text: String,
    val startMillis: Long,
)

fun ChaptersManifestDto.normalized(): PlaybackManifest {
    val normalizedChapters =
        chapters
            .filter { it.startSeconds.isFinite() && it.endSeconds.isFinite() && it.endSeconds >= it.startSeconds }
            .sortedWith(compareBy<ChapterDto> { it.startSeconds }.thenBy { it.position })
            .mapIndexed { index, chapter ->
                PlaybackChapter(
                    position = chapter.position.takeIf { it > 0 } ?: index + 1,
                    title = chapter.songTitle.ifBlank { "Song ${index + 1}" },
                    startMillis = chapter.startSeconds.secondsToMillis(),
                    endMillis = chapter.endSeconds.secondsToMillis(),
                    lines =
                        chapter.lines
                            .filter { it.startSeconds.isFinite() && it.text.isNotBlank() }
                            .sortedBy { it.startSeconds }
                            .map { line ->
                                PlaybackLine(
                                    text = line.text.trim(),
                                    startMillis = line.startSeconds.secondsToMillis(),
                                )
                            },
                )
            }
    val inferredDuration = normalizedChapters.maxOfOrNull { it.endMillis } ?: 0L
    return PlaybackManifest(
        chapters = normalizedChapters,
        totalDurationMillis = maxOf(totalDurationSeconds.secondsToMillis(), inferredDuration),
        generatedAt = generatedAt,
    )
}

private fun Double.secondsToMillis(): Long = (this * 1000.0).toLong().coerceAtLeast(0L)
