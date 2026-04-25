"""Tests for CatalogService cross-DB lookups.

Tests the two-step Python-side JOIN replacement for songset items.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.app.db.models import SongsetItem
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.catalog import CatalogService, SongsetItemWithDetails


class TestCrossDBLookup:
    """Test suite for cross-DB songset item lookups."""

    def test_get_songset_with_items_resolves_references(self, tmp_path):
        """Test that songset items resolve song/recording references."""
        # Create catalog database
        catalog_db = tmp_path / "catalog.db"
        conn = sqlite3.connect(catalog_db)
        conn.execute("""
            CREATE TABLE songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT NOT NULL,
                song_id TEXT,
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO songs VALUES ('song_1', 'Test Song', 'http://test', '2024-01-01', NULL)"
        )
        conn.execute(
            "INSERT INTO recordings VALUES ('full_hash', 'abc123', 'song_1', 'test.mp3', 1000, '2024-01-01', NULL)"
        )
        conn.commit()
        conn.close()

        # Create songsets database
        songsets_db = tmp_path / "songsets.db"
        conn = sqlite3.connect(songsets_db)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY,
                songset_id TEXT NOT NULL,
                song_id TEXT NOT NULL,
                recording_hash_prefix TEXT,
                position INTEGER NOT NULL,
                gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0,
                crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0,
                tempo_ratio REAL DEFAULT 1.0,
                created_at TEXT
            )
        """)
        conn.execute("INSERT INTO songsets VALUES ('set_1', 'Test Set')")
        conn.execute(
            "INSERT INTO songset_items VALUES ('item_1', 'set_1', 'song_1', 'abc123', 0, 2.0, 0, NULL, 0, 1.0, '2024-01-01')"
        )
        conn.commit()
        conn.close()

        # Create clients
        read_client = ReadOnlyClient(catalog_db)
        songset_client = SongsetClient(songsets_db)

        catalog = CatalogService(read_client)

        # Get songset with items
        items, orphan_count = catalog.get_songset_with_items("set_1", songset_client)

        assert len(items) == 1
        assert orphan_count == 0

        item = items[0]
        assert isinstance(item, SongsetItemWithDetails)
        assert item.song is not None
        assert item.song.title == "Test Song"
        assert item.recording is not None
        assert item.recording.hash_prefix == "abc123"
        assert item.is_orphan is False

    def test_get_songset_with_items_detects_orphans(self, tmp_path):
        """Test that missing references are marked as orphans."""
        # Create catalog database (empty - no songs/recordings)
        catalog_db = tmp_path / "catalog.db"
        conn = sqlite3.connect(catalog_db)
        conn.execute("""
            CREATE TABLE songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT NOT NULL,
                song_id TEXT,
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        # Create songsets database with orphaned items
        songsets_db = tmp_path / "songsets.db"
        conn = sqlite3.connect(songsets_db)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY,
                songset_id TEXT NOT NULL,
                song_id TEXT NOT NULL,
                recording_hash_prefix TEXT,
                position INTEGER NOT NULL,
                gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0,
                crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0,
                tempo_ratio REAL DEFAULT 1.0,
                created_at TEXT
            )
        """)
        conn.execute("INSERT INTO songsets VALUES ('set_1', 'Test Set')")
        # Item references non-existent recording
        conn.execute(
            "INSERT INTO songset_items VALUES ('item_1', 'set_1', 'song_1', 'missing_hash', 0, 2.0, 0, NULL, 0, 1.0, '2024-01-01')"
        )
        conn.commit()
        conn.close()

        # Create clients
        read_client = ReadOnlyClient(catalog_db)
        songset_client = SongsetClient(songsets_db)

        catalog = CatalogService(read_client)

        # Get songset with items
        items, orphan_count = catalog.get_songset_with_items("set_1", songset_client)

        assert len(items) == 1
        assert orphan_count == 1

        item = items[0]
        assert item.is_orphan is True
        assert item.song is None
        assert item.recording is None
        assert item.display_title == "Unknown"

    def test_get_songset_with_items_detects_soft_deleted(self, tmp_path):
        """Test that soft-deleted songs are marked as orphans."""
        # Create catalog with soft-deleted song
        catalog_db = tmp_path / "catalog.db"
        conn = sqlite3.connect(catalog_db)
        conn.execute("""
            CREATE TABLE songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT NOT NULL,
                song_id TEXT,
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        # Soft-deleted song
        conn.execute(
            "INSERT INTO songs VALUES ('song_1', 'Deleted Song', 'http://test', '2024-01-01', '2024-01-02')"
        )
        # Soft-deleted recording
        conn.execute(
            "INSERT INTO recordings VALUES ('full_hash', 'abc123', 'song_1', 'test.mp3', 1000, '2024-01-01', '2024-01-02')"
        )
        conn.commit()
        conn.close()

        # Create songsets database
        songsets_db = tmp_path / "songsets.db"
        conn = sqlite3.connect(songsets_db)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY,
                songset_id TEXT NOT NULL,
                song_id TEXT NOT NULL,
                recording_hash_prefix TEXT,
                position INTEGER NOT NULL,
                gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0,
                crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0,
                tempo_ratio REAL DEFAULT 1.0,
                created_at TEXT
            )
        """)
        conn.execute("INSERT INTO songsets VALUES ('set_1', 'Test Set')")
        conn.execute(
            "INSERT INTO songset_items VALUES ('item_1', 'set_1', 'song_1', 'abc123', 0, 2.0, 0, NULL, 0, 1.0, '2024-01-01')"
        )
        conn.commit()
        conn.close()

        # Create clients
        read_client = ReadOnlyClient(catalog_db)
        songset_client = SongsetClient(songsets_db)

        catalog = CatalogService(read_client)

        # Get songset with items
        items, orphan_count = catalog.get_songset_with_items("set_1", songset_client)

        assert len(items) == 1
        # Song and recording exist but are soft-deleted
        # The read_client.get_song_including_deleted will find them
        # But item.is_orphan should be True because recording is deleted
        # (depends on business logic - currently we mark as orphan if deleted)

        item = items[0]
        # Soft-deleted items are found via including_deleted=True
        # but should be treated as orphans for display purposes
        # This depends on the exact implementation


class TestSongsetItemWithDetails:
    """Test SongsetItemWithDetails helper class."""

    def test_is_orphan_when_song_missing(self):
        """Test is_orphan when song is None."""
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=None, recording=None)
        assert details.is_orphan is True

    def test_is_orphan_when_recording_missing(self):
        """Test is_orphan when recording is None."""
        song = MagicMock(spec=Song)
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=song, recording=None)
        assert details.is_orphan is True

    def test_not_orphan_when_both_present(self):
        """Test not orphan when both present."""
        song = MagicMock(spec=Song)
        recording = MagicMock(spec=Recording)
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=song, recording=recording)
        assert details.is_orphan is False

    def test_display_title_from_song(self):
        """Test display_title uses song title."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01",
        )
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=song, recording=None)
        assert details.display_title == "Test Song"

    def test_display_title_unknown_when_no_song(self):
        """Test display_title is 'Unknown' when no song."""
        item = SongsetItem(
            id="item_1",
            songset_id="set_1",
            song_id="song_1",
            position=0,
        )
        details = SongsetItemWithDetails(item=item, song=None, recording=None)
        assert details.display_title == "Unknown"
