package org.streamofworship.android.feature.render

import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.ExperimentalCoroutinesApi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.streamofworship.android.core.model.RenderState
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetItem
import org.streamofworship.android.core.model.SongsetItemRecording
import org.streamofworship.android.core.model.SongsetItemSong
import org.streamofworship.android.core.model.SongsetSummary
import org.streamofworship.android.core.model.SongsetsPage
import org.streamofworship.android.core.model.TransitionSettings
import org.streamofworship.android.data.render.ActiveRenderConflict
import org.streamofworship.android.data.render.ActiveRenderConflictException
import org.streamofworship.android.data.render.ArtifactSizes
import org.streamofworship.android.data.render.RenderFormConfig
import org.streamofworship.android.data.render.RenderJob
import org.streamofworship.android.data.render.RenderJobStatus
import org.streamofworship.android.data.render.RenderRepository
import org.streamofworship.android.data.songsets.ReorderItemRequest
import org.streamofworship.android.data.songsets.SongsetsRepository

@OptIn(ExperimentalCoroutinesApi::class)
class RenderViewModelTest {
    @Test
    fun `local validation blocks empty output and oversized songset`() =
        runTest {
            val viewModel = viewModel(this)
            viewModel.load()
            advanceUntilIdle()

            viewModel.updateConfig(RenderFormConfig(audioEnabled = false, videoEnabled = false))

            assertEquals("Select audio, video, or both.", viewModel.uiState.value.validationMessage)

            val oversized = detail(items = (0..5).map { item("item-$it", it, 30.0) })
            val oversizedVm = viewModel(this, songsets = FakeRenderSongsetsRepository(oversized))
            oversizedVm.load()
            advanceUntilIdle()
            oversizedVm.requestRender()

            assertEquals("Songset exceeds maximum of 5 songs.", oversizedVm.uiState.value.validationMessage)
        }

    @Test
    fun `previous render requires confirmation before submit`() =
        runTest {
            val previous = detail().copy(lastCompletedRenderJobId = "old-job")
            val render = FakeRenderRepository()
            val viewModel = viewModel(this, songsets = FakeRenderSongsetsRepository(previous), render = render)
            viewModel.load()
            advanceUntilIdle()

            viewModel.requestRender()
            advanceUntilIdle()

            assertEquals(0, render.createCalls)
            assertEquals("Confirm before replacing the previous render.", viewModel.uiState.value.validationMessage)

            viewModel.confirmPreviousRenderAndStart()
            runCurrent()
            viewModel.stopPolling()

            assertEquals(1, render.createCalls)
        }

    @Test
    fun `active conflict starts polling existing job`() =
        runTest {
            val render =
                FakeRenderRepository(
                    createError =
                        ActiveRenderConflictException(
                            ActiveRenderConflict(jobId = "active-job", message = "already running"),
                        ),
                    jobs = mutableListOf(job("active-job", RenderJobStatus.Completed)),
                )
            val viewModel = viewModel(this, render = render, pollInterval = 100)
            viewModel.load()
            advanceUntilIdle()

            viewModel.requestRender()
            advanceUntilIdle()

            assertEquals("active-job", viewModel.uiState.value.currentJob?.id)
            assertEquals("already running", viewModel.uiState.value.serverMessage)
        }

    @Test
    fun `polling transitions through queued running and completed with artifact sizes`() =
        runTest {
            val render =
                FakeRenderRepository(
                    jobs =
                        mutableListOf(
                            job("job-1", RenderJobStatus.Queued),
                            job("job-1", RenderJobStatus.Running),
                            job("job-1", RenderJobStatus.Completed),
                        ),
                )
            val viewModel = viewModel(this, render = render, pollInterval = 100)

            viewModel.startPolling("job-1")
            runCurrent()
            assertEquals(RenderJobStatus.Queued, viewModel.uiState.value.currentJob?.status)
            assertTrue(viewModel.uiState.value.isPolling)

            advanceTimeBy(100)
            runCurrent()
            assertEquals(RenderJobStatus.Running, viewModel.uiState.value.currentJob?.status)

            advanceTimeBy(100)
            advanceUntilIdle()
            assertEquals(RenderJobStatus.Completed, viewModel.uiState.value.currentJob?.status)
            assertFalse(viewModel.uiState.value.isPolling)
            assertEquals(2048L, viewModel.uiState.value.artifactSizes?.mp4SizeBytes)
        }

    @Test
    fun `stop polling cancels scheduled status requests`() =
        runTest {
            val render =
                FakeRenderRepository(
                    jobs =
                        mutableListOf(
                            job("job-1", RenderJobStatus.Queued),
                            job("job-1", RenderJobStatus.Running),
                        ),
                )
            val viewModel = viewModel(this, render = render, pollInterval = 1_000)

            viewModel.startPolling("job-1")
            runCurrent()
            viewModel.stopPolling()
            advanceTimeBy(1_000)
            runCurrent()

            assertEquals(1, render.getCalls)
            assertFalse(viewModel.uiState.value.isPolling)
        }

    @Test
    fun `initial conflict message is cleared after the first successful poll`() =
        runTest {
            val render =
                FakeRenderRepository(
                    jobs =
                        mutableListOf(
                            job("active-job", RenderJobStatus.Running),
                            job("active-job", RenderJobStatus.Completed),
                        ),
                )
            val viewModel = viewModel(this, render = render, pollInterval = 100)

            viewModel.startPolling("active-job", initialMessage = "A render job is already in progress")
            runCurrent()
            // First successful response surfaces the initial message for exactly one iteration.
            assertEquals("A render job is already in progress", viewModel.uiState.value.serverMessage)

            advanceTimeBy(100)
            runCurrent()
            // Subsequent poll clears the initial message — the render is actively progressing.
            assertEquals(null, viewModel.uiState.value.serverMessage)
            assertEquals(RenderJobStatus.Completed, viewModel.uiState.value.currentJob?.status)
        }

    @Test
    fun `latest failed render surfaces the previous completed render for review and playback`() =
        runTest {
            val songset =
                detail().copy(latestRenderJobId = "latest", lastCompletedRenderJobId = "completed-old")
            val render =
                FakeRenderRepository(
                    jobs =
                        mutableListOf(
                            job("latest", RenderJobStatus.Failed),
                            job("completed-old", RenderJobStatus.Completed),
                        ),
                )
            val viewModel =
                viewModel(
                    this,
                    songsets = FakeRenderSongsetsRepository(songset),
                    render = render,
                    pollInterval = 100,
                )
            viewModel.load()
            advanceUntilIdle()

            assertEquals(RenderJobStatus.Failed, viewModel.uiState.value.currentJob?.status)
            assertFalse(viewModel.uiState.value.currentJob?.hasPlayableArtifacts == true)
            val reviewable = viewModel.uiState.value.reviewableCompletedJob
            assertNotNull(reviewable)
            assertEquals("completed-old", reviewable?.id)
            assertTrue(reviewable?.hasPlayableArtifacts == true)
        }

    @Test
    fun `polling stops and surfaces terminal status after exhausting retries`() =
        runTest {
            val render =
                FakeRenderRepository(
                    jobs = mutableListOf(job("job-1", RenderJobStatus.Queued)),
                    getError = RuntimeException("server down"),
                )
            val viewModel =
                viewModel(
                    this,
                    render = render,
                    pollInterval = 100,
                    retryDelay = 50,
                    maxRetries = 2,
                )

            viewModel.startPolling("job-1")
            // Two retry delays of 50ms then 100ms (exponential cap kicks in) before giving up.
            advanceUntilIdle()

            assertFalse(viewModel.uiState.value.isPolling)
            assertEquals(3, render.getCalls)
            val message = viewModel.uiState.value.serverMessage
            assertTrue(message != null && message.contains("server down"))
        }

    private fun viewModel(
        scope: TestScope,
        songsets: FakeRenderSongsetsRepository = FakeRenderSongsetsRepository(),
        render: FakeRenderRepository = FakeRenderRepository(),
        pollInterval: Long = 100,
        retryDelay: Long = 100,
        maxRetries: Int = 10,
    ): RenderViewModel =
        RenderViewModel(
            songsetId = "set-1",
            songsetsRepository = songsets,
            renderRepository = render,
            scope = scope,
            pollIntervalMillis = pollInterval,
            retryDelayMillis = retryDelay,
            maxRetries = maxRetries,
        )
}

internal class FakeRenderRepository(
    private val createError: RuntimeException? = null,
    private val jobs: MutableList<RenderJob> = mutableListOf(job("job-1", RenderJobStatus.Queued)),
    private val getError: RuntimeException? = null,
) : RenderRepository {
    var createCalls = 0
    var getCalls = 0

    override suspend fun createRenderJob(
        songsetId: String,
        config: RenderFormConfig,
    ): RenderJob {
        createCalls += 1
        createError?.let { throw it }
        return jobs.first()
    }

    override suspend fun getRenderJob(id: String): RenderJob {
        getCalls += 1
        getError?.let { throw it }
        return if (jobs.size > 1) jobs.removeAt(0) else jobs.first()
    }

    override suspend fun cancelRenderJob(id: String): RenderJob = job(id, RenderJobStatus.Cancelled)

    override suspend fun getArtifactSizes(id: String): ArtifactSizes =
        ArtifactSizes(renderJobId = id, mp3SizeBytes = 1024, mp4SizeBytes = 2048)
}

internal class FakeRenderSongsetsRepository(
    private val detail: SongsetDetail = detail(),
) : SongsetsRepository {
    override suspend fun listSongsets(limit: Int, offset: Int): SongsetsPage =
        SongsetsPage(songsets = listOf(detail.summary()), total = 1)

    override suspend fun createSongset(name: String, description: String?): SongsetSummary =
        detail.summary().copy(name = name, description = description)

    override suspend fun getSongset(id: String): SongsetDetail = detail

    override suspend fun updateSongset(id: String, name: String?, description: String?): SongsetSummary =
        detail.summary()

    override suspend fun deleteSongset(id: String) = Unit

    override suspend fun duplicateSongset(id: String, name: String, description: String?): SongsetDetail = detail

    override suspend fun addItem(
        songsetId: String,
        songId: String,
        recordingHashPrefix: String?,
        position: Int,
    ): SongsetItem = item("new", position, 60.0)

    override suspend fun updateItemTransition(
        songsetId: String,
        itemId: String,
        settings: TransitionSettings,
    ): SongsetItem = detail.items.first()

    override suspend fun deleteItem(songsetId: String, itemId: String) = Unit

    override suspend fun reorderItems(songsetId: String, updates: List<ReorderItemRequest>) = Unit
}

internal fun detail(items: List<SongsetItem> = listOf(item("item-1", 0, 60.0))): SongsetDetail =
    SongsetDetail(
        id = "set-1",
        name = "Morning Set",
        createdAt = "2026-01-01T00:00:00.000Z",
        updatedAt = "2026-01-01T00:00:00.000Z",
        itemCount = items.size,
        durationSeconds = items.sumOf { it.recording?.durationSeconds ?: 0.0 },
        renderState = RenderState.Fresh,
        items = items,
    )

internal fun item(
    id: String,
    position: Int,
    duration: Double,
): SongsetItem =
    SongsetItem(
        id = id,
        songId = "song-$id",
        recordingHashPrefix = "hash-$id",
        position = position,
        song = SongsetItemSong(id = "song-$id", title = "Song $id"),
        recording = SongsetItemRecording(contentHash = "content-$id", durationSeconds = duration),
    )

internal fun job(
    id: String,
    status: RenderJobStatus,
): RenderJob =
    RenderJob(
        id = id,
        songsetId = "set-1",
        userId = 42,
        status = status,
        phase = null,
        mp3R2Key = "artifact/$id/audio.mp3".takeIf { status == RenderJobStatus.Completed },
        mp4R2Key = "artifact/$id/video.mp4".takeIf { status == RenderJobStatus.Completed },
        chaptersR2Key = "artifact/$id/chapters.json".takeIf { status == RenderJobStatus.Completed },
    )
