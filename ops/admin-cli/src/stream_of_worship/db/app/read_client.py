"""Read-only database client for catalog tables.

Provides read-only access to songs and recordings tables.  Uses ``psycopg``
via a shared ``ConnectionProvider``.
"""

import logging
from typing import Callable, Optional, TypeVar

import psycopg
import psycopg.rows

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.db.schema import RECORDING_COLUMNS_SELECT, SONG_COLUMNS_SELECT
from stream_of_worship.db.connection import ConnectionProvider

logger = logging.getLogger("sow_app.db")

T = TypeVar("T")


class DatabaseError(Exception):
    """User-facing database error with a friendly message."""


class ReadOnlyClient:
    """Read-only client for songs and recordings tables.

    This client provides read access to the catalog.  Write restrictions are
    enforced at the Postgres role level, not in code.

    Attributes:
        connection_provider: ``ConnectionProvider`` instance.
    """

    def __init__(self, connection_provider: ConnectionProvider):
        """Initialize the read-only client.

        Args:
            connection_provider: ``ConnectionProvider`` wrapping the DSN.
        """
        self.connection_provider = connection_provider

    @property
    def connection(self) -> psycopg.Connection:
        """Get the current psycopg connection from the provider."""
        return self.connection_provider.get_connection()

    def _execute_with_retry(self, fn: Callable[[psycopg.Connection], T]) -> T:
        """Run fn(conn); on OperationalError, invalidate and retry once."""
        try:
            return fn(self.connection)
        except psycopg.OperationalError:
            self.connection_provider.invalidate()
            return fn(self.connection)

    def close(self) -> None:
        """Close the underlying connection via the provider."""
        self.connection_provider.close()

    def __enter__(self) -> "ReadOnlyClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def check_connection(self) -> bool:
        """Verify the database connection is alive.

        Returns:
            True if the connection is healthy.
        """
        try:
            self.connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Song operations (read-only, deleted-aware)
    # ------------------------------------------------------------------

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

    def get_song_including_deleted(self, song_id: str) -> Optional[Song]:
        """Get a song by ID, including soft-deleted songs.

        Useful for displaying orphaned songset items.

        Args:
            song_id: The song ID.

        Returns:
            ``Song`` or ``None`` if not found.
        """
        return self.get_song(song_id, include_deleted=True)

    def list_songs(
        self,
        album: Optional[str] = None,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> list[Song]:
        """List songs with optional filters.

        Args:
            album: Filter by album name.
            key: Filter by musical key.
            limit: Maximum number of results.
            offset: Number of results to skip.
            include_deleted: Whether to include soft-deleted songs.

        Returns:
            List of songs matching the filters.
        """
        cursor = self.connection.cursor()

        query = f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE 1=1"
        params: list = []

        if not include_deleted:
            query += " AND deleted_at IS NULL"

        if album:
            query += " AND album_name = %s"
            params.append(album)

        if key:
            query += " AND musical_key = %s"
            params.append(key)

        query += " ORDER BY title"

        if limit:
            query += f" LIMIT {limit}"

        if offset:
            query += f" OFFSET {offset}"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    def search_songs(
        self, query: str, field: str = "all", limit: int = 20, include_deleted: bool = False
    ) -> list[Song]:
        """Search songs by query.

        Args:
            query: Search query string.
            field: Field to search (``title``, ``lyrics``, ``composer``, ``all``).
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
        else:  # all
            sql = f"""
                SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE {deleted_clause}(
                title ILIKE %s OR title_pinyin ILIKE %s OR
                lyrics_raw ILIKE %s OR composer ILIKE %s OR lyricist ILIKE %s)
            """
            params = [search_pattern] * 5

        sql += f" ORDER BY title LIMIT {limit}"

        cursor.execute(sql, params)

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    def list_albums(self) -> list[str]:
        """List all unique album names.

        Returns:
            List of album names (excluding deleted songs).
        """
        cursor = self.connection.cursor()
        cursor.execute("""SELECT DISTINCT album_name FROM songs
            WHERE album_name IS NOT NULL AND deleted_at IS NULL
            ORDER BY album_name""")
        return [row[0] for row in cursor.fetchall() if row[0]]

    def list_keys(self) -> list[str]:
        """List all unique musical keys.

        Returns:
            List of key names (excluding deleted songs).
        """
        cursor = self.connection.cursor()
        cursor.execute("""SELECT DISTINCT musical_key FROM songs
            WHERE musical_key IS NOT NULL AND deleted_at IS NULL
            ORDER BY musical_key""")
        return [row[0] for row in cursor.fetchall() if row[0]]

    # ------------------------------------------------------------------
    # Recording operations (read-only, deleted-aware)
    # ------------------------------------------------------------------

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
        cursor = self.connection.cursor()
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

        if row:
            return Recording.from_row(tuple(row))
        return None

    def get_recording_by_song_id(
        self, song_id: str, include_deleted: bool = False
    ) -> Optional[Recording]:
        """Get a recording by its associated song ID.

        Args:
            song_id: The song ID.
            include_deleted: Whether to include soft-deleted recordings.

        Returns:
            ``Recording`` or ``None`` if not found.
        """
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE song_id = %s",
                (song_id,),
            )
        else:
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE song_id = %s AND deleted_at IS NULL",
                (song_id,),
            )
        row = cursor.fetchone()

        if row:
            return Recording.from_row(tuple(row))
        return None

    def list_recordings(
        self,
        status: Optional[str] = None,
        has_analysis: bool = False,
        limit: Optional[int] = None,
        include_deleted: bool = False,
    ) -> list[Recording]:
        """List recordings with optional filters.

        Args:
            status: Filter by analysis status.
            has_analysis: Only return recordings with completed analysis.
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
            query += " AND analysis_status = %s"
            params.append(status)

        if has_analysis:
            query += " AND analysis_status = 'completed'"

        query += " ORDER BY imported_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            results.append(Recording.from_row(tuple(row)))
        return results

    def get_recording_count(self) -> int:
        """Get total number of active recordings.

        Returns:
            Total count (excluding soft-deleted).
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM recordings WHERE deleted_at IS NULL")
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_analyzed_recording_count(self) -> int:
        """Get number of recordings with completed analysis.

        Returns:
            Count of analyzed recordings (excluding soft-deleted).
        """
        cursor = self.connection.cursor()
        cursor.execute("""SELECT COUNT(*) FROM recordings
            WHERE analysis_status = 'completed' AND deleted_at IS NULL""")
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_song_count(self) -> int:
        """Get total number of active songs.

        Returns:
            Total count (excluding soft-deleted).
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM songs WHERE deleted_at IS NULL")
        result = cursor.fetchone()
        count = result[0] if result else 0
        logger.debug(f"Total songs in database: {count}")
        return count

    def get_lrc_ready_count(self) -> int:
        """Get number of songs with LRC ready (completed + published).

        Returns:
            Count of LRC-ready songs.
        """
        cursor = self.connection.cursor()
        cursor.execute("""SELECT COUNT(*) FROM songs s
            JOIN recordings r ON s.id = r.song_id
            WHERE r.lrc_status = 'completed' AND r.visibility_status = 'published'
            AND r.deleted_at IS NULL AND s.deleted_at IS NULL""")
        result = cursor.fetchone()
        count = result[0] if result else 0
        logger.debug(f"Songs with LRC ready: {count}")
        return count

    # ------------------------------------------------------------------
    # Batch query methods (Fix 10 — eliminate N+1 patterns)
    # ------------------------------------------------------------------

    def get_recordings_by_song_ids(self, song_ids: list[str]) -> dict[str, Recording]:
        """Batch-fetch recordings by song IDs.

        Args:
            song_ids: List of song IDs to look up.

        Returns:
            Dict keyed by song_id.
        """
        if not song_ids:
            return {}

        def _fetch(conn: psycopg.Connection) -> dict[str, Recording]:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE song_id = ANY(%s) AND deleted_at IS NULL",
                (song_ids,),
            )
            # song_id is at index 2 in the recordings table schema
            return {row[2]: Recording.from_row(tuple(row)) for row in cursor.fetchall()}

        try:
            return self._execute_with_retry(_fetch)
        except psycopg.OperationalError as e:
            raise DatabaseError(f"Failed to fetch recordings: {e}") from e

    def get_recordings_by_hashes(
        self, hash_prefixes: list[str], include_deleted: bool = False
    ) -> dict[str, Recording]:
        """Batch-fetch recordings by hash prefixes.

        Args:
            hash_prefixes: List of hash prefixes to look up.
            include_deleted: Whether to include soft-deleted recordings.

        Returns:
            Dict keyed by hash_prefix.
        """
        if not hash_prefixes:
            return {}

        def _fetch(conn: psycopg.Connection) -> dict[str, Recording]:
            cursor = conn.cursor()
            if include_deleted:
                cursor.execute(
                    f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE hash_prefix = ANY(%s)",
                    (hash_prefixes,),
                )
            else:
                cursor.execute(
                    f"SELECT {RECORDING_COLUMNS_SELECT} FROM recordings WHERE hash_prefix = ANY(%s) AND deleted_at IS NULL",
                    (hash_prefixes,),
                )
            return {row[1]: Recording.from_row(tuple(row)) for row in cursor.fetchall()}

        try:
            return self._execute_with_retry(_fetch)
        except psycopg.OperationalError as e:
            raise DatabaseError(f"Failed to fetch recordings: {e}") from e

    def get_songs_by_ids(
        self, song_ids: list[str], include_deleted: bool = False
    ) -> dict[str, Song]:
        """Batch-fetch songs by IDs.

        Args:
            song_ids: List of song IDs to look up.
            include_deleted: Whether to include soft-deleted songs.

        Returns:
            Dict keyed by song id.
        """
        if not song_ids:
            return {}

        def _fetch(conn: psycopg.Connection) -> dict[str, Song]:
            cursor = conn.cursor()
            if include_deleted:
                cursor.execute(
                    f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE id = ANY(%s)",
                    (song_ids,),
                )
            else:
                cursor.execute(
                    f"SELECT {SONG_COLUMNS_SELECT} FROM songs WHERE id = ANY(%s) AND deleted_at IS NULL",
                    (song_ids,),
                )
            return {row[0]: Song.from_row(tuple(row)) for row in cursor.fetchall()}

        try:
            return self._execute_with_retry(_fetch)
        except psycopg.OperationalError as e:
            raise DatabaseError(f"Failed to fetch songs: {e}") from e
