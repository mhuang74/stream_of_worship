package org.streamofworship.android.core.session

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
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

@OptIn(ExperimentalCoroutinesApi::class)
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

    @Test
    fun `register success publishes authenticated state`() =
        runTest {
            val dispatcher = StandardTestDispatcher(testScheduler)
            val manager =
                AuthSessionManager(
                    repository =
                        AuthRepository(
                            api = FakeAuthApi(registerResponse = Response.success(successSessionBody(email = "new@example.com"))),
                            cookieStore = InMemorySessionCookieStore(),
                        ),
                    scope = CoroutineScope(dispatcher),
                )

            manager.register(name = "New User", email = "new@example.com", password = "password123")
            advanceUntilIdle()

            val state = manager.authState.value
            assertTrue(state is AuthState.Authenticated)
            assertEquals("new@example.com", (state as AuthState.Authenticated).session.user.email)
        }

    @Test
    fun `register validation failure publishes typed error state`() =
        runTest {
            val dispatcher = StandardTestDispatcher(testScheduler)
            val manager =
                AuthSessionManager(
                    repository =
                        AuthRepository(
                            api = FakeAuthApi(registerResponse = Response.error(400, errorBody("Email already exists"))),
                            cookieStore = InMemorySessionCookieStore(),
                        ),
                    scope = CoroutineScope(dispatcher),
                )

            manager.register(name = "User", email = "taken@example.com", password = "password123")
            advanceUntilIdle()

            val state = manager.authState.value
            assertTrue(state is AuthState.Error)
            assertEquals(ApiErrorKind.Validation, (state as AuthState.Error).error.kind)
            assertEquals("Email already exists", state.error.message)
        }

    @Test
    fun `sign out clears cookies and publishes unauthenticated on success or server failure`() =
        runTest {
            val dispatcher = StandardTestDispatcher(testScheduler)
            val cookieStore = InMemorySessionCookieStore()
            cookieStore.save(listOf(StoredCookie("sid", "abc", "example.com", "/", Long.MAX_VALUE, false, true, true)))
            val manager =
                AuthSessionManager(
                    repository =
                        AuthRepository(
                            api = FakeAuthApi(signOutResponse = Response.error(500, errorBody("down"))),
                            cookieStore = cookieStore,
                        ),
                    scope = CoroutineScope(dispatcher),
                )

            manager.signOut()
            advanceUntilIdle()

            assertTrue(manager.authState.value is AuthState.Unauthenticated)
            assertTrue(cookieStore.load().isEmpty())
        }

    @Test
    fun `on session expired drops an authenticated state back to unauthenticated`() =
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
            assertTrue(manager.authState.value is AuthState.Authenticated)

            manager.onSessionExpired()

            assertTrue(manager.authState.value is AuthState.Unauthenticated)
        }

    @Test
    fun `on session expired does not clobber restoring or error states`() =
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
            assertTrue(manager.authState.value is AuthState.Error)

            manager.onSessionExpired()

            assertTrue("error state should be preserved", manager.authState.value is AuthState.Error)
        }

    private class FakeAuthApi(
        private val signInResponse: Response<ResponseBody> = Response.success(successSessionBody()),
        private val registerResponse: Response<ResponseBody> = Response.success(successSessionBody()),
        private val signOutResponse: Response<ResponseBody> = Response.success("{}".jsonBody()),
        private val sessionBody: ResponseBody? = null,
    ) : AuthApi {
        override suspend fun signIn(request: EmailPasswordRequest): Response<ResponseBody> = signInResponse

        override suspend fun register(request: RegisterRequest): Response<ResponseBody> =
            registerResponse

        override suspend fun signOut(): Response<ResponseBody> = signOutResponse

        override suspend fun getSession(): Response<ResponseBody> =
            Response.success(sessionBody ?: successSessionBody())
    }

    private companion object {
        fun successSessionBody(email: String = "user@example.com"): ResponseBody =
            """
            {"user":{"id":"42","email":"$email","name":"User"},"session":{"id":"s1"}}
            """.trimIndent().jsonBody()

        fun errorBody(message: String = "Invalid email or password"): ResponseBody = """{"message":"$message"}""".jsonBody()

        fun String.jsonBody(): ResponseBody = toResponseBody("application/json".toMediaType())
    }
}
