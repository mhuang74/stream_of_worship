"""Tests for sow-admin database client (Postgres via testcontainers)."""

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def admin_client(postgres_url):
    """Create a DatabaseClient connected to a fresh Postgres schema."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()

    # Create schema
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    client = DatabaseClient(provider)
    yield client

    # Cleanup (use fresh connection in case provider was closed by a test)
    try:
        cleanup_provider = ConnectionProvider(postgres_url)
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
            """)
        cleanup_provider.close()
    except Exception:
        pass


@pytest.mark.integration
class TestDatabaseClientIntegration:
    """Integration tests for admin DatabaseClient."""

    def test_insert_and_get_song(self, admin_client):
        """Test inserting and retrieving a song."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        result = admin_client.get_song("song_1")
        assert result is not None
        assert result.title == "Test Song"
        assert result.id == "song_1"

    def test_list_songs(self, admin_client):
        """Test listing songs with filters."""
        admin_client.insert_song(
            Song(
                id="song_1",
                title="Song A",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album 1",
                musical_key="G",
            )
        )
        admin_client.insert_song(
            Song(
                id="song_2",
                title="Song B",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album 2",
                musical_key="D",
            )
        )

        all_songs = admin_client.list_songs()
        assert len(all_songs) == 2

        album_songs = admin_client.list_songs(album="Album 1")
        assert len(album_songs) == 1
        assert album_songs[0].title == "Song A"

        key_songs = admin_client.list_songs(key="D")
        assert len(key_songs) == 1
        assert key_songs[0].title == "Song B"

    def test_search_songs(self, admin_client):
        """Test searching songs."""
        admin_client.insert_song(
            Song(
                id="song_1",
                title="Amazing Grace",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        admin_client.insert_song(
            Song(
                id="song_2",
                title="How Great Thou Art",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )

        results = admin_client.search_songs("Amazing")
        assert len(results) == 1
        assert results[0].title == "Amazing Grace"

    def test_insert_and_get_recording(self, admin_client):
        """Test inserting and retrieving a recording."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_1",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        admin_client.insert_recording(recording)

        result = admin_client.get_recording_by_hash("abc123")
        assert result is not None
        assert result.hash_prefix == "abc123"
        assert result.song_id == "song_1"

    def test_list_recordings(self, admin_client):
        """Test listing recordings with status filter."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        admin_client.insert_recording(
            Recording(
                content_hash="a" * 64,
                hash_prefix="abc123",
                song_id="song_1",
                original_filename="test.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
                analysis_status="completed",
            )
        )
        admin_client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="def456",
                song_id="song_1",
                original_filename="test2.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
                analysis_status="pending",
            )
        )

        completed = admin_client.list_recordings(status="completed")
        assert len(completed) == 1
        assert completed[0].hash_prefix == "abc123"

    def test_soft_delete_and_restore_song(self, admin_client):
        """Test soft-deleting and restoring a song."""
        from stream_of_worship.app.db.read_client import ReadOnlyClient

        read_client = ReadOnlyClient(admin_client.connection_provider)

        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        # Soft delete
        assert admin_client.soft_delete_song("song_1") is True
        # ReadOnlyClient excludes deleted songs
        assert read_client.get_song("song_1") is None
        # Admin client gets it (for listing deleted)
        deleted = admin_client.get_song("song_1")
        assert deleted is not None
        assert deleted.deleted_at is not None

        # Restore
        assert admin_client.restore_song("song_1") is True
        result = read_client.get_song("song_1")
        assert result is not None
        assert result.deleted_at is None

    def test_delete_and_restore_recording(self, admin_client):
        """Test soft-deleting and restoring a recording."""
        from stream_of_worship.app.db.read_client import ReadOnlyClient

        read_client = ReadOnlyClient(admin_client.connection_provider)

        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_1",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        admin_client.insert_recording(recording)

        # Soft delete
        admin_client.delete_recording("abc123")
        # ReadOnlyClient excludes deleted recordings
        assert read_client.get_recording_by_hash("abc123") is None
        # Admin client still finds it (includes deleted)
        deleted = admin_client.get_recording_by_hash("abc123")
        assert deleted is not None
        assert deleted.deleted_at is not None

        # Restore
        assert admin_client.restore_recording("abc123") is True
        result = read_client.get_recording_by_hash("abc123")
        assert result is not None
        assert result.deleted_at is None

    def test_update_recording_status(self, admin_client):
        """Test updating recording status fields."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_1",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        admin_client.insert_recording(recording)

        admin_client.update_recording_status(
            "abc123", analysis_status="processing", analysis_job_id="job_123"
        )

        result = admin_client.get_recording_by_hash("abc123")
        assert result.analysis_status == "processing"
        assert result.analysis_job_id == "job_123"

    def test_update_recording_visibility(self, admin_client):
        """Test updating recording visibility."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_1",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        admin_client.insert_recording(recording)

        result = admin_client.update_recording_visibility("abc123", "published")
        assert result is True

        updated = admin_client.get_recording_by_hash("abc123")
        assert updated.visibility_status == "published"

    def test_update_recording_lrc(self, admin_client):
        """Test updating recording LRC status."""
        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        recording = Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_1",
            original_filename="test.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
        )
        admin_client.insert_recording(recording)

        admin_client.update_recording_lrc("abc123", "https://r2.example.com/lrc")

        result = admin_client.get_recording_by_hash("abc123")
        assert result.lrc_status == "completed"
        assert result.r2_lrc_url == "https://r2.example.com/lrc"

    def test_get_stats(self, admin_client):
        """Test database statistics."""
        stats = admin_client.get_stats()
        assert stats.is_healthy is True
        assert stats.total_songs == 0
        assert stats.total_recordings == 0
        assert stats.sync_version == "3"

        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        stats = admin_client.get_stats()
        assert stats.total_songs == 1

    def test_list_albums(self, admin_client):
        """Test listing albums with counts."""
        admin_client.insert_song(
            Song(
                id="song_1",
                title="Song A",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album 1",
            )
        )
        admin_client.insert_song(
            Song(
                id="song_2",
                title="Song B",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album 1",
            )
        )

        albums = admin_client.list_albums()
        assert len(albums) == 1
        assert albums[0][0] == "Album 1"
        assert albums[0][2] == 2  # count

    def test_context_manager(self, admin_client):
        """Test DatabaseClient works as a context manager."""
        with admin_client as client:
            assert client is admin_client
