package org.streamofworship.android.core.navigation

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.core.session.AndroidSecureSessionCookieStore
import org.streamofworship.android.data.render.HttpRenderRepository
import org.streamofworship.android.data.render.RenderApi
import org.streamofworship.android.data.songs.HttpSongsRepository
import org.streamofworship.android.data.songs.SongsApi
import org.streamofworship.android.data.songsets.HttpSongsetsRepository
import org.streamofworship.android.data.songsets.SongsetsApi
import org.streamofworship.android.feature.render.RenderScreen
import org.streamofworship.android.feature.render.RenderViewModel
import org.streamofworship.android.feature.songsets.SongsetDetailScreen
import org.streamofworship.android.feature.songsets.SongsetDetailViewModel
import org.streamofworship.android.feature.songsets.SongsetsListScreen
import org.streamofworship.android.feature.songsets.SongsetsListViewModel

@Composable
fun SowNavGraph(modifier: Modifier = Modifier) {
    val navController = rememberNavController()
    NavHost(
        navController = navController,
        startDestination = SowRoute.Songsets.pattern,
        modifier = modifier,
    ) {
        sowComposable(SowRoute.Login, "Sign in to continue.")
        composable(SowRoute.Songsets.pattern) {
            val dependencies = rememberSongsetsDependencies()
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
            val dependencies = rememberSongsetsDependencies()
            val viewModel =
                remember(songsetId, dependencies.songsetsRepository, dependencies.songsRepository) {
                    SongsetDetailViewModel(
                        songsetId = songsetId,
                        songsetsRepository = dependencies.songsetsRepository,
                        songsRepository = dependencies.songsRepository,
                    )
                }
            SongsetDetailScreen(viewModel = viewModel, onBack = { navController.popBackStack() })
        }
        composable(SowRoute.Render.pattern) { backStackEntry ->
            val songsetId = backStackEntry.arguments?.getString("songsetId").orEmpty()
            val dependencies = rememberSongsetsDependencies()
            val viewModel =
                remember(songsetId, dependencies.songsetsRepository, dependencies.renderRepository) {
                    RenderViewModel(
                        songsetId = songsetId,
                        songsetsRepository = dependencies.songsetsRepository,
                        renderRepository = dependencies.renderRepository,
                    )
                }
            RenderScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
                onPlay = { setId, jobId -> navController.navigate(SowRoute.Player.createRoute(setId, jobId)) },
                onDownload = { jobId -> navController.navigate(SowRoute.Player.createRoute(songsetId, jobId)) },
            )
        }
        sowComposable(SowRoute.Player, "Play rendered worship audio and video.")
        sowComposable(SowRoute.Share, "Open shared worship playback links.")
        sowComposable(SowRoute.Settings, "Configure account and Android workflow defaults.")
    }
}

private data class SongsetsDependencies(
    val songsetsRepository: HttpSongsetsRepository,
    val songsRepository: HttpSongsRepository,
    val renderRepository: HttpRenderRepository,
)

@Composable
private fun rememberSongsetsDependencies(): SongsetsDependencies {
    val context = LocalContext.current.applicationContext
    return remember(context) {
        val cookieStore = AndroidSecureSessionCookieStore(context)
        val client =
            SowApiClientFactory.create(
                config = AppConfig.fromBuildConfig(),
                cookieStore = cookieStore,
            )
        SongsetsDependencies(
            songsetsRepository = HttpSongsetsRepository(client.create<SongsetsApi>()),
            songsRepository = HttpSongsRepository(client.create<SongsApi>()),
            renderRepository = HttpRenderRepository(client.create<RenderApi>()),
        )
    }
}

private fun NavGraphBuilder.sowComposable(
    route: SowRoute,
    description: String,
) {
    composable(route.pattern) {
        PlaceholderRoute(route = route, description = description)
    }
}

@Composable
private fun PlaceholderRoute(
    route: SowRoute,
    description: String,
) {
    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp, vertical = 24.dp),
    ) {
        Text(
            text = route.title,
            style = MaterialTheme.typography.headlineSmall,
        )
        Text(
            text = description,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}
