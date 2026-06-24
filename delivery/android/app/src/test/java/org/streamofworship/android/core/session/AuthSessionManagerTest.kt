package org.streamofworship.android.core.session

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.streamofworship.android.core.network.ApiErrorKind
import org.streamofworship.android.core.network.AuthApi
import org.streamofworship.android.core.network.EmailPasswordRequest
import org.streamofworship.android.core.network.RegisterRequest
import retrofit2.Response

class AuthSessionManagerTest {
    @Test
    fun `restore session publishes authenticated state`() =
        runTest {
            val dispatcher = StandardTestDispatcher(testScheduler)
            val manager =
                AuthSessionManager(
                    repository =
                        AuthRepository(
                            api = FakeAuthApi(sessionBody = successSessionBody()),
                            cookieStore = InMemorySessionCookieStore(),
                        ),
                    scope = CoroutineScope(dispatcher),
                )

            manager.restoreSession()
            advanceUntilIdle()

            val state = manager.authState.value
            assertTrue(state is AuthState.Authenticated)
            assertEquals("user@example.com", (state as AuthState.Authenticated).session.user.email)
        }

    @Test
    fun `sign in failure publishes typed error state`() =
        runTest {
            val dispatcher = StandardTestDispatcher(testScheduler)
            val manager =
                AuthSessionManager(
                    repository =
                        AuthRepository(
                            api = FakeAuthApi(signInResponse = Response.error(400, errorBody())),
                            cookieStore = InMemorySessionCookieStore(),
                        ),
                    scope = CoroutineScope(dispatcher),
                )

            manager.signIn(email = "user@example.com", password = "wrongpass")
            advanceUntilIdle()

            val state = manager.authState.value
            assertTrue(state is AuthState.Error)
            assertEquals(ApiErrorKind.Validation, (state as AuthState.Error).error.kind)
        }

    private class FakeAuthApi(
        private val signInResponse: Response<ResponseBody> = Response.success(successSessionBody()),
        private val sessionBody: ResponseBody? = null,
    ) : AuthApi {
        override suspend fun signIn(request: EmailPasswordRequest): Response<ResponseBody> = signInResponse

        override suspend fun register(request: RegisterRequest): Response<ResponseBody> =
            Response.success(successSessionBody())

        override suspend fun signOut(): Response<ResponseBody> = Response.success("{}".jsonBody())

        override suspend fun getSession(): Response<ResponseBody> =
            Response.success(sessionBody ?: successSessionBody())
    }

    private companion object {
        fun successSessionBody(): ResponseBody =
            """
            {"user":{"id":"42","email":"user@example.com","name":"User"},"session":{"id":"s1"}}
            """.trimIndent().jsonBody()

        fun errorBody(): ResponseBody = """{"message":"Invalid email or password"}""".jsonBody()

        fun String.jsonBody(): ResponseBody = toResponseBody("application/json".toMediaType())
    }
}
