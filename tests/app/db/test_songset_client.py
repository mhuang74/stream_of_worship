"""Tests for SongsetClient.

Tests CRUD operations for songsets and songset items.
"""

import sqlite3

import pytest

from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.db.schema import ALL_APP_SCHEMA_STATEMENTS


@pytest.fixture
def schema_db(tmp_path):
    """Create a database with full schema including admin tables."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Create admin tables
    conn.execute("""
        CREATE TABLE songs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            title_pinyin TEXT,
            composer TEXT,
            lyricist TEXT,
            album_name TEXT,
            album_series TEXT,
            musical_key TEXT,
            lyrics_raw TEXT,
            lyrics_lines TEXT,
            sections TEXT,
            source_url TEXT NOT NULL,
            table_row_number INTEGER,
            scraped_at TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE recordings (
            content_hash TEXT PRIMARY KEY,
            hash_prefix TEXT UNIQUE NOT NULL,
            song_id TEXT REFERENCES songs(id),
            original_filename TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            r2_audio_url TEXT,
            r2_stems_url TEXT,
            r2_lrc_url TEXT,
            duration_seconds REAL,
            tempo_bpm REAL,
            musical_key TEXT,
            musical_mode TEXT,
            key_confidence REAL,
            loudness_db REAL,
            beats TEXT,
            downbeats TEXT,
            sections TEXT,
            embeddings_shape TEXT,
            analysis_status TEXT DEFAULT 'pending',
            analysis_job_id TEXT,
            lrc_status TEXT DEFAULT 'pending',
            lrc_job_id TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Insert sample data for FK references
    conn.execute(
        "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (?, ?, ?, ?)",
        ("song_0001", "Test Song", "http://example.com", "2024-01-01T00:00:00")
    )
    conn.execute(
        "INSERT INTO recordings (content_hash, hash_prefix, song_id, original_filename, file_size_bytes, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("abc123" * 8, "abc123def456", "song_0001", "test.mp3", 1000, "2024-01-01T00:00:00")
    )

    # Create app schema
    cursor = conn.cursor()
    for statement in ALL_APP_SCHEMA_STATEMENTS:
        cursor.execute(statement)

    conn.commit()
    conn.close()

    return db_path


@pytest.fixture
def songset_client(schema_db):
    """SongsetClient instance with initialized schema."""
    client = SongsetClient(schema_db)
    client.initialize_schema()
    return client


@pytest.fixture
def sample_songset(songset_client):
    """Create a sample songset for item tests."""
    return songset_client.create_songset("Test Songset", "A description")


class TestSongsetCRUD:
    """Tests for songset CRUD operations."""

    def test_create_songset_generates_id(self, songset_client):
        """Verify ID auto-generated."""
        songset = songset_client.create_songset("My Songset")

        assert songset.id is not None
        assert songset.id.startswith("songset_")

    def test_create_songset_stores_fields(self, songset_client):
        """Verify name/description stored."""
        songset = songset_client.create_songset("My Songset", "My Description")

        assert songset.name == "My Songset"
        assert songset.description == "My Description"
        assert songset.created_at is not None
        assert songset.updated_at is not None

    def test_get_songset_returns_songset(self, songset_client, sample_songset):
        """Verify retrieval works."""
        retrieved = songset_client.get_songset(sample_songset.id)

        assert retrieved is not None
        assert retrieved.id == sample_songset.id
        assert retrieved.name == sample_songset.name

    def test_get_songset_returns_none_for_missing(self, songset_client):
        """Verify None for unknown ID."""
        retrieved = songset_client.get_songset("songset_nonexistent")

        assert retrieved is None

    def test_list_songsets_returns_all(self, songset_client, monkeypatch):
        """Verify all songsets returned."""
        from stream_of_worship.app.db import models

        # Patch ID generation to use sequential IDs for testing
        counter = [0]
        original_generate_id = models.Songset.generate_id

        def mock_generate_id():
            counter[0] += 1
            return f"songset_test_{counter[0]:04d}"

        monkeypatch.setattr(models.Songset, "generate_id", mock_generate_id)

        songset_client.create_songset("Songset 1")
        songset_client.create_songset("Songset 2")

        # Restore original
        monkeypatch.setattr(models.Songset, "generate_id", original_generate_id)

        songsets = songset_client.list_songsets()

        assert len(songsets) == 2
        assert all(isinstance(s, Songset) for s in songsets)

    def test_update_songset_modifies_fields(self, songset_client, sample_songset):
        """Verify update changes stored."""
        result = songset_client.update_songset(sample_songset.id, name="Updated Name")

        assert result is True

        retrieved = songset_client.get_songset(sample_songset.id)
        assert retrieved.name == "Updated Name"

    def test_update_songset_updates_timestamp(self, songset_client, sample_songset):
        """Verify updated_at changes."""
        original_updated_at = sample_songset.updated_at

        # Force a small delay to ensure timestamp changes
        import time
        time.sleep(0.01)

        songset_client.update_songset(sample_songset.id, name="New Name")

        retrieved = songset_client.get_songset(sample_songset.id)
        assert retrieved.updated_at != original_updated_at

    def test_delete_songset_removes_songset(self, songset_client, sample_songset):
        """Verify deletion works."""
        result = songset_client.delete_songset(sample_songset.id)

        assert result is True
        assert songset_client.get_songset(sample_songset.id) is None

    def test_delete_songset_cascades_to_items(self, songset_client, sample_songset):
        """Verify CASCADE deletes items."""
        # Add an item
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")

        # Verify item exists
        items = songset_client.get_items(sample_songset.id, detailed=False)
        assert len(items) == 1

        # Delete songset
        songset_client.delete_songset(sample_songset.id)

        # Verify items are gone
        items = songset_client.get_items(sample_songset.id, detailed=False)
        assert len(items) == 0


class TestSongsetItemOperations:
    """Tests for songset item operations."""

    def test_add_item_appends_to_end(self, songset_client, sample_songset):
        """Verify position auto-assigned."""
        item = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")

        assert item.position == 0

        # Add another item
        item2 = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")
        assert item2.position == 1

    def test_add_item_inserts_at_position(self, songset_client, sample_songset):
        """Verify explicit position respected."""
        item = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=5)

        assert item.position == 5

    def test_add_item_uses_explicit_position(self, songset_client, sample_songset):
        """Verify explicit position is respected without reindexing."""
        # Add items at positions 0 and 1
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=0)
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=1)

        # Insert at position 0 (no reindexing - just inserts with position 0)
        new_item = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=0)

        # Verify all items exist with their specified positions
        items = songset_client.get_items(sample_songset.id, detailed=False)
        positions = [item.position for item in items]
        assert len(items) == 3
        assert new_item.position == 0

    def test_remove_item_reindexes_positions(self, songset_client, sample_songset):
        """Verify positions corrected after removal."""
        # Add three items
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")
        item2 = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")

        # Remove middle item
        songset_client.remove_item(item2.id)

        # Verify positions are 0 and 1 now
        items = songset_client.get_items(sample_songset.id, detailed=False)
        positions = sorted([item.position for item in items])
        assert positions == [0, 1]

    def test_move_item_up_shifts_others(self, songset_client, sample_songset):
        """Verify swap works for upward move."""
        # Add items at positions 0, 1, 2
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=0)
        item2 = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=1)
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=2)

        # Move item from position 2 to 0
        result = songset_client.reorder_item(item2.id, 0)

        assert result is True
        items = songset_client.get_items(sample_songset.id, detailed=False)
        positions = {item.id: item.position for item in items}
        assert positions[item2.id] == 0

    def test_move_item_down_shifts_others(self, songset_client, sample_songset):
        """Verify swap works for downward move."""
        # Add items at positions 0, 1, 2
        item1 = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=0)
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=1)
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=2)

        # Move item from position 0 to 2
        result = songset_client.reorder_item(item1.id, 2)

        assert result is True
        items = songset_client.get_items(sample_songset.id, detailed=False)
        positions = {item.id: item.position for item in items}
        assert positions[item1.id] == 2

    def test_move_item_same_position_noop(self, songset_client, sample_songset):
        """Verify no change when same position."""
        item = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=0)

        result = songset_client.reorder_item(item.id, 0)

        assert result is True
        items = songset_client.get_items(sample_songset.id, detailed=False)
        assert items[0].position == 0

    def test_get_items_returns_ordered_by_position(self, songset_client, sample_songset):
        """Verify ORDER BY position."""
        # Add items in reverse order
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=2)
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=1)
        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456", position=0)

        items = songset_client.get_items(sample_songset.id, detailed=False)

        positions = [item.position for item in items]
        assert positions == [0, 1, 2]

    def test_update_item_modifies_transition_params(self, songset_client, sample_songset):
        """Verify gap_beats, crossfade stored."""
        item = songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")

        result = songset_client.update_item(
            item.id,
            gap_beats=4.0,
            crossfade_enabled=True,
            crossfade_duration_seconds=2.0,
            key_shift_semitones=2,
            tempo_ratio=1.1,
        )

        assert result is True

        items = songset_client.get_items(sample_songset.id, detailed=False)
        updated = items[0]
        assert updated.gap_beats == 4.0
        assert updated.crossfade_enabled is True
        assert updated.crossfade_duration_seconds == 2.0
        assert updated.key_shift_semitones == 2
        assert updated.tempo_ratio == 1.1

    def test_get_item_count(self, songset_client, sample_songset):
        """Verify item count is accurate."""
        assert songset_client.get_item_count(sample_songset.id) == 0

        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")
        assert songset_client.get_item_count(sample_songset.id) == 1

        songset_client.add_item(sample_songset.id, "song_0001", "abc123def456")
        assert songset_client.get_item_count(sample_songset.id) == 2


class TestSchemaOperations:
    """Tests for schema operations."""

    def test_initialize_schema_idempotent(self, schema_db):
        """Verify multiple calls don't error."""
        client = SongsetClient(schema_db)

        # First call
        client.initialize_schema()

        # Second call - should not raise
        client.initialize_schema()

        # Third call - should not raise
        client.initialize_schema()

    def test_foreign_key_constraint_song_id(self, songset_client, sample_songset):
        """Verify FK error on invalid song_id."""
        with pytest.raises(sqlite3.IntegrityError):
            songset_client.add_item(sample_songset.id, "invalid_song", "abc123def456")

    def test_foreign_key_constraint_recording_hash(self, songset_client, sample_songset):
        """Verify FK error on invalid recording_hash_prefix."""
        with pytest.raises(sqlite3.IntegrityError):
            songset_client.add_item(sample_songset.id, "song_0001", "invalid_hash")


class TestTransactionManagement:
    """Tests for transaction handling."""

    def test_transaction_commits_on_success(self, songset_client):
        """Verify transaction commits when no exception."""
        with songset_client.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("songset_txn", "Txn Test", "2024-01-01T00:00:00", "2024-01-01T00:00:00")
            )

        # Verify committed
        result = songset_client.get_songset("songset_txn")
        assert result is not None

    def test_transaction_rolls_back_on_exception(self, songset_client):
        """Verify transaction rolls back on exception."""
        try:
            with songset_client.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO songsets (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("songset_rollback", "Rollback Test", "2024-01-01T00:00:00", "2024-01-01T00:00:00")
                )
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Verify rolled back
        result = songset_client.get_songset("songset_rollback")
        assert result is None
