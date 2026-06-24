package org.streamofworship.android.feature.settings

import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.streamofworship.android.data.settings.SettingsRepository
import org.streamofworship.android.data.settings.UserSettings

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {
    @Test
    fun `validates and saves settings`() =
        runTest {
            val repository = FakeSettingsRepository()
            val viewModel = SettingsViewModel(repository, this)

            viewModel.load()
            advanceUntilIdle()
            viewModel.update { it.copy(defaultGapBeats = 20.0) }
            viewModel.save()
            assertEquals("Default gap must be between 0 and 16 beats.", viewModel.uiState.value.validationMessage)

            viewModel.update { it.copy(defaultGapBeats = 4.0, defaultResolution = "1080p") }
            viewModel.save()
            advanceUntilIdle()

            assertTrue(viewModel.uiState.value.saved)
            assertEquals(4.0, repository.saved.defaultGapBeats, 0.0)
            assertEquals("1080p", repository.saved.defaultResolution)
        }
}

private class FakeSettingsRepository : SettingsRepository {
    var saved = UserSettings()

    override suspend fun getSettings(): UserSettings = saved

    override suspend fun saveSettings(settings: UserSettings): UserSettings {
        saved = settings
        return settings
    }
}
