package org.streamofworship.android.feature.player

import android.content.pm.ActivityInfo
import androidx.activity.ComponentActivity
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.unit.Density
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.streamofworship.android.core.design.SowTheme
import org.streamofworship.android.data.playback.PlaybackChapter
import org.streamofworship.android.data.playback.PlaybackLine
import org.streamofworship.android.data.playback.PlaybackManifest

@RunWith(AndroidJUnit4::class)
class PlayerScreenTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun `player controls expose labels and render Chinese lyrics with large text`() {
        val scope = TestScope()
        val viewModel =
            PlayerViewModel(
                renderJobId = "job-1",
                repository = ChinesePlaybackRepository(),
                controller = FakePlayerController(durationMillis = 60_000),
                scope = scope,
                tickerMillis = 0,
            )

        composeRule.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
        composeRule.setContent {
            SowTheme {
                CompositionLocalProvider(LocalDensity provides Density(density = 1f, fontScale = 1.8f)) {
                    PlayerScreen(viewModel = viewModel, media3Controller = null, onBack = {})
                }
            }
        }
        composeRule.waitForIdle()
        scope.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-screen").assertIsDisplayed()
        composeRule.onNodeWithText("耶和華是我的牧者").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Back").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Previous chapter").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Back 10 seconds").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Forward 10 seconds").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Next chapter").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Fullscreen").assertIsDisplayed()
    }
}

private class ChinesePlaybackRepository : FakePlaybackRepository() {
    override suspend fun chapters(renderJobId: String): PlaybackManifest =
        PlaybackManifest(
            totalDurationMillis = 60_000,
            generatedAt = "2026-01-01T00:00:00.000Z",
            chapters =
                listOf(
                    PlaybackChapter(
                        position = 1,
                        title = "詩篇二十三篇",
                        startMillis = 0L,
                        endMillis = 60_000L,
                        lines = listOf(PlaybackLine("耶和華是我的牧者", 0L)),
                    ),
                ),
        )
}
