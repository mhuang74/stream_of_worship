package org.streamofworship.android.core.network

import kotlinx.serialization.SerializationException
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.ResponseBody
import java.io.IOException

data class ApiError(
    val statusCode: Int? = null,
    val message: String,
    val code: String? = null,
    val kind: ApiErrorKind,
)

enum class ApiErrorKind {
    Unauthorized,
    Server,
    Validation,
    Network,
    Malformed,
    Unknown,
}

class ApiException(
    val error: ApiError,
    cause: Throwable? = null,
) : IOException(error.message, cause)

@Serializable
private data class ErrorPayload(
    val message: String? = null,
    val error: String? = null,
    val code: String? = null,
)

object ApiErrorMapper {
    private val json =
        Json {
            ignoreUnknownKeys = true
            isLenient = true
        }

    fun fromHttpError(
        statusCode: Int,
        errorBody: ResponseBody?,
    ): ApiException {
        val payload = parsePayload(errorBody)
        val message =
            payload?.message
                ?: payload?.error
                ?: defaultMessage(statusCode)
        val kind =
            when (statusCode) {
                401 -> ApiErrorKind.Unauthorized
                400, 422 -> ApiErrorKind.Validation
                in 500..599 -> ApiErrorKind.Server
                else -> ApiErrorKind.Unknown
            }
        return ApiException(
            ApiError(
                statusCode = statusCode,
                message = message,
                code = payload?.code,
                kind = kind,
            ),
        )
    }

    fun malformed(cause: Throwable): ApiException =
        ApiException(
            ApiError(
                message = "The server returned an unreadable response.",
                kind = ApiErrorKind.Malformed,
            ),
            cause,
        )

    fun network(cause: IOException): ApiException =
        ApiException(
            ApiError(
                message = "Unable to reach Stream of Worship.",
                kind = ApiErrorKind.Network,
            ),
            cause,
        )

    private fun parsePayload(errorBody: ResponseBody?): ErrorPayload? {
        val body = errorBody?.string()?.takeIf { it.isNotBlank() } ?: return null
        return try {
            json.decodeFromString(ErrorPayload.serializer(), body)
        } catch (_: SerializationException) {
            null
        } catch (_: IllegalArgumentException) {
            null
        }
    }

    private fun defaultMessage(statusCode: Int): String =
        when (statusCode) {
            401 -> "Your session has expired. Please sign in again."
            in 500..599 -> "Stream of Worship is temporarily unavailable."
            else -> "Request failed with status $statusCode."
        }
}
