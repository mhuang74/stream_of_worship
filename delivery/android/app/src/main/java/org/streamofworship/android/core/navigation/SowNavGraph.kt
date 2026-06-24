package org.streamofworship.android.core.navigation

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraphBuilder
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController

@Composable
fun SowNavGraph(modifier: Modifier = Modifier) {
    val navController = rememberNavController()
    NavHost(
        navController = navController,
        startDestination = SowRoute.Songsets.pattern,
        modifier = modifier,
    ) {
        sowComposable(SowRoute.Login, "Sign in to continue.")
        sowComposable(SowRoute.Songsets, "Manage worship sets and rendering status.")
        sowComposable(SowRoute.SongsetDetail, "Review songs and transition settings.")
        sowComposable(SowRoute.Render, "Choose audio and lyric video output options.")
        sowComposable(SowRoute.Player, "Play rendered worship audio and video.")
        sowComposable(SowRoute.Share, "Open shared worship playback links.")
        sowComposable(SowRoute.Settings, "Configure account and Android workflow defaults.")
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
        verticalArrangement = Arrangement.spacedBy(8.dp),
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
