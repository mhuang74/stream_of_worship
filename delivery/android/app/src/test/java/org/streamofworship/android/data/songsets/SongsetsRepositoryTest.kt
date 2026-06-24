package org.streamofworship.android.data.songsets

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
import org.streamofworship.android.core.model.TransitionSettings
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.core.session.InMemorySessionCookieStore

class SongsetsRepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repository: SongsetsRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val client =
            SowApiClientFactory.create(
                config = AppConfig(server.url("/").toString(), BuildVariant.Debug),
                cookieStore = InMemorySessionCookieStore(),
            )
        repository = HttpSongsetsRepository(client.create<SongsetsApi>())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `lists songsets with pagination query`() =
        runTest {
            server.enqueue(json("""{"songsets":[${summaryJson("set-1")}],"total":1}"""))

            val page = repository.listSongsets(limit = 10, offset = 20)

            assertEquals(1, page.songsets.size)
            assertEquals("/api/songsets?limit=10&offset=20", server.takeRequest().path)
        }

    @Test
    fun `creates gets updates duplicates and deletes songsets`() =
        runTest {
            server.enqueue(json(summaryJson("created"), 201))
            server.enqueue(json(detailJson("created")))
            server.enqueue(json(summaryJson("created")))
            server.enqueue(json(detailJson("copy"), 201))
            server.enqueue(json("""{"success":true}"""))

            repository.createSongset("Morning", "Opening")
            repository.getSongset("created")
            repository.updateSongset("created", description = "Updated")
            repository.duplicateSongset("created", "Copy of Morning", "Updated")
            repository.deleteSongset("created")

            assertEquals("/api/songsets", server.takeRequest().path)
            assertEquals("/api/songsets/created", server.takeRequest().path)
            val update = server.takeRequest()
            assertEquals("PATCH", update.method)
            assertEquals("""{"description":"Updated"}""", update.body.readUtf8())
            val duplicate = server.takeRequest()
            assertEquals("/api/songsets/created/duplicate", duplicate.path)
            assertTrue(duplicate.body.readUtf8().contains("Copy of Morning"))
            assertEquals("DELETE", server.takeRequest().method)
        }

    @Test
    fun `adds updates removes and reorders songset items`() =
        runTest {
            server.enqueue(json(itemJson("item-1"), 201))
            server.enqueue(json(itemJson("item-1")))
            server.enqueue(json("""{"success":true}"""))
            server.enqueue(json("""{"success":true}"""))

            repository.addItem("set-1", "song-1", "hash1", 0)
            repository.updateItemTransition(
                "set-1",
                "item-1",
                TransitionSettings(
                    gapBeats = 2.0,
                    crossfadeEnabled = 1,
                    crossfadeDurationSeconds = 1.5,
                    keyShiftSemitones = 1,
                    tempoRatio = 1.05,
                ),
            )
            repository.deleteItem("set-1", "item-1")
            repository.reorderItems("set-1", listOf(ReorderItemRequest("item-1", 0)))

            val add = server.takeRequest()
            assertEquals("/api/songsets/set-1/items", add.path)
            assertTrue(add.body.readUtf8().contains("recordingHashPrefix"))
            val update = server.takeRequest()
            assertEquals("PATCH", update.method)
            assertTrue(update.body.readUtf8().contains("tempoRatio"))
            assertEquals("/api/songsets/set-1/items?itemId=item-1", server.takeRequest().path)
            val reorder = server.takeRequest()
            assertEquals("/api/songsets/set-1/items/reorder", reorder.path)
            assertTrue(reorder.body.readUtf8().contains("updates"))
        }

    private fun json(
        body: String,
        code: Int = 200,
    ): MockResponse =
        MockResponse()
            .setResponseCode(code)
            .setHeader("Content-Type", "application/json")
            .setBody(body)

    private fun summaryJson(id: String): String =
        """
        {
          "id":"$id",
          "name":"Morning Set",
          "description":null,
          "createdAt":"2026-01-01T00:00:00.000Z",
          "updatedAt":"2026-01-01T00:00:00.000Z",
          "latestRenderJobId":null,
          "lastFailedRenderJobId":null,
          "lastCompletedRenderJobId":null,
          "itemCount":1,
          "durationSeconds":180,
          "renderState":"fresh",
          "renderErrorMessage":null,
          "failedAt":null
        }
        """.trimIndent()

    private fun detailJson(id: String): String =
        """
        ${summaryJson(id).dropLast(1)},
          "items":[${itemJson("item-1")}]
        }
        """.trimIndent()

    private fun itemJson(id: String): String =
        """
        {
          "id":"$id",
          "songId":"song-1",
          "recordingHashPrefix":"hash1",
          "position":0,
          "gapBeats":0,
          "crossfadeEnabled":0,
          "crossfadeDurationSeconds":0,
          "keyShiftSemitones":0,
          "tempoRatio":1,
          "markedLineCount":4,
          "song":{"id":"song-1","title":"Amazing Grace","composer":"Newton","lyricist":null,"albumName":"Hymns","musicalKey":"G"},
          "recording":{"contentHash":"content1","durationSeconds":180,"tempoBpm":72,"musicalKey":"G","r2AudioUrl":null}
        }
        """.trimIndent()
}
