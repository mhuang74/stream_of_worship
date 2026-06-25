package org.streamofworship.android.feature.player

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.streamofworship.android.core.design.SowTheme
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest

@RunWith(AndroidJUnit4::class)
class LyricsPanelTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<androidx.activity.ComponentActivity>()

    private val chapter1 =
        PlaybackChapter(
            position = 1,
            title = "詩篇二十三篇",
            startMillis = 0L,
            endMillis = 60_000L,
            lines = listOf(PlaybackLine("耶和華是我的牧者", 0L), PlaybackLine("我必不至缺乏", 5_000L)),
        )
    private val chapter2 =
        PlaybackChapter(
            position = 2,
            title = "恩典之路",
            startMillis = 60_000L,
            endMillis = 120_000L,
            lines = listOf(PlaybackLine("祢是我主", 65_000L), PlaybackLine("我跟隨祢", 70_000L)),
        )
    private val manifest =
        PlaybackManifest(
            chapters = listOf(chapter1, chapter2),
            totalDurationMillis = 120_000L,
            generatedAt = "2026-01-01T00:00:00.000Z",
        )

    @Test
    fun `renders all chapter titles`() {
        composeRule.setContent {
            SowTheme {
                Box(Modifier.fillMaxSize()) {
                    LyricsPanel(
                        modifier = Modifier.fillMaxSize(),
                        manifest = manifest,
                        positionMillis = 0L,
                        currentChapter = chapter1,
                        currentLine = chapter1.lines.first(),
                        onJumpToChapter = {},
                        onJumpToLine = {},
                    )
                }
            }
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-lyrics-panel").assertIsDisplayed()
        composeRule.onNodeWithTag("player-lyrics-chapter-0").assertIsDisplayed()
        composeRule.onNodeWithTag("player-lyrics-chapter-1").assertIsDisplayed()
        composeRule.onNodeWithText("1. 詩篇二十三篇").assertIsDisplayed()
        composeRule.onNodeWithText("2. 恩典之路").assertIsDisplayed()
    }

    @Test
    fun `renders lines for current chapter only`() {
        composeRule.setContent {
            SowTheme {
                Box(Modifier.fillMaxSize()) {
                    LyricsPanel(
                        modifier = Modifier.fillMaxSize(),
                        manifest = manifest,
                        positionMillis = 65_000L,
                        currentChapter = chapter2,
                        currentLine = chapter2.lines.first(),
                        onJumpToChapter = {},
                        onJumpToLine = {},
                    )
                }
            }
        }
        composeRule.waitForIdle()

        // Chapter 2 (current) lines appear.
        composeRule.onNodeWithText("祢是我主").assertIsDisplayed()
        // Chapter 1 (not current) lines do not appear.
        composeRule.onNodeWithText("我必不至缺乏").assertDoesNotExist()
    }

    @Test
    fun `current line has player-lyrics-current-line tag`() {
        composeRule.setContent {
            SowTheme {
                Box(Modifier.fillMaxSize()) {
                    LyricsPanel(
                        modifier = Modifier.fillMaxSize(),
                        manifest = manifest,
                        positionMillis = 0L,
                        currentChapter = chapter1,
                        currentLine = chapter1.lines.first(),
                        onJumpToChapter = {},
                        onJumpToLine = {},
                    )
                }
            }
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-lyrics-current-line").assertIsDisplayed()
        composeRule.onNodeWithTag("player-lyrics-line-0-0").assertIsDisplayed()
    }

    @Test
    fun `tapping a line invokes onJumpToLine`() {
        var jumpedLine: PlaybackLine? = null
        composeRule.setContent {
            SowTheme {
                Box(Modifier.fillMaxSize()) {
                    LyricsPanel(
                        modifier = Modifier.fillMaxSize(),
                        manifest = manifest,
                        positionMillis = 65_000L,
                        currentChapter = chapter2,
                        currentLine = chapter2.lines.first(),
                        onJumpToChapter = {},
                        onJumpToLine = { jumpedLine = it },
                    )
                }
            }
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-lyrics-line-1-1").performClick()
        composeRule.waitForIdle()
        assertEquals(chapter2.lines[1], jumpedLine)
    }

    @Test
    fun `tapping a chapter header invokes onJumpToChapter`() {
        var jumpedChapter: PlaybackChapter? = null
        composeRule.setContent {
            SowTheme {
                Box(Modifier.fillMaxSize()) {
                    LyricsPanel(
                        modifier = Modifier.fillMaxSize(),
                        manifest = manifest,
                        positionMillis = 65_000L,
                        currentChapter = chapter2,
                        currentLine = chapter2.lines.first(),
                        onJumpToChapter = { jumpedChapter = it },
                        onJumpToLine = {},
                    )
                }
            }
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-lyrics-chapter-0").performClick()
        composeRule.waitForIdle()
        assertEquals(chapter1, jumpedChapter)
    }
}
