package org.streamofworship.android.feature.player

import android.content.ComponentName
import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.Locale

class Media3PlayerController private constructor(
    internal val player: MediaPlayerFacade,
) : PlayerController {
    constructor(context: Context) : this(ServiceMediaControllerFacade(context.applicationContext))

    constructor(
        player: Player,
        renderJobId: String? = null,
        artifact: PlaybackArtifact? = null,
    ) : this(
        DirectPlayerFacade(
            playerView = player,
            diagnostics = PlaybackDiagnostics(renderJobId = renderJobId, artifact = artifact),
        ),
    )

    internal constructor(
        fakePlayer: MediaPlayerFacade,
        @Suppress("UNUSED_PARAMETER") forTest: Unit = Unit,
    ) : this(fakePlayer)

    private val playerViewHost = player as? PlayerViewHost

    /**
     * Underlying [Player] suitable for a [androidx.media3.ui.PlayerView] (e.g. for video
     * rendering). Null until the service-bound controller is connected, or for fake facades.
     */
    val playerView: Player?
        get() = playerViewHost?.playerView

    val playerViewState: StateFlow<Player?> =
        playerViewHost?.playerViewState ?: MutableStateFlow(null)

    override val durationMillis: Long
        get() = player.durationMillis.takeIf { it > 0 } ?: 0L

    override val positionMillis: Long
        get() = player.positionMillis.coerceAtLeast(0L)

    override val isPlaying: Boolean
        get() = player.isPlaying

    override fun setMedia(
        url: String,
        isVideo: Boolean,
    ) {
        player.setMedia(url)
        player.prepare()
    }

    override fun play() {
        player.play()
    }

    override fun pause() {
        player.pause()
    }

    override fun seekTo(positionMillis: Long) {
        player.seekTo(positionMillis.coerceAtLeast(0L))
    }

    override fun release() {
        player.release()
    }

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        player.setEventListener(listener)
    }
}

internal interface MediaPlayerFacade {
    val durationMillis: Long

    val positionMillis: Long

    val isPlaying: Boolean

    fun setMedia(url: String)

    fun prepare()

    fun play()

    fun pause()

    fun seekTo(positionMillis: Long)

    fun release()

    fun setEventListener(listener: PlayerController.PlayerEventListener?)
}

internal interface PlayerViewHost {
    val playerView: Player?

    val playerViewState: StateFlow<Player?>
}

/**
 * Binds commands to a [Player] (e.g. an in-process [androidx.media3.exoplayer.ExoPlayer]) and
 * forwards [Player.Listener] callbacks into [PlayerController.PlayerEventListener] events.
 */
internal class DirectPlayerFacade(
    override val playerView: Player,
    private val diagnostics: PlaybackDiagnostics = PlaybackDiagnostics(),
) : MediaPlayerFacade, PlayerViewHost {
    private var eventListener: PlayerController.PlayerEventListener? = null
    private var listenerAdapter: Player.Listener? = null
    private val diagnosticListener: Player.Listener? = createVideoDiagnosticListener()
    private val analyticsListener: AnalyticsListener = createVideoAnalyticsListener()
    private val mutablePlayerViewState: MutableStateFlow<Player?> = MutableStateFlow(playerView)
    override val playerViewState: StateFlow<Player?> = mutablePlayerViewState

    init {
        diagnosticListener?.let { playerView.addListener(it) }
        (playerView as? ExoPlayer)?.addAnalyticsListener(analyticsListener)
    }

    override val durationMillis: Long get() = playerView.duration

    override val positionMillis: Long get() = playerView.currentPosition

    override val isPlaying: Boolean get() = playerView.isPlaying

    override fun setMedia(url: String) {
        SowVideoLogger.debug(diagnostics) { "setMedia" }
        playerView.setMediaItem(MediaItem.fromUri(url))
    }

    override fun prepare() {
        SowVideoLogger.debug(diagnostics) { "prepare" }
        playerView.prepare()
    }

    override fun play() {
        playerView.play()
    }

    override fun pause() {
        playerView.pause()
    }

    override fun seekTo(positionMillis: Long) {
        playerView.seekTo(positionMillis)
    }

    override fun release() {
        listenerAdapter?.let { playerView.removeListener(it) }
        diagnosticListener?.let { playerView.removeListener(it) }
        (playerView as? ExoPlayer)?.removeAnalyticsListener(analyticsListener)
        eventListener = null
        listenerAdapter = null
        playerView.release()
        mutablePlayerViewState.value = null
    }

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        listenerAdapter?.let { playerView.removeListener(it) }
        listenerAdapter = null
        eventListener = listener
        if (listener == null) return
        val adapter =
            object : Player.Listener {
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    listener.onEvent(PlayerEvent.IsPlayingChanged(isPlaying))
                }

                override fun onPlayerError(error: PlaybackException) {
                    listener.onEvent(error.toPlayerErrorEvent())
                }

                override fun onPositionDiscontinuity(
                    oldPosition: Player.PositionInfo,
                    newPosition: Player.PositionInfo,
                    reason: Int,
                ) {
                    listener.onEvent(PlayerEvent.PositionDiscontinuity)
                }
            }
        playerView.addListener(adapter)
        listenerAdapter = adapter
    }

    @OptIn(UnstableApi::class)
    private fun createVideoAnalyticsListener(): AnalyticsListener =
        object : AnalyticsListener {
            override fun onVideoDecoderInitialized(
                eventTime: AnalyticsListener.EventTime,
                decoderName: String,
                initializedTimestampMs: Long,
                initializationDurationMs: Long,
            ) {
                val softwareDecoder = decoderName.isSoftwareVideoDecoder()
                SowVideoLogger.debug(diagnostics) {
                    "videoDecoderInitialized name=$decoderName software=$softwareDecoder"
                }
                eventListener?.onEvent(
                    PlayerEvent.VideoDecoderChanged(
                        decoderName = decoderName,
                        softwareDecoderActive = softwareDecoder,
                    ),
                )
            }
        }

    private fun createVideoDiagnosticListener(): Player.Listener? {
        if (!SowVideoLogger.enabled) return null
        return object : Player.Listener {
            override fun onVideoSizeChanged(videoSize: VideoSize) {
                SowVideoLogger.debug(diagnostics) {
                    "videoSize=${videoSize.width}x${videoSize.height} " +
                        "rotation=${videoSize.unappliedRotationDegrees} " +
                        "pixelRatio=${videoSize.pixelWidthHeightRatio}"
                }
            }

            override fun onSurfaceSizeChanged(
                width: Int,
                height: Int,
            ) {
                SowVideoLogger.debug(diagnostics) { "surfaceSize=${width}x$height" }
            }

            override fun onTracksChanged(tracks: Tracks) {
                val format = tracks.selectedVideoFormat()
                if (format == null) {
                    SowVideoLogger.debug(diagnostics) { "selectedVideoFormat=none" }
                } else {
                    SowVideoLogger.debug(diagnostics) { "selectedVideoFormat=${format.videoSummary()}" }
                }
            }

            override fun onRenderedFirstFrame() {
                SowVideoLogger.debug(diagnostics) { "renderedFirstFrame" }
            }

                override fun onPlayerError(error: PlaybackException) {
                    SowVideoLogger.error(diagnostics, error) {
                        "playerError code=${error.errorCodeName} cause=${error.cause?.javaClass?.name ?: "none"}"
                }
            }
        }
    }
}

private fun Tracks.selectedVideoFormat(): Format? {
    for (group in getGroups()) {
        if (group.type != C.TRACK_TYPE_VIDEO) continue
        for (index in 0 until group.length) {
            if (group.isTrackSelected(index)) return group.getTrackFormat(index)
        }
    }
    return null
}

@OptIn(UnstableApi::class)
private fun Format.videoSummary(): String =
    "mime=${sampleMimeType ?: "unknown"} " +
        "codecs=${codecs ?: "unknown"} " +
        "size=${width}x$height " +
        "frameRate=$frameRate " +
        "rotation=$rotationDegrees " +
        "pixelRatio=$pixelWidthHeightRatio " +
        "bitrate=$bitrate"

internal fun String.isSoftwareVideoDecoder(): Boolean {
    val normalized = lowercase(Locale.US)
    return normalized.contains("c2.android") ||
        normalized.contains("omx.google") ||
        normalized.contains("ffmpeg") ||
        normalized.contains("software")
}

private fun PlaybackException.toPlayerErrorEvent(): PlayerEvent.Error =
    PlayerEvent.Error(
        message = message ?: "Playback error",
        kind = if (isDecoderPlaybackError()) PlaybackErrorKind.Decoder else PlaybackErrorKind.Generic,
    )

private fun PlaybackException.isDecoderPlaybackError(): Boolean =
    errorCode == PlaybackException.ERROR_CODE_DECODER_INIT_FAILED ||
        errorCode == PlaybackException.ERROR_CODE_DECODER_QUERY_FAILED ||
        errorCode == PlaybackException.ERROR_CODE_DECODING_FAILED ||
        errorCode == PlaybackException.ERROR_CODE_DECODING_FORMAT_EXCEEDS_CAPABILITIES ||
        errorCode == PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED

/**
 * Connects to the in-process [SowPlaybackService] session through a [MediaController].
 *
 * Media3's [MediaController] queues commands issued before the connection completes, so the
 * synchronous [PlayerController] API remains safe; position/duration/isPlaying report defaults
 * until the connection flushes a synchronized snapshot. Lifecycle-aware player events are
 * surfaced through [setEventListener] after the controller binds.
 */
internal class ServiceMediaControllerFacade(
    context: Context,
) : MediaPlayerFacade, PlayerViewHost {
    private val appContext = context.applicationContext
    private val sessionToken =
        SessionToken(appContext, ComponentName(appContext, SowPlaybackService::class.java))
    private val controllerFuture: ListenableFuture<MediaController> =
        MediaController.Builder(appContext, sessionToken).buildAsync()
    private var boundController: MediaController? = null
    private var listenerAdapter: Player.Listener? = null
    private val pendingCommands = ArrayDeque<(MediaController) -> Unit>()
    private val lock = Any()
    private var released = false
    override val playerViewState = MutableStateFlow<Player?>(null)

    init {
        controllerFuture.addListener(
            {
                val controller = runCatching { controllerFuture.get() }.getOrNull()
                val commands =
                    synchronized(lock) {
                        if (released || controller == null) {
                            emptyList()
                        } else {
                            boundController = controller
                            listenerAdapter?.let { controller.addListener(it) }
                            playerViewState.value = controller
                            pendingCommands.toList().also { pendingCommands.clear() }
                        }
                    }
                if (controller != null) {
                    commands.forEach { it(controller) }
                }
            },
            MoreExecutors.directExecutor(),
        )
    }

    override val playerView: Player?
        get() = boundController

    override val durationMillis: Long
        get() = boundController?.duration ?: 0L

    override val positionMillis: Long
        get() = boundController?.currentPosition ?: 0L

    override val isPlaying: Boolean
        get() = boundController?.isPlaying ?: false

    override fun setMedia(url: String) {
        runWhenBound { it.setMediaItem(MediaItem.fromUri(url)) }
    }

    override fun prepare() {
        runWhenBound { it.prepare() }
    }

    override fun play() {
        runWhenBound { it.play() }
    }

    override fun pause() {
        runWhenBound { it.pause() }
    }

    override fun seekTo(positionMillis: Long) {
        runWhenBound { it.seekTo(positionMillis) }
    }

    override fun release() {
        synchronized(lock) {
            released = true
            listenerAdapter?.let { boundController?.removeListener(it) }
            pendingCommands.clear()
            boundController = null
            listenerAdapter = null
            playerViewState.value = null
        }
        MediaController.releaseFuture(controllerFuture)
    }

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        val previous = listenerAdapter
        val controller = boundController
        previous?.let { controller?.removeListener(it) }
        if (listener == null) {
            listenerAdapter = null
            return
        }
        val adapter =
            object : Player.Listener {
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    listener.onEvent(PlayerEvent.IsPlayingChanged(isPlaying))
                }

                override fun onPlayerError(error: PlaybackException) {
                    listener.onEvent(error.toPlayerErrorEvent())
                }

                override fun onPositionDiscontinuity(
                    oldPosition: Player.PositionInfo,
                    newPosition: Player.PositionInfo,
                    reason: Int,
                ) {
                    listener.onEvent(PlayerEvent.PositionDiscontinuity)
                }
            }
        synchronized(lock) {
            listenerAdapter = adapter
            boundController?.addListener(adapter)
        }
    }

    private fun runWhenBound(command: (MediaController) -> Unit) {
        val controller =
            synchronized(lock) {
                if (released) return
                boundController
                    ?: run {
                        pendingCommands += command
                        return
                    }
            }
        command(controller)
    }
}
