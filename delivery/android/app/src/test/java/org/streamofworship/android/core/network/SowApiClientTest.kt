package org.streamofworship.android.core.network

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.config.BuildVariant
import org.streamofworship.android.core.session.AuthRepository
import org.streamofworship.android.core.session.InMemorySessionCookieStore

class SowApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var cookieStore: InMemorySessionCookieStore
    private lateinit var repository: AuthRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        cookieStore = InMemorySessionCookieStore()
        val client =
            SowApiClientFactory.create(
                config =
                    AppConfig(
                        apiBaseUrl = server.url("/").toString(),
                        buildVariant = BuildVariant.Debug,
                    ),
                cookieStore = cookieStore,
            )
        repository =
            AuthRepository(
                api = client.create<AuthApi>(),
                cookieStore = cookieStore,
            )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `persists auth cookies and sends them on session lookup`() =
        runTest {
            server.enqueue(
                jsonResponse(
                    body =
                        """
                        {"user":{"id":"42","email":"user@example.com","name":"User"},"session":{"id":"s1"}}
                        """.trimIndent(),
                ).addHeader("Set-Cookie", "better-auth.session_token=abc123; Path=/; HttpOnly"),
            )
            server.enqueue(
                jsonResponse(
                    body =
                        """
                        {"user":{"id":"42","email":"user@example.com","name":"User"},"session":{"id":"s1"}}
                        """.trimIndent(),
                ),
            )

            repository.signIn(email = "user@example.com", password = "password123")
            repository.restoreSession()

            assertEquals("/api/auth/sign-in/email", server.takeRequest().path)
            val sessionRequest = server.takeRequest()
            assertEquals("/api/auth/get-session", sessionRequest.path)
            assertTrue(sessionRequest.getHeader("Cookie")!!.contains("better-auth.session_token=abc123"))
            assertTrue(cookieStore.load().isNotEmpty())
        }

    @Test
    fun `clears persisted cookies after unauthorized response`() =
        runTest {
            server.enqueue(
                jsonResponse(
                    body =
                        """
                        {"user":{"id":"42","email":"user@example.com"},"session":{"id":"s1"}}
                        """.trimIndent(),
                ).addHeader("Set-Cookie", "better-auth.session_token=abc123; Path=/; HttpOnly"),
            )
            server.enqueue(jsonResponse("""{"message":"Unauthorized"}""", code = 401))

            repository.signIn(email = "user@example.com", password = "password123")
            val restored = repository.restoreSession()

            assertEquals(null, restored)
            assertTrue(cookieStore.load().isEmpty())
        }

    @Test
    fun `maps http error payloads into typed api errors`() =
        runTest {
            server.enqueue(jsonResponse("""{"message":"Invalid email or password","code":"BAD"}""", 400))

            val error =
                runCatching {
                    repository.signIn(email = "user@example.com", password = "wrongpass")
                }.exceptionOrNull() as ApiException

            assertEquals(ApiErrorKind.Validation, error.error.kind)
            assertEquals(400, error.error.statusCode)
            assertEquals("Invalid email or password", error.error.message)
            assertEquals("BAD", error.error.code)
        }

    @Test
    fun `maps malformed successful response into typed api error`() =
        runTest {
            server.enqueue(jsonResponse("""{"user":"""))

            val error =
                runCatching {
                    repository.signIn(email = "user@example.com", password = "password123")
                }.exceptionOrNull() as ApiException

            assertEquals(ApiErrorKind.Malformed, error.error.kind)
        }

    private fun jsonResponse(
        body: String,
        code: Int = 200,
    ): MockResponse =
        MockResponse()
            .setResponseCode(code)
            .setHeader("Content-Type", "application/json")
            .setBody(body)
}
