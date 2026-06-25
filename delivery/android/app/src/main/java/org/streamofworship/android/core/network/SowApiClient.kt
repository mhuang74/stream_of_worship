package org.streamofworship.android.core.network

import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import org.streamofworship.android.core.config.AppConfig
import org.streamofworship.android.core.session.SessionCookieStore
import org.streamofworship.android.core.session.PersistentSessionCookieJar
import retrofit2.Retrofit
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

data class SowApiClient(
    val retrofit: Retrofit,
    val okHttpClient: OkHttpClient,
) {
    inline fun <reified T> create(): T = retrofit.create(T::class.java)
}

object SowApiClientFactory {
    val json: Json =
        Json {
            ignoreUnknownKeys = true
            isLenient = true
            explicitNulls = false
        }

    fun create(
        config: AppConfig,
        cookieStore: SessionCookieStore,
        onUnauthorized: (() -> Unit)? = null,
    ): SowApiClient {
        val cookieJar = PersistentSessionCookieJar(cookieStore)
        val okHttpClient =
            OkHttpClient
                .Builder()
                .cookieJar(cookieJar)
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .callTimeout(45, TimeUnit.SECONDS)
                .addInterceptor { chain ->
                    val response = chain.proceed(chain.request())
                    if (response.code == 401) {
                        cookieStore.clear()
                        // Surface the session invalidation so the auth gate can transition
                        // Authenticated -> Unauthenticated and return the user to sign-in.
                        onUnauthorized?.invoke()
                    }
                    response
                }.build()
        val retrofit =
            Retrofit
                .Builder()
                .baseUrl("${config.normalizedApiBaseUrl}/")
                .client(okHttpClient)
                .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
                .build()
        return SowApiClient(retrofit = retrofit, okHttpClient = okHttpClient)
    }
}
