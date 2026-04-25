"""Read-only database client for admin tables.

Provides read-only access to songs and recordings tables managed by the admin CLI.
Supports libsql/Turso embedded replicas for sync with the cloud database.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Union

from stream_of_worship.admin.db.models import Recording, Song

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


class ReadOnlyClient:
    """Read-only client for admin songs and recordings tables.

    This client provides read-only access to the admin-managed tables.
    It supports both standard SQLite and libsql/Turso embedded replicas.

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
        """Initialize the read-only client.

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

            self._migrate_schema()

        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def _migrate_schema(self) -> None:
        """Run schema migrations for the read-only catalog replica."""
        cursor = self._connection.cursor()
        for table in ("songs", "recordings"):
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMP")
            except Exception:
                pass

    def sync(self) -> None:
        """Sync with Turso cloud database.

        Raises:
            SyncError: If sync fails or Turso is not configured
        """
        if not self.is_turso_enabled:
            raise SyncError("Turso sync is not configured")

        try:
            conn = self.connection
            conn.sync()  # type: ignore
        except Exception as e:
            raise SyncError(f"Sync failed: {e}", cause=e)

    def __enter__(self) -> "ReadOnlyClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    # Song operations (read-only, deleted-aware)

    def get_song(self, song_id: str, include_deleted: bool = False) -> Optional[Song]:
        """Get a song by ID.

        Args:
            song_id: The song ID
            include_deleted: Whether to include soft-deleted songs

        Returns:
            Song or None if not found
        """
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
        else:
            cursor.execute("SELECT * FROM songs WHERE id = ? AND deleted_at IS NULL", (song_id,))
        row = cursor.fetchone()

        if row:
            return Song.from_row(tuple(row))
        return None

    def get_song_including_deleted(self, song_id: str) -> Optional[Song]:
        """Get a song by ID, including soft-deleted songs.

        This is useful for displaying orphaned songset items.

        Args:
            song_id: The song ID

        Returns:
            Song or None if not found (including soft-deleted)
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
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            offset: Number of results to skip
            include_deleted: Whether to include soft-deleted songs

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
            query: Search query string
            field: Field to search (title, lyrics, composer, all)
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
        else:  # all
            sql = f"""
                SELECT * FROM songs WHERE {deleted_clause}(
                title LIKE ? OR title_pinyin LIKE ? OR
                lyrics_raw LIKE ? OR composer LIKE ? OR lyricist LIKE ?)
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
            List of album names (excluding deleted songs)
        """
        cursor = self.connection.cursor()
        cursor.execute(
            """SELECT DISTINCT album_name FROM songs
            WHERE album_name IS NOT NULL AND deleted_at IS NULL
            ORDER BY album_name"""
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def list_keys(self) -> list[str]:
        """List all unique musical keys.

        Returns:
            List of key names (excluding deleted songs)
        """
        cursor = self.connection.cursor()
        cursor.execute(
            """SELECT DISTINCT musical_key FROM songs
            WHERE musical_key IS NOT NULL AND deleted_at IS NULL
            ORDER BY musical_key"""
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    # Recording operations (read-only, deleted-aware)

    def get_recording_by_hash(
        self, hash_prefix: str, include_deleted: bool = False
    ) -> Optional[Recording]:
        """Get a recording by its hash prefix.

        Args:
            hash_prefix: The hash prefix (first 12 chars)
            include_deleted: Whether to include soft-deleted recordings

        Returns:
            Recording or None if not found
        """
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute(
                "SELECT * FROM recordings WHERE hash_prefix = ?",
                (hash_prefix,),
            )
        else:
            cursor.execute(
                "SELECT * FROM recordings WHERE hash_prefix = ? AND deleted_at IS NULL",
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
            song_id: The song ID
            include_deleted: Whether to include soft-deleted recordings

        Returns:
            Recording or None if not found
        """
        cursor = self.connection.cursor()
        if include_deleted:
            cursor.execute(
                "SELECT * FROM recordings WHERE song_id = ?",
                (song_id,),
            )
        else:
            cursor.execute(
                "SELECT * FROM recordings WHERE song_id = ? AND deleted_at IS NULL",
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
            status: Filter by analysis status
            has_analysis: Only return recordings with completed analysis
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
            query += " AND analysis_status = ?"
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
            Total count (excluding soft-deleted)
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM recordings WHERE deleted_at IS NULL")
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_analyzed_recording_count(self) -> int:
        """Get number of recordings with completed analysis.

        Returns:
            Count of analyzed recordings (excluding soft-deleted)
        """
        cursor = self.connection.cursor()
        cursor.execute(
            """SELECT COUNT(*) FROM recordings
            WHERE analysis_status = 'completed' AND deleted_at IS NULL"""
        )
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_song_count(self) -> int:
        """Get total number of active songs.

        Returns:
            Total count (excluding soft-deleted)
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM songs WHERE deleted_at IS NULL")
        result = cursor.fetchone()
        return result[0] if result else 0
