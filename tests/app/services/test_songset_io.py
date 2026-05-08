"""Tests for SongsetIOService.

Tests JSON export/import with catalog validation via PostgreSQL.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS as ADMIN_SCHEMA
from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS as APP_SCHEMA
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.services.songset_io import ImportResult, SongsetIOService
from stream_of_worship.db.connection import ConnectionProvider


@pytest.fixture(scope="module")
def db(postgres_url):
    """Create a module-scoped unified Postgres database with all schemas."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()
    cursor = conn.cursor()
    for stmt in ADMIN_SCHEMA:
        cursor.execute(stmt)
    for stmt in APP_SCHEMA:
        cursor.execute(stmt)
    conn.commit()
    yield provider
    provider.close()


@pytest.fixture(autouse=True)
def clean_tables(db):
    """Truncate all tables between tests."""
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("TRUNCATE TABLE songs, recordings, songsets, songset_items CASCADE")
    conn.commit()


class TestSongsetExport:
    """Test suite for songset export."""

    def test_export_songset_creates_json(self, db, tmp_path):
        """Test export creates valid JSON file."""
        client = SongsetClient(db)
        ss = client.create_songset("Test Songset", id="test_123")
        client.add_item("test_123", "song_1", "abc123", position=0, gap_beats=2.0)

        io_service = SongsetIOService(client)
        output_path = tmp_path / "exported.json"
        result_path = io_service.export_songset("test_123", output_path)

        assert result_path.exists()

        data = json.loads(result_path.read_text())
        assert "songset" in data
        assert "items" in data
        assert data["songset"]["id"] == "test_123"
        assert data["songset"]["name"] == "Test Songset"
        assert len(data["items"]) == 1
        assert data["items"][0]["song_id"] == "song_1"


class TestSongsetImport:
    """Test suite for songset import."""

    def test_import_songset_from_json(self, db, tmp_path):
        """Test import creates songset from JSON."""
        client = SongsetClient(db)
        io_service = SongsetIOService(client)

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

        result = io_service.import_songset(input_path)

        assert result.success is True
        assert result.songset_id == "imported_123"
        assert result.imported_items == 1

        songset = client.get_songset("imported_123")
        assert songset is not None
        assert songset.name == "Imported Songset"

        items = client.get_items_raw("imported_123")
        assert len(items) == 1
        assert items[0].song_id == "song_1"
        assert items[0].gap_beats == 3.0

    def test_import_detects_missing_recording(self, db, tmp_path):
        """Test import detects missing recordings."""
        client = SongsetClient(db)

        def mock_get_recording(hash_prefix):
            return None

        io_service = SongsetIOService(client, get_recording=mock_get_recording)

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

        result = io_service.import_songset(input_path)

        assert result.success is True
        assert result.orphaned_items == 1
        assert len(result.warnings) == 1

    def test_import_conflict_rename(self, db, tmp_path):
        """Test import with rename on conflict."""
        client = SongsetClient(db)
        client.create_songset("Existing", id="existing_123")

        io_service = SongsetIOService(client)

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

        result = io_service.import_songset(input_path, on_conflict="rename")

        assert result.success is True
        assert result.songset_id != "existing_123"

        original = client.get_songset("existing_123")
        assert original is not None
        assert original.name == "Existing"

    def test_import_conflict_skip(self, db, tmp_path):
        """Test import with skip on conflict."""
        client = SongsetClient(db)
        client.create_songset("Existing", id="existing_123")

        io_service = SongsetIOService(client)

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

        result = io_service.import_songset(input_path, on_conflict="skip")

        assert result.success is False
        assert "already exists" in result.error

    def test_import_invalid_json(self, db, tmp_path):
        """Test import with invalid JSON file."""
        client = SongsetClient(db)
        io_service = SongsetIOService(client)

        input_path = tmp_path / "invalid.json"
        input_path.write_text("not valid json {{")

        result = io_service.import_songset(input_path)

        assert result.success is False
        assert "Invalid JSON" in result.error

    def test_import_missing_fields(self, db, tmp_path):
        """Test import with missing required fields."""
        client = SongsetClient(db)
        io_service = SongsetIOService(client)

        json_data = {"songset": {"id": "test", "name": "Test"}}
        input_path = tmp_path / "import.json"
        input_path.write_text(json.dumps(json_data))

        result = io_service.import_songset(input_path)

        assert result.success is False
        assert "Invalid songset file format" in result.error

    def test_import_uses_create_songset_with_id(self, db, tmp_path):
        """Verify import uses client.create_songset with imported ID."""
        client = SongsetClient(db)
        io_service = SongsetIOService(client)

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

        io_service.import_songset(input_path)

        songset = client.get_songset("imported_123")
        assert songset is not None
        assert songset.id == "imported_123"

    def test_import_uses_add_item_for_each_item(self, db, tmp_path):
        """Verify import uses client.add_item for each item."""
        client = SongsetClient(db)
        io_service = SongsetIOService(client)

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

        io_service.import_songset(input_path)

        items = client.get_items_raw("test_set")
        assert len(items) == 2

    def test_import_validates_recordings(self, db, tmp_path):
        """Verify import validates recordings and handles missing ones."""
        client = SongsetClient(db)

        def mock_get_recording(hash_prefix):
            return None

        io_service = SongsetIOService(client, get_recording=mock_get_recording)

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

        result = io_service.import_songset(input_path)

        assert result.success is True
        assert "Recording not found" in " ".join(result.warnings)

    def test_import_preserves_crossfade_params(self, db, tmp_path):
        """Verify import preserves crossfade and transition parameters."""
        client = SongsetClient(db)
        io_service = SongsetIOService(client)

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

        assert "sqlite3" not in dir(songset_io_module)


class TestSongsetExportImportRoundTrip:
    """Round-trip tests: export then re-import."""

    def test_round_trip_export_import(self, db, tmp_path):
        """Export a songset and then import it back."""
        client = SongsetClient(db)
        ss = client.create_songset("Round Trip Set", "A test description")
        client.add_item(
            ss.id, "song_1", "rec_hash",
            position=0, gap_beats=4.0,
            crossfade_enabled=True, crossfade_duration_seconds=3.0,
            key_shift_semitones=2, tempo_ratio=1.1,
        )

        io_service = SongsetIOService(client)
        export_path = tmp_path / f"{ss.id}_export.json"
        io_service.export_songset(ss.id, export_path)

        # Verify export file
        exported = json.loads(export_path.read_text())
        assert exported["songset"]["name"] == "Round Trip Set"
        assert len(exported["items"]) == 1

        # Delete original
        client.delete_songset(ss.id)
        assert client.get_songset(ss.id) is None

        # Import back
        result = io_service.import_songset(export_path)
        assert result.success is True

        imported = client.get_songset(result.songset_id)
        assert imported is not None
        assert imported.name == "Round Trip Set"

        items = client.get_items_raw(result.songset_id)
        assert len(items) == 1
        assert items[0].gap_beats == 4.0
        assert items[0].crossfade_enabled is True
        assert items[0].crossfade_duration_seconds == 3.0
        assert items[0].key_shift_semitones == 2
        assert items[0].tempo_ratio == 1.1
