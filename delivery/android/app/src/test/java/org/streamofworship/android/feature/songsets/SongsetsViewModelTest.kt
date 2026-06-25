package org.streamofworship.android.feature.songsets

import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.streamofworship.android.core.model.Recording
import org.streamofworship.android.core.model.RenderState
import org.streamofworship.android.core.model.Song
import org.streamofworship.android.core.model.SongsPage
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetItem
import org.streamofworship.android.core.model.SongsetItemRecording
import org.streamofworship.android.core.model.SongsetItemSong
import org.streamofworship.android.core.model.SongsetSummary
import org.streamofworship.android.core.model.SongsetsPage
import org.streamofworship.android.core.model.TransitionSettings
import org.streamofworship.android.data.songs.SongsRepository
import org.streamofworship.android.data.songsets.ReorderItemRequest
import org.streamofworship.android.data.songsets.SongsetsRepository

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class SongsetsViewModelTest {
    @Test
    fun `delete rolls back optimistic list update on failure`() =
        runTest {
            val repository = FakeSongsetsRepository(deleteError = RuntimeException("nope"))
            val viewModel = SongsetsListViewModel(repository, this)
            viewModel.load()
            advanceUntilIdle()

            viewModel.delete("set-1")
            advanceUntilIdle()

            assertEquals(listOf("set-1"), viewModel.uiState.value.songsets.map { it.id })
            assertEquals("nope", viewModel.uiState.value.error)
        }

    @Test
    fun `delete rollback preserves server total when more pages exist`() =
        runTest {
            val repository = FakeSongsetsRepository(deleteError = RuntimeException("nope"), total = 50)
            val viewModel = SongsetsListViewModel(repository, this)
            viewModel.load()
            advanceUntilIdle()

            viewModel.delete("set-1")
            advanceUntilIdle()

            assertEquals(listOf("set-1"), viewModel.uiState.value.songsets.map { it.id })
            assertEquals(50, viewModel.uiState.value.total)
        }

    @Test
    fun `clearing description sends an explicit empty string`() =
        runTest {
            val songsets = FakeSongsetsRepository(detail = detail().copy(description = "Existing"))
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            viewModel.updateDescription("   ")
            advanceUntilIdle()

            assertNull(viewModel.uiState.value.songset?.description)
            assertEquals(listOf(""), songsets.updatedDescriptions)
        }

    @Test
    fun `remove item marks stale and restores previous state on failure`() =
        runTest {
            val songsets = FakeSongsetsRepository(deleteItemError = RuntimeException("remove failed"))
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            viewModel.removeItem("item-1")
            advanceUntilIdle()

            val state = viewModel.uiState.value
            assertEquals(RenderState.Fresh, state.songset?.renderState)
            assertEquals(listOf("item-1", "item-2"), state.songset?.items?.map { it.id })
            assertEquals("remove failed", state.error)
        }

    @Test
    fun `reorder marks stale after successful optimistic update`() =
        runTest {
            val songsets = FakeSongsetsRepository()
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            viewModel.moveItem("item-2", -1)
            advanceUntilIdle()

            val detail = viewModel.uiState.value.songset
            assertEquals(RenderState.Stale, detail?.renderState)
            assertEquals(listOf("item-2", "item-1"), detail?.items?.map { it.id })
            assertEquals(listOf("item-2" to 0, "item-1" to 1), songsets.reorderUpdates)
        }

    @Test
    fun `add song validates maximum songs and duration`() =
        runTest {
            val fullDetail =
                detail(items = (0 until 5).map { item("item-$it", it, duration = 60.0) })
            val songsets = FakeSongsetsRepository(detail = fullDetail)
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            viewModel.addSong(song("song-new", duration = 60.0))

            assertEquals("Songsets can include up to 5 songs", viewModel.uiState.value.validationMessage)

            val longDetail = detail(items = listOf(item("item-1", 0, duration = 1490.0)))
            val durationVm = detailViewModel(this, FakeSongsetsRepository(detail = longDetail))
            durationVm.load()
            advanceUntilIdle()
            durationVm.addSong(song("song-long", duration = 20.0))

            assertEquals("Songset duration must stay under 25 minutes", durationVm.uiState.value.validationMessage)
        }

    @Test
    fun `add song appends item and marks stale`() =
        runTest {
            val songsets = FakeSongsetsRepository()
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            viewModel.addSong(song("song-3", duration = 90.0))
            advanceUntilIdle()

            val detail = viewModel.uiState.value.songset
            assertNull(viewModel.uiState.value.validationMessage)
            assertEquals(RenderState.Stale, detail?.renderState)
            assertEquals(3, detail?.items?.size)
        }

    @Test
    fun `concurrent add song calls post distinct positions`() =
        runTest {
            val songsets = RecordingAddSongsetsRepository()
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            // Fire two addSong calls before either coroutine has a chance to update state.
            viewModel.addSong(song("song-a", duration = 60.0))
            viewModel.addSong(song("song-b", duration = 60.0))
            advanceUntilIdle()

            assertEquals(2, songsets.addedPositions.size)
            assertEquals(listOf(2, 3), songsets.addedPositions.sorted())
            assertEquals(4, viewModel.uiState.value.songset?.items?.size)
        }

    @Test
    fun `load does not clobber optimistic transition edits while a write is in flight`() =
        runTest {
            val songsets = ClobberingSongsetsRepository()
            val viewModel = detailViewModel(this, songsets)
            viewModel.load()
            advanceUntilIdle()

            val originalGap = viewModel.uiState.value.songset?.items?.first()?.gapBeats
            assertEquals(0.0, originalGap)

            viewModel.updateTransition(
                "item-1",
                TransitionSettings(
                    gapBeats = 4.5,
                    crossfadeEnabled = 0,
                    crossfadeDurationSeconds = 0.0,
                    keyShiftSemitones = 0,
                    tempoRatio = 1.0,
                ),
            )
            // The update coroutine is now suspended on the gate; fire a fresh load() while it
            // is still in flight. pendingOptimisticEdits is non-zero so the snapshot must be
            // rejected and the optimistically-edited gap must survive the reload.
            viewModel.load()
            advanceUntilIdle()
            assertEquals(4.5, viewModel.uiState.value.songset?.items?.first()?.gapBeats)

            // Release the gated write so the test scope drains cleanly at teardown.
            songsets.updateGate.complete(Unit)
            advanceUntilIdle()
        }

    private fun detailViewModel(
        scope: TestScope,
        songsets: FakeSongsetsRepository,
    ): SongsetDetailViewModel =
        SongsetDetailViewModel(
            songsetId = "set-1",
            songsetsRepository = songsets,
            songsRepository = FakeSongsRepository(),
            scope = scope,
        )
}

internal open class FakeSongsetsRepository(
    private val detail: SongsetDetail = detail(),
    private val deleteError: RuntimeException? = null,
    private val deleteItemError: RuntimeException? = null,
    private val total: Int = 1,
) : SongsetsRepository {
    var reorderUpdates: List<Pair<String, Int>> = emptyList()
    val updatedDescriptions = mutableListOf<String?>()

    override suspend fun listSongsets(limit: Int, offset: Int): SongsetsPage =
        SongsetsPage(songsets = listOf(detail.summary()), total = total)

    override suspend fun createSongset(name: String, description: String?): SongsetSummary =
        detail.summary().copy(id = "created", name = name, description = description)

    override suspend fun getSongset(id: String): SongsetDetail = detail

    override suspend fun updateSongset(
        id: String,
        name: String?,
        description: String?,
    ): SongsetSummary {
        updatedDescriptions += description
        return detail.summary().copy(name = name ?: detail.name, description = description)
    }

    override suspend fun deleteSongset(id: String) {
        deleteError?.let { throw it }
    }

    override suspend fun duplicateSongset(
        id: String,
        name: String,
        description: String?,
    ): SongsetDetail = detail.copy(id = "copy", name = name, description = description)

    override suspend fun addItem(
        songsetId: String,
        songId: String,
        recordingHashPrefix: String?,
        position: Int,
    ): SongsetItem = item("item-new", position, duration = 90.0).copy(songId = songId)

    override suspend fun updateItemTransition(
        songsetId: String,
        itemId: String,
        settings: TransitionSettings,
    ): SongsetItem = detail.items.first { it.id == itemId }

    override suspend fun deleteItem(songsetId: String, itemId: String) {
        deleteItemError?.let { throw it }
    }

    override suspend fun reorderItems(songsetId: String, updates: List<ReorderItemRequest>) {
        reorderUpdates = updates.map { it.itemId to it.position }
    }
}

internal class RecordingAddSongsetsRepository : FakeSongsetsRepository() {
    val addedPositions = mutableListOf<Int>()

    override suspend fun addItem(
        songsetId: String,
        songId: String,
        recordingHashPrefix: String?,
        position: Int,
    ): SongsetItem {
        addedPositions += position
        return super.addItem(songsetId, songId, recordingHashPrefix, position)
    }
}

internal class ClobberingSongsetsRepository : FakeSongsetsRepository() {
    val updateGate = kotlinx.coroutines.CompletableDeferred<Unit>()

    override suspend fun updateItemTransition(
        songsetId: String,
        itemId: String,
        settings: TransitionSettings,
    ): SongsetItem {
        // Suspend until the test releases the gate, keeping the optimistic write flagged as
        // in-flight while load() runs but without hanging the test scheduler indefinitely.
        updateGate.await()
        return super.updateItemTransition(songsetId, itemId, settings)
    }
}

internal class FakeSongsRepository : SongsRepository {
    override suspend fun listSongs(limit: Int, offset: Int, albumName: String?): SongsPage =
        SongsPage(listOf(song("song-3", 90.0)), 1)

    override suspend fun searchSongs(query: String, limit: Int, offset: Int): SongsPage =
        SongsPage(listOf(song("song-3", 90.0)), 1)

    override suspend fun semanticSearch(query: String, limit: Int): SongsPage =
        SongsPage(listOf(song("song-3", 90.0)), 1, query)
}

internal fun detail(items: List<SongsetItem> = listOf(item("item-1", 0), item("item-2", 1))): SongsetDetail =
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
    duration: Double = 60.0,
): SongsetItem =
    SongsetItem(
        id = id,
        songId = "song-$id",
        recordingHashPrefix = "hash-$id",
        position = position,
        gapBeats = 0.0,
        crossfadeEnabled = 0,
        crossfadeDurationSeconds = 0.0,
        keyShiftSemitones = 0,
        tempoRatio = 1.0,
        song = SongsetItemSong(id = "song-$id", title = "Song $id", albumName = "Hymns"),
        recording = SongsetItemRecording(contentHash = "content-$id", durationSeconds = duration),
    )

internal fun song(
    id: String,
    duration: Double,
): Song =
    Song(
        id = id,
        title = "New Song",
        recordings =
            listOf(
                Recording(
                    contentHash = "content-$id",
                    hashPrefix = "hash-$id",
                    durationSeconds = duration,
                    visibilityStatus = "published",
                ),
            ),
    )
