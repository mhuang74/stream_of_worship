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
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.media3.exoplayer.ExoPlayer
import androidx.test.core.app.ApplicationProvider
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
    fun `player controls expose labels and lyrics render Chinese text through the panel`() {
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
        // The inline current-line Text is removed; open the lyrics panel to reveal it.
        composeRule.onNodeWithTag("player-lyrics-toggle").performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithTag("player-lyrics-panel").assertIsDisplayed()
        composeRule.onNodeWithText("耶和華是我的牧者").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Back").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Previous chapter").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Back 10 seconds").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Forward 10 seconds").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Next chapter").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Fullscreen").assertIsDisplayed()
        composeRule.onNodeWithContentDescription("Lyrics").assertIsDisplayed()
    }

    @Test
    fun `lyrics toggle expands panel listing all chapter titles`() {
        val scope = TestScope()
        val viewModel =
            PlayerViewModel(
                renderJobId = "job-1",
                repository = TwoChapterPlaybackRepository(),
                controller = FakePlayerController(durationMillis = 120_000),
                scope = scope,
                tickerMillis = 0,
            )

        composeRule.setContent {
            SowTheme {
                PlayerScreen(viewModel = viewModel, media3Controller = null, onBack = {})
            }
        }
        composeRule.waitForIdle()
        scope.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-lyrics-toggle").performClick()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("player-lyrics-panel").assertIsDisplayed()
        composeRule.onNodeWithText("1. 詩篇二十三篇").assertIsDisplayed()
        composeRule.onNodeWithText("2. 恩典之路").assertIsDisplayed()
    }

    @Test
    fun `direct player facade backed controller renders player video view`() {
        val scope = TestScope()
        val exoPlayer = ExoPlayer.Builder(ApplicationProvider.getApplicationContext()).build()
        val controller = Media3PlayerController(exoPlayer)
        val viewModel =
            PlayerViewModel(
                renderJobId = "job-1",
                repository = TwoChapterPlaybackRepository(),
                controller = FakePlayerController(durationMillis = 120_000),
                scope = scope,
                tickerMillis = 0,
            )

        try {
            composeRule.setContent {
                SowTheme {
                    PlayerScreen(viewModel = viewModel, media3Controller = controller, onBack = {})
                }
            }
            composeRule.waitForIdle()
            composeRule.onNodeWithTag("player-video-view").assertIsDisplayed()
        } finally {
            controller.release()
        }
    }

    @Test
    fun `fullscreen toggle enters the immersive overlay layout`() {
        val scope = TestScope()
        val exoPlayer = ExoPlayer.Builder(ApplicationProvider.getApplicationContext()).build()
        val controller = Media3PlayerController(exoPlayer)
        val viewModel =
            PlayerViewModel(
                renderJobId = "job-1",
                repository = TwoChapterPlaybackRepository(),
                controller = FakePlayerController(durationMillis = 120_000),
                scope = scope,
                tickerMillis = 0,
            )

        try {
            composeRule.setContent {
                SowTheme {
                    PlayerScreen(viewModel = viewModel, media3Controller = controller, onBack = {})
                }
            }
            composeRule.waitForIdle()

            composeRule.onNodeWithContentDescription("Fullscreen").performClick()
            composeRule.waitForIdle()

            composeRule.onNodeWithTag("player-fullscreen").assertIsDisplayed()
            composeRule.onNodeWithTag("player-fullscreen-exit").assertIsDisplayed()
        } finally {
            controller.release()
        }
    }

    @Test
    fun `exit fullscreen affordance flips back to inline layout`() {
        val scope = TestScope()
        val exoPlayer = ExoPlayer.Builder(ApplicationProvider.getApplicationContext()).build()
        val controller = Media3PlayerController(exoPlayer)
        val viewModel =
            PlayerViewModel(
                renderJobId = "job-1",
                repository = TwoChapterPlaybackRepository(),
                controller = FakePlayerController(durationMillis = 120_000),
                scope = scope,
                tickerMillis = 0,
            )

        try {
            composeRule.setContent {
                SowTheme {
                    PlayerScreen(viewModel = viewModel, media3Controller = controller, onBack = {})
                }
            }
            composeRule.waitForIdle()

            composeRule.onNodeWithContentDescription("Fullscreen").performClick()
            composeRule.waitForIdle()
            composeRule.onNodeWithTag("player-fullscreen").assertIsDisplayed()

            // Exit via the on-screen affordance (BackHandler path is exercised by the OS
            // back button; this test covers the equivalent in-app exit control).
            composeRule.onNodeWithTag("player-fullscreen-exit").performClick()
            composeRule.waitForIdle()

            composeRule.onNodeWithTag("player-screen").assertIsDisplayed()
            composeRule.onNodeWithTag("player-video-view").assertIsDisplayed()
        } finally {
            controller.release()
        }
    }

    @Test
    fun `back press exits fullscreen without popping the screen`() {
        val scope = TestScope()
        var backPopped = false
        val exoPlayer = ExoPlayer.Builder(ApplicationProvider.getApplicationContext()).build()
        val controller = Media3PlayerController(exoPlayer)
        val viewModel =
            PlayerViewModel(
                renderJobId = "job-1",
                repository = TwoChapterPlaybackRepository(),
                controller = FakePlayerController(durationMillis = 120_000),
                scope = scope,
                tickerMillis = 0,
            )

        try {
            composeRule.setContent {
                SowTheme {
                    PlayerScreen(
                        viewModel = viewModel,
                        media3Controller = controller,
                        onBack = { backPopped = true },
                    )
                }
            }
            composeRule.waitForIdle()

            composeRule.onNodeWithContentDescription("Fullscreen").performClick()
            composeRule.waitForIdle()
            composeRule.onNodeWithTag("player-fullscreen").assertIsDisplayed()

            // Trigger the system-back path via the activity's dispatcher. The BackHandler
            // must consume the event (exit fullscreen) instead of popping the screen.
            composeRule.activity.onBackPressedDispatcher.onBackPressed()
            composeRule.waitForIdle()

            // Fullscreen exited, but onBack must NOT have fired (screen was not popped).
            composeRule.onNodeWithTag("player-screen").assertIsDisplayed()
            composeRule.onNodeWithTag("player-video-view").assertIsDisplayed()
            assert(!backPopped)
        } finally {
            controller.release()
        }
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

private class TwoChapterPlaybackRepository : FakePlaybackRepository() {
    override suspend fun chapters(renderJobId: String): PlaybackManifest =
        PlaybackManifest(
            totalDurationMillis = 120_000,
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
                    PlaybackChapter(
                        position = 2,
                        title = "恩典之路",
                        startMillis = 60_000L,
                        endMillis = 120_000L,
                        lines = listOf(PlaybackLine("祢是我主", 65_000L)),
                    ),
                ),
        )
}
