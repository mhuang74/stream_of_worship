"""Data models for sow-app database entities.

Provides dataclasses for Songset and SongsetItem entities with serialization
to/from database rows.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class Songset:
    """User-created songset (playlist) for worship sets.

    Attributes:
        id: Unique songset ID (e.g., "songset_0001")
        name: Display name for the songset
        description: Optional description
        created_at: ISO timestamp when created
        updated_at: ISO timestamp when last updated
    """

    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: tuple) -> "Songset":
        """Create a Songset from a database row tuple.

        Args:
            row: Database row tuple with columns in schema order

        Returns:
            Songset instance
        """
        return cls(
            id=row[0],
            name=row[1],
            description=row[2],
            created_at=row[3],
            updated_at=row[4],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Songset to dictionary.

        Returns:
            Dictionary representation of the songset
        """
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def generate_id(cls) -> str:
        """Generate a new unique songset ID.

        Returns:
            Unique ID string
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"songset_{timestamp}"


@dataclass
class SongsetItem:
    """A song within a songset with transition parameters.

    Attributes:
        id: Unique item ID
        songset_id: Reference to songsets.id
        song_id: Reference to songs.id
        recording_hash_prefix: Reference to recordings.hash_prefix (optional)
        position: Position in the songset (0-indexed)
        gap_beats: Gap duration before this song (for first song: intro gap)
        crossfade_enabled: Whether to use crossfade instead of gap
        crossfade_duration_seconds: Duration of crossfade if enabled
        key_shift_semitones: Key adjustment for this song
        tempo_ratio: Tempo adjustment ratio (1.0 = original)
        created_at: ISO timestamp when created
        song_title: Joined song title (not in DB, populated by query)
        song_key: Joined song key (not in DB, populated by query)
        duration_seconds: Joined recording duration (not in DB, populated by query)
        tempo_bpm: Joined recording tempo (not in DB, populated by query)
        recording_key: Joined recording key (not in DB, populated by query)
        loudness_db: Joined recording loudness (not in DB, populated by query)
    """

    id: str
    songset_id: str
    song_id: str
    position: int
    recording_hash_prefix: Optional[str] = None
    gap_beats: float = 2.0
    crossfade_enabled: bool = False
    crossfade_duration_seconds: Optional[float] = None
    key_shift_semitones: int = 0
    tempo_ratio: float = 1.0
    created_at: Optional[str] = None

    # Joined fields (populated by detailed query)
    song_title: Optional[str] = None
    song_key: Optional[str] = None
    duration_seconds: Optional[float] = None
    tempo_bpm: Optional[float] = None
    recording_key: Optional[str] = None
    loudness_db: Optional[float] = None

    @classmethod
    def from_row(cls, row: tuple, detailed: bool = False) -> "SongsetItem":
        """Create a SongsetItem from a database row tuple.

        Args:
            row: Database row tuple with columns in schema order
            detailed: Whether this is a detailed query with joined fields

        Returns:
            SongsetItem instance
        """
        if detailed and len(row) >= 16:
            # Detailed query with joined fields
            return cls(
                id=row[0],
                songset_id=row[1],
                song_id=row[2],
                recording_hash_prefix=row[3],
                position=row[4],
                gap_beats=row[5] if row[5] is not None else 2.0,
                crossfade_enabled=bool(row[6]) if row[6] is not None else False,
                crossfade_duration_seconds=row[7],
                key_shift_semitones=row[8] if row[8] is not None else 0,
                tempo_ratio=row[9] if row[9] is not None else 1.0,
                created_at=row[10],
                song_title=row[11],
                song_key=row[12],
                duration_seconds=row[13],
                tempo_bpm=row[14],
                recording_key=row[15],
                loudness_db=row[16] if len(row) > 16 else None,
            )
        else:
            # Basic query
            return cls(
                id=row[0],
                songset_id=row[1],
                song_id=row[2],
                recording_hash_prefix=row[3],
                position=row[4],
                gap_beats=row[5] if row[5] is not None else 2.0,
                crossfade_enabled=bool(row[6]) if row[6] is not None else False,
                crossfade_duration_seconds=row[7],
                key_shift_semitones=row[8] if row[8] is not None else 0,
                tempo_ratio=row[9] if row[9] is not None else 1.0,
                created_at=row[10],
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert SongsetItem to dictionary.

        Returns:
            Dictionary representation of the item
        """
        return {
            "id": self.id,
            "songset_id": self.songset_id,
            "song_id": self.song_id,
            "recording_hash_prefix": self.recording_hash_prefix,
            "position": self.position,
            "gap_beats": self.gap_beats,
            "crossfade_enabled": self.crossfade_enabled,
            "crossfade_duration_seconds": self.crossfade_duration_seconds,
            "key_shift_semitones": self.key_shift_semitones,
            "tempo_ratio": self.tempo_ratio,
            "created_at": self.created_at,
            "song_title": self.song_title,
            "song_key": self.song_key,
            "duration_seconds": self.duration_seconds,
            "tempo_bpm": self.tempo_bpm,
            "recording_key": self.recording_key,
            "loudness_db": self.loudness_db,
        }

    @classmethod
    def generate_id(cls) -> str:
        """Generate a new unique item ID.

        Returns:
            Unique ID string
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"item_{timestamp}"

    @property
    def formatted_duration(self) -> str:
        """Get duration formatted as MM:SS.

        Returns:
            Formatted duration string
        """
        if self.duration_seconds is None:
            return "--:--"
        minutes = int(self.duration_seconds // 60)
        seconds = int(self.duration_seconds % 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def display_key(self) -> str:
        """Get the key to display (song key or recording key).

        Returns:
            Key string
        """
        return self.recording_key or self.song_key or "?"
