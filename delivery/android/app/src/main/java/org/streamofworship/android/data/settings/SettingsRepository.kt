package org.streamofworship.android.data.settings

import org.streamofworship.android.data.songsets.execute

interface SettingsRepository {
    suspend fun getSettings(): UserSettings

    suspend fun saveSettings(settings: UserSettings): UserSettings
}

class HttpSettingsRepository(
    private val api: SettingsApi,
) : SettingsRepository {
    override suspend fun getSettings(): UserSettings = execute { api.getSettings() }.settings

    override suspend fun saveSettings(settings: UserSettings): UserSettings =
        execute { api.saveSettings(settings.toUpdateRequest()) }.settings
}
