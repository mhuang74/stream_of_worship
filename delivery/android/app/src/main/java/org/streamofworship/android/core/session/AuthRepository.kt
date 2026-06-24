package org.streamofworship.android.core.session

import kotlinx.serialization.SerializationException
import okhttp3.ResponseBody
import org.streamofworship.android.core.network.ApiError
import org.streamofworship.android.core.network.ApiErrorKind
import org.streamofworship.android.core.network.ApiErrorMapper
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.core.network.AuthApi
import org.streamofworship.android.core.network.BetterAuthEnvelope
import org.streamofworship.android.core.network.CurrentSession
import org.streamofworship.android.core.network.EmailPasswordRequest
import org.streamofworship.android.core.network.RegisterRequest
import org.streamofworship.android.core.network.SowApiClientFactory
import retrofit2.Response
import java.io.IOException

class AuthRepository(
    private val api: AuthApi,
    private val cookieStore: SessionCookieStore,
) {
    suspend fun signIn(
        email: String,
        password: String,
    ): CurrentSession =
        executeSessionRequest {
            api.signIn(EmailPasswordRequest(email = email, password = password))
        }

    suspend fun register(
        name: String,
        email: String,
        password: String,
    ): CurrentSession =
        executeSessionRequest {
            api.register(RegisterRequest(email = email, password = password, name = name))
        }

    suspend fun restoreSession(): CurrentSession? {
        try {
            val response = api.getSession()
            if (response.code() == 401) {
                cookieStore.clear()
                return null
            }
            if (!response.isSuccessful) {
                throw ApiErrorMapper.fromHttpError(response.code(), response.errorBody())
            }
            return parseSession(response.body()) ?: run {
                cookieStore.clear()
                null
            }
        } catch (exception: IOException) {
            if (exception is ApiException) throw exception
            throw ApiErrorMapper.network(exception)
        } catch (exception: SerializationException) {
            throw ApiErrorMapper.malformed(exception)
        } catch (exception: IllegalArgumentException) {
            throw ApiErrorMapper.malformed(exception)
        }
    }

    suspend fun signOut() {
        try {
            val response = api.signOut()
            if (!response.isSuccessful && response.code() != 401) {
                throw ApiErrorMapper.fromHttpError(response.code(), response.errorBody())
            }
        } finally {
            cookieStore.clear()
        }
    }

    fun clearSession() {
        cookieStore.clear()
    }

    private suspend fun executeSessionRequest(block: suspend () -> Response<ResponseBody>): CurrentSession =
        try {
            val response = block()
            if (!response.isSuccessful) {
                if (response.code() == 401) {
                    cookieStore.clear()
                }
                throw ApiErrorMapper.fromHttpError(response.code(), response.errorBody())
            }
            parseSession(response.body())
                ?: throw ApiException(
                    ApiError(
                        message = "The server did not include a signed-in user.",
                        kind = ApiErrorKind.Malformed,
                    ),
                )
        } catch (exception: IOException) {
            if (exception is ApiException) throw exception
            throw ApiErrorMapper.network(exception)
        } catch (exception: SerializationException) {
            throw ApiErrorMapper.malformed(exception)
        } catch (exception: IllegalArgumentException) {
            throw ApiErrorMapper.malformed(exception)
        }

    private fun parseSession(body: ResponseBody?): CurrentSession? {
        val content = body?.string()?.trim().orEmpty()
        if (content.isBlank() || content == "null") return null
        val envelope =
            SowApiClientFactory.json.decodeFromString(BetterAuthEnvelope.serializer(), content)
        val user = envelope.user ?: envelope.data?.user
        val session = envelope.session ?: envelope.data?.session
        val error = envelope.error
        if (error != null) {
            throw ApiException(
                ApiError(
                    statusCode = error.status,
                    message = error.message ?: "Authentication failed.",
                    code = error.code,
                    kind = ApiErrorKind.Validation,
                ),
            )
        }
        return user?.let { CurrentSession(user = it, session = session) }
    }
}
