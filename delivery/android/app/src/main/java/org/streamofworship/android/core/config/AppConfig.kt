package org.streamofworship.android.core.config

import org.streamofworship.android.BuildConfig

data class AppConfig(
    val apiBaseUrl: String,
    val buildVariant: BuildVariant,
) {
    init {
        require(apiBaseUrl.isNotBlank()) { "API base URL must not be blank." }
        require(apiBaseUrl.startsWith("http://") || apiBaseUrl.startsWith("https://")) {
            "API base URL must start with http:// or https://."
        }
    }

    val normalizedApiBaseUrl: String = apiBaseUrl.trimEnd('/')

    companion object {
        fun fromBuildConfig(): AppConfig =
            AppConfig(
                apiBaseUrl = BuildConfig.API_BASE_URL,
                buildVariant = BuildVariant.parse(BuildConfig.BUILD_VARIANT),
            )
    }
}

enum class BuildVariant {
    Debug,
    Staging,
    Release,
    Unknown,
    ;

    companion object {
        fun parse(value: String): BuildVariant =
            when (value.lowercase()) {
                "debug" -> Debug
                "staging" -> Staging
                "release" -> Release
                else -> Unknown
            }
    }
}
