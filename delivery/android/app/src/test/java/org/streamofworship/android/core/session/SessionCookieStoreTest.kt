package org.streamofworship.android.core.session

import okhttp3.Cookie
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionCookieStoreTest {
    @Test
    fun `cookie jar stores matching cookies and excludes other domains`() {
        val store = InMemorySessionCookieStore()
        val jar = PersistentSessionCookieJar(store)
        val url = "https://app.example.test/api/auth/sign-in/email".toHttpUrl()
        val cookie =
            Cookie
                .Builder()
                .name("better-auth.session_token")
                .value("abc123")
                .hostOnlyDomain("app.example.test")
                .path("/")
                .expiresAt(System.currentTimeMillis() + 60_000)
                .httpOnly()
                .secure()
                .build()

        jar.saveFromResponse(url, listOf(cookie))

        assertEquals(1, jar.loadForRequest("https://app.example.test/api/songsets".toHttpUrl()).size)
        assertTrue(jar.loadForRequest("https://other.example.test/api/songsets".toHttpUrl()).isEmpty())
    }

    @Test
    fun `cookie jar removes expired cookies`() {
        val store = InMemorySessionCookieStore()
        val jar = PersistentSessionCookieJar(store)
        val expired =
            Cookie
                .Builder()
                .name("better-auth.session_token")
                .value("expired")
                .hostOnlyDomain("app.example.test")
                .path("/")
                .expiresAt(System.currentTimeMillis() - 1_000)
                .build()

        jar.saveFromResponse("https://app.example.test/".toHttpUrl(), listOf(expired))

        assertTrue(jar.loadForRequest("https://app.example.test/api/songsets".toHttpUrl()).isEmpty())
        assertTrue(store.load().isEmpty())
    }
}
