package org.streamofworship.android.feature.player

import android.content.res.Configuration
import androidx.annotation.OptIn
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Forward10
import androidx.compose.material.icons.outlined.Fullscreen
import androidx.compose.material.icons.outlined.Pause
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Replay10
import androidx.compose.material.icons.outlined.SkipNext
import androidx.compose.material.icons.outlined.SkipPrevious
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState

@OptIn(UnstableApi::class)
@Composable
fun PlayerScreen(
    viewModel: PlayerViewModel,
    media3Controller: Media3PlayerController?,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    val videoPlayer by
        (media3Controller?.playerViewState?.collectAsState() ?: remember { mutableStateOf(null) })
    val context = LocalContext.current
    val wakeLock = remember(context) { PlaybackWakeLock(context.applicationContext) }
    LaunchedEffect(viewModel) {
        if (state.mediaUrl == null && !state.isLoading) viewModel.load()
    }
    LaunchedEffect(state.isPlaying) {
        wakeLock.update(state.isPlaying)
    }
    DisposableEffect(viewModel) {
        onDispose {
            wakeLock.release()
            media3Controller?.release()
        }
    }

    val configuration = LocalConfiguration.current
    val isLandscape = configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .padding(16.dp)
                .testTag("player-screen"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
        }
        if (state.isLoading) SowLoadingState(label = "Loading playback")
        OfflinePlaybackBanner(state = state, onRetry = { viewModel.load(state.artifact) })
        if (state.artifact == PlaybackArtifact.Video && media3Controller != null) {
            AndroidView(
                factory = { PlayerView(it).apply { player = videoPlayer } },
                update = { it.player = videoPlayer },
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .height(
                            when {
                                state.isFullscreen -> 420.dp
                                isLandscape -> 180.dp
                                else -> 220.dp
                            },
                        )
                        .testTag("player-video-view"),
            )
        }
        Text(state.currentChapter?.title ?: "Rendered worship set", style = MaterialTheme.typography.titleLarge)
        Text(state.currentLine?.text ?: "", color = MaterialTheme.colorScheme.onSurfaceVariant)
        LinearProgressIndicator(
            progress = { if (state.durationMillis > 0) state.positionMillis.toFloat() / state.durationMillis else 0f },
            modifier = Modifier.fillMaxWidth().testTag("player-progress"),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            IconButton(onClick = viewModel::previousChapter) {
                Icon(Icons.Outlined.SkipPrevious, contentDescription = "Previous chapter")
            }
            IconButton(onClick = { viewModel.skipBy(-10_000) }) {
                Icon(Icons.Outlined.Replay10, contentDescription = "Back 10 seconds")
            }
            Button(onClick = viewModel::playPause, modifier = Modifier.weight(1f)) {
                Icon(if (state.isPlaying) Icons.Outlined.Pause else Icons.Outlined.PlayArrow, contentDescription = null)
                Text(if (state.isPlaying) "Pause" else "Play")
            }
            IconButton(onClick = { viewModel.skipBy(10_000) }) {
                Icon(Icons.Outlined.Forward10, contentDescription = "Forward 10 seconds")
            }
            IconButton(onClick = viewModel::nextChapter) {
                Icon(Icons.Outlined.SkipNext, contentDescription = "Next chapter")
            }
            IconButton(onClick = viewModel::toggleFullscreen) {
                Icon(Icons.Outlined.Fullscreen, contentDescription = "Fullscreen")
            }
        }
        LazyColumn(modifier = Modifier.testTag("player-jump-list")) {
            items(state.manifest?.chapters.orEmpty()) { chapter ->
                OutlinedButton(onClick = { viewModel.jumpToChapter(chapter) }, modifier = Modifier.fillMaxWidth()) {
                    Text("${chapter.position}. ${chapter.title}")
                }
            }
        }
    }
}

@Composable
private fun ColumnScope.OfflinePlaybackBanner(
    state: PlayerUiState,
    onRetry: () -> Unit,
) {
    when (state.offlineState) {
        OfflinePlaybackState.Cached ->
            Text(
                "Playing cached artifact",
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.testTag("player-offline-state"),
            )
        OfflinePlaybackState.Missing ->
            Text(
                state.message ?: "Not cached on this device.",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("player-offline-state"),
            )
        OfflinePlaybackState.ExpiredSignedUrl ->
            SowErrorState(
                title = "Playback link expired",
                message = state.message ?: "Retry to refresh the signed URL.",
                actionLabel = "Retry",
                onAction = onRetry,
                modifier = Modifier.testTag("player-expired-url-state"),
            )
        else ->
            state.message?.let { SowErrorState(title = "Playback", message = it) }
    }
}
