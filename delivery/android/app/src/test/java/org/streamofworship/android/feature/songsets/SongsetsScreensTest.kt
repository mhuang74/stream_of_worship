package org.streamofworship.android.feature.songsets

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.test.TestScope
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.streamofworship.android.core.design.SowTheme

@RunWith(AndroidJUnit4::class)
class SongsetsScreensTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `list screen renders songsets create controls and status`() {
        val scope = TestScope()
        val viewModel = SongsetsListViewModel(FakeSongsetsRepository(), scope)
        scope.testScheduler.runCurrent()

        composeRule.setContent {
            SowTheme {
                SongsetsListScreen(viewModel = viewModel, onOpenSongset = {})
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("songsets-list-screen").assertIsDisplayed()
        composeRule.onNodeWithText("Create songset").assertIsDisplayed()
        composeRule.onNodeWithText("Morning Set").assertIsDisplayed()
        composeRule.onNodeWithText("Fresh").assertIsDisplayed()
    }

    @Test
    fun `detail screen renders editor song search and transition states`() {
        val scope = TestScope()
        val viewModel =
            SongsetDetailViewModel(
                songsetId = "set-1",
                songsetsRepository = FakeSongsetsRepository(),
                songsRepository = FakeSongsRepository(),
                scope = scope,
            )
        scope.testScheduler.runCurrent()

        composeRule.setContent {
            SowTheme {
                SongsetDetailScreen(viewModel = viewModel, onBack = {}, onRender = {})
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("songset-detail-screen").assertIsDisplayed()
        composeRule.onNodeWithText("Morning Set").assertIsDisplayed()
        composeRule.onNodeWithTag("songset-detail-screen").performScrollToNode(hasText("Transition"))
        composeRule.onNodeWithText("Transition").assertIsDisplayed()
        composeRule.onNodeWithTag("songset-detail-screen").performScrollToNode(hasText("Browse songs"))
        composeRule.onNodeWithText("Browse songs").assertIsDisplayed()
        composeRule.onNodeWithText("New Song").assertIsDisplayed()
    }

    @Test
    fun `detail screen exposes render action`() {
        val scope = TestScope()
        val viewModel =
            SongsetDetailViewModel(
                songsetId = "set-1",
                songsetsRepository = FakeSongsetsRepository(),
                songsRepository = FakeSongsRepository(),
                scope = scope,
            )
        var renderOpened = false

        composeRule.setContent {
            SowTheme {
                SongsetDetailScreen(viewModel = viewModel, onBack = {}, onRender = { renderOpened = true })
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("songset-render-button").performClick()

        assertTrue(renderOpened)
    }

    @Test
    fun `detail search field accepts query text`() {
        val scope = TestScope()
        val viewModel =
            SongsetDetailViewModel(
                songsetId = "set-1",
                songsetsRepository = FakeSongsetsRepository(),
                songsRepository = FakeSongsRepository(),
                scope = scope,
            )

        composeRule.setContent {
            SowTheme {
                SongsetDetailScreen(viewModel = viewModel, onBack = {}, onRender = {})
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("songset-detail-screen").performScrollToNode(hasText("Browse songs"))
        composeRule.onNodeWithTag("song-search-query").performTextInput("grace")
        composeRule.onNodeWithText("grace").assertIsDisplayed()
    }
}
