package org.streamofworship.android.core.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.QueueMusic
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.ui.graphics.vector.ImageVector

sealed class SowRoute(
    val pattern: String,
    val title: String,
) {
    data object Login : SowRoute("login", "Login")
    data object Songsets : SowRoute("songsets", "Songsets")
    data object SongsetDetail : SowRoute("songsets/{songsetId}", "Songset")
    data object Render : SowRoute("songsets/{songsetId}/render", "Render")
    data object Player : SowRoute("player/{jobId}/{artifact}", "Player")
    data object Share : SowRoute("share/{token}", "Share")
    data object Settings : SowRoute("settings", "Settings")

    companion object {
        val all: List<SowRoute>
            get() = listOf(Login, Songsets, SongsetDetail, Render, Player, Share, Settings)
    }
}

data class BottomNavDestination(
    val route: SowRoute,
    val icon: ImageVector,
)

val SowBottomNavDestinations =
    listOf(
        BottomNavDestination(SowRoute.Songsets, Icons.AutoMirrored.Outlined.QueueMusic),
        BottomNavDestination(SowRoute.Settings, Icons.Outlined.Settings),
    )

fun SowRoute.SongsetDetail.createRoute(songsetId: String): String =
    "songsets/${songsetId.encodeRouteSegment()}"

fun SowRoute.Render.createRoute(songsetId: String): String =
    "songsets/${songsetId.encodeRouteSegment()}/render"

fun SowRoute.Player.createRoute(
    jobId: String,
    artifact: String = "video",
): String = "player/${jobId.encodeRouteSegment()}/${artifact.encodeRouteSegment()}"

fun SowRoute.Share.createRoute(token: String): String = "share/${token.encodeRouteSegment()}"

private fun String.encodeRouteSegment(): String =
    replace("%", "%25").replace("/", "%2F").replace("?", "%3F").replace("#", "%23")
