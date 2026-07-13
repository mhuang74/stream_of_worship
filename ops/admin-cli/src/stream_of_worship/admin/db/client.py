"""Database client for sow-admin.

Provides PostgreSQL database operations for song catalog and recording
metadata via ``psycopg``.
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

import psycopg
import psycopg.errors

from stream_of_worship.admin.db.models import DatabaseStats, Recording, Song
from stream_of_worship.admin.db.schema import (
    ACTIVE_ROW_COUNT_QUERY,
    RECORDING_COLUMNS_FOR_JOIN,
    RECORDING_COLUMNS_SELECT,
    RECORDING_COLUMN_COUNT,
    ROW_COUNT_QUERY,
    SONG_COLUMNS_FOR_JOIN,
    SONG_COLUMNS_SELECT,
    SONG_COLUMN_COUNT,
)
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.helpers import to_str

logger = logging.getLogger("sow_admin.db")

_VALID_VISIBILITY_STATUSES = {"published", "review", "hold"}


class DatabaseClient:
    """Client for PostgreSQL database operations.

    This client manages the connection via a ``ConnectionProvider`` and
    provides methods for CRUD operations on songs and recordings.

    Attributes:
        connection_provider: ``ConnectionProvider`` instance that manages
            the underlying ``psycopg.Connection``.
    """

    def __init__(self, connection_provider: ConnectionProvider):
        """Initialize the database client.

        Args:
            connection_provider: ``ConnectionProvider`` wrapping the DSN.
        """
        self.connection_provider = connection_provider

    @property
    def connection(self) -> psycopg.Connection:
        """Get the current psycopg connection from the provider."""
        return self.connection_provider.get_connection()

    def _execute_with_retry(self, fn):
        try:
            return fn(self.connection)
        except psycopg.OperationalError:
            self.connection_provider.invalidate()
            return fn(self.connection)

    def close(self) -> None:
        """Close the underlying connection via the provider."""
        self.connection_provider.close()

    def __enter__(self) -> "DatabaseClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Generator[psycopg.Connection, None, None]:
        """Context manager for database transactions.

        Yields:
            psycopg connection with an active transaction.
        """
        conn = self.connection
        try:
            with conn.transaction():
                yield conn
            conn.commit()
        except Exception:
            raise

    def initialize_schema(self, wipe_songsets: bool = False) -> None:
        """Initialize the full database schema (catalog + auth + app + per-user).

        Runs the unified ``ALL_SCHEMA_STATEMENTS`` list from
        ``stream_of_worship.db.postgres_schema``. All statements are
        ``CREATE ... IF NOT EXISTS`` or ``CREATE OR REPLACE``, so re-running
        is safe.

        Args:
            wipe_songsets: If True, drops ``songset_items`` and ``songsets``
                (CASCADE) before creating the new schema. Used during the
                multi-user cutover to swap the songsets table for the new
                NOT NULL ``user_id`` column without preserving old rows.
        """
        # Lazy import to break the admin.db.__init__ ↔ postgres_schema cycle.
        from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

        with self.transaction() as conn:
            cursor = conn.cursor()

            if wipe_songsets:
                cursor.execute("DROP TABLE IF EXISTS songset_items, songsets CASCADE;")

            for statement in ALL_SCHEMA_STATEMENTS:
                cursor.execute(statement)

    def get_stats(self) -> DatabaseStats:
        """Get database statistics.

        Returns:
            ``DatabaseStats`` with current database state.
        """
        cursor = self.connection.cursor()

        # Row counts (active/non-deleted only) — tolerate missing tables
        try:
            cursor.execute(ACTIVE_ROW_COUNT_QUERY)
            table_counts = {row[0]: row[1] for row in cursor.fetchall()}
        except psycopg.errors.UndefinedTable:
            self.connection.rollback()
            cursor = self.connection.cursor()
            table_counts = {"songs": 0, "recordings": 0}

        # Health check: Postgres is in recovery mode?
        cursor.execute("SELECT pg_is_in_recovery()")
        is_healthy = not cursor.fetchone()[0]

        return DatabaseStats(
            table_counts=table_counts,
            is_healthy=is_healthy,
            last_sync_at=None,
            sync_version="3",
        )

    # ------------------------------------------------------------------
    # Song operations
    # ------------------------------------------------------------------

    def insert_song(self, song: Song) -> None:
        """Insert or upsert a song into the database.

        Clears ``deleted_at`` on conflict to resurrect a previously-deleted
        song.

        Args:
            song: Song to insert.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO songs (
                    id, title, title_pinyin, composer, lyricist,
                    album_name, album_series, musical_key, musical_key_root,
                    musical_key_mode, musical_key_start_root, musical_key_end_root,
                    musical_key_start_pitch_class, musical_key_end_pitch_class,
                    musical_key_parse_status, lyrics_raw, lyrics_lines, sections,
                    source_url, table_row_number, scraped_at, created_at, updated_at,
                    deleted_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    title_pinyin = EXCLUDED.title_pinyin,
                    composer = EXCLUDED.composer,
                    lyricist = EXCLUDED.lyricist,
                    album_name = EXCLUDED.album_name,
                    album_series = EXCLUDED.album_series,
                    musical_key = EXCLUDED.musical_key,
                    musical_key_root = EXCLUDED.musical_key_root,
                    musical_key_mode = EXCLUDED.musical_key_mode,
                    musical_key_start_root = EXCLUDED.musical_key_start_root,
                    musical_key_end_root = EXCLUDED.musical_key_end_root,
                    musical_key_start_pitch_class = EXCLUDED.musical_key_start_pitch_class,
                    musical_key_end_pitch_class = EXCLUDED.musical_key_end_pitch_class,
                    musical_key_parse_status = EXCLUDED.musical_key_parse_status,
                    lyrics_raw = EXCLUDED.lyrics_raw,
                    lyrics_lines = EXCLUDED.lyrics_lines,
                    sections = EXCLUDED.sections,
                    source_url = EXCLUDED.source_url,
                    table_row_number = EXCLUDED.table_row_number,
                    scraped_at = EXCLUDED.scraped_at,
                    updated_at = EXCLUDED.updated_at,
                    deleted_at = NULL
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
                    song.musical_key_root,
                    song.musical_key_mode,
                    song.musical_key_start_root,
                    song.musical_key_end_root,
                    song.musical_key_start_pitch_class,
                    song.musical_key_end_pitch_class,
                    song.musical_key_parse_status,
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

    def insert_songs_bulk(self, songs: list[Song]) -> int:
        """Bulk insert or upsert songs into the database.

        Uses executemany() for efficient batch processing with a single
        network round-trip.

        Args:
            songs: List of Song objects to insert.

        Returns:
            Number of songs inserted/updated.
        """
        if not songs:
            return 0

        start_time = time.time()
        logger.info(f"Starting bulk insert of {len(songs)} songs")

        sql = """
            INSERT INTO songs (
                id, title, title_pinyin, composer, lyricist,
                album_name, album_series, musical_key, musical_key_root,
                musical_key_mode, musical_key_start_root, musical_key_end_root,
                musical_key_start_pitch_class, musical_key_end_pitch_class,
                musical_key_parse_status, lyrics_raw, lyrics_lines, sections,
                source_url, table_row_number, scraped_at, created_at, updated_at,
                deleted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                title_pinyin = EXCLUDED.title_pinyin,
                composer = EXCLUDED.composer,
                lyricist = EXCLUDED.lyricist,
                album_name = EXCLUDED.album_name,
                album_series = EXCLUDED.album_series,
                musical_key = EXCLUDED.musical_key,
                musical_key_root = EXCLUDED.musical_key_root,
                musical_key_mode = EXCLUDED.musical_key_mode,
                musical_key_start_root = EXCLUDED.musical_key_start_root,
                musical_key_end_root = EXCLUDED.musical_key_end_root,
                musical_key_start_pitch_class = EXCLUDED.musical_key_start_pitch_class,
                musical_key_end_pitch_class = EXCLUDED.musical_key_end_pitch_class,
                musical_key_parse_status = EXCLUDED.musical_key_parse_status,
                lyrics_raw = EXCLUDED.lyrics_raw,
                lyrics_lines = EXCLUDED.lyrics_lines,
                sections = EXCLUDED.sections,
                source_url = EXCLUDED.source_url,
                table_row_number = EXCLUDED.table_row_number,
                scraped_at = EXCLUDED.scraped_at,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                deleted_at = NULL
        """

        now_iso = datetime.now().isoformat()
        params_list = [
            (
                song.id,
                song.title,
                song.title_pinyin,
                song.composer,
                song.lyricist,
                song.album_name,
                song.album_series,
                song.musical_key,
                song.musical_key_root,
                song.musical_key_mode,
                song.musical_key_start_root,
                song.musical_key_end_root,
                song.musical_key_start_pitch_class,
                song.musical_key_end_pitch_class,
                song.musical_key_parse_status,
                song.lyrics_raw,
                song.lyrics_lines,
                song.sections,
                song.source_url,
                song.table_row_number,
                song.scraped_at,
                song.created_at or now_iso,
                song.updated_at or now_iso,
            )
            for song in songs
        ]

        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.executemany(sql, params_list)
            elapsed = time.time() - start_time
            logger.info(
                f"Bulk insert completed: {len(songs)} songs in {elapsed:.2f}s ({len(songs) / elapsed:.1f} songs/sec)"
            )
            return len(songs)

    def get_song(self, song_id: str, include_deleted: bool = False) -> Optional[Song]:
        """Get a song by ID.

        Args:
            song_id: The song ID.
            include_deleted: Whether to include soft-deleted songs.

        Returns:
            ``Song`` or ``None`` if not found.
        """
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute(f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE id = %s", (song_id,))
        else:
            cursor.execute(
                f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE id = %s AND deleted_at IS NULL",
                (song_id,),
            )
        row = cursor.fetchone()

        if row:
            return Song.from_row(tuple(row))
        return None

    def find_song_by_source_url(
        self,
        source_url: str,
        include_deleted: bool = False,
    ) -> Optional[Song]:
        """Find a song by source URL."""
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute(
                f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE source_url = %s ORDER BY updated_at DESC",
                (source_url,),
            )
        else:
            cursor.execute(
                f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE source_url = %s AND deleted_at IS NULL ORDER BY updated_at DESC",
                (source_url,),
            )
        row = cursor.fetchone()
        return Song.from_row(tuple(row)) if row else None

    def update_song(self, song: Song) -> bool:
        """Update an existing song row."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE songs SET
                    title = %s,
                    title_pinyin = %s,
                    composer = %s,
                    lyricist = %s,
                    album_name = %s,
                    album_series = %s,
                    musical_key = %s,
                    lyrics_raw = %s,
                    lyrics_lines = %s,
                    sections = %s,
                    source_url = %s,
                    scraped_at = %s,
                    updated_at = %s
                WHERE id = %s AND deleted_at IS NULL
                """,
                (
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
                    song.scraped_at,
                    song.updated_at or datetime.now().isoformat(),
                    song.id,
                ),
            )
            return cursor.rowcount > 0

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
            album: Filter by album name.
            key: Filter by musical key.
            limit: Maximum number of results.
            include_deleted: Whether to include soft-deleted songs.
            sort_by: Sort order (``album``, ``series``, ``title``, ``id``).

        Returns:
            List of songs matching the filters.
        """
        cursor = self.connection.cursor()

        query = f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE 1=1"
        params: list = []

        if not include_deleted:
            query += " AND deleted_at IS NULL"

        if album:
            query += " AND (album_name ILIKE %s OR album_series ILIKE %s)"
            params.extend([f"%{album}%", f"%{album}%"])

        if key:
            query += " AND musical_key = %s"
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

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    def list_albums(self, include_deleted: bool = False) -> list[tuple[str, str, int]]:
        """List distinct album names with song counts.

        Args:
            include_deleted: Whether to include soft-deleted songs.

        Returns:
            List of ``(album_name, album_series, song_count)`` tuples.
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
            query: Search query string.
            field: Field to search (``title``, ``lyrics``, ``composer``, ``album``, ``all``).
            limit: Maximum number of results.
            include_deleted: Whether to include soft-deleted songs.

        Returns:
            List of matching songs.
        """
        cursor = self.connection.cursor()

        search_pattern = f"%{query}%"

        deleted_clause = "" if include_deleted else "deleted_at IS NULL AND "

        if field == "title":
            sql = f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE {deleted_clause}(title ILIKE %s OR title_pinyin ILIKE %s)"
            params = [search_pattern, search_pattern]
        elif field == "lyrics":
            sql = (
                f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE {deleted_clause}lyrics_raw ILIKE %s"
            )
            params = [search_pattern]
        elif field == "composer":
            sql = f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE {deleted_clause}(composer ILIKE %s OR lyricist ILIKE %s)"
            params = [search_pattern, search_pattern]
        elif field == "album":
            sql = f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE {deleted_clause}(album_name ILIKE %s OR album_series ILIKE %s)"
            params = [search_pattern, search_pattern]
        else:  # all
            sql = f"""
                SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE {deleted_clause}(
                title ILIKE %s OR title_pinyin ILIKE %s OR
                lyrics_raw ILIKE %s OR composer ILIKE %s OR lyricist ILIKE %s OR
                album_name ILIKE %s OR album_series ILIKE %s)
            """
            params = [search_pattern] * 7

        sql += f" ORDER BY id LIMIT {limit}"

        cursor.execute(sql, params)

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    # ------------------------------------------------------------------
    # Recording operations
    # ------------------------------------------------------------------

    def insert_recording(self, recording: Recording) -> None:
        """Insert or upsert a recording into the database.

        Args:
            recording: Recording to insert.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            self._insert_recording_with_cursor(cursor, recording)

    def _insert_recording_with_cursor(self, cursor, recording: Recording) -> None:
        """Insert or upsert a recording using an existing transaction cursor."""
        cursor.execute(
            """
            INSERT INTO recordings (
                content_hash, hash_prefix, song_id, original_filename,
                file_size_bytes, imported_at, r2_audio_url, r2_stems_url,
                r2_lrc_url, duration_seconds, tempo_bpm, musical_key,
                musical_mode, key_confidence, loudness_db, beats,
                downbeats, sections, embeddings_shape, analysis_status,
                analysis_job_id, lrc_status, lrc_job_id, visibility_status,
                download_status, created_at, updated_at, youtube_url
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (content_hash) DO UPDATE SET
                hash_prefix = EXCLUDED.hash_prefix,
                song_id = CASE WHEN recordings.song_id IS NOT NULL THEN recordings.song_id ELSE EXCLUDED.song_id END,
                original_filename = EXCLUDED.original_filename,
                file_size_bytes = EXCLUDED.file_size_bytes,
                imported_at = EXCLUDED.imported_at,
                r2_audio_url = EXCLUDED.r2_audio_url,
                r2_stems_url = EXCLUDED.r2_stems_url,
                r2_lrc_url = EXCLUDED.r2_lrc_url,
                duration_seconds = COALESCE(EXCLUDED.duration_seconds, recordings.duration_seconds),
                tempo_bpm = EXCLUDED.tempo_bpm,
                musical_key = EXCLUDED.musical_key,
                musical_mode = EXCLUDED.musical_mode,
                key_confidence = EXCLUDED.key_confidence,
                loudness_db = EXCLUDED.loudness_db,
                beats = EXCLUDED.beats,
                downbeats = EXCLUDED.downbeats,
                sections = EXCLUDED.sections,
                embeddings_shape = EXCLUDED.embeddings_shape,
                analysis_status = EXCLUDED.analysis_status,
                analysis_job_id = EXCLUDED.analysis_job_id,
                lrc_status = EXCLUDED.lrc_status,
                lrc_job_id = EXCLUDED.lrc_job_id,
                visibility_status = EXCLUDED.visibility_status,
                download_status = EXCLUDED.download_status,
                updated_at = EXCLUDED.updated_at,
                youtube_url = EXCLUDED.youtube_url,
                deleted_at = NULL
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
                recording.visibility_status,
                recording.download_status or "pending",
                recording.created_at or datetime.now().isoformat(),
                recording.updated_at or datetime.now().isoformat(),
                recording.youtube_url,
            ),
        )

    def get_recording_by_hash(
        self, hash_prefix: str, include_deleted: bool = False
    ) -> Optional[Recording]:
        """Get a recording by its hash prefix.

        Args:
            hash_prefix: The hash prefix (first 12 chars).
            include_deleted: Whether to include soft-deleted recordings.

        Returns:
            ``Recording`` or ``None`` if not found.
        """

        def _query(conn):
            cursor = conn.cursor()
            if include_deleted:
                cursor.execute(
                    f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE hash_prefix = %s",
                    (hash_prefix,),
                )
            else:
                cursor.execute(
                    f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE hash_prefix = %s AND deleted_at IS NULL",
                    (hash_prefix,),
                )
            row = cursor.fetchone()
            return Recording.from_row(tuple(row)) if row else None

        return self._execute_with_retry(_query)

    def get_recording_by_song_id(self, song_id: str) -> Optional[Recording]:
        """Get a recording by its associated song ID.

        Args:
            song_id: The song ID.

        Returns:
            ``Recording`` or ``None`` if not found.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE song_id = %s AND deleted_at IS NULL",
            (song_id,),
        )
        row = cursor.fetchone()

        if row:
            return Recording.from_row(tuple(row))
        return None

    def list_recordings_by_song_id(
        self,
        song_id: str,
        include_deleted: bool = False,
    ) -> list[Recording]:
        """List all recordings for a song."""
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE song_id = %s ORDER BY imported_at DESC",
                (song_id,),
            )
        else:
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE song_id = %s AND deleted_at IS NULL ORDER BY imported_at DESC",
                (song_id,),
            )
        return [Recording.from_row(tuple(row)) for row in cursor.fetchall()]

    def get_recordings_without_duration(self) -> list[Recording]:
        """Get all recordings where duration_seconds is NULL.

        Returns:
            List of Recording objects with NULL duration_seconds.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE duration_seconds IS NULL AND deleted_at IS NULL",
        )
        rows = cursor.fetchall()
        return [Recording.from_row(tuple(row)) for row in rows]

    def list_recordings(
        self,
        status: Optional[str] = None,
        visibility: Optional[str] = None,
        lrc_status: Optional[str] = None,
        download_status: Optional[str] = None,
        limit: Optional[int] = None,
        include_deleted: bool = False,
    ) -> list[Recording]:
        """List recordings with optional filters.

        Args:
            status: Filter by analysis status.
            visibility: Filter by visibility status. Pass "none" to match recordings with NULL visibility_status.
            lrc_status: Filter by LRC status.
            download_status: Filter by download status.
            limit: Maximum number of results.
            include_deleted: Whether to include soft-deleted recordings.

        Returns:
            List of recordings matching the filters.
        """
        cursor = self.connection.cursor()

        query = f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE 1=1"
        params: list = []

        if not include_deleted:
            query += " AND deleted_at IS NULL"

        if status:
            if status == "incomplete":
                query += (
                    " AND (analysis_status IN ('pending', 'processing', 'failed')"
                    " OR analysis_status IS NULL)"
                )
            else:
                query += " AND analysis_status = %s"
                params.append(status)

        if visibility:
            if visibility == "none":
                query += " AND visibility_status IS NULL"
            else:
                query += " AND visibility_status = %s"
                params.append(visibility)

        if lrc_status:
            if lrc_status == "incomplete":
                query += " AND lrc_status IN ('pending', 'processing', 'failed')"
            else:
                query += " AND lrc_status = %s"
                params.append(lrc_status)

        if download_status:
            if download_status == "incomplete":
                query += " AND download_status IN ('pending', 'processing', 'failed')"
            else:
                query += " AND download_status = %s"
                params.append(download_status)

        query += " ORDER BY imported_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append(Recording.from_row(tuple(row)))
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

        Uses LEFT JOIN to fetch recording and song data in a single query.

        Args:
            status: Filter by analysis status.
            visibility: Filter by visibility status. Pass "none" to match recordings with NULL visibility_status.
            lrc_status: Filter by LRC status.
            album: Filter by album name (case-insensitive substring).
            sort_by: Sort order (``album``, ``series``, ``title``, ``imported``).
            limit: Maximum number of results.
            include_deleted: Whether to include soft-deleted recordings.

        Returns:
            List of tuples ``(Recording, song_title, album_name, album_series)``.
        """
        cursor = self.connection.cursor()

        query = f"""
            SELECT {RECORDING_COLUMNS_FOR_JOIN}, s.title as song_title, s.album_name, s.album_series
            FROM recordings r
            LEFT JOIN songs s ON r.song_id = s.id
            WHERE 1=1
        """
        params: list = []

        if not include_deleted:
            query += " AND r.deleted_at IS NULL"
            query += " AND (s.deleted_at IS NULL OR s.id IS NULL)"

        if status:
            if status == "incomplete":
                query += (
                    " AND (r.analysis_status IN ('pending', 'processing', 'failed')"
                    " OR r.analysis_status IS NULL)"
                )
            else:
                query += " AND r.analysis_status = %s"
                params.append(status)

        if visibility:
            if visibility == "none":
                query += " AND r.visibility_status IS NULL"
            else:
                query += " AND r.visibility_status = %s"
                params.append(visibility)

        if lrc_status:
            if lrc_status == "incomplete":
                query += " AND r.lrc_status IN ('pending', 'processing', 'failed')"
            else:
                query += " AND r.lrc_status = %s"
                params.append(lrc_status)

        if album:
            query += " AND (s.album_name ILIKE %s OR s.album_series ILIKE %s)"
            params.extend([f"%{album}%", f"%{album}%"])

        order_map = {
            "album": "s.album_name ASC NULLS LAST, s.title ASC NULLS LAST",
            "series": "s.album_series ASC NULLS LAST, s.album_name ASC NULLS LAST, s.title ASC NULLS LAST",
            "title": "s.title ASC NULLS LAST",
            "imported": "r.imported_at DESC",
            "created": "r.created_at ASC NULLS LAST, r.hash_prefix ASC",
        }
        query += f" ORDER BY {order_map.get(sort_by, 'r.imported_at DESC')}"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            row_tuple = tuple(row)
            recording_cols = row_tuple[:-3]
            song_title = row_tuple[-3]
            album_name = row_tuple[-2]
            album_series_val = row_tuple[-1]
            recording = Recording.from_row(recording_cols)
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
            hash_prefix: The hash prefix of the recording.
            analysis_status: New analysis status.
            analysis_job_id: New analysis job ID.
            lrc_status: New LRC status.
            lrc_job_id: New LRC job ID.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            updates = []
            params: list = []

            if analysis_status:
                updates.append("analysis_status = %s")
                params.append(analysis_status)

            if analysis_job_id:
                updates.append("analysis_job_id = %s")
                params.append(analysis_job_id)

            if lrc_status:
                updates.append("lrc_status = %s")
                params.append(lrc_status)

            if lrc_job_id:
                updates.append("lrc_job_id = %s")
                params.append(lrc_job_id)

            if not updates:
                return

            params.append(hash_prefix)

            sql = f"""
                UPDATE recordings
                SET {", ".join(updates)}, updated_at = NOW()
                WHERE hash_prefix = %s
            """
            cursor.execute(sql, params)

    def get_recording_by_job_id(
        self, job_id: str, job_type: str = "analysis"
    ) -> Optional[Recording]:
        """Get a recording by its analysis or LRC job ID.

        Args:
            job_id: The job ID.
            job_type: Type of job (``analysis`` or ``lrc``).

        Returns:
            ``Recording`` or ``None`` if not found.
        """
        cursor = self.connection.cursor()
        if job_type == "analysis":
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE analysis_job_id = %s AND deleted_at IS NULL",
                (job_id,),
            )
        else:
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE lrc_job_id = %s AND deleted_at IS NULL",
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
        key_algorithm_version: Optional[str] = None,
        key_score_margin: Optional[float] = None,
        key_window_agreement: Optional[float] = None,
        key_candidates: Optional[str] = None,
        key_detected_at: Optional[str] = None,
        loudness_db: Optional[float] = None,
        beats: Optional[str] = None,
        downbeats: Optional[str] = None,
        sections: Optional[str] = None,
        embeddings_shape: Optional[str] = None,
        r2_stems_url: Optional[str] = None,
        analysis_status: Optional[str] = None,
    ) -> None:
        """Update recording with analysis results.

        Args:
            hash_prefix: The hash prefix of the recording.
            duration_seconds: Audio duration.
            tempo_bpm: Detected tempo.
            musical_key: Detected key.
            musical_mode: Detected mode.
            key_confidence: Key detection confidence.
            loudness_db: Loudness in dB.
            beats: JSON array of beat timestamps.
            downbeats: JSON array of downbeat timestamps.
            sections: JSON array of sections.
            embeddings_shape: JSON array of dimensions.
            r2_stems_url: R2 URL for stems directory.
            analysis_status: Override the analysis_status column. If None
                (default), preserves the existing behavior of setting
                'completed'. If 'partial', only fast-tier columns are written
                and full-only columns (beats, downbeats, sections,
                embeddings_shape, r2_stems_url) are PRESERVED (not nulled).
                If 'completed', all columns are written as today.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            # Resolve the effective analysis_status
            effective_status = analysis_status if analysis_status is not None else "completed"

            if effective_status == "partial":
                # Fast-tier only: preserve full-only columns on disk by
                # omitting them from the UPDATE entirely (data-loss protection).
                sql = """
                    UPDATE recordings SET
                        duration_seconds = %s,
                        tempo_bpm = %s,
                        musical_key = %s,
                        musical_mode = %s,
                        key_confidence = %s,
                        key_algorithm_version = %s,
                        key_score_margin = %s,
                        key_window_agreement = %s,
                        key_candidates = %s,
                        key_detected_at = %s,
                        loudness_db = %s,
                        analysis_status = 'partial',
                        updated_at = NOW()
                    WHERE hash_prefix = %s
                """
                cursor.execute(
                    sql,
                    (
                        duration_seconds,
                        tempo_bpm,
                        musical_key,
                        musical_mode,
                        key_confidence,
                        key_algorithm_version,
                        key_score_margin,
                        key_window_agreement,
                        key_candidates,
                        key_detected_at,
                        loudness_db,
                        hash_prefix,
                    ),
                )
            else:
                # Full tier: update all columns, set 'completed'
                sql = """
                    UPDATE recordings SET
                        duration_seconds = %s,
                        tempo_bpm = %s,
                        musical_key = %s,
                        musical_mode = %s,
                        key_confidence = %s,
                        key_algorithm_version = %s,
                        key_score_margin = %s,
                        key_window_agreement = %s,
                        key_candidates = %s,
                        key_detected_at = %s,
                        loudness_db = %s,
                        beats = %s,
                        downbeats = %s,
                        sections = %s,
                        embeddings_shape = %s,
                        r2_stems_url = COALESCE(%s, r2_stems_url),
                        analysis_status = 'completed',
                        updated_at = NOW()
                    WHERE hash_prefix = %s
                """
                cursor.execute(
                    sql,
                    (
                        duration_seconds,
                        tempo_bpm,
                        musical_key,
                        musical_mode,
                        key_confidence,
                        key_algorithm_version,
                        key_score_margin,
                        key_window_agreement,
                        key_candidates,
                        key_detected_at,
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
        visibility_status: Optional[str] = None,
    ) -> None:
        """Update recording with LRC results.

        Auto-publishes the recording when ``visibility_status`` is ``NULL`` unless
        an explicit visibility status override is provided.

        Args:
            hash_prefix: The hash prefix of the recording.
            r2_lrc_url: R2 URL for the generated LRC file.
            visibility_status: Optional visibility status to force on the recording.

        Raises:
            ValueError: If ``visibility_status`` is not valid.
        """
        if visibility_status is not None and visibility_status not in _VALID_VISIBILITY_STATUSES:
            raise ValueError(
                f"Invalid visibility_status: {visibility_status}. "
                f"Must be one of: {', '.join(sorted(_VALID_VISIBILITY_STATUSES))}"
            )

        def _query(conn):
            with conn.transaction():
                cursor = conn.cursor()
                if visibility_status is None:
                    cursor.execute(
                        """
                        UPDATE recordings SET
                            r2_lrc_url = %s,
                            lrc_status = 'completed',
                            visibility_status = COALESCE(visibility_status, 'published'),
                            updated_at = NOW()
                        WHERE hash_prefix = %s
                        """,
                        (r2_lrc_url, hash_prefix),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE recordings SET
                            r2_lrc_url = %s,
                            lrc_status = 'completed',
                            visibility_status = %s,
                            updated_at = NOW()
                        WHERE hash_prefix = %s
                        """,
                        (r2_lrc_url, visibility_status, hash_prefix),
                    )

        self._execute_with_retry(_query)

    def update_recording_download(
        self,
        hash_prefix: str,
        download_status: str,
    ) -> None:
        """Update download status for a recording.

        Args:
            hash_prefix: The hash prefix of the recording.
            download_status: New download status.

        Raises:
            ValueError: If ``download_status`` is not valid.
        """
        valid_statuses = {"pending", "processing", "completed", "failed"}
        if download_status not in valid_statuses:
            raise ValueError(
                f"Invalid download_status: {download_status}. "
                f"Must be one of: {', '.join(valid_statuses)}"
            )

        with self.transaction() as conn:
            cursor = conn.cursor()

            sql = """
                UPDATE recordings SET
                    download_status = %s,
                    updated_at = NOW()
                WHERE hash_prefix = %s
            """

            cursor.execute(sql, (download_status, hash_prefix))

    def update_recording_youtube_url(
        self,
        hash_prefix: str,
        youtube_url: Optional[str],
    ) -> None:
        """Update YouTube URL for a recording.

        Args:
            hash_prefix: The hash prefix of the recording.
            youtube_url: The YouTube URL (or None to clear it).
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            sql = """
                UPDATE recordings SET
                    youtube_url = %s,
                    updated_at = NOW()
                WHERE hash_prefix = %s
            """

            cursor.execute(sql, (youtube_url, hash_prefix))

    def update_recording_duration(
        self,
        hash_prefix: str,
        duration_seconds: float,
    ) -> None:
        """Update duration_seconds for a recording.

        Args:
            hash_prefix: The hash prefix of the recording.
            duration_seconds: The probed audio duration in seconds.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE recordings SET
                    duration_seconds = %s,
                    updated_at = NOW()
                WHERE hash_prefix = %s
                """,
                (duration_seconds, hash_prefix),
            )

    def update_recording_r2_url(
        self,
        hash_prefix: str,
        r2_audio_url: str,
    ) -> None:
        """Update r2_audio_url for a recording.

        Args:
            hash_prefix: The hash prefix of the recording.
            r2_audio_url: The R2 URL for the audio file.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE recordings SET
                    r2_audio_url = %s,
                    updated_at = NOW()
                WHERE hash_prefix = %s
                """,
                (r2_audio_url, hash_prefix),
            )

    def update_recording_visibility(
        self,
        hash_prefix: str,
        visibility_status: str,
    ) -> bool:
        """Update recording visibility status.

        Args:
            hash_prefix: The hash prefix of the recording.
            visibility_status: New visibility status.

        Returns:
            True if recording was updated, False if not found.

        Raises:
            ValueError: If ``visibility_status`` is not valid.
        """
        if visibility_status not in _VALID_VISIBILITY_STATUSES:
            raise ValueError(
                f"Invalid visibility_status: {visibility_status}. "
                f"Must be one of: {', '.join(sorted(_VALID_VISIBILITY_STATUSES))}"
            )

        with self.transaction() as conn:
            cursor = conn.cursor()

            sql = """
                UPDATE recordings SET
                    visibility_status = %s,
                    updated_at = NOW()
                WHERE hash_prefix = %s
            """

            cursor.execute(sql, (visibility_status, hash_prefix))
            return cursor.rowcount > 0

    def delete_recording(self, hash_prefix: str) -> None:
        """Soft-delete a recording by hash_prefix.

        Args:
            hash_prefix: The hash prefix of the recording to delete.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE recordings SET deleted_at = NOW() WHERE hash_prefix = %s",
                (hash_prefix,),
            )

    def soft_delete_song(self, song_id: str) -> bool:
        """Soft-delete a song by ID.

        Args:
            song_id: The song ID to soft-delete.

        Returns:
            True if song was marked as deleted, False if not found.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE songs SET deleted_at = NOW() WHERE id = %s", (song_id,))
            return cursor.rowcount > 0

    def hold_recordings_for_song(self, song_id: str) -> int:
        """Set active recordings for a song to hold visibility."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE recordings
                SET visibility_status = 'hold', updated_at = NOW()
                WHERE song_id = %s AND deleted_at IS NULL
                """,
                (song_id,),
            )
            return cursor.rowcount

    def list_deleted_songs(self) -> list[Song]:
        """List all soft-deleted songs.

        Returns:
            List of soft-deleted songs.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        )
        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    def list_deleted_recordings(self) -> list[Recording]:
        """List all soft-deleted recordings.

        Returns:
            List of soft-deleted recordings.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
        )
        results = []
        for row in cursor.fetchall():
            results.append(Recording.from_row(tuple(row)))
        return results

    def restore_song(self, song_id: str) -> bool:
        """Restore a soft-deleted song.

        Args:
            song_id: The song ID to restore.

        Returns:
            True if song was restored, False if not found.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE songs SET deleted_at = NULL WHERE id = %s", (song_id,))
            return cursor.rowcount > 0

    def restore_recordings_visibility_for_song(self, song_id: str) -> int:
        """Restore held recordings to 'review' visibility after song restore.

        Resets all non-deleted recordings for a restored song that are still
        on 'hold' visibility back to 'review' so they re-enter the user-app
        review queue. Recordings already in 'published' or 'review' are left
        untouched, and independently soft-deleted recordings are skipped.

        Args:
            song_id: The restored song's ID.

        Returns:
            Number of recordings updated.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE recordings
                SET visibility_status = 'review', updated_at = NOW()
                WHERE song_id = %s
                  AND visibility_status = 'hold'
                  AND deleted_at IS NULL
                """,
                (song_id,),
            )
            return cursor.rowcount

    def count_held_recordings_for_song(self, song_id: str) -> int:
        """Count non-deleted recordings currently held at 'hold' visibility.

        Used for dry-run previews of song restore.

        Args:
            song_id: The song ID to count held recordings for.

        Returns:
            Number of held, non-deleted recordings for the song.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM recordings
            WHERE song_id = %s
              AND visibility_status = 'hold'
              AND deleted_at IS NULL
            """,
            (song_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def count_songset_references(self, song_id: str) -> int:
        """Count songset item references to a song."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM songset_items WHERE song_id = %s", (song_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def restore_recording(self, hash_prefix: str) -> bool:
        """Restore a soft-deleted recording.

        Args:
            hash_prefix: The hash prefix of the recording to restore.

        Returns:
            True if recording was restored, False if not found.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE recordings SET deleted_at = NULL WHERE hash_prefix = %s", (hash_prefix,)
            )
            return cursor.rowcount > 0

    def count_active_recordings_by_song(self, song_id: str) -> int:
        """Count active recordings for a song."""
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM recordings WHERE song_id = %s AND deleted_at IS NULL",
            (song_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def list_active_recordings_by_song(self, song_id: str) -> list[Recording]:
        """List active recordings for a song using deterministic ordering."""
        cursor = self.connection.cursor()
        cursor.execute(
            f"""
            SELECT {RECORDING_COLUMNS_SELECT} FROM recordings
            WHERE song_id = %s AND deleted_at IS NULL
            ORDER BY imported_at DESC, hash_prefix ASC
            """,
            (song_id,),
        )
        return [Recording.from_row(tuple(row)) for row in cursor.fetchall()]

    def count_recording_songset_references(self, hash_prefix: str) -> int:
        """Count songset item references to a recording hash."""
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM songset_items WHERE recording_hash_prefix = %s",
            (hash_prefix,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def count_recordings_for_song(self, song_id: str) -> int:
        """Count all recordings for a song, active and soft-deleted."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM recordings WHERE song_id = %s", (song_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def recording_row_exists(self, hash_prefix: str) -> bool:
        """Return whether any recording row exists for a hash prefix."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT 1 FROM recordings WHERE hash_prefix = %s", (hash_prefix,))
        return cursor.fetchone() is not None

    def replace_recording_after_import(self, old_hash_prefix: str, recording: Recording) -> int:
        """Persist replacement, update songsets, and soft-delete old recording atomically."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT hash_prefix FROM recordings WHERE hash_prefix = %s AND deleted_at IS NULL FOR UPDATE",
                (old_hash_prefix,),
            )
            if cursor.fetchone() is None:
                raise ValueError(f"Active recording not found: {old_hash_prefix}")
            if recording.hash_prefix == old_hash_prefix:
                self._insert_recording_with_cursor(cursor, recording)
                return 0
            self._insert_recording_with_cursor(cursor, recording)
            cursor.execute(
                """
                UPDATE songset_items
                SET recording_hash_prefix = %s
                WHERE recording_hash_prefix = %s
                """,
                (recording.hash_prefix, old_hash_prefix),
            )
            updated_items = cursor.rowcount
            cursor.execute(
                "UPDATE recordings SET deleted_at = NOW() WHERE hash_prefix = %s AND deleted_at IS NULL",
                (old_hash_prefix,),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Could not soft-delete old recording: {old_hash_prefix}")
            return updated_items

    def update_songset_items_recording_hash(
        self, old_hash_prefix: str, new_hash_prefix: str
    ) -> int:
        """Update songset item recording hashes in a batch."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE songset_items
                SET recording_hash_prefix = %s
                WHERE recording_hash_prefix = %s
                """,
                (new_hash_prefix, old_hash_prefix),
            )
            return cursor.rowcount

    def list_soft_deleted_songs_with_counts(self, limit: Optional[int] = None) -> list[dict]:
        """List soft-deleted songs with recording and songset reference counts."""
        cursor = self.connection.cursor()
        sql = """
            SELECT s.*, COUNT(DISTINCT r.content_hash) AS recording_count,
                   COUNT(DISTINCT si.id) AS songset_reference_count
            FROM songs s
            LEFT JOIN recordings r ON r.song_id = s.id
            LEFT JOIN songset_items si ON si.song_id = s.id
            WHERE s.deleted_at IS NOT NULL
            GROUP BY s.id
            ORDER BY s.deleted_at DESC, s.id ASC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cursor.execute(sql)
        results = []
        for row in cursor.fetchall():
            values = tuple(row)
            results.append(
                {
                    "song": Song.from_row(values[:SONG_COLUMN_COUNT]),
                    "recording_count": int(values[SONG_COLUMN_COUNT]),
                    "songset_reference_count": int(values[SONG_COLUMN_COUNT + 1]),
                }
            )
        return results

    def list_soft_deleted_recordings_with_counts(self, limit: Optional[int] = None) -> list[dict]:
        """List soft-deleted recordings with songset reference counts."""
        cursor = self.connection.cursor()
        sql = f"""
            SELECT {RECORDING_COLUMNS_FOR_JOIN}, COUNT(si.id) AS songset_reference_count
            FROM recordings r
            LEFT JOIN songset_items si ON si.recording_hash_prefix = r.hash_prefix
            WHERE r.deleted_at IS NOT NULL
            GROUP BY r.content_hash
            ORDER BY r.deleted_at DESC, r.hash_prefix ASC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cursor.execute(sql)
        results = []
        for row in cursor.fetchall():
            values = tuple(row)
            results.append(
                {
                    "recording": Recording.from_row(values[:RECORDING_COLUMN_COUNT]),
                    "songset_reference_count": int(values[RECORDING_COLUMN_COUNT]),
                }
            )
        return results

    def hard_delete_soft_deleted_recording(self, hash_prefix: str) -> bool:
        """Hard-delete a soft-deleted recording after locking and reference checks."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT hash_prefix FROM recordings
                WHERE hash_prefix = %s AND deleted_at IS NOT NULL
                FOR UPDATE
                """,
                (hash_prefix,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "SELECT COUNT(*) FROM songset_items WHERE recording_hash_prefix = %s",
                (hash_prefix,),
            )
            if int(cursor.fetchone()[0]) > 0:
                raise ValueError(f"Recording {hash_prefix} is still referenced by songset_items")
            cursor.execute(
                "DELETE FROM recordings WHERE hash_prefix = %s AND deleted_at IS NOT NULL",
                (hash_prefix,),
            )
            return cursor.rowcount > 0

    def hard_delete_soft_deleted_song(self, song_id: str) -> bool:
        """Hard-delete a soft-deleted song with no recordings or songset references."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM songs WHERE id = %s AND deleted_at IS NOT NULL FOR UPDATE",
                (song_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute("SELECT COUNT(*) FROM recordings WHERE song_id = %s", (song_id,))
            if int(cursor.fetchone()[0]) > 0:
                raise ValueError(f"Song {song_id} still has recordings")
            cursor.execute("SELECT COUNT(*) FROM songset_items WHERE song_id = %s", (song_id,))
            if int(cursor.fetchone()[0]) > 0:
                raise ValueError(f"Song {song_id} is still referenced by songset_items")
            cursor.execute("DELETE FROM songs WHERE id = %s AND deleted_at IS NOT NULL", (song_id,))
            return cursor.rowcount > 0

    def is_recording_song_soft_deleted(self, hash_prefix: str) -> bool:
        """Return true when a recording's parent song is soft-deleted."""
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT s.deleted_at IS NOT NULL
            FROM recordings r
            JOIN songs s ON s.id = r.song_id
            WHERE r.hash_prefix = %s
            """,
            (hash_prefix,),
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False

    def find_stale_songset_items(
        self,
        songset_id: Optional[str] = None,
        hash_prefix: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Find songset items pointing at missing or soft-deleted recordings."""
        cursor = self.connection.cursor()
        params: list = []
        sql = """
            SELECT si.id, si.songset_id, si.song_id, si.recording_hash_prefix,
                   s.title, r.deleted_at, r.hash_prefix
            FROM songset_items si
            LEFT JOIN songs s ON s.id = si.song_id
            LEFT JOIN recordings r ON r.hash_prefix = si.recording_hash_prefix
            WHERE si.recording_hash_prefix IS NOT NULL
              AND (r.hash_prefix IS NULL OR r.deleted_at IS NOT NULL)
        """
        if songset_id:
            sql += " AND si.songset_id = %s"
            params.append(songset_id)
        if hash_prefix:
            sql += " AND si.recording_hash_prefix = %s"
            params.append(hash_prefix)
        sql += " ORDER BY si.songset_id, si.id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        cursor.execute(sql, params)
        rows = []
        for row in cursor.fetchall():
            rows.append(
                {
                    "item_id": row[0],
                    "songset_id": row[1],
                    "song_id": row[2],
                    "old_hash": row[3],
                    "song_title": row[4],
                    "reason": "missing-row" if row[6] is None else "soft-deleted-row",
                }
            )
        return rows

    def find_songsets_needing_repair(self, limit: Optional[int] = 20) -> list[dict]:
        """Find songsets that have at least one stale songset item.

        Returns one row per songset with: songset_id, name, created_at,
        song_count (total items in songset), user_email.
        """
        cursor = self.connection.cursor()
        sql = """
            SELECT s.id, s.name, s.created_at,
                   (SELECT COUNT(*) FROM songset_items si2
                    WHERE si2.songset_id = s.id) AS song_count,
                   u.email
            FROM songsets s
            LEFT JOIN "user" u ON u.id = s.user_id
            WHERE EXISTS (
                SELECT 1 FROM songset_items si
                LEFT JOIN recordings r ON r.hash_prefix = si.recording_hash_prefix
                WHERE si.songset_id = s.id
                  AND si.recording_hash_prefix IS NOT NULL
                  AND (r.hash_prefix IS NULL OR r.deleted_at IS NOT NULL)
            )
            ORDER BY s.created_at DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        cursor.execute(sql)
        return [
            {
                "songset_id": row[0],
                "name": row[1],
                "created_at": to_str(row[2]),
                "song_count": row[3],
                "user_email": row[4] or "",
            }
            for row in cursor.fetchall()
        ]

    def find_replacement_recording_candidates(self, song_id: str) -> list[Recording]:
        """Find active recording candidates for a stale songset item."""
        cursor = self.connection.cursor()
        cursor.execute(
            f"""
            SELECT {RECORDING_COLUMNS_SELECT} FROM recordings
            WHERE song_id = %s AND deleted_at IS NULL
            ORDER BY
                CASE WHEN visibility_status = 'published' THEN 0 ELSE 1 END,
                CASE WHEN lrc_status = 'completed' THEN 0 ELSE 1 END,
                CASE WHEN analysis_status = 'completed' THEN 0 ELSE 1 END,
                imported_at DESC,
                hash_prefix ASC
            """,
            (song_id,),
        )
        return [Recording.from_row(tuple(row)) for row in cursor.fetchall()]

    def find_active_render_jobs_for_songsets(self, songset_ids: list[str]) -> list[dict]:
        """Find queued/running render jobs for affected songsets."""
        if not songset_ids:
            return []
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                SELECT id, songset_id, status
                FROM render_jobs
                WHERE songset_id = ANY(%s) AND status IN ('queued', 'running')
                ORDER BY created_at DESC
                """,
                (songset_ids,),
            )
        except psycopg.errors.UndefinedTable:
            self.connection.rollback()
            return []
        return [{"id": row[0], "songset_id": row[1], "status": row[2]} for row in cursor.fetchall()]

    def render_jobs_table_exists(self) -> bool:
        """Return whether the optional render_jobs table exists."""
        cursor = self.connection.cursor()
        cursor.execute("SELECT to_regclass('public.render_jobs')")
        row = cursor.fetchone()
        return bool(row and row[0])

    def repair_songset_items(self, replacements: list[tuple[str, str, str]]) -> int:
        """Apply stale songset item repairs after locking affected songsets/jobs."""
        if not replacements:
            return 0
        songset_ids = sorted({songset_id for _, songset_id, _ in replacements})
        active_jobs = self.find_active_render_jobs_for_songsets(songset_ids)
        if active_jobs:
            raise ValueError("Affected songsets have queued or running render jobs")
        has_render_jobs = self.render_jobs_table_exists()
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM songsets WHERE id = ANY(%s) FOR UPDATE", (songset_ids,))
            if has_render_jobs:
                cursor.execute(
                    """
                    SELECT id FROM render_jobs
                    WHERE songset_id = ANY(%s) AND status IN ('queued', 'running')
                    FOR UPDATE
                    """,
                    (songset_ids,),
                )
                if cursor.fetchall():
                    raise ValueError("Affected songsets have queued or running render jobs")
            updated = 0
            for item_id, _songset_id, new_hash in replacements:
                cursor.execute(
                    "UPDATE songset_items SET recording_hash_prefix = %s WHERE id = %s",
                    (new_hash, item_id),
                )
                updated += cursor.rowcount
            return updated

    def find_failed_render_jobs(
        self,
        job_id: Optional[str] = None,
        since_days: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Find failed render jobs for diagnosis."""
        cursor = self.connection.cursor()
        params: list = []
        sql = """
            SELECT id, songset_id, status, error_message, created_at, updated_at
            FROM render_jobs
            WHERE status = 'failed'
        """
        if job_id:
            sql += " AND id = %s"
            params.append(job_id)
        if since_days:
            sql += " AND created_at >= NOW() - (%s * INTERVAL '1 day')"
            params.append(since_days)
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        try:
            cursor.execute(sql, params)
        except psycopg.errors.UndefinedTable:
            self.connection.rollback()
            return []
        return [
            {
                "job_id": row[0],
                "songset_id": row[1],
                "status": row[2],
                "error_message": row[3],
                "created_at": to_str(row[4]),
                "updated_at": to_str(row[5]),
            }
            for row in cursor.fetchall()
        ]

    def upsert_song_embedding(
        self, song_id: str, embedding: list[float], model_version: str, content_hash: str
    ) -> None:
        """Insert or update a song embedding.

        Args:
            song_id: Song ID (primary key).
            embedding: Embedding vector as list of floats.
            model_version: Model version string.
            content_hash: Content hash for staleness detection.
        """
        import json

        emb_str = json.dumps(embedding)
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO song_embedding (song_id, embedding, model_version, content_hash)
                VALUES (%s, %s::vector, %s, %s)
                ON CONFLICT (song_id) DO UPDATE
                SET embedding = EXCLUDED.embedding,
                    model_version = EXCLUDED.model_version,
                    content_hash = EXCLUDED.content_hash
                """,
                (song_id, emb_str, model_version, content_hash),
            )

    def upsert_song_line_embeddings(
        self, song_id: str, model_version: str, line_embeddings: list[dict]
    ) -> None:
        """Replace all line embeddings for a song.

        Args:
            song_id: Song ID.
            model_version: Model version string.
            line_embeddings: List of dicts with keys: line_index, line_text, embedding.
        """
        import json

        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM song_line_embedding WHERE song_id = %s", (song_id,))
            if not line_embeddings:
                return
            values = []
            for le in line_embeddings:
                emb_str = json.dumps(le["embedding"])
                values.append((song_id, le["line_index"], le["line_text"], emb_str, model_version))
            cursor.executemany(
                """
                INSERT INTO song_line_embedding (song_id, line_index, line_text, embedding, model_version)
                VALUES (%s, %s, %s, %s::vector, %s)
                """,
                values,
            )

    def get_songs_without_embeddings(self) -> list[Song]:
        """Get songs that have no embedding, have non-empty lyrics, and have
        at least one published recording (i.e. visible in webapp Browse Song).

        Returns:
            List of Song objects without embeddings.
        """
        cursor = self.connection.cursor()
        cursor.execute(f"""
            SELECT {SONG_COLUMNS_FOR_JOIN}
            FROM songs s
            LEFT JOIN song_embedding se ON s.id = se.song_id
            WHERE se.song_id IS NULL
              AND s.deleted_at IS NULL
              AND s.lyrics_raw IS NOT NULL
              AND s.lyrics_raw != ''
              AND EXISTS (
                  SELECT 1 FROM recordings r
                  WHERE r.song_id = s.id
                    AND r.visibility_status = 'published'
                    AND r.deleted_at IS NULL
              )
            """)
        return [Song.from_row(tuple(row)) for row in cursor.fetchall()]

    def get_all_songs_with_lyrics(self) -> list[Song]:
        """Get all non-deleted songs that have non-empty lyrics and at least
        one published recording (i.e. visible in webapp Browse Song).

        Returns:
            List of Song objects with lyrics and published recordings.
        """
        cursor = self.connection.cursor()
        cursor.execute(f"""
            SELECT {SONG_COLUMNS_FOR_JOIN}
            FROM songs s
            WHERE s.deleted_at IS NULL
              AND s.lyrics_raw IS NOT NULL
              AND s.lyrics_raw != ''
              AND EXISTS (
                  SELECT 1 FROM recordings r
                  WHERE r.song_id = s.id
                    AND r.visibility_status = 'published'
                    AND r.deleted_at IS NULL
              )
            """)
        return [Song.from_row(tuple(row)) for row in cursor.fetchall()]

    def get_embedding_content_hash(self, song_id: str) -> Optional[str]:
        """Get the content hash of an existing song embedding.

        Args:
            song_id: Song ID.

        Returns:
            Content hash string, or None if no embedding exists.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT content_hash FROM song_embedding WHERE song_id = %s",
            (song_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None
