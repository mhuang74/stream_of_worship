"""Playlist data models for multi-song support."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from stream_of_worship.tui.models.transition import TransitionParams


@dataclass
class PlaylistItem:
    """A single item in a playlist (song + transition to next)."""

    # Song identification
    song_id: str
    song_filename: str

    # Section selection (which parts of the song to include)
    start_section: int = 0  # Index of first section to include
    end_section: Optional[int] = None  # Index of last section (None = to end)

    # Transition to the next song (if not last item)
    transition_to_next: Optional[TransitionParams] = None

    @property
    def duration(self) -> float:
        """Calculate duration based on section selection.

        For now, returns full song duration as sections are not loaded.
        In the full implementation, this would use the song's sections.
        """
        # This would be computed from the Song object's sections
        # For now, return a placeholder
        return 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "song_id": self.song_id,
            "song_filename": self.song_filename,
            "start_section": self.start_section,
            "end_section": self.end_section,
        }
        if self.transition_to_next is not None:
            result["transition_to_next"] = self.transition_to_next.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "PlaylistItem":
        """Create from dictionary."""
        transition = None
        if "transition_to_next" in data:
            transition = TransitionParams.from_dict(data["transition_to_next"])

        return cls(
            song_id=data.get("song_id", ""),
            song_filename=data.get("song_filename", ""),
            start_section=data.get("start_section", 0),
            end_section=data.get("end_section"),
            transition_to_next=transition,
        )


@dataclass
class PlaylistMetadata:
    """Metadata about a playlist."""

    name: str
    created_at: datetime
    updated_at: datetime
    total_duration: float  # in seconds
    total_songs: int

    @property
    def formatted_duration(self) -> str:
        """Format duration as MM:SS or HH:MM:SS."""
        total_seconds = int(self.total_duration)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def song_count_display(self) -> str:
        """Format song count display."""
        return f"{self.total_songs} song{'s' if self.total_songs != 1 else ''}"

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "total_duration": self.total_duration,
            "total_songs": self.total_songs,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlaylistMetadata":
        """Create from dictionary."""
        return cls(
            name=data.get("name", "Untitled Playlist"),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat())),
            total_duration=data.get("total_duration", 0.0),
            total_songs=data.get("total_songs", 0),
        )


@dataclass
class Playlist:
    """A complete playlist containing multiple songs and transitions."""

    # Unique identifier
    id: str

    # Playlist content
    items: List[PlaylistItem] = field(default_factory=list)

    # Metadata
    metadata: PlaylistMetadata = None

    # File persistence
    file_path: Optional[Path] = None

    def __post_init__(self):
        """Initialize default values."""
        if self.id is None:
            self.id = str(uuid4())
        if self.metadata is None:
            self.metadata = PlaylistMetadata(
                name="Untitled Playlist",
                created_at=datetime.now(),
                updated_at=datetime.now(),
                total_duration=0.0,
                total_songs=len(self.items),
            )

    @property
    def duration(self) -> float:
        """Get total playlist duration in seconds."""
        return self.metadata.total_duration

    @property
    def song_count(self) -> int:
        """Get number of songs in playlist."""
        return len(self.items)

    @property
    def name(self) -> str:
        """Get playlist name."""
        return self.metadata.name

    @name.setter
    def name(self, value: str):
        """Set playlist name and update metadata."""
        self.metadata.name = value
        self.metadata.updated_at = datetime.now()

    def add_song(
        self,
        song_id: str,
        song_filename: str,
        start_section: int = 0,
        end_section: Optional[int] = None,
        transition: Optional[TransitionParams] = None,
        index: Optional[int] = None,
    ) -> None:
        """Add a song to the playlist.

        Args:
            song_id: Song identifier
            song_filename: Song filename for display
            start_section: Index of first section to include
            end_section: Index of last section (None = to end)
            transition: Transition parameters to next song
            index: Position to insert (None = append to end)
        """
        item = PlaylistItem(
            song_id=song_id,
            song_filename=song_filename,
            start_section=start_section,
            end_section=end_section,
            transition_to_next=transition,
        )

        if index is None:
            self.items.append(item)
        else:
            self.items.insert(index, item)

        self._update_metadata()

    def remove_song(self, index: int) -> Optional[PlaylistItem]:
        """Remove a song from the playlist.

        Args:
            index: Index of song to remove

        Returns:
            Removed item, or None if index invalid
        """
        if 0 <= index < len(self.items):
            item = self.items.pop(index)
            # Also remove the transition from the previous song
            if index > 0 and self.items:
                self.items[index - 1].transition_to_next = None
            self._update_metadata()
            return item
        return None

    def move_song(self, from_index: int, to_index: int) -> bool:
        """Move a song to a new position.

        Args:
            from_index: Current position
            to_index: New position

        Returns:
            True if successful, False otherwise
        """
        if not (0 <= from_index < len(self.items) and 0 <= to_index < len(self.items)):
            return False

        if from_index == to_index:
            return True

        item = self.items.pop(from_index)
        # Adjust to_index when moving forward (from_index < to_index)
        # because the list has shrunk by 1 after pop
        insert_index = to_index if from_index > to_index else to_index - 1
        self.items.insert(insert_index, item)
        self._update_metadata()
        return True

    def update_transition(self, index: int, transition: TransitionParams) -> bool:
        """Update transition for a song.

        Args:
            index: Index of song to update transition for
            transition: New transition parameters

        Returns:
            True if successful, False otherwise
        """
        if 0 <= index < len(self.items):
            self.items[index].transition_to_next = transition
            self.metadata.updated_at = datetime.now()
            return True
        return False

    def get_transition(self, index: int) -> Optional[TransitionParams]:
        """Get transition for a song.

        Args:
            index: Index of song to get transition for

        Returns:
            Transition parameters, or None if no transition or invalid index
        """
        if 0 <= index < len(self.items):
            return self.items[index].transition_to_next
        return None

    def clear(self) -> None:
        """Clear all items from playlist."""
        self.items.clear()
        self._update_metadata()

    def _update_metadata(self) -> None:
        """Update metadata after playlist changes."""
        self.metadata.total_songs = len(self.items)
        self.metadata.total_duration = sum(
            item.duration for item in self.items
        )
        self.metadata.updated_at = datetime.now()

    def to_dict(self) -> dict:
        """Convert playlist to dictionary for serialization."""
        return {
            "id": self.id,
            "metadata": self.metadata.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Playlist":
        """Create playlist from dictionary.

        Args:
            data: Dictionary containing playlist data

        Returns:
            Playlist instance
        """
        metadata = PlaylistMetadata.from_dict(data.get("metadata", {}))

        items = [
            PlaylistItem.from_dict(item_data)
            for item_data in data.get("items", [])
        ]

        return cls(
            id=data.get("id", str(uuid4())),
            items=items,
            metadata=metadata,
        )

    def save(self, path: Optional[Path] = None) -> None:
        """Save playlist to JSON file.

        Args:
            path: Path to save to (uses file_path if None)
        """
        if path is None:
            path = self.file_path

        if path is None:
            raise ValueError("No file path specified for saving playlist")

        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as f:
            import json

            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

        self.file_path = path

    @classmethod
    def load(cls, path: Path) -> "Playlist":
        """Load playlist from JSON file.

        Args:
            path: Path to playlist JSON file

        Returns:
            Playlist instance

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is invalid
        """
        import json

        if not path.exists():
            raise FileNotFoundError(f"Playlist file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        playlist = cls.from_dict(data)
        playlist.file_path = path
        return playlist
