package org.streamofworship.android.feature.share

import org.streamofworship.android.data.songsets.execute

interface ShareRepository {
    suspend fun createRenderShare(
        renderJobId: String,
        allowDownload: Boolean,
    ): ShareToken

    suspend fun createSongsetShare(
        songsetId: String,
        allowDownload: Boolean,
    ): ShareToken

    suspend fun listShares(
        songsetId: String? = null,
        renderJobId: String? = null,
    ): List<ShareToken>
}

class HttpShareRepository(
    private val api: ShareApi,
) : ShareRepository {
    override suspend fun createRenderShare(
        renderJobId: String,
        allowDownload: Boolean,
    ): ShareToken =
        execute { api.createShare(CreateShareRequest(renderJobId = renderJobId, allowDownload = allowDownload)) }

    override suspend fun createSongsetShare(
        songsetId: String,
        allowDownload: Boolean,
    ): ShareToken =
        execute { api.createShare(CreateShareRequest(songsetId = songsetId, allowDownload = allowDownload)) }

    override suspend fun listShares(
        songsetId: String?,
        renderJobId: String?,
    ): List<ShareToken> = execute { api.listShares(songsetId = songsetId, renderJobId = renderJobId) }.shares
}
