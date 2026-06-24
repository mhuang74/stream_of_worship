package org.streamofworship.android.feature.player

import androidx.annotation.OptIn
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.Modifier
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
    val context = LocalContext.current
    val wakeLock = androidx.compose.runtime.remember(context) { PlaybackWakeLock(context.applicationContext) }
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
        state.message?.let { SowErrorState(title = "Playback", message = it) }
        if (state.artifact == PlaybackArtifact.Video && media3Controller != null) {
            AndroidView(
                factory = { PlayerView(it).apply { player = media3Controller.player } },
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .height(if (state.isFullscreen) 420.dp else 220.dp)
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
