package org.streamofworship.android.data.render

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

class RenderRepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repository: RenderRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val client =
            SowApiClientFactory.create(
                config = AppConfig(server.url("/").toString(), BuildVariant.Debug),
                cookieStore = InMemorySessionCookieStore(),
            )
        repository = HttpRenderRepository(client.create<RenderApi>())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `creates render job with selected payload values`() =
        runTest {
            server.enqueue(json(jobJson("job-1"), 201))

            repository.createRenderJob(
                songsetId = "set-1",
                config =
                    RenderFormConfig(
                        audioEnabled = true,
                        videoEnabled = true,
                        template = RenderTemplate.GradientBlue.value,
                        resolution = RenderResolution.FullHd1080.value,
                        fontSizePreset = RenderFontSize.Large.value,
                        fontFamily = RenderFontFamily.Elegant.value,
                        includeTitleCard = true,
                        titleCardDurationSeconds = 15,
                        titleCardLines = listOf("Morning Worship", "Amazing Grace"),
                    ),
            )

            val request = server.takeRequest()
            val body = request.body.readUtf8()
            assertEquals("/api/render-jobs", request.path)
            assertTrue(body.contains(""""songsetId":"set-1""""))
            assertTrue(body.contains(""""template":"gradient_blue""""))
            assertTrue(body.contains(""""resolution":"1080p""""))
            assertTrue(body.contains(""""fontFamily":"chiron_goround_tc""""))
            assertTrue(body.contains(""""titleCardDurationSeconds":15"""))
            assertTrue(body.contains("Morning Worship"))
        }

    @Test
    fun `maps active render conflict response with job id`() =
        runTest {
            server.enqueue(
                json(
                    """
                    {
                      "error":"A render job is already in progress for this songset",
                      "jobId":"active-job",
                      "estimatedTotalSeconds":120,
                      "config":{"audioEnabled":true,"videoEnabled":false,"fontFamily":"noto_serif_tc"}
                    }
                    """.trimIndent(),
                    409,
                ),
            )

            val error =
                runCatching {
                    repository.createRenderJob("set-1", RenderFormConfig())
                }.exceptionOrNull() as ActiveRenderConflictException

            assertEquals("active-job", error.conflict.jobId)
            assertEquals(120.0, error.conflict.estimatedTotalSeconds)
            assertEquals(false, error.conflict.config?.videoEnabled)
        }

    @Test
    fun `gets cancels and fetches artifact sizes`() =
        runTest {
            server.enqueue(json(jobJson("job-1", status = "running")))
            server.enqueue(json(jobJson("job-1", status = "cancelled")))
            server.enqueue(json("""{"renderJobId":"job-1","mp3SizeBytes":1024,"mp4SizeBytes":2048}"""))

            val running = repository.getRenderJob("job-1")
            val cancelled = repository.cancelRenderJob("job-1")
            val sizes = repository.getArtifactSizes("job-1")

            assertEquals(RenderJobStatus.Running, running.status)
            assertEquals(RenderJobStatus.Cancelled, cancelled.status)
            assertEquals(1024L, sizes.mp3SizeBytes)
            assertEquals("/api/render-jobs/job-1", server.takeRequest().path)
            assertEquals("DELETE", server.takeRequest().method)
            assertEquals("/api/render-jobs/job-1/artifact-sizes", server.takeRequest().path)
        }

    private fun json(
        body: String,
        code: Int = 200,
    ): MockResponse =
        MockResponse()
            .setResponseCode(code)
            .setHeader("Content-Type", "application/json")
            .setBody(body)

    private fun jobJson(
        id: String,
        status: String = "queued",
    ): String =
        """
        {
          "id":"$id",
          "songsetId":"set-1",
          "userId":42,
          "status":"$status",
          "phase":"preparing",
          "phaseIndex":0,
          "totalPhases":5,
          "elapsedSeconds":0,
          "errorMessage":null,
          "estimatedTotalSeconds":120,
          "totalDurationSeconds":null,
          "startedAt":null,
          "template":"dark",
          "resolution":"720p",
          "audioEnabled":true,
          "videoEnabled":true,
          "fontSizePreset":"M",
          "fontFamily":"noto_serif_tc",
          "includeTitleCard":false,
          "titleCardDurationSeconds":null,
          "titleCardLines":null,
          "mp3R2Key":"artifact/$id/audio.mp3",
          "mp4R2Key":"artifact/$id/video.mp4",
          "chaptersR2Key":"artifact/$id/chapters.json",
          "songCount":2,
          "songsetDurationSeconds":180,
          "createdAt":"2026-01-01T00:00:00.000Z",
          "updatedAt":"2026-01-01T00:00:00.000Z",
          "completedAt":null
        }
        """.trimIndent()
}
