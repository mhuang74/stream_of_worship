package org.streamofworship.android.data.render

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

interface RenderApi {
    @POST("api/render-jobs")
    suspend fun createRenderJob(
        @Body request: CreateRenderJobRequest,
    ): Response<RenderJob>

    @GET("api/render-jobs/{id}")
    suspend fun getRenderJob(
        @Path("id") id: String,
    ): Response<RenderJob>

    @DELETE("api/render-jobs/{id}")
    suspend fun cancelRenderJob(
        @Path("id") id: String,
    ): Response<RenderJob>

    @GET("api/render-jobs/{id}/artifact-sizes")
    suspend fun getArtifactSizes(
        @Path("id") id: String,
    ): Response<ArtifactSizes>
}
