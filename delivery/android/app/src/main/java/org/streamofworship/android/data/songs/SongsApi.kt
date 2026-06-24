package org.streamofworship.android.data.songs

import kotlinx.serialization.Serializable
import org.streamofworship.android.core.model.SongsPage
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

interface SongsApi {
    @GET("api/songs")
    suspend fun listSongs(
        @Query("limit") limit: Int,
        @Query("offset") offset: Int,
        @Query("visibilityStatus") visibilityStatus: String = "published",
        @Query("albumName") albumName: String? = null,
        @Query("albumSeries") albumSeries: String? = null,
        @Query("composer") composer: String? = null,
        @Query("lyricist") lyricist: String? = null,
    ): Response<SongsPage>

    @GET("api/songs/search")
    suspend fun searchSongs(
        @Query("q") query: String,
        @Query("limit") limit: Int,
        @Query("offset") offset: Int,
        @Query("visibilityStatus") visibilityStatus: String = "published",
    ): Response<SongsPage>

    @POST("api/songs/search/semantic")
    suspend fun semanticSearch(
        @Body request: SemanticSearchRequest,
    ): Response<SongsPage>
}

@Serializable
data class SemanticSearchRequest(
    val query: String,
    val limit: Int = 20,
)
