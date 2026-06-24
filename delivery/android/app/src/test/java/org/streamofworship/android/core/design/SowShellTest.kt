package org.streamofworship.android.core.design

import androidx.compose.material3.Text
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SowShellTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `renders shell content and bottom navigation`() {
        composeRule.setContent {
            SowTheme {
                SowShell {
                    Text("Songset workspace")
                }
            }
        }

        composeRule.onNodeWithTag("sow-shell").assertIsDisplayed()
        composeRule.onNodeWithText("Songset workspace").assertIsDisplayed()
        composeRule.onNodeWithText("Songsets").assertIsDisplayed()
        composeRule.onNodeWithText("Render").assertIsDisplayed()
        composeRule.onNodeWithText("Player").assertIsDisplayed()
        composeRule.onNodeWithText("Share").assertIsDisplayed()
        composeRule.onNodeWithText("Settings").assertIsDisplayed()
    }

    @Test
    fun `renders reusable state panels`() {
        composeRule.setContent {
            SowTheme {
                SowEmptyState(
                    title = "No songsets",
                    message = "Create a worship set to begin.",
                    actionLabel = "Create",
                    onAction = {},
                )
            }
        }

        composeRule.onNodeWithTag("sow-empty-state").assertIsDisplayed()
        composeRule.onNodeWithText("No songsets").assertIsDisplayed()
        composeRule.onNodeWithText("Create").assertIsDisplayed()
    }
}
