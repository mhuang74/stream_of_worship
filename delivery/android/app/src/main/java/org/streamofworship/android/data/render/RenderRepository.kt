package org.streamofworship.android.data.render

import kotlinx.serialization.SerializationException
import kotlinx.serialization.Serializable
import org.streamofworship.android.core.network.ApiErrorMapper
import org.streamofworship.android.core.network.ApiException
import org.streamofworship.android.core.network.SowApiClientFactory
import org.streamofworship.android.data.songsets.execute
import retrofit2.Response
import java.io.IOException

interface RenderRepository {
    suspend fun createRenderJob(
        songsetId: String,
        config: RenderFormConfig,
    ): RenderJob

    suspend fun getRenderJob(id: String): RenderJob

    suspend fun cancelRenderJob(id: String): RenderJob

    suspend fun getArtifactSizes(id: String): ArtifactSizes
}

class HttpRenderRepository(
    private val api: RenderApi,
) : RenderRepository {
    override suspend fun createRenderJob(
        songsetId: String,
        config: RenderFormConfig,
    ): RenderJob =
        executeRender {
            api.createRenderJob(
                CreateRenderJobRequest(
                    songsetId = songsetId,
                    template = config.template,
                    resolution = config.resolution,
                    audioEnabled = config.audioEnabled,
                    videoEnabled = config.videoEnabled,
                    fontSizePreset = config.fontSizePreset,
                    fontFamily = config.fontFamily,
                    includeTitleCard = config.includeTitleCard,
                    titleCardDurationSeconds =
                        config.titleCardDurationSeconds.takeIf { config.includeTitleCard },
                    titleCardLines =
                        config.titleCardLines
                            .map { it.trim() }
                            .filter { it.isNotEmpty() }
                            .takeIf { config.includeTitleCard && it.isNotEmpty() },
                ),
            )
        }

    override suspend fun getRenderJob(id: String): RenderJob = execute { api.getRenderJob(id) }

    override suspend fun cancelRenderJob(id: String): RenderJob = execute { api.cancelRenderJob(id) }

    override suspend fun getArtifactSizes(id: String): ArtifactSizes = execute { api.getArtifactSizes(id) }
}

private suspend fun <T : Any> executeRender(block: suspend () -> Response<T>): T =
    try {
        val response = block()
        if (response.code() == 409) {
            throw parseConflict(response)
        }
        if (!response.isSuccessful) {
            throw ApiErrorMapper.fromHttpError(response.code(), response.errorBody())
        }
        response.body()
            ?: throw ApiErrorMapper.malformed(IllegalStateException("Empty response body."))
    } catch (exception: IOException) {
        if (exception is ApiException) throw exception
        throw ApiErrorMapper.network(exception)
    } catch (exception: SerializationException) {
        throw ApiErrorMapper.malformed(exception)
    } catch (exception: IllegalArgumentException) {
        throw ApiErrorMapper.malformed(exception)
    }

private fun parseConflict(response: Response<*>): ActiveRenderConflictException {
    val body = response.errorBody()?.string().orEmpty()
    val payload =
        runCatching {
            SowApiClientFactory.json.decodeFromString(ConflictPayload.serializer(), body)
        }.getOrNull()
    return ActiveRenderConflictException(
        ActiveRenderConflict(
            jobId = payload?.jobId,
            estimatedTotalSeconds = payload?.estimatedTotalSeconds,
            config = payload?.config,
            message = payload?.error ?: "A render job is already in progress for this songset",
        ),
    )
}

@Serializable
private data class ConflictPayload(
    val error: String? = null,
    val jobId: String? = null,
    val estimatedTotalSeconds: Double? = null,
    val config: ConflictRenderConfig? = null,
)
