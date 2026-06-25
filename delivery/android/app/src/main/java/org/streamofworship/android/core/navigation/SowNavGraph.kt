package org.streamofworship.android.core.navigation

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.download.AndroidArtifactDownloadScheduler
import org.streamofworship.android.core.download.ArtifactDownloadCoordinator
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.core.session.AndroidSecureSessionCookieStore
import org.streamofworship.android.core.session.AuthController
import org.streamofworship.android.data.offline.FileOfflineCacheRepository
import org.streamofworship.android.data.playback.HttpPlaybackRepository
import org.streamofworship.android.data.playback.PlaybackApi
import org.streamofworship.android.data.render.HttpRenderRepository
import org.streamofworship.android.data.render.RenderApi
import org.streamofworship.android.data.settings.HttpSettingsRepository
import org.streamofworship.android.data.settings.SettingsApi
import org.streamofworship.android.data.songs.HttpSongsRepository
import org.streamofworship.android.data.songs.SongsApi
import org.streamofworship.android.data.songsets.HttpSongsetsRepository
import org.streamofworship.android.data.songsets.SongsetsApi
import org.streamofworship.android.feature.auth.LoginScreen
import org.streamofworship.android.feature.auth.rememberAuthController
import org.streamofworship.android.feature.player.Media3PlayerController
import org.streamofworship.android.feature.player.PlaybackArtifact
import org.streamofworship.android.feature.player.PlayerScreen
import org.streamofworship.android.feature.player.PlayerViewModel
import org.streamofworship.android.feature.player.VideoExoPlayerFactory
import org.streamofworship.android.feature.render.RenderScreen
import org.streamofworship.android.feature.render.RenderViewModel
import org.streamofworship.android.feature.settings.SettingsScreen
import org.streamofworship.android.feature.settings.SettingsViewModel
import org.streamofworship.android.feature.share.HttpShareRepository
import org.streamofworship.android.feature.share.ShareApi
import org.streamofworship.android.feature.share.ShareScreen
import org.streamofworship.android.feature.share.ShareViewModel
import org.streamofworship.android.feature.songsets.SongsetDetailScreen
import org.streamofworship.android.feature.songsets.SongsetDetailViewModel
import org.streamofworship.android.feature.songsets.SongsetsListScreen
import org.streamofworship.android.feature.songsets.SongsetsListViewModel

@Composable
fun SowNavGraph(
    modifier: Modifier = Modifier,
    navController: NavHostController = rememberNavController(),
    authController: AuthController? = null,
) {
    NavHost(
        navController = navController,
        startDestination = SowRoute.Songsets.pattern,
        modifier = modifier,
    ) {
        composable(SowRoute.Login.pattern) {
            val authController = rememberAuthController()
            LoginScreen(
                loading = false,
                formError = null,
                onSubmit = authController::signIn,
                onRegisterClick = {},
            )
        }
        composable(SowRoute.Songsets.pattern) {
            val dependencies = rememberSongsetsDependencies(authController)
            val viewModel = remember(dependencies.songsetsRepository) {
                SongsetsListViewModel(dependencies.songsetsRepository)
            }
            SongsetsListScreen(
                viewModel = viewModel,
                onOpenSongset = { songsetId ->
                    navController.navigate(SowRoute.SongsetDetail.createRoute(songsetId))
                },
            )
        }
        composable(SowRoute.SongsetDetail.pattern) { backStackEntry ->
            val songsetId = backStackEntry.arguments?.getString("songsetId").orEmpty()
            val dependencies = rememberSongsetsDependencies(authController)
            val viewModel =
                remember(songsetId, dependencies.songsetsRepository, dependencies.songsRepository) {
                    SongsetDetailViewModel(
                        songsetId = songsetId,
                        songsetsRepository = dependencies.songsetsRepository,
                        songsRepository = dependencies.songsRepository,
                    )
                }
            SongsetDetailScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
                onRender = { navController.navigate(SowRoute.Render.createRoute(songsetId)) },
            )
        }
        composable(SowRoute.Render.pattern) { backStackEntry ->
            val songsetId = backStackEntry.arguments?.getString("songsetId").orEmpty()
            val dependencies = rememberSongsetsDependencies(authController)
            val viewModel =
                remember(songsetId, dependencies.songsetsRepository, dependencies.renderRepository, dependencies.offlineCacheRepository) {
                    RenderViewModel(
                        songsetId = songsetId,
                        songsetsRepository = dependencies.songsetsRepository,
                        renderRepository = dependencies.renderRepository,
                        offlineCacheRepository = dependencies.offlineCacheRepository,
                    )
                }
            RenderScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
                onPlay = { jobId, artifact ->
                    navController.navigate(SowRoute.Player.createRoute(jobId, artifact.routeValue))
                },
                onDownload = { jobId -> navController.navigate(SowRoute.Share.createRoute(jobId)) },
            )
        }
        composable(SowRoute.Player.pattern) { backStackEntry ->
            val jobId = backStackEntry.arguments?.getString("jobId").orEmpty()
            val artifact = backStackEntry.arguments?.getString("artifact").toPlaybackArtifact()
            val dependencies = rememberSongsetsDependencies(authController)
            val context = LocalContext.current.applicationContext
            // Always wire the in-process ExoPlayer: a MediaController-backed PlayerView cannot
            // render a video surface (only forwards audio commands to the service). This fixes
            // the blank-video bug. The controller is keyed on (jobId, context) so rotation
            // recreates it; PlayerViewModel keyed on jobId via viewModel() survives config
            // changes (Phase 3) and the LaunchedEffect in PlayerScreen rebinds the media.
            val mediaController =
                remember(jobId, context) {
                    val exoPlayer = VideoExoPlayerFactory.create(context)
                    Media3PlayerController(exoPlayer)
                }
            val viewModel =
                viewModel(key = jobId) {
                    PlayerViewModel(
                        renderJobId = jobId,
                        repository = dependencies.playbackRepository,
                        controller = mediaController,
                        offlineCacheRepository = dependencies.offlineCacheRepository,
                        defaultArtifact = artifact,
                    )
                }
            PlayerScreen(viewModel = viewModel, media3Controller = mediaController, onBack = { navController.popBackStack() })
        }
        composable(SowRoute.Share.pattern) { backStackEntry ->
            val renderJobId = backStackEntry.arguments?.getString("token").orEmpty()
            val dependencies = rememberSongsetsDependencies(authController)
            val viewModel =
                remember(renderJobId, dependencies.shareRepository, dependencies.playbackRepository, dependencies.renderRepository, dependencies.downloadCoordinator) {
                    ShareViewModel(
                        renderJobId = renderJobId,
                        shareRepository = dependencies.shareRepository,
                        playbackRepository = dependencies.playbackRepository,
                        renderRepository = dependencies.renderRepository,
                        downloadCoordinator = dependencies.downloadCoordinator,
                    )
                }
            ShareScreen(viewModel = viewModel, onBack = { navController.popBackStack() })
        }
        composable(SowRoute.Settings.pattern) {
            val dependencies = rememberSongsetsDependencies(authController)
            val viewModel = remember(dependencies.settingsRepository) { SettingsViewModel(dependencies.settingsRepository) }
            SettingsScreen(viewModel = viewModel, onSignOut = { authController?.signOut() })
        }
    }
}

private data class SongsetsDependencies(
    val songsetsRepository: HttpSongsetsRepository,
    val songsRepository: HttpSongsRepository,
    val renderRepository: HttpRenderRepository,
    val playbackRepository: HttpPlaybackRepository,
    val shareRepository: HttpShareRepository,
    val settingsRepository: HttpSettingsRepository,
    val offlineCacheRepository: FileOfflineCacheRepository,
    val downloadCoordinator: ArtifactDownloadCoordinator,
)

@Composable
private fun rememberSongsetsDependencies(authController: AuthController? = null): SongsetsDependencies {
    val context = LocalContext.current.applicationContext
    return remember(context, authController) {
        val cookieStore = AndroidSecureSessionCookieStore(context)
        val client =
            SowApiClientFactory.create(
                config = AppConfig.fromBuildConfig(),
                cookieStore = cookieStore,
                onUnauthorized = authController?.let { controller -> { controller.onSessionExpired() } },
            )
        val offlineCacheRepository = FileOfflineCacheRepository(context)
        val downloadCoordinator =
            ArtifactDownloadCoordinator(
                cacheRepository = offlineCacheRepository,
                scheduler = AndroidArtifactDownloadScheduler(context),
            )
        SongsetsDependencies(
            songsetsRepository = HttpSongsetsRepository(client.create<SongsetsApi>()),
            songsRepository = HttpSongsRepository(client.create<SongsApi>()),
            renderRepository = HttpRenderRepository(client.create<RenderApi>()),
            playbackRepository = HttpPlaybackRepository(client.create<PlaybackApi>()),
            shareRepository = HttpShareRepository(client.create<ShareApi>()),
            settingsRepository = HttpSettingsRepository(client.create<SettingsApi>()),
            offlineCacheRepository = offlineCacheRepository,
            downloadCoordinator = downloadCoordinator,
        )
    }
}

private val PlaybackArtifact.routeValue: String
    get() =
        when (this) {
            PlaybackArtifact.Video -> "video"
            PlaybackArtifact.Audio -> "audio"
        }

private fun String?.toPlaybackArtifact(): PlaybackArtifact =
    when (this?.lowercase()) {
        "audio" -> PlaybackArtifact.Audio
        else -> PlaybackArtifact.Video
    }
