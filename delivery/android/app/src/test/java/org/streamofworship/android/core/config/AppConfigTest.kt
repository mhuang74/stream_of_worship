package org.streamofworship.android.core.config

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class AppConfigTest {
    @Test
    fun `normalizes trailing slash from API base URL`() {
        val config =
            AppConfig(
                apiBaseUrl = "http://10.0.2.2:8080/",
                buildVariant = BuildVariant.Debug,
            )

        assertEquals("http://10.0.2.2:8080", config.normalizedApiBaseUrl)
    }

    @Test
    fun `rejects blank API base URL`() {
        assertThrows(IllegalArgumentException::class.java) {
            AppConfig(apiBaseUrl = " ", buildVariant = BuildVariant.Debug)
        }
    }

    @Test
    fun `rejects API base URL without HTTP scheme`() {
        assertThrows(IllegalArgumentException::class.java) {
            AppConfig(apiBaseUrl = "streamofworship.local", buildVariant = BuildVariant.Release)
        }
    }

    @Test
    fun `parses supported build variants`() {
        assertEquals(BuildVariant.Debug, BuildVariant.parse("debug"))
        assertEquals(BuildVariant.Staging, BuildVariant.parse("STAGING"))
        assertEquals(BuildVariant.Release, BuildVariant.parse("release"))
        assertEquals(BuildVariant.Unknown, BuildVariant.parse("preview"))
    }
}
