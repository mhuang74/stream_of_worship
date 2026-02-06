"""Read-write database client for songset tables.

Provides CRUD operations for songsets and songset_items tables.
These tables are managed by the app and separate from admin tables.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.schema import (
    ALL_APP_SCHEMA_STATEMENTS,
    SONGSET_ITEMS_DETAIL_QUERY,
)


class SongsetClient:
    """Client for songset CRUD operations.

    This client manages the app-specific songsets and songset_items tables,
    which are separate from the admin-managed songs/recordings tables.

    Attributes:
        db_path: Path to the SQLite database file
        connection: Active database connection
    """

    def __init__(self, db_path: Path):
        """Initialize the songset client.

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
            # Ensure directory exists
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

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

    def __enter__(self) -> "SongsetClient":
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
        """Initialize the app-specific database schema.

        Creates songsets and songset_items tables if they don't exist.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            for statement in ALL_APP_SCHEMA_STATEMENTS:
                cursor.execute(statement)

    # Songset operations

    def create_songset(self, name: str, description: Optional[str] = None) -> Songset:
        """Create a new songset.

        Args:
            name: Display name for the songset
            description: Optional description

        Returns:
            Created Songset instance
        """
        songset = Songset(
            id=Songset.generate_id(),
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO songsets (id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (songset.id, songset.name, songset.description, songset.created_at, songset.updated_at),
            )

        return songset

    def get_songset(self, songset_id: str) -> Optional[Songset]:
        """Get a songset by ID.

        Args:
            songset_id: The songset ID

        Returns:
            Songset or None if not found
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM songsets WHERE id = ?", (songset_id,))
        row = cursor.fetchone()

        if row:
            return Songset.from_row(tuple(row))
        return None

    def list_songsets(self, limit: Optional[int] = None) -> list[Songset]:
        """List all songsets.

        Args:
            limit: Maximum number of results

        Returns:
            List of songsets ordered by updated_at desc
        """
        cursor = self.connection.cursor()

        query = "SELECT * FROM songsets ORDER BY updated_at DESC"

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)

        results = []
        for row in cursor.fetchall():
            results.append(Songset.from_row(tuple(row)))
        return results

    def update_songset(self, songset_id: str, name: Optional[str] = None, description: Optional[str] = None) -> bool:
        """Update a songset's name and/or description.

        Args:
            songset_id: The songset ID
            name: New name (optional)
            description: New description (optional)

        Returns:
            True if updated, False if not found
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            updates = []
            params: list = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)

            if description is not None:
                updates.append("description = ?")
                params.append(description)

            if not updates:
                return False

            params.append(songset_id)

            sql = f"""
                UPDATE songsets
                SET {', '.join(updates)}, updated_at = datetime('now')
                WHERE id = ?
            """

            cursor.execute(sql, params)
            return cursor.rowcount > 0

    def delete_songset(self, songset_id: str) -> bool:
        """Delete a songset and all its items.

        Args:
            songset_id: The songset ID

        Returns:
            True if deleted, False if not found
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM songsets WHERE id = ?", (songset_id,))
            return cursor.rowcount > 0

    # Songset item operations

    def add_item(
        self,
        songset_id: str,
        song_id: str,
        recording_hash_prefix: Optional[str] = None,
        position: Optional[int] = None,
        gap_beats: float = 2.0,
    ) -> SongsetItem:
        """Add a song to a songset.

        Args:
            songset_id: The songset ID
            song_id: The song ID to add
            recording_hash_prefix: Optional recording hash
            position: Position in songset (None = append to end)
            gap_beats: Gap duration before this song

        Returns:
            Created SongsetItem
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            # Determine position if not specified
            if position is None:
                cursor.execute(
                    "SELECT MAX(position) FROM songset_items WHERE songset_id = ?",
                    (songset_id,),
                )
                result = cursor.fetchone()
                position = (result[0] + 1) if result[0] is not None else 0

            item = SongsetItem(
                id=SongsetItem.generate_id(),
                songset_id=songset_id,
                song_id=song_id,
                recording_hash_prefix=recording_hash_prefix,
                position=position,
                gap_beats=gap_beats,
                created_at=datetime.now().isoformat(),
            )

            cursor.execute(
                """
                INSERT INTO songset_items
                (id, songset_id, song_id, recording_hash_prefix, position, gap_beats, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.songset_id,
                    item.song_id,
                    item.recording_hash_prefix,
                    item.position,
                    item.gap_beats,
                    item.created_at,
                ),
            )

            # Update songset updated_at
            cursor.execute(
                "UPDATE songsets SET updated_at = datetime('now') WHERE id = ?",
                (songset_id,),
            )

        return item

    def get_items(self, songset_id: str, detailed: bool = True) -> list[SongsetItem]:
        """Get all items in a songset.

        Args:
            songset_id: The songset ID
            detailed: Whether to include joined song/recording details

        Returns:
            List of SongsetItem ordered by position
        """
        cursor = self.connection.cursor()

        if detailed:
            cursor.execute(SONGSET_ITEMS_DETAIL_QUERY, (songset_id,))
            return [SongsetItem.from_row(tuple(row), detailed=True) for row in cursor.fetchall()]
        else:
            cursor.execute(
                "SELECT * FROM songset_items WHERE songset_id = ? ORDER BY position",
                (songset_id,),
            )
            return [SongsetItem.from_row(tuple(row), detailed=False) for row in cursor.fetchall()]

    def update_item(
        self,
        item_id: str,
        gap_beats: Optional[float] = None,
        crossfade_enabled: Optional[bool] = None,
        crossfade_duration_seconds: Optional[float] = None,
        key_shift_semitones: Optional[int] = None,
        tempo_ratio: Optional[float] = None,
        recording_hash_prefix: Optional[str] = None,
    ) -> bool:
        """Update a songset item's transition parameters.

        Args:
            item_id: The item ID
            gap_beats: New gap duration
            crossfade_enabled: Whether to use crossfade
            crossfade_duration_seconds: Crossfade duration
            key_shift_semitones: Key adjustment
            tempo_ratio: Tempo adjustment
            recording_hash_prefix: New recording hash

        Returns:
            True if updated, False if not found
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            updates = []
            params: list = []

            if gap_beats is not None:
                updates.append("gap_beats = ?")
                params.append(gap_beats)

            if crossfade_enabled is not None:
                updates.append("crossfade_enabled = ?")
                params.append(1 if crossfade_enabled else 0)

            if crossfade_duration_seconds is not None:
                updates.append("crossfade_duration_seconds = ?")
                params.append(crossfade_duration_seconds)

            if key_shift_semitones is not None:
                updates.append("key_shift_semitones = ?")
                params.append(key_shift_semitones)

            if tempo_ratio is not None:
                updates.append("tempo_ratio = ?")
                params.append(tempo_ratio)

            if recording_hash_prefix is not None:
                updates.append("recording_hash_prefix = ?")
                params.append(recording_hash_prefix)

            if not updates:
                return False

            params.append(item_id)

            sql = f"""
                UPDATE songset_items
                SET {', '.join(updates)}
                WHERE id = ?
            """

            cursor.execute(sql, params)
            updated = cursor.rowcount > 0

            if updated:
                # Update songset updated_at
                cursor.execute(
                    """
                    UPDATE songsets
                    SET updated_at = datetime('now')
                    WHERE id = (SELECT songset_id FROM songset_items WHERE id = ?)
                    """,
                    (item_id,),
                )

            return updated

    def remove_item(self, item_id: str) -> bool:
        """Remove an item from a songset.

        Args:
            item_id: The item ID

        Returns:
            True if removed, False if not found
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            # Get songset_id and position for reordering
            cursor.execute(
                "SELECT songset_id, position FROM songset_items WHERE id = ?",
                (item_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            songset_id, position = row

            # Delete the item
            cursor.execute("DELETE FROM songset_items WHERE id = ?", (item_id,))

            # Reorder remaining items
            cursor.execute(
                """
                UPDATE songset_items
                SET position = position - 1
                WHERE songset_id = ? AND position > ?
                """,
                (songset_id, position),
            )

            # Update songset updated_at
            cursor.execute(
                "UPDATE songsets SET updated_at = datetime('now') WHERE id = ?",
                (songset_id,),
            )

            return True

    def reorder_item(self, item_id: str, new_position: int) -> bool:
        """Move an item to a new position.

        Args:
            item_id: The item ID
            new_position: New position (0-indexed)

        Returns:
            True if moved, False if not found
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            # Get current position
            cursor.execute(
                "SELECT songset_id, position FROM songset_items WHERE id = ?",
                (item_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            songset_id, old_position = row

            if old_position == new_position:
                return True

            if old_position < new_position:
                # Moving down: shift items between old and new up
                cursor.execute(
                    """
                    UPDATE songset_items
                    SET position = position - 1
                    WHERE songset_id = ? AND position > ? AND position <= ?
                    """,
                    (songset_id, old_position, new_position),
                )
            else:
                # Moving up: shift items between new and old down
                cursor.execute(
                    """
                    UPDATE songset_items
                    SET position = position + 1
                    WHERE songset_id = ? AND position >= ? AND position < ?
                    """,
                    (songset_id, new_position, old_position),
                )

            # Update the moved item
            cursor.execute(
                "UPDATE songset_items SET position = ? WHERE id = ?",
                (new_position, item_id),
            )

            # Update songset updated_at
            cursor.execute(
                "UPDATE songsets SET updated_at = datetime('now') WHERE id = ?",
                (songset_id,),
            )

            return True

    def get_item_count(self, songset_id: str) -> int:
        """Get the number of items in a songset.

        Args:
            songset_id: The songset ID

        Returns:
            Item count
        """
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM songset_items WHERE songset_id = ?",
            (songset_id,),
        )
        result = cursor.fetchone()
        return result[0] if result else 0
