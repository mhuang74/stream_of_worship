package org.streamofworship.android.core.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Login
import androidx.compose.material.icons.automirrored.outlined.QueueMusic
import androidx.compose.material.icons.outlined.Movie
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.SmartDisplay
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.ui.graphics.vector.ImageVector

sealed class SowRoute(
    val pattern: String,
    val title: String,
) {
    data object Login : SowRoute("login", "Login")
    data object Songsets : SowRoute("songsets", "Songsets")
    data object SongsetDetail : SowRoute("songsets/{songsetId}", "Songset")
    data object Render : SowRoute("songsets/{songsetId}/render", "Render")
    data object Player : SowRoute("songsets/{songsetId}/player/{jobId}", "Player")
    data object Share : SowRoute("share/{token}", "Share")
    data object Settings : SowRoute("settings", "Settings")

    companion object {
        val all: List<SowRoute> =
            listOf(Login, Songsets, SongsetDetail, Render, Player, Share, Settings)
    }
}

data class BottomNavDestination(
    val route: SowRoute,
    val icon: ImageVector,
)

val SowBottomNavDestinations =
    listOf(
        BottomNavDestination(SowRoute.Songsets, Icons.AutoMirrored.Outlined.QueueMusic),
        BottomNavDestination(SowRoute.Render, Icons.Outlined.Tune),
        BottomNavDestination(SowRoute.Player, Icons.Outlined.SmartDisplay),
        BottomNavDestination(SowRoute.Share, Icons.Outlined.Share),
        BottomNavDestination(SowRoute.Settings, Icons.Outlined.Settings),
    )

val SowRouteIconHints: Map<SowRoute, ImageVector> =
    mapOf(
        SowRoute.Login to Icons.AutoMirrored.Outlined.Login,
        SowRoute.Songsets to Icons.AutoMirrored.Outlined.QueueMusic,
        SowRoute.SongsetDetail to Icons.Outlined.Movie,
        SowRoute.Render to Icons.Outlined.Tune,
        SowRoute.Player to Icons.Outlined.SmartDisplay,
        SowRoute.Share to Icons.Outlined.Share,
        SowRoute.Settings to Icons.Outlined.Settings,
    )

fun SowRoute.SongsetDetail.createRoute(songsetId: String): String =
    "songsets/${songsetId.encodeRouteSegment()}"

fun SowRoute.Render.createRoute(songsetId: String): String =
    "songsets/${songsetId.encodeRouteSegment()}/render"

fun SowRoute.Player.createRoute(
    songsetId: String,
    jobId: String,
): String = "songsets/${songsetId.encodeRouteSegment()}/player/${jobId.encodeRouteSegment()}"

fun SowRoute.Share.createRoute(token: String): String = "share/${token.encodeRouteSegment()}"

private fun String.encodeRouteSegment(): String =
    replace("%", "%25").replace("/", "%2F").replace("?", "%3F").replace("#", "%23")
