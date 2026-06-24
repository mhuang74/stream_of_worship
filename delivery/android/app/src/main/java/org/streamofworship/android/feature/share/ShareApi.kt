package org.streamofworship.android.feature.share

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

interface ShareApi {
    @POST("api/share")
    suspend fun createShare(
        @Body request: CreateShareRequest,
    ): Response<ShareToken>

    @GET("api/share")
    suspend fun listShares(
        @Query("songsetId") songsetId: String? = null,
        @Query("renderJobId") renderJobId: String? = null,
    ): Response<ShareListResponse>
}
