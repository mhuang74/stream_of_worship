package org.streamofworship.android.feature.player

import android.content.Context
import android.graphics.Color
import android.util.Log
import android.view.View
import androidx.annotation.OptIn
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import org.streamofworship.android.BuildConfig

private const val SOW_VIDEO_LOG_TAG = "SowVideo"

internal data class PlaybackDiagnostics(
    val renderJobId: String? = null,
    val artifact: PlaybackArtifact? = null,
) {
    fun logPrefix(): String {
        val job = renderJobId?.takeIf { it.isNotBlank() } ?: "unknown"
        val kind = artifact?.name ?: "unknown"
        return "renderJobId=$job artifact=$kind"
    }
}

internal object SowVideoLogger {
    val enabled: Boolean
        get() = BuildConfig.DEBUG

    fun debug(
        diagnostics: PlaybackDiagnostics,
        message: () -> String,
    ) {
        if (!enabled) return
        Log.d(SOW_VIDEO_LOG_TAG, "${diagnostics.logPrefix()} ${message()}")
    }

    fun error(
        diagnostics: PlaybackDiagnostics,
        throwable: Throwable,
        message: () -> String,
    ) {
        if (!enabled) return
        Log.e(SOW_VIDEO_LOG_TAG, "${diagnostics.logPrefix()} ${message()}", throwable)
    }
}

internal enum class SowPlayerViewMode {
    Inline,
    Fullscreen,
}

@OptIn(UnstableApi::class)
internal fun createSowPlayerView(
    context: Context,
    player: Player?,
    mode: SowPlayerViewMode,
    diagnostics: PlaybackDiagnostics,
): PlayerView =
    PlayerView(context).apply {
        configureSowPlayerView(
            player = player,
            useController = mode == SowPlayerViewMode.Fullscreen,
        )
        attachPlayerViewDiagnostics(mode, diagnostics)
    }

@OptIn(UnstableApi::class)
internal fun PlayerView.configureSowPlayerView(
    player: Player?,
    useController: Boolean,
) {
    setBackgroundColor(Color.BLACK)
    setShutterBackgroundColor(Color.BLACK)
    setUseController(useController)
    setResizeMode(AspectRatioFrameLayout.RESIZE_MODE_FIT)
    setUseArtwork(false)
    setDefaultArtwork(null)
    setArtworkDisplayMode(PlayerView.ARTWORK_DISPLAY_MODE_OFF)
    setShowBuffering(PlayerView.SHOW_BUFFERING_WHEN_PLAYING)
    setKeepContentOnPlayerReset(false)
    setEnableComposeSurfaceSyncWorkaround(true)
    this.player = player
}

@OptIn(UnstableApi::class)
private fun PlayerView.attachPlayerViewDiagnostics(
    mode: SowPlayerViewMode,
    diagnostics: PlaybackDiagnostics,
) {
    if (!SowVideoLogger.enabled) return
    addOnLayoutChangeListener(
        object : View.OnLayoutChangeListener {
            override fun onLayoutChange(
                view: View,
                left: Int,
                top: Int,
                right: Int,
                bottom: Int,
                oldLeft: Int,
                oldTop: Int,
                oldRight: Int,
                oldBottom: Int,
            ) {
                val width = right - left
                val height = bottom - top
                val oldWidth = oldRight - oldLeft
                val oldHeight = oldBottom - oldTop
                if (width == oldWidth && height == oldHeight) return
                val surface = getVideoSurfaceView()
                val surfaceWidth = surface?.width ?: 0
                val surfaceHeight = surface?.height ?: 0
                SowVideoLogger.debug(diagnostics) {
                    "playerViewLayout mode=$mode view=${width}x$height " +
                        "surface=${surfaceWidth}x${surfaceHeight} attached=${view.isAttachedToWindow}"
                }
            }
        },
    )
}
