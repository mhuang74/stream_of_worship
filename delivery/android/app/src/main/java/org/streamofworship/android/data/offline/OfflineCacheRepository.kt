package org.streamofworship.android.data.offline

import android.content.Context
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.nio.file.Path
import kotlin.io.path.createDirectories
import kotlin.io.path.exists
import kotlin.io.path.readText
import kotlin.io.path.writeText

interface OfflineCacheRepository {
    suspend fun getArtifact(
        renderJobId: String,
        kind: OfflineArtifactKind,
    ): OfflineArtifactMetadata?

    suspend fun listArtifacts(renderJobId: String): List<OfflineArtifactMetadata>

    suspend fun markCompletedArtifacts(artifacts: CompletedRenderArtifacts): List<OfflineArtifactMetadata>

    suspend fun upsert(metadata: OfflineArtifactMetadata): OfflineArtifactMetadata

    suspend fun markQueued(
        renderJobId: String,
        kind: OfflineArtifactKind,
        remoteUrl: String,
        signedUrlExpiresAt: String?,
        downloadId: Long?,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata

    suspend fun markDownloading(
        renderJobId: String,
        kind: OfflineArtifactKind,
        bytesDownloaded: Long,
        totalBytes: Long?,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata

    suspend fun markCached(
        renderJobId: String,
        kind: OfflineArtifactKind,
        localUri: String,
        bytesDownloaded: Long,
        totalBytes: Long?,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata

    suspend fun markFailed(
        renderJobId: String,
        kind: OfflineArtifactKind,
        message: String,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata
}

class FileOfflineCacheRepository(
    private val storageFile: Path,
    private val clockMillis: () -> Long = { System.currentTimeMillis() },
) : OfflineCacheRepository {
    private val mutex = Mutex()
    private val json =
        Json {
            ignoreUnknownKeys = true
            prettyPrint = true
        }
    private val serializer = ListSerializer(OfflineArtifactMetadata.serializer())

    constructor(
        context: Context,
        clockMillis: () -> Long = { System.currentTimeMillis() },
    ) : this(
        storageFile = context.filesDir.toPath().resolve("offline").resolve("artifacts.json"),
        clockMillis = clockMillis,
    )

    override suspend fun getArtifact(
        renderJobId: String,
        kind: OfflineArtifactKind,
    ): OfflineArtifactMetadata? =
        mutex.withLock {
            readAllLocked().firstOrNull { it.renderJobId == renderJobId && it.kind == kind }
        }

    override suspend fun listArtifacts(renderJobId: String): List<OfflineArtifactMetadata> =
        mutex.withLock {
            readAllLocked().filter { it.renderJobId == renderJobId }.sortedBy { it.kind.name }
        }

    override suspend fun markCompletedArtifacts(artifacts: CompletedRenderArtifacts): List<OfflineArtifactMetadata> =
        mutex.withLock {
            val existing = readAllLocked().associateBy { it.cacheKey }.toMutableMap()
            val now = clockMillis()
            val availableKinds =
                buildList {
                    if (artifacts.audioAvailable) add(OfflineArtifactKind.Audio)
                    if (artifacts.videoAvailable) add(OfflineArtifactKind.Video)
                    if (artifacts.chaptersAvailable) add(OfflineArtifactKind.Chapters)
                }
            val updated =
                availableKinds.map { kind ->
                    val current = existing[OfflineArtifactMetadata.cacheKey(artifacts.renderJobId, kind)]
                    if (current?.isPlayableOffline == true) {
                        current.copy(updatedAtEpochMillis = now)
                    } else {
                        OfflineArtifactMetadata(
                            renderJobId = artifacts.renderJobId,
                            kind = kind,
                            status = OfflineArtifactStatus.Available,
                            updatedAtEpochMillis = now,
                        )
                    }.also { existing[it.cacheKey] = it }
                }
            writeAllLocked(existing.values)
            updated
        }

    override suspend fun upsert(metadata: OfflineArtifactMetadata): OfflineArtifactMetadata =
        mutex.withLock {
            val existing = readAllLocked().associateBy { it.cacheKey }.toMutableMap()
            existing[metadata.cacheKey] = metadata
            writeAllLocked(existing.values)
            metadata
        }

    override suspend fun markQueued(
        renderJobId: String,
        kind: OfflineArtifactKind,
        remoteUrl: String,
        signedUrlExpiresAt: String?,
        downloadId: Long?,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata =
        transition(renderJobId, kind) {
            it.copy(
                status = OfflineArtifactStatus.Queued,
                remoteUrl = remoteUrl,
                signedUrlExpiresAt = signedUrlExpiresAt,
                downloadId = downloadId,
                failureMessage = null,
                updatedAtEpochMillis = nowEpochMillis,
            )
        }

    override suspend fun markDownloading(
        renderJobId: String,
        kind: OfflineArtifactKind,
        bytesDownloaded: Long,
        totalBytes: Long?,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata =
        transition(renderJobId, kind) {
            it.copy(
                status = OfflineArtifactStatus.Downloading,
                bytesDownloaded = bytesDownloaded.coerceAtLeast(0L),
                totalBytes = totalBytes,
                failureMessage = null,
                updatedAtEpochMillis = nowEpochMillis,
            )
        }

    override suspend fun markCached(
        renderJobId: String,
        kind: OfflineArtifactKind,
        localUri: String,
        bytesDownloaded: Long,
        totalBytes: Long?,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata =
        transition(renderJobId, kind) {
            it.copy(
                status = OfflineArtifactStatus.Cached,
                localUri = localUri,
                bytesDownloaded = bytesDownloaded.coerceAtLeast(0L),
                totalBytes = totalBytes,
                failureMessage = null,
                updatedAtEpochMillis = nowEpochMillis,
            )
        }

    override suspend fun markFailed(
        renderJobId: String,
        kind: OfflineArtifactKind,
        message: String,
        nowEpochMillis: Long,
    ): OfflineArtifactMetadata =
        transition(renderJobId, kind) {
            it.copy(
                status = OfflineArtifactStatus.Failed,
                failureMessage = message,
                updatedAtEpochMillis = nowEpochMillis,
            )
        }

    private suspend fun transition(
        renderJobId: String,
        kind: OfflineArtifactKind,
        transform: (OfflineArtifactMetadata) -> OfflineArtifactMetadata,
    ): OfflineArtifactMetadata =
        mutex.withLock {
            val existing = readAllLocked().associateBy { it.cacheKey }.toMutableMap()
            val key = OfflineArtifactMetadata.cacheKey(renderJobId, kind)
            val current =
                existing[key]
                    ?: OfflineArtifactMetadata(
                        renderJobId = renderJobId,
                        kind = kind,
                        status = OfflineArtifactStatus.Available,
                        updatedAtEpochMillis = clockMillis(),
                    )
            val updated = transform(current)
            existing[key] = updated
            writeAllLocked(existing.values)
            updated
        }

    private fun readAllLocked(): List<OfflineArtifactMetadata> {
        if (!storageFile.exists()) return emptyList()
        val text = storageFile.readText().takeIf { it.isNotBlank() } ?: return emptyList()
        return json.decodeFromString(serializer, text)
    }

    private fun writeAllLocked(items: Collection<OfflineArtifactMetadata>) {
        storageFile.parent?.createDirectories()
        val sorted = items.sortedWith(compareBy<OfflineArtifactMetadata> { it.renderJobId }.thenBy { it.kind.name })
        storageFile.writeText(json.encodeToString(serializer, sorted))
    }
}
