"""Integration tests for Postgres database clients.

Uses testcontainers to spin up a real Postgres instance and verifies
end-to-end operations through DatabaseClient, ReadOnlyClient, and SongsetClient.
"""

import pytest

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song, SongComponent
from stream_of_worship.db.app.read_client import ReadOnlyClient
from stream_of_worship.db.app.songset_client import SongsetClient
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS


@pytest.fixture(scope="function")
def db_clients(make_test_provider, seed_user):
    """Create schema, seed a default user, and return connected clients.

    The ``SongsetClient`` returned is scoped to the seeded user so existing
    test bodies don't have to thread ``user_id`` through every call.

    Returns:
        Tuple of (DatabaseClient, ReadOnlyClient, SongsetClient, connection).
    """
    provider = make_test_provider()
    conn = provider.get_connection()

    # Create all tables
    with conn.cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)

    # Songsets now require a user_id FK; seed a default user for the test.
    default_user_id = seed_user(provider, email="default@test", name="Default")

    admin_client = DatabaseClient(provider)
    read_client = ReadOnlyClient(provider)
    songset_client = SongsetClient(provider, user_id=default_user_id)

    yield admin_client, read_client, songset_client, conn

    # Cleanup (use a fresh connection in case provider was closed by test)
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyrics_feedback CASCADE;
                DROP TABLE IF EXISTS user_favorite_songs CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
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

        # Partial, case-insensitive album match (ILIKE %s)
        partial_songs = admin_client.list_songs(album="album 1")
        assert len(partial_songs) == 1
        assert partial_songs[0].title == "Song A"

        partial_songs = admin_client.list_songs(album="bum")
        assert {s.title for s in partial_songs} == {"Song A", "Song B"}

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

    def test_update_recording_structure_preserves_fast_tier_columns(self, db_clients):
        """Writing sections/beats must not clobber fast-tier analysis columns.

        The Arrange gate needs duration_seconds, so a write-back that reused
        update_recording_analysis(analysis_status="completed") would NULL it
        (and tempo/key) via that method's full-tier branch and disable Arrange.
        """
        admin_client, _, _, _ = db_clients

        admin_client.insert_song(
            Song(
                id="song_structure",
                title="Structure Song",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        admin_client.insert_recording(
            Recording(
                content_hash="b" * 64,
                hash_prefix="structure123",
                song_id="song_structure",
                original_filename="structure.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
                duration_seconds=215.5,
                tempo_bpm=73.8,
                musical_key="G",
                analysis_status="partial",
            )
        )

        updated = admin_client.update_recording_structure(
            hash_prefix="structure123",
            sections='[{"label": "verse", "start": 10.0, "end": 30.0}]',
            beats="[10.0, 10.5, 30.0]",
        )

        assert updated is True
        result = admin_client.get_recording_by_hash("structure123")
        assert result is not None
        assert result.sections == '[{"label": "verse", "start": 10.0, "end": 30.0}]'
        assert result.beats == "[10.0, 10.5, 30.0]"
        # Fast-tier columns the Arrange gate depends on survive untouched.
        assert result.duration_seconds == 215.5
        assert result.tempo_bpm == 73.8
        assert result.musical_key == "G"
        # Status is deliberately unchanged: sections are derived, not allin1.
        assert result.analysis_status == "partial"

    def test_update_recording_structure_returns_false_for_unknown_prefix(self, db_clients):
        admin_client, _, _, _ = db_clients

        updated = admin_client.update_recording_structure(
            hash_prefix="nosuchrow123",
            sections="[]",
            beats="[]",
        )

        assert updated is False

    def test_upsert_song_components_dedupes_within_one_batch(self, db_clients):
        """Duplicate component keys inside one batch must not abort the insert.

        The unique index covers (song_id, component_type, occurrence_index,
        role). The DELETE only clears previously stored rows, so cached R2
        payloads carrying the same verse twice used to raise UniqueViolation
        and lose the entire batch.
        """
        admin_client, _, _, _ = db_clients

        admin_client.insert_song(
            Song(
                id="song_dup_components",
                title="Dup Components",
                source_url="http://test",
                scraped_at="2024-01-01T00:00:00",
            )
        )
        admin_client.insert_recording(
            Recording(
                content_hash="c" * 64,
                hash_prefix="cccccccccccc",
                song_id="song_dup_components",
                original_filename="dup.mp3",
                file_size_bytes=1000,
                imported_at="2024-01-01T00:00:00",
            )
        )

        def component(start: float, end: float) -> SongComponent:
            return SongComponent(
                song_id="song_dup_components",
                content_hash="c" * 64,
                component_type="verse",
                occurrence_index=1,
                role="none",
                start_time=start,
                end_time=end,
            )

        inserted = admin_client.upsert_song_components(
            "song_dup_components",
            "c" * 64,
            [component(10.0, 20.0), component(10.0, 20.0)],
        )

        assert inserted == 1
        assert len(admin_client.get_song_components("song_dup_components")) == 1

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
        deleted = admin_client.get_song("song_1", include_deleted=True)
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
