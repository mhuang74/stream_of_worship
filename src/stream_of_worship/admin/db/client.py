"""Database client for sow-admin.

Provides SQLite database operations for local storage of song catalog
and recording metadata. Supports libsql/Turso for embedded replica sync.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional, Union

from stream_of_worship.admin.db.models import DatabaseStats, Recording, Song
from stream_of_worship.admin.db.schema import (
    ALL_SCHEMA_STATEMENTS,
    DEFAULT_SYNC_METADATA,
    FOREIGN_KEYS_QUERY,
    INTEGRITY_CHECK_QUERY,
    ROW_COUNT_QUERY,
)

# Optional libsql import for Turso support
try:
    import libsql

    LIBSQL_AVAILABLE = True
except ImportError:
    LIBSQL_AVAILABLE = False
    libsql = None  # type: ignore


class SyncError(Exception):
    """Error during database sync operation."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class DatabaseClient:
    """Client for local SQLite database operations with optional Turso sync.

    This client manages the connection to the local SQLite database and
    provides methods for CRUD operations on songs and recordings.
    When Turso is configured, it uses libsql for embedded replica sync.

    Attributes:
        db_path: Path to the SQLite database file
        turso_url: Turso database URL for sync (optional)
        turso_token: Turso auth token (optional)
        connection: Active database connection
    """

    def __init__(
        self,
        db_path: Path,
        turso_url: Optional[str] = None,
        turso_token: Optional[str] = None,
    ):
        """Initialize the database client.

        Args:
            db_path: Path to the SQLite database file
            turso_url: Turso database URL for sync (optional)
            turso_token: Turso auth token (optional)
        """
        self.db_path = db_path
        self.turso_url = turso_url
        self.turso_token = turso_token
        self._connection: Optional[Union[sqlite3.Connection, "libsql.Connection"]] = None

    @property
    def is_turso_enabled(self) -> bool:
        """Check if Turso sync is enabled.

        Returns:
            True if Turso URL is configured and libsql is available
        """
        return bool(self.turso_url and LIBSQL_AVAILABLE)

    @property
    def connection(self) -> Union[sqlite3.Connection, "libsql.Connection"]:
        """Get or create database connection.

        Returns:
            Active database connection (sqlite3 or libsql)
        """
        if self._connection is None:
            # Ensure directory exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

            if self.is_turso_enabled:
                # Use libsql for Turso embedded replica
                self._connection = libsql.connect(
                    str(self.db_path),
                    sync_url=self.turso_url,
                    auth_token=self.turso_token or "",
                )
            else:
                # Use standard sqlite3
                self._connection = sqlite3.connect(
                    self.db_path,
                    detect_types=sqlite3.PARSE_DECLTYPES,
                )
                self._connection.row_factory = sqlite3.Row

                # Enable foreign keys
                self._connection.execute("PRAGMA foreign_keys = ON")

        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def sync(self) -> None:
        """Sync with Turso cloud database.

        Raises:
            SyncError: If sync fails or Turso is not configured
        """
        if not self.is_turso_enabled:
            raise SyncError("Turso sync is not configured")

        try:
            # Cast to libsql.Connection for type checking
            conn = self.connection
            conn.sync()  # type: ignore

            # Update last sync timestamp
            self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
        except Exception as e:
            raise SyncError(f"Sync failed: {e}", cause=e)

    def update_sync_metadata(self, key: str, value: str) -> None:
        """Update sync metadata value.

        Args:
            key: Metadata key to update
            value: New value
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                """,
                (key, value),
            )

    def __enter__(self) -> "DatabaseClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database transactions.

        Yields:
            SQLite connection with active transaction
        """
        conn = self.connection
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def initialize_schema(self) -> None:
        """Initialize the database schema.

        Creates all tables, indexes, and triggers if they don't exist.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            for statement in ALL_SCHEMA_STATEMENTS:
                cursor.execute(statement)

            # Initialize sync metadata if empty
            for key, value in DEFAULT_SYNC_METADATA.items():
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO sync_metadata (key, value)
                    VALUES (?, ?)
                    """,
                    (key, value),
                )

    def reset_database(self) -> None:
        """Reset the database by dropping all tables.

        WARNING: This is a destructive operation that will delete all data.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()

            for (table_name,) in tables:
                if not table_name.startswith("sqlite_"):
                    cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

        # Re-initialize schema
        self.initialize_schema()

    def get_stats(self) -> DatabaseStats:
        """Get database statistics.

        Returns:
            DatabaseStats with current database state
        """
        cursor = self.connection.cursor()

        # Get row counts
        cursor.execute(ROW_COUNT_QUERY)
        table_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # Run integrity check
        cursor.execute(INTEGRITY_CHECK_QUERY)
        integrity_result = cursor.fetchone()
        integrity_ok = integrity_result[0] == "ok" if integrity_result else False

        # Check foreign keys status
        cursor.execute(FOREIGN_KEYS_QUERY)
        fk_result = cursor.fetchone()
        foreign_keys_enabled = bool(fk_result[0]) if fk_result else False

        # Get sync metadata
        cursor.execute("SELECT key, value FROM sync_metadata")
        sync_meta = {row[0]: row[1] for row in cursor.fetchall()}

        # Ensure local_device_id is set
        local_device_id = sync_meta.get("local_device_id", "")
        if not local_device_id and self.is_turso_enabled:
            local_device_id = str(uuid.uuid4())[:8]
            self.update_sync_metadata("local_device_id", local_device_id)

        return DatabaseStats(
            table_counts=table_counts,
            integrity_ok=integrity_ok,
            foreign_keys_enabled=foreign_keys_enabled,
            last_sync_at=sync_meta.get("last_sync_at") or None,
            sync_version=sync_meta.get("sync_version", "1"),
            local_device_id=local_device_id,
            turso_configured=self.is_turso_enabled,
        )

    # Song operations

    def insert_song(self, song: Song) -> None:
        """Insert a song into the database.

        Args:
            song: Song to insert
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO songs (
                    id, title, title_pinyin, composer, lyricist,
                    album_name, album_series, musical_key, lyrics_raw,
                    lyrics_lines, sections, source_url, table_row_number,
                    scraped_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    song.id,
                    song.title,
                    song.title_pinyin,
                    song.composer,
                    song.lyricist,
                    song.album_name,
                    song.album_series,
                    song.musical_key,
                    song.lyrics_raw,
                    song.lyrics_lines,
                    song.sections,
                    song.source_url,
                    song.table_row_number,
                    song.scraped_at,
                    song.created_at or datetime.now().isoformat(),
                    song.updated_at or datetime.now().isoformat(),
                ),
            )

    def get_song(self, song_id: str) -> Optional[Song]:
        """Get a song by ID.

        Args:
            song_id: The song ID

        Returns:
            Song or None if not found
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
        row = cursor.fetchone()

        if row:
            return Song.from_row(tuple(row))
        return None

    def list_songs(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Song]:
        """List songs with optional filters.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results

        Returns:
            List of songs matching the filters
        """
        cursor = self.connection.cursor()

        query = "SELECT * FROM songs WHERE 1=1"
        params: list = []

        if album:
            query += " AND album_name = ?"
            params.append(album)

        if key:
            query += " AND musical_key = ?"
            params.append(key)

        query += " ORDER BY id"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    def search_songs(self, query: str, field: str = "all", limit: int = 20) -> list[Song]:
        """Search songs by query.

        Args:
            query: Search query string
            field: Field to search (title, lyrics, composer, all)
            limit: Maximum number of results

        Returns:
            List of matching songs
        """
        cursor = self.connection.cursor()

        search_pattern = f"%{query}%"

        if field == "title":
            sql = "SELECT * FROM songs WHERE title LIKE ? OR title_pinyin LIKE ?"
            params = [search_pattern, search_pattern]
        elif field == "lyrics":
            sql = "SELECT * FROM songs WHERE lyrics_raw LIKE ?"
            params = [search_pattern]
        elif field == "composer":
            sql = "SELECT * FROM songs WHERE composer LIKE ? OR lyricist LIKE ?"
            params = [search_pattern, search_pattern]
        else:  # all
            sql = """
                SELECT * FROM songs WHERE
                title LIKE ? OR title_pinyin LIKE ? OR
                lyrics_raw LIKE ? OR composer LIKE ? OR lyricist LIKE ?
            """
            params = [search_pattern] * 5

        sql += f" ORDER BY id LIMIT {limit}"

        cursor.execute(sql, params)

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    # Recording operations

    def insert_recording(self, recording: Recording) -> None:
        """Insert a recording into the database.

        Args:
            recording: Recording to insert
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO recordings (
                    content_hash, hash_prefix, song_id, original_filename,
                    file_size_bytes, imported_at, r2_audio_url, r2_stems_url,
                    r2_lrc_url, duration_seconds, tempo_bpm, musical_key,
                    musical_mode, key_confidence, loudness_db, beats,
                    downbeats, sections, embeddings_shape, analysis_status,
                    analysis_job_id, lrc_status, lrc_job_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recording.content_hash,
                    recording.hash_prefix,
                    recording.song_id,
                    recording.original_filename,
                    recording.file_size_bytes,
                    recording.imported_at,
                    recording.r2_audio_url,
                    recording.r2_stems_url,
                    recording.r2_lrc_url,
                    recording.duration_seconds,
                    recording.tempo_bpm,
                    recording.musical_key,
                    recording.musical_mode,
                    recording.key_confidence,
                    recording.loudness_db,
                    recording.beats,
                    recording.downbeats,
                    recording.sections,
                    recording.embeddings_shape,
                    recording.analysis_status,
                    recording.analysis_job_id,
                    recording.lrc_status,
                    recording.lrc_job_id,
                    recording.created_at or datetime.now().isoformat(),
                    recording.updated_at or datetime.now().isoformat(),
                ),
            )

    def get_recording_by_hash(self, hash_prefix: str) -> Optional[Recording]:
        """Get a recording by its hash prefix.

        Args:
            hash_prefix: The hash prefix (first 12 chars)

        Returns:
            Recording or None if not found
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM recordings WHERE hash_prefix = ?",
            (hash_prefix,),
        )
        row = cursor.fetchone()

        if row:
            return Recording.from_row(tuple(row))
        return None

    def get_recording_by_song_id(self, song_id: str) -> Optional[Recording]:
        """Get a recording by its associated song ID.

        Args:
            song_id: The song ID

        Returns:
            Recording or None if not found
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM recordings WHERE song_id = ?",
            (song_id,),
        )
        row = cursor.fetchone()

        if row:
            return Recording.from_row(tuple(row))
        return None

    def list_recordings(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Recording]:
        """List recordings with optional filters.

        Args:
            status: Filter by analysis status
            limit: Maximum number of results

        Returns:
            List of recordings matching the filters
        """
        cursor = self.connection.cursor()

        query = "SELECT * FROM recordings WHERE 1=1"
        params: list = []

        if status:
            query += " AND analysis_status = ?"
            params.append(status)

        query += " ORDER BY imported_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append(Recording.from_row(tuple(row)))
        return results

    def update_recording_status(
        self,
        hash_prefix: str,
        analysis_status: Optional[str] = None,
        analysis_job_id: Optional[str] = None,
        lrc_status: Optional[str] = None,
        lrc_job_id: Optional[str] = None,
    ) -> None:
        """Update recording processing status.

        Args:
            hash_prefix: The hash prefix of the recording
            analysis_status: New analysis status
            analysis_job_id: New analysis job ID
            lrc_status: New LRC status
            lrc_job_id: New LRC job ID
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            updates = []
            params: list = []

            if analysis_status:
                updates.append("analysis_status = ?")
                params.append(analysis_status)

            if analysis_job_id:
                updates.append("analysis_job_id = ?")
                params.append(analysis_job_id)

            if lrc_status:
                updates.append("lrc_status = ?")
                params.append(lrc_status)

            if lrc_job_id:
                updates.append("lrc_job_id = ?")
                params.append(lrc_job_id)

            if not updates:
                return

            params.append(hash_prefix)

            sql = f"""
                UPDATE recordings
                SET {', '.join(updates)}, updated_at = datetime('now')
                WHERE hash_prefix = ?
            """

            cursor.execute(sql, params)

    def get_recording_by_job_id(self, job_id: str, job_type: str = "analysis") -> Optional[Recording]:
        """Get a recording by its analysis or LRC job ID.

        Args:
            job_id: The job ID
            job_type: Type of job ("analysis" or "lrc")

        Returns:
            Recording or None if not found
        """
        cursor = self.connection.cursor()
        if job_type == "analysis":
            cursor.execute(
                "SELECT * FROM recordings WHERE analysis_job_id = ?",
                (job_id,),
            )
        else:
            cursor.execute(
                "SELECT * FROM recordings WHERE lrc_job_id = ?",
                (job_id,),
            )
        row = cursor.fetchone()

        if row:
            return Recording.from_row(tuple(row))
        return None

    def update_recording_analysis(
        self,
        hash_prefix: str,
        duration_seconds: Optional[float] = None,
        tempo_bpm: Optional[float] = None,
        musical_key: Optional[str] = None,
        musical_mode: Optional[str] = None,
        key_confidence: Optional[float] = None,
        loudness_db: Optional[float] = None,
        beats: Optional[str] = None,
        downbeats: Optional[str] = None,
        sections: Optional[str] = None,
        embeddings_shape: Optional[str] = None,
        r2_stems_url: Optional[str] = None,
    ) -> None:
        """Update recording with analysis results.

        Args:
            hash_prefix: The hash prefix of the recording
            duration_seconds: Audio duration
            tempo_bpm: Detected tempo
            musical_key: Detected key
            musical_mode: Detected mode
            key_confidence: Key detection confidence
            loudness_db: Loudness in dB
            beats: JSON array of beat timestamps
            downbeats: JSON array of downbeat timestamps
            sections: JSON array of sections
            embeddings_shape: JSON array of dimensions
            r2_stems_url: R2 URL for stems directory
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            sql = """
                UPDATE recordings SET
                    duration_seconds = ?,
                    tempo_bpm = ?,
                    musical_key = ?,
                    musical_mode = ?,
                    key_confidence = ?,
                    loudness_db = ?,
                    beats = ?,
                    downbeats = ?,
                    sections = ?,
                    embeddings_shape = ?,
                    r2_stems_url = COALESCE(?, r2_stems_url),
                    analysis_status = 'completed',
                    updated_at = datetime('now')
                WHERE hash_prefix = ?
            """

            cursor.execute(
                sql,
                (
                    duration_seconds,
                    tempo_bpm,
                    musical_key,
                    musical_mode,
                    key_confidence,
                    loudness_db,
                    beats,
                    downbeats,
                    sections,
                    embeddings_shape,
                    r2_stems_url,
                    hash_prefix,
                ),
            )

    def update_recording_lrc(
        self,
        hash_prefix: str,
        r2_lrc_url: str,
    ) -> None:
        """Update recording with LRC results.

        Args:
            hash_prefix: The hash prefix of the recording
            r2_lrc_url: R2 URL for the generated LRC file
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            sql = """
                UPDATE recordings SET
                    r2_lrc_url = ?,
                    lrc_status = 'completed',
                    updated_at = datetime('now')
                WHERE hash_prefix = ?
            """

            cursor.execute(sql, (r2_lrc_url, hash_prefix))
