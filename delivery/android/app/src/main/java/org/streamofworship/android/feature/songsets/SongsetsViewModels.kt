package org.streamofworship.android.feature.songsets

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.streamofworship.android.core.model.RenderState
import org.streamofworship.android.core.model.Song
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetItem
import org.streamofworship.android.core.model.SongsetMaxDurationSeconds
import org.streamofworship.android.core.model.SongsetMaxSongs
import org.streamofworship.android.core.model.SongsetSummary
import org.streamofworship.android.core.model.TransitionSettings
import org.streamofworship.android.core.model.label
import org.streamofworship.android.core.model.withItemsMarkedStale
import org.streamofworship.android.data.songs.SongsRepository
import org.streamofworship.android.data.songsets.ReorderItemRequest
import org.streamofworship.android.data.songsets.SongsetsRepository
import java.util.concurrent.atomic.AtomicInteger

data class SongsetsListUiState(
    val songsets: List<SongsetSummary> = emptyList(),
    val total: Int = 0,
    val pageSize: Int = 20,
    val isLoading: Boolean = false,
    val isRefreshing: Boolean = false,
    val isCreating: Boolean = false,
    val error: String? = null,
)

class SongsetsListViewModel(
    private val repository: SongsetsRepository,
    private val scope: CoroutineScope? = null,
) : ViewModel() {
    private val mutableState = MutableStateFlow(SongsetsListUiState())
    val uiState: StateFlow<SongsetsListUiState> = mutableState

    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    fun load(refresh: Boolean = false) {
        launchScope.launch {
            mutableState.update {
                it.copy(
                    isLoading = !refresh && it.songsets.isEmpty(),
                    isRefreshing = refresh,
                    error = null,
                )
            }
            runCatching {
                repository.listSongsets(limit = mutableState.value.pageSize, offset = 0)
            }.onSuccess { page ->
                mutableState.update {
                    it.copy(
                        songsets = page.songsets,
                        total = page.total,
                        isLoading = false,
                        isRefreshing = false,
                    )
                }
            }.onFailure { error ->
                mutableState.update {
                    it.copy(
                        isLoading = false,
                        isRefreshing = false,
                        error = error.message ?: "Failed to load songsets",
                    )
                }
            }
        }
    }

    fun loadMore() {
        val current = mutableState.value
        if (current.songsets.size >= current.total || current.isLoading) return
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, error = null) }
            runCatching {
                repository.listSongsets(
                    limit = current.pageSize,
                    offset = current.songsets.size,
                )
            }.onSuccess { page ->
                mutableState.update {
                    it.copy(
                        songsets = it.songsets + page.songsets,
                        total = page.total,
                        isLoading = false,
                    )
                }
            }.onFailure { error ->
                mutableState.update {
                    it.copy(isLoading = false, error = error.message ?: "Failed to load more songsets")
                }
            }
        }
    }

    fun create(
        name: String,
        description: String?,
        onCreated: (String) -> Unit = {},
    ) {
        if (mutableState.value.isCreating) return
        val cleanName = name.trim()
        if (cleanName.isEmpty()) {
            mutableState.update { it.copy(error = "Songset name is required") }
            return
        }
        mutableState.update { it.copy(isCreating = true, error = null) }
        launchScope.launch {
            runCatching {
                repository.createSongset(cleanName, description?.trim()?.takeIf { it.isNotEmpty() })
            }.onSuccess { created ->
                mutableState.update {
                    it.copy(
                        songsets = listOf(created) + it.songsets,
                        total = it.total + 1,
                        isCreating = false,
                    )
                }
                onCreated(created.id)
            }.onFailure { error ->
                mutableState.update {
                    it.copy(isCreating = false, error = error.message ?: "Failed to create songset")
                }
            }
        }
    }

    fun duplicate(id: String) {
        val source = mutableState.value.songsets.firstOrNull { it.id == id } ?: return
        launchScope.launch {
            runCatching {
                repository.duplicateSongset(
                    id = id,
                    name = "Copy of ${source.name}",
                    description = source.description,
                )
            }.onSuccess { duplicated ->
                mutableState.update {
                    it.copy(
                        songsets = listOf(duplicated.summary()) + it.songsets,
                        total = it.total + 1,
                        error = null,
                    )
                }
            }.onFailure { error ->
                mutableState.update { it.copy(error = error.message ?: "Failed to duplicate songset") }
            }
        }
    }

    fun delete(id: String) {
        val previous = mutableState.value.songsets
        val removed = previous.firstOrNull { it.id == id } ?: return
        mutableState.update {
            it.copy(songsets = it.songsets.filterNot { songset -> songset.id == id }, total = it.total - 1)
        }
        launchScope.launch {
            runCatching { repository.deleteSongset(id) }
                .onFailure { error ->
                    mutableState.update {
                        it.copy(
                            songsets = previous,
                            total = previous.size,
                            error = error.message ?: "Failed to delete ${removed.name}",
                        )
                    }
                }
        }
    }
}

data class SongsetDetailUiState(
    val songset: SongsetDetail? = null,
    val isLoading: Boolean = false,
    val error: String? = null,
    val validationMessage: String? = null,
) {
    val isFull: Boolean
        get() = (songset?.items?.size ?: 0) >= SongsetMaxSongs

    val isDurationOverLimit: Boolean
        get() = (songset?.durationSeconds ?: 0.0) > SongsetMaxDurationSeconds
}

class SongsetDetailViewModel(
    private val songsetId: String,
    private val songsetsRepository: SongsetsRepository,
    private val songsRepository: SongsRepository,
    private val scope: CoroutineScope? = null,
) : ViewModel() {
    private val mutableState = MutableStateFlow(SongsetDetailUiState())
    val uiState: StateFlow<SongsetDetailUiState> = mutableState

    private val mutableSearchState = MutableStateFlow(SongSearchUiState())
    val searchState: StateFlow<SongSearchUiState> = mutableSearchState

    private val launchScope: CoroutineScope
        get() = scope ?: viewModelScope

    // Tracks optimistic edits (description, reorder, transition, removal, add) whose local
    // state has not yet been flushed to the server. While > 0, load() must not clobber the
    // optimistically-edited songset with a server snapshot fetched mid-edit.
    private val pendingOptimisticEdits = AtomicInteger(0)

    private val addSongMutex = Mutex()

    fun load() {
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, error = null) }
            runCatching { songsetsRepository.getSongset(songsetId) }
                .onSuccess { detail ->
                    // Only apply the server snapshot when no optimistic edit is mid-flight;
                    // otherwise in-flight edits (description change, reorder, transition tweak,
                    // item removal) would be silently discarded before the server has flushed
                    // the optimistic value back.
                    val applySnapshot = pendingOptimisticEdits.get() == 0
                    mutableState.update {
                        if (applySnapshot) {
                            it.copy(songset = detail, isLoading = false)
                        } else {
                            it.copy(isLoading = false)
                        }
                    }
                }.onFailure { error ->
                    val applyError = pendingOptimisticEdits.get() == 0
                    mutableState.update {
                        if (applyError) {
                            it.copy(isLoading = false, error = error.message ?: "Failed to load songset")
                        } else {
                            it.copy(isLoading = false)
                        }
                    }
                }
        }
    }

    fun updateDescription(description: String) {
        val previous = mutableState.value.songset ?: return
        val nextDescription = description.trim().takeIf { it.isNotEmpty() }
        mutableState.update { it.copy(songset = previous.copy(description = nextDescription), error = null) }
        pendingOptimisticEdits.incrementAndGet()
        launchScope.launch {
            try {
                runCatching {
                    songsetsRepository.updateSongset(songsetId, description = nextDescription)
                }.onFailure { error ->
                    mutableState.update {
                        it.copy(songset = previous, error = error.message ?: "Failed to update description")
                    }
                }
            } finally {
                pendingOptimisticEdits.decrementAndGet()
            }
        }
    }

    fun removeItem(itemId: String) {
        val previous = mutableState.value.songset ?: return
        val nextItems = previous.items.filterNot { it.id == itemId }.mapIndexed { index, item ->
            item.copy(position = index)
        }
        mutableState.update { it.copy(songset = previous.withItemsMarkedStale(nextItems), error = null) }
        pendingOptimisticEdits.incrementAndGet()
        launchScope.launch {
            try {
                runCatching { songsetsRepository.deleteItem(songsetId, itemId) }
                    .onFailure { error ->
                        mutableState.update {
                            it.copy(songset = previous, error = error.message ?: "Failed to remove song")
                        }
                    }
            } finally {
                pendingOptimisticEdits.decrementAndGet()
            }
        }
    }

    fun moveItem(
        itemId: String,
        direction: Int,
    ) {
        val previous = mutableState.value.songset ?: return
        val currentIndex = previous.items.indexOfFirst { it.id == itemId }
        val targetIndex = currentIndex + direction
        if (currentIndex !in previous.items.indices || targetIndex !in previous.items.indices) return
        val reordered = previous.items.toMutableList()
        val moved = reordered.removeAt(currentIndex)
        reordered.add(targetIndex, moved)
        val nextItems = reordered.mapIndexed { index, item -> item.copy(position = index) }
        mutableState.update { it.copy(songset = previous.withItemsMarkedStale(nextItems), error = null) }
        pendingOptimisticEdits.incrementAndGet()
        launchScope.launch {
            try {
                val updates = nextItems.map { ReorderItemRequest(itemId = it.id, position = it.position) }
                runCatching { songsetsRepository.reorderItems(songsetId, updates) }
                    .onFailure { error ->
                        mutableState.update {
                            it.copy(songset = previous, error = error.message ?: "Failed to reorder items")
                        }
                    }
            } finally {
                pendingOptimisticEdits.decrementAndGet()
            }
        }
    }

    fun updateTransition(
        itemId: String,
        settings: TransitionSettings,
    ) {
        val previous = mutableState.value.songset ?: return
        val nextItems =
            previous.items.map { item ->
                if (item.id == itemId) {
                    item.copy(
                        gapBeats = settings.gapBeats,
                        crossfadeEnabled = settings.crossfadeEnabled,
                        crossfadeDurationSeconds = settings.crossfadeDurationSeconds,
                        keyShiftSemitones = settings.keyShiftSemitones,
                        tempoRatio = settings.tempoRatio,
                    )
                } else {
                    item
                }
            }
        mutableState.update { it.copy(songset = previous.withItemsMarkedStale(nextItems), error = null) }
        pendingOptimisticEdits.incrementAndGet()
        launchScope.launch {
            try {
                runCatching { songsetsRepository.updateItemTransition(songsetId, itemId, settings) }
                    .onFailure { error ->
                        mutableState.update {
                            it.copy(songset = previous, error = error.message ?: "Failed to update transition")
                        }
                    }
            } finally {
                pendingOptimisticEdits.decrementAndGet()
            }
        }
    }

    fun browseSongs(query: String = "") {
        launchScope.launch {
            mutableSearchState.update { it.copy(isLoading = true, query = query, error = null) }
            val result =
                runCatching {
                    if (query.isBlank()) {
                        songsRepository.listSongs()
                    } else {
                        songsRepository.searchSongs(query.trim())
                    }
                }
            result.onSuccess { page ->
                mutableSearchState.update {
                    it.copy(songs = page.songs, total = page.total, isLoading = false)
                }
            }.onFailure { error ->
                mutableSearchState.update {
                    it.copy(isLoading = false, error = error.message ?: "Failed to search songs")
                }
            }
        }
    }

    fun semanticSearch(query: String) {
        if (query.isBlank()) return
        launchScope.launch {
            mutableSearchState.update { it.copy(isLoading = true, query = query, error = null, semantic = true) }
            runCatching { songsRepository.semanticSearch(query.trim()) }
                .onSuccess { page ->
                    mutableSearchState.update {
                        it.copy(songs = page.songs, total = page.total, isLoading = false)
                    }
                }.onFailure { error ->
                    mutableSearchState.update {
                        it.copy(isLoading = false, error = error.message ?: "Semantic search unavailable")
                    }
                }
        }
    }

    fun addSong(song: Song) {
        val previous = mutableState.value.songset ?: return
        val recording = song.publishedRecordings.firstOrNull()
        // Synchronous pre-check for immediate UX feedback (does not race-protect alone).
        if (previous.items.size >= SongsetMaxSongs) {
            mutableState.update { it.copy(validationMessage = "Songsets can include up to 5 songs") }
            return
        }
        val durationAfterAdd =
            (previous.durationSeconds ?: 0.0) + (recording?.durationSeconds ?: 0.0)
        if (durationAfterAdd > SongsetMaxDurationSeconds) {
            mutableState.update { it.copy(validationMessage = "Songset duration must stay under 25 minutes") }
            return
        }
        // Serialize concurrent addSong invocations so two rapid taps do not both pass
        // validation against the same snapshot and POST the same position to the server.
        launchScope.launch {
            addSongMutex.withLock {
                // Re-read latest state after acquiring the mutex and re-validate before posting
                // so concurrent addSong calls do not both pass validation and POST the same
                // position or exceed the song/duration cap.
                val current = mutableState.value.songset ?: return@withLock
                val currentRecording = song.publishedRecordings.firstOrNull()
                if (current.items.size >= SongsetMaxSongs) {
                    mutableState.update { it.copy(validationMessage = "Songsets can include up to 5 songs") }
                    return@withLock
                }
                val recheckDuration =
                    (current.durationSeconds ?: 0.0) + (currentRecording?.durationSeconds ?: 0.0)
                if (recheckDuration > SongsetMaxDurationSeconds) {
                    mutableState.update { it.copy(validationMessage = "Songset duration must stay under 25 minutes") }
                    return@withLock
                }
                pendingOptimisticEdits.incrementAndGet()
                try {
                    runCatching {
                        songsetsRepository.addItem(
                            songsetId = songsetId,
                            songId = song.id,
                            recordingHashPrefix = currentRecording?.hashPrefix,
                            position = current.items.size,
                        )
                    }.onSuccess { item ->
                        val latest = mutableState.value.songset ?: current
                        mutableState.update {
                            it.copy(
                                songset = latest.withItemsMarkedStale(latest.items + item),
                                validationMessage = null,
                                error = null,
                            )
                        }
                    }.onFailure { error ->
                        mutableState.update { it.copy(error = error.message ?: "Failed to add song") }
                    }
                } finally {
                    pendingOptimisticEdits.decrementAndGet()
                }
            }
        }
    }
}

data class SongSearchUiState(
    val query: String = "",
    val songs: List<Song> = emptyList(),
    val total: Int = 0,
    val isLoading: Boolean = false,
    val error: String? = null,
    val semantic: Boolean = false,
)

fun SongsetSummary.statusLabel(): String =
    when (renderState) {
        RenderState.Failed -> renderErrorMessage ?: "Render failed"
        else -> renderState.label()
    }
