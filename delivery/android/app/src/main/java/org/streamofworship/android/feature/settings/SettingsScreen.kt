package org.streamofworship.android.feature.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState

@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onSignOut: () -> Unit = {},
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    LaunchedEffect(viewModel) {
        if (!state.isLoading) viewModel.load()
    }
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp)
                .testTag("settings-screen"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Settings", style = MaterialTheme.typography.headlineSmall)
        if (state.isLoading) SowLoadingState(label = "Loading settings")
        state.serverMessage?.let { SowErrorState(title = "Settings", message = it) }
        state.validationMessage?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.testTag("settings-validation")) }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
            Text("Auto-cache renders")
            Switch(
                checked = state.settings.offlineAutoCache,
                onCheckedChange = { checked -> viewModel.update { it.copy(offlineAutoCache = checked) } },
                modifier = Modifier.testTag("settings-cache-toggle"),
            )
        }
        ChoiceRow("Template", ValidVideoTemplates.toList(), state.settings.defaultVideoTemplate) {
            viewModel.update { settings -> settings.copy(defaultVideoTemplate = it) }
        }
        ChoiceRow("Resolution", ValidResolutions.toList(), state.settings.defaultResolution) {
            viewModel.update { settings -> settings.copy(defaultResolution = it) }
        }
        ChoiceRow("Font size", ValidFontSizePresets.toList(), state.settings.defaultFontSizePreset) {
            viewModel.update { settings -> settings.copy(defaultFontSizePreset = it) }
        }
        ChoiceRow("Font", ValidRenderFonts.toList(), state.settings.defaultFontFamily) {
            viewModel.update { settings -> settings.copy(defaultFontFamily = it) }
        }
        Button(onClick = viewModel::save, enabled = !state.isSaving, modifier = Modifier.fillMaxWidth().testTag("settings-save")) {
            Text(if (state.isSaving) "Saving..." else "Save")
        }
        if (state.saved) Text("Saved", modifier = Modifier.testTag("settings-saved"))
        OutlinedButton(onClick = onSignOut, modifier = Modifier.fillMaxWidth().testTag("settings-sign-out")) {
            Text("Sign out")
        }
    }
}

@Composable
private fun ChoiceRow(
    title: String,
    options: List<String>,
    selected: String,
    onSelected: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            options.forEach { option ->
                FilterChip(selected = option == selected, onClick = { onSelected(option) }, label = { Text(option) })
            }
        }
    }
}
