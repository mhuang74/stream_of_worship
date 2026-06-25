package org.streamofworship.android.data.offline

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
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

    suspend fun findArtifactByDownloadId(downloadId: Long): OfflineArtifactMetadata?

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
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
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
        ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    ) : this(
        storageFile = context.filesDir.toPath().resolve("offline").resolve("artifacts.json"),
        clockMillis = clockMillis,
        ioDispatcher = ioDispatcher,
    )

    override suspend fun getArtifact(
        renderJobId: String,
        kind: OfflineArtifactKind,
    ): OfflineArtifactMetadata? =
        mutex.withLock {
            readAllLocked()?.firstOrNull { it.renderJobId == renderJobId && it.kind == kind }
        }

    override suspend fun listArtifacts(renderJobId: String): List<OfflineArtifactMetadata> =
        mutex.withLock {
            readAllLocked()?.filter { it.renderJobId == renderJobId }.orEmpty().sortedBy { it.kind.name }
        }

    override suspend fun findArtifactByDownloadId(downloadId: Long): OfflineArtifactMetadata? =
        mutex.withLock {
            readAllLocked()?.firstOrNull { it.downloadId == downloadId }
        }

    override suspend fun markCompletedArtifacts(artifacts: CompletedRenderArtifacts): List<OfflineArtifactMetadata> =
        mutex.withLock {
            val existing = (readAllLocked() ?: emptyList()).associateBy { it.cacheKey }.toMutableMap()
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
            val existing = (readAllLocked() ?: emptyList()).associateBy { it.cacheKey }.toMutableMap()
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
            val existing = (readAllLocked() ?: emptyList()).associateBy { it.cacheKey }.toMutableMap()
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

    /**
     * Reads the cache from disk off the calling dispatcher. Returns null (treated as an empty
     * cache) when the file is missing, blank, or corrupted by a partial write or schema drift;
     * a corrupted cache resets cleanly instead of throwing back to every caller.
     */
    private suspend fun readAllLocked(): List<OfflineArtifactMetadata>? =
        withContext(ioDispatcher) {
            if (!storageFile.exists()) return@withContext emptyList()
            val text =
                runCatching { storageFile.readText() }.getOrElse {
                    Log.w(TAG, "Offline cache unavailable; resetting.", it)
                    return@withContext emptyList()
                }
            if (text.isBlank()) return@withContext emptyList()
            runCatching { json.decodeFromString(serializer, text) }
                .recoverCatching { error ->
                    if (error is SerializationException || error is IllegalArgumentException) {
                        Log.w(TAG, "Offline cache corrupted; resetting.", error)
                        emptyList()
                    } else {
                        throw error
                    }
                }
                .getOrNull()
        }

    /**
     * Writes the cache atomically: serialize to a sibling temp file in the same directory,
     * fsync the temp, then move/rename over the destination so a process kill mid-write leaves
     * either the previous complete file or the new complete file on disk (never a half-write).
     */
    private suspend fun writeAllLocked(items: Collection<OfflineArtifactMetadata>) {
        withContext(ioDispatcher) {
            storageFile.parent?.createDirectories()
            val sorted = items.sortedWith(compareBy<OfflineArtifactMetadata> { it.renderJobId }.thenBy { it.kind.name })
            val payload = json.encodeToString(serializer, sorted)
            val tempFile = storageFile.resolveSibling("${storageFile.fileName}.tmp")
            tempFile.writeText(payload)
            try {
                Files.move(
                    tempFile,
                    storageFile,
                    StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE,
                )
            } catch (_: Throwable) {
                Files.move(tempFile, storageFile, StandardCopyOption.REPLACE_EXISTING)
            }
        }
    }

    private companion object {
        private const val TAG = "OfflineCache"
    }
}
