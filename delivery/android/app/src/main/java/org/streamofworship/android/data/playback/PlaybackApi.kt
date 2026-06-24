package org.streamofworship.android.data.playback

import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Query

interface PlaybackApi {
    @GET("api/signed-url")
    suspend fun getSignedUrl(
        @Query("renderJobId") renderJobId: String? = null,
        @Query("hashPrefix") hashPrefix: String? = null,
        @Query("fileType") fileType: String? = null,
        @Query("expiresInSeconds") expiresInSeconds: Int? = null,
        @Query("contentDisposition") contentDisposition: String? = null,
    ): Response<SignedUrlResponse>

    @GET("api/r2/artifact/{jobId}/chapters.json")
    suspend fun getChapters(
        @retrofit2.http.Path("jobId") jobId: String,
    ): Response<ChaptersManifestDto>
}
