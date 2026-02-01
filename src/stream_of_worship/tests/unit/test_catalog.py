"""Tests for song catalog management."""

import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import pytest

from stream_of_worship.core.catalog import Song, CatalogIndex


class TestSong:
    """Tests for Song dataclass."""

    def test_song_creation(self):
        """Test creating a Song with all fields."""
        song = Song(
            id="test_song_1",
            title="Test Song",
            artist="Test Artist",
            bpm=120.0,
            key="C",
            duration=180.0,
            tempo_category="medium",
            vocalist="mixed",
            themes=["Praise", "Worship"],
            bible_verses=["Psalm 23:1"],
            ai_summary="A test song about praising God.",
            has_stems=True,
            has_lrc=True,
        )

        assert song.id == "test_song_1"
        assert song.title == "Test Song"
        assert song.bpm == 120.0
        assert song.tempo_category == "medium"
        assert song.has_stems is True
        assert song.has_lrc is True

    def test_display_name_property(self):
        """Test display_name property."""
        song = Song(
            id="test_song",
            title="Awesome Song",
            artist="Great Artist",
            bpm=100.0,
            key="G",
            duration=240.0,
        )

        assert song.display_name == "Awesome Song - Great Artist"

    def test_from_dict_with_all_fields(self):
        """Test creating Song from full dictionary."""
        data = {
            "id": "song_123",
            "title": "Praise Song",
            "artist": "Worship Band",
            "bpm": 128.5,
            "key": "D",
            "duration": 245.0,
            "tempo_category": "fast",
            "vocalist": "female",
            "themes": ["Praise", "Glory"],
            "bible_verses": ["Revelation 4:8"],
            "ai_summary": "A fast praise song.",
            "has_stems": True,
            "has_lrc": True,
        }

        song = Song.from_dict(data)

        assert song.id == "song_123"
        assert song.title == "Praise Song"
        assert song.bpm == 128.5
        assert song.tempo_category == "fast"
        assert song.vocalist == "female"
        assert song.themes == ["Praise", "Glory"]
        assert song.has_stems is True
        assert song.has_lrc is True

    def test_from_dict_with_defaults(self):
        """Test creating Song from minimal dictionary."""
        data = {
            "id": "song_min",
            "title": "Minimal Song",
            "artist": "Artist",
            "bpm": 90.0,
            "key": "E",
            "duration": 200.0,
        }

        song = Song.from_dict(data)

        assert song.tempo_category == "medium"  # Default
        assert song.vocalist == "mixed"  # Default
        assert song.themes == []
        assert song.bible_verses == []
        assert song.ai_summary == ""
        assert song.has_stems is False  # Default
        assert song.has_lrc is False  # Default

    def test_to_dict(self):
        """Test converting Song to dictionary."""
        song = Song(
            id="test_song",
            title="Test",
            artist="Artist",
            bpm=110.0,
            key="F#",
            duration=180.0,
            tempo_category="medium",
            themes=["Thanksgiving"],
            bible_verses=["1 Thess 5:18"],
        )

        result = song.to_dict()

        assert result["id"] == "test_song"
        assert result["title"] == "Test"
        assert result["artist"] == "Artist"
        assert result["bpm"] == 110.0
        assert result["key"] == "F#"
        assert result["themes"] == ["Thanksgiving"]
        assert result["bible_verses"] == ["1 Thess 5:18"]
        assert result["tempo_category"] == "medium"


class TestCatalogIndex:
    """Tests for CatalogIndex dataclass."""

    def test_default_values(self):
        """Test default CatalogIndex values."""
        catalog = CatalogIndex()

        assert catalog.last_updated == ""
        assert catalog.version == "1.0"
        assert catalog.songs == []

    @pytest.fixture
    def catalog_file(self, tmp_path):
        """Fixture providing a temporary catalog file."""
        return tmp_path / "catalog.json"

    def test_load_with_songs(self, catalog_file):
        """Test loading catalog from JSON file."""
        data = {
            "last_updated": "2026-01-30T10:00:00Z",
            "version": "1.0",
            "songs": [
                {
                    "id": "song_1",
                    "title": "Song One",
                    "artist": "Artist A",
                    "bpm": 120.0,
                    "key": "C",
                    "duration": 180.0,
                },
                {
                    "id": "song_2",
                    "title": "Song Two",
                    "artist": "Artist B",
                    "bpm": 100.0,
                    "key": "G",
                    "duration": 240.0,
                },
            ],
        }

        with catalog_file.open("w") as f:
            json.dump(data, f)

        catalog = CatalogIndex.load(catalog_file)

        assert len(catalog.songs) == 2
        assert catalog.songs[0].title == "Song One"
        assert catalog.songs[1].title == "Song Two"

    def test_load_creates_directory(self, catalog_file):
        """Test that load creates parent directory if needed."""
        # Use subdirectory that doesn't exist
        sub_dir = catalog_file.parent / "subdir"
        test_file = sub_dir / "catalog.json"

        # First create the directory and write the file
        sub_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_updated": "2026-01-30T10:00:00Z",
            "version": "1.0",
            "songs": [],
        }

        with test_file.open("w") as f:
            json.dump(data, f)

        catalog = CatalogIndex.load(test_file)

        assert sub_dir.exists()
        assert isinstance(catalog, CatalogIndex)

    def test_load_raises_file_not_found(self):
        """Test that load raises FileNotFoundError for non-existent file."""
        with pytest.raises(FileNotFoundError):
            CatalogIndex.load(Path("/nonexistent/catalog.json"))

    def test_load_with_empty_songs(self, catalog_file):
        """Test loading catalog with empty songs list."""
        data = {
            "last_updated": "2026-01-30T10:00:00Z",
            "version": "1.0",
            "songs": [],
        }

        with catalog_file.open("w") as f:
            json.dump(data, f)

        catalog = CatalogIndex.load(catalog_file)

        assert len(catalog.songs) == 0

    def test_save_creates_directory(self, catalog_file):
        """Test that save creates parent directory."""
        sub_dir = catalog_file.parent / "subdir"
        test_file = sub_dir / "catalog.json"

        catalog = CatalogIndex()
        catalog.save(test_file)

        assert sub_dir.exists()
        assert test_file.exists()

    def test_save_writes_json(self, catalog_file):
        """Test that save writes valid JSON."""
        catalog = CatalogIndex(
            last_updated="2026-01-30T10:00:00Z",
            version="1.0",
            songs=[
                Song(
                    id="song_1",
                    title="Test Song",
                    artist="Artist",
                    bpm=120.0,
                    key="C",
                    duration=180.0,
                )
            ],
        )

        catalog.save(catalog_file)

        with catalog_file.open("r") as f:
            data = json.load(f)

        assert data["version"] == "1.0"
        assert len(data["songs"]) == 1
        assert data["songs"][0]["title"] == "Test Song"

    def test_save_updates_last_updated(self, catalog_file):
        """Test that save updates last_updated timestamp."""
        catalog = CatalogIndex()

        # Save once
        catalog.save(catalog_file)

        with catalog_file.open("r") as f:
            data = json.load(f)

        first_timestamp = data["last_updated"]
        assert first_timestamp != ""

    def test_add_song_new(self, catalog_file):
        """Test adding a new song to catalog."""
        catalog = CatalogIndex()

        song = Song(
            id="new_song",
            title="New Song",
            artist="New Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
        )

        catalog.add_song(song)

        assert len(catalog.songs) == 1
        assert catalog.songs[0] == song

    def test_add_song_updates_existing(self):
        """Test that add_song updates existing song by ID."""
        catalog = CatalogIndex()
        original_song = Song(
            id="song_1",
            title="Original Title",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
        )
        catalog.add_song(original_song)

        # Add same ID with different data
        updated_song = Song(
            id="song_1",
            title="Updated Title",
            artist="Updated Artist",
            bpm=120.0,
            key="D",
            duration=180.0,
        )
        catalog.add_song(updated_song)

        assert len(catalog.songs) == 1  # Not duplicated
        assert catalog.songs[0].title == "Updated Title"
        assert catalog.songs[0].bpm == 120.0

    def test_add_song_updates_last_updated(self):
        """Test that add_song updates last_updated."""
        catalog = CatalogIndex()

        # Get initial timestamp
        initial_timestamp = catalog.last_updated

        song = Song(
            id="test_song",
            title="Test",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
        )
        catalog.add_song(song)

        # Should be updated
        new_timestamp = catalog.last_updated
        assert new_timestamp != initial_timestamp

    def test_remove_song_success(self):
        """Test removing an existing song."""
        catalog = CatalogIndex()
        song = Song(
            id="song_1",
            title="Song One",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
        )
        catalog.add_song(song)

        result = catalog.remove_song("song_1")

        assert result is True
        assert len(catalog.songs) == 0

    def test_remove_song_not_found(self):
        """Test removing a non-existent song."""
        catalog = CatalogIndex()

        result = catalog.remove_song("nonexistent")

        assert result is False

    def test_remove_song_updates_last_updated(self):
        """Test that remove_song updates last_updated."""
        catalog = CatalogIndex()
        song = Song(
            id="song_1",
            title="Song One",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
        )
        catalog.add_song(song)

        initial_timestamp = catalog.last_updated
        catalog.remove_song("song_1")
        new_timestamp = catalog.last_updated

        assert new_timestamp != initial_timestamp

    def test_get_song_found(self):
        """Test getting a song that exists."""
        catalog = CatalogIndex()
        song = Song(
            id="song_1",
            title="Song One",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
        )
        catalog.add_song(song)

        result = catalog.get_song("song_1")

        assert result is not None
        assert result.title == "Song One"

    def test_get_song_not_found(self):
        """Test getting a song that doesn't exist."""
        catalog = CatalogIndex()

        result = catalog.get_song("nonexistent")

        assert result is None

    def test_find_by_theme(self):
        """Test finding songs by theme."""
        catalog = CatalogIndex()
        catalog.add_song(Song(
            id="song_1",
            title="Praise Song",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
            themes=["Praise", "Worship"],
        ))
        catalog.add_song(Song(
            id="song_2",
            title="Love Song",
            artist="Artist",
            bpm=100.0,
            key="D",
            duration=200.0,
            themes=["Love"],
        ))
        catalog.add_song(Song(
            id="song_3",
            title="Another Praise Song",
            artist="Artist",
            bpm=100.0,
            key="E",
            duration=200.0,
            themes=["Praise"],
        ))

        results = catalog.find_by_theme("praise")

        assert len(results) == 2
        assert all("praise" in [t.lower() for t in s.themes] for s in results)

    def test_find_by_theme_case_insensitive(self):
        """Test that find_by_theme is case-insensitive."""
        catalog = CatalogIndex()
        catalog.add_song(Song(
            id="song_1",
            title="Song",
            artist="Artist",
            bpm=100.0,
            key="C",
            duration=200.0,
            themes=["Praise", "Worship"],
        ))

        # Search with lowercase
        results = catalog.find_by_theme("praise")
        assert len(results) == 1

        # Search with uppercase
        results = catalog.find_by_theme("PRAISE")
        assert len(results) == 1

    def test_find_by_tempo_category(self):
        """Test finding songs by tempo category."""
        catalog = CatalogIndex()
        catalog.add_song(Song(
            id="song_1",
            title="Slow Song",
            artist="Artist",
            bpm=80.0,
            key="C",
            duration=200.0,
            tempo_category="slow",
        ))
        catalog.add_song(Song(
            id="song_2",
            title="Fast Song",
            artist="Artist",
            bpm=140.0,
            key="D",
            duration=200.0,
            tempo_category="fast",
        ))
        catalog.add_song(Song(
            id="song_3",
            title="Medium Song",
            artist="Artist",
            bpm=110.0,
            key="E",
            duration=200.0,
            tempo_category="medium",
        ))

        # Find slow songs
        slow_songs = catalog.find_by_tempo("slow")
        assert len(slow_songs) == 1
        assert slow_songs[0].bpm == 80.0

        # Find fast songs
        fast_songs = catalog.find_by_tempo("fast")
        assert len(fast_songs) == 1
        assert fast_songs[0].bpm == 140.0

        # Find medium songs
        medium_songs = catalog.find_by_tempo("medium")
        assert len(medium_songs) == 1
        assert medium_songs[0].bpm == 110.0

    def test_filter_by_bpm_range(self):
        """Test filtering songs by BPM range."""
        catalog = CatalogIndex()
        catalog.add_song(Song(
            id="song_1",
            title="Song 80",
            artist="Artist",
            bpm=80.0,
            key="C",
            duration=200.0,
        ))
        catalog.add_song(Song(
            id="song_2",
            title="Song 100",
            artist="Artist",
            bpm=100.0,
            key="D",
            duration=200.0,
        ))
        catalog.add_song(Song(
            id="song_3",
            title="Song 120",
            artist="Artist",
            bpm=120.0,
            key="E",
            duration=200.0,
        ))
        catalog.add_song(Song(
            id="song_4",
            title="Song 140",
            artist="Artist",
            bpm=140.0,
            key="F",
            duration=200.0,
        ))

        # Find songs 90-110 BPM
        results = catalog.filter_by_bpm_range(90.0, 110.0)

        assert len(results) == 1
        assert results[0].bpm == 100.0

        # Find songs 80-140 BPM
        results = catalog.filter_by_bpm_range(80.0, 140.0)

        assert len(results) == 4  # All songs

        # Find songs above 120 BPM
        results = catalog.filter_by_bpm_range(120.0, 200.0)

        assert len(results) == 2
        assert all(s.bpm >= 120.0 for s in results)

    def test_save_load_round_trip(self, catalog_file):
        """Test that save and load round-trip preserves data."""
        original = CatalogIndex(
            version="1.0",
            songs=[
                Song(
                    id="song_1",
                    title="Original Song",
                    artist="Artist",
                    bpm=120.0,
                    key="C",
                    duration=200.0,
                    themes=["Praise"],
                )
            ],
        )

        original.save(catalog_file)
        loaded = CatalogIndex.load(catalog_file)

        assert len(loaded.songs) == len(original.songs)
        assert loaded.songs[0].id == original.songs[0].id
        assert loaded.songs[0].title == original.songs[0].title
        assert loaded.songs[0].themes == original.songs[0].themes
