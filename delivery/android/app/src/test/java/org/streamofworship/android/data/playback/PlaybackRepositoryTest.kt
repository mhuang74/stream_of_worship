package org.streamofworship.android.data.playback

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

class PlaybackRepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repository: PlaybackRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val client =
            SowApiClientFactory.create(
                AppConfig(server.url("/").toString(), BuildVariant.Debug),
                InMemorySessionCookieStore(),
            )
        repository = HttpPlaybackRepository(client.create<PlaybackApi>())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `maps signed url parameters for rendered and source files`() =
        runTest {
            repeat(5) { server.enqueue(json("""{"url":"https://r2/$it","expiresAt":"2026-01-01T00:00:00.000Z"}""")) }

            repository.renderedAudioUrl("job-1")
            repository.renderedVideoUrl("job-1", contentDisposition = "attachment")
            repository.renderedChaptersUrl("job-1")
            repository.sourceAudioUrl("hash-1")
            repository.sourceLrcUrl("hash-1")

            assertTrue(server.takeRequest().path!!.contains("renderJobId=job-1"))
            assertTrue(server.takeRequest().path!!.contains("fileType=video"))
            assertTrue(server.takeRequest().path!!.contains("fileType=json"))
            assertTrue(server.takeRequest().path!!.contains("hashPrefix=hash-1"))
            assertTrue(server.takeRequest().path!!.contains("fileType=lrc"))
        }

    @Test
    fun `loads and normalizes chapters manifest`() =
        runTest {
            server.enqueue(
                json(
                    """
                    {
                      "totalDurationSeconds": 130.5,
                      "generatedAt": "2026-01-01T00:00:00.000Z",
                      "chapters": [
                        {
                          "position": 2,
                          "songTitle": "Second",
                          "startSeconds": 65.0,
                          "endSeconds": 130.5,
                          "lines": [{"text": "  line two  ", "startSeconds": 70.25}]
                        },
                        {
                          "position": 1,
                          "songTitle": "First",
                          "startSeconds": 0,
                          "endSeconds": 65,
                          "lines": [{"text": "line one", "startSeconds": 1.5}]
                        }
                      ]
                    }
                    """.trimIndent(),
                ),
            )

            val manifest = repository.chapters("job-1")

            assertEquals("/api/r2/artifact/job-1/chapters.json", server.takeRequest().path)
            assertEquals(130_500L, manifest.totalDurationMillis)
            assertEquals("First", manifest.chapters[0].title)
            assertEquals("line two", manifest.currentLineAt(70_250)?.text)
        }

    private fun json(body: String): MockResponse =
        MockResponse().setHeader("Content-Type", "application/json").setBody(body)
}
