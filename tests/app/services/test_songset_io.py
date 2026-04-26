"""Tests for SongsetIOService.

Tests JSON export/import with catalog validation.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.songset_io import ImportResult, SongsetIOService


class TestSongsetExport:
    """Test suite for songset export."""

    def test_export_songset_creates_json(self, tmp_path):
        """Test export creates valid JSON file."""
        # Create songset database
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT
            )
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
        conn.execute(
            "INSERT INTO songsets VALUES ('test_123', 'Test Songset', NULL, '2024-01-01', '2024-01-01')"
        )
        conn.execute(
            "INSERT INTO songset_items VALUES ('item_1', 'test_123', 'song_1', 'abc123', 0, 2.0, 0, NULL, 0, 1.0, '2024-01-01')"
        )
        conn.commit()
        conn.close()

        # Export
        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)

        output_path = tmp_path / "exported.json"
        result_path = io_service.export_songset("test_123", output_path)

        assert result_path.exists()

        # Verify JSON structure
        data = json.loads(result_path.read_text())
        assert "songset" in data
        assert "items" in data
        assert data["songset"]["id"] == "test_123"
        assert data["songset"]["name"] == "Test Songset"
        assert len(data["items"]) == 1
        assert data["items"][0]["song_id"] == "song_1"


class TestSongsetImport:
    """Test suite for songset import."""

    def test_import_songset_from_json(self, tmp_path):
        """Test import creates songset from JSON."""
        # Create empty songset database
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT
            )
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
        conn.commit()
        conn.close()

        # Create JSON file to import
        json_data = {
            "songset": {
                "id": "imported_123",
                "name": "Imported Songset",
                "description": "Test description",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [
                {
                    "id": "item_1",
                    "songset_id": "imported_123",
                    "song_id": "song_1",
                    "recording_hash_prefix": "abc123",
                    "position": 0,
                    "gap_beats": 3.0,
                    "crossfade_enabled": False,
                    "key_shift_semitones": 2,
                    "tempo_ratio": 1.1,
                    "created_at": "2024-01-01",
                }
            ],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        # Import
        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)

        result = io_service.import_songset(input_path)

        assert result.success is True
        assert result.songset_id == "imported_123"
        assert result.imported_items == 1

        # Verify in database
        songset = client.get_songset("imported_123")
        assert songset is not None
        assert songset.name == "Imported Songset"

        items = client.get_items_raw("imported_123")
        assert len(items) == 1
        assert items[0].song_id == "song_1"
        assert items[0].gap_beats == 3.0

    def test_import_detects_missing_recording(self, tmp_path):
        """Test import detects missing recordings."""
        # Create empty database
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT
            )
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
        conn.commit()
        conn.close()

        # Create JSON with recording that doesn't exist
        json_data = {
            "songset": {
                "id": "test_123",
                "name": "Test",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [
                {
                    "id": "item_1",
                    "songset_id": "test_123",
                    "song_id": "song_1",
                    "recording_hash_prefix": "nonexistent_hash",
                    "position": 0,
                    "gap_beats": 2.0,
                    "crossfade_enabled": False,
                    "key_shift_semitones": 0,
                    "tempo_ratio": 1.0,
                    "created_at": "2024-01-01",
                }
            ],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        # Mock get_recording to return None (recording not found)
        def mock_get_recording(hash_prefix):
            return None

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client, get_recording=mock_get_recording)

        result = io_service.import_songset(input_path)

        assert result.success is True
        assert result.orphaned_items == 1
        assert len(result.warnings) == 1

    def test_import_conflict_rename(self, tmp_path):
        """Test import with rename on conflict."""
        # Create database with existing songset
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT
            )
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
        conn.execute(
            "INSERT INTO songsets VALUES ('existing_123', 'Existing', NULL, '2024-01-01', '2024-01-01')"
        )
        conn.commit()
        conn.close()

        # Try to import with same ID
        json_data = {
            "songset": {
                "id": "existing_123",
                "name": "New Name",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)

        result = io_service.import_songset(input_path, on_conflict="rename")

        assert result.success is True
        # ID should be different (renamed)
        assert result.songset_id != "existing_123"

        # Original should still exist
        original = client.get_songset("existing_123")
        assert original is not None
        assert original.name == "Existing"

    def test_import_conflict_skip(self, tmp_path):
        """Test import with skip on conflict."""
        # Create database with existing songset
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT
            )
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
        conn.execute(
            "INSERT INTO songsets VALUES ('existing_123', 'Existing', NULL, '2024-01-01', '2024-01-01')"
        )
        conn.commit()
        conn.close()

        # Try to import with same ID
        json_data = {
            "songset": {
                "id": "existing_123",
                "name": "New Name",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)

        result = io_service.import_songset(input_path, on_conflict="skip")

        assert result.success is False
        assert "already exists" in result.error

    def test_import_invalid_json(self, tmp_path):
        """Test import with invalid JSON file."""
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE songsets (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        input_path = tmp_path / "invalid.json"
        input_path.write_text("not valid json {{")

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)

        result = io_service.import_songset(input_path)

        assert result.success is False
        assert "Invalid JSON" in result.error

    def test_import_missing_fields(self, tmp_path):
        """Test import with missing required fields."""
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE songsets (id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        # JSON missing "items"
        json_data = {"songset": {"id": "test", "name": "Test"}}
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)

        result = io_service.import_songset(input_path)

        assert result.success is False
        assert "Invalid songset file format" in result.error

    def test_import_uses_create_songset_with_id(self, tmp_path):
        """Verify import uses client.create_songset with imported ID."""
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY, songset_id TEXT NOT NULL, song_id TEXT NOT NULL,
                recording_hash_prefix TEXT, position INTEGER NOT NULL, gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0, crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0, tempo_ratio REAL DEFAULT 1.0, created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        json_data = {
            "songset": {
                "id": "imported_123",
                "name": "Test",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)
        io_service.import_songset(input_path)

        songset = client.get_songset("imported_123")
        assert songset is not None
        assert songset.id == "imported_123"

    def test_import_uses_add_item_for_each_item(self, tmp_path):
        """Verify import uses client.add_item for each item."""
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY, songset_id TEXT NOT NULL, song_id TEXT NOT NULL,
                recording_hash_prefix TEXT, position INTEGER NOT NULL, gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0, crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0, tempo_ratio REAL DEFAULT 1.0, created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        json_data = {
            "songset": {
                "id": "test_set",
                "name": "Test",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [
                {"id": "item_1", "songset_id": "test_set", "song_id": "song_1", "position": 0},
                {"id": "item_2", "songset_id": "test_set", "song_id": "song_2", "position": 1},
            ],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)
        io_service.import_songset(input_path)

        items = client.get_items_raw("test_set")
        assert len(items) == 2

    def test_import_validates_recordings(self, tmp_path):
        """Verify import validates recordings and handles missing ones."""
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY, songset_id TEXT NOT NULL, song_id TEXT NOT NULL,
                recording_hash_prefix TEXT, position INTEGER NOT NULL, gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0, crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0, tempo_ratio REAL DEFAULT 1.0, created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        def mock_get_recording(hash_prefix):
            return None

        json_data = {
            "songset": {
                "id": "test_123",
                "name": "Test",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [
                {
                    "id": "item_1",
                    "songset_id": "test_123",
                    "song_id": "song_1",
                    "recording_hash_prefix": "missing_hash",
                    "position": 0,
                    "gap_beats": 2.0,
                    "crossfade_enabled": False,
                },
            ],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client, get_recording=mock_get_recording)
        result = io_service.import_songset(input_path)

        assert result.success is True
        assert "Recording not found" in " ".join(result.warnings)

    def test_import_preserves_crossfade_params(self, tmp_path):
        """Verify import preserves crossfade and transition parameters."""
        db_path = tmp_path / "songsets.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songsets (id TEXT PRIMARY KEY, name TEXT NOT NULL)
        """)
        conn.execute("""
            CREATE TABLE songset_items (
                id TEXT PRIMARY KEY, songset_id TEXT NOT NULL, song_id TEXT NOT NULL,
                recording_hash_prefix TEXT, position INTEGER NOT NULL, gap_beats REAL DEFAULT 2.0,
                crossfade_enabled INTEGER DEFAULT 0, crossfade_duration_seconds REAL,
                key_shift_semitones INTEGER DEFAULT 0, tempo_ratio REAL DEFAULT 1.0, created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        json_data = {
            "songset": {
                "id": "test_123",
                "name": "Test",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-01",
            },
            "items": [
                {
                    "id": "item_1",
                    "songset_id": "test_123",
                    "song_id": "song_1",
                    "position": 0,
                    "gap_beats": 2.0,
                    "crossfade_enabled": True,
                    "crossfade_duration_seconds": 3.0,
                    "key_shift_semitones": 2,
                    "tempo_ratio": 1.1,
                },
            ],
        }
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        client = SongsetClient(db_path)
        io_service = SongsetIOService(client)
        io_service.import_songset(input_path)

        items = client.get_items_raw("test_123")
        assert len(items) == 1
        assert items[0].crossfade_enabled is True
        assert items[0].crossfade_duration_seconds == 3.0
        assert items[0].key_shift_semitones == 2
        assert items[0].tempo_ratio == 1.1

    def test_import_no_raw_sql_in_songset_io(self, tmp_path):
        """Verify songset_io doesn't use sqlite3 directly."""
        import stream_of_worship.app.services.songset_io as songset_io_module

        # Check that sqlite3 is NOT imported in the module
        assert "sqlite3" not in dir(songset_io_module)
