"""Song catalog management for Stream of Worship.

This module handles loading, indexing, and managing the song library
from the catalog_index.json file.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any

from stream_of_worship.core.paths import get_catalog_index_path


@dataclass
class Song:
    """Song metadata from the catalog."""

    id: str
    title: str
    artist: str
    bpm: float
    key: str
    duration: float
    tempo_category: str = "medium"
    vocalist: str = "mixed"
    themes: List[str] = field(default_factory=list)
    bible_verses: List[str] = field(default_factory=list)
    ai_summary: str = ""
    has_stems: bool = False
    has_lrc: bool = False

    @property
    def display_name(self) -> str:
        """Get formatted display name."""
        return f"{self.title} - {self.artist}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Song":
        """Create Song from dictionary.

        Args:
            data: Dictionary containing song data

        Returns:
            Song instance
        """
        return cls(
            id=data["id"],
            title=data["title"],
            artist=data["artist"],
            bpm=data["bpm"],
            key=data["key"],
            duration=data["duration"],
            tempo_category=data.get("tempo_category", "medium"),
            vocalist=data.get("vocalist", "mixed"),
            themes=data.get("themes", []),
            bible_verses=data.get("bible_verses", []),
            ai_summary=data.get("ai_summary", ""),
            has_stems=data.get("has_stems", False),
            has_lrc=data.get("has_lrc", False),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert Song to dictionary.

        Returns:
            Dictionary representation of song
        """
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "bpm": self.bpm,
            "key": self.key,
            "duration": self.duration,
            "tempo_category": self.tempo_category,
            "vocalist": self.vocalist,
            "themes": self.themes,
            "bible_verses": self.bible_verses,
            "ai_summary": self.ai_summary,
            "has_stems": self.has_stems,
            "has_lrc": self.has_lrc,
        }


@dataclass
class CatalogIndex:
    """Catalog index containing all songs and metadata."""

    last_updated: str = ""
    version: str = "1.0"
    songs: List[Song] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "CatalogIndex":
        """Load catalog index from JSON file.

        Args:
            path: Path to catalog_index.json (uses default if None)

        Returns:
            CatalogIndex instance

        Raises:
            FileNotFoundError: If catalog file doesn't exist
            ValueError: If catalog file contains invalid data
        """
        if path is None:
            path = get_catalog_index_path()

        if not path.exists():
            raise FileNotFoundError(f"Catalog index not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        songs = [Song.from_dict(s) for s in data.get("songs", [])]

        return cls(
            last_updated=data.get("last_updated", ""),
            version=data.get("version", "1.0"),
            songs=songs,
        )

    def save(self, path: Optional[Path] = None) -> None:
        """Save catalog index to JSON file.

        Args:
            path: Path to save catalog_index.json (uses default if None)
        """
        if path is None:
            path = get_catalog_index_path()

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "last_updated": self.last_updated or datetime.now().isoformat(),
            "version": self.version,
            "songs": [song.to_dict() for song in self.songs],
        }

        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_song(self, song: Song) -> None:
        """Add a song to the catalog.

        Args:
            song: Song to add
        """
        # Check if song already exists by ID
        existing_index = next(
            (i for i, s in enumerate(self.songs) if s.id == song.id), None
        )
        if existing_index is not None:
            # Update existing song
            self.songs[existing_index] = song
        else:
            # Add new song
            self.songs.append(song)
        self.last_updated = datetime.now().isoformat()

    def remove_song(self, song_id: str) -> bool:
        """Remove a song from the catalog.

        Args:
            song_id: ID of song to remove

        Returns:
            True if song was removed, False if not found
        """
        for i, song in enumerate(self.songs):
            if song.id == song_id:
                self.songs.pop(i)
                self.last_updated = datetime.now().isoformat()
                return True
        return False

    def get_song(self, song_id: str) -> Optional[Song]:
        """Get a song by ID.

        Args:
            song_id: Song ID to look up

        Returns:
            Song if found, None otherwise
        """
        for song in self.songs:
            if song.id == song_id:
                return song
        return None

    def find_by_theme(self, theme: str) -> List[Song]:
        """Find songs matching a theme.

        Args:
            theme: Theme to search for

        Returns:
            List of matching songs
        """
        return [s for s in self.songs if theme.lower() in [t.lower() for t in s.themes]]

    def find_by_tempo(self, category: str) -> List[Song]:
        """Find songs by tempo category.

        Args:
            category: Tempo category (fast, medium, slow)

        Returns:
            List of matching songs
        """
        return [s for s in self.songs if s.tempo_category == category.lower()]

    def filter_by_bpm_range(self, min_bpm: float, max_bpm: float) -> List[Song]:
        """Filter songs by BPM range.

        Args:
            min_bpm: Minimum BPM
            max_bpm: Maximum BPM

        Returns:
            List of songs within BPM range
        """
        return [s for s in self.songs if min_bpm <= s.bpm <= max_bpm]
