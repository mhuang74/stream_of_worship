package org.streamofworship.android.feature.player

import android.content.ComponentName
import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors

class Media3PlayerController private constructor(
    internal val player: MediaPlayerFacade,
) : PlayerController {
    constructor(context: Context) : this(ServiceMediaControllerFacade(context.applicationContext))

    constructor(player: Player) : this(DirectPlayerFacade(player))

    internal constructor(fakePlayer: MediaPlayerFacade, @Suppress("UNUSED_PARAMETER") forTest: Unit = Unit) : this(fakePlayer)

    /**
     * Underlying [Player] suitable for a [androidx.media3.ui.PlayerView] (e.g. for video
     * rendering). Null until the service-bound controller is connected, or for fake facades.
     */
    val playerView: Player?
        get() = (player as? PlayerViewHost)?.playerView

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
}

/**
 * Binds commands to a [Player] (e.g. an in-process [androidx.media3.exoplayer.ExoPlayer]) and
 * forwards [Player.Listener] callbacks into [PlayerController.PlayerEventListener] events.
 */
internal class DirectPlayerFacade(
    override val playerView: Player,
) : MediaPlayerFacade, PlayerViewHost {
    private var listenerAdapter: Player.Listener? = null

    override val durationMillis: Long get() = playerView.duration

    override val positionMillis: Long get() = playerView.currentPosition

    override val isPlaying: Boolean get() = playerView.isPlaying

    override fun setMedia(url: String) {
        playerView.setMediaItem(MediaItem.fromUri(url))
    }

    override fun prepare() {
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
        listenerAdapter = null
        playerView.release()
    }

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        listenerAdapter?.let { playerView.removeListener(it) }
        listenerAdapter = null
        if (listener == null) return
        val adapter =
            object : Player.Listener {
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    listener.onEvent(PlayerEvent.IsPlayingChanged(isPlaying))
                }

                override fun onPlayerErrorChanged(error: PlaybackException?) {
                    listener.onEvent(PlayerEvent.Error(error?.message ?: "Playback error"))
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
}

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

    init {
        controllerFuture.addListener(
            {
                boundController =
                    runCatching { controllerFuture.get() }.getOrNull()
                listenerAdapter?.let { boundController?.addListener(it) }
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
        boundController?.setMediaItem(MediaItem.fromUri(url))
    }

    override fun prepare() {
        boundController?.prepare()
    }

    override fun play() {
        boundController?.play()
    }

    override fun pause() {
        boundController?.pause()
    }

    override fun seekTo(positionMillis: Long) {
        boundController?.seekTo(positionMillis)
    }

    override fun release() {
        MediaController.releaseFuture(controllerFuture)
        boundController = null
        listenerAdapter = null
    }

    override fun setEventListener(listener: PlayerController.PlayerEventListener?) {
        listenerAdapter?.let { boundController?.removeListener(it) }
        listenerAdapter = null
        if (listener == null) return
        val adapter =
            object : Player.Listener {
                override fun onIsPlayingChanged(isPlaying: Boolean) {
                    listener.onEvent(PlayerEvent.IsPlayingChanged(isPlaying))
                }

                override fun onPlayerErrorChanged(error: PlaybackException?) {
                    listener.onEvent(PlayerEvent.Error(error?.message ?: "Playback error"))
                }

                override fun onPositionDiscontinuity(
                    oldPosition: Player.PositionInfo,
                    newPosition: Player.PositionInfo,
                    reason: Int,
                ) {
                    listener.onEvent(PlayerEvent.PositionDiscontinuity)
                }
            }
        boundController?.addListener(adapter)
        listenerAdapter = adapter
    }
}
