"""Tests for ReadOnlyClient (Postgres via testcontainers)."""

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def read_client(postgres_url):
    """Create a ReadOnlyClient connected to a fresh Postgres schema with sample data."""
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()

    # Create schema
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    admin_client = DatabaseClient(provider)
    client = ReadOnlyClient(provider)

    # Insert sample data
    admin_client.insert_song(
        Song(
            id="song_1",
            title="Amazing Grace",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
            album_name="Hymns",
            musical_key="G",
        )
    )
    admin_client.insert_song(
        Song(
            id="song_2",
            title="How Great Thou Art",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
            album_name="Hymns",
            musical_key="D",
        )
    )
    admin_client.insert_song(
        Song(
            id="song_deleted",
            title="Deleted Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
    )
    # Soft delete one song
    with conn.cursor() as cur:
        cur.execute("UPDATE songs SET deleted_at = NOW() WHERE id = %s", ("song_deleted",))

    admin_client.insert_recording(
        Recording(
            content_hash="a" * 64,
            hash_prefix="abc123",
            song_id="song_1",
            original_filename="amazing.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
            analysis_status="completed",
            lrc_status="completed",
            visibility_status="published",
        )
    )
    admin_client.insert_recording(
        Recording(
            content_hash="b" * 64,
            hash_prefix="def456",
            song_id="song_2",
            original_filename="howgreat.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
            analysis_status="pending",
        )
    )

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
class TestReadOnlyClient:
    """Integration tests for ReadOnlyClient."""

    def test_get_song(self, read_client):
        """Test retrieving a song by ID."""
        song = read_client.get_song("song_1")
        assert song is not None
        assert song.title == "Amazing Grace"

    def test_get_song_not_found(self, read_client):
        """Test retrieving a non-existent song."""
        song = read_client.get_song("nonexistent")
        assert song is None

    def test_get_song_excludes_deleted(self, read_client):
        """Test that deleted songs are excluded by default."""
        song = read_client.get_song("song_deleted")
        assert song is None

    def test_get_song_including_deleted(self, read_client):
        """Test that deleted songs can be fetched."""
        song = read_client.get_song_including_deleted("song_deleted")
        assert song is not None
        assert song.title == "Deleted Song"
        assert song.deleted_at is not None

    def test_list_songs(self, read_client):
        """Test listing all active songs."""
        songs = read_client.list_songs()
        assert len(songs) == 2  # Excludes deleted
        titles = {s.title for s in songs}
        assert titles == {"Amazing Grace", "How Great Thou Art"}

    def test_list_songs_with_album_filter(self, read_client):
        """Test filtering songs by album."""
        songs = read_client.list_songs(album="Hymns")
        assert len(songs) == 2

    def test_list_songs_with_key_filter(self, read_client):
        """Test filtering songs by key."""
        songs = read_client.list_songs(key="G")
        assert len(songs) == 1
        assert songs[0].title == "Amazing Grace"

    def test_list_songs_with_limit(self, read_client):
        """Test listing songs with a limit."""
        songs = read_client.list_songs(limit=1)
        assert len(songs) == 1

    def test_list_songs_with_offset(self, read_client):
        """Test listing songs with an offset."""
        songs = read_client.list_songs(offset=1)
        assert len(songs) == 1

    def test_search_songs_by_title(self, read_client):
        """Test searching songs by title."""
        results = read_client.search_songs("Amazing", field="title")
        assert len(results) == 1
        assert results[0].title == "Amazing Grace"

    def test_search_songs_all_fields(self, read_client):
        """Test searching across all fields."""
        results = read_client.search_songs("Grace")
        assert len(results) == 1
        assert results[0].title == "Amazing Grace"

    def test_list_albums(self, read_client):
        """Test listing distinct albums."""
        albums = read_client.list_albums()
        assert "Hymns" in albums

    def test_list_keys(self, read_client):
        """Test listing distinct keys."""
        keys = read_client.list_keys()
        assert set(keys) == {"G", "D"}

    def test_get_recording_by_hash(self, read_client):
        """Test retrieving a recording by hash prefix."""
        recording = read_client.get_recording_by_hash("abc123")
        assert recording is not None
        assert recording.hash_prefix == "abc123"

    def test_get_recording_by_hash_not_found(self, read_client):
        """Test retrieving a non-existent recording."""
        recording = read_client.get_recording_by_hash("nonexistent")
        assert recording is None

    def test_get_recording_by_song_id(self, read_client):
        """Test retrieving a recording by song ID."""
        recording = read_client.get_recording_by_song_id("song_1")
        assert recording is not None
        assert recording.song_id == "song_1"

    def test_list_recordings(self, read_client):
        """Test listing all recordings."""
        recordings = read_client.list_recordings()
        assert len(recordings) == 2

    def test_list_recordings_by_status(self, read_client):
        """Test filtering recordings by status."""
        completed = read_client.list_recordings(status="completed")
        assert len(completed) == 1
        assert completed[0].hash_prefix == "abc123"

    def test_list_recordings_with_analysis(self, read_client):
        """Test filtering for analyzed recordings."""
        analyzed = read_client.list_recordings(has_analysis=True)
        assert len(analyzed) == 1
        assert analyzed[0].analysis_status == "completed"

    def test_get_recording_count(self, read_client):
        """Test getting total recording count."""
        count = read_client.get_recording_count()
        assert count == 2

    def test_get_analyzed_recording_count(self, read_client):
        """Test getting analyzed recording count."""
        count = read_client.get_analyzed_recording_count()
        assert count == 1

    def test_get_song_count(self, read_client):
        """Test getting total song count."""
        count = read_client.get_song_count()
        assert count == 2

    def test_get_lrc_ready_count(self, read_client):
        """Test getting LRC-ready song count."""
        count = read_client.get_lrc_ready_count()
        assert count == 1

    def test_check_connection(self, read_client):
        """Test connection health check."""
        assert read_client.check_connection() is True

    def test_context_manager(self, read_client):
        """Test ReadOnlyClient works as a context manager."""
        with read_client as client:
            assert client is read_client
            song = client.get_song("song_1")
            assert song is not None
