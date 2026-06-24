package org.streamofworship.android.feature.songsets

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
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
        val cleanName = name.trim()
        if (cleanName.isEmpty()) {
            mutableState.update { it.copy(error = "Songset name is required") }
            return
        }
        launchScope.launch {
            mutableState.update { it.copy(isCreating = true, error = null) }
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

    fun load() {
        launchScope.launch {
            mutableState.update { it.copy(isLoading = true, error = null) }
            runCatching { songsetsRepository.getSongset(songsetId) }
                .onSuccess { detail ->
                    mutableState.update { it.copy(songset = detail, isLoading = false) }
                }.onFailure { error ->
                    mutableState.update {
                        it.copy(isLoading = false, error = error.message ?: "Failed to load songset")
                    }
                }
        }
    }

    fun updateDescription(description: String) {
        val previous = mutableState.value.songset ?: return
        val nextDescription = description.trim().takeIf { it.isNotEmpty() }
        mutableState.update { it.copy(songset = previous.copy(description = nextDescription), error = null) }
        launchScope.launch {
            runCatching {
                songsetsRepository.updateSongset(songsetId, description = nextDescription)
            }.onFailure { error ->
                mutableState.update {
                    it.copy(songset = previous, error = error.message ?: "Failed to update description")
                }
            }
        }
    }

    fun removeItem(itemId: String) {
        val previous = mutableState.value.songset ?: return
        val nextItems = previous.items.filterNot { it.id == itemId }.mapIndexed { index, item ->
            item.copy(position = index)
        }
        mutableState.update { it.copy(songset = previous.withItemsMarkedStale(nextItems), error = null) }
        launchScope.launch {
            runCatching { songsetsRepository.deleteItem(songsetId, itemId) }
                .onFailure { error ->
                    mutableState.update {
                        it.copy(songset = previous, error = error.message ?: "Failed to remove song")
                    }
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
        launchScope.launch {
            val updates = nextItems.map { ReorderItemRequest(itemId = it.id, position = it.position) }
            runCatching { songsetsRepository.reorderItems(songsetId, updates) }
                .onFailure { error ->
                    mutableState.update {
                        it.copy(songset = previous, error = error.message ?: "Failed to reorder items")
                    }
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
        launchScope.launch {
            runCatching { songsetsRepository.updateItemTransition(songsetId, itemId, settings) }
                .onFailure { error ->
                    mutableState.update {
                        it.copy(songset = previous, error = error.message ?: "Failed to update transition")
                    }
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
        val durationAfterAdd =
            (previous.durationSeconds ?: 0.0) + (recording?.durationSeconds ?: 0.0)
        if (previous.items.size >= SongsetMaxSongs) {
            mutableState.update { it.copy(validationMessage = "Songsets can include up to 5 songs") }
            return
        }
        if (durationAfterAdd > SongsetMaxDurationSeconds) {
            mutableState.update { it.copy(validationMessage = "Songset duration must stay under 25 minutes") }
            return
        }
        launchScope.launch {
            runCatching {
                songsetsRepository.addItem(
                    songsetId = songsetId,
                    songId = song.id,
                    recordingHashPrefix = recording?.hashPrefix,
                    position = previous.items.size,
                )
            }.onSuccess { item ->
                val latest = mutableState.value.songset ?: previous
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
