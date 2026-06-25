package org.streamofworship.android.feature.player

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Foreground service hosting the playback [MediaSession] so the system can publish the
 * lock-screen / notification transport controls and so audio continues in the background.
 *
 * The service owns the [ExoPlayer] and the [MediaSession]; the activity binds through
 * [Media3PlayerController] via a [androidx.media3.session.MediaController].
 */
class SowPlaybackService : MediaSessionService() {
    private var mediaSession: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        val player =
            ExoPlayer
                .Builder(this)
                .setHandleAudioBecomingNoisy(true)
                .build()
        val sessionActivity = packageManager.getLaunchIntentForPackage(packageName)
        val builder = MediaSession.Builder(this, player)
        if (sessionActivity != null) {
            builder.setSessionActivity(
                PendingIntent.getActivity(
                    this,
                    0,
                    sessionActivity,
                    PendingIntent.FLAG_IMMUTABLE,
                ),
            )
        }
        mediaSession = builder.build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = mediaSession

    override fun onTaskRemoved(rootIntent: Intent?) {
        val player = mediaSession?.player
        if (
            player == null ||
            !player.playWhenReady ||
            player.mediaItemCount == 0 ||
            player.playbackState == Player.STATE_IDLE ||
            player.playbackState == Player.STATE_ENDED
        ) {
            stopSelf()
        }
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        mediaSession?.run {
            player.release()
            release()
        }
        mediaSession = null
        super.onDestroy()
    }
}
