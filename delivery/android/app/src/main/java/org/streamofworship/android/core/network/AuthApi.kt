package org.streamofworship.android.core.network

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

interface AuthApi {
    @POST("api/auth/sign-in/email")
    suspend fun signIn(
        @Body request: EmailPasswordRequest,
    ): Response<ResponseBody>

    @POST("api/auth/sign-up/email")
    suspend fun register(
        @Body request: RegisterRequest,
    ): Response<ResponseBody>

    @POST("api/auth/sign-out")
    suspend fun signOut(): Response<ResponseBody>

    @GET("api/auth/get-session")
    suspend fun getSession(): Response<ResponseBody>
}

@Serializable
data class EmailPasswordRequest(
    val email: String,
    val password: String,
)

@Serializable
data class RegisterRequest(
    val email: String,
    val password: String,
    val name: String,
)

@Serializable
data class BetterAuthEnvelope(
    val user: AuthUser? = null,
    val session: AuthSession? = null,
    val data: BetterAuthData? = null,
    val error: BetterAuthError? = null,
)

@Serializable
data class BetterAuthData(
    val user: AuthUser? = null,
    val session: AuthSession? = null,
)

@Serializable
data class BetterAuthError(
    val message: String? = null,
    val code: String? = null,
    val status: Int? = null,
)

@Serializable
data class AuthUser(
    val id: String,
    val email: String,
    val name: String? = null,
    val image: String? = null,
    @SerialName("emailVerified")
    val emailVerified: Boolean? = null,
)

@Serializable
data class AuthSession(
    val id: String? = null,
    val token: String? = null,
    @SerialName("expiresAt")
    val expiresAt: String? = null,
    @SerialName("createdAt")
    val createdAt: String? = null,
    @SerialName("updatedAt")
    val updatedAt: String? = null,
)

data class CurrentSession(
    val user: AuthUser,
    val session: AuthSession? = null,
)
