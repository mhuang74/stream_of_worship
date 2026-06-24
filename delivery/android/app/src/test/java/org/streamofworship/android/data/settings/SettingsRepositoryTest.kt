package org.streamofworship.android.data.settings

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.config.BuildVariant
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.core.session.InMemorySessionCookieStore

class SettingsRepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repository: SettingsRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val client =
            SowApiClientFactory.create(
                AppConfig(server.url("/").toString(), BuildVariant.Debug),
                InMemorySessionCookieStore(),
            )
        repository = HttpSettingsRepository(client.create<SettingsApi>())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `gets and saves settings payload`() =
        runTest {
            server.enqueue(json(settingsJson(defaultGapBeats = 2.0)))
            server.enqueue(json(settingsJson(defaultGapBeats = 4.0)))

            val loaded = repository.getSettings()
            val saved = repository.saveSettings(loaded.copy(defaultGapBeats = 4.0, offlineAutoCache = false))

            assertEquals(2.0, loaded.defaultGapBeats, 0.0)
            assertEquals(4.0, saved.defaultGapBeats, 0.0)
            assertEquals("/api/settings", server.takeRequest().path)
            val request = server.takeRequest()
            assertEquals("PUT", request.method)
            val body = request.body.readUtf8()
            assertTrue(body.contains(""""defaultGapBeats":4.0"""))
            assertTrue(body.contains(""""offlineAutoCache":false"""))
        }

    private fun json(body: String): MockResponse =
        MockResponse().setHeader("Content-Type", "application/json").setBody(body)

    private fun settingsJson(defaultGapBeats: Double): String =
        """
        {
          "settings": {
            "userId": 42,
            "offlineAutoCache": true,
            "defaultGapBeats": $defaultGapBeats,
            "defaultVideoTemplate": "dark",
            "defaultResolution": "720p",
            "lyricsLoopWindowSeconds": 3.0,
            "defaultFontSizePreset": "M",
            "defaultFontFamily": "noto_serif_tc",
            "defaultKeyShiftSemitones": 0,
            "timingReviewFont": "sans"
          }
        }
        """.trimIndent()
}
