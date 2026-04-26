"""Read-write database client for songset tables.

Provides CRUD operations for songsets and songset_items tables.
These tables are managed by the app and separate from admin tables.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.schema import (
    ALL_APP_SCHEMA_STATEMENTS,
    SONGSET_ITEMS_QUERY,
)


class MissingReferenceError(Exception):
    """Error when a referenced song or recording is not found."""

    def __init__(self, message: str, reference_type: str, reference_id: str):
        super().__init__(message)
        self.reference_type = reference_type
        self.reference_id = reference_id


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

    def create_songset(
        self,
        name: str,
        description: Optional[str] = None,
        id: Optional[str] = None,
    ) -> Songset:
        """Create a new songset.

        Args:
            name: Display name for the songset
            description: Optional description
            id: Optional ID to use (for import); generated if None

        Returns:
            Created Songset instance
        """
        songset = Songset(
            id=id or Songset.generate_id(),
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
                (
                    songset.id,
                    songset.name,
                    songset.description,
                    songset.created_at,
                    songset.updated_at,
                ),
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

    def update_songset(
        self, songset_id: str, name: Optional[str] = None, description: Optional[str] = None
    ) -> bool:
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
                SET {", ".join(updates)}, updated_at = datetime('now')
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

    def validate_recording_exists(
        self,
        recording_hash_prefix: str,
        get_recording: Optional[Callable[[str], Optional[Any]]] = None,
    ) -> bool:
        """Validate that a recording exists in the catalog.

        Args:
            recording_hash_prefix: The recording hash prefix to validate
            get_recording: Optional callable to check recording existence (e.g., ReadOnlyClient.get_recording_by_hash)

        Returns:
            True if recording exists or no validation function provided

        Raises:
            MissingReferenceError: If recording does not exist
        """
        if get_recording is None:
            return True

        recording = get_recording(recording_hash_prefix)
        if recording is None:
            raise MissingReferenceError(
                f"Recording not found: {recording_hash_prefix}",
                "recording",
                recording_hash_prefix,
            )
        return True

    def snapshot_db(self, retention: int = 5) -> Path:
        """Create a timestamped backup of the songsets database.

        Args:
            retention: Number of backups to keep (oldest are pruned)

        Returns:
            Path to the created backup file
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        # Create backup filename with ISO8601 timestamp
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = self.db_path.parent / f"{self.db_path.name}.bak-{timestamp}"

        # Use SQLite backup API for consistent snapshot
        backup_conn = sqlite3.connect(str(backup_path))
        try:
            self.connection.backup(backup_conn)
        finally:
            backup_conn.close()

        # Prune old backups beyond retention limit
        backup_pattern = f"{self.db_path.name}.bak-*"
        backups = sorted(
            self.db_path.parent.glob(backup_pattern),
            key=lambda p: p.stat().st_mtime,
        )

        # Remove oldest backups beyond retention
        while len(backups) > retention:
            oldest = backups.pop(0)
            oldest.unlink()

        return backup_path

    # Songset item operations

    def add_item(
        self,
        songset_id: str,
        song_id: str,
        recording_hash_prefix: Optional[str] = None,
        position: Optional[int] = None,
        gap_beats: float = 2.0,
        crossfade_enabled: bool = False,
        crossfade_duration_seconds: Optional[float] = None,
        key_shift_semitones: int = 0,
        tempo_ratio: float = 1.0,
        get_recording: Optional[Callable[[str], Optional]] = None,
    ) -> SongsetItem:
        """Add a song to a songset.

        Args:
            songset_id: The songset ID
            song_id: The song ID to add (denormalized display hint)
            recording_hash_prefix: Optional recording hash (canonical anchor)
            position: Position in songset (None = append to end)
            gap_beats: Gap duration before this song
            crossfade_enabled: Whether to use crossfade instead of gap
            crossfade_duration_seconds: Duration of crossfade if enabled
            key_shift_semitones: Key adjustment for this song
            tempo_ratio: Tempo adjustment ratio (1.0 = original)
            get_recording: Optional callable to validate recording existence

        Returns:
            Created SongsetItem

        Raises:
            MissingReferenceError: If recording_hash_prefix is provided but not found
        """
        # Validate recording exists if provided
        if recording_hash_prefix:
            self.validate_recording_exists(recording_hash_prefix, get_recording)

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
                crossfade_enabled=crossfade_enabled,
                crossfade_duration_seconds=crossfade_duration_seconds,
                key_shift_semitones=key_shift_semitones,
                tempo_ratio=tempo_ratio,
                created_at=datetime.now().isoformat(),
            )

            cursor.execute(
                """
                INSERT INTO songset_items
                (id, songset_id, song_id, recording_hash_prefix, position, gap_beats,
                 crossfade_enabled, crossfade_duration_seconds, key_shift_semitones,
                 tempo_ratio, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.songset_id,
                    item.song_id,
                    item.recording_hash_prefix,
                    item.position,
                    item.gap_beats,
                    1 if item.crossfade_enabled else 0,
                    item.crossfade_duration_seconds,
                    item.key_shift_semitones,
                    item.tempo_ratio,
                    item.created_at,
                ),
            )

            # Update songset updated_at
            cursor.execute(
                "UPDATE songsets SET updated_at = datetime('now') WHERE id = ?",
                (songset_id,),
            )

        return item

    def get_items(self, songset_id: str, detailed: bool = False) -> list[SongsetItem]:
        """Get all items in a songset.

        Args:
            songset_id: The songset ID
            detailed: Deprecated - kept for compatibility, always returns basic items

        Returns:
            List of SongsetItem ordered by position (without joined data)
        """
        cursor = self.connection.cursor()
        cursor.execute(SONGSET_ITEMS_QUERY, (songset_id,))
        return [SongsetItem.from_row(tuple(row), detailed=False) for row in cursor.fetchall()]

    def get_items_raw(self, songset_id: str) -> list[SongsetItem]:
        """Get all items in a songset (raw, no joined data).

        This is the preferred method for cross-DB lookups where song/recording
        data is fetched separately via CatalogService.

        Args:
            songset_id: The songset ID

        Returns:
            List of SongsetItem ordered by position
        """
        return self.get_items(songset_id, detailed=False)

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
                SET {", ".join(updates)}
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

    def get_metadata(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get metadata value from _sync_metadata table.

        Args:
            key: Metadata key
            default: Default value if key not found

        Returns:
            Metadata value or default
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT value FROM _sync_metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default

    def set_metadata(self, key: str, value: str) -> None:
        """Set metadata value in _sync_metadata table.

        Args:
            key: Metadata key
            value: Metadata value
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO _sync_metadata (key, value) VALUES (?, ?)",
                (key, value),
            )
