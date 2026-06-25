package org.streamofworship.android.feature.player

import android.content.res.Configuration
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Forward10
import androidx.compose.material.icons.outlined.Fullscreen
import androidx.compose.material.icons.outlined.Pause
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Replay10
import androidx.compose.material.icons.outlined.SkipNext
import androidx.compose.material.icons.outlined.SkipPrevious
import androidx.compose.material.icons.outlined.Subtitles
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.media3.common.util.UnstableApi
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState
import org.streamofworship.android.core.util.findActivity

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
    val lifecycleOwner = LocalLifecycleOwner.current
    val activity = remember(context) { context.findActivity() }

    LaunchedEffect(viewModel) {
        if (state.mediaUrl == null && !state.isLoading) viewModel.load()
    }
    LaunchedEffect(state.isPlaying) {
        wakeLock.update(state.isPlaying)
    }
    // Keyed on the controller (not the ViewModel): the controller owns the native ExoPlayer
    // resource. The ViewModel survives configuration changes; the controller is recreated on
    // rotation and must be released when its reference changes.
    DisposableEffect(media3Controller) {
        onDispose {
            wakeLock.release()
            media3Controller?.release()
        }
    }
    // Re-bind media + restore the saved position after rotation (no autoplay). When the
    // controller is fresh but the ViewModel already holds a URL, reload the media and seek to
    // the retained position. `durationMillis <= 0L` is a proxy for "no media loaded yet".
    // The effect also re-binds the surviving ViewModel to the (possibly new) controller so
    // that subsequent play/pause/seek commands route to the live ExoPlayer — the controller
    // captured at VM construction may have already been released by the previous composition's
    // DisposableEffect on rotation.
    LaunchedEffect(media3Controller, state.mediaUrl) {
        val controller = media3Controller ?: return@LaunchedEffect
        viewModel.bindController(controller)
        val url = state.mediaUrl ?: return@LaunchedEffect
        if (controller.durationMillis <= 0L) {
            controller.setMedia(url, isVideo = true)
            if (state.positionMillis > 0L) {
                controller.seekTo(state.positionMillis)
            }
        }
    }
    // Pause playback when the app goes to the background. There is no background-audio
    // requirement for the video-only worship screen, so a simple ON_STOP pause suffices.
    // The observer reads the latest isPlaying value via the StateFlow (not a snapshot
    // captured at composition time) so that playback started after the observer was
    // registered is still paused.
    DisposableEffect(lifecycleOwner) {
        val observer =
            LifecycleEventObserver { _, event ->
                if (event == Lifecycle.Event.ON_STOP && viewModel.uiState.value.isPlaying) {
                    viewModel.pause()
                }
            }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    // Immersive OS-level bar control for fullscreen. Uses DisposableEffect so the system
    // bars are restored in two scenarios: when fullscreen is toggled off (key change →
    // onDispose + new effect with show), and when the composable leaves the composition
    // while still in fullscreen (e.g. user navigates back without exiting fullscreen first).
    // Without this restore, the bars would remain hidden for the rest of the app.
    DisposableEffect(state.isFullscreen) {
        val a = activity
        val window = a?.window
        val controller = if (window != null) WindowInsetsControllerCompat(window, window.decorView) else null
        if (controller != null) {
            if (state.isFullscreen) {
                controller.hide(WindowInsetsCompat.Type.systemBars())
                controller.systemBarsBehavior =
                    WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            } else {
                controller.show(WindowInsetsCompat.Type.systemBars())
            }
        }
        onDispose {
            // Always restore system bars on dispose — covers both the toggle-off path and
            // the leaves-composition-while-fullscreen path.
            controller?.show(WindowInsetsCompat.Type.systemBars())
        }
    }
    // While in fullscreen, system/gesture back exits fullscreen first. Otherwise back
    // navigates as normal (pops the screen).
    BackHandler(enabled = state.isFullscreen) {
        viewModel.toggleFullscreen()
    }

    if (state.isFullscreen && media3Controller != null) {
        Box(
            modifier = Modifier.fillMaxSize().background(Color.Black).testTag("player-fullscreen"),
            contentAlignment = Alignment.Center,
        ) {
            AndroidView(
                factory = {
                    createSowPlayerView(
                        context = it,
                        player = videoPlayer,
                        mode = SowPlayerViewMode.Fullscreen,
                        diagnostics =
                            PlaybackDiagnostics(
                                renderJobId = viewModel.renderJobId,
                                artifact = state.artifact,
                            ),
                    )
                },
                update = {
                    it.configureSowPlayerView(
                        player = videoPlayer,
                        useController = true,
                    )
                },
                modifier = Modifier.fillMaxSize(),
            )
            IconButton(
                onClick = { viewModel.toggleFullscreen() },
                modifier =
                    Modifier
                        .align(Alignment.TopStart)
                        .padding(16.dp)
                        .testTag("player-fullscreen-exit"),
            ) {
                Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Exit fullscreen")
            }
            FullscreenPlaybackOverlays(
                state = state,
                onRetry = viewModel::retryPlayback,
                onDismissError = viewModel::dismissPlaybackError,
            )
        }
        return
    }

    val configuration = LocalConfiguration.current
    val isLandscape = configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
    var lyricsExpanded by remember { mutableStateOf(false) }
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
        SoftwareDecoderWarningBanner(state)
        PlaybackErrorPanel(
            error = state.playbackError,
            onRetry = viewModel::retryPlayback,
            onDismiss = viewModel::dismissPlaybackError,
        )
        if (media3Controller != null) {
            AndroidView(
                factory = {
                    createSowPlayerView(
                        context = it,
                        player = videoPlayer,
                        mode = SowPlayerViewMode.Inline,
                        diagnostics =
                            PlaybackDiagnostics(
                                renderJobId = viewModel.renderJobId,
                                artifact = state.artifact,
                            ),
                    )
                },
                update = {
                    it.configureSowPlayerView(
                        player = videoPlayer,
                        useController = false,
                    )
                },
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
            IconButton(
                onClick = { lyricsExpanded = !lyricsExpanded },
                modifier = Modifier.testTag("player-lyrics-toggle"),
            ) {
                Icon(Icons.Outlined.Subtitles, contentDescription = "Lyrics")
            }
            IconButton(onClick = viewModel::toggleFullscreen) {
                Icon(Icons.Outlined.Fullscreen, contentDescription = "Fullscreen")
            }
        }
        if (lyricsExpanded) {
            val manifest = state.manifest
            if (manifest != null) {
                LyricsPanel(
                    manifest = manifest,
                    positionMillis = state.positionMillis,
                    currentChapter = state.currentChapter,
                    currentLine = state.currentLine,
                    onJumpToChapter = viewModel::jumpToChapter,
                    onJumpToLine = viewModel::jumpToLine,
                    modifier = Modifier.weight(1f).fillMaxWidth().testTag("player-lyrics-panel"),
                )
            }
        }
    }
}

@Composable
private fun BoxScope.FullscreenPlaybackOverlays(
    state: PlayerUiState,
    onRetry: () -> Unit,
    onDismissError: () -> Unit,
) {
    if (state.softwareDecoderWarning) {
        SoftwareDecoderWarningBanner(
            state = state,
            modifier =
                Modifier
                    .align(Alignment.BottomCenter)
                    .padding(16.dp)
                    .widthIn(max = 520.dp),
        )
    }
    state.playbackError?.let { error ->
        Surface(
            modifier =
                Modifier
                    .align(Alignment.Center)
                    .padding(24.dp)
                    .widthIn(max = 520.dp)
                    .testTag("player-playback-error-overlay"),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 6.dp,
            shape = MaterialTheme.shapes.medium,
        ) {
            PlaybackErrorContent(
                error = error,
                onRetry = onRetry,
                onDismiss = onDismissError,
                modifier = Modifier.padding(20.dp),
            )
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

@Composable
private fun SoftwareDecoderWarningBanner(
    state: PlayerUiState,
    modifier: Modifier = Modifier,
) {
    if (!state.softwareDecoderWarning) return
    Surface(
        modifier = modifier.fillMaxWidth().testTag("player-software-decoder-warning"),
        color = MaterialTheme.colorScheme.tertiaryContainer,
        contentColor = MaterialTheme.colorScheme.onTertiaryContainer,
        shape = MaterialTheme.shapes.small,
    ) {
        Text(
            text = "Video playback is using software decoding. Battery may drain faster.",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
        )
    }
}

@Composable
private fun PlaybackErrorPanel(
    error: PlaybackUiError?,
    onRetry: () -> Unit,
    onDismiss: () -> Unit,
) {
    error ?: return
    Surface(
        modifier = Modifier.fillMaxWidth().testTag("player-playback-error-panel"),
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        shape = MaterialTheme.shapes.small,
    ) {
        PlaybackErrorContent(
            error = error,
            onRetry = onRetry,
            onDismiss = onDismiss,
            modifier = Modifier.padding(16.dp),
        )
    }
}

@Composable
private fun PlaybackErrorContent(
    error: PlaybackUiError,
    onRetry: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(error.title, style = MaterialTheme.typography.titleMedium)
        Text(error.message, style = MaterialTheme.typography.bodyMedium)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedButton(onClick = onDismiss, modifier = Modifier.testTag("player-playback-error-dismiss")) {
                Text("Dismiss")
            }
            Spacer(modifier = Modifier.width(8.dp))
            Button(onClick = onRetry, modifier = Modifier.testTag("player-playback-error-retry")) {
                Text("Retry")
            }
        }
    }
}
