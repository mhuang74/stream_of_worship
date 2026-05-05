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
    _format_param,
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
        """Soft-deletes recording by hash_prefix."""
        client.insert_recording(sample_recording)

        # Verify recording exists
        assert client.get_recording_by_hash("c6de4449928d") is not None

        # Delete recording (soft delete)
        client.delete_recording("c6de4449928d")

        # Verify recording is soft-deleted (has deleted_at set)
        deleted = client.get_recording_by_hash("c6de4449928d")
        assert deleted is not None
        assert deleted.deleted_at is not None

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
        with patch("stream_of_worship.admin.db.client.LIBSQL_AVAILABLE", False):
            client = DatabaseClient(
                temp_db_path,
                turso_url="libsql://test.turso.io",
                turso_token="test-token",
            )
            assert client.is_turso_enabled is False

    def test_sync_raises_error_when_not_configured(self, temp_db_path):
        """Test that sync raises error when Turso is not configured."""
        client = DatabaseClient(temp_db_path)

        with pytest.raises(SyncError, match="Turso sync is not configured"):
            client.sync()

    def test_get_stats_without_sync_metadata(self, client):
        """Test getting stats when sync metadata is not initialized."""
        stats = client.get_stats()

        assert stats.sync_version == "2"
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


class TestFormatParam:
    """Tests for _format_param() helper function."""

    def test_none(self):
        assert _format_param(None) == {"type": "null"}

    def test_bool_true(self):
        assert _format_param(True) == {"type": "integer", "value": "1"}

    def test_bool_false(self):
        assert _format_param(False) == {"type": "integer", "value": "0"}

    def test_int(self):
        assert _format_param(42) == {"type": "integer", "value": "42"}

    def test_negative_int(self):
        assert _format_param(-1) == {"type": "integer", "value": "-1"}

    def test_float(self):
        assert _format_param(3.14) == {"type": "float", "value": "3.14"}

    def test_str(self):
        assert _format_param("hello") == {"type": "text", "value": "hello"}

    def test_empty_str(self):
        assert _format_param("") == {"type": "text", "value": ""}

    def test_bytes(self):
        import base64

        data = b"\x00\x01\x02"
        result = _format_param(data)
        assert result["type"] == "blob"
        assert result["base64"] == base64.b64encode(data).decode()

    def test_other_type(self):
        result = _format_param(object())
        assert result["type"] == "text"
        assert "object" in result["value"]


class TestHttpPipelineUrl:
    """Tests for http_pipeline_url property."""

    def test_libsql_url(self, temp_db_path):
        client = DatabaseClient(temp_db_path, turso_url="libsql://my-db.turso.io")
        assert client.http_pipeline_url == "https://my-db.turso.io/v2/pipeline"

    def test_https_url_passthrough(self, temp_db_path):
        client = DatabaseClient(temp_db_path, turso_url="https://my-db.turso.io")
        assert client.http_pipeline_url == "https://my-db.turso.io/v2/pipeline"

    def test_no_url(self, temp_db_path):
        client = DatabaseClient(temp_db_path)
        assert client.http_pipeline_url is None

    def test_trailing_slash_stripped(self, temp_db_path):
        client = DatabaseClient(temp_db_path, turso_url="libsql://my-db.turso.io/")
        assert client.http_pipeline_url == "https://my-db.turso.io/v2/pipeline"


class TestExecuteRemotePipeline:
    """Tests for _execute_remote_pipeline() with mocked HTTP."""

    @pytest.fixture
    def turso_client(self, temp_db_path):
        return DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

    def test_successful_pipeline(self, turso_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"type": "ok", "response": {"type": "execute", "result": {"rows": []}}},
                {"type": "ok", "response": {"type": "close"}},
            ]
        }

        with patch("stream_of_worship.admin.db.client.requests.post", return_value=mock_response) as mock_post:
            results = turso_client._execute_remote_pipeline(
                [{"type": "execute", "stmt": {"sql": "SELECT 1", "args": []}}, {"type": "close"}]
            )

            assert len(results) == 2
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs[1]["headers"]["Authorization"] == "Bearer test-token"
            assert call_kwargs[1]["json"]["requests"][0]["type"] == "execute"

    def test_timeout_raises_sync_error(self, turso_client):
        import requests as http_requests

        with patch(
            "stream_of_worship.admin.db.client.requests.post",
            side_effect=http_requests.exceptions.Timeout(),
        ):
            with pytest.raises(SyncError, match="timed out"):
                turso_client._execute_remote_pipeline([{"type": "close"}])

    def test_connection_error_raises_sync_error(self, turso_client):
        import requests as http_requests

        with patch(
            "stream_of_worship.admin.db.client.requests.post",
            side_effect=http_requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(SyncError, match="Cannot connect to Turso"):
                turso_client._execute_remote_pipeline([{"type": "close"}])

    def test_no_url_raises_sync_error(self, temp_db_path):
        client = DatabaseClient(temp_db_path)
        with pytest.raises(SyncError, match="Turso not configured"):
            client._execute_remote_pipeline([{"type": "close"}])


class TestCheckPipelineResults:
    """Tests for _check_pipeline_results()."""

    @pytest.fixture
    def turso_client(self, temp_db_path):
        return DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

    def test_all_ok(self, turso_client):
        results = [{"type": "ok", "response": {"type": "execute"}}]
        turso_client._check_pipeline_results(results)

    def test_error_raises(self, turso_client):
        results = [{"type": "error", "error": {"message": "syntax error"}}]
        with pytest.raises(SyncError, match="syntax error"):
            turso_client._check_pipeline_results(results)

    def test_ignored_error_suppressed(self, turso_client):
        results = [{"type": "error", "error": {"message": "duplicate column name: foo"}}]
        turso_client._check_pipeline_results(results, ignore_sql_errors={"duplicate column name"})

    def test_non_ignored_error_raises(self, turso_client):
        results = [{"type": "error", "error": {"message": "no such table: songs"}}]
        with pytest.raises(SyncError, match="no such table"):
            turso_client._check_pipeline_results(results, ignore_sql_errors={"duplicate column name"})


class TestExecuteRemote:
    """Tests for _execute_remote() single-statement helper."""

    @pytest.fixture
    def turso_client(self, temp_db_path):
        return DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

    def test_returns_result_dict(self, turso_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {
                    "type": "ok",
                    "response": {
                        "type": "execute",
                        "result": {"rows": [[1, "col1"], [2, "col2"]], "cols": [{"name": "cid"}, {"name": "name"}]},
                    },
                },
                {"type": "ok", "response": {"type": "close"}},
            ]
        }

        with patch("stream_of_worship.admin.db.client.requests.post", return_value=mock_response):
            result = turso_client._execute_remote("PRAGMA table_info(songs)")
            assert "rows" in result
            assert len(result["rows"]) == 2

    def test_formats_params_correctly(self, turso_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"type": "ok", "response": {"type": "execute", "result": {}}},
                {"type": "ok", "response": {"type": "close"}},
            ]
        }

        with patch("stream_of_worship.admin.db.client.requests.post", return_value=mock_response) as mock_post:
            turso_client._execute_remote("INSERT INTO songs (id) VALUES (?)", ("song_001",))

            payload = mock_post.call_args[1]["json"]
            args = payload["requests"][0]["stmt"]["args"]
            assert args[0] == {"type": "text", "value": "song_001"}

    def test_empty_result_on_no_ok_response(self, turso_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [{"type": "ok", "response": {"type": "close"}}]
        }

        with patch("stream_of_worship.admin.db.client.requests.post", return_value=mock_response):
            result = turso_client._execute_remote("COMMIT")
            assert result == {}


class TestExecuteRemoteTransaction:
    """Tests for _execute_remote_transaction()."""

    @pytest.fixture
    def turso_client(self, temp_db_path):
        return DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

    def test_sends_begin_and_commit(self, turso_client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"type": "ok", "response": {"type": "execute"}},
                {"type": "ok", "response": {"type": "execute"}},
                {"type": "ok", "response": {"type": "execute"}},
                {"type": "ok", "response": {"type": "close"}},
            ]
        }

        with patch("stream_of_worship.admin.db.client.requests.post", return_value=mock_response) as mock_post:
            turso_client._execute_remote_transaction(
                [("INSERT INTO songs (id) VALUES (?)", ("s1",)), ("INSERT INTO songs (id) VALUES (?)", ("s2",))]
            )

            payload = mock_post.call_args[1]["json"]
            requests_list = payload["requests"]
            assert requests_list[0]["stmt"]["sql"] == "BEGIN"
            assert requests_list[1]["stmt"]["sql"].startswith("INSERT")
            assert requests_list[2]["stmt"]["sql"].startswith("INSERT")
            assert requests_list[3]["stmt"]["sql"] == "COMMIT"
            assert requests_list[4]["type"] == "close"


class TestSyncReplica:
    """Tests for _sync_replica() with fatal vs non-fatal modes."""

    @patch("stream_of_worship.admin.db.client.LIBSQL_AVAILABLE", True)
    @patch("stream_of_worship.admin.db.client.libsql")
    def test_fatal_raises_on_failure(self, mock_libsql, temp_db_path):
        mock_conn = MagicMock()
        mock_conn.sync.side_effect = RuntimeError("WAL conflict")
        mock_libsql.connect.return_value = mock_conn

        client = DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )
        _ = client.connection

        with pytest.raises(SyncError, match="Replica sync failed"):
            client._sync_replica(fatal=True)

    @patch("stream_of_worship.admin.db.client.LIBSQL_AVAILABLE", True)
    @patch("stream_of_worship.admin.db.client.libsql")
    def test_non_fatal_logs_warning(self, mock_libsql, temp_db_path):
        mock_conn = MagicMock()
        mock_conn.sync.side_effect = RuntimeError("WAL conflict")
        mock_libsql.connect.return_value = mock_conn

        client = DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )
        _ = client.connection

        client._sync_replica(fatal=False)

    def test_noop_when_turso_disabled(self, temp_db_path):
        client = DatabaseClient(temp_db_path)
        client._sync_replica(fatal=True)

    def test_noop_when_no_connection(self, temp_db_path):
        client = DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )
        client._sync_replica(fatal=True)


class TestApplyColumnMigrationsRemote:
    """Tests for apply_column_migrations_remote() in schema.py."""

    @pytest.fixture
    def turso_client(self, temp_db_path):
        return DatabaseClient(
            temp_db_path,
            turso_url="libsql://test.turso.io",
            turso_token="test-token",
        )

    def test_idempotent_when_columns_exist(self, turso_client):
        from stream_of_worship.admin.db.schema import apply_column_migrations_remote, COLUMN_MIGRATIONS

        tables = {t for t, _, _ in COLUMN_MIGRATIONS}
        pragma_results = {}
        for table in tables:
            pragma_results[table] = {
                "rows": [[i, col, "TEXT", 0, None, 0] for i, (t, col, _) in enumerate(COLUMN_MIGRATIONS) if t == table],
                "cols": [{"name": "cid"}, {"name": "name"}, {"name": "type"}, {"name": "notnull"}, {"name": "dflt_value"}, {"name": "pk"}],
            }

        call_count = 0

        def mock_execute_remote(sql, params=(), timeout=10):
            nonlocal call_count
            call_count += 1
            if sql.startswith("PRAGMA table_info"):
                for table in tables:
                    if table in sql:
                        return pragma_results[table]
            return {}

        turso_client._execute_remote = mock_execute_remote
        apply_column_migrations_remote(turso_client)

        assert call_count == len(tables)

    def test_issues_alter_when_column_missing(self, turso_client):
        from stream_of_worship.admin.db.schema import apply_column_migrations_remote, COLUMN_MIGRATIONS

        tables = {t for t, _, _ in COLUMN_MIGRATIONS}
        alter_statements = []

        def mock_execute_remote(sql, params=(), timeout=10):
            if sql.startswith("PRAGMA table_info"):
                for table in tables:
                    if table in sql:
                        return {"rows": [], "cols": []}
            elif sql.startswith("ALTER TABLE"):
                alter_statements.append(sql)
            return {}

        turso_client._execute_remote = mock_execute_remote
        apply_column_migrations_remote(turso_client)

        assert len(alter_statements) == len(COLUMN_MIGRATIONS)

    def test_suppresses_duplicate_column_error(self, turso_client):
        from stream_of_worship.admin.db.schema import apply_column_migrations_remote

        call_idx = 0

        def mock_execute_remote(sql, params=(), timeout=10):
            nonlocal call_idx
            call_idx += 1
            if sql.startswith("PRAGMA"):
                return {"rows": [], "cols": []}
            if sql.startswith("ALTER TABLE") and call_idx > 2:
                raise SyncError("duplicate column name: foo")
            return {}

        turso_client._execute_remote = mock_execute_remote
        apply_column_migrations_remote(turso_client)
