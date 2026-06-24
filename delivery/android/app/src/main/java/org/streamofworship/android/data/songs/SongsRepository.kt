package org.streamofworship.android.data.songs

import org.streamofworship.android.core.model.SongsPage
import org.streamofworship.android.data.songsets.execute

interface SongsRepository {
    suspend fun listSongs(
        limit: Int = 50,
        offset: Int = 0,
        albumName: String? = null,
    ): SongsPage

    suspend fun searchSongs(
        query: String,
        limit: Int = 50,
        offset: Int = 0,
    ): SongsPage

    suspend fun semanticSearch(
        query: String,
        limit: Int = 20,
    ): SongsPage
}

class HttpSongsRepository(
    private val api: SongsApi,
) : SongsRepository {
    override suspend fun listSongs(
        limit: Int,
        offset: Int,
        albumName: String?,
    ): SongsPage =
        execute {
            api.listSongs(
                limit = limit,
                offset = offset,
                visibilityStatus = "published",
                albumName = albumName,
            )
        }.publishedOnly()

    override suspend fun searchSongs(
        query: String,
        limit: Int,
        offset: Int,
    ): SongsPage =
        execute {
            api.searchSongs(
                query = query,
                limit = limit,
                offset = offset,
                visibilityStatus = "published",
            )
        }.publishedOnly()

    override suspend fun semanticSearch(
        query: String,
        limit: Int,
    ): SongsPage =
        execute {
            api.semanticSearch(SemanticSearchRequest(query = query, limit = limit))
        }.publishedOnly()
}

private fun SongsPage.publishedOnly(): SongsPage =
    copy(
        songs =
            songs
                .map { song -> song.copy(recordings = song.publishedRecordings) }
                .filter { it.recordings.isNotEmpty() },
    )
