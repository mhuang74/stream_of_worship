"""Integration tests for Postgres database clients.

Uses testcontainers to spin up a real Postgres instance and verifies
end-to-end operations through DatabaseClient, ReadOnlyClient, and SongsetClient.
"""

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def db_clients(postgres_url):
    """Create schema and return connected clients.

    Returns:
        Tuple of (DatabaseClient, ReadOnlyClient, SongsetClient, connection).
    """
    provider = ConnectionProvider(postgres_url)
    conn = provider.get_connection()

    # Create all tables
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    admin_client = DatabaseClient(provider)
    read_client = ReadOnlyClient(provider)
    songset_client = SongsetClient(provider)

    yield admin_client, read_client, songset_client, conn

    # Cleanup (use a fresh connection in case provider was closed by test)
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
class TestDatabaseClient:
    """Integration tests for admin DatabaseClient."""

    def test_insert_and_get_song(self, db_clients):
        """Test inserting and retrieving a song."""
        admin_client, _, _, _ = db_clients

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

    def test_list_songs(self, db_clients):
        """Test listing songs with filters."""
        admin_client, _, _, _ = db_clients

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

    def test_insert_and_get_recording(self, db_clients):
        """Test inserting and retrieving a recording."""
        admin_client, _, _, _ = db_clients

        # Need a song first for FK
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

    def test_soft_delete_and_restore_song(self, db_clients):
        """Test soft-deleting and restoring a song."""
        admin_client, read_client, _, _ = db_clients

        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        # Soft delete
        assert admin_client.soft_delete_song("song_1") is True
        # Admin get_song() doesn't filter deleted, but read_client does
        assert read_client.get_song("song_1") is None
        # Verify it's actually soft-deleted via admin client
        deleted = admin_client.get_song("song_1")
        assert deleted is not None
        assert deleted.deleted_at is not None

        # Restore
        assert admin_client.restore_song("song_1") is True
        result = read_client.get_song("song_1")
        assert result is not None
        assert result.deleted_at is None

    def test_get_stats(self, db_clients):
        """Test database statistics."""
        admin_client, _, _, _ = db_clients

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


@pytest.mark.integration
class TestReadOnlyClient:
    """Integration tests for app ReadOnlyClient."""

    def test_get_song(self, db_clients):
        """Test reading a song."""
        admin_client, read_client, _, _ = db_clients

        song = Song(
            id="song_1",
            title="Test Song",
            source_url="http://test",
            scraped_at="2024-01-01T00:00:00",
        )
        admin_client.insert_song(song)

        result = read_client.get_song("song_1")
        assert result is not None
        assert result.title == "Test Song"

    def test_get_recording_by_hash(self, db_clients):
        """Test reading a recording by hash."""
        admin_client, read_client, _, _ = db_clients

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

        result = read_client.get_recording_by_hash("abc123")
        assert result is not None
        assert result.hash_prefix == "abc123"

    def test_search_songs(self, db_clients):
        """Test searching songs."""
        admin_client, read_client, _, _ = db_clients

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

        results = read_client.search_songs("Amazing")
        assert len(results) == 1
        assert results[0].title == "Amazing Grace"

    def test_list_albums(self, db_clients):
        """Test listing albums."""
        admin_client, read_client, _, _ = db_clients

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
        admin_client.insert_song(
            Song(
                id="song_3",
                title="Song C",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
                album_name="Album 2",
            )
        )

        albums = read_client.list_albums()
        assert set(albums) == {"Album 1", "Album 2"}

    def test_check_connection(self, db_clients):
        """Test connection health check."""
        _, read_client, _, _ = db_clients
        assert read_client.check_connection() is True


@pytest.mark.integration
class TestSongsetClient:
    """Integration tests for SongsetClient."""

    def test_create_songset(self, db_clients):
        """Test creating a songset."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set", description="Test")
        assert songset.name == "My Set"
        assert songset.description == "Test"

        result = songset_client.get_songset(songset.id)
        assert result is not None
        assert result.name == "My Set"

    def test_add_and_get_items(self, db_clients):
        """Test adding items to a songset."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set")
        songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            recording_hash_prefix="abc123",
            position=0,
        )

        items = songset_client.get_items(songset.id)
        assert len(items) == 1
        assert items[0].song_id == "song_1"
        assert items[0].recording_hash_prefix == "abc123"

    def test_update_item(self, db_clients):
        """Test updating a songset item."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set")
        item = songset_client.add_item(
            songset_id=songset.id,
            song_id="song_1",
            gap_beats=2.0,
        )

        assert songset_client.update_item(item.id, gap_beats=4.0) is True

        items = songset_client.get_items(songset.id)
        assert items[0].gap_beats == 4.0

    def test_reorder_item(self, db_clients):
        """Test reordering items."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set")
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")
        _item3 = songset_client.add_item(songset_id=songset.id, song_id="song_3")

        # Move item3 to position 0
        assert songset_client.reorder_item(_item3.id, 0) is True

        items = songset_client.get_items(songset.id)
        assert items[0].song_id == "song_3"
        assert items[1].song_id == "song_1"
        assert items[2].song_id == "song_2"

    def test_remove_item(self, db_clients):
        """Test removing an item."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set")
        _item1 = songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")

        assert songset_client.remove_item(_item1.id) is True

        items = songset_client.get_items(songset.id)
        assert len(items) == 1
        assert items[0].song_id == "song_2"

    def test_delete_songset_cascades(self, db_clients):
        """Test that deleting a songset removes its items."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set")
        songset_client.add_item(songset_id=songset.id, song_id="song_1")
        songset_client.add_item(songset_id=songset.id, song_id="song_2")

        assert songset_client.delete_songset(songset.id) is True
        assert songset_client.get_songset(songset.id) is None
        assert songset_client.get_item_count(songset.id) == 0

    def test_update_songset(self, db_clients):
        """Test updating songset metadata."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("Original Name")
        assert songset_client.update_songset(songset.id, name="New Name") is True

        result = songset_client.get_songset(songset.id)
        assert result.name == "New Name"

    def test_list_songsets(self, db_clients):
        """Test listing songsets."""
        _, _, songset_client, _ = db_clients

        songset_client.create_songset("Set A")
        songset_client.create_songset("Set B")

        songsets = songset_client.list_songsets()
        assert len(songsets) == 2

    def test_songset_timestamps(self, db_clients):
        """Test that songset timestamps are set correctly."""
        _, _, songset_client, _ = db_clients

        songset = songset_client.create_songset("My Set")
        assert songset.created_at is not None
        assert songset.updated_at is not None
