package org.streamofworship.android.feature.songsets

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.KeyboardArrowUp
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.streamofworship.android.core.design.SowEmptyState
import org.streamofworship.android.core.design.SowErrorState
import org.streamofworship.android.core.design.SowLoadingState
import org.streamofworship.android.core.model.RenderState
import org.streamofworship.android.core.model.Song
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetItem
import org.streamofworship.android.core.model.SongsetMaxDurationSeconds
import org.streamofworship.android.core.model.SongsetMaxSongs
import org.streamofworship.android.core.model.SongsetSummary
import org.streamofworship.android.core.model.TransitionSettings
import org.streamofworship.android.core.model.label

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SongsetsListScreen(
    viewModel: SongsetsListViewModel,
    onOpenSongset: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    LaunchedEffect(viewModel) {
        if (state.songsets.isEmpty() && !state.isLoading) viewModel.load()
    }
    var name by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }

    PullToRefreshBox(
        isRefreshing = state.isRefreshing,
        onRefresh = { viewModel.load(refresh = true) },
        modifier = modifier.fillMaxSize().testTag("songsets-list-screen"),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Text("Songsets", style = MaterialTheme.typography.headlineSmall)
                Text(
                    "Manage worship sets and rendering status.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            item {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedTextField(
                            value = name,
                            onValueChange = { name = it },
                            label = { Text("Songset name") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        OutlinedTextField(
                            value = description,
                            onValueChange = { description = it },
                            label = { Text("Description") },
                            minLines = 2,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Button(
                            onClick = {
                                viewModel.create(name, description) {
                                    name = ""
                                    description = ""
                                    onOpenSongset(it)
                                }
                            },
                            enabled = !state.isCreating,
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Icon(Icons.Outlined.Add, contentDescription = null)
                            Text(if (state.isCreating) "Creating..." else "Create songset")
                        }
                    }
                }
            }
            state.error?.let { message ->
                item {
                    SowErrorState(
                        title = "Songsets unavailable",
                        message = message,
                        actionLabel = "Refresh",
                        onAction = { viewModel.load(refresh = true) },
                    )
                }
            }
            if (state.isLoading && state.songsets.isEmpty()) {
                item { SowLoadingState(label = "Loading songsets") }
            } else if (state.songsets.isEmpty() && state.error == null) {
                item {
                    SowEmptyState(
                        title = "No songsets",
                        message = "Create a worship set to begin.",
                    )
                }
            }
            items(state.songsets, key = { it.id }) { songset ->
                SongsetSummaryCard(
                    songset = songset,
                    onOpen = { onOpenSongset(songset.id) },
                    onDuplicate = { viewModel.duplicate(songset.id) },
                    onDelete = { viewModel.delete(songset.id) },
                )
            }
            if (state.songsets.size < state.total) {
                item {
                    OutlinedButton(
                        onClick = viewModel::loadMore,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Load more")
                    }
                }
            }
            item {
                OutlinedButton(
                    onClick = { viewModel.load(refresh = true) },
                    modifier = Modifier.fillMaxWidth().testTag("songsets-refresh-button"),
                ) {
                    Icon(Icons.Outlined.Refresh, contentDescription = null)
                    Text("Refresh")
                }
            }
        }
    }
}

@Composable
private fun SongsetSummaryCard(
    songset: SongsetSummary,
    onOpen: () -> Unit,
    onDuplicate: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        onClick = onOpen,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        modifier = Modifier.fillMaxWidth().testTag("songset-card-${songset.id}"),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(songset.name, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        "${songset.itemCount} songs • ${formatDuration(songset.durationSeconds)}",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                RenderStateChip(songset.renderState, songset.statusLabel())
            }
            songset.description?.let {
                Text(it, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onDuplicate, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Outlined.ContentCopy, contentDescription = null)
                    Text("Duplicate")
                }
                OutlinedButton(onClick = onDelete, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Outlined.Delete, contentDescription = null)
                    Text("Delete")
                }
            }
        }
    }
}

@Composable
fun SongsetDetailScreen(
    viewModel: SongsetDetailViewModel,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val state by viewModel.uiState.collectAsState()
    val searchState by viewModel.searchState.collectAsState()
    LaunchedEffect(viewModel) {
        if (state.songset == null && !state.isLoading) {
            viewModel.load()
            viewModel.browseSongs()
        }
    }

    LazyColumn(
        modifier = modifier.fillMaxSize().padding(16.dp).testTag("songset-detail-screen"),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Back")
            }
        }
        if (state.isLoading) {
            item { SowLoadingState(label = "Loading songset") }
        }
        state.error?.let { message ->
            item {
                SowErrorState(
                    title = "Songset update failed",
                    message = message,
                    actionLabel = "Reload",
                    onAction = viewModel::load,
                )
            }
        }
        state.validationMessage?.let { message ->
            item {
                Text(
                    text = message,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.testTag("songset-validation-message"),
                )
            }
        }
        state.songset?.let { songset ->
            item { SongsetHeader(songset = songset, onSaveDescription = viewModel::updateDescription) }
            item {
                if (state.isDurationOverLimit) {
                    Text(
                        "Duration exceeds 25 minutes.",
                        color = MaterialTheme.colorScheme.error,
                    )
                }
                Text("${songset.items.size}/$SongsetMaxSongs songs • limit ${formatDuration(SongsetMaxDurationSeconds.toDouble())}")
            }
            items(songset.items, key = { it.id }) { item ->
                SongsetItemCard(
                    item = item,
                    onRemove = { viewModel.removeItem(item.id) },
                    onMoveUp = { viewModel.moveItem(item.id, -1) },
                    onMoveDown = { viewModel.moveItem(item.id, 1) },
                    onTransitionChange = { viewModel.updateTransition(item.id, it) },
                )
            }
            item {
                SongBrowsePanel(
                    searchState = searchState,
                    isFull = state.isFull,
                    onSearch = viewModel::browseSongs,
                    onSemanticSearch = viewModel::semanticSearch,
                    onAddSong = viewModel::addSong,
                )
            }
        }
    }
}

@Composable
private fun SongsetHeader(
    songset: SongsetDetail,
    onSaveDescription: (String) -> Unit,
) {
    var description by remember(songset.id, songset.description) {
        mutableStateOf(songset.description.orEmpty())
    }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(songset.name, style = MaterialTheme.typography.titleLarge)
                    Text("Updated ${songset.updatedAt}", style = MaterialTheme.typography.bodySmall)
                }
                RenderStateChip(songset.renderState, songset.renderState.label())
            }
            OutlinedTextField(
                value = description,
                onValueChange = { description = it },
                label = { Text("Description") },
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedButton(onClick = { onSaveDescription(description) }) {
                Text("Save description")
            }
        }
    }
}

@Composable
private fun SongsetItemCard(
    item: SongsetItem,
    onRemove: () -> Unit,
    onMoveUp: () -> Unit,
    onMoveDown: () -> Unit,
    onTransitionChange: (TransitionSettings) -> Unit,
) {
    var settings by remember(item.id, item.transitionSettings) { mutableStateOf(item.transitionSettings) }
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        modifier = Modifier.fillMaxWidth().testTag("songset-item-${item.id}"),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(item.song?.title ?: "Unknown song", fontWeight = FontWeight.SemiBold)
                    Text(
                        listOfNotNull(
                            item.song?.albumName,
                            item.recording?.tempoBpm?.let { "${it.toInt()} BPM" },
                            item.recording?.musicalKey,
                            item.recording?.durationSeconds?.let(::formatDuration),
                        ).joinToString(" • "),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                IconButton(onClick = onMoveUp) {
                    Icon(Icons.Outlined.KeyboardArrowUp, contentDescription = "Move up")
                }
                IconButton(onClick = onMoveDown) {
                    Icon(Icons.Outlined.KeyboardArrowDown, contentDescription = "Move down")
                }
                IconButton(onClick = onRemove) {
                    Icon(Icons.Outlined.Delete, contentDescription = "Remove")
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(Icons.Outlined.Tune, contentDescription = null)
                Text("Transition", fontWeight = FontWeight.Medium)
            }
            OutlinedTextField(
                value = settings.gapBeats?.toString().orEmpty(),
                onValueChange = { value ->
                    val next = settings.copy(gapBeats = value.toDoubleOrNull() ?: 0.0)
                    settings = next
                    onTransitionChange(next)
                },
                label = { Text("Gap beats") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Crossfade", modifier = Modifier.weight(1f))
                Switch(
                    checked = settings.crossfadeEnabled == 1,
                    onCheckedChange = { enabled ->
                        val next = settings.copy(crossfadeEnabled = if (enabled) 1 else 0)
                        settings = next
                        onTransitionChange(next)
                    },
                )
            }
        }
    }
}

@Composable
private fun SongBrowsePanel(
    searchState: SongSearchUiState,
    isFull: Boolean,
    onSearch: (String) -> Unit,
    onSemanticSearch: (String) -> Unit,
    onAddSong: (Song) -> Unit,
) {
    var query by remember { mutableStateOf("") }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(
            modifier = Modifier.padding(14.dp).testTag("song-browse-panel"),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Browse songs", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                label = { Text("Title, album, composer, lyrics") },
                leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
                modifier = Modifier.fillMaxWidth().testTag("song-search-query"),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { onSearch(query) }, modifier = Modifier.weight(1f)) {
                    Text("Search")
                }
                OutlinedButton(onClick = { onSemanticSearch(query) }, modifier = Modifier.weight(1f)) {
                    Text("Describe")
                }
            }
            if (isFull) {
                Text("Songset is full.", color = MaterialTheme.colorScheme.error)
            }
            searchState.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            if (searchState.isLoading) {
                SowLoadingState(label = "Searching songs")
            }
            searchState.songs.forEach { song ->
                SongSearchResult(song = song, addEnabled = !isFull, onAdd = { onAddSong(song) })
            }
        }
    }
}

@Composable
private fun SongSearchResult(
    song: Song,
    addEnabled: Boolean,
    onAdd: () -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(song.title, fontWeight = FontWeight.Medium)
                    Text(
                        listOfNotNull(
                            song.albumName,
                            song.composer,
                            song.publishedRecordings.firstOrNull()?.tempoBpm?.let { "${it.toInt()} BPM" },
                            song.publishedRecordings.firstOrNull()?.musicalKey,
                        ).joinToString(" • "),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Button(onClick = onAdd, enabled = addEnabled && song.publishedRecordings.isNotEmpty()) {
                    Text("Add")
                }
            }
            song.matchingSnippet?.let {
                Text(it, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun RenderStateChip(
    state: RenderState,
    label: String,
) {
    val container =
        when (state) {
            RenderState.Fresh -> MaterialTheme.colorScheme.primaryContainer
            RenderState.Rendering -> MaterialTheme.colorScheme.secondaryContainer
            RenderState.Failed -> MaterialTheme.colorScheme.errorContainer
            RenderState.Stale -> MaterialTheme.colorScheme.tertiaryContainer
            RenderState.Unrendered -> MaterialTheme.colorScheme.surfaceVariant
        }
    AssistChip(
        onClick = {},
        label = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        colors = androidx.compose.material3.AssistChipDefaults.assistChipColors(containerColor = container),
    )
}

private fun formatDuration(seconds: Double?): String {
    val total = seconds?.toInt() ?: 0
    val minutes = total / 60
    val remainingSeconds = total % 60
    return "${minutes}:${remainingSeconds.toString().padStart(2, '0')}"
}
