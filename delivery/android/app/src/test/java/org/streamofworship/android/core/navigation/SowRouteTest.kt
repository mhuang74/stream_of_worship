package org.streamofworship.android.core.navigation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SowRouteTest {
    @Test
    fun `defines expected top level route patterns`() {
        val patterns = SowRoute.all.map { it.pattern }

        assertEquals(
            listOf(
                "login",
                "songsets",
                "songsets/{songsetId}",
                "songsets/{songsetId}/render",
                "songsets/{songsetId}/player/{jobId}",
                "share/{token}",
                "settings",
            ),
            patterns,
        )
    }

    @Test
    fun `bottom navigation exposes utility workflow destinations`() {
        val destinationTitles = SowBottomNavDestinations.map { it.route.title }

        assertEquals(
            listOf("Songsets", "Render", "Player", "Share", "Settings"),
            destinationTitles,
        )
    }

    @Test
    fun `route builders encode path separators`() {
        assertEquals("songsets/set%2F1", SowRoute.SongsetDetail.createRoute("set/1"))
        assertEquals("songsets/set%3F1/render", SowRoute.Render.createRoute("set?1"))
        assertEquals(
            "songsets/set%231/player/job%2F9",
            SowRoute.Player.createRoute(songsetId = "set#1", jobId = "job/9"),
        )
        assertEquals("share/token%252Fraw", SowRoute.Share.createRoute("token%2Fraw"))
    }

    @Test
    fun `every route has an icon hint`() {
        assertTrue(SowRoute.all.all { route -> SowRouteIconHints.containsKey(route) })
    }
}
