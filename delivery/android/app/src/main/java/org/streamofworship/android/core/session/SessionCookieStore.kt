package org.streamofworship.android.core.session

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import okhttp3.Cookie
import okhttp3.HttpUrl

interface SessionCookieStore {
    fun load(): List<StoredCookie>

    fun save(cookies: List<StoredCookie>)

    fun clear()
}

@Serializable
data class StoredCookie(
    val name: String,
    val value: String,
    val domain: String,
    val path: String,
    val expiresAt: Long,
    val secure: Boolean,
    val httpOnly: Boolean,
    val hostOnly: Boolean,
) {
    fun toCookie(): Cookie =
        Cookie
            .Builder()
            .name(name)
            .value(value)
            .apply {
                if (hostOnly) {
                    hostOnlyDomain(domain)
                } else {
                    domain(domain)
                }
                path(path)
                expiresAt(expiresAt)
                if (secure) secure()
                if (httpOnly) httpOnly()
            }.build()

    companion object {
        fun fromCookie(cookie: Cookie): StoredCookie =
            StoredCookie(
                name = cookie.name,
                value = cookie.value,
                domain = cookie.domain,
                path = cookie.path,
                expiresAt = cookie.expiresAt,
                secure = cookie.secure,
                httpOnly = cookie.httpOnly,
                hostOnly = cookie.hostOnly,
            )
    }
}

class AndroidSecureSessionCookieStore(
    context: Context,
    private val json: Json = Json,
) : SessionCookieStore {
    private val appContext = context.applicationContext
    private val preferences: SharedPreferences =
        createEncryptedPreferences(appContext).getOrElse {
            appContext.deleteSharedPreferences(PREFERENCES_NAME)
            createEncryptedPreferences(appContext).getOrElse {
                appContext.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)
            }
        }

    override fun load(): List<StoredCookie> {
        val encoded = preferences.getString(COOKIES_KEY, null) ?: return emptyList()
        return runCatching {
            json.decodeFromString(ListSerializer(StoredCookie.serializer()), encoded)
        }.getOrDefault(emptyList())
    }

    override fun save(cookies: List<StoredCookie>) {
        preferences
            .edit()
            .putString(COOKIES_KEY, json.encodeToString(ListSerializer(StoredCookie.serializer()), cookies))
            .apply()
    }

    override fun clear() {
        preferences.edit().remove(COOKIES_KEY).apply()
    }

    private companion object {
        const val PREFERENCES_NAME = "sow_secure_session"
        const val COOKIES_KEY = "cookies"

        fun createEncryptedPreferences(context: Context): Result<SharedPreferences> =
            runCatching {
                EncryptedSharedPreferences.create(
                    context,
                    PREFERENCES_NAME,
                    MasterKey
                        .Builder(context)
                        .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                        .build(),
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
                )
            }
    }
}

class InMemorySessionCookieStore : SessionCookieStore {
    private var cookies: List<StoredCookie> = emptyList()

    override fun load(): List<StoredCookie> = cookies

    override fun save(cookies: List<StoredCookie>) {
        this.cookies = cookies
    }

    override fun clear() {
        cookies = emptyList()
    }
}

class PersistentSessionCookieJar(
    private val store: SessionCookieStore,
) : okhttp3.CookieJar {
    override fun saveFromResponse(
        url: HttpUrl,
        cookies: List<Cookie>,
    ) {
        val now = System.currentTimeMillis()
        val current =
            store
                .load()
                .map { it.toCookie() }
                .filter { it.expiresAt > now }
                .filterNot { existing ->
                    cookies.any { incoming ->
                        incoming.name == existing.name &&
                            incoming.domain == existing.domain &&
                            incoming.path == existing.path
                    }
                }
        val merged =
            (current + cookies)
                .filter { it.expiresAt > now }
                .map(StoredCookie::fromCookie)
        store.save(merged)
    }

    override fun loadForRequest(url: HttpUrl): List<Cookie> {
        val now = System.currentTimeMillis()
        val all = store.load().map { it.toCookie() }
        val valid = all.filter { it.expiresAt > now }
        if (valid.size != all.size) {
            store.save(valid.map(StoredCookie::fromCookie))
        }
        return valid.filter { it.matches(url) }
    }
}
