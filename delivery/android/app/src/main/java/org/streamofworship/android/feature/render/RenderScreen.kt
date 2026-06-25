package org.streamofworship.android.feature.render

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Cancel
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.RocketLaunch
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState
import org.streamofworship.android.data.render.ArtifactSizes
import org.streamofworship.android.data.offline.OfflineArtifactMetadata
import org.streamofworship.android.data.render.RenderFontFamily
import org.streamofworship.android.data.render.RenderFontSize
import org.streamofworship.android.data.render.RenderFormConfig
import org.streamofworship.android.data.render.RenderJob
import org.streamofworship.android.data.render.RenderJobStatus
import org.streamofworship.android.data.render.RenderResolution
import org.streamofworship.android.data.render.RenderTemplate
import org.streamofworship.android.data.render.RenderTitleCardDurations
import org.streamofworship.android.feature.player.PlaybackArtifact

@Composable
fun RenderScreen(
    viewModel: RenderViewModel,
    onBack: () -> Unit,
    onPlay: (String, PlaybackArtifact) -> Unit,
    onDownload: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    LaunchedEffect(viewModel) {
        if (state.songset == null && !state.isLoadingSongset) viewModel.load()
    }
    DisposableEffect(viewModel) {
        onDispose { viewModel.stopPolling() }
    }

    Column(
        modifier =
            modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp)
                .testTag("render-screen"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
        }
        if (state.isLoadingSongset) {
            SowLoadingState(label = "Loading render settings")
        }
        state.songset?.let { songset ->
            Text(songset.name, style = MaterialTheme.typography.headlineSmall)
            Text(
                "${songset.items.size} songs • ${formatDuration(songset.durationSeconds)}",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
        state.validationMessage?.let { message ->
            Text(
                message,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.testTag("render-validation-message"),
            )
        }
        state.serverMessage?.let { message ->
            SowErrorState(
                title = "Render update",
                message = message,
                actionLabel = if (state.currentJob != null) "Retry status" else null,
                onAction = viewModel::retryPolling,
            )
        }

        RenderForm(
            config = state.config,
            isSubmitting = state.isSubmitting,
            hasPreviousRender = state.requiresPreviousRenderConfirmation,
            canReviewPrevious = state.currentJob?.hasPlayableArtifacts == true,
            onConfigChange = viewModel::updateConfig,
            onSubmit = viewModel::requestRender,
            onConfirmPrevious = viewModel::confirmPreviousRenderAndStart,
            onReviewPrevious = {
                state.currentJob?.let { job ->
                    onPlay(job.id, job.preferredPlaybackArtifact())
                }
            },
        )

        state.currentJob?.let { job ->
            RenderStatusPanel(
                job = job,
                sizes = state.artifactSizes,
                offlineArtifacts = state.offlineArtifacts,
                isPolling = state.isPolling,
                retryCount = state.retryCount,
                onCancel = viewModel::cancelRender,
                onPlay = { onPlay(job.id, job.preferredPlaybackArtifact()) },
                onDownload = { onDownload(job.id) },
            )
        }
    }
}

private fun RenderJob.preferredPlaybackArtifact(): PlaybackArtifact =
    if (mp4R2Key != null) PlaybackArtifact.Video else PlaybackArtifact.Audio

@Composable
fun RenderForm(
    config: RenderFormConfig,
    isSubmitting: Boolean,
    hasPreviousRender: Boolean,
    canReviewPrevious: Boolean,
    onConfigChange: (RenderFormConfig) -> Unit,
    onSubmit: () -> Unit,
    onConfirmPrevious: () -> Unit,
    onReviewPrevious: () -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.testTag("render-form")) {
        RenderSection("Output Options") {
            ToggleRow(
                title = "Audio (MP3)",
                subtitle = "Mixed audio with transitions",
                checked = config.audioEnabled,
                onCheckedChange = { onConfigChange(config.copy(audioEnabled = it)) },
                testTag = "render-audio-toggle",
            )
            ToggleRow(
                title = "Video (MP4)",
                subtitle = "Lyrics video with audio",
                checked = config.videoEnabled,
                onCheckedChange = { onConfigChange(config.copy(videoEnabled = it)) },
                testTag = "render-video-toggle",
            )
        }

        if (config.videoEnabled) {
            RenderSection("Video Settings") {
                ChoiceRow(
                    title = "Template",
                    options = RenderTemplate.entries.map { it.value to it.label },
                    selected = config.template,
                    onSelected = { onConfigChange(config.copy(template = it)) },
                )
                ChoiceRow(
                    title = "Resolution",
                    options = RenderResolution.entries.map { it.value to it.label },
                    selected = config.resolution,
                    onSelected = { onConfigChange(config.copy(resolution = it)) },
                )
                ChoiceRow(
                    title = "Font Size",
                    options = RenderFontSize.entries.map { it.value to it.label },
                    selected = config.fontSizePreset,
                    onSelected = { onConfigChange(config.copy(fontSizePreset = it)) },
                )
                ChoiceRow(
                    title = "Font Family",
                    options = RenderFontFamily.entries.map { it.value to it.label },
                    selected = config.fontFamily,
                    onSelected = { onConfigChange(config.copy(fontFamily = it)) },
                )
                Text("耶和華是我的牧者", style = MaterialTheme.typography.titleMedium)
                Text("我必不至缺乏", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }

        RenderSection("Title Card") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(
                    checked = config.includeTitleCard,
                    onCheckedChange = { onConfigChange(config.copy(includeTitleCard = it)) },
                    modifier = Modifier.testTag("render-title-card-toggle"),
                )
                Text("Include title card")
            }
            if (config.includeTitleCard) {
                ChoiceRow(
                    title = "Duration",
                    options = RenderTitleCardDurations.map { it.toString() to "$it seconds" },
                    selected = config.titleCardDurationSeconds.toString(),
                    onSelected = { onConfigChange(config.copy(titleCardDurationSeconds = it.toInt())) },
                )
                OutlinedTextField(
                    value = config.titleCardLines.joinToString("\n"),
                    onValueChange = { text ->
                        onConfigChange(
                            config.copy(
                                titleCardLines =
                                    text
                                        .lineSequence()
                                        .map { it.trim() }
                                        .filter { it.isNotEmpty() }
                                        .toList(),
                            ),
                        )
                    },
                    label = { Text("Custom title card text") },
                    minLines = 3,
                    modifier = Modifier.fillMaxWidth().testTag("render-title-card-lines"),
                )
            }
        }

        if (hasPreviousRender) {
            RenderSection("Previous Render") {
                Text("A previous render exists for this songset.")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                    OutlinedButton(
                        onClick = onReviewPrevious,
                        enabled = canReviewPrevious,
                        modifier = Modifier.weight(1f).testTag("render-review-previous-button"),
                    ) {
                        Text("Review")
                    }
                    Button(onClick = onConfirmPrevious, enabled = !isSubmitting, modifier = Modifier.weight(1f)) {
                        Text("Start new")
                    }
                }
            }
        } else {
            Button(
                onClick = onSubmit,
                enabled = !isSubmitting && (config.audioEnabled || config.videoEnabled),
                modifier = Modifier.fillMaxWidth().testTag("render-start-button"),
            ) {
                Icon(Icons.Outlined.RocketLaunch, contentDescription = null)
                Text(if (isSubmitting) "Starting..." else "Start Render")
            }
        }
    }
}

@Composable
private fun RenderStatusPanel(
    job: RenderJob,
    sizes: ArtifactSizes?,
    offlineArtifacts: List<OfflineArtifactMetadata>,
    isPolling: Boolean,
    retryCount: Int,
    onCancel: () -> Unit,
    onPlay: () -> Unit,
    onDownload: () -> Unit,
) {
    RenderSection("Render Status") {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(job.status.label(), fontWeight = FontWeight.SemiBold, modifier = Modifier.testTag("render-job-status"))
            if (isPolling) AssistChip(onClick = {}, label = { Text("Polling") })
            if (retryCount > 0) AssistChip(onClick = {}, label = { Text("Retry $retryCount") })
        }
        job.phase?.let { phase ->
            Text(
                "${phase.name} ${job.phaseIndex ?: 0}/${job.totalPhases ?: 0}",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        job.errorMessage?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (job.isActive) {
            OutlinedButton(onClick = onCancel, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Outlined.Cancel, contentDescription = null)
                Text("Cancel render")
            }
        }
        if (job.hasPlayableArtifacts) {
            Text(
                artifactLabel(sizes, job),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.testTag("render-artifact-availability"),
            )
            if (offlineArtifacts.isNotEmpty()) {
                Text(
                    offlineArtifacts.joinToString(" • ") { "${it.kind.name}: ${it.status.name}" },
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.testTag("render-offline-cache-state"),
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                Button(onClick = onPlay, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Outlined.PlayArrow, contentDescription = null)
                    Text("Play")
                }
                OutlinedButton(onClick = onDownload, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Outlined.Download, contentDescription = null)
                    Text("Download")
                }
            }
        } else if (job.status == RenderJobStatus.Completed) {
            Text("Completed, but no artifacts are available.", color = MaterialTheme.colorScheme.error)
        } else if (!job.isActive) {
            OutlinedButton(onClick = onDownload, modifier = Modifier.fillMaxWidth()) {
                Icon(Icons.Outlined.Refresh, contentDescription = null)
                Text("Refresh artifacts")
            }
        }
    }
}

@Composable
private fun RenderSection(
    title: String,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun ToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    testTag: String,
) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.Medium)
            Text(subtitle, color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange, modifier = Modifier.testTag(testTag))
    }
}

@Composable
private fun ChoiceRow(
    title: String,
    options: List<Pair<String, String>>,
    selected: String,
    onSelected: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, fontWeight = FontWeight.Medium)
        options.chunked(2).forEach { rowOptions ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                rowOptions.forEach { (value, label) ->
                    FilterChip(
                        selected = selected == value,
                        onClick = { onSelected(value) },
                        label = { Text(label, maxLines = 1) },
                        modifier = Modifier.weight(1f).testTag("render-choice-$value"),
                    )
                }
                if (rowOptions.size == 1) {
                    Text("", modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

private fun RenderJobStatus.label(): String =
    when (this) {
        RenderJobStatus.Queued -> "Queued"
        RenderJobStatus.Running -> "Running"
        RenderJobStatus.Completed -> "Completed"
        RenderJobStatus.Failed -> "Failed"
        RenderJobStatus.Cancelled -> "Cancelled"
    }

private fun artifactLabel(
    sizes: ArtifactSizes?,
    job: RenderJob,
): String {
    val audio = job.mp3R2Key?.let { "MP3 ${sizes?.mp3SizeBytes?.formatBytes().orEmpty()}".trim() }
    val video = job.mp4R2Key?.let { "MP4 ${sizes?.mp4SizeBytes?.formatBytes().orEmpty()}".trim() }
    return listOfNotNull(audio, video).joinToString(" • ").ifBlank { "Artifacts ready" }
}

private fun Long.formatBytes(): String =
    when {
        this >= 1_048_576 -> "${this / 1_048_576} MB"
        this >= 1_024 -> "${this / 1_024} KB"
        else -> "$this B"
    }

private fun formatDuration(seconds: Double?): String {
    val total = seconds?.toInt() ?: return "0:00"
    val minutes = total / 60
    val remainder = total % 60
    return "$minutes:${remainder.toString().padStart(2, '0')}"
}
