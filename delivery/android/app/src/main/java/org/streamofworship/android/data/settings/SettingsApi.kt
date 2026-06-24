package org.streamofworship.android.data.settings

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.PUT

interface SettingsApi {
    @GET("api/settings")
    suspend fun getSettings(): Response<SettingsEnvelope>

    @PUT("api/settings")
    suspend fun saveSettings(
        @Body request: SettingsUpdateRequest,
    ): Response<SettingsEnvelope>
}
