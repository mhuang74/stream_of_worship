package org.streamofworship.android.data.songsets

import kotlinx.serialization.Serializable
import org.streamofworship.android.core.model.SongsetDetail
import org.streamofworship.android.core.model.SongsetItem
import org.streamofworship.android.core.model.SongsetSummary
import org.streamofworship.android.core.model.SongsetsPage
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface SongsetsApi {
    @GET("api/songsets")
    suspend fun listSongsets(
        @Query("limit") limit: Int,
        @Query("offset") offset: Int,
    ): Response<SongsetsPage>

    @POST("api/songsets")
    suspend fun createSongset(
        @Body request: CreateSongsetRequest,
    ): Response<SongsetSummary>

    @GET("api/songsets/{id}")
    suspend fun getSongset(
        @Path("id") id: String,
    ): Response<SongsetDetail>

    @PATCH("api/songsets/{id}")
    suspend fun updateSongset(
        @Path("id") id: String,
        @Body request: UpdateSongsetRequest,
    ): Response<SongsetSummary>

    @DELETE("api/songsets/{id}")
    suspend fun deleteSongset(
        @Path("id") id: String,
    ): Response<SuccessResponse>

    @POST("api/songsets/{id}/duplicate")
    suspend fun duplicateSongset(
        @Path("id") id: String,
        @Body request: DuplicateSongsetRequest,
    ): Response<SongsetDetail>

    @POST("api/songsets/{id}/items")
    suspend fun addItem(
        @Path("id") songsetId: String,
        @Body request: CreateSongsetItemRequest,
    ): Response<SongsetItem>

    @PATCH("api/songsets/{id}/items")
    suspend fun updateItem(
        @Path("id") songsetId: String,
        @Body request: UpdateSongsetItemRequest,
    ): Response<SongsetItem>

    @DELETE("api/songsets/{id}/items")
    suspend fun deleteItem(
        @Path("id") songsetId: String,
        @Query("itemId") itemId: String,
    ): Response<SuccessResponse>

    @POST("api/songsets/{id}/items/reorder")
    suspend fun reorderItems(
        @Path("id") songsetId: String,
        @Body request: ReorderItemsRequest,
    ): Response<SuccessResponse>
}

@Serializable
data class CreateSongsetRequest(
    val name: String,
    val description: String? = null,
)

@Serializable
data class UpdateSongsetRequest(
    val name: String? = null,
    val description: String? = null,
)

@Serializable
data class DuplicateSongsetRequest(
    val name: String,
    val description: String? = null,
)

@Serializable
data class CreateSongsetItemRequest(
    val songId: String,
    val recordingHashPrefix: String? = null,
    val position: Int,
    val gapBeats: Double? = null,
    val crossfadeEnabled: Int? = null,
    val crossfadeDurationSeconds: Double? = null,
    val keyShiftSemitones: Int? = null,
    val tempoRatio: Double? = null,
)

@Serializable
data class UpdateSongsetItemRequest(
    val itemId: String,
    val songId: String? = null,
    val recordingHashPrefix: String? = null,
    val position: Int? = null,
    val gapBeats: Double? = null,
    val crossfadeEnabled: Int? = null,
    val crossfadeDurationSeconds: Double? = null,
    val keyShiftSemitones: Int? = null,
    val tempoRatio: Double? = null,
)

@Serializable
data class ReorderItemsRequest(
    val updates: List<ReorderItemRequest>,
)

@Serializable
data class ReorderItemRequest(
    val itemId: String,
    val position: Int,
)

@Serializable
data class SuccessResponse(
    val success: Boolean,
)
