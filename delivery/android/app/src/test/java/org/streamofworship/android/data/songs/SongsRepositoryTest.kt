package org.streamofworship.android.data.songs

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.config.BuildVariant
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.core.session.InMemorySessionCookieStore

class SongsRepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repository: SongsRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val client =
            SowApiClientFactory.create(
                config = AppConfig(server.url("/").toString(), BuildVariant.Debug),
                cookieStore = InMemorySessionCookieStore(),
            )
        repository = HttpSongsRepository(client.create<SongsApi>())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `lists songs with published recordings only`() =
        runTest {
            server.enqueue(json("""{"songs":[${songJson()}],"total":1}"""))

            val page = repository.listSongs(limit = 25, offset = 5, albumName = "Hymns")

            assertEquals(1, page.songs.size)
            assertEquals(1, page.songs.single().recordings.size)
            assertFalse(page.songs.single().recordings.any { it.visibilityStatus == "draft" })
            assertEquals(
                "/api/songs?limit=25&offset=5&visibilityStatus=published%2Creview&albumName=Hymns",
                server.takeRequest().path,
            )
        }

    @Test
    fun `searches songs with published visibility query`() =
        runTest {
            server.enqueue(json("""{"songs":[${songJson()}],"total":1}"""))

            repository.searchSongs("grace", limit = 10, offset = 0)

            assertEquals(
                "/api/songs/search?q=grace&limit=10&offset=0&visibilityStatus=published%2Creview",
                server.takeRequest().path,
            )
        }

    @Test
    fun `keeps review recordings`() =
        runTest {
            server.enqueue(json("""{"songs":[${songJson(visibility = "review")}],"total":1}"""))

            val page = repository.listSongs()

            assertEquals(1, page.songs.size)
            assertEquals(1, page.songs.single().recordings.size)
            assertEquals("review", page.songs.single().recordings.single().visibilityStatus)
        }

    @Test
    fun `keeps published and review recordings in mixed response`() =
        runTest {
            server.enqueue(
                json("""{"songs":[${songJson(visibility = "published")},${songJson(visibility = "review")}],"total":2}"""),
            )

            val page = repository.listSongs()

            assertEquals(2, page.songs.size)
            assertEquals(listOf("published", "review"), page.songs.map { it.recordings.single().visibilityStatus })
        }

    @Test
    fun `semantic search posts query body`() =
        runTest {
            server.enqueue(json("""{"songs":[${songJson()}],"query":"joy","total":1}"""))

            repository.semanticSearch("joy", limit = 7)

            val request = server.takeRequest()
            assertEquals("/api/songs/search/semantic", request.path)
            assertEquals("""{"query":"joy","limit":7}""", request.body.readUtf8())
        }

    private fun json(body: String): MockResponse =
        MockResponse()
            .setHeader("Content-Type", "application/json")
            .setBody(body)

    private fun songJson(visibility: String = "published"): String =
        """
        {
          "id":"song-1",
          "title":"Amazing Grace",
          "titlePinyin":null,
          "composer":"Newton",
          "lyricist":null,
          "albumName":"Hymns",
          "albumSeries":null,
          "musicalKey":"G",
          "createdAt":"2026-01-01T00:00:00.000Z",
          "updatedAt":"2026-01-01T00:00:00.000Z",
          "recordings":[
            {"contentHash":"content1","hashPrefix":"hash1","originalFilename":"a.mp3","durationSeconds":180,"tempoBpm":72,"musicalKey":"G","musicalMode":null,"loudnessDb":null,"r2AudioUrl":null,"r2LrcUrl":null,"visibilityStatus":"$visibility","analysisStatus":"complete"},
            {"contentHash":"content2","hashPrefix":"hash2","originalFilename":"b.mp3","durationSeconds":200,"tempoBpm":70,"musicalKey":"F","musicalMode":null,"loudnessDb":null,"r2AudioUrl":null,"r2LrcUrl":null,"visibilityStatus":"draft","analysisStatus":"complete"}
          ]
        }
        """.trimIndent()
}
