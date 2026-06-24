package org.streamofworship.android.data.playback

import org.streamofworship.android.data.songsets.execute

interface PlaybackRepository {
    suspend fun renderedAudioUrl(
        renderJobId: String,
        contentDisposition: String? = null,
    ): SignedUrlResponse

    suspend fun renderedVideoUrl(
        renderJobId: String,
        contentDisposition: String? = null,
    ): SignedUrlResponse

    suspend fun renderedChaptersUrl(renderJobId: String): SignedUrlResponse

    suspend fun sourceAudioUrl(hashPrefix: String): SignedUrlResponse

    suspend fun sourceLrcUrl(hashPrefix: String): SignedUrlResponse

    suspend fun chapters(renderJobId: String): PlaybackManifest
}

class HttpPlaybackRepository(
    private val api: PlaybackApi,
) : PlaybackRepository {
    override suspend fun renderedAudioUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse =
        signedUrl(renderJobId = renderJobId, fileType = SignedUrlFileType.RenderedAudio, contentDisposition = contentDisposition)

    override suspend fun renderedVideoUrl(
        renderJobId: String,
        contentDisposition: String?,
    ): SignedUrlResponse =
        signedUrl(renderJobId = renderJobId, fileType = SignedUrlFileType.RenderedVideo, contentDisposition = contentDisposition)

    override suspend fun renderedChaptersUrl(renderJobId: String): SignedUrlResponse =
        signedUrl(renderJobId = renderJobId, fileType = SignedUrlFileType.RenderedChapters)

    override suspend fun sourceAudioUrl(hashPrefix: String): SignedUrlResponse =
        signedUrl(hashPrefix = hashPrefix, fileType = SignedUrlFileType.SourceAudio)

    override suspend fun sourceLrcUrl(hashPrefix: String): SignedUrlResponse =
        signedUrl(hashPrefix = hashPrefix, fileType = SignedUrlFileType.SourceLrc)

    override suspend fun chapters(renderJobId: String): PlaybackManifest =
        execute { api.getChapters(renderJobId) }.normalized()

    private suspend fun signedUrl(
        renderJobId: String? = null,
        hashPrefix: String? = null,
        fileType: SignedUrlFileType,
        contentDisposition: String? = null,
    ): SignedUrlResponse =
        execute {
            api.getSignedUrl(
                renderJobId = renderJobId,
                hashPrefix = hashPrefix,
                fileType = fileType.value,
                expiresInSeconds = 3600,
                contentDisposition = contentDisposition,
            )
        }
}
