"""Database client for sow-admin.

Provides SQLite database operations for local storage of song catalog
and recording metadata. Supports libsql/Turso for embedded replica sync.
"""

import base64
import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional, Union

import requests

logger = logging.getLogger("sow_admin.db")

from stream_of_worship.admin.db.models import DatabaseStats, Recording, Song
from stream_of_worship.admin.db.schema import (
    CREATE_INDEXES,
    CREATE_RECORDINGS_TABLE,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    RECORDING_COLUMN_COUNT,
    CREATE_SONGS_TABLE,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_SYNC_METADATA_TABLE,
    DEFAULT_SYNC_METADATA,
    ACTIVE_ROW_COUNT_QUERY,
    FOREIGN_KEYS_QUERY,
    INTEGRITY_CHECK_QUERY,
    ROW_COUNT_QUERY,
    SONG_COLUMN_COUNT,
    apply_column_migrations,
    apply_column_migrations_remote,
)

# Optional libsql import for Turso support
try:
    import libsql

    LIBSQL_AVAILABLE = True
    _LIBSQL_ERROR: tuple = (libsql.Error,)
except ImportError:
    LIBSQL_AVAILABLE = False
    libsql = None  # type: ignore
    _LIBSQL_ERROR = ()


class SyncError(Exception):
    """Error during database sync operation."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


def _format_param(value) -> dict:
    """Format a Python value for the Turso HTTP API /v2/pipeline.

    Args:
        value: Python value to format.

    Returns:
        Dict with type and value for HTTP API.
    """
    if value is None:
        return {"type": "null"}
    elif isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    elif isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    elif isinstance(value, float):
        return {"type": "float", "value": str(value)}
    elif isinstance(value, str):
        return {"type": "text", "value": value}
    elif isinstance(value, bytes):
        return {"type": "blob", "base64": base64.b64encode(value).decode()}
    else:
        return {"type": "text", "value": str(value)}


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
            turso_token: Turso auth token (optional, falls back to SOW_TURSO_TOKEN env var)
        """
        self.db_path = db_path
        self.turso_url = turso_url
        self.turso_token = turso_token or os.environ.get("SOW_TURSO_TOKEN")
        self._connection: Optional[Union[sqlite3.Connection, "libsql.Connection"]] = None

    @property
    def is_turso_enabled(self) -> bool:
        """Check if Turso sync is enabled.

        Returns:
            True if Turso URL is configured and libsql is available
        """
        return bool(self.turso_url and LIBSQL_AVAILABLE)

    @property
    def http_pipeline_url(self) -> Optional[str]:
        """Derive HTTPS pipeline URL from libsql:// URL.

        Returns:
            HTTPS URL for /v2/pipeline endpoint, or None if Turso not configured.
        """
        if not self.turso_url:
            return None
        url = self.turso_url.replace("libsql://", "https://")
        if not url.startswith("https://"):
            url = "https://" + url.split("://", 1)[-1]
        return url.rstrip("/") + "/v2/pipeline"

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
                try:
                    self._connection = libsql.connect(
                        str(self.db_path),
                        sync_url=self.turso_url,
                        auth_token=self.turso_token or "",
                    )
                except (ValueError, *_LIBSQL_ERROR) as e:
                    # libsql can raise ValueError for sync/metadata errors
                    # Convert to SyncError so recovery logic can handle it
                    error_msg = str(e).lower()
                    if "metadata" in error_msg or "sync" in error_msg or "local state" in error_msg:
                        raise SyncError(
                            f"Local database metadata is missing or invalid. "
                            f"This typically happens when a vanilla SQLite database was created "
                            f"by 'db init' and needs to be migrated to a libsql embedded replica. "
                            f"Auto-recovery will recreate the database from Turso. "
                            f"Original error: {e}"
                        )
                    raise SyncError(f"Failed to connect to Turso: {e}")
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

    def _execute_remote_pipeline(
        self,
        requests_payload: list[dict],
        timeout: int = 30,
    ) -> list[dict]:
        """Execute a pipeline of SQL statements on Turso Cloud via HTTP API.

        Args:
            requests_payload: List of request dicts for /v2/pipeline.
                Each dict is {"type": "execute", "stmt": {"sql": ..., "args": [...]}}
                or {"type": "close"}.
            timeout: HTTP timeout in seconds.

        Returns:
            List of result dicts from the pipeline response.

        Raises:
            SyncError: If HTTP request fails or any statement returns an error.
        """
        url = self.http_pipeline_url
        if not url:
            raise SyncError("Turso not configured: no URL available")

        payload = {"requests": requests_payload}
        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.turso_token}",
                    "Content-Type": "application/json",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.Timeout:
            raise SyncError(
                f"Remote write timed out after {timeout}s. "
                "The write may have succeeded — verify with 'db pull' before retrying."
            )
        except requests.exceptions.ConnectionError as e:
            raise SyncError(f"Cannot connect to Turso: {e}")
        except requests.exceptions.RequestException as e:
            raise SyncError(f"Remote write failed: {e}")

        results = result.get("results", [])
        return results

    def _check_pipeline_results(
        self, results: list[dict], ignore_sql_errors: Optional[set[str]] = None
    ) -> None:
        """Check pipeline results for errors, optionally ignoring specific SQL error codes.

        Args:
            results: Results list from _execute_remote_pipeline().
            ignore_sql_errors: Set of SQL error message substrings to suppress (case-insensitive).
                Use for DDL idempotency (e.g., {"duplicate column name", "already exists"}).

        Raises:
            SyncError: If any result is an error that is not in ignore_sql_errors.
        """
        for r in results:
            if r.get("type") == "error":
                error_obj = r.get("error", {})
                msg = error_obj.get("message", "unknown error")
                if ignore_sql_errors:
                    msg_lower = msg.lower()
                    if any(pattern in msg_lower for pattern in ignore_sql_errors):
                        continue
                raise SyncError(f"Remote execute failed: {msg}")

    def _execute_remote(self, sql: str, params: tuple = (), timeout: int = 10) -> dict:
        """Execute a single DML statement on Turso Cloud. Returns result dict.

        Args:
            sql: SQL statement to execute.
            params: Parameters for the SQL statement.
            timeout: HTTP timeout in seconds.

        Returns:
            Result dict from the execute response (may include rows for SELECT/PRAGMA).
        """
        stmt = {"sql": sql, "args": [_format_param(p) for p in params]}
        results = self._execute_remote_pipeline(
            [{"type": "execute", "stmt": stmt}, {"type": "close"}],
            timeout=timeout,
        )
        self._check_pipeline_results(results)
        for r in results:
            if r.get("type") == "ok":
                resp = r.get("response", {})
                if resp.get("type") == "execute":
                    return resp.get("result", {})
        return {}

    def _execute_remote_transaction(
        self,
        statements: list[tuple[str, tuple]],
        timeout: int = 30,
    ) -> None:
        """Execute multiple DML statements in a single remote transaction.

        All statements are sent in one HTTP pipeline request with BEGIN/COMMIT.

        Args:
            statements: List of (sql, params) tuples.
            timeout: HTTP timeout in seconds.
        """
        pipeline = [{"type": "execute", "stmt": {"sql": "BEGIN", "args": []}}]
        for sql, params in statements:
            pipeline.append(
                {
                    "type": "execute",
                    "stmt": {"sql": sql, "args": [_format_param(p) for p in params]},
                }
            )
        pipeline.append({"type": "execute", "stmt": {"sql": "COMMIT", "args": []}})
        pipeline.append({"type": "close"})

        results = self._execute_remote_pipeline(pipeline, timeout=timeout)
        self._check_pipeline_results(results)

    def _sync_replica(self, fatal: bool = False) -> None:
        """Pull remote changes to local embedded replica.

        Since no DML writes go to the replica in the new model, conn.sync()
        is effectively pull-only (nothing to push).

        Args:
            fatal: If True, raise SyncError on failure. If False, log warning only.
                Use fatal=True before any operation that reads locally then writes remotely.
        """
        if not self.is_turso_enabled or self._connection is None:
            return
        try:
            self._connection.sync()  # type: ignore
            self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
        except Exception as e:
            if fatal:
                raise SyncError(
                    f"Replica sync failed before read-then-write operation: {e}. "
                    "Aborting to prevent stale reads. Run 'db pull' to recover.",
                    cause=e,
                )
            logger.warning(f"Replica sync after write failed (non-fatal): {e}")

    def sync(self) -> None:
        """Pull remote changes from Turso to local embedded replica.

        In the remote-write model, this is pull-only: the replica has no local
        DML writes to push, so conn.sync() fetches remote changes without conflict.

        Raises:
            SyncError: If sync fails or schema is invalid after pull.
        """
        if not self.is_turso_enabled:
            raise SyncError("Turso sync is not configured")

        try:
            conn = self.connection
            conn.sync()  # type: ignore
        except Exception as e:
            raise SyncError(f"Sync failed: {e}", cause=e)

        cursor = self.connection.cursor()
        self._validate_schema(cursor)

        self.update_sync_metadata("last_sync_at", datetime.now().isoformat())

    def _validate_schema(self, cursor) -> None:
        """Validate that tables have expected column counts.

        Args:
            cursor: Database cursor to execute queries on.

        Raises:
            SyncError: If schema mismatch detected after sync.
        """
        expected = {"recordings": RECORDING_COLUMN_COUNT, "songs": SONG_COLUMN_COUNT}
        for table, expected_count in expected.items():
            try:
                cursor.execute(f"PRAGMA table_info({table})")
                actual_count = len(cursor.fetchall())
                if actual_count != expected_count:
                    raise SyncError(
                        f"Schema mismatch after sync: {table} has {actual_count} columns, "
                        f"expected {expected_count}. This may indicate a migration was not "
                        f"applied. Run 'db init' to apply missing migrations."
                    )
            except (sqlite3.OperationalError, *_LIBSQL_ERROR):
                pass  # Table doesn't exist yet (fresh DB)

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
        Runs migrations before creating indexes to handle column additions.

        For Turso: sends DDL to remote via HTTP API.
        For sqlite3: applies locally.
        """
        if self.is_turso_enabled:
            for stmt in [CREATE_SONGS_TABLE, CREATE_RECORDINGS_TABLE, CREATE_SYNC_METADATA_TABLE]:
                try:
                    self._execute_remote(stmt)
                except SyncError as e:
                    if "already exists" not in str(e).lower():
                        raise
            apply_column_migrations_remote(self)
            try:
                self._execute_remote(
                    "UPDATE recordings SET visibility_status = 'published' "
                    "WHERE lrc_status = 'completed' AND visibility_status IS NULL"
                )
            except SyncError:
                pass
            for stmt in CREATE_INDEXES:
                try:
                    self._execute_remote(stmt)
                except SyncError as e:
                    if "already exists" not in str(e).lower():
                        raise
            for stmt in [CREATE_SONGS_UPDATE_TRIGGER, CREATE_RECORDINGS_UPDATE_TRIGGER]:
                try:
                    self._execute_remote(stmt)
                except SyncError as e:
                    if "already exists" not in str(e).lower():
                        raise
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(CREATE_SONGS_TABLE)
                cursor.execute(CREATE_RECORDINGS_TABLE)
                cursor.execute(CREATE_SYNC_METADATA_TABLE)
                apply_column_migrations(cursor)
                try:
                    cursor.execute("""
                        UPDATE recordings SET visibility_status = 'published'
                        WHERE lrc_status = 'completed' AND visibility_status IS NULL
                    """)
                except sqlite3.OperationalError:
                    pass
                for statement in CREATE_INDEXES:
                    cursor.execute(statement)
                cursor.execute(CREATE_SONGS_UPDATE_TRIGGER)
                cursor.execute(CREATE_RECORDINGS_UPDATE_TRIGGER)
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
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()

            for (table_name,) in tables:
                if not table_name.startswith("sqlite_"):
                    cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

            cursor.execute("PRAGMA foreign_keys = ON")

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

        # Get active (non-deleted) row counts
        cursor.execute(ACTIVE_ROW_COUNT_QUERY)
        active_counts = {row[0]: row[1] for row in cursor.fetchall()}

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
            active_counts=active_counts,
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

        Clears deleted_at on INSERT OR REPLACE to resurrect a previously-deleted song.

        Args:
            song: Song to insert
        """
        sql = """
            INSERT OR REPLACE INTO songs (
                id, title, title_pinyin, composer, lyricist,
                album_name, album_series, musical_key, lyrics_raw,
                lyrics_lines, sections, source_url, table_row_number,
                scraped_at, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """
        params = (
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
        )
        if self.is_turso_enabled:
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

    def bulk_insert_songs(self, songs: list[Song]) -> None:
        """Insert multiple songs in a single remote transaction.

        Args:
            songs: List of songs to insert.
        """
        if not songs:
            return
        sql = """
            INSERT OR REPLACE INTO songs (
                id, title, title_pinyin, composer, lyricist,
                album_name, album_series, musical_key, lyrics_raw,
                lyrics_lines, sections, source_url, table_row_number,
                scraped_at, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """
        if self.is_turso_enabled:
            statements = [
                (
                    sql,
                    (
                        s.id,
                        s.title,
                        s.title_pinyin,
                        s.composer,
                        s.lyricist,
                        s.album_name,
                        s.album_series,
                        s.musical_key,
                        s.lyrics_raw,
                        s.lyrics_lines,
                        s.sections,
                        s.source_url,
                        s.table_row_number,
                        s.scraped_at,
                        s.created_at or datetime.now().isoformat(),
                        s.updated_at or datetime.now().isoformat(),
                    ),
                )
                for s in songs
            ]
            self._execute_remote_transaction(statements)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                for song in songs:
                    cursor.execute(
                        sql,
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
            return Song.from_row(tuple(row), cursor.description)
        return None

    def list_songs(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        include_deleted: bool = False,
        sort_by: str = "album",
    ) -> list[Song]:
        """List songs with optional filters.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            include_deleted: Whether to include soft-deleted songs
            sort_by: Sort order - "album" (album_name, title), "title", or "id"

        Returns:
            List of songs matching the filters
        """
        cursor = self.connection.cursor()

        query = "SELECT * FROM songs WHERE 1=1"
        params: list = []

        if not include_deleted:
            query += " AND deleted_at IS NULL"

        if album:
            query += " AND album_name = ?"
            params.append(album)

        if key:
            query += " AND musical_key = ?"
            params.append(key)

        order_map = {
            "album": "album_name, title",
            "series": "album_series, album_name, title",
            "title": "title",
            "id": "id",
        }
        query += f" ORDER BY {order_map.get(sort_by, 'album_name, title')}"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)
        description = cursor.description

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row), description))
        return results

    def list_albums(self, include_deleted: bool = False) -> list[tuple[str, str, int]]:
        """List distinct album names with song counts.

        Args:
            include_deleted: Whether to include soft-deleted songs

        Returns:
            List of (album_name, album_series, song_count) tuples sorted by album_name
        """
        cursor = self.connection.cursor()

        query = (
            "SELECT album_name, "
            "MAX(album_series) as album_series, "
            "COUNT(*) as cnt FROM songs WHERE 1=1"
        )
        params: list = []

        if not include_deleted:
            query += " AND deleted_at IS NULL"

        query += " GROUP BY album_name ORDER BY album_name"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append((row[0], row[1], row[2]))
        return results

    def search_songs(
        self, query: str, field: str = "all", limit: int = 20, include_deleted: bool = False
    ) -> list[Song]:
        """Search songs by query.

        Args:
            query: Search query string
            field: Field to search (title, lyrics, composer, album, all)
            limit: Maximum number of results
            include_deleted: Whether to include soft-deleted songs

        Returns:
            List of matching songs
        """
        cursor = self.connection.cursor()

        search_pattern = f"%{query}%"

        deleted_clause = "" if include_deleted else "deleted_at IS NULL AND "

        if field == "title":
            sql = f"SELECT * FROM songs WHERE {deleted_clause}(title LIKE ? OR title_pinyin LIKE ?)"
            params = [search_pattern, search_pattern]
        elif field == "lyrics":
            sql = f"SELECT * FROM songs WHERE {deleted_clause}lyrics_raw LIKE ?"
            params = [search_pattern]
        elif field == "composer":
            sql = f"SELECT * FROM songs WHERE {deleted_clause}(composer LIKE ? OR lyricist LIKE ?)"
            params = [search_pattern, search_pattern]
        elif field == "album":
            sql = f"SELECT * FROM songs WHERE {deleted_clause}(album_name LIKE ? OR album_series LIKE ?)"
            params = [search_pattern, search_pattern]
        else:  # all
            sql = f"""
                SELECT * FROM songs WHERE {deleted_clause}(
                title LIKE ? OR title_pinyin LIKE ? OR
                lyrics_raw LIKE ? OR composer LIKE ? OR lyricist LIKE ? OR
                album_name LIKE ? OR album_series LIKE ?)
            """
            params = [search_pattern] * 7

        sql += f" ORDER BY id LIMIT {limit}"

        cursor.execute(sql, params)
        description = cursor.description

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row), description))
        return results

    # Recording operations

    def insert_recording(self, recording: Recording) -> None:
        """Insert a recording into the database.

        Args:
            recording: Recording to insert
        """
        sql = """
            INSERT OR REPLACE INTO recordings (
                content_hash, hash_prefix, song_id, original_filename,
                file_size_bytes, imported_at, r2_audio_url, r2_stems_url,
                r2_lrc_url, duration_seconds, tempo_bpm, musical_key,
                musical_mode, key_confidence, loudness_db, beats,
                downbeats, sections, embeddings_shape, analysis_status,
                analysis_job_id, lrc_status, lrc_job_id, visibility_status,
                download_status, created_at, updated_at, youtube_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
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
            recording.visibility_status,
            recording.download_status or "pending",
            recording.created_at or datetime.now().isoformat(),
            recording.updated_at or datetime.now().isoformat(),
            recording.youtube_url,
        )
        if self.is_turso_enabled:
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

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
            return Recording.from_row(tuple(row), cursor.description)
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
            return Recording.from_row(tuple(row), cursor.description)
        return None

    def list_recordings(
        self,
        status: Optional[str] = None,
        visibility: Optional[str] = None,
        lrc_status: Optional[str] = None,
        limit: Optional[int] = None,
        include_deleted: bool = False,
    ) -> list[Recording]:
        """List recordings with optional filters.

        Args:
            status: Filter by analysis status
            visibility: Filter by visibility status (published|review|hold)
            lrc_status: Filter by LRC status (pending|processing|completed|failed|incomplete)
            limit: Maximum number of results
            include_deleted: Whether to include soft-deleted recordings

        Returns:
            List of recordings matching the filters
        """
        cursor = self.connection.cursor()

        query = "SELECT * FROM recordings WHERE 1=1"
        params: list = []

        if not include_deleted:
            query += " AND deleted_at IS NULL"

        if status:
            if status == "incomplete":
                query += " AND analysis_status IN ('pending', 'processing', 'failed')"
            else:
                query += " AND analysis_status = ?"
                params.append(status)

        if visibility:
            query += " AND visibility_status = ?"
            params.append(visibility)

        if lrc_status:
            if lrc_status == "incomplete":
                query += " AND lrc_status IN ('pending', 'processing', 'failed')"
            else:
                query += " AND lrc_status = ?"
                params.append(lrc_status)

        query += " ORDER BY imported_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)
        description = cursor.description

        results = []
        for row in cursor.fetchall():
            results.append(Recording.from_row(tuple(row), description))
        return results

    def list_recordings_with_songs(
        self,
        status: Optional[str] = None,
        visibility: Optional[str] = None,
        lrc_status: Optional[str] = None,
        album: Optional[str] = None,
        sort_by: str = "imported",
        limit: Optional[int] = None,
        include_deleted: bool = False,
    ) -> list[tuple[Recording, Optional[str], Optional[str], Optional[str]]]:
        """List recordings with joined song data for efficient querying.

        Uses LEFT JOIN to fetch recording and song data in a single query,
        avoiding the N+1 query problem. Supports filtering by album name
        and sorting by various fields.

        Args:
            status: Filter by analysis status
            visibility: Filter by visibility status (published|review|hold)
            lrc_status: Filter by LRC status (pending|processing|completed|failed|incomplete)
            album: Filter by album name (case-insensitive substring match)
            sort_by: Sort order - "album", "series", "title", or "imported" (default)
            limit: Maximum number of results
            include_deleted: Whether to include soft-deleted recordings

        Returns:
            List of tuples (Recording, song_title, album_name, album_series)
        """
        cursor = self.connection.cursor()

        # Build query with LEFT JOIN to songs table
        # Note: r.* returns all recording columns including download_status (RECORDING_COLUMN_COUNT cols total)
        query = """
            SELECT r.*, s.title as song_title, s.album_name, s.album_series
            FROM recordings r
            LEFT JOIN songs s ON r.song_id = s.id
            WHERE 1=1
        """
        params: list = []

        if not include_deleted:
            query += " AND r.deleted_at IS NULL"

        if status:
            query += " AND r.analysis_status = ?"
            params.append(status)

        if visibility:
            query += " AND r.visibility_status = ?"
            params.append(visibility)

        if lrc_status:
            if lrc_status == "incomplete":
                query += " AND r.lrc_status IN ('pending', 'processing', 'failed')"
            else:
                query += " AND r.lrc_status = ?"
                params.append(lrc_status)

        if album:
            query += " AND s.album_name LIKE ?"
            params.append(f"%{album}%")

        # ORDER BY clause based on sort_by
        order_map = {
            "album": "s.album_name ASC NULLS LAST, s.title ASC NULLS LAST",
            "series": "s.album_series ASC NULLS LAST, s.album_name ASC NULLS LAST, s.title ASC NULLS LAST",
            "title": "s.title ASC NULLS LAST",
            "imported": "r.imported_at DESC",
        }
        query += f" ORDER BY {order_map.get(sort_by, 'r.imported_at DESC')}"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)
        description = cursor.description

        results = []
        for row in cursor.fetchall():
            # Extract song data from row (last 3 columns)
            # Row structure: [RECORDING_COLUMN_COUNT recording columns] + [song_title, album_name, album_series]
            row_tuple = tuple(row)
            recording_cols = row_tuple[:RECORDING_COLUMN_COUNT]
            song_title = row_tuple[RECORDING_COLUMN_COUNT]
            album_name = row_tuple[RECORDING_COLUMN_COUNT + 1]
            album_series_val = row_tuple[RECORDING_COLUMN_COUNT + 2]
            rec_description = description[:RECORDING_COLUMN_COUNT]
            recording = Recording.from_row(recording_cols, rec_description)
            results.append((recording, song_title, album_name, album_series_val))
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
            SET {", ".join(updates)}, updated_at = datetime('now')
            WHERE hash_prefix = ?
        """

        if self.is_turso_enabled:
            self._execute_remote(sql, tuple(params))
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

    def get_recording_by_job_id(
        self, job_id: str, job_type: str = "analysis"
    ) -> Optional[Recording]:
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
            return Recording.from_row(tuple(row), cursor.description)
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
        params = (
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
        )
        if self.is_turso_enabled:
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

    def update_recording_lrc(
        self,
        hash_prefix: str,
        r2_lrc_url: str,
    ) -> None:
        """Update recording with LRC results.

        Auto-publishes the recording when visibility_status is NULL (first-time LRC).
        When visibility is already set (review/hold), keeps current status so user
        can explicitly publish after reviewing/fixing.

        Args:
            hash_prefix: The hash prefix of the recording
            r2_lrc_url: R2 URL for the generated LRC file
        """
        sql = """
            UPDATE recordings SET
                r2_lrc_url = ?,
                lrc_status = 'completed',
                visibility_status = COALESCE(visibility_status, 'published'),
                updated_at = datetime('now')
            WHERE hash_prefix = ?
        """
        params = (r2_lrc_url, hash_prefix)
        if self.is_turso_enabled:
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

    def update_recording_download(
        self,
        hash_prefix: str,
        download_status: str,
    ) -> None:
        """Update download status for a recording.

        Args:
            hash_prefix: The hash prefix of the recording
            download_status: New download status (pending|processing|completed|failed)

        Raises:
            ValueError: If download_status is not valid
        """
        valid_statuses = {"pending", "processing", "completed", "failed"}
        if download_status not in valid_statuses:
            raise ValueError(
                f"Invalid download_status: {download_status}. "
                f"Must be one of: {', '.join(valid_statuses)}"
            )

        sql = """
            UPDATE recordings SET
                download_status = ?,
                updated_at = datetime('now')
            WHERE hash_prefix = ?
        """
        params = (download_status, hash_prefix)
        if self.is_turso_enabled:
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

    def update_recording_visibility(
        self,
        hash_prefix: str,
        visibility_status: str,
    ) -> bool:
        """Update recording visibility status.

        Args:
            hash_prefix: The hash prefix of the recording
            visibility_status: New visibility status (published, review, hold)

        Returns:
            True if recording was updated, False if not found

        Raises:
            ValueError: If visibility_status is not valid
        """
        valid_statuses = {"published", "review", "hold"}
        if visibility_status not in valid_statuses:
            raise ValueError(
                f"Invalid visibility_status: {visibility_status}. "
                f"Must be one of: {', '.join(valid_statuses)}"
            )

        sql = """
            UPDATE recordings SET
                visibility_status = ?,
                updated_at = datetime('now')
            WHERE hash_prefix = ?
        """
        params = (visibility_status, hash_prefix)
        if self.is_turso_enabled:
            result = self._execute_remote(
                f"SELECT 1 FROM recordings WHERE hash_prefix = ? LIMIT 1", (hash_prefix,)
            )
            exists = len(result.get("rows", [])) > 0
            if not exists:
                return False
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
            return True
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.rowcount > 0

    def delete_recording(self, hash_prefix: str) -> None:
        """Soft-delete a recording by hash_prefix.

        Args:
            hash_prefix: The hash prefix of the recording to delete
        """
        sql = "UPDATE recordings SET deleted_at = datetime('now') WHERE hash_prefix = ?"
        params = (hash_prefix,)
        if self.is_turso_enabled:
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)

    def soft_delete_song(self, song_id: str) -> bool:
        """Soft-delete a song by ID.

        Args:
            song_id: The song ID to soft-delete

        Returns:
            True if song was marked as deleted, False if not found
        """
        sql = "UPDATE songs SET deleted_at = datetime('now') WHERE id = ?"
        params = (song_id,)
        if self.is_turso_enabled:
            result = self._execute_remote(
                f"SELECT 1 FROM songs WHERE id = ? LIMIT 1", (song_id,)
            )
            exists = len(result.get("rows", [])) > 0
            if not exists:
                return False
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
            return True
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.rowcount > 0

    def list_deleted_songs(self) -> list[Song]:
        """List all soft-deleted songs.

        Returns:
            List of soft-deleted songs
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM songs WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
        description = cursor.description
        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row), description))
        return results

    def list_deleted_recordings(self) -> list[Recording]:
        """List all soft-deleted recordings.

        Returns:
            List of soft-deleted recordings
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT * FROM recordings WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        )
        description = cursor.description
        results = []
        for row in cursor.fetchall():
            results.append(Recording.from_row(tuple(row), description))
        return results

    def restore_song(self, song_id: str) -> bool:
        """Restore a soft-deleted song.

        Args:
            song_id: The song ID to restore

        Returns:
            True if song was restored, False if not found
        """
        sql = "UPDATE songs SET deleted_at = NULL WHERE id = ?"
        params = (song_id,)
        if self.is_turso_enabled:
            result = self._execute_remote(
                f"SELECT 1 FROM songs WHERE id = ? LIMIT 1", (song_id,)
            )
            exists = len(result.get("rows", [])) > 0
            if not exists:
                return False
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
            return True
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.rowcount > 0

    def restore_recording(self, hash_prefix: str) -> bool:
        """Restore a soft-deleted recording.

        Args:
            hash_prefix: The hash prefix of the recording to restore

        Returns:
            True if recording was restored, False if not found
        """
        sql = "UPDATE recordings SET deleted_at = NULL WHERE hash_prefix = ?"
        params = (hash_prefix,)
        if self.is_turso_enabled:
            result = self._execute_remote(
                f"SELECT 1 FROM recordings WHERE hash_prefix = ? LIMIT 1", (hash_prefix,)
            )
            exists = len(result.get("rows", [])) > 0
            if not exists:
                return False
            self._execute_remote(sql, params)
            self._sync_replica(fatal=False)
            return True
        else:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.rowcount > 0
