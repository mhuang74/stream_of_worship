package org.streamofworship.android.data.songsets

import kotlinx.serialization.SerializationException
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetItem
import org.streamofworship.android.core.model.SongsetSummary
import org.streamofworship.android.core.model.SongsetsPage
import org.streamofworship.android.core.model.TransitionSettings
import org.streamofworship.android.core.network.ApiErrorMapper
import org.streamofworship.android.core.network.ApiException
import retrofit2.Response
import java.io.IOException

interface SongsetsRepository {
    suspend fun listSongsets(
        limit: Int = 20,
        offset: Int = 0,
    ): SongsetsPage

    suspend fun createSongset(
        name: String,
        description: String? = null,
    ): SongsetSummary

    suspend fun getSongset(id: String): SongsetDetail

    suspend fun updateSongset(
        id: String,
        name: String? = null,
        description: String? = null,
    ): SongsetSummary

    suspend fun deleteSongset(id: String)

    suspend fun duplicateSongset(
        id: String,
        name: String,
        description: String?,
    ): SongsetDetail

    suspend fun addItem(
        songsetId: String,
        songId: String,
        recordingHashPrefix: String?,
        position: Int,
    ): SongsetItem

    suspend fun updateItemTransition(
        songsetId: String,
        itemId: String,
        settings: TransitionSettings,
    ): SongsetItem

    suspend fun deleteItem(
        songsetId: String,
        itemId: String,
    )

    suspend fun reorderItems(
        songsetId: String,
        updates: List<ReorderItemRequest>,
    )
}

class HttpSongsetsRepository(
    private val api: SongsetsApi,
) : SongsetsRepository {
    override suspend fun listSongsets(
        limit: Int,
        offset: Int,
    ): SongsetsPage = execute { api.listSongsets(limit = limit, offset = offset) }

    override suspend fun createSongset(
        name: String,
        description: String?,
    ): SongsetSummary =
        execute {
            api.createSongset(CreateSongsetRequest(name = name, description = description))
        }

    override suspend fun getSongset(id: String): SongsetDetail = execute { api.getSongset(id) }

    override suspend fun updateSongset(
        id: String,
        name: String?,
        description: String?,
    ): SongsetSummary =
        execute {
            api.updateSongset(id, UpdateSongsetRequest(name = name, description = description))
        }

    override suspend fun deleteSongset(id: String) {
        execute<SuccessResponse> { api.deleteSongset(id) }
    }

    override suspend fun duplicateSongset(
        id: String,
        name: String,
        description: String?,
    ): SongsetDetail =
        execute {
            api.duplicateSongset(
                id = id,
                request = DuplicateSongsetRequest(name = name, description = description),
            )
        }

    override suspend fun addItem(
        songsetId: String,
        songId: String,
        recordingHashPrefix: String?,
        position: Int,
    ): SongsetItem =
        execute {
            api.addItem(
                songsetId,
                CreateSongsetItemRequest(
                    songId = songId,
                    recordingHashPrefix = recordingHashPrefix,
                    position = position,
                ),
            )
        }

    override suspend fun updateItemTransition(
        songsetId: String,
        itemId: String,
        settings: TransitionSettings,
    ): SongsetItem =
        execute {
            api.updateItem(
                songsetId,
                UpdateSongsetItemRequest(
                    itemId = itemId,
                    gapBeats = settings.gapBeats,
                    crossfadeEnabled = settings.crossfadeEnabled,
                    crossfadeDurationSeconds = settings.crossfadeDurationSeconds,
                    keyShiftSemitones = settings.keyShiftSemitones,
                    tempoRatio = settings.tempoRatio,
                ),
            )
        }

    override suspend fun deleteItem(
        songsetId: String,
        itemId: String,
    ) {
        execute<SuccessResponse> { api.deleteItem(songsetId = songsetId, itemId = itemId) }
    }

    override suspend fun reorderItems(
        songsetId: String,
        updates: List<ReorderItemRequest>,
    ) {
        execute<SuccessResponse> {
            api.reorderItems(songsetId, ReorderItemsRequest(updates = updates))
        }
    }
}

suspend fun <T : Any> execute(block: suspend () -> Response<T>): T =
    try {
        val response = block()
        if (!response.isSuccessful) {
            throw ApiErrorMapper.fromHttpError(response.code(), response.errorBody())
        }
        response.body()
            ?: throw ApiErrorMapper.malformed(IllegalStateException("Empty response body."))
    } catch (exception: IOException) {
        if (exception is ApiException) throw exception
        throw ApiErrorMapper.network(exception)
    } catch (exception: SerializationException) {
        throw ApiErrorMapper.malformed(exception)
    } catch (exception: IllegalArgumentException) {
        throw ApiErrorMapper.malformed(exception)
    }
