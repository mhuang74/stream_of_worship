"""Tests for sow-admin database client."""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stream_of_worship.admin.db.client import (
    DatabaseClient,
    LIBSQL_AVAILABLE,
    SyncError,
)
from stream_of_worship.admin.db.models import Recording, Song


@pytest.fixture
def temp_db_path(tmp_path):
    """Return a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def client(temp_db_path):
    """Return an initialized DatabaseClient."""
    db = DatabaseClient(temp_db_path)
    db.initialize_schema()
    return db


class TestDatabaseClient:
    """Tests for DatabaseClient class."""

    def test_initialization_creates_db(self, temp_db_path):
        """Test that client initialization creates database."""
        client = DatabaseClient(temp_db_path)
        client.initialize_schema()

        assert temp_db_path.exists()

    def test_context_manager(self, temp_db_path):
        """Test using client as context manager."""
        with DatabaseClient(temp_db_path) as client:
            client.initialize_schema()
            assert temp_db_path.exists()

    def test_initialize_schema_creates_tables(self, client):
        """Test that schema initialization creates required tables."""
        cursor = client.connection.cursor()

        # Check for songs table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='songs'"
        )
        assert cursor.fetchone() is not None

        # Check for recordings table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recordings'"
        )
        assert cursor.fetchone() is not None

        # Check for sync_metadata table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_metadata'"
        )
        assert cursor.fetchone() is not None

    def test_foreign_keys_enabled(self, client):
        """Test that foreign keys are enabled."""
        cursor = client.connection.cursor()
        cursor.execute("PRAGMA foreign_keys")
        result = cursor.fetchone()

        assert result[0] == 1

    def test_get_stats(self, client):
        """Test getting database statistics."""
        stats = client.get_stats()

        assert stats.total_songs == 0
        assert stats.total_recordings == 0
        assert stats.integrity_ok is True

    def test_reset_database(self, client):
        """Test resetting the database."""
        # Insert a song first
        song = Song(
            id="song_0001",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        # Verify song exists
        assert client.get_song("song_0001") is not None

        # Reset database
        client.reset_database()

        # Verify song is gone
        assert client.get_song("song_0001") is None

        # Verify schema is re-created
        stats = client.get_stats()
        assert stats.integrity_ok is True


class TestSongOperations:
    """Tests for song CRUD operations."""

    @pytest.fixture
    def sample_song(self):
        """Return a sample song."""
        return Song(
            id="song_0001",
            title="將天敞開",
            source_url="https://sop.org/song/123",
            scraped_at=datetime.now().isoformat(),
            title_pinyin="jiang_tian_chang_kai",
            album_name="敬拜讚美15",
            musical_key="G",
        )

    def test_insert_and_get_song(self, client, sample_song):
        """Test inserting and retrieving a song."""
        client.insert_song(sample_song)

        retrieved = client.get_song("song_0001")

        assert retrieved is not None
        assert retrieved.id == "song_0001"
        assert retrieved.title == "將天敞開"
        assert retrieved.album_name == "敬拜讚美15"

    def test_get_nonexistent_song(self, client):
        """Test retrieving a song that doesn't exist."""
        result = client.get_song("nonexistent")

        assert result is None

    def test_insert_song_updates_existing(self, client, sample_song):
        """Test that inserting same ID updates the song."""
        client.insert_song(sample_song)

        # Modify and re-insert
        sample_song.title = "Updated Title"
        client.insert_song(sample_song)

        retrieved = client.get_song("song_0001")
        assert retrieved.title == "Updated Title"

    def test_list_songs(self, client):
        """Test listing songs."""
        # Insert multiple songs
        for i in range(5):
            song = Song(
                id=f"song_{i:04d}",
                title=f"Song {i}",
                source_url=f"https://example.com/{i}",
                scraped_at=datetime.now().isoformat(),
                album_name="Test Album" if i < 3 else "Other Album",
                musical_key="G" if i < 2 else "D",
            )
            client.insert_song(song)

        # Test listing all
        all_songs = client.list_songs()
        assert len(all_songs) == 5

        # Test filtering by album
        album_songs = client.list_songs(album="Test Album")
        assert len(album_songs) == 3

        # Test filtering by key
        key_songs = client.list_songs(key="G")
        assert len(key_songs) == 2

        # Test with limit
        limited = client.list_songs(limit=2)
        assert len(limited) == 2

    def test_search_songs(self, client):
        """Test searching songs."""
        # Insert test songs
        songs = [
            Song(
                id="song_0001",
                title="將天敞開",
                source_url="https://example.com/1",
                scraped_at=datetime.now().isoformat(),
                composer="Composer A",
            ),
            Song(
                id="song_0002",
                title="感謝",
                source_url="https://example.com/2",
                scraped_at=datetime.now().isoformat(),
                composer="Composer B",
                lyrics_raw="這是歌詞內容",
            ),
            Song(
                id="song_0003",
                title="另一首歌",
                source_url="https://example.com/3",
                scraped_at=datetime.now().isoformat(),
            ),
        ]

        for song in songs:
            client.insert_song(song)

        # Search by title
        results = client.search_songs("將天", field="title")
        assert len(results) == 1
        assert results[0].id == "song_0001"

        # Search by lyrics
        results = client.search_songs("歌詞", field="lyrics")
        assert len(results) == 1
        assert results[0].id == "song_0002"

        # Search by composer
        results = client.search_songs("Composer A", field="composer")
        assert len(results) == 1
        assert results[0].id == "song_0001"

        # Search all fields
        results = client.search_songs("感謝", field="all")
        assert len(results) == 1

        # Search with limit - use broad term that matches title prefix
        results = client.search_songs("歌", field="all", limit=2)
        assert len(results) == 2  # Matches "另一首歌" and "感謝" contains 歌


class TestRecordingOperations:
    """Tests for recording CRUD operations."""

    @pytest.fixture
    def sample_recording(self):
        """Return a sample recording."""
        return Recording(
            content_hash="c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            hash_prefix="c6de4449928d",
            song_id=None,  # No foreign key reference for basic tests
            original_filename="test.mp3",
            file_size_bytes=5242880,
            imported_at=datetime.now().isoformat(),
            r2_audio_url="s3://bucket/c6de4449928d/audio.mp3",
            duration_seconds=245.3,
            tempo_bpm=128.5,
            analysis_status="completed",
        )

    def test_insert_and_get_recording(self, client, sample_recording):
        """Test inserting and retrieving a recording."""
        client.insert_recording(sample_recording)

        retrieved = client.get_recording_by_hash("c6de4449928d")

        assert retrieved is not None
        assert retrieved.hash_prefix == "c6de4449928d"
        assert retrieved.tempo_bpm == 128.5

    def test_get_recording_by_song_id(self, client, sample_recording):
        """Test retrieving a recording by song ID."""
        # First create a song
        song = Song(
            id="song_0001",
            title="Test Song",
            source_url="https://example.com",
            scraped_at=datetime.now().isoformat(),
        )
        client.insert_song(song)

        # Create recording with song reference
        recording_with_song = Recording(
            content_hash="c6de4449928d0c4c5b76e23c9f4e5b8a7c6d5e4f3b2a1908",
            hash_prefix="c6de4449928d",
            song_id="song_0001",
            original_filename="test.mp3",
            file_size_bytes=5242880,
            imported_at=datetime.now().isoformat(),
        )
        client.insert_recording(recording_with_song)

        retrieved = client.get_recording_by_song_id("song_0001")

        assert retrieved is not None
        assert retrieved.hash_prefix == "c6de4449928d"

    def test_list_recordings(self, client):
        """Test listing recordings."""
        # Insert recordings with different statuses
        for i in range(5):
            recording = Recording(
                content_hash=f"hash{i}" * 16,
                hash_prefix=f"hash{i}",
                original_filename=f"song{i}.mp3",
                file_size_bytes=1000000,
                imported_at=datetime.now().isoformat(),
                analysis_status="completed" if i < 3 else "pending",
            )
            client.insert_recording(recording)

        # List all
        all_recordings = client.list_recordings()
        assert len(all_recordings) == 5

        # Filter by status
        completed = client.list_recordings(status="completed")
        assert len(completed) == 3

        pending = client.list_recordings(status="pending")
        assert len(pending) == 2

    def test_update_recording_status(self, client, sample_recording):
        """Test updating recording status."""
        client.insert_recording(sample_recording)

        # Update status
        client.update_recording_status(
            hash_prefix="c6de4449928d",
            analysis_status="processing",
            analysis_job_id="job_123",
        )

        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved.analysis_status == "processing"
        assert retrieved.analysis_job_id == "job_123"

    def test_update_recording_analysis(self, client, sample_recording):
        """Test updating recording with analysis results."""
        # Insert with pending status
        sample_recording.analysis_status = "pending"
        client.insert_recording(sample_recording)

        # Update with analysis results
        client.update_recording_analysis(
            hash_prefix="c6de4449928d",
            duration_seconds=300.0,
            tempo_bpm=120.0,
            musical_key="D",
            musical_mode="minor",
            key_confidence=0.95,
            loudness_db=-10.0,
            beats="[0.0, 0.5, 1.0]",
        )

        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved.duration_seconds == 300.0
        assert retrieved.tempo_bpm == 120.0
        assert retrieved.musical_key == "D"
        assert retrieved.musical_mode == "minor"
        assert retrieved.key_confidence == 0.95
        assert retrieved.analysis_status == "completed"

    def test_recording_hash_prefix_unique(self, client, sample_recording):
        """Test that hash_prefix must be unique."""
        client.insert_recording(sample_recording)

        # Try to insert another with same hash_prefix
        duplicate = Recording(
            content_hash="different" * 8,
            hash_prefix="c6de4449928d",  # Same prefix
            song_id=None,
            original_filename="other.mp3",
            file_size_bytes=2000000,
            imported_at=datetime.now().isoformat(),
        )

        # Should replace due to INSERT OR REPLACE
        client.insert_recording(duplicate)

        retrieved = client.get_recording_by_hash("c6de4449928d")
        assert retrieved.original_filename == "other.mp3"

    def test_delete_recording_success(self, client, sample_recording):
        """Deletes recording by hash_prefix."""
        client.insert_recording(sample_recording)

        # Verify recording exists
        assert client.get_recording_by_hash("c6de4449928d") is not None

        # Delete recording
        client.delete_recording("c6de4449928d")

        # Verify recording is deleted
        assert client.get_recording_by_hash("c6de4449928d") is None

    def test_delete_recording_not_found(self, client):
        """Deleting non-existent recording does not raise error."""
        # Should not raise an error
        client.delete_recording("nonexistent_hash")


class TestTransaction:
    """Tests for transaction handling."""

    def test_transaction_commit(self, client):
        """Test that successful transaction commits."""
        with client.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (?, ?, ?, ?)",
                ("test_song", "Test", "https://example.com", datetime.now().isoformat()),
            )

        # Verify data was committed
        result = client.get_song("test_song")
        assert result is not None

    def test_transaction_rollback(self, client):
        """Test that failed transaction rolls back."""
        try:
            with client.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO songs (id, title, source_url, scraped_at) VALUES (?, ?, ?, ?)",
                    ("test_song2", "Test", "https://example.com", datetime.now().isoformat()),
                )
                # Force an error
                raise ValueError("Test error")
        except ValueError:
            pass

        # Verify data was not committed
        result = client.get_song("test_song2")
        assert result is None


class TestSyncFeatures:
    """Tests for Turso sync functionality."""

    def test_is_turso_enabled_without_config(self, temp_db_path):
        """Test that Turso is disabled when not configured."""
        client = DatabaseClient(temp_db_path)
        assert client.is_turso_enabled is False

    def test_is_turso_enabled_with_config(self, temp_db_path):
        """Test Turso detection with configuration."""
        client = DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )
        # Will be False because libsql is not available in test environment
        assert client.is_turso_enabled is False

    def test_sync_raises_error_when_not_configured(self, temp_db_path):
        """Test that sync raises error when Turso is not configured."""
        client = DatabaseClient(temp_db_path)

        with pytest.raises(SyncError, match="Turso sync is not configured"):
            client.sync()

    def test_get_stats_without_sync_metadata(self, client):
        """Test getting stats when sync metadata is not initialized."""
        stats = client.get_stats()

        assert stats.sync_version == "1"
        assert stats.local_device_id == ""
        assert stats.turso_configured is False

    def test_get_stats_with_turso_disabled(self, client):
        """Test getting stats with Turso explicitly disabled."""
        stats = client.get_stats()

        assert stats.turso_configured is False
        assert stats.last_sync_at is None

    def test_update_sync_metadata(self, client):
        """Test updating sync metadata."""
        client.update_sync_metadata("test_key", "test_value")

        cursor = client.connection.cursor()
        cursor.execute("SELECT value FROM sync_metadata WHERE key = 'test_key'")
        result = cursor.fetchone()

        assert result[0] == "test_value"

    def test_update_sync_metadata_overwrites_existing(self, client):
        """Test that updating existing metadata overwrites value."""
        client.update_sync_metadata("test_key", "value1")
        client.update_sync_metadata("test_key", "value2")

        cursor = client.connection.cursor()
        cursor.execute("SELECT value FROM sync_metadata WHERE key = 'test_key'")
        result = cursor.fetchone()

        assert result[0] == "value2"

    @patch("stream_of_worship.admin.db.client.LIBSQL_AVAILABLE", True)
    @patch("stream_of_worship.admin.db.client.libsql")
    def test_turso_connection_mocked(self, mock_libsql, temp_db_path):
        """Test Turso connection with mocked libsql."""
        mock_conn = MagicMock()
        mock_libsql.connect.return_value = mock_conn

        client = DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

        assert client.is_turso_enabled is True

        # Access connection property
        _ = client.connection

        mock_libsql.connect.assert_called_once_with(
            str(temp_db_path),
            sync_url="libsql://test.turso.io",
            auth_token="test-token",
        )
