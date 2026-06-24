package org.streamofworship.android.feature.share

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.IosShare
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState

@Composable
fun ShareScreen(
    viewModel: ShareViewModel,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .padding(16.dp)
                .testTag("share-screen"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
        }
        Text("Share render", style = MaterialTheme.typography.headlineSmall)
        Row(horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
            Text("Allow downloads")
            Switch(checked = state.allowDownload, onCheckedChange = viewModel::setAllowDownload)
        }
        Button(onClick = viewModel::createShare, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.Outlined.IosShare, contentDescription = null)
            Text("Create share link")
        }
        OutlinedButton(onClick = viewModel::loadDownloadUrls, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.Outlined.Download, contentDescription = null)
            Text("Prepare downloads")
        }
        if (state.isLoading) SowLoadingState(label = "Preparing")
        state.message?.let { SowErrorState(title = "Share", message = it) }
        state.shareToken?.shareUrl?.let { url ->
            Text(url, modifier = Modifier.testTag("share-url"))
            Button(
                onClick = { context.startActivity(IntentChooserFactory.create(buildShareTextIntent(url), "Share worship set")) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Send link")
            }
        }
        state.audioUrl?.let { Text("Audio ready", modifier = Modifier.testTag("share-audio-ready")) }
        state.videoUrl?.let { Text("Video ready", modifier = Modifier.testTag("share-video-ready")) }
        state.downloads.values.forEach { download ->
            Text(
                "${download.kind.name} download ${download.status.name.lowercase()}",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("share-download-${download.kind.name.lowercase()}"),
            )
            download.failureMessage?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
    }
}

private object IntentChooserFactory {
    fun create(
        intent: android.content.Intent,
        title: String,
    ): android.content.Intent = android.content.Intent.createChooser(intent, title)
}
