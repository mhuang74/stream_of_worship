package org.streamofworship.android.feature.settings

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.test.TestScope
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.streamofworship.android.core.design.SowTheme
import org.streamofworship.android.data.settings.SettingsRepository
import org.streamofworship.android.data.settings.UserSettings

@RunWith(AndroidJUnit4::class)
class SettingsScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `sign out button is surfaced and invokes the sign out callback`() {
        val scope = TestScope()
        val viewModel = SettingsViewModel(FakeSettingsScreenRepository(), scope)
        var signOutCalls = 0

        composeRule.setContent {
            SowTheme {
                SettingsScreen(viewModel = viewModel, onSignOut = { signOutCalls += 1 })
            }
        }
        composeRule.waitForIdle()
        scope.testScheduler.advanceUntilIdle()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag("settings-sign-out").performScrollTo().assertIsDisplayed().performClick()

        assertTrue(signOutCalls == 1)
    }
}

private class FakeSettingsScreenRepository : SettingsRepository {
    override suspend fun getSettings(): UserSettings = UserSettings()

    override suspend fun saveSettings(settings: UserSettings): UserSettings = settings
}
