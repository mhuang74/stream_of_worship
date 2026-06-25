package org.streamofworship.android.feature.render

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.test.TestScope
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.streamofworship.android.core.design.SowTheme
import org.streamofworship.android.data.offline.FileOfflineCacheRepository
import org.streamofworship.android.data.render.RenderJobStatus
import org.streamofworship.android.feature.player.PlaybackArtifact

@RunWith(AndroidJUnit4::class)
class RenderScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun `screen renders render form and validates empty outputs`() {
        val scope = TestScope()
        val viewModel = RenderViewModel("set-1", FakeRenderSongsetsRepository(), FakeRenderRepository(), scope = scope)

        composeRule.setContent {
            SowTheme {
                RenderScreen(viewModel = viewModel, onBack = {}, onPlay = { _, _ -> }, onDownload = {})
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("render-screen").assertIsDisplayed()
        composeRule.onNodeWithText("Output Options").assertIsDisplayed()
        composeRule.onNodeWithTag("render-screen").performScrollToNode(hasText("耶和華是我的牧者"))
        composeRule.onAllNodesWithText("耶和華是我的牧者").assertCountEquals(1)
        composeRule.onNodeWithTag("render-audio-toggle").performClick()
        composeRule.onNodeWithTag("render-video-toggle").performClick()
        composeRule.onNodeWithTag("render-start-button").performClick()

        composeRule.onNodeWithTag("render-validation-message").assertIsDisplayed()
        assertEquals("Select audio, video, or both.", viewModel.uiState.value.validationMessage)
    }

    @Test
    fun `screen surfaces completed artifacts and routes actions`() {
        val scope = TestScope()
        val render =
            FakeRenderRepository(
                jobs = mutableListOf(job("job-1", RenderJobStatus.Completed)),
            )
        val offlineRepository = FileOfflineCacheRepository(temporaryFolder.newFile("artifacts.json").toPath())
        val viewModel = RenderViewModel("set-1", FakeRenderSongsetsRepository(), render, offlineRepository, scope)
        var playRoute: Pair<String, PlaybackArtifact>? = null
        var downloadJob: String? = null

        composeRule.setContent {
            SowTheme {
                RenderScreen(
                    viewModel = viewModel,
                    onBack = {},
                    onPlay = { jobId, artifact -> playRoute = Pair(jobId, artifact) },
                    onDownload = { downloadJob = it },
                )
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        viewModel.startPolling("job-1")
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("render-screen").performScrollToNode(hasText("Play"))
        composeRule.onNodeWithTag("render-artifact-availability").assertIsDisplayed()
        composeRule.onNodeWithTag("render-offline-cache-state").assertIsDisplayed()
        composeRule.onNodeWithText("Play").performClick()
        composeRule.onNodeWithText("Download").performClick()

        assertEquals(Pair("job-1", PlaybackArtifact.Video), playRoute)
        assertEquals("job-1", downloadJob)
    }
}
