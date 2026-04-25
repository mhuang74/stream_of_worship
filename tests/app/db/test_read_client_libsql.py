"""Tests for ReadOnlyClient with libsql/Turso integration.

Tests the libsql branching logic and deleted-aware queries.
"""

import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from stream_of_worship.admin.db.models import Song, Recording
from stream_of_worship.app.db.read_client import ReadOnlyClient, SyncError


class TestReadOnlyClientLibsql:
    """Test libsql integration in ReadOnlyClient."""

    def test_is_turso_enabled_requires_libsql_and_url(self):
        """Test Turso is only enabled with both libsql and URL."""
        with tempfile_db() as db_path:
            # No URL - should not be enabled
            client = ReadOnlyClient(db_path)
            assert client.is_turso_enabled is False

            # With URL but libsql not available
            client = ReadOnlyClient(db_path, turso_url="libsql://test.turso.io")
            # Depends on whether libsql is installed

    def test_uses_sqlite_without_turso(self):
        """Test that standard sqlite3 is used when Turso not configured."""
        with tempfile_db() as db_path:
            client = ReadOnlyClient(db_path)
            conn = client.connection

            # Should be sqlite3 connection
            assert isinstance(conn, sqlite3.Connection)

    def test_sync_raises_error_when_not_configured(self):
        """Test sync raises error when Turso not configured."""
        with tempfile_db() as db_path:
            client = ReadOnlyClient(db_path)

            with pytest.raises(SyncError):
                client.sync()


class TestReadOnlyClientDeletedAware:
    """Test deleted_at filtering in queries."""

    def test_get_song_excludes_deleted_by_default(self, tmp_path):
        """Test get_song excludes soft-deleted by default."""
        db_path = tmp_path / "test.db"

        # Create test database
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO songs VALUES ('song_1', 'Test Song', 'http://test', '2024-01-01', NULL)"
        )
        conn.execute(
            "INSERT INTO songs VALUES ('song_2', 'Deleted Song', 'http://test', '2024-01-01', '2024-01-02')"
        )
        conn.commit()
        conn.close()

        client = ReadOnlyClient(db_path)

        # Should find active song
        song = client.get_song("song_1")
        assert song is not None
        assert song.title == "Test Song"

        # Should not find deleted song by default
        deleted = client.get_song("song_2")
        assert deleted is None

        # Should find deleted song with include_deleted=True
        deleted = client.get_song("song_2", include_deleted=True)
        assert deleted is not None
        assert deleted.title == "Deleted Song"

    def test_list_songs_excludes_deleted(self, tmp_path):
        """Test list_songs excludes soft-deleted by default."""
        db_path = tmp_path / "test.db"

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO songs VALUES ('song_1', 'Active', 'http://test', '2024-01-01', NULL)"
        )
        conn.execute(
            "INSERT INTO songs VALUES ('song_2', 'Deleted', 'http://test', '2024-01-01', '2024-01-02')"
        )
        conn.commit()
        conn.close()

        client = ReadOnlyClient(db_path)

        # Should only return active songs
        songs = client.list_songs()
        assert len(songs) == 1
        assert songs[0].title == "Active"

    def test_get_song_including_deleted(self, tmp_path):
        """Test get_song_including_deleted convenience method."""
        db_path = tmp_path / "test.db"

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE songs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_url TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO songs VALUES ('deleted_song', 'Deleted', 'http://test', '2024-01-01', '2024-01-02')"
        )
        conn.commit()
        conn.close()

        client = ReadOnlyClient(db_path)

        song = client.get_song_including_deleted("deleted_song")
        assert song is not None
        assert song.title == "Deleted"


class TestReadOnlyClientRecordingQueries:
    """Test recording queries with deleted_at."""

    def test_get_recording_by_hash_excludes_deleted(self, tmp_path):
        """Test recording queries exclude soft-deleted."""
        db_path = tmp_path / "test.db"

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE recordings (
                content_hash TEXT PRIMARY KEY,
                hash_prefix TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                deleted_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO recordings VALUES ('hash1', 'abc123', 'test.mp3', 1000, '2024-01-01', NULL)"
        )
        conn.execute(
            "INSERT INTO recordings VALUES ('hash2', 'def456', 'deleted.mp3', 1000, '2024-01-01', '2024-01-02')"
        )
        conn.commit()
        conn.close()

        client = ReadOnlyClient(db_path)

        # Should find active recording
        rec = client.get_recording_by_hash("abc123")
        assert rec is not None

        # Should not find deleted by default
        rec = client.get_recording_by_hash("def456")
        assert rec is None

        # Should find deleted with include_deleted=True
        rec = client.get_recording_by_hash("def456", include_deleted=True)
        assert rec is not None


# Context manager for temporary databases
import contextlib


@contextlib.contextmanager
def tempfile_db():
    """Create a temporary database for testing."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
