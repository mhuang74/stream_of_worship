package org.streamofworship.android.feature.player

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest

/**
 * Inline collapsible lyrics panel that mirrors the behaviour of the webapp's
 * `LyricJumpList.tsx`. The panel grows to fill leftover vertical space below the
 * video surface (via [Modifier.weight]), never overlapping the video.
 *
 * - All chapters render as headers (tap to jump).
 * - Only the current chapter's lines render; the others stay collapsed (matches the
 *   webapp, which expands only the active song).
 * - The current line is highlighted; past lines are dimmed; future lines use the
 *   surface variant tone.
 * - Tapping a line seeks playback to its start.
 */
@Composable
fun LyricsPanel(
    manifest: PlaybackManifest,
    positionMillis: Long,
    currentChapter: PlaybackChapter?,
    currentLine: PlaybackLine?,
    onJumpToChapter: (PlaybackChapter) -> Unit,
    onJumpToLine: (PlaybackLine) -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()
    val currentChapterIndex = manifest.chapters.indexOf(currentChapter).takeIf { it >= 0 } ?: 0

    // Auto-scroll only when the current chapter changes — never on individual line changes.
    // Including `currentLine` as a key would snap the list back to the chapter header every
    // time the current lyric line updates during playback, fighting the user's manual scroll.
    LaunchedEffect(currentChapter) {
        if (manifest.chapters.isNotEmpty()) {
            listState.animateScrollToItem(currentChapterIndex)
        }
    }

    Surface(
        tonalElevation = 1.dp,
        shape = MaterialTheme.shapes.medium,
        modifier = modifier.testTag("player-lyrics-panel"),
    ) {
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().padding(8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            itemsIndexed(manifest.chapters, key = { _, chapter -> "${chapter.position}-${chapter.startMillis}" }) { index, chapter ->
                ChapterRow(
                    chapter = chapter,
                    isCurrent = chapter == currentChapter,
                    positionMillis = positionMillis,
                    currentLine = if (chapter == currentChapter) currentLine else null,
                    onJumpToChapter = onJumpToChapter,
                    onJumpToLine = onJumpToLine,
                    testTagPrefix = "player-lyrics-chapter-$index",
                    chapterIndex = index,
                )
            }
        }
    }
}

@Composable
private fun ChapterRow(
    chapter: PlaybackChapter,
    isCurrent: Boolean,
    positionMillis: Long,
    currentLine: PlaybackLine?,
    onJumpToChapter: (PlaybackChapter) -> Unit,
    onJumpToLine: (PlaybackLine) -> Unit,
    testTagPrefix: String,
    chapterIndex: Int,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        OutlinedButton(
            onClick = { onJumpToChapter(chapter) },
            modifier = Modifier.fillMaxWidth().testTag(testTagPrefix),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        "${chapter.position}. ${chapter.title}".trim(),
                        fontWeight = if (isCurrent) FontWeight.SemiBold else FontWeight.Normal,
                        style = MaterialTheme.typography.titleSmall,
                        color = if (isCurrent) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                    )
                    Text(
                        "${formatTime(chapter.startMillis)} - ${formatTime(chapter.endMillis)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        if (isCurrent) {
            Column(modifier = Modifier.fillMaxWidth().padding(start = 8.dp, top = 4.dp)) {
                chapter.lines.forEachIndexed { lineIndex, line ->
                    val isCurrentLine = line == currentLine
                    val isPastLine = line.startMillis < positionMillis && !isCurrentLine
                    // Two test tags (per spec): the per-line identifier lives on the wrapping
                    // Box, while the current-line marker lives on the TextButton. Keeping them
                    // on distinct layout nodes avoids ambiguous semantics merging while
                    // exposing both identifiers to UI tests.
                    Box(modifier = Modifier.fillMaxWidth().testTag("player-lyrics-line-$chapterIndex-$lineIndex")) {
                        TextButton(
                            onClick = { onJumpToLine(line) },
                            modifier =
                                Modifier
                                    .fillMaxWidth()
                                    .then(if (isCurrentLine) Modifier.testTag("player-lyrics-current-line") else Modifier),
                        ) {
                            Row(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(
                                    line.text,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color =
                                        when {
                                            isCurrentLine -> MaterialTheme.colorScheme.primary
                                            isPastLine -> MaterialTheme.colorScheme.onSurface.copy(alpha = 0.4f)
                                            else -> MaterialTheme.colorScheme.onSurfaceVariant
                                        },
                                )
                                Text(
                                    formatTime(line.startMillis),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

internal fun formatTime(millis: Long): String {
    if (millis < 0) return "0:00"
    val totalSeconds = millis / 1000
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "$minutes:${seconds.toString().padStart(2, '0')}"
}
