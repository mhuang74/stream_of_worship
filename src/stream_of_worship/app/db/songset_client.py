"""Read-write database client for songset tables.

Provides CRUD operations for songsets and songset_items tables via ``psycopg``
and a shared ``ConnectionProvider``.
"""

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Generator, Optional, TypeVar

import psycopg

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.schema import SONGSET_ITEMS_QUERY
from stream_of_worship.db.connection import ConnectionProvider

T = TypeVar("T")


class MissingReferenceError(Exception):
    """Error when a referenced song or recording is not found."""

    def __init__(self, message: str, reference_type: str, reference_id: str):
        super().__init__(message)
        self.reference_type = reference_type
        self.reference_id = reference_id


class NotOwnerError(Exception):
    """Raised when a mutation targets a songset not owned by the current user."""

    def __init__(self, songset_id: str, user_id: int):
        super().__init__(
            f"Songset {songset_id!r} is not owned by user {user_id}"
        )
        self.songset_id = songset_id
        self.user_id = user_id


class SongsetClient:
    """Client for songset CRUD operations, scoped to a single user.

    All reads and writes are filtered by ``user_id``: ``list_songsets`` /
    ``get_songset`` return only this user's rows; ``create_songset`` writes
    ``user_id`` automatically; mutations on items raise ``NotOwnerError`` if
    the songset belongs to someone else.

    Attributes:
        connection_provider: ``ConnectionProvider`` instance.
        user_id: Owning user ID; baked into every query.
    """

    def __init__(self, connection_provider: ConnectionProvider, user_id: int):
        """Initialize the songset client for a specific user.

        Args:
            connection_provider: ``ConnectionProvider`` wrapping the DSN.
            user_id: ID of the user whose songsets this client operates on.
        """
        self.connection_provider = connection_provider
        self.user_id = user_id

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

    def __enter__(self) -> "SongsetClient":
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
            conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Ownership helpers
    # ------------------------------------------------------------------

    def _owns_songset(self, songset_id: str) -> bool:
        """Return True iff this client's user owns the given songset."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM songsets WHERE id = %s AND user_id = %s",
                (songset_id, self.user_id),
            )
            return cursor.fetchone() is not None

    def _assert_owner(self, songset_id: str) -> None:
        """Raise ``NotOwnerError`` if this client's user doesn't own the songset."""
        if not self._owns_songset(songset_id):
            raise NotOwnerError(songset_id, self.user_id)

    # ------------------------------------------------------------------
    # Songset operations
    # ------------------------------------------------------------------

    _SONGSET_COLUMNS = "id, user_id, name, description, created_at, updated_at"

    def create_songset(
        self,
        name: str,
        description: Optional[str] = None,
        id: Optional[str] = None,
    ) -> Songset:
        """Create a new songset owned by this client's user.

        Args:
            name: Display name for the songset.
            description: Optional description.
            id: Optional ID to use (for import); generated if None.

        Returns:
            Created ``Songset`` instance.
        """
        songset = Songset(
            id=id or Songset.generate_id(),
            user_id=self.user_id,
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )

        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO songsets (id, user_id, name, description, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    songset.id,
                    songset.user_id,
                    songset.name,
                    songset.description,
                    songset.created_at,
                    songset.updated_at,
                ),
            )

        return songset

    def get_songset(self, songset_id: str) -> Optional[Songset]:
        """Get a songset by ID, scoped to the current user.

        Args:
            songset_id: The songset ID.

        Returns:
            ``Songset`` or ``None`` if not found or not owned by this user.
        """
        cursor = self.connection.cursor()
        cursor.execute(
            f"SELECT {self._SONGSET_COLUMNS} FROM songsets "
            "WHERE id = %s AND user_id = %s",
            (songset_id, self.user_id),
        )
        row = cursor.fetchone()

        if row:
            return Songset.from_row(tuple(row))
        return None

    def list_songsets(self, limit: Optional[int] = None) -> list[Songset]:
        """List songsets owned by this client's user.

        Args:
            limit: Maximum number of results.

        Returns:
            List of songsets ordered by ``updated_at`` desc.
        """
        cursor = self.connection.cursor()

        query = (
            f"SELECT {self._SONGSET_COLUMNS} FROM songsets "
            "WHERE user_id = %s ORDER BY updated_at DESC"
        )
        params: list = [self.user_id]

        if limit:
            query += " LIMIT %s"
            params.append(int(limit))

        cursor.execute(query, params)

        return [Songset.from_row(tuple(row)) for row in cursor.fetchall()]

    def update_songset(
        self, songset_id: str, name: Optional[str] = None, description: Optional[str] = None
    ) -> bool:
        """Update a songset's name and/or description, scoped to current user.

        Args:
            songset_id: The songset ID.
            name: New name (optional).
            description: New description (optional).

        Returns:
            True if updated, False if not found or not owned by this user.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            updates = []
            params: list = []

            if name is not None:
                updates.append("name = %s")
                params.append(name)

            if description is not None:
                updates.append("description = %s")
                params.append(description)

            if not updates:
                return False

            params.extend([songset_id, self.user_id])

            sql = f"""
                UPDATE songsets
                SET {', '.join(updates)}, updated_at = NOW()
                WHERE id = %s AND user_id = %s
            """

            cursor.execute(sql, params)
            return cursor.rowcount > 0

    def delete_songset(self, songset_id: str) -> bool:
        """Delete a songset and all its items (CASCADE), scoped to current user.

        Args:
            songset_id: The songset ID.

        Returns:
            True if deleted, False if not found or not owned by this user.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM songsets WHERE id = %s AND user_id = %s",
                (songset_id, self.user_id),
            )
            return cursor.rowcount > 0

    def validate_recording_exists(
        self,
        recording_hash_prefix: str,
        get_recording: Optional[Callable[[str], Optional[Any]]] = None,
    ) -> bool:
        """Validate that a recording exists in the catalog.

        Args:
            recording_hash_prefix: The recording hash prefix to validate.
            get_recording: Optional callable to check recording existence.

        Returns:
            True if recording exists or no validation function provided.

        Raises:
            MissingReferenceError: If recording does not exist.
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

    # ------------------------------------------------------------------
    # Songset item operations
    # ------------------------------------------------------------------

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
        get_recording: Optional[Callable[[str], Optional[Any]]] = None,
    ) -> SongsetItem:
        """Add a song to a songset.

        Args:
            songset_id: The songset ID.
            song_id: The song ID to add.
            recording_hash_prefix: Optional recording hash.
            position: Position in songset (None = append to end).
            gap_beats: Gap duration before this song.
            crossfade_enabled: Whether to use crossfade instead of gap.
            crossfade_duration_seconds: Duration of crossfade if enabled.
            key_shift_semitones: Key adjustment for this song.
            tempo_ratio: Tempo adjustment ratio.
            get_recording: Optional callable to validate recording existence.

        Returns:
            Created ``SongsetItem``.

        Raises:
            MissingReferenceError: If ``recording_hash_prefix`` is provided but not found.
        """
        if recording_hash_prefix:
            self.validate_recording_exists(recording_hash_prefix, get_recording)

        self._assert_owner(songset_id)

        with self.transaction() as conn:
            cursor = conn.cursor()

            if position is None:
                cursor.execute(
                    "SELECT MAX(position) FROM songset_items WHERE songset_id = %s",
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

            cursor.execute(
                "UPDATE songsets SET updated_at = NOW() WHERE id = %s",
                (songset_id,),
            )

        return item

    def get_items(self, songset_id: str, detailed: bool = False) -> list[SongsetItem]:
        """Get all items in a songset.

        Returns an empty list if the songset isn't owned by this client's user
        (rather than raising) so the TUI's read paths stay quiet.

        Args:
            songset_id: The songset ID.
            detailed: Deprecated - kept for compatibility, always returns basic items.

        Returns:
            List of ``SongsetItem`` ordered by position.
        """
        if not self._owns_songset(songset_id):
            return []
        cursor = self.connection.cursor()
        cursor.execute(SONGSET_ITEMS_QUERY, (songset_id,))
        return [SongsetItem.from_row(tuple(row), detailed=False) for row in cursor.fetchall()]

    def get_items_raw(self, songset_id: str) -> list[SongsetItem]:
        """Get all items in a songset (raw, no joined data).

        This is the preferred method for lookups where song/recording data
        is fetched separately via ``CatalogService``.

        Args:
            songset_id: The songset ID.

        Returns:
            List of ``SongsetItem`` ordered by position.
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
            item_id: The item ID.
            gap_beats: New gap duration.
            crossfade_enabled: Whether to use crossfade.
            crossfade_duration_seconds: Crossfade duration.
            key_shift_semitones: Key adjustment.
            tempo_ratio: Tempo adjustment.
            recording_hash_prefix: New recording hash.

        Returns:
            True if updated, False if not found.

        Raises:
            NotOwnerError: If the item's songset is not owned by this user.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT i.songset_id, s.user_id
                FROM songset_items i
                JOIN songsets s ON i.songset_id = s.id
                WHERE i.id = %s
                """,
                (item_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            songset_id, owner_id = row
            if owner_id != self.user_id:
                raise NotOwnerError(songset_id, self.user_id)

            updates = []
            params: list = []

            if gap_beats is not None:
                updates.append("gap_beats = %s")
                params.append(gap_beats)

            if crossfade_enabled is not None:
                updates.append("crossfade_enabled = %s")
                params.append(1 if crossfade_enabled else 0)

            if crossfade_duration_seconds is not None:
                updates.append("crossfade_duration_seconds = %s")
                params.append(crossfade_duration_seconds)

            if key_shift_semitones is not None:
                updates.append("key_shift_semitones = %s")
                params.append(key_shift_semitones)

            if tempo_ratio is not None:
                updates.append("tempo_ratio = %s")
                params.append(tempo_ratio)

            if recording_hash_prefix is not None:
                updates.append("recording_hash_prefix = %s")
                params.append(recording_hash_prefix)

            if not updates:
                return False

            params.append(item_id)

            sql = f"""
                UPDATE songset_items
                SET {', '.join(updates)}
                WHERE id = %s
            """

            cursor.execute(sql, params)
            updated = cursor.rowcount > 0

            if updated:
                cursor.execute(
                    """
                    UPDATE songsets
                    SET updated_at = NOW()
                    WHERE id = (SELECT songset_id FROM songset_items WHERE id = %s)
                    """,
                    (item_id,),
                )

            return updated

    def remove_item(self, item_id: str) -> bool:
        """Remove an item from a songset.

        Args:
            item_id: The item ID.

        Returns:
            True if removed, False if not found.

        Raises:
            NotOwnerError: If the item's songset is not owned by this user.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT i.songset_id, i.position, s.user_id
                FROM songset_items i
                JOIN songsets s ON i.songset_id = s.id
                WHERE i.id = %s
                """,
                (item_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            songset_id, position, owner_id = row
            if owner_id != self.user_id:
                raise NotOwnerError(songset_id, self.user_id)

            cursor.execute("DELETE FROM songset_items WHERE id = %s", (item_id,))

            cursor.execute(
                """
                UPDATE songset_items
                SET position = position - 1
                WHERE songset_id = %s AND position > %s
                """,
                (songset_id, position),
            )

            cursor.execute(
                "UPDATE songsets SET updated_at = NOW() WHERE id = %s",
                (songset_id,),
            )

            return True

    def reorder_item(self, item_id: str, new_position: int) -> bool:
        """Move an item to a new position.

        Args:
            item_id: The item ID.
            new_position: New position (0-indexed).

        Returns:
            True if moved, False if not found.

        Raises:
            NotOwnerError: If the item's songset is not owned by this user.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT i.songset_id, i.position, s.user_id
                FROM songset_items i
                JOIN songsets s ON i.songset_id = s.id
                WHERE i.id = %s
                """,
                (item_id,),
            )
            row = cursor.fetchone()

            if not row:
                return False

            songset_id, old_position, owner_id = row
            if owner_id != self.user_id:
                raise NotOwnerError(songset_id, self.user_id)

            if old_position == new_position:
                return True

            if old_position < new_position:
                cursor.execute(
                    """
                    UPDATE songset_items
                    SET position = position - 1
                    WHERE songset_id = %s AND position > %s AND position <= %s
                    """,
                    (songset_id, old_position, new_position),
                )
            else:
                cursor.execute(
                    """
                    UPDATE songset_items
                    SET position = position + 1
                    WHERE songset_id = %s AND position >= %s AND position < %s
                    """,
                    (songset_id, new_position, old_position),
                )

            cursor.execute(
                "UPDATE songset_items SET position = %s WHERE id = %s",
                (new_position, item_id),
            )

            cursor.execute(
                "UPDATE songsets SET updated_at = NOW() WHERE id = %s",
                (songset_id,),
            )

            return True

    def get_item_count(self, songset_id: str) -> int:
        """Get the number of items in a songset.

        Returns 0 if the songset isn't owned by this client's user.

        Args:
            songset_id: The songset ID.

        Returns:
            Item count.
        """
        if not self._owns_songset(songset_id):
            return 0
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM songset_items WHERE songset_id = %s",
            (songset_id,),
        )
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_item_counts_batch(self, songset_ids: list[str]) -> dict[str, int]:
        """Batch-fetch item counts for multiple songsets.

        Args:
            songset_ids: List of songset IDs.

        Returns:
            Dict keyed by songset_id mapping to item count.
        """
        if not songset_ids:
            return {}

        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT songset_id, COUNT(*) FROM songset_items "
            "WHERE songset_id = ANY(%s) GROUP BY songset_id",
            (songset_ids,),
        )
        result = {row[0]: row[1] for row in cursor.fetchall()}
        # Ensure all requested IDs have an entry (0 if not in result)
        return {sid: result.get(sid, 0) for sid in songset_ids}
