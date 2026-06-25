package org.streamofworship.android.feature.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.data.settings.SettingsRepository
import org.streamofworship.android.data.settings.UserSettings

data class SettingsUiState(
    val settings: UserSettings = UserSettings(),
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val validationMessage: String? = null,
    val serverMessage: String? = null,
    val saved: Boolean = false,
)

class SettingsViewModel(
    private val repository: SettingsRepository,
    private val scope: CoroutineScope? = null,
) : ViewModel() {
    private val mutableState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = mutableState
    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    fun load() {
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, serverMessage = null) }
            runCatching { repository.getSettings() }
                .onSuccess { settings -> mutableState.update { it.copy(settings = settings, isLoading = false) } }
                .onFailure { error -> mutableState.update { it.copy(isLoading = false, serverMessage = error.statusMessage()) } }
        }
    }

    fun update(transform: (UserSettings) -> UserSettings) {
        mutableState.update {
            val settings = transform(it.settings)
            it.copy(settings = settings, validationMessage = settingsValidationError(settings), saved = false)
        }
    }

    fun save() {
        if (mutableState.value.isSaving) return
        val settings = mutableState.value.settings
        val validation = settingsValidationError(settings)
        if (validation != null) {
            mutableState.update { it.copy(validationMessage = validation) }
            return
        }
        mutableState.update { it.copy(isSaving = true, serverMessage = null, saved = false) }
        launchScope.launch {
            runCatching { repository.saveSettings(settings) }
                .onSuccess { saved -> mutableState.update { it.copy(settings = saved, isSaving = false, saved = true) } }
                .onFailure { error -> mutableState.update { it.copy(isSaving = false, serverMessage = error.statusMessage()) } }
        }
    }
}

private fun Throwable.statusMessage(): String =
    when (this) {
        is ApiException -> error.message
        else -> message ?: "Settings request failed"
    }
