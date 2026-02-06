"""Read-only database client for admin tables.

Provides read-only access to songs and recordings tables managed by the admin CLI.
This client does not modify admin data - it only reads for display and selection.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from stream_of_worship.admin.db.models import Recording, Song


class ReadOnlyClient:
    """Read-only client for admin songs and recordings tables.

    This client provides read-only access to the admin-managed tables.
    It does not create the schema or modify any data.

    Attributes:
        db_path: Path to the SQLite database file
        connection: Active database connection
    """

    def __init__(self, db_path: Path):
        """Initialize the read-only client.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Get or create database connection.

        Returns:
            Active SQLite connection
        """
        if self._connection is None:
            self._connection = sqlite3.connect(
                self.db_path,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self._connection.row_factory = sqlite3.Row

        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "ReadOnlyClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    # Song operations (read-only)

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
        offset: int = 0,
    ) -> list[Song]:
        """List songs with optional filters.

        Args:
            album: Filter by album name
            key: Filter by musical key
            limit: Maximum number of results
            offset: Number of results to skip

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

        sql += f" ORDER BY title LIMIT {limit}"

        cursor.execute(sql, params)

        results = []
        for row in cursor.fetchall():
            results.append(Song.from_row(tuple(row)))
        return results

    def list_albums(self) -> list[str]:
        """List all unique album names.

        Returns:
            List of album names
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT DISTINCT album_name FROM songs WHERE album_name IS NOT NULL ORDER BY album_name"
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    def list_keys(self) -> list[str]:
        """List all unique musical keys.

        Returns:
            List of key names
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT DISTINCT musical_key FROM songs WHERE musical_key IS NOT NULL ORDER BY musical_key"
        )
        return [row[0] for row in cursor.fetchall() if row[0]]

    # Recording operations (read-only)

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
        has_analysis: bool = False,
        limit: Optional[int] = None,
    ) -> list[Recording]:
        """List recordings with optional filters.

        Args:
            status: Filter by analysis status
            has_analysis: Only return recordings with completed analysis
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
        """Get total number of recordings.

        Returns:
            Total count
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM recordings")
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_analyzed_recording_count(self) -> int:
        """Get number of recordings with completed analysis.

        Returns:
            Count of analyzed recordings
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM recordings WHERE analysis_status = 'completed'")
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_song_count(self) -> int:
        """Get total number of songs.

        Returns:
            Total count
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM songs")
        result = cursor.fetchone()
        return result[0] if result else 0
